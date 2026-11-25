import Foundation

/// PB-109 (owner 25.11): call series the PM puts together by hand. Without it a series is the calls of one project with
/// the same title (title key, letters and digits only), as before. The book adds:
/// - a manual series: a name, the title keys whose calls of the project still join by themselves, and calls picked by
///   hand (any title of the same project);
/// - calls taken out ("alone"): a call the PM took out of its series joins no series by its title any more.
/// A call is in one series at most: a call picked into a series leaves every other one. Calls of another project are
/// never members: a series and its calls always share the project (a call moved to another project is out).
/// Register items keep their `series` field (rules/commitments.yaml > series_key); which items a series shows is
/// worked out from its calls (`items`), so old keys keep working and adding or taking out a call moves its items.
public struct CallSeriesBook: Codable, Equatable {
    public struct Series: Codable, Equatable, Identifiable {
        public var id: String
        public var project: String          // the project name, as CallHistory records it
        public var name: String
        public var titles: [String]         // title keys whose calls of the project join by themselves
        public var calls: [String]          // call keys (CommitmentRegister.callKey) picked by hand
    }

    /// A call as the book sees it (CallHistory record).
    public struct Call: Equatable {
        public var file: String
        public var title: String
        public var project: String
        public var start: Date
        public var minutes: Double?
        public init(file: String, title: String, project: String, start: Date, minutes: Double? = nil) {
            self.file = file; self.title = title; self.project = project; self.start = start; self.minutes = minutes
        }
        public var key: String { CommitmentRegister.callKey(file) }
    }

    public var series: [Series] = []
    /// "<project>|<call key>": calls taken out of their series; their title no longer brings them into one.
    public var alone: [String] = []

    public init() {}

    /// The same title key as EditLog.seriesKey and the title part of CommitmentRegister.seriesKey.
    public static func titleKey(_ title: String) -> String {
        title.lowercased().replacingOccurrences(of: "[^\\p{L}\\p{N}]", with: "", options: .regularExpression)
    }

    static func aloneKey(_ project: String, _ file: String) -> String { project + "|" + CommitmentRegister.callKey(file) }

    public func isAlone(_ file: String, project: String) -> Bool { alone.contains(Self.aloneKey(project, file)) }

    // MARK: which series

    /// The manual series of a call: picked by hand, else by its title (unless the call was taken out).
    public func manual(file: String, title: String, project: String) -> Series? {
        let k = CommitmentRegister.callKey(file)
        if let s = series.first(where: { $0.project == project && $0.calls.contains(k) }) { return s }
        if isAlone(file, project: project) { return nil }
        let t = Self.titleKey(title)
        return t.isEmpty ? nil : series.first { $0.project == project && $0.titles.contains(t) }
    }

    /// The manual series of a call still to come (call setup): by its title.
    public func manual(title: String, project: String) -> Series? {
        let t = Self.titleKey(title)
        return t.isEmpty ? nil : series.first { $0.project == project && $0.titles.contains(t) }
    }

    public func manual(id: String) -> Series? { series.first { $0.id == id } }

    /// The calls of a manual series among `all`, newest first.
    public func members(_ s: Series, in all: [Call]) -> [Call] {
        Self.unique(all.filter { $0.project == s.project && manual(file: $0.file, title: $0.title, project: $0.project)?.id == s.id })
    }

    /// Newest first, one entry per call (the app's and the skill's transcript of one call are one call).
    public static func unique(_ calls: [Call]) -> [Call] {
        var seen = Set<String>()
        return calls.sorted { $0.start > $1.start }.filter { seen.insert($0.key).inserted }
    }

    /// The automatic series of a title: the calls of the project with that title that are not alone and not in a
    /// manual series, newest first.
    public func automatic(title: String, project: String, in all: [Call]) -> [Call] {
        let t = Self.titleKey(title)
        guard !t.isEmpty else { return [] }
        return all.filter { $0.project == project && Self.titleKey($0.title) == t && !isAlone($0.file, project: project)
            && manual(file: $0.file, title: $0.title, project: project) == nil }.sorted { $0.start > $1.start }
    }

    /// The series of a saved call, newest first (the call itself included).
    public func members(of c: Call, in all: [Call]) -> [Call] {
        if let s = manual(file: c.file, title: c.title, project: c.project) { return members(s, in: all) }
        if isAlone(c.file, project: c.project) { return all.filter { $0.key == c.key && $0.project == c.project } }
        return automatic(title: c.title, project: c.project, in: all)
    }

    /// The earlier calls of a call still to come (call setup, upcoming page, preparation), newest first.
    public func members(title: String, project: String, in all: [Call]) -> [Call] {
        if let s = manual(title: title, project: project) { return members(s, in: all) }
        return automatic(title: title, project: project, in: all)
    }

    /// How the preparation (every route) and the people of a call still to come pick its earlier calls.
    public enum Previous: Equatable {
        /// A manual series has this title: its calls, whatever their titles.
        case manual(Series)
        /// The old pick by title, skipping these call keys (taken out, or picked into a manual series).
        case title(exclude: Set<String>)
    }

    /// Nil when the book has nothing for these projects: then everything works exactly as before PB-109.
    public func previous(title: String, projects: Set<String>) -> Previous? {
        for p in projects.sorted() { if let s = manual(title: title, project: p) { return .manual(s) } }
        let exclude = excluded(projects: projects)
        return exclude.isEmpty ? nil : .title(exclude: exclude)
    }

    /// The keys of the preparation request the packed ways read (localprep.series_previous, series_open_text): a manual
    /// series sends its name, calls (an empty list means none, no fallback to the title) and open commitments; the pick
    /// by title sends the call keys to skip.
    public static func prepRequest(name: String?, calls: [String], open: [String], exclude: Set<String>) -> [String: Any] {
        if let n = name { return ["series_name": n, "series_calls": calls, "series_open": open] }
        return ["series_exclude": exclude.sorted()]
    }

