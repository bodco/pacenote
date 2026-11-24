import AppKit
import LiveTipsCore
import SwiftUI

/// PB-17 (INT7, rules/hubspot-tasks.yaml): the PM's HubSpot tasks in On you, the Tracker and the call window's On me,
/// and the link between the commitments register and HubSpot (Add to HubSpot tasks, ticks and closes both ways,
/// Remove, the automatic add after a call). The workflow is CRMTaskFlow (LiveTipsCore, checked on a mocked HubSpot);
/// this shell gives it the client of Update HubSpot, the settings, the register folders and the toasts.
/// Nothing is written to HubSpot without the PM's action or the switch the PM turned on.
@MainActor
final class HubSpotTasks: ObservableObject {
    static let shared = HubSpotTasks()

    @Published private(set) var version = 0
    private var timer: Timer?
    private var flow: CRMTaskFlow!

    var tasks: [CRMTask] { flow.tasks }
    var syncedAt: Date? { flow.syncedAt }
    var error: String { flow.error }
    var busy: Bool { flow.busy }
    var settings: CRMTasksSettings { SettingsWindow.model.config.crm.tasks }
    var ready: Bool { flow.ready }
    /// "notion", "hubspot" or nil: where a new task of a commitment goes (rules/hubspot-tasks.yaml > destination).
    var destination: String? {
        CRMTasks.destination(notionReady: NotionTasks.shared.settings.ready, hubspotReady: ready, choice: settings.destination)
    }

    private init() {
        let path = (CallEngine.shared.appRoot as NSString).appendingPathComponent("state/hubspot-tasks.json")
        flow = CRMTaskFlow(cachePath: path, connected: { CRMService.shared.connected }, connector: { HubSpotTasks.connector() },
                           settings: { SettingsWindow.model.config.crm.tasks }, matchingOwner: { SettingsWindow.model.config.crm.ownerId },
                           items: { let reg = Register.shared; return reg.folders.flatMap { reg.items(in: $0) } },
                           onChange: { [weak self] folder in
                               if let f = folder { Register.shared.touch(f) }
                               self?.version += 1
                           },
                           notify: { note in HubSpotTasks.toast(note) })
    }

    /// The client of Update HubSpot; new tasks get the owner whose tasks are shown (else the Matching owner).
    private static func connector() -> HubSpotConnector? {
        guard let hs = CRMService.shared.connector() as? HubSpotConnector else { return nil }
        let own = SettingsWindow.model.config.crm.tasks.ownerFilter(matchingOwner: SettingsWindow.model.config.crm.ownerId)
        if !own.isEmpty { hs.ownerId = own }
        return hs
    }

    private static func toast(_ note: CRMTaskFlow.Note) {
        let n = NotionTasks.shared
        switch note {
        case .added(let count, let undo): n.toast = .init(text: L("htask.added", ["n": "\(count)"]), undo: undo)
        case .completed(let title, let undo): n.toast = .init(text: L("htask.setDone", ["title": title]), undo: undo)
        case .refused(let why): n.toast = .init(text: L("htask.refused", ["reason": why]), isError: true)
        case .removed(let undo): n.toast = .init(text: L("htask.removed"), undo: undo)
        case .removeRefused(let why, let url): n.toast = .init(text: L("htask.removeRefused", ["reason": why]), openURL: url, isError: true)
        case .undoRefused(let count, let why): n.toast = .init(text: L("htask.undoRefused", ["n": "\(count)", "reason": why]), isError: true)
        case .accountChanged: n.toast = .init(text: L("htask.accountChanged"), isError: true)
        case .relinkCall: n.toast = .init(text: L("htask.relinkCall"), isError: true)
        case .replayRefused(let count, let why, let retry):
            n.toast = .init(text: L("htask.replayRefused", ["n": "\(count)", "reason": why]), undo: { Task { await retry() } },
                            isError: true, actionTitle: L("htask.retry"))
        }
    }

