import AppKit
import LiveTipsCore
import SwiftUI

/// PB-17 (INT7, rules/hubspot-tasks.yaml): the PM's HubSpot tasks in On you, the Tracker and the call window's On me,
/// and the link between the commitments register and HubSpot (Add to HubSpot tasks, ticks and closes both ways,
/// Remove, the automatic add after a call). The same shape as NotionTasks, on the HubSpot client of Update HubSpot.
/// Nothing is written to HubSpot without the PM's action or the switch the PM turned on.
@MainActor
final class HubSpotTasks: ObservableObject {
    static let shared = HubSpotTasks()

    @Published private(set) var tasks: [CRMTask] = []
    @Published private(set) var syncedAt: Date?
    @Published var error = ""
    @Published private(set) var busy = false
    @Published private(set) var version = 0
    private var timer: Timer?
    private var portal = "", region = ""
    private var ownerNames: [String: String] = [:]
    private var pending: [[String: String]] = []      // offline ticks: {"task": id, "status": value}
    private var removing: [String: Task<Void, Never>] = [:]

    var settings: CRMTasksSettings { SettingsWindow.model.config.crm.tasks }
    var ready: Bool { settings.enabled && CRMService.shared.connected }
    /// "notion", "hubspot" or nil: where a new task of a commitment goes (rules/hubspot-tasks.yaml > destination).
    var destination: String? {
        CRMTasks.destination(notionReady: NotionTasks.shared.settings.ready, hubspotReady: ready, choice: settings.destination)
    }
    private var cachePath: String { (CallEngine.shared.appRoot as NSString).appendingPathComponent("state/hubspot-tasks.json") }

    private init() { loadCache() }

    /// The client of Update HubSpot; new tasks get the owner whose tasks are shown (else the Matching owner).
    private func connector() -> HubSpotConnector? {
        guard let hs = CRMService.shared.connector() as? HubSpotConnector else { return nil }
        let own = settings.ownerFilter(matchingOwner: SettingsWindow.model.config.crm.ownerId)
        if !own.isEmpty { hs.ownerId = own }
        return hs
    }

    private func line(_ e: Error) -> String { (e as? CRMError)?.message ?? e.localizedDescription }

    // MARK: sync

    /// Home, the Tracker or the call window is open: refresh now and every 5 minutes while one of them is.
    func keepFresh() {
        refresh()
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { _ in
            MainActor.assumeIsolated {
                if HomeWindow.window?.isVisible == true || CallEngine.shared.isRunning { HubSpotTasks.shared.refresh() }
            }
        }
    }

    func refresh() {
        guard ready, !busy, let hs = connector() else { return }
        let s = settings
        busy = true
        Task {
            defer { busy = false }
            do {
                await flushPending(hs)
                if portal.isEmpty, let p = try? await hs.portal() { portal = p.id; region = p.region }
                if ownerNames.isEmpty, let os = try? await hs.owners() { ownerNames = Dictionary(os.map { ($0.id, $0.name) }, uniquingKeysWith: { a, _ in a }) }
                tasks = try await hs.openTasks(owner: s.ownerFilter(matchingOwner: SettingsWindow.model.config.crm.ownerId), queue: s.queueId)
                syncedAt = Date()
                error = ""
                saveCache()
                await syncLinks(hs)
                version += 1
            } catch {
                self.error = "HubSpot: " + line(error)
            }
        }
    }

    /// rules/hubspot-tasks.yaml > both_ways: a linked item closed or reopened in HubSpot follows in the register; a task
    /// deleted in HubSpot unlinks the item, which stays open. Decided from a read of each task, never from search alone.
    private func syncLinks(_ hs: HubSpotConnector) async {
        let reg = Register.shared
        let openIds = Set(tasks.map(\.id))
        let linked = reg.folders.flatMap { reg.items(in: $0) }.filter { !$0.crmTask.isEmpty && $0.status != "dropped" }
        for c in linked.prefix(80) {
            if c.status == "open" && openIds.contains(c.crmTask) { continue }
            if c.status == "done" && c.doneSource != "hubspot" { continue }     // closed here; HubSpot was told
            guard let t = try? await hs.task(id: c.crmTask) else { continue } // an error: try again next time
            switch CRMTasks.syncOp(commitmentStatus: c.status, doneSource: c.doneSource, task: t) {
            case "done": reg.mark(c, "done", source: "hubspot")
            case "reopen": reg.mark(c, "reopen")
            case "crm_unlink": reg.mark(c, "crm_unlink", source: "removed")
            default: break
            }
        }
    }

