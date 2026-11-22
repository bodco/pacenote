import AppKit
import LiveTipsCore
import SwiftUI

// PB-37 (G6, subtask C): "Research this website" in Sources. The plan of one site (webplan), the ticks and the page
// budget, the cost line, the read (webfetch) with progress and Cancel. The panel is one view shared by Expert and,
// later (FB7), Quick. The pure part (contract, ticks, cost, grouping) is LiveTipsCore/SiteResearch.swift.

@MainActor
final class SiteResearchSession: ObservableObject {
    enum Phase: Equatable { case planning, ready, reading, finished, failed }

    let sourceID: String
    let url: String
    @Published var phase: Phase = .planning
    @Published var plan: SitePlan?
    @Published var error = ""
    @Published var done = 0
    @Published var total = 0
    @Published var report: SiteReadReport?
    @Published var cancelling = false
    /// The read was stopped before any page was read (nothing was added).
    @Published var lostOnCancel = false
    /// The page being read and, after the read, every page's state and reason as the engine reported them (stopped = not reached).
    @Published var currentPath = ""
    @Published var pageStates: [String: (state: String, note: String)] = [:]

    private var process: Process?
    private var cancelPath = ""
    private var runID = UUID()

    /// No read process: what the shot harness stages (also checked by source in CoreChecks).
    var idle: Bool { process == nil }

    init(sourceID: String, url: String) { self.sourceID = sourceID; self.url = url }

    func loadPlan(request: [String: Any]) {
        phase = .planning; plan = nil; error = ""; report = nil
        SheetCLI.run("webplan", request: request) { r in
            if let p = r.json.flatMap({ SitePlan(json: $0) }), r.json?["error"] == nil {
                self.plan = p
                self.phase = .ready
            } else {
                self.error = SheetCLI.error(r) ?? L("sheet.ui.err.noResult")
                self.phase = .failed
            }
        }
    }

    /// Reads the ticked pages into `outDir` with one `webfetch` call (spec section 7): the sidecar keeps the unique file names,
    /// the duplicate check and the crawl delay, prints one JSON line per page (the current page for the panel) and the report
    /// as the last line. `done` gets the report once.
    func read(outDir: String, done onDone: @escaping (SiteReadReport) -> Void) {
        guard let plan, phase == .ready || phase == .finished, plan.tickedCount > 0 else { return }
        guard !SheetShots.active else { error = L("sheet.ui.err.noEngine"); phase = .failed; return }
        try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
        cancelPath = (outDir as NSString).appendingPathComponent(".cancel")
        try? FileManager.default.removeItem(atPath: cancelPath)
        let req = SiteResearch.fetchRequest(plan: plan, outDir: outDir, cancelPath: cancelPath)
        let path = (SheetCLI.stateDir as NSString).appendingPathComponent("webfetch-\(UUID().uuidString.prefix(8)).request.json")
        guard SheetCLI.writeRequest(req, to: path), let p = SheetCLI.process("webfetch", ["--request", path]) else {
            error = L("sheet.ui.err.noEngine"); phase = .failed; return
        }
        phase = .reading; cancelling = false; lostOnCancel = false; done = 0; total = plan.tickedCount; report = nil
        pageStates = [:]; currentPath = ""
        runID = UUID()
        let run = runID, pages = plan.ticked
        let o = Pipe()
        p.standardOutput = o; p.standardError = FileHandle.nullDevice
        process = p
        DispatchQueue.global().async {
            var r = SheetCLI.Result(code: 127, json: nil, lines: [], text: "")
            if (try? p.run()) != nil {
                var all = Data(), pending = Data()
                while true {
                    let d = o.fileHandleForReading.availableData
                    if d.isEmpty { break }
                    all.append(d); pending.append(d)
                    while let nl = pending.firstIndex(of: 10) {
                        let line = pending.prefix(upTo: nl)
                        pending = Data(pending.suffix(from: nl + 1))
                        if let j = try? JSONSerialization.jsonObject(with: line) as? [String: Any], let s = SiteResearch.progressStart(j) {
                            DispatchQueue.main.async { MainActor.assumeIsolated {
                                guard run == self.runID else { return }
                                self.currentPath = s.path; self.done = max(s.n - 1, 0)
                            } }
                        }
                    }
                }
                p.waitUntilExit()
                r = SheetCLI.parse(all, code: p.terminationStatus)
            }
            try? FileManager.default.removeItem(atPath: path)
            DispatchQueue.main.async { MainActor.assumeIsolated {
                guard run == self.runID else { return }
                self.finish(self.take(r, pages: pages), pages: pages, onDone)
            } }
        }
    }

