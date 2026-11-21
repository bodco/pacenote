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
    /// The read was stopped and the engine did not hand back its report (nothing was added).
    @Published var lostOnCancel = false

    private var process: Process?
    private var poll: Timer?
    private var cancelPath = ""

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

    /// Reads the ticked pages into `outDir`; `done` gets the report once (the pages already read stay on a cancel).
    func read(outDir: String, done onDone: @escaping (SiteReadReport) -> Void) {
        guard let plan, phase == .ready || phase == .finished, plan.tickedCount > 0 else { return }
        try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
        cancelPath = (outDir as NSString).appendingPathComponent(".cancel")
        try? FileManager.default.removeItem(atPath: cancelPath)
        let before = Self.copies(outDir)
        let req = SiteResearch.fetchRequest(plan: plan, outDir: outDir, cancelPath: cancelPath)
        let path = (SheetCLI.stateDir as NSString).appendingPathComponent("webfetch-\(UUID().uuidString.prefix(8)).request.json")
        guard !SheetShots.active, SheetCLI.writeRequest(req, to: path), let p = SheetCLI.process("webfetch", ["--request", path]) else {
            error = L("sheet.ui.err.noEngine"); phase = .failed; return
        }
        phase = .reading; cancelling = false; lostOnCancel = false; self.done = 0; total = plan.tickedCount; report = nil
        let o = Pipe()
        p.standardOutput = o; p.standardError = FileHandle.nullDevice
        process = p
        poll = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { guard let s = self else { return }; s.done = min(s.total, Self.copies(outDir).subtracting(before).count) } }
        }
        DispatchQueue.global().async {
            var r = SheetCLI.Result(code: 127, json: nil, lines: [], text: "")
            if (try? p.run()) != nil {
                let out = o.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                r = SheetCLI.parse(out, code: p.terminationStatus)
            }
            DispatchQueue.main.async { MainActor.assumeIsolated { self.finish(r, onDone) } }
        }
    }

    private static func copies(_ dir: String) -> Set<String> {
        Set(((try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []).filter { $0.hasSuffix(".txt") })
    }

    private func finish(_ r: SheetCLI.Result, _ onDone: (SiteReadReport) -> Void) {
        poll?.invalidate(); poll = nil; process = nil
        try? FileManager.default.removeItem(atPath: cancelPath)
        if let j = r.json, let rep = SiteReadReport(json: j) {
            report = rep; done = rep.counts["read"] ?? done; phase = .finished
            onDone(rep)
        } else if cancelling {
            lostOnCancel = true; phase = .ready
        } else {
            error = SheetCLI.error(r) ?? L("sheet.ui.err.noResult"); phase = .failed
        }
    }

    /// Stops at once for the person: the engine looks for the file between pages and hands back what it read; if it
    /// does not answer in a few seconds the process is ended and nothing is added.
    func cancel() {
        guard phase == .reading else { return }
        cancelling = true
        FileManager.default.createFile(atPath: cancelPath, contents: Data())
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { [weak self] in
            MainActor.assumeIsolated { if let p = self?.process, p.isRunning { p.terminate() } }
        }
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
                            let line = ([p.kind == "page" ? "" : p.kind, p.whyRanked].filter { !$0.isEmpty } + [plan.reason(p).map { L("sheet.ui.web.why." + $0) }].compactMap { $0 })
                                .joined(separator: " · ")
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
                Text(session.cancelling ? L("sheet.ui.web.stopping") : L("sheet.ui.web.progress", ["d": "\(min(session.done + 1, session.total))", "n": "\(session.total)"]))
                    .font(th.homeText(13)).foregroundStyle(PN.C.text2)
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
