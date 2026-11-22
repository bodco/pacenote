import Foundation
import SwiftUI
import LiveTipsCore

/// PB-32 (FB2, D56, S6): the call page's Output language. Shown only for a call with two or three languages that each
/// have 10% of it. Choosing a language only remembers it for this call (<stem>.outlang.json); nothing is rewritten and
/// nothing is sent until Regenerate. Regenerate writes the notes (full notes, Summary, Recap), the Notion Summary, Recap and
/// meeting notes (wrapup_notion) and an existing follow-up draft again in that language; a call over an hour asks first.
@MainActor
final class OutputLanguageModel: ObservableObject {
    private static var all: [String: OutputLanguageModel] = [:]
    static func of(_ file: String) -> OutputLanguageModel {
        if let m = all[file] { return m }
        let m = OutputLanguageModel(file: file); all[file] = m; return m
    }

    /// A call longer than this asks before Regenerate spends (owner answer to question 2, option C).
    static let longCallMinutes = 60.0

    let file: String
    @Published private(set) var languages: [String] = []
    @Published private(set) var current = CallLanguage.defaultCode
    @Published private(set) var sidecar = CallLanguage.Sidecar()
    @Published private(set) var running = false
    @Published private(set) var failed: String? = nil
    init(file: String) { self.file = file; refresh() }

    /// Read the sidecar and the transcript again (cheap: counting tags and one small file).
    func refresh() {
        let text = (try? String(contentsOfFile: file, encoding: .utf8)) ?? ""
        languages = CallLanguage.toggleLanguages(CallLanguage.spoken(transcript: text))
        sidecar = CallLanguage.readSidecar(transcript: file)
        current = CallLanguage.outputLanguage(transcript: file, text: text)
    }

    var isMulti: Bool { CallLanguage.isMulti(languages) }

    /// The choice is remembered for this call only; no model is called.
    func choose(_ code: String) {
        guard !running else { return }
        CallLanguage.writeSidecar(transcript: file, outputLanguage: code)
        refresh()
    }

    var stale: [String] { sidecar.staleArtifacts }

    /// "Notes: Ukrainian, follow-up: English (out of date)"; empty when nothing was written yet.
    var stateLine: String {
        let rows: [(String, String)] = [("notes", "call.outlang.art.notes"), ("followup", "call.outlang.art.followup"),
                                        ("notion", "call.outlang.art.notion")]
        let stale = Set(self.stale)
        return rows.compactMap { a, key in
            sidecar.artifacts[a].map { L(key) + ": " + CallLanguage.name($0) + (stale.contains(a) ? " (" + L("call.outlang.outOfDate") + ")" : "") }
        }.joined(separator: ", ")
    }

    /// Not while the call records or a notes run is going (one request at a time, no clash with the local model).
    var canRegenerate: Bool {
        !running && !NotesRewrite.of(file).running && !(CallEngine.shared.isRunning && CallEngine.shared.lastFile == file)
    }

    /// The call is long enough to ask first.
    func needsConfirm(minutes: Double?) -> Bool { (minutes ?? 0) > Self.longCallMinutes }

    func regenerate() {
        guard canRegenerate else { return }
        running = true; failed = nil
        let job = NotesRewrite.of(file)
        job.start()          // full notes, Summary and Recap in the output language; the Notion sections follow (CallEngine)
        Task { @MainActor in
            while job.running { try? await Task.sleep(nanoseconds: 500_000_000) }
            if let err = job.failed { self.failed = err; self.running = false; self.refresh(); return }
            if let page = CallPageSync.of(self.file).notionURL { await self.rewriteNotionPage(page: page) }
            let fu = FollowUpModel.of(self.file)
            if fu.hasDraft, !CallLanguage.readSidecar(transcript: self.file).followUpUsesClientLanguage {
                fu.write(replacingDraft: true)
                while fu.busy { try? await Task.sleep(nanoseconds: 500_000_000) }     // the follow-up records its language when it is done
            }
            self.running = false
            self.refresh()
        }
    }

