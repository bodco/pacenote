import SwiftUI
import LiveTipsCore

// H6 (PB-62, docs/specs/hubspot-crm-integration.md section 13): Settings > Integrations > HubSpot > Sync (live check,
// Auto per object, field mapping, owner mapping) and the call's sync log in Update HubSpot with Retry and Undo.

/// Writes the call's sync log and tells open log views to reload. Each returns false when the log file could not be
/// saved, so the caller can say so next to the HubSpot result (the write in HubSpot is not undone by that).
@MainActor
enum CRMSyncRecorder {
    static var sync: CRMSyncSettings { SettingsWindow.model.config.crm.sync }

    @discardableResult
    static func applied(plan: CRMUpdatePlan, portal: String, done: [CRMUndoItem], error: String?, failed: String?, stem: String, source: String,
                        revisions: [String: String]) -> Bool {
        defer { CRMSyncLogStore.shared.version += 1 }
        return CRMSyncLog.append(CRMSyncLog.applyEntries(plan: plan, done: done, error: error, failed: failed, portal: portal, source: source,
                                                         sync: sync, revisions: revisions), stem: stem)
    }

    /// An Auto or Retry Call write; H4: its deal move (`deal`, the "callDeal" Undo row) gets a line of its own.
    @discardableResult
    static func call(_ r: CRMCallResult, deal: CRMUndoItem? = nil, log: CRMCallLog, stem: String, source: String, revision: String, portal: String) -> Bool {
        defer { CRMSyncLogStore.shared.version += 1 }
        var lines = [CRMSyncLog.callEntry(r, log: log, source: source, sync: sync, revision: revision, portal: portal)]
        if let d = deal { lines.append(CRMSyncEntry(object: d.type, action: "update", source: source, hubspotId: d.id, old: d.old, portal: portal)) }
        return CRMSyncLog.append(lines, stem: stem)
    }

    /// H3 Auto: one line per contact, company or Lead created, with HubSpot's revision so the log can undo it, and a
    /// failed line when HubSpot refused a create (its Retry reopens Update HubSpot, where the PM ticks the people).
    @discardableResult
    static func people(_ made: [CRMUndoItem], failed: String?, stem: String, source: String, portal: String, connector c: CRMConnector) -> Bool {
        guard !made.isEmpty || failed != nil else { return true }
        defer { CRMSyncLogStore.shared.version += 1 }
        var lines = made.map { CRMSyncEntry(object: $0.type, action: "create", source: source, hubspotId: $0.id,
                                            revision: (c as? HubSpotConnector)?.revision(type: $0.type, id: $0.id) ?? "", portal: portal) }
        if let e = failed { lines.append(CRMSyncEntry(object: "contacts", action: "create", source: source, ok: false, error: e, portal: portal)) }
        return CRMSyncLog.append(lines, stem: stem)
    }

    @discardableResult
    static func failed(_ object: String, stem: String, source: String, error: String, portal: String) -> Bool {
        defer { CRMSyncLogStore.shared.version += 1 }
        return CRMSyncLog.append([CRMSyncEntry(object: object, action: "create", source: source, ok: false, error: error, portal: portal)], stem: stem)
    }

    @discardableResult
    static func undo(_ object: String, _ id: String, stem: String, error: String?, source: String = "click") -> Bool {
        defer { CRMSyncLogStore.shared.version += 1 }
        return CRMSyncLog.recordUndo(object: object, hubspotId: id, error: error, source: source, stem: stem)
    }

    /// The line shown after a write or an Undo: the HubSpot result, plus a note when the log was not saved.
    static func status(_ result: String, saved: Bool) -> String { saved ? result : result + " " + L("crm.log.saveFailed") }