    /// The end of the call: every ticked page's state and reason come from the report (the sidecar's `pages` rows); when the
    /// process was ended before its report, from the page lines it had printed. A page without a row was not reached.
    private func take(_ r: SheetCLI.Result, pages: [SitePlanPage]) -> SiteReadReport {
        var rep = r.json.flatMap { SiteReadReport(json: $0) }
        if rep == nil, cancelling || r.json?["error"] == nil { rep = SiteReadReport(progress: r.lines, url: plan?.url ?? url) }
        guard let rep else {
            if !cancelling { for p in pages { pageStates[p.url] = ("error", SheetCLI.error(r) ?? L("sheet.ui.err.noResult")) } }
            return SiteReadReport()
        }
        for p in pages { if let st = rep.states[p.url] { pageStates[p.url] = st } }
        return rep
    }

    private func finish(_ acc: SiteReadReport, pages: [SitePlanPage], _ onDone: (SiteReadReport) -> Void) {
        process = nil
        try? FileManager.default.removeItem(atPath: cancelPath)
        var rep = acc
        for p in pages where pageStates[p.url] == nil { pageStates[p.url] = ("stopped", "") }
        var c: [String: Int] = [:]
        for (_, v) in pageStates where v.state != "stopped" { c[SiteResearch.stateKey(v.state), default: 0] += 1 }
        rep.counts = c
        if cancelling && rep.children.isEmpty {
            lostOnCancel = true; phase = .ready
        } else {
            report = rep; done = c["read"] ?? 0; phase = .finished
            onDone(rep)
        }
    }

    /// The sidecar stops before the next page and returns what it read. A page slow to answer does not hold the person: after
    /// a short grace the process is ended and the pages already printed are kept. Only the process of this read is ended.
    func cancel() {
        guard phase == .reading else { return }
        cancelling = true
        FileManager.default.createFile(atPath: cancelPath, contents: Data())
        let run = runID
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { MainActor.assumeIsolated {
            if run == self.runID, let p = self.process, p.isRunning { p.terminate() }
        } }
    }

    func close() { if phase != .reading { phase = .ready } }
}