    /// The page's Summary, Recap and Meeting notes: the same wrapup_notion run as after a call, on the call's own page (--page: never a lookup by date, never a new page). wrapup_notion
    /// records the Notion language in the sidecar itself, after Notion took the write; a failure leaves "out of date" and shows here.
    private func rewriteNotionPage(page: String) async {
        let c = AppConfig.load(from: AppPaths.config)
        let appRoot = c.effectiveAppRoot(appSupport: AppPaths.support.path)
        let (proj, ws) = PyTool.project(of: file)
        var args = ["--md", file, "--page", page, "--lt", c.engine.python, (appRoot as NSString).appendingPathComponent("bin/lt"), "--model", c.models.analysis]
        args += proj != nil ? NotionTargets.projectArgs(c, proj) : NotionTargets.workspaceArgs(c, ws, kind: "meetings")
        if !c.notionMeetingsDB.isEmpty { args += ["--meetings-db", c.notionMeetingsDB] }
        let title = CallPageView.title(file)
        if !title.isEmpty { args += ["--title", title] }
        let r: [String: Any] = await withCheckedContinuation { k in
            PyTool.run("wrapup_notion", args, notion: true) { k.resume(returning: $0) }
        }
        if let usd = r["cost_usd"] as? Double, usd > 0 { SpendDays.add(usd); CallHistory.shared.addCost(file: file, usd) }
        if (r["ok"] as? Bool) != true { failed = L("call.outlang.notionFailed", ["why": (r["error"] as? String) ?? ""]) }
    }
}

/// The line under the status lines of a call page: Output language menu, Regenerate, what each text was written in.
struct OutputLanguageBar: View {
    let file: String
    let minutes: Double?
    @ObservedObject private var model: OutputLanguageModel
    @ObservedObject private var job: NotesRewrite
    @State private var confirm = false
    @Environment(\.pnTheme) private var t

    init(file: String, minutes: Double?) {
        self.file = file; self.minutes = minutes
        model = OutputLanguageModel.of(file); job = NotesRewrite.of(file)
    }

    var body: some View {
        if model.isMulti {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 12) {
                    Text(L("call.outlang.label")).font(t.homeText(13)).foregroundStyle(PN.C.text2)
                    PrepMenuField(value: CallLanguage.name(model.current)) {
                        ForEach(model.languages, id: \.self) { c in
                            Button(CallLanguage.name(c)) { model.choose(c) }
                        }
                    }
                    .disabled(model.running)
                    .accessibilityLabel(L("call.outlang.label"))
                    if model.running || job.running {
                        PNSpinner(size: 18)
                        Text(L("call.outlang.running")).font(t.homeText(13)).foregroundStyle(PN.C.text2)
                    } else {
                        HomeLink(L("call.outlang.regenerate"), size: 13) {
                            if model.needsConfirm(minutes: minutes) { confirm = true } else { model.regenerate() }
                        }
                        .disabled(!model.canRegenerate)
                        .help(L("call.outlang.help"))
                    }
                }
                if !model.stateLine.isEmpty {
                    Text(model.stateLine).font(t.homeText(13)).foregroundStyle(model.stale.isEmpty ? PN.C.text3 : PN.C.notreadyText)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let f = model.failed {
                    Text(f).font(t.homeText(13)).foregroundStyle(PN.C.notreadyText).fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.top, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .alert(L("call.outlang.confirm.title", ["lang": CallLanguage.name(model.current)]), isPresented: $confirm) {
                Button(L("call.outlang.regenerate")) { model.regenerate() }
                Button(L("common.cancel"), role: .cancel) {}
            } message: { Text(L("call.outlang.confirm.body")) }
            .onReceive(FollowUpModel.of(file).$busy) { busy in if !busy { model.refresh() } }     // also a follow-up written from its tab
            .onReceive(NotificationCenter.default.publisher(for: NotesRewrite.finished)) { n in
                if (n.object as? String) == file { model.refresh() }
            }
        }
    }
}
