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

    /// Reads the ticked pages into `outDir`, one `webfetch` per page (the contract of spec section 7 with a one-page plan and
    /// `start_n`): the app knows the current page, a cancel ends only that page's process, and the pages read stay.
    /// `done` gets the report once.
    func read(outDir: String, done onDone: @escaping (SiteReadReport) -> Void) {
        guard let plan, phase == .ready || phase == .finished, plan.tickedCount > 0 else { return }
        guard !SheetShots.active else { error = L("sheet.ui.err.noEngine"); phase = .failed; return }
        try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
        cancelPath = (outDir as NSString).appendingPathComponent(".cancel")
        try? FileManager.default.removeItem(atPath: cancelPath)
        phase = .reading; cancelling = false; lostOnCancel = false; done = 0; total = plan.tickedCount; report = nil
        pageStates = [:]; currentPath = ""
        runID = UUID()
        readPage(0, of: plan.ticked, plan: plan, outDir: outDir, acc: SiteReadReport(), run: runID, onDone)
    }

    private func readPage(_ i: Int, of pages: [SitePlanPage], plan: SitePlan, outDir: String, acc: SiteReadReport, run: UUID, _ onDone: @escaping (SiteReadReport) -> Void) {
        guard run == runID else { return }
        if cancelling || i >= pages.count { return finish(acc, pages: pages, onDone) }
        let page = pages[i]
        done = i; currentPath = page.path
        let req = SiteResearch.fetchRequest(plan: plan, outDir: outDir, cancelPath: cancelPath, startN: i + 1, only: page.url)
        let path = (SheetCLI.stateDir as NSString).appendingPathComponent("webfetch-\(UUID().uuidString.prefix(8)).request.json")
        guard SheetCLI.writeRequest(req, to: path), let p = SheetCLI.process("webfetch", ["--request", path]) else {
            error = L("sheet.ui.err.noEngine"); phase = .failed; return
        }
        let o = Pipe()
        p.standardOutput = o; p.standardError = FileHandle.nullDevice
        process = p
        DispatchQueue.global().async {
            var r = SheetCLI.Result(code: 127, json: nil, lines: [], text: "")
            if (try? p.run()) != nil {
                let out = o.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                r = SheetCLI.parse(out, code: p.terminationStatus)
            }
            try? FileManager.default.removeItem(atPath: path)
            DispatchQueue.main.async { MainActor.assumeIsolated {
                guard run == self.runID else { return }
                self.process = nil
                var acc = acc
                // a page cut off by Cancel is discarded: it has no state and no source
                if !self.cancelling { self.take(r, page: page, into: &acc) }
                self.done = i + 1
                if self.cancelling || i + 1 >= pages.count { return self.finish(acc, pages: pages, onDone) }
                let next = acc
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { MainActor.assumeIsolated { self.readPage(i + 1, of: pages, plan: plan, outDir: outDir, acc: next, run: run, onDone) } }
            } }
        }
    }

    /// One page's answer: its sources join the report; its state and reason come from the report (the sidecar's `pages` row,
    /// else its sources); a failed run is an error with the engine's message.
    private func take(_ r: SheetCLI.Result, page: SitePlanPage, into acc: inout SiteReadReport) {
        guard let j = r.json, let rep = SiteReadReport(json: j) else {
            pageStates[page.url] = ("error", SheetCLI.error(r) ?? L("sheet.ui.err.noResult"))
            return
        }
        acc.merge(rep)
        if let st = rep.states[page.url] ?? rep.states.values.first { pageStates[page.url] = st; return }
        if let c = rep.children.first { pageStates[page.url] = (c.used == "not_used" ? "empty" : "read", c.reason ?? ""); return }
        let k = ["blocked", "error", "duplicate", "skipped", "empty"].first { (rep.counts[$0] ?? 0) > 0 } ?? "error"
        pageStates[page.url] = (k, "")
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

    /// Stops at once for the person: the page being read is ended (its partial result is dropped), the pages already read
    /// are kept. Only the process of this read is ended, never one of a later read.
    func cancel() {
        guard phase == .reading else { return }
        cancelling = true
        FileManager.default.createFile(atPath: cancelPath, contents: Data())
        if let p = process, p.isRunning { p.terminate() }
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
        ScrollView {
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
        .frame(maxHeight: 240)
    }

    private func summary(_ plan: SitePlan) -> some View {
        let tokens: Int = session.report?.tokens ?? plan.tickedTokens
        return VStack(alignment: .leading, spacing: 6) {
            Stepper(value: Binding(get: { plan.budget }, set: { session.plan?.setBudget($0) }), in: 1...SiteResearch.maxBudget) {
                Text(L("sheet.ui.web.counter", ["n": "\(plan.tickedCount)", "b": "\(plan.budget)"])).font(th.homeText(13, .semibold)).foregroundStyle(PN.C.text)
            }
            .disabled(session.phase == .reading).fixedSize()
            Text("~" + SheetLabels.tokens(tokens) + " · " + costLine(tokens)).font(th.homeMono(11)).foregroundStyle(PN.C.text2)
                .fixedSize(horizontal: false, vertical: true)
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