    // MARK: sync

    /// Home, the Tracker or the call window is open: refresh now and every 5 minutes while one of them is.
    func keepFresh() {
        refresh()
        startTimer()
    }

    private func startTimer() {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { _ in
            MainActor.assumeIsolated {
                if HomeWindow.window?.isVisible == true || CallEngine.shared.isRunning { HubSpotTasks.shared.refresh() }
            }
        }
    }

    func refresh() { Task { await flow.sync() } }

    // MARK: what a list shows

    var shown: [CRMTask] { flow.shown }

    func has(_ id: String) -> Bool { !id.isEmpty && shown.contains { $0.id == id } }

    func url(_ t: CRMTask) -> String { flow.url(t) }

    func ownerName(_ t: CRMTask) -> String {
        t.owner.isEmpty || t.owner == SettingsWindow.model.config.crm.ownerId ? "me" : (flow.ownerNames[t.owner] ?? t.owner)
    }

    func linkedCommitment(_ t: CRMTask) -> Commitment? { flow.linkedCommitment(t) }

    /// The CRM link (deal, company, contacts) of the call a commitment or a transcript belongs to, with the portal the
    /// link was made in (H2's portalId; "" for a link that does not say).
    static func refs(callFile: String) -> (refs: [CRMRef], portal: String) {
        CRMTasks.callLink(base: callFile.hasSuffix(".md") ? String(callFile.dropLast(3)) : callFile)
    }

    /// The Tracker's rows (the Tracker shows every task; the owner filter there works on "me").
    func trackerItems() -> [TrackerItem] {
        shown.map { t in
            let c = linkedCommitment(t)
            return TrackerItem(id: "hs:" + t.id, kind: .task, title: t.title, project: c.map { Register.shared.projectName($0) } ?? "",
                               workspace: "", owner: ownerName(t), due: t.due, status: L("htask.st." + t.status),
                               url: url(t), callFile: c.flatMap { Register.shared.transcript($0) } ?? "",
                               callTitle: c.map { TrackerModel.callTitle($0) } ?? "", crmTask: t.id)
        }
    }

    /// PB-88: this project's tasks for the Project memory snapshot, from the list already loaded (no request): a task is
    /// placed by its linked register item, keyed `crm:<portal>:<task>` as the engine keys it; the status is HubSpot's own value, so the snapshot does not
    /// change with the app language. A task with no linked item
    /// has no project and stays out.
    func snapshotItems(slug: String) -> [TrackerSnapshot.Item] {
        shown.compactMap { t in
            guard let c = linkedCommitment(t), c.project == slug, let id = TrackerSnapshot.crmID(portal: c.crmPortal, task: t.id) else { return nil }
            return TrackerSnapshot.Item(id: id, text: t.title, status: t.status, owner: ownerName(t), due: t.due, project: slug)
        }
    }

    // MARK: actions

    /// A tick on a HubSpot task (or on a linked commitment): COMPLETED at once, Undo for 8 s writes the status before.
    func tick(_ t: CRMTask, done: Bool = true) { flow.tick(t, done: done) }

    /// rules/hubspot-tasks.yaml > remove: out of the lists at once; deleted in HubSpot after the 8 s Undo.
    func remove(_ t: CRMTask, commitment: Commitment?) { flow.remove(t, commitment: commitment) }

    /// A tick ("done", "reopen") or Remove ("drop") on a register item linked to a HubSpot task, from any list: it goes
    /// to the saved task id and portal, in the list or not. False when the item is not linked (the register handles it).
    @discardableResult
    func mark(_ c: Commitment, _ op: String) -> Bool {
        guard flow.linkedTask(c) != nil else { return false }
        if op == "drop" { flow.removeLinked(c) } else { flow.tickLinked(c, done: op == "done") }
        return true
    }