    private func flushPending(_ hs: HubSpotConnector) async {
        var left: [[String: String]] = []
        for p in pending {
            guard let id = p["task"], let st = p["status"] else { continue }
            do { try await hs.setTaskStatus(id: id, st) } catch let e as CRMError where e.code == 404 { continue } catch { left.append(p) }
        }
        pending = left
    }

    private func loadCache() {
        guard let d = FileManager.default.contents(atPath: cachePath),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return }
        if let a = o["tasks"], let data = try? JSONSerialization.data(withJSONObject: a),
           let list = try? JSONDecoder().decode([CRMTask].self, from: data) { tasks = list }
        if let ts = o["synced"] as? Double { syncedAt = Date(timeIntervalSince1970: ts) }
        pending = (o["pending"] as? [[String: String]]) ?? []
        portal = (o["portal"] as? String) ?? ""; region = (o["region"] as? String) ?? ""
        ownerNames = (o["owners"] as? [String: String]) ?? [:]
    }

    private func saveCache() {
        let list = (try? JSONSerialization.jsonObject(with: (try? JSONEncoder().encode(tasks)) ?? Data("[]".utf8))) ?? []
        let o: [String: Any] = ["tasks": list, "synced": (syncedAt ?? Date()).timeIntervalSince1970, "pending": pending,
                                "portal": portal, "region": region, "owners": ownerNames]
        if let d = try? JSONSerialization.data(withJSONObject: o) {
            try? FileManager.default.createDirectory(atPath: (cachePath as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
            try? d.write(to: URL(fileURLWithPath: cachePath))
        }
    }

    // MARK: what a list shows

    var shown: [CRMTask] { ready ? tasks.filter { $0.open && removing[$0.id] == nil } : [] }

    func has(_ id: String) -> Bool { !id.isEmpty && shown.contains { $0.id == id } }

    func url(_ t: CRMTask) -> String { CRMTasks.url(portal: portal, region: region, id: t.id) }

    func ownerName(_ t: CRMTask) -> String {
        t.owner.isEmpty || t.owner == SettingsWindow.model.config.crm.ownerId ? "me" : (ownerNames[t.owner] ?? t.owner)
    }

    func linkedCommitment(_ t: CRMTask) -> Commitment? {
        let reg = Register.shared
        return reg.folders.lazy.flatMap { reg.items(in: $0) }.first { $0.crmTask == t.id }
    }

    /// The CRM link (deal, company, contacts) of the call a commitment or a transcript belongs to.
    static func refs(callFile: String) -> [CRMRef] {
        let base = callFile.hasSuffix(".md") ? String(callFile.dropLast(3)) : callFile
        return CRMLink.load(base: base).map(CRMCallLogger.refs) ?? []
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

    // MARK: actions

    /// A tick on a HubSpot task (or on a linked commitment): COMPLETED at once, Undo for 8 s writes the status before.
    func tick(_ t: CRMTask, done: Bool = true) {
        guard let hs = connector() else { return }
        let before = t.open ? t.status : "NOT_STARTED"
        let value = done ? "COMPLETED" : before
        if let i = tasks.firstIndex(where: { $0.id == t.id }) { tasks[i].status = value }
        if let c = linkedCommitment(t) { Register.shared.mark(c, done ? "done" : "reopen") }
        version += 1
        Task {
            do {
                try await hs.setTaskStatus(id: t.id, value)
                if done {
                    NotionTasks.shared.toast = .init(text: L("htask.setDone", ["title": t.title]), undo: { [weak self] in self?.untick(t, to: before) })
                }
            } catch let e as CRMError where e.code >= 400 {
                if let i = tasks.firstIndex(where: { $0.id == t.id }) { tasks[i].status = t.status }
                if let c = linkedCommitment(t) { Register.shared.mark(c, done ? "reopen" : "done") }
                NotionTasks.shared.toast = .init(text: L("htask.refused", ["reason": line(e)]), isError: true)
            } catch {
                pending.append(["task": t.id, "status": value]); saveCache()      // offline: sent when HubSpot answers
            }
            version += 1
        }
    }

    private func untick(_ t: CRMTask, to status: String) {
        guard let hs = connector() else { return }
        if let i = tasks.firstIndex(where: { $0.id == t.id }) { tasks[i].status = status } else { tasks.append(t) }
        if let c = linkedCommitment(t) { Register.shared.mark(c, "reopen") }
        version += 1
        Task { try? await hs.setTaskStatus(id: t.id, status); version += 1 }
    }

    /// rules/hubspot-tasks.yaml > remove: out of the lists at once; the task is deleted in HubSpot (its recycling bin)
    /// after the 8 s Undo, so Undo never needs HubSpot to restore anything.
    func remove(_ t: CRMTask, commitment: Commitment?) {
        guard let hs = connector() else { return }
        if let c = commitment { Register.shared.mark(c, "drop") }
        removing[t.id] = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 8_500_000_000)
            guard let self, !Task.isCancelled else { return }
            do { try await hs.delete(type: "tasks", id: t.id); self.tasks.removeAll { $0.id == t.id } }
            catch { NotionTasks.shared.toast = .init(text: L("htask.removeRefused", ["reason": self.line(error)]), openURL: self.url(t), isError: true) }
            self.removing[t.id] = nil
            self.saveCache(); self.version += 1
        }
        NotionTasks.shared.toast = .init(text: L("htask.removed"), undo: { [weak self] in
            guard let self else { return }
            self.removing[t.id]?.cancel(); self.removing[t.id] = nil
            if let c = commitment { Register.shared.mark(c, "reopen") }
            self.version += 1
        })
        version += 1
    }

    /// rules/hubspot-tasks.yaml > remove: a task Pacenote did not make (no linked item) asks once; one from a call does not.
    func confirmRemove(_ t: CRMTask, commitment: Commitment?) {
        guard commitment == nil else { remove(t, commitment: commitment); return }
        AskCenter.shared.ask(kind: "trash", origin: .window, title: L("htask.deleteQ", ["title": t.title]), info: L("htask.deleteInfo"),
                             choices: [Ask.Choice(id: "trash", title: L("htask.delete"), role: .danger), .cancel()]) { [weak self] _ in
            self?.remove(t, commitment: nil)
        }
    }

    /// "Add to HubSpot tasks" on a commitment (owner me): a task associated with the call's deal, company and
    /// contacts, then op crm_link. An open task with the same words on the same deal or company is linked instead.
    @discardableResult
    func add(_ c: Commitment, silent: Bool = false) async -> String? {
        guard ready, c.mine, c.notionPage.isEmpty, c.crmTask.isEmpty, let hs = connector() else { return nil }
        let file = Register.shared.transcript(c) ?? ""
        let refs = file.isEmpty ? [] : Self.refs(callFile: file)
        if let twin = CRMTasks.twin(shown, text: c.text, refs: refs) {
            link(c, twin.id)
            return twin.id
        }
        do {
            let rec = CallHistory.shared.records.first { $0.file == file }
            let when = c.createdDate.map { DateFormatter.localizedString(from: $0, dateStyle: .medium, timeStyle: .none) } ?? ""
            let body = CRMTasks.body(callTitle: Register.shared.callTitle(c) ?? TrackerModel.callTitle(c), date: when, link: rec?.notionURL ?? "")
            let due = CommitmentRegister.dueDate(c.due, from: c.createdDate ?? Date())
            let t = try await hs.createTask(CRMTasks.newTask(settings, title: c.text, due: CRMTasks.dueDate(due, defaultDays: settings.dueDays), body: body),
                                            associations: refs)
            guard !t.id.isEmpty else { return nil }
            link(c, t.id)
            tasks.append(t); saveCache()
            if !silent { NotionTasks.shared.toast = .init(text: L("htask.added", ["n": "1"])) }
            version += 1
            return t.id
        } catch {
            if !silent { NotionTasks.shared.toast = .init(text: L("htask.refused", ["reason": line(error)]), isError: true) }
            else { self.error = "HubSpot: " + line(error) }
            return nil
        }
    }

    private func link(_ c: Commitment, _ id: String) {
        CommitmentRegister.mark(folder: c.folder, id: c.id, op: "crm_link", crmTask: id)
        Register.shared.touch(c.folder)
    }

    /// rules/hubspot-tasks.yaml > after_call: with the switch on, every open item owned by me of this call becomes a
    /// HubSpot task, once. When Notion tasks are set up too, only when the PM chose HubSpot for new tasks.
    func afterCall(file: String) {
        guard ready, settings.autoAdd, destination == "hubspot" else { return }
        let folder = CallLayout.liveFolder(of: file)
        let key = CommitmentRegister.callKey(file)
        let items = CommitmentRegister.load(folder: folder).filter {
            $0.mine && $0.status == "open" && $0.notionPage.isEmpty && $0.crmTask.isEmpty && CommitmentRegister.callKey($0.call) == key }
        guard !items.isEmpty else { return }
        Task {
            var made: [String] = []
            for c in items {
                let before = tasks.count
                if let id = await add(c, silent: true), tasks.count > before { made.append(id) }   // linked twins are not undone
            }
            guard !made.isEmpty, let hs = connector() else { return }
            NotionTasks.shared.toast = .init(text: L("htask.added", ["n": "\(made.count)"]), undo: { [weak self] in
                guard let self else { return }
                for c in CommitmentRegister.load(folder: folder) where made.contains(c.crmTask) {
                    CommitmentRegister.mark(folder: folder, id: c.id, op: "crm_unlink", source: "undo")
                }
                Register.shared.touch(folder)
                self.tasks.removeAll { made.contains($0.id) }
                Task { for id in made { try? await hs.delete(type: "tasks", id: id) }; self.saveCache(); self.version += 1 }
            })
        }
    }

    // MARK: the call window

    /// The On me tab's HubSpot group: the open tasks on the call's deal, company or contacts. Register items linked to
    /// a shown task leave the "Open before the call" group (shown once, as the HubSpot task).
    func merge(into payload: inout [String: Any], callFile: String) {
        guard ready else { return }
        keepFresh()
        let base = callFile.hasSuffix(".md") ? String(callFile.dropLast(3)) : callFile
        let link = CRMLink.load(base: base)
        let ts = CRMTasks.forCall(shown, refs: link.map(CRMCallLogger.refs) ?? []).prefix(30)
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
        payload["hubspotProject"] = link.map { $0.dealName.isEmpty ? $0.companyName : $0.dealName } ?? ""
    }

    /// A tick or Remove from the call window: "hs-<id>", or a register id linked to a HubSpot task. False when the id
    /// is not a HubSpot one (the Notion tasks handle it).
    func callAction(id: String, folder: String, op: String, done: Bool = true) -> Bool {
        var task: CRMTask?, c: Commitment?
        if id.hasPrefix("hs-") {
            task = tasks.first { $0.id == String(id.dropFirst(3)) }
            c = task.flatMap(linkedCommitment)
        } else if let item = Register.shared.items(in: folder).first(where: { $0.id == id }), !item.crmTask.isEmpty {
            c = item; task = tasks.first { $0.id == item.crmTask }
            if task == nil {          // the task is not in the list (closed or another owner's): the register alone
                if op == "remove" { Register.shared.mark(item, "drop") } else { Register.shared.mark(item, done ? "done" : "reopen") }
                return true
            }
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
