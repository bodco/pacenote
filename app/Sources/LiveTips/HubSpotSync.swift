import SwiftUI
import LiveTipsCore

// H6 (PB-62, docs/specs/hubspot-crm-integration.md section 12): Settings > Integrations > HubSpot > Sync (live check,
// Auto per object, field mapping, owner mapping) and the call's sync log in Update HubSpot with Retry and Undo.

/// Writes the call's sync log and tells open log views to reload.
@MainActor
enum CRMSyncRecorder {
    static var sync: CRMSyncSettings { SettingsWindow.model.config.crm.sync }

    static func applied(plan: CRMUpdatePlan, link: CRMLink, done: [(String, String, [String: String])], error: String?, stem: String, source: String,
                        revisions: [String: String]) {
        let linked = !link.dealId.isEmpty && link.dealId != "none"
        CRMSyncLog.append(CRMSyncLog.applyEntries(plan: plan, dealLinked: linked, done: done, error: error, source: source, sync: sync, revisions: revisions), stem: stem)
        CRMSyncLogStore.shared.version += 1
    }

    static func call(_ r: CRMCallResult, log: CRMCallLog, stem: String, source: String, revision: String) {
        CRMSyncLog.append([CRMSyncLog.callEntry(r, log: log, source: source, sync: sync, revision: revision)], stem: stem)
        CRMSyncLogStore.shared.version += 1
    }

    static func failed(_ object: String, stem: String, source: String, error: String) {
        CRMSyncLog.append([CRMSyncEntry(object: object, action: "create", source: source, ok: false, error: error)], stem: stem)
        CRMSyncLogStore.shared.version += 1
    }

    static func undo(_ object: String, _ id: String, stem: String, error: String?, source: String = "click") {
        CRMSyncLog.recordUndo(object: object, hubspotId: id, error: error, source: source, stem: stem)
        CRMSyncLogStore.shared.version += 1
    }
}

final class CRMSyncLogStore: ObservableObject {
    static let shared = CRMSyncLogStore()
    @Published var version = 0
}

/// The call's sync log: newest first, Retry for a failed write, Undo within a day while nobody changed the object.
struct CRMSyncLogView: View {
    let file: String
    let stem: String
    @ObservedObject var store = CRMSyncLogStore.shared
    @State private var busy = false
    @State private var line = ""

    var body: some View {
        let log = CRMSyncLog.load(path: CRMSyncLog.path(stem: stem))
        let _ = store.version
        if !log.isEmpty {
            DisclosureGroup(L("crm.log.title", ["n": "\(log.count)"])) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(log.reversed()) { e in row(e, log) }
                    }
                }
                .frame(maxHeight: 160)
                if !line.isEmpty { Text(line).font(PN.Font.caption).foregroundStyle(PN.C.text3).lineLimit(2) }
            }
            .font(PN.Font.caption)
        }
    }

    /// "22 Nov 14:05  Call, created (Auto)"
    static func title(_ e: CRMSyncEntry) -> String {
        let when: String = CallPageView.fmt("dMMMHHmm").string(from: e.at)
        let what: String = L("crm.log.object." + e.object) + ", " + L("crm.log.action." + e.action)
        let source: String = " (" + L("crm.log.source." + e.source) + ")"
        return when + "  " + what + source + (e.undone ? ", " + L("crm.log.undone") : "")
    }

    func row(_ e: CRMSyncEntry, _ log: [CRMSyncEntry]) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: e.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(e.ok ? PN.C.turn : PN.C.danger)
            VStack(alignment: .leading, spacing: 1) {
                Text(Self.title(e))
                if !e.ok { Text(e.error).foregroundStyle(PN.C.danger).fixedSize(horizontal: false, vertical: true) }
                else if !e.fields.isEmpty { Text(e.fields.joined(separator: ", ")).foregroundStyle(PN.C.text3).lineLimit(2) }
            }
            Spacer()
            if CRMSyncLog.canRetry(e, in: log) { Button(L("crm.log.retry")) { retry(e) }.buttonStyle(.pnTertiary).disabled(busy) }
            // Undo here can delete what Pacenote created and restore a Call or deal fields; other updates come with their branches
            if CRMSyncLog.canUndo(e, in: log, now: Date()), e.action == "create" || ["calls", "deals"].contains(e.object) { Button(L("crm.undo")) { undo(e) }.buttonStyle(.pnTertiary).disabled(busy) }
        }
    }

    /// The Call is written again at once (it never makes a second Call); a note, deal fields or a task reopen Update
    /// HubSpot with only that row ticked, so the PM still reviews and presses Apply.
    func retry(_ e: CRMSyncEntry) {
        if e.object == "calls" {
            busy = true; line = L("crm.writing")
            Task { let err = await CRMCallLogger.run(file, source: "retry"); line = err.isEmpty ? L("crm.updated", ["crm": "HubSpot"]) : err; busy = false }
        } else {
            let row = ["notes": "note", "deals": "deal", "tasks": "task"][e.object] ?? e.object
            CRMUpdateWindow.show(file: file, only: [row])
        }
    }

    func undo(_ shown: CRMSyncEntry) {
        // the button may be older than the log: the window and the latest write are checked again on the click
        guard let e = CRMSyncLog.undoable(id: shown.id, stem: stem) else { line = L("crm.log.expired"); store.version += 1; return }
        guard let hs = CRMService.shared.connector() as? HubSpotConnector else { line = L("crm.notConnected"); return }
        busy = true; line = ""
        Task {
            do {
                // never overwrite a change made in HubSpot after Pacenote's write (rule 6): the revision must be the one
                // HubSpot gave our write; a later change, or one that cannot be read, stops the Undo
                guard CRMSyncLog.unchanged(revision: e.revision, current: try await hs.updatedAt(type: e.object, id: e.hubspotId)) else {
                    line = L("crm.log.changed"); busy = false; return
                }
                if e.action == "update" {
                    // exactly the properties written, never through today's field mapping
                    if e.object == "deals" { try await hs.restoreDeal(id: e.hubspotId, fields: e.old) } else { try await hs.restoreCall(id: e.hubspotId, fields: e.old) }
                } else {
                    try await hs.delete(type: e.object, id: e.hubspotId)
                    if e.object == "calls", var l = CRMLink.load(base: stem), l.callId == e.hubspotId { l.callId = ""; l.save(base: stem) }
                }
                CRMSyncRecorder.undo(e.object, e.hubspotId, stem: stem, error: nil, source: "log")
                line = L("crm.undone")
            } catch {
                let msg = (error as? CRMError)?.message ?? error.localizedDescription
                CRMSyncRecorder.undo(e.object, e.hubspotId, stem: stem, error: msg, source: "log")
                line = msg
            }
            busy = false
        }
    }
}