    /// rules/hubspot-tasks.yaml > remove: a task Pacenote did not make (no linked item) asks once; one from a call does not.
    func confirmRemove(_ t: CRMTask, commitment: Commitment?) {
        guard commitment == nil else { remove(t, commitment: commitment); return }
        AskCenter.shared.ask(kind: "trash", origin: .window, title: L("htask.deleteQ", ["title": t.title]), info: L("htask.deleteInfo"),
                             choices: [Ask.Choice(id: "trash", title: L("htask.delete"), role: .danger), .cancel()]) { [weak self] _ in
            self?.remove(t, commitment: nil)
        }
    }

    /// The new task of a commitment: the settings' type, priority, queue and due date, and the call's title, date and
    /// link back (never transcript text); associated with the call's deal, company and contacts.
    private func draft(_ c: Commitment) -> (properties: [String: Any], refs: [CRMRef], portal: String) {
        let file = Register.shared.transcript(c) ?? ""
        let rec = CallHistory.shared.records.first { $0.file == file }
        let when = c.createdDate.map { DateFormatter.localizedString(from: $0, dateStyle: .medium, timeStyle: .none) } ?? ""
        let body = CRMTasks.body(callTitle: Register.shared.callTitle(c) ?? TrackerModel.callTitle(c), date: when, link: rec?.notionURL ?? "")
        let due = CommitmentRegister.dueDate(c.due, from: c.createdDate ?? Date())
        let s = settings
        let link = file.isEmpty ? (refs: [], portal: "") : Self.refs(callFile: file)
        return (CRMTasks.newTask(s, title: c.text, due: CRMTasks.dueDate(due, defaultDays: s.dueDays), body: body), link.refs, link.portal)
    }

    /// "Add to HubSpot tasks" on a commitment (owner me), from On you and the call page.
    func add(_ c: Commitment) async {
        let d = draft(c)
        await flow.add(c, properties: d.properties, refs: d.refs, linkPortal: d.portal)
    }

    /// rules/hubspot-tasks.yaml > after_call: with the switch on, every open item owned by me of this call becomes a
    /// HubSpot task, once. When Notion tasks are set up too, only when the PM chose HubSpot for new tasks.
    func afterCall(file: String) {
        let dest = destination
        Task { await flow.afterCall(file: file, folder: CallLayout.liveFolder(of: file), destination: dest, draft: draft) }
    }

    // MARK: the call window

    /// The On me tab's HubSpot group: the open tasks on the call's deal, company or contacts. Register items linked to
    /// a shown task leave the "Open before the call" group (shown once, as the HubSpot task). Built every few seconds,
    /// so it syncs only when the last sync is older than 5 minutes.
    func merge(into payload: inout [String: Any], callFile: String) {
        guard ready else { return }
        startTimer()
        if flow.stale() && !flow.busy { refresh() }
        let link = Self.refs(callFile: callFile)
        let mine = !link.portal.isEmpty && link.portal == flow.portal        // ids of this portal only
        let ts = CRMTasks.forCall(shown, refs: mine ? link.refs : []).prefix(30)
        let ids = Set(ts.map(\.id))
        let linkedIds = Set(Register.shared.items(in: CallLayout.liveFolder(of: callFile)).filter { ids.contains($0.crmTask) }.map(\.id))
        if var open = payload["open"] as? [[String: Any]] {
            open.removeAll { linkedIds.contains(($0["id"] as? String) ?? "") }
            payload["open"] = open
        }
        payload["hubspot"] = ts.map { t -> [String: Any] in
            ["id": "hs-" + t.id, "text": t.title, "due": t.due.isEmpty ? "" : Register.dueText(t.due), "done": false, "mine": true,
             "badge": "HS", "status": L("htask.st." + t.status), "fromCall": linkedCommitment(t) != nil]
        }
        let named = mine ? CRMLink.load(base: callFile.hasSuffix(".md") ? String(callFile.dropLast(3)) : callFile) : nil
        payload["hubspotProject"] = named.map { $0.dealName.isEmpty ?$0.companyName : $0.dealName } ?? ""
    }

