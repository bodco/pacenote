import AppKit
import SwiftUI
import LiveTipsCore

/// PB-109 (owner 25.11): the series the PM puts together by hand (LiveTipsCore/CallSeries.swift), kept in
/// Application Support/LiveTips/call-series.json. Nothing leaves the Mac. Without an entry a series is still the
/// calls of one project with the same title.
@MainActor
final class CallSeriesStore: ObservableObject {
    static let shared = CallSeriesStore()
    static let changed = Notification.Name("CallSeriesStore.changed")

    @Published private(set) var book = CallSeriesBook()
    private var path: URL { AppPaths.support.appendingPathComponent("call-series.json") }

    private init() {
        if let d = try? Data(contentsOf: path), let b = try? JSONDecoder().decode(CallSeriesBook.self, from: d) { book = b }
    }

    func update(_ change: (inout CallSeriesBook) -> Void) {
        change(&book)
        book.series.removeAll { $0.titles.isEmpty && $0.calls.isEmpty }
        let enc = JSONEncoder(); enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let d = try? enc.encode(book) { try? d.write(to: path, options: .atomic) }
        NotificationCenter.default.post(name: Self.changed, object: nil)
    }

    static func call(_ r: CallHistory.Record) -> CallSeriesBook.Call {
        .init(file: r.file, title: r.title, project: r.project, start: r.start, minutes: r.minutes)
    }

    /// Every call the PM can see (not only the 40 newest of Home), for manual series and the picker.
    var allRecords: [CallHistory.Record] {
        CallHistory.shared.records.filter { !$0.hidden && FileManager.default.fileExists(atPath: $0.file) }.sorted { $0.start > $1.start }
    }

    private func records(_ calls: [CallSeriesBook.Call], from list: [CallHistory.Record]) -> [CallHistory.Record] {
        let files = Set(calls.map(\.file))
        return list.filter { files.contains($0.file) }
    }

    /// The series of a saved call, newest first. Without a manual series and without calls taken out it is exactly
    /// the old rule (same project, same title, Home's calls).
    func records(of r: CallHistory.Record) -> [CallHistory.Record] {
        if book.manual(file: r.file, title: r.title, project: r.project) != nil {
            let all = allRecords
            return records(book.members(of: Self.call(r), in: all.map(Self.call)), from: all)
        }
        let vis = CallHistory.shared.visible
        return records(book.members(of: Self.call(r), in: vis.map(Self.call)), from: vis)
    }

    /// The earlier calls of a call still to come, newest first, among the projects named `projects` (name, slug,
    /// folder). Without a manual series: the old rule, minus calls taken out or picked into another series.
    func earlier(title: String, projects: Set<String>, before: Date = .distantFuture) -> [CallHistory.Record] {
        let key = CallSeriesBook.titleKey(title)
        if let p = projects.first(where: { book.manual(title: title, project: $0) != nil }), let s = book.manual(title: title, project: p) {
            let all = allRecords
            return records(book.members(s, in: all.map(Self.call)), from: all).filter { $0.start < before }
        }
        return CallHistory.shared.visible.filter { r in
            r.start < before && EditLog.seriesKey(r.title) == key && projects.contains(r.project)
                && !book.isAlone(r.file, project: r.project) && book.manual(file: r.file, title: r.title, project: r.project) == nil
        }
    }

    /// The register items of a saved call's series (see CallSeriesBook.items).
    func items(_ items: [Commitment], of r: CallHistory.Record) -> [Commitment] {
        book.items(items, members: records(of: r).map(Self.call), titles: book.titles(file: r.file, title: r.title, project: r.project), project: r.project)
    }

    /// The register items of a call still to come.
    func items(_ items: [Commitment], title: String, projects: Set<String>) -> [Commitment] {
        let p = projects.first { book.manual(title: title, project: $0) != nil } ?? projects.first ?? ""
        return book.items(items, members: earlier(title: title, projects: projects).map(Self.call), titles: book.titles(title: title, project: p), project: p)
    }

    /// The name of a manual series ("" for an automatic one).
    func name(file: String, title: String, project: String) -> String { book.manual(file: file, title: title, project: project)?.name ?? "" }
}

/// What the picker edits: the series of a saved call, or of a call still to come (call setup).
struct SeriesAnchor {
    var file: String?
    var title: String
    var project: String
}

/// PB-109: "Series" on the call page and in call setup. The name, the calls in the series (take one out), suggestions
/// and every earlier call of the same project with a search box (newest first, title, date, length). Nothing is added
/// without the PM's click; calls of another project are never listed.
struct SeriesPickerView: View {
    let anchor: SeriesAnchor
    var done: () -> Void = {}
    @ObservedObject private var store = CallSeriesStore.shared
    @State private var name = ""
    @State private var query = ""

    private var book: CallSeriesBook { store.book }
    private var manual: CallSeriesBook.Series? {
        anchor.file.map { book.manual(file: $0, title: anchor.title, project: anchor.project) } ?? book.manual(title: anchor.title, project: anchor.project)
    }
    /// The calls of this project only (another project's calls never reach the picker).
    private var all: [CallSeriesBook.Call] { store.allRecords.filter { $0.project == anchor.project }.map(CallSeriesStore.call) }
    private func members(_ all: [CallSeriesBook.Call]) -> [CallSeriesBook.Call] {
        if let f = anchor.file {
            return CallSeriesBook.unique(book.members(of: .init(file: f, title: anchor.title, project: anchor.project, start: .distantPast), in: all))
        }
        return CallSeriesBook.unique(book.members(title: anchor.title, project: anchor.project, in: all))
    }