/// The panel under a website source in Sources.
struct SheetSiteResearchPanel: View {
    @ObservedObject var draft: SheetDraft
    @ObservedObject var session: SiteResearchSession
    var onClose: () -> Void
    @Environment(\.pnTheme) private var th

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("sheet.ui.web.title", ["domain": SiteResearch.host(session.url)])).font(th.homeText(14, .semibold)).foregroundStyle(PN.C.text)
            switch session.phase {
            case .planning:
                HStack(spacing: 6) { PNSpinner(size: 16); Text(L("sheet.ui.web.planning")).font(th.homeText(13)).foregroundStyle(PN.C.text2) }
            case .failed:
                Text(L("sheet.ui.web.planFailed", ["e": session.error])).font(th.homeText(13)).foregroundStyle(PN.C.pending).fixedSize(horizontal: false, vertical: true)
                HStack { Button(L("sheet.ui.web.retry")) { draft.researchSite(sourceID: session.sourceID) }.buttonStyle(.pnSecondary)
                         Button(L("sheet.ui.web.close"), action: onClose).buttonStyle(.pnTertiary) }
            case .ready, .reading, .finished:
                if let plan = session.plan { content(plan) }
            }
        }
        .padding(12)
        .overlay(RoundedRectangle(cornerRadius: th.style == .classic ? PN.Radius.s : 0).strokeBorder(PN.C.ruleStrong, lineWidth: 1))
    }

    @ViewBuilder private func content(_ plan: SitePlan) -> some View {
        if plan.startStatus != "ok" {
            Text(L("sheet.ui.web.start." + (plan.startStatus == "blocked" ? "blocked" : "error"), ["e": plan.startNote]))
                .font(th.homeText(13)).foregroundStyle(PN.C.pending).fixedSize(horizontal: false, vertical: true)
            Button(L("sheet.ui.web.close"), action: onClose).buttonStyle(.pnTertiary)
        } else {
            pageList(plan)
            summary(plan)
            footer(plan)
        }
    }

    /// After a read the line of a page is what the engine reported for it (state and reason), not the plan's old reason.
    private func pageLine(_ plan: SitePlan, _ p: SitePlanPage) -> String {
        if session.phase != .ready, let st = session.pageStates[p.url] {
            let note = st.note.isEmpty ? "" : " (" + st.note + ")"
            return L("sheet.ui.web.pageState." + (st.state == "stopped" ? "stopped" : SiteResearch.stateKey(st.state))) + note
        }
        return ([p.kind == "page" ? "" : p.kind, p.whyRanked].filter { !$0.isEmpty } + [plan.reason(p).map { L("sheet.ui.web.why." + $0) }].compactMap { $0 })
            .joined(separator: " · ")
    }

    private func pageList(_ plan: SitePlan) -> some View {
        // the list grows with the page (no inner scroll: the wheel would stick on it); at most 30 rows
        VStack(alignment: .leading, spacing: 0) {
            ForEach(plan.pages) { p in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Toggle("", isOn: Binding(get: { p.ticked }, set: { _ in session.plan?.toggle(p.url) })).toggleStyle(.pnCheckbox).labelsHidden()
                        .disabled(session.phase == .reading || !p.tickable || (!p.ticked && plan.tickedCount >= plan.budget))
                        .accessibilityLabel(p.path)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(p.path).font(th.homeMono(12)).foregroundStyle(p.ticked ? PN.C.text : PN.C.text3).lineLimit(1).truncationMode(.middle)
                        let line = pageLine(plan, p)
                        if !line.isEmpty { Text(line).font(th.homeText(12)).foregroundStyle(PN.C.text3).lineLimit(2) }
                    }
                    Spacer(minLength: 6)
                    if p.ticked { Text("~" + SheetLabels.tokens(p.estTokens)).font(th.homeMono(11)).foregroundStyle(PN.C.text3) }
                }
                .padding(.vertical, 4)
            }
        }
    }

    private func summary(_ plan: SitePlan) -> some View {
        let tokens: Int = session.report?.tokens ?? plan.tickedTokens
        return VStack(alignment: .leading, spacing: 6) {
            // square buttons instead of the system Stepper (it draws the rounded macOS control)
            HStack(spacing: 8) {
                Text(L("sheet.ui.web.counter", ["n": "\(plan.tickedCount)", "b": "\(plan.budget)"])).font(th.homeText(13, .semibold)).foregroundStyle(PN.C.text)
                Button("-") { session.plan?.setBudget(plan.budget - 1) }.buttonStyle(.pnSecondary).frame(minWidth: 28, minHeight: 28)
                    .disabled(session.phase == .reading || plan.budget <= 1).accessibilityLabel(L("sheet.ui.web.fewer"))
                Button("+") { session.plan?.setBudget(plan.budget + 1) }.buttonStyle(.pnSecondary).frame(minWidth: 28, minHeight: 28)
                    .disabled(session.phase == .reading || plan.budget >= SiteResearch.maxBudget).accessibilityLabel(L("sheet.ui.web.more"))
            }
            if session.phase != .reading, plan.tickedCount >= plan.budget, plan.pages.contains(where: { $0.tickable && !$0.ticked }) {
                Text(L("sheet.ui.web.budgetReached")).font(th.homeText(12)).foregroundStyle(PN.C.text2).fixedSize(horizontal: false, vertical: true)
            }
            Text("~" + SheetLabels.tokens(tokens)).font(th.homeMono(11)).foregroundStyle(PN.C.text2)
            Text(costLine(tokens)).font(th.homeText(12)).foregroundStyle(PN.C.text2).fixedSize(horizontal: false, vertical: true)
            Text(L("sheet.ui.web.found", ["n": "\(plan.found)", "k": "\(plan.tickedCount)"])).font(th.homeText(12)).foregroundStyle(PN.C.text3)
        }
    }

    private func costLine(_ tokens: Int) -> String {
        let route = AIRoute.name(draft.route)
        switch SiteResearch.cost(tokens: tokens, route: draft.route, window: SheetRoutes.window(draft.route)) {
        case .dollars(_, let usd): return L("sheet.ui.web.cost.usd", ["usd": usd < 0.01 ? "<$0.01" : String(format: "$%.2f", usd), "route": route])
        case .free: return L("sheet.ui.web.cost.free", ["route": route])
        case .window(_, let p): return L("sheet.ui.web.cost.window", ["p": "\(max(p, 1))", "route": route])
        }
    }

    @ViewBuilder private func footer(_ plan: SitePlan) -> some View {
        switch session.phase {
        case .reading:
            HStack(spacing: 8) {
                PNSpinner(size: 16)
                Text(session.cancelling ? L("sheet.ui.web.stopping") : L("sheet.ui.web.progress", ["d": "\(min(session.done + 1, session.total))", "n": "\(session.total)", "p": session.currentPath]))
                    .font(th.homeText(13)).foregroundStyle(PN.C.text2).lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 8)
                Button(L("sheet.ui.web.cancel")) { draft.cancelResearch(sourceID: session.sourceID) }.buttonStyle(.pnSecondary).disabled(session.cancelling)
            }
        case .finished:
            if let r = session.report { result(r) }
            HStack {
                Button(L("sheet.ui.web.again")) { session.phase = .ready }.buttonStyle(.pnSecondary)
                Button(L("sheet.ui.web.close"), action: onClose).buttonStyle(.pnTertiary)
            }
        default:
            if session.lostOnCancel { Text(L("sheet.ui.web.lost")).font(th.homeText(13)).foregroundStyle(PN.C.text2) }
            HStack {
                Button(plan.tickedCount == 1 ? L("sheet.ui.web.readOne") : L("sheet.ui.web.read", ["n": "\(plan.tickedCount)"])) { draft.readPages(sourceID: session.sourceID) }
                    .buttonStyle(.pnPrimary).disabled(plan.tickedCount == 0)
                Button(L("sheet.ui.web.close"), action: onClose).buttonStyle(.pnTertiary)
            }
            Text(L("sheet.ui.web.sent", ["route": AIRoute.name(draft.route)])).font(th.homeText(12)).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private func result(_ r: SiteReadReport) -> some View {
        let n = r.usable.count
        Text(L("sheet.ui.web.done", ["n": "\(n)", "t": SheetLabels.tokens(r.tokens)]) + (session.cancelling ? " " + L("sheet.ui.web.keptOnCancel") : ""))
            .font(th.homeText(13, .semibold)).foregroundStyle(PN.C.text).fixedSize(horizontal: false, vertical: true)
        let left = ["empty", "blocked", "error", "skipped", "duplicate"].compactMap { k -> String? in
            let c = r.counts[k] ?? 0
            return c > 0 ? L("sheet.ui.web.state." + k, ["n": "\(c)"]) : nil
        }
        if !left.isEmpty { Text(left.joined(separator: " · ")).font(th.homeText(12)).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true) }
        if n == 0 && (r.counts["empty"] ?? 0) > 0 {
            Text(L("sheet.ui.web.js")).font(th.homeText(13)).foregroundStyle(PN.C.pending).fixedSize(horizontal: false, vertical: true)
        }
    }
}