/// Settings > Integrations > HubSpot > Sync.
struct HubSpotSyncSection: View {
    @ObservedObject var model: SettingsModel
    let owners: [CRMOwner]
    @ObservedObject var crm = CRMService.shared
    struct Prop: Hashable { let name: String; let label: String }
    @State private var props: [String: [Prop]] = [:]       // object type -> writable properties
    @State private var newUser = ""

    var cfg: CRMSettings { model.config.crm }

    var body: some View {
        Group {
            live
            autoSwitches
            fieldMapping
            ownerMapping
        }
        .onAppear {
            // the live check: again when the last one is older than five minutes
            let last = ISO8601DateFormatter().date(from: cfg.lastTest) ?? .distantPast
            if Date().timeIntervalSince(last) > 300, crm.state != .testing { crm.test() }
            loadProps()
        }
    }

    private var live: some View {
        Section {
            HStack {
                switch crm.state {
                case .ok(let s): Label(L("int.connectedTo", ["name": s]), systemImage: "checkmark.circle.fill").foregroundStyle(PN.C.turn)
                case .failed(let e): Label(e, systemImage: "exclamationmark.triangle.fill").foregroundStyle(PN.C.danger)
                case .testing: PNSpinner(size: 16); Text(L("common.checking")).foregroundStyle(PN.C.text3)
                case .idle: Text(cfg.account.isEmpty ? L("int.hubspot.untested") : L("int.connectedTo", ["name": cfg.account])).foregroundStyle(PN.C.text3)
                }
                Spacer()
                Button(L("int.hubspot.sync.checkNow")) { crm.test() }.buttonStyle(.pnSecondary).disabled(crm.state == .testing)
            }
            if !cfg.missingScopes.isEmpty {
                Text(L("int.hubspot.sync.missing", ["list": cfg.missingScopes.joined(separator: ", ")])).foregroundStyle(PN.C.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if case .ok = crm.state, !crm.unverifiedScopes.isEmpty {
                Text(L("int.hubspot.sync.unverified", ["list": crm.unverifiedScopes.joined(separator: ", ")])).foregroundStyle(PN.C.notreadyText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } header: { Text(L("int.hubspot.sync.live")).settingsAnchor("int.hubspot.sync") } footer: {
            Text(L("int.hubspot.sync.liveFooter")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
        }
        .font(PN.Font.caption)
    }

    private var autoSwitches: some View {
        Section {
            ForEach(CRMObject.allCases, id: \.self) { o in
                VStack(alignment: .leading, spacing: 2) {
                    Toggle(L("int.hubspot.sync.auto." + o.rawValue), isOn: Binding(get: { model.config.crm.autoWrites(o) },
                                                                                  set: { model.config.crm.setAutoWrites(o, $0) }))
                    Text(o.hasWriter ? L("int.hubspot.sync.autoHelp." + o.rawValue) : L("int.hubspot.sync.notYet"))
                        .font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
                    let blocking = CRMScopeCheck.blocking(o, missing: cfg.missingScopes)
                    if !blocking.isEmpty {
                        Text(L("int.hubspot.sync.needs", ["list": blocking.joined(separator: ", ")])).font(PN.Font.caption).foregroundStyle(PN.C.danger)
                    }
                }
            }
        } header: { Text(L("int.hubspot.sync.autoTitle")) } footer: {
            Text(L("int.hubspot.sync.autoFooter")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var fieldMapping: some View {
        Section {
            ForEach(CRMObject.allCases, id: \.self) { o in
                DisclosureGroup(L("int.hubspot.sync.object." + o.rawValue)) {
                    ForEach(o.defaultFields, id: \.property) { f in
                        Picker(L("int.hubspot.sync.field." + f.field), selection: Binding(get: { model.config.crm.sync.property(o, f.property) },
                                                                                         set: { model.config.crm.sync.setProperty(o, f.property, to: $0) })) {
                            Text(L("int.hubspot.sync.default", ["name": f.property])).tag(f.property)
                            Text(L("int.hubspot.dontWrite")).tag("")
                            let cur = cfg.sync.property(o, f.property)
                            // never a dedupe key, the owner, a status or what another field writes (CRMSyncSettings.canTarget)
                            let list = (props[o.objectType(field: f.field)] ?? []).filter { $0.name != f.property && cfg.sync.canTarget(o, f.property, $0.name) }
                            if !cur.isEmpty, cur != f.property, !list.contains(where: { $0.name == cur }) { Text(cur).tag(cur) }
                            ForEach(list, id: \.self) { p in Text(p.label).tag(p.name) }
                        }
                    }
                    Text(L("int.hubspot.sync.fixed", ["list": o.fixed.joined(separator: ", ")]))
                        .font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
                }
            }
            if !cfg.sync.fields.isEmpty {
                HStack { Spacer(); Button(L("int.hubspot.sync.reset")) { model.config.crm.sync.fields = [:] }.buttonStyle(.pnTertiary) }
            }
        } header: { Text(L("int.hubspot.sync.fieldsTitle")) } footer: {
            Text(L("int.hubspot.sync.fieldsFooter")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var ownerMapping: some View {
        Section {
            // the user of this Mac: the same choice as Matching > Owner (one place to keep it)
            Picker(L("int.hubspot.sync.you", ["email": CRMService.ownEmails.first ?? ""]), selection: Binding(get: { model.config.crm.ownerId }, set: { id in
                model.config.crm.ownerId = id; model.config.crm.ownerName = owners.first { $0.id == id }?.name ?? ""; model.config.crm.ownerAuto = false
            })) { ownerOptions(current: cfg.ownerId, name: cfg.ownerName, none: L("int.hubspot.ownerNone")) }
            ForEach(cfg.sync.owners.keys.sorted(), id: \.self) { email in
                HStack {
                    Picker(email, selection: Binding(get: { model.config.crm.sync.owners[email] ?? "" }, set: { model.config.crm.sync.owners[email] = $0 })) {
                        ownerOptions(current: cfg.sync.owners[email] ?? "", name: "", none: L("int.hubspot.sync.byEmail"))
                    }
                    Button { model.config.crm.sync.owners[email] = nil } label: { Image(systemName: "minus.circle") }
                        .buttonStyle(.borderless).accessibilityLabel(L("int.hubspot.sync.removeUser"))
                }
            }
            HStack {
                TextField(L("int.hubspot.sync.addUser"), text: $newUser, prompt: Text("emily.clark@example.com"))
                Button(L("int.hubspot.sync.add")) {
                    let k = CRMSyncSettings.key(newUser)
                    if k.contains("@") { model.config.crm.sync.owners[k] = CRMOwner.pick(owners, emails: [k])?.id ?? ""; newUser = "" }
                }.buttonStyle(.pnSecondary).disabled(!newUser.contains("@"))
            }
        } header: { Text(L("int.hubspot.sync.ownersTitle")) } footer: {
            Text(L("int.hubspot.sync.ownersFooter")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private func ownerOptions(current: String, name: String, none: String) -> some View {
        Text(none).tag("")
        if !current.isEmpty, !owners.contains(where: { $0.id == current }) {
            Text(name.isEmpty ? L("int.hubspot.ownerUnknown") : name).tag(current)
        }
        ForEach(owners) { o in Text(o.name).tag(o.id) }
    }

    private func loadProps() {
        guard let c = crm.connector() else { return }
        Task {
            var out: [String: [Prop]] = [:]
            for t in ["calls", "tasks", "contacts", "companies", "deals", "meetings"] {
                out[t] = ((try? await c.properties(objectType: t)) ?? []).map { Prop(name: $0.name, label: $0.label) }
            }
            props = out
        }
    }
}