    /// Speaker names per call, read once per call while the picker is open (suggestions by people).
    @State private var peopleCache: [String: [String]] = [:]
    private func people(_ c: CallSeriesBook.Call) -> [String] {
        if let p = peopleCache[c.key] { return p }
        let p = AfterCall.people(c.file)
        DispatchQueue.main.async { peopleCache[c.key] = p }
        return p
    }

    /// The manual series to change: the one the anchor is in, or a new one made from the anchor on the first change.
    private func ensure() -> String {
        if let s = manual { return s.id }
        let alone = anchor.file.map { book.isAlone($0, project: anchor.project) } ?? false
        var id = ""
        store.update { b in
            let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
            id = b.create(project: anchor.project, name: n.isEmpty ? anchor.title : n, title: alone ? nil : anchor.title,
                          calls: alone ? [anchor.file ?? ""].filter { !$0.isEmpty } : []).id
        }
        return id
    }

    private static let df: DateFormatter = { let f = DateFormatter(); f.dateFormat = "EEE d MMM yyyy, HH:mm"; return f }()

    private func line(_ c: CallSeriesBook.Call) -> String {
        [Self.df.string(from: c.start), c.minutes.map { L("call.page.minutes", ["n": "\(max(1, Int($0.rounded())))"]) } ?? ""]
            .filter { !$0.isEmpty }.joined(separator: " · ")
    }

    @ViewBuilder private func row(_ c: CallSeriesBook.Call, button: String, _ run: @escaping () -> Void) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(c.title).font(PN.Font.body).foregroundStyle(PN.C.text).lineLimit(1)
                Text(line(c)).font(PN.Font.caption).foregroundStyle(PN.C.text3)
            }
            Spacer()
            Button(button, action: run).buttonStyle(.pnSecondary)
        }
    }

    var body: some View {
        let calls = all
        let mine = members(calls)
        let keys = Set(mine.map(\.key))
        let cands = CallSeriesBook.candidates(calls, project: anchor.project, excluding: keys, query: query)
        let titles = [manual?.name ?? anchor.title, anchor.title] + mine.map(\.title)
        let names = Set(mine.flatMap { people($0) })
        let suggested = query.isEmpty ? CallSeriesBook.suggested(cands, project: anchor.project, titles: titles, people: names, peopleOf: { people($0) }) : []
        let shown = cands.filter { c in !suggested.contains(where: { $0.key == c.key }) }.prefix(40)
        VStack(alignment: .leading, spacing: 8) {
            Text(L("seriesPick.title")).font(PN.Font.body.weight(.semibold)).foregroundStyle(PN.C.text)
            Text(L("seriesPick.hint", ["project": anchor.project])).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                TextField("", text: $name, prompt: Text(L("seriesPick.name"))).textFieldStyle(.roundedBorder)
                    .onSubmit { saveName() }
                Button(L("seriesPick.rename")) { saveName() }.buttonStyle(.pnSecondary)
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name == (manual?.name ?? anchor.title))
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("seriesPick.in", ["n": "\(mine.count)"])).font(PN.Font.caption.weight(.semibold)).foregroundStyle(PN.C.text2)
                    if mine.isEmpty { Text(L("seriesPick.none")).font(PN.Font.caption).foregroundStyle(PN.C.text3) }
                    ForEach(mine, id: \.key) { c in
                        row(c, button: L("seriesPick.takeOut")) { store.update { $0.takeOut(c.file, project: anchor.project) } }
                    }
                    if !suggested.isEmpty {
                        Text(L("seriesPick.suggested")).font(PN.Font.caption.weight(.semibold)).foregroundStyle(PN.C.text2).padding(.top, 6)
                        ForEach(suggested, id: \.key) { c in row(c, button: L("seriesPick.add")) { add(c) } }
                    }
                    Text(L("seriesPick.earlier")).font(PN.Font.caption.weight(.semibold)).foregroundStyle(PN.C.text2).padding(.top, 6)
                    TextField("", text: $query, prompt: Text(L("seriesPick.search"))).textFieldStyle(.roundedBorder)
                    if shown.isEmpty { Text(L("seriesPick.noCalls")).font(PN.Font.caption).foregroundStyle(PN.C.text3) }
                    ForEach(Array(shown), id: \.key) { c in row(c, button: L("seriesPick.add")) { add(c) } }
                }
            }
            HStack { Spacer(); Button(L("seriesPick.done"), action: done).buttonStyle(.pnPrimary).keyboardShortcut(.defaultAction) }
        }
        .padding(12)
        .frame(width: 440, height: 520)
        .onAppear { name = manual?.name ?? anchor.title }
    }

    private func add(_ c: CallSeriesBook.Call) {
        guard c.project == anchor.project else { return }          // never another project's call
        let id = ensure()
        store.update { $0.add(c.file, to: id) }
    }

    private func saveName() {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !n.isEmpty else { return }
        let id = ensure()
        store.update { $0.rename(id, to: n) }
    }
}
