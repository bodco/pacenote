import AppKit
import LiveTipsCore
import SwiftUI

/// 0.16.0 (D3): the required parts in three groups people understand, shared by the onboarding's step 5 and
/// Settings > Components. Each group lists the setup.sh steps it is made of; its state comes from SetupModel.
struct RequiredGroup: Identifiable {
    let id: String
    let steps: [String]          // setup.sh steps (state)
    let measured: [String]       // ComponentCatalog.required ids whose sizes add up to the group
    var title: String { L("cl.req.\(id)") }
    var why: String { L("cl.req.\(id).why") }

    static let all = [
        RequiredGroup(id: "speech", steps: ["recorder", "whisper"], measured: ["whisper"]),
        RequiredGroup(id: "search", steps: ["localai"], measured: ["localai"]),
        RequiredGroup(id: "engine", steps: ["python", "core", "venv", "tips", "voices"], measured: ["core", "venv", "voices"]),
    ]

    enum Status { case installed, installing, waiting, failed, checking }

    @MainActor func status(_ setup: SetupModel, checking: Bool = false) -> Status {
        let s = setup.steps.filter { steps.contains($0.id) }
        if checking && s.allSatisfy({ $0.state == .unknown }) { return .checking }
        if !s.isEmpty && s.allSatisfy({ $0.state == .ok }) { return .installed }
        if s.contains(where: { $0.state == .failed }) { return .failed }
        if s.contains(where: { $0.state == .installing || $0.state == .checking }) { return .installing }
        return .waiting
    }

    /// The group's size: measured parts, the venv without the optional Python add-ons living in it.
    @MainActor func bytes(_ oc: OptionalComponents) -> Int64? {
        var total: Int64 = 0, any = false
        for id in measured {
            guard var v = oc.sizes[id] else { continue }
            if id == "venv" { v -= (oc.sizes["agent"] ?? 0) + (oc.sizes["link_downloader"] ?? 0) }
            total += max(0, v); any = true
        }
        return any ? total : nil
    }
}

/// A small coloured label: Installed, Installing, Waiting, Failed.
struct ComponentStatePill: View {
    let status: RequiredGroup.Status
    var body: some View {
        let (t, c): (String, Color) = {
            switch status {
            case .installed: return (L("cl.state.installed"), PN.C.turn)
            case .installing: return (L("cl.state.installing"), PN.C.accent)
            case .waiting: return (L("cl.state.waiting"), PN.C.onMe)
            case .failed: return (L("cl.state.failed"), PN.C.danger)
            case .checking: return (L("cl.state.checking"), PN.C.text3)
            }
        }()
        Text(t).font(PN.Font.caption.weight(.semibold)).foregroundStyle(c)
            .padding(.horizontal, 8).padding(.vertical, 2)
            .background(c.opacity(0.13), in: RoundedRectangle(cornerRadius: PN.r(6)))
    }
}

/// Onboarding step 5 and the compact add-ons card in What's New.
struct ComponentsChecklist: View {
    var provider: String = "claude"
    var compact = false
    var done: () -> Void
    @ObservedObject var oc = OptionalComponents.shared
    @ObservedObject var llm = LocalLLM.shared
    @ObservedObject var setup = SetupWindow.model
    @State private var picked: Set<String> = []
    @State private var submitted = false
    @State private var installingSelection = false

    static let chosenKey = "addonsChosen"