    /// A tick or Remove from the call window: "hs-<id>", or a register id linked to a HubSpot task. False when the id
    /// is not a HubSpot one (the Notion tasks handle it).
    func callAction(id: String, folder: String, op: String, done: Bool = true) -> Bool {
        var task: CRMTask?, c: Commitment?
        if id.hasPrefix("hs-") {
            task = tasks.first { $0.id == String(id.dropFirst(3)) }
            c = task.flatMap(linkedCommitment)
        } else if let item = Register.shared.items(in: folder).first(where: { $0.id == id }), !item.crmTask.isEmpty {
            // in the list or not (closed, another owner's): the saved task id and portal
            let o = op == "remove" ? "drop" : done ? "done" : "reopen"
            if !mark(item, o) { Register.shared.mark(item, o) }        // a link without a portal: the register alone
            return true
        } else { return false }
        guard let t = task else { return true }
        if op == "remove" { remove(t, commitment: c) } else { tick(t, done: done) }
        return true
    }
}

/// One HubSpot task in On you: the badge HS, the title, the second line (status, due) and Remove.
struct CRMTaskRow: View {
    let task: CRMTask
    let commitment: Commitment?
    var onHide: (() -> Void)? = nil
    @ObservedObject var store = HubSpotTasks.shared

    var body: some View {
        let t = task
        let dueD = CommitmentRegister.dueDate(t.due, from: Date())
        let overdue = dueD.map { $0 < Calendar.current.startOfDay(for: Date()) } ?? false
        let sub = [commitment.map { Register.shared.projectName($0) } ?? "", L("htask.st." + t.status),
                   commitment != nil ? L("ntask.fromACall") : ""].filter { !$0.isEmpty }.joined(separator: " · ")
        let dueS = t.due.isEmpty ? "" : (overdue ? L("reg.overdue", ["due": Register.dueText(t.due)]) : L("reg.due", ["due": Register.dueText(t.due)]))
        HStack(alignment: .top, spacing: 8) {
            Toggle("", isOn: Binding(get: { false }, set: { _ in store.tick(t) }))
                .toggleStyle(.pnCheckbox).labelsHidden()
                .accessibilityLabel(L("reg.markDoneA11y", ["text": t.title]))
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(L("htask.badge")).font(PN.sys(10, .bold)).foregroundStyle(PN.C.text2)
                        .padding(.horizontal, 4).padding(.vertical, 1)
                        .background(PN.C.surface2, in: RoundedRectangle(cornerRadius: PN.r(4)))
                    Text(t.title).font(PN.Font.body).foregroundStyle(PN.C.text).fixedSize(horizontal: false, vertical: true)
                }
                (Text(sub).foregroundColor(PN.C.text3) + Text((sub.isEmpty || dueS.isEmpty ? "" : " · ") + dueS).foregroundColor(overdue ? PN.C.danger : PN.C.text3))
                    .font(PN.Font.caption)
            }
            Spacer(minLength: 0)
            RemoveButton { store.confirmRemove(t, commitment: commitment) }
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        .onTapGesture(count: 2) { if let u = URL(string: store.url(t)) { NSWorkspace.shared.open(u) } }
        .contextMenu {
            Button(L("reg.copy")) { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(t.title, forType: .string) }
            if let u = URL(string: store.url(t)), !store.url(t).isEmpty { Button(L("htask.open")) { NSWorkspace.shared.open(u) } }
            if let c = commitment, let f = Register.shared.transcript(c) { Button(L("reg.openCall")) { NSWorkspace.shared.open(URL(fileURLWithPath: f)) } }
            Divider()
            if let h = onHide { Button(L("ntask.hideHere")) { h() } }
            Button(L("ntask.remove")) { store.confirmRemove(t, commitment: commitment) }
        }
    }
}