    /// The call keys of these projects that the pick by title skips: taken out, or picked into a manual series.
    public func excluded(projects: Set<String>) -> Set<String> {
        let claimed = series.filter { projects.contains($0.project) }.flatMap(\.calls)
        let out = alone.compactMap { a -> String? in
            guard let i = a.firstIndex(of: "|"), projects.contains(String(a[..<i])) else { return nil }
            return String(a[a.index(after: i)...])
        }
        return Set(claimed + out)
    }

    /// The title keys that bring a call into this series: the manual series' titles, else the title's own key.
    public func titles(file: String? = nil, title: String, project: String) -> [String] {
        if let f = file, isAlone(f, project: project), manual(file: f, title: title, project: project) == nil { return [] }
        let s = file.map { manual(file: $0, title: title, project: project) } ?? manual(title: title, project: project)
        return s?.titles ?? [Self.titleKey(title)]
    }

    /// The register items of a series: every item of its calls; an item of a call that is not among the known calls
    /// (the skill's, an old one) by its series key when that title is one of the series' titles. An item of a call
    /// taken out or picked into another series is not shown here.
    public func items(_ items: [Commitment], members: [Call], titles: [String], project: String) -> [Commitment] {
        let mine = Set(members.map(\.key))
        let claimed = Set(series.filter { $0.project == project }.flatMap(\.calls))
        return items.filter { c in
            let k = CommitmentRegister.callKey(c.call)
            if mine.contains(k) { return true }
            if claimed.contains(k) || alone.contains(project + "|" + k) { return false }
            let t = c.series.split(separator: "|", maxSplits: 1).last.map(String.init) ?? ""
            return !t.isEmpty && titles.contains(t)
        }
    }

    // MARK: changes (each one only on the PM's click)

    /// Makes a manual series and returns it. `title` (optional) is the title whose calls keep joining by themselves;
    /// a title belongs to one series of a project.
    @discardableResult
    public mutating func create(project: String, name: String, title: String?, calls: [String] = [], id: String = "s-" + UUID().uuidString.prefix(8).lowercased()) -> Series {
        let t = title.map(Self.titleKey) ?? ""
        if !t.isEmpty { for i in series.indices where series[i].project == project { series[i].titles.removeAll { $0 == t } } }
        let s = Series(id: id, project: project, name: name.trimmingCharacters(in: .whitespacesAndNewlines), titles: t.isEmpty ? [] : [t], calls: [])
        series.append(s)
        for f in calls { add(f, to: id) }
        return manual(id: id) ?? s
    }

    /// Puts a call into a series: it leaves any other series and is no longer alone.
    public mutating func add(_ file: String, to id: String) {
        guard let i = series.firstIndex(where: { $0.id == id }) else { return }
        let k = CommitmentRegister.callKey(file), p = series[i].project
        for j in series.indices where series[j].project == p { series[j].calls.removeAll { $0 == k } }
        alone.removeAll { $0 == p + "|" + k }
        series[i].calls.append(k)
    }

    /// Takes a call out of its series (manual or automatic): it stays alone until the PM picks it into a series.
    public mutating func takeOut(_ file: String, project: String) {
        let k = CommitmentRegister.callKey(file)
        for j in series.indices where series[j].project == project { series[j].calls.removeAll { $0 == k } }
        if !alone.contains(project + "|" + k) { alone.append(project + "|" + k) }
    }

    public mutating func rename(_ id: String, to name: String) {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !n.isEmpty, let i = series.firstIndex(where: { $0.id == id }) else { return }
        series[i].name = n
    }

    /// The call moved to another project (or was deleted): it leaves every series and the alone list.
    public mutating func forget(_ file: String) {
        let k = CommitmentRegister.callKey(file)
        for j in series.indices { series[j].calls.removeAll { $0 == k } }
        alone.removeAll { $0.hasSuffix("|" + k) }
    }

    // MARK: the picker

    /// The calls the PM can pick: the same project only, not already in this series, newest first, filtered by the
    /// search text (title words or the date "2026-11-03").
    public static func candidates(_ all: [Call], project: String, excluding: Set<String>, query: String = "") -> [Call] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX")
        var seen = Set<String>()
        return all.filter { $0.project == project && !excluding.contains($0.key) }
            .sorted { $0.start > $1.start }
            .filter { c in
                guard seen.insert(c.key).inserted else { return false }
                return q.isEmpty || c.title.lowercased().contains(q) || f.string(from: c.start).contains(q)
            }
    }

    /// Suggestions only (never added without a click): candidates of the same project with a similar title or a
    /// person of the series. `titles`: the series' name and its calls' titles as written; `peopleOf` gives the speaker
    /// names of a call.
    public static func suggested(_ candidates: [Call], project: String, titles: [String], people: Set<String>, peopleOf: (Call) -> [String], limit: Int = 5) -> [Call] {
        let names = Set(people.map { $0.lowercased() })
        let keys = titles.map(titleKey).filter { !$0.isEmpty }
        return Array(candidates.filter { c in
            guard c.project == project else { return false }          // never another project's call
            let k = titleKey(c.title)
            if !k.isEmpty, keys.contains(where: { $0 == k || (min($0.count, k.count) >= 4 && ($0.contains(k) || k.contains($0))) }) { return true }
            if titles.contains(where: { CommitmentRegister.similar($0, c.title, share: 0.5) }) { return true }
            return !names.isEmpty && peopleOf(c).contains { names.contains($0.lowercased()) }
        }.prefix(limit))
    }
}