    var offered: [OptionalComponents.Comp] { oc.all }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !compact {
                requiredCard
                SetupLoader(model: setup)
            }
            optionalCard
            HStack(spacing: 12) {
                Text(summaryLine).font(PN.Font.caption).foregroundStyle(PN.C.text2)
                Spacer()
                Button { install() } label: { Text(cta).frame(minWidth: 160) }
                    .buttonStyle(.pnPrimary).keyboardShortcut(compact ? nil : .defaultAction)
                    .disabled(installingSelection || hasDownloads || (oc.duringCall && !chosen.isEmpty) || (!compact && setup.busy))
            }
            if !compact {
                Text(L("cl.later")).font(PN.Font.caption).foregroundStyle(PN.C.text3)
            }
        }
        .onAppear { oc.refresh(); Task { await oc.measureAll() } }
    }

    var requiredCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(L("cl.req.head")).pnLabel().foregroundStyle(PN.C.text3).frame(minHeight: 30)
            ForEach(RequiredGroup.all) { g in
                Divider()
                HStack(spacing: 12) {
                    Toggle("", isOn: .constant(true)).toggleStyle(.pnCheckbox).labelsHidden().disabled(true)
                        .accessibilityLabel(L("cl.req.a11y", ["name": g.title]))
                    VStack(alignment: .leading, spacing: 1) {
                        Text(g.title)
                        Text(g.why).font(PN.Font.caption).foregroundStyle(PN.C.text3).lineLimit(2)
                    }
                    Spacer()
                    Text(ComponentCatalog.format(g.bytes(oc) ?? g.measured.reduce(0) { $0 + ComponentCatalog.estimate($1) }))
                        .font(PN.Font.caption).foregroundStyle(PN.C.text3).frame(width: 64, alignment: .trailing)
                    ComponentStatePill(status: g.status(setup)).frame(width: 100, alignment: .trailing)
                }
                .padding(.vertical, 4)
            }
            Divider()
            HStack(spacing: 8) {
                Image(systemName: "lock").font(PN.sys(11)).foregroundStyle(PN.C.text3).accessibilityHidden(true)
                Text(L("cl.req.locked")).font(PN.Font.caption).foregroundStyle(PN.C.text2)
                Spacer()
                let py = setup.steps.first { $0.id == "python" }?.state
                Text(py == .ok ? L("cl.python.ok") : L("cl.python.need")).font(PN.Font.caption)
                    .foregroundStyle(py == .ok ? PN.C.turn : PN.C.text3)
            }
            .padding(.vertical, 8)
        }
        .padding(.horizontal, 16)
        .background(PN.C.surface, in: RoundedRectangle(cornerRadius: PN.Radius.m))
    }

    var optionalCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !compact { Text(L("cl.opt.head")).pnLabel().foregroundStyle(PN.C.text3).frame(minHeight: 30) }
            ForEach(Array(offered.enumerated()), id: \.element.id) { i, c in
                if !compact || i > 0 { Divider() }
                let installed = oc.isInstalled(c.id)
                let allowed = ComponentCatalog.offered(c.id, provider: provider)
                HStack(alignment: .center, spacing: 12) {
                    Toggle(isOn: Binding(get: { installed || picked.contains(c.id) },
                                         set: { if $0 { picked.insert(c.id) } else { picked.remove(c.id) } })) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(c.title)
                            if !compact {
                                Text(c.purpose).font(PN.Font.caption).foregroundStyle(PN.C.text2).fixedSize(horizontal: false, vertical: true)
                                Text(installed ? L("comp.installed") : note(c.id, allowed: allowed)).font(PN.Font.caption).foregroundStyle(PN.C.text3)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                    .toggleStyle(.pnCheckbox).disabled(installed || !allowed || submitted || (!compact && !setup.allRequiredOK))
                    Spacer()
                    Text(ComponentCatalog.format(oc.sizes[c.id] ?? ComponentCatalog.estimate(c.id)))
                        .font(PN.Font.caption).foregroundStyle(PN.C.text3).frame(width: 64, alignment: .trailing)
                    if case .installing = oc.state(c.id) { ComponentAction(id: c.id) }
                }
                .padding(.vertical, compact ? 4 : 6)
                if submitted && picked.contains(c.id) { ComponentStateLine(id: c.id) }
            }
            if picked.contains("local_model") {
                Text(L("download.localOllama")).font(PN.Font.caption).foregroundStyle(PN.C.text3)
                    .fixedSize(horizontal: false, vertical: true).padding(.bottom, 8)
            }
        }
        .padding(.horizontal, compact ? 0 : 16)
        .background(compact ? Color.clear : PN.C.surface, in: RoundedRectangle(cornerRadius: PN.Radius.m))
    }

    func note(_ id: String, allowed: Bool) -> String {
        switch id {
        case "local_model": return llm.installed ? L("cl.note.model.ollama") : L("cl.note.model")
        case "agent": return allowed ? L("cl.note.agent") : L("cl.note.agentClaudeOnly")
        default: return "yt-dlp"
        }
    }

    var chosen: [String] { offered.map(\.id).filter { picked.contains($0) && !oc.isInstalled($0) } }

    var summaryLine: String {
        let s = ComponentCatalog.summary(picked: submitted ? [] : chosen, sizes: oc.sizes)
        let free = ComponentCatalog.format(DiskSpace.free)
        return s.count == 0 ? L("cl.sum.none", ["free": free])
            : L("cl.sum", ["n": "\(s.count)", "size": ComponentCatalog.format(s.bytes), "free": free])
    }

    var hasDownloads: Bool {
        offered.contains { if case .installing = oc.state($0.id) { return true }; return false }
    }

    var cta: String {
        if !submitted && !chosen.isEmpty { return L("download.installSelected") }
        return submitted ? L("download.continue") : L(compact ? "cl.cta.none.compact" : "cl.cta.none")
    }

    func install() {
        if !submitted && !chosen.isEmpty {
            let ids = chosen
            submitted = true
            UserDefaults.standard.set(true, forKey: Self.chosenKey)
            UsageLog.log("addons_chosen", ["n": ids.count, "where": compact ? "whatsnew" : "onboarding"])
            installingSelection = true
            Task {
                for id in ids { await oc.installAndWait(id) }
                installingSelection = false
            }
            return
        }
        if !submitted {
            UserDefaults.standard.set(true, forKey: Self.chosenKey)
            UsageLog.log("addons_chosen", ["n": 0, "where": compact ? "whatsnew" : "onboarding"])
        }
        done()
    }
}