    /// What an Undo error means to the PM: expired, changed in HubSpot, another portal, or HubSpot's own message.
    static func undoError(_ error: Error) -> String {
        switch (error as? CRMError)?.code {
        case CRMSyncLog.expiredCode: return L("crm.log.expired")
        case CRMSyncLog.changedCode: return L("crm.log.changed")
        case CRMPortal.mismatchCode: return L("crm.undo.portal", ["crm": "HubSpot"])
        default: return (error as? CRMError)?.message ?? error.localizedDescription
        }
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
        } else if CRMSyncLog.unreadable(stem: stem) {
            // fix round 9: a damaged file is kept, not shown as an empty log that the next write would replace
            Text(L("crm.log.unreadable")).font(PN.Font.caption).foregroundStyle(PN.C.danger).fixedSize(horizontal: false, vertical: true)
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

    /// The Call is written again at once (it never makes a second Call); a note, deal fields, a task, the Meeting or the
    /// Email reopen Update HubSpot with only that row ticked, so the PM still reviews and presses Apply. People (H3) are
    /// not rows of the plan: their Retry reopens the window with the people to tick there. All write only to the portal
    /// of the failed line (CRMSyncLog.retryPortal), checked when the write goes out.
    func retry(_ e: CRMSyncEntry) {
        if e.object == "calls" {
            busy = true; line = L("crm.writing")
            Task {
                let r = await CRMCallLogger.run(file, source: "retry", portal: e.portal)
                line = CRMSyncRecorder.status(r.line.isEmpty ? L("crm.updated", ["crm": "HubSpot"]) : r.line, saved: r.saved); busy = false
            }
        } else {
            let row = ["notes": "note", "deals": "deal", "tasks": "task", "meetings": "meeting", "emails": "email"][e.object]
            CRMUpdateWindow.show(file: file, only: row.map { [$0] } ?? [], portal: e.portal)
        }
    }

    func undo(_ shown: CRMSyncEntry) {
        // the button may be older than the log: the window and the latest write are checked again on the click
        guard let e = CRMSyncLog.undoable(id: shown.id, stem: stem) else { line = L("crm.log.expired"); store.version += 1; return }
        guard let hs = CRMService.shared.connector() as? HubSpotConnector else { line = L("crm.notConnected"); return }
        busy = true; line = ""
        let deadline = e.at.addingTimeInterval(CRMSyncLog.logUndoSeconds)
        // an update gets exactly the properties written back (never through today's field mapping); a create is deleted
        // (a Meeting Pacenote created keeps H5's Undo record, so it is deleted only while it holds just Pacenote's block)
        let item = CRMUndoItem(e.object, e.hubspotId, e.old)
        let key = CRMCallLogger.keyed(base: stem, CRMLink.load(base: stem) ?? CRMLink()).callKey
        let o = CRMUndoOutcomes()
        Task {
            // the same Undo as Apply's: in the call's turn, the portal written to first, then before the change the
            // window and the revision HubSpot gave our write (rule 6: a later change, or one that cannot be read, stops it)
            let r = await CRMCallLogger.undo([item], portal: e.portal, base: stem, key: key, connector: hs,
                                             check: CRMSyncLog.undoCheck(revision: { _ in e.revision }, deadline: deadline, connector: hs),
                                             result: { it, err in o.record(it, err, stem: stem, source: "log") })
            if let err = r.error { line = CRMSyncRecorder.undoError(err) }       // another portal: nothing was sent, no line
            else if let err = o.refused { line = CRMSyncRecorder.undoError(err) }
            else if r.kept > 0 { line = L("crm.undoKept", ["n": "\(r.kept)", "crm": "HubSpot"]) }
            else if r.failed > 0 { line = CRMSyncRecorder.status(L("crm.undoFailed", ["n": "\(r.failed)", "crm": "HubSpot"]), saved: o.saved) }
            else { line = CRMSyncRecorder.status(L("crm.undone"), saved: o.saved) }
            store.version += 1
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
                    // one source per object: the Call, contacts and companies, and tasks are the switches of their own groups
                    Toggle(L("int.hubspot.sync.auto." + o.rawValue), isOn: Binding(get: { model.config.crm.autoWrites(o) },
                                                                                  set: { model.config.crm.setAutoWrites(o, $0) }))
                        .disabled(o.neverAuto)      // H5: a Meeting is written only by the PM's click
                    Text(o.neverAuto ? L("int.hubspot.sync.neverAuto") : o.hasWriter ? L("int.hubspot.sync.autoHelp." + o.rawValue) : L("int.hubspot.sync.notYet"))
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
            // Meetings have no mappable field (H5 writes only Pacenote's block of the notes)
            ForEach(CRMObject.allCases.filter { !$0.defaultFields.isEmpty }, id: \.self) { o in
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
            for t in ["calls", "tasks", "contacts", "companies", "deals"] {
                out[t] = ((try? await c.properties(objectType: t)) ?? []).map { Prop(name: $0.name, label: $0.label) }
            }
            props = out
        }
    }
}
