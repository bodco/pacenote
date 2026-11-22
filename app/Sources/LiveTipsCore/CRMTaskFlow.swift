import Foundation

/// PB-17 (INT7, rules/hubspot-tasks.yaml): the workflow of the PM's HubSpot tasks without the UI, so CoreChecks drives
/// it on a mocked HubSpot: the sync, Add to HubSpot tasks, ticks, Remove, the after-call add and their Undo. The app's
/// HubSpotTasks is a thin shell over it (the connector, the settings, the register folders, the toasts).
///
/// Every write goes to the portal the task came from: the portal is read before each write and before the queue is
/// sent, cached tasks and queued writes carry it, and the register's crm_link keeps it (crm_portal).
@MainActor
public final class CRMTaskFlow {
    /// What the PM is told (the app turns these into toasts and lines).
    public enum Note {
        case added(Int, undo: (() -> Void)?)
        case completed(String, undo: () -> Void)
        case refused(String)
        case removed(undo: () -> Void)
        case removeRefused(String, url: String)
        case undoRefused(Int, reason: String)       // N new tasks stayed in HubSpot and stay linked
        case accountChanged                        // the key now opens another portal: nothing was written
        case replayRefused(Int, reason: String, retry: () async -> Void)   // queued writes HubSpot refused at the next sync
        case relinkCall                            // the call's CRM link is of another (or an unknown) account: nothing was written
    }
    public enum Outcome: Equatable { case sent, queued, refused(String), changed }

    public private(set) var tasks: [CRMTask] = [] { didSet { onChange(nil) } }
    public private(set) var syncedAt: Date? { didSet { onChange(nil) } }
    private var attemptedAt: Date?                  // the last sync, also one that failed
    public private(set) var error = "" { didSet { if error != oldValue { onChange(nil) } } }
    public private(set) var busy = false { didSet { onChange(nil) } }
    public private(set) var portal = "", region = ""
    public private(set) var ownerNames: [String: String] = [:]
    /// Writes HubSpot has not taken yet: {"key", "op": "status"|"delete", "task", "status", "portal", and for a delete
    /// "kind": "remove"|"undo", "folder", "item"}. Sent at the next sync, only to their portal.
    public private(set) var pending: [[String: String]] = []
    private var hidden: Set<String> = []            // removed, inside the 8 s Undo
    private var removing: [String: Task<Void, Never>] = [:]
    private var adding: Set<String> = []            // folder|id of commitments being added now
    private var sending: Set<String> = []           // keys of queued writes being sent now
    private var linkCursor = 0                      // where the next sync goes on reading linked tasks
    private var relinkNeeded = false                // a silent add met a call link of another account
    public static let linkReads = 80

    let cachePath: String
    let connected: () -> Bool
    let connector: () -> HubSpotConnector?
    let settings: () -> CRMTasksSettings
    let matchingOwner: () -> String
    let items: () -> [Commitment]
    let onChange: (String?) -> Void                 // a register folder changed (nil: only the lists)
    let notify: (Note) -> Void
    let sleep: (Double) async -> Void

    public init(cachePath: String, connected: @escaping () -> Bool, connector: @escaping () -> HubSpotConnector?,
                settings: @escaping () -> CRMTasksSettings, matchingOwner: @escaping () -> String,
                items: @escaping () -> [Commitment], onChange: @escaping (String?) -> Void = { _ in },
                notify: @escaping (Note) -> Void = { _ in },
                sleep: @escaping (Double) async -> Void = { s in try? await Task.sleep(nanoseconds: UInt64(s * 1e9)) }) {
        self.cachePath = cachePath; self.connected = connected; self.connector = connector; self.settings = settings
        self.matchingOwner = matchingOwner; self.items = items; self.onChange = onChange; self.notify = notify; self.sleep = sleep
        loadCache()
    }

    public var ready: Bool { settings().enabled && connected() }

    func line(_ e: Error) -> String { (e as? CRMError)?.message ?? e.localizedDescription }
    /// HubSpot said no for good (a 4xx); a 5xx, a 429 or no answer may pass later, so the write waits in the queue.
    static func refusal(_ e: Error) -> CRMError? {
        guard let x = e as? CRMError, (400..<500).contains(x.code), ![408, 429].contains(x.code) else { return nil }
        return x
    }
    static let maxTries = 6                         // a queued write still failing after this many syncs is reported
    static let changedLine = "the HubSpot key now opens another account; nothing was written"

    // MARK: the portal

    /// Reads the portal the key opens now. Another portal than the one of the cache drops the cached tasks, owners
    /// and queued writes (their ids belong to the other account; a queued Remove gives its item back).
    func bind(_ hs: HubSpotConnector) async throws {
        let p = try await hs.portal()
        guard !p.id.isEmpty else { throw CRMError(code: 0, message: "HubSpot did not say which account the key opens") }
        if !portal.isEmpty && portal != p.id {
            var dropped = pending
            pending = []; tasks = []; ownerNames = [:]; hidden = []
            for i in dropped.indices {               // ticks of one task: only the last is settled, from the first's state
                guard dropped[i]["op"] == "status", let j = dropped.indices.first(where: { $0 > i && dropped[$0]["op"] == "status"
                        && dropped[$0]["task"] == dropped[i]["task"] }) else { continue }
                Self.carry(dropped[i], into: &dropped[j]); dropped[i]["op"] = "carried"
            }
            for e in dropped { settle(e, ok: false) }
            if !dropped.isEmpty { notify(.accountChanged) }
        }
        portal = p.id; region = p.region
        saveCache()
    }

    /// Before a write: the key still opens `at`, the portal the task was read from.
    private func sameAccount(_ hs: HubSpotConnector, _ at: String) async throws -> Bool {
        try await bind(hs)
        return !at.isEmpty && portal == at
    }

    // MARK: sync

    public func sync() async {
        guard ready, !busy, let hs = connector() else { return }
        busy = true
        attemptedAt = Date()
        defer { busy = false }
        do {
            try await bind(hs)
            let queued = pending
            let out = await flushPending(hs, bound: true)
            let failed = queued.filter { e in out[e["key"] ?? ""].map { if case .refused = $0 { return true }; return false } ?? false }
            var refusal: String?
            if !failed.isEmpty, case .refused(let why)? = out[failed.last?["key"] ?? ""] {
                refusal = why                                                   // said before the list read, which may fail
                notify(.replayRefused(failed.count, reason: why, retry: { [weak self] in await self?.retry(failed) }))
            }
            if ownerNames.isEmpty, let os = try? await hs.owners() {
                ownerNames = Dictionary(os.map { ($0.id, $0.name) }, uniquingKeysWith: { a, _ in a })
            }
            let s = settings()
            tasks = try await hs.openTasks(owner: s.ownerFilter(matchingOwner: matchingOwner()), queue: s.queueId)
            syncedAt = Date()
            error = refusal.map { "HubSpot: " + $0 } ?? ""                       // the list read does not hide it
            saveCache()
            await syncLinks(hs)
        } catch {
            self.error = "HubSpot: " + line(error)
        }
    }

    /// The last sync (or the last attempt, so a failing one is not repeated every few seconds) is older than `seconds`.
    public func stale(_ seconds: Double = 300, now: Date = Date()) -> Bool {
        (attemptedAt ?? syncedAt).map { now.timeIntervalSince($0) > seconds } ?? true
    }

    /// rules/hubspot-tasks.yaml > both_ways, for the items linked to a task of this portal only. Only the links the
    /// list does not answer are read, 80 per sync, the next 80 at the next sync, so every link is read in turn.
    private func syncLinks(_ hs: HubSpotConnector) async {
        let openIds = Set(tasks.map(\.id))
        let all = items().filter { c in
            guard !c.crmTask.isEmpty, c.status != "dropped", c.crmPortal == portal, !isPendingDelete(c.crmTask) else { return false }
            if c.status == "open" && openIds.contains(c.crmTask) { return false }
            if c.status == "done" && c.doneSource != "hubspot" && openIds.contains(c.crmTask) { return false }   // closed here, HubSpot not yet
            return true
        }
        let need = all.filter { $0.status == "open" } + all.filter { $0.status != "open" }
        let batch = need.count <= Self.linkReads ? need : (0..<Self.linkReads).map { need[(linkCursor + $0) % need.count] }
        linkCursor = need.count <= Self.linkReads ? 0 : (linkCursor + Self.linkReads) % need.count
        for c in batch {
            let t: CRMTask?
            do { t = try await hs.task(id: c.crmTask) } catch { continue }   // nil: deleted in HubSpot; an error: next time
            switch CRMTasks.syncOp(commitmentStatus: c.status, doneSource: c.doneSource, task: t) {
            case "done": mark(c, "done", source: "hubspot")
            case "reopen": mark(c, "reopen")
            case "crm_unlink": mark(c, "crm_unlink", source: "removed")
            default: break
            }
        }
    }

    /// Sends the queued writes of the portal the key opens; the rest of the queue stays. Returns what happened to each.
    @discardableResult
    func flushPending(_ hs: HubSpotConnector, bound: Bool = false) async -> [String: Outcome] {
        var out: [String: Outcome] = [:]
        let batch = pending.filter { !sending.contains($0["key"] ?? "") }
        guard !batch.isEmpty else { return out }
        if !bound { do { try await bind(hs) } catch { return out } }     // unreachable: everything waits
        for b in batch {
            // as the queue has it now: dropped by bind, or carrying the state of a refused write before it
            guard let key = b["key"], let e = pending.first(where: { $0["key"] == key }) else { continue }
            guard let id = e["task"], e["portal"] == portal else { finish(key); settle(e, ok: false); out[key] = .changed; continue }
            // an earlier write of the task still waits (busy HubSpot) or is being sent: this one goes after it, so a
            // retried older write never lands over a newer one
            if let i = pending.firstIndex(where: { $0["key"] == key }), pending[..<i].contains(where: { $0["task"] == id }) {
                out[key] = .queued; continue
            }
            sending.insert(key)
            do {
                if e["op"] == "delete" { try await hs.delete(type: "tasks", id: id) } else { try await hs.setTaskStatus(id: id, e["status"] ?? "") }
                finish(key); settle(e, ok: true); out[key] = .sent
            } catch let x as CRMError where x.code == 404 {
                finish(key); settle(e, ok: true); out[key] = .sent             // gone already
            } catch let x where Self.refusal(x) != nil || tries(key) >= Self.maxTries {
                finish(key); settle(e, ok: false); out[key] = .refused(line(x))
                error = "HubSpot: " + line(x)
            } catch {
                bump(key); out[key] = .queued                                   // offline or HubSpot busy: next sync
            }
            sending.remove(key)
        }
        saveCache()
        return out
    }

    private func finish(_ key: String) { pending.removeAll { $0["key"] == key } }
    private func tries(_ key: String) -> Int { Int(pending.first { $0["key"] == key }?["tries"] ?? "") ?? 0 }
    private func bump(_ key: String) {
        if let i = pending.firstIndex(where: { $0["key"] == key }) { pending[i]["tries"] = String(tries(key) + 1) }
    }

    /// Retry of queued writes HubSpot refused: each goes back in the queue as it was (a Remove takes its item out
    /// again, a tick ticks it again) and is sent now. Refused again: said again.
    public func retry(_ entries: [[String: String]]) async {
        guard let hs = connector() else { return }
        var keys: [String] = []
        for var e in entries {
            e["key"] = nil; e["tries"] = nil
            if e["op"] == "status", let id = e["task"] {
                setStatus(id, e["status"] ?? "")
                let done = e["done"] == "1"
                if let c = queuedItem(e), c.status == (done ? "open" : "done") { mark(c, done ? "done" : "reopen") }
            }
            if e["op"] == "delete", let id = e["task"] {
                hidden.insert(id)
                if e["kind"] == "remove", let f = e["folder"], !f.isEmpty, let i = e["item"], !i.isEmpty {
                    CommitmentRegister.mark(folder: f, id: i, op: "drop"); onChange(f)
                }
            }
            keys.append(enqueue(e))
        }
        onChange(nil)
        let out = await flushPending(hs)
        let again = entries.indices.filter { if case .refused? = out[keys[$0]] { return true }; return false }
        if again.isEmpty { error = "" }
        if let last = again.last, case .refused(let why)? = out[keys[last]] {
            notify(.replayRefused(again.count, reason: why, retry: { [weak self] in await self?.retry(again.map { entries[$0] }) }))
        }
    }

    /// A queued write is over: a delete that went through takes the task out (and unlinks an undone add); a refused
    /// Remove gives the item back, a refused undo leaves the item linked to the task that stayed.
    private func settle(_ e: [String: String], ok: Bool) {
        if e["op"] == "carried" { return }
        if e["op"] == "status" { if !ok { settleStatus(e) }; return }
        guard e["op"] == "delete", let id = e["task"] else { return }
        hidden.remove(id)
        let folder = e["folder"] ?? "", item = e["item"] ?? ""
        if ok {
            tasks.removeAll { $0.id == id }
            if e["kind"] == "undo", !folder.isEmpty {
                CommitmentRegister.mark(folder: folder, id: item, op: "crm_unlink", source: "undo"); onChange(folder)
            }
        } else if e["kind"] == "remove", !folder.isEmpty,
                  CommitmentRegister.load(folder: folder).first(where: { $0.id == item })?.status == "dropped" {
            restore(folder: folder, id: item, e)                                 // as it was: open, or done with its date
        } else {
            onChange(nil)
        }
    }

    private func isPendingDelete(_ id: String) -> Bool { pending.contains { $0["op"] == "delete" && $0["task"] == id } }

    private func enqueue(_ e: [String: String]) -> String {
        var e = e
        let key = UUID().uuidString
        e["key"] = key
        pending.append(e)
        saveCache()
        return key
    }

    // MARK: the cache

    private func loadCache() {
        guard let d = FileManager.default.contents(atPath: cachePath),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return }
        if let a = o["tasks"], let data = try? JSONSerialization.data(withJSONObject: a),
           let list = try? JSONDecoder().decode([CRMTask].self, from: data) { tasks = list }
        if let ts = o["synced"] as? Double { syncedAt = Date(timeIntervalSince1970: ts) }
        portal = (o["portal"] as? String) ?? ""; region = (o["region"] as? String) ?? ""
        // a write queued before the portal was known (or without a key) is never sent
        pending = ((o["pending"] as? [[String: String]]) ?? []).filter { !($0["portal"] ?? "").isEmpty && $0["key"] != nil }
        ownerNames = (o["owners"] as? [String: String]) ?? [:]
        if portal.isEmpty { tasks = [] }
    }

    func saveCache() {
        let list = (try? JSONSerialization.jsonObject(with: (try? JSONEncoder().encode(tasks)) ?? Data("[]".utf8))) ?? []
        let o: [String: Any] = ["tasks": list, "synced": (syncedAt ?? Date()).timeIntervalSince1970, "pending": pending,
                                "portal": portal, "region": region, "owners": ownerNames]
        if let d = try? JSONSerialization.data(withJSONObject: o) {
            try? FileManager.default.createDirectory(atPath: (cachePath as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
            try? d.write(to: URL(fileURLWithPath: cachePath))
        }
    }

    // MARK: what a list shows

    public var shown: [CRMTask] {
        ready ? tasks.filter { $0.open && !hidden.contains($0.id) && !isPendingDelete($0.id) } : []
    }

    public func url(_ t: CRMTask) -> String { CRMTasks.url(portal: portal, region: region, id: t.id) }

    /// The register item linked to a task of this portal.
    public func linkedCommitment(_ t: CRMTask) -> Commitment? {
        items().first { $0.crmTask == t.id && $0.crmPortal == portal && !portal.isEmpty }
    }

    /// The item as the register file says now (another add may have linked it since the menu was built).
    func current(_ c: Commitment) -> Commitment? { CommitmentRegister.load(folder: c.folder).first { $0.id == c.id } }

    private func mark(_ c: Commitment, _ op: String, source: String = "manual") {
        CommitmentRegister.mark(folder: c.folder, id: c.id, op: op, source: source)
        onChange(c.folder)
    }

    // MARK: ticks

    /// A tick on a task (or a linked item): its status at once, then HubSpot, in the portal `at` (the task's; the
    /// current one for a row of the lists). Another portal than the key opens: nothing changes. Refused or another
    /// account found at the write: the row and the item come back. Offline: the write waits in the queue.
    @discardableResult
    public func tick(_ t: CRMTask, done: Bool = true, at: String? = nil, commitment: Commitment? = nil) -> Task<Void, Never>? {
        guard let hs = connector() else { return nil }
        let at = at ?? portal
        guard !at.isEmpty, at == portal else { notify(.accountChanged); return nil }
        let before = t.open ? t.status : "NOT_STARTED"
        let value = done ? "COMPLETED" : before
        let c = (commitment ?? linkedCommitment(t)).map { current($0) ?? $0 }   // as the register has it now, to give back
        setStatus(t.id, value)
        if let c { mark(c, done ? "done" : "reopen") }
        return Task {
            switch await self.write(hs, id: t.id, status: value, at: at, item: c, done: done, prior: t.status) {
            case .sent:
                if done { self.notify(.completed(t.title, undo: { [weak self] in self?.untick(t, to: before, at: at, commitment: c) })) }
            case .queued: break
            case .refused(let why):
                self.setStatus(t.id, t.status); if let c { self.restore(c) }
                self.notify(.refused(why))
            case .changed:
                if let c { self.restore(c) }
                self.notify(.accountChanged)
            }
        }
    }

    /// Undo of a tick, in the portal of the tick: the status before, and the item reopened. The key opens another
    /// portal now: nothing changes or is sent. Refused: the task and the item stay completed.
    @discardableResult
    public func untick(_ t: CRMTask, to status: String, at: String, commitment: Commitment? = nil) -> Task<Void, Never>? {
        guard let hs = connector() else { return nil }
        guard !at.isEmpty, at == portal else { notify(.accountChanged); return nil }
        let c = (commitment ?? linkedCommitment(t)).map { current($0) ?? $0 }   // done, with its date and source
        if tasks.contains(where: { $0.id == t.id }) { setStatus(t.id, status) }
        else if commitment == nil { var u = t; u.status = status; tasks.append(u) }   // a linked task outside the lists stays out
        if let c { mark(c, "reopen") }
        return Task {
            switch await self.write(hs, id: t.id, status: status, at: at, item: c, done: false, prior: "COMPLETED") {
            case .sent, .queued: break
            case .refused(let why):
                self.setStatus(t.id, "COMPLETED"); if let c { self.restore(c) }
                self.notify(.refused(why))
            case .changed:
                if let c { self.restore(c) }
                self.notify(.accountChanged)
            }
        }
    }

    private func setStatus(_ id: String, _ status: String) {
        if let i = tasks.firstIndex(where: { $0.id == id }) { tasks[i].status = status }
    }

    /// One status write to `at`. Offline (no answer, or HubSpot busy after the retries): queued with the item it
    /// moved (`done`: ticked or reopened) and the task's status before, so a refusal at the next sync gives both back.
    func write(_ hs: HubSpotConnector, id: String, status: String, at: String, item c: Commitment? = nil, done: Bool = true,
               prior: String = "") async -> Outcome {
        let queue = { () -> String in
            self.enqueue(["op": "status", "task": id, "status": status, "portal": at, "folder": c?.folder ?? "", "item": c?.id ?? "",
                          "done": done ? "1" : "0", "prior": prior].merging(Self.before(c)) { a, _ in a })
        }
        // an earlier write of the task still waits: this one goes after it, so HubSpot ends with the last. The queue is
        // sent now; a refusal there already gave the row and the item back, so it is only said here.
        if pending.contains(where: { $0["op"] == "status" && $0["task"] == id }) {
            let key = queue()
            switch await flushPending(hs)[key] {
            case .sent?: return .sent
            case .refused(let why)?: notify(.refused(why)); return .queued
            case .changed?: notify(.accountChanged); return .queued
            default: return .queued
            }
        }
        do {
            guard try await sameAccount(hs, at) else { return .changed }
            try await hs.setTaskStatus(id: id, status)
            return .sent
        } catch let e where Self.refusal(e) != nil {
            return .refused(line(e))
        } catch {
            _ = queue()
            return .queued
        }
    }

    /// A queued status write HubSpot refused (or another account): the row and the item go back to what HubSpot last
    /// took, the state before the first write of the task that did not go through. A later write of the task still
    /// queued takes that state over; the last one gives it back, unless the PM changed the item since.
    private func settleStatus(_ e: [String: String]) {
        guard let id = e["task"] else { return }
        if let j = pending.firstIndex(where: { $0["op"] == "status" && $0["task"] == id }) {
            Self.carry(e, into: &pending[j]); saveCache(); onChange(nil); return
        }
        if let p = e["prior"], !p.isEmpty { setStatus(id, p) }
        let done = e["done"] == "1"
        if let c = queuedItem(e), c.status == (done ? "done" : "open") {
            restore(folder: c.folder, id: c.id, e, fallback: done ? "open" : "done")
        } else { onChange(nil) }
    }

    /// The state a write found, taken over by the next write of the same task when this one did not go through.
    static func carry(_ e: [String: String], into next: inout [String: String]) {
        for k in ["prior", "was", "done_at", "done_source"] { next[k] = e[k] ?? "" }
        if e["was"] == nil { next["was"] = e["done"] == "1" ? "open" : "done" }
    }

    /// The item as it was before a write that did not go through or was undone: open, or done with its date and
    /// source (a reopen would lose them), from `was`, `done_at` and `done_source` of the write.
    private func restore(folder: String, id: String, _ e: [String: String], fallback: String = "open") {
        let was = (e["was"] ?? "").isEmpty ? fallback : e["was"]!
        if was == "done" {
            let at = e["done_at"] ?? "", source = e["done_source"] ?? ""
            CommitmentRegister.append(folder: folder, [["op": "done", "id": id, "at": at.isEmpty ? CommitmentRegister.nowISO() : at,
                                                        "source": source.isEmpty ? "manual" : source]])
        } else if was == "dropped" {
            CommitmentRegister.mark(folder: folder, id: id, op: "drop")
        } else {
            CommitmentRegister.mark(folder: folder, id: id, op: "reopen")
        }
        onChange(folder)
    }

    /// `c` as it is now, for a write that may have to give it back.
    static func before(_ c: Commitment?) -> [String: String] {
        guard let c else { return [:] }
        return ["was": c.status, "done_at": c.status == "done" ? c.doneAt : "", "done_source": c.status == "done" ? c.doneSource : ""]
    }

    private func restore(_ c: Commitment) { restore(folder: c.folder, id: c.id, Self.before(c)) }

    /// The item a queued write moved, as the register says now, while still linked to the write's task and portal.
    private func queuedItem(_ e: [String: String]) -> Commitment? {
        guard let id = e["task"], let f = e["folder"], !f.isEmpty, let i = e["item"], !i.isEmpty else { return nil }
        return CommitmentRegister.load(folder: f).first { $0.id == i && $0.crmTask == id && $0.crmPortal == e["portal"] }
    }

    // MARK: Remove

    /// rules/hubspot-tasks.yaml > remove: out of the lists at once, deleted in HubSpot after the 8 s Undo. A delete
    /// HubSpot refuses (or another account) gives the task and the item back; offline it waits in the queue.
    /// Returns the job (CoreChecks awaits it).
    @discardableResult
    public func remove(_ t: CRMTask, commitment: Commitment?, at: String? = nil) -> Task<Void, Never>? {
        guard let hs = connector() else { return nil }
        let at = at ?? portal
        guard !at.isEmpty, at == portal else { notify(.accountChanged); return nil }
        let c = commitment.map { current($0) ?? $0 }        // a completed item comes back completed (Undo, refusal)
        if let c { mark(c, "drop") }
        hidden.insert(t.id); onChange(nil)
        let job = Task { [weak self] in
            guard let self else { return }
            await self.sleep(8.5)
            guard !Task.isCancelled, self.removing[t.id] != nil else { return }
            self.removing[t.id] = nil
            let key = self.enqueue(["op": "delete", "kind": "remove", "task": t.id, "portal": at,
                                    "folder": c?.folder ?? "", "item": c?.id ?? ""].merging(Self.before(c)) { a, _ in a })
            switch await self.flushPending(hs)[key] {
            case .refused(let why)?: self.notify(.removeRefused(why, url: self.url(t)))
            case .changed?: self.notify(.accountChanged)
            default: break
            }
        }
        removing[t.id] = job
        notify(.removed(undo: { [weak self] in
            guard let self, let j = self.removing[t.id] else { return }         // the delete started: too late
            j.cancel(); self.removing[t.id] = nil; self.hidden.remove(t.id)
            if let c { self.restore(c) } else { self.onChange(nil) }
        }))
        return job
    }

    // MARK: a linked item from any list

    /// The task of an item linked to HubSpot, read from the link (the task may be closed, another owner's or in another
    /// queue, so not in the lists): the cached row when there is one, else a row made from the item.
    public func linkedTask(_ c: Commitment) -> CRMTask? {
        guard !c.crmTask.isEmpty, !c.crmPortal.isEmpty else { return nil }
        if c.crmPortal == portal, let t = tasks.first(where: { $0.id == c.crmTask }) { return t }
        return CRMTask(id: c.crmTask, title: c.text, status: c.status == "done" ? "COMPLETED" : "NOT_STARTED")
    }

    /// A tick on an item linked to a HubSpot task, wherever it is shown: the task's saved id and portal. HubSpot tasks
    /// off, or the key opens another portal than the link's: the item alone changes (and the PM is told of the account).
    @discardableResult
    public func tickLinked(_ c: Commitment, done: Bool) -> Task<Void, Never>? {
        guard linkedTask(c) != nil else { return nil }
        guard ready, connector() != nil, c.crmPortal == portal, let t = linkedTask(c) else {
            mark(c, done ? "done" : "reopen")
            if ready { notify(.accountChanged) }
            return nil
        }
        return tick(t, done: done, at: c.crmPortal, commitment: c)
    }

    /// Remove on an item linked to a HubSpot task, wherever it is shown: rules/hubspot-tasks.yaml > remove on the
    /// saved id and portal. HubSpot tasks off, or another portal: the item alone is dropped.
    @discardableResult
    public func removeLinked(_ c: Commitment) -> Task<Void, Never>? {
        guard linkedTask(c) != nil else { return nil }
        guard ready, connector() != nil, c.crmPortal == portal, let t = linkedTask(c) else {
            mark(c, "drop")
            if ready { notify(.accountChanged) }
            return nil
        }
        return remove(t, commitment: c, at: c.crmPortal)
    }

    // MARK: Add

    public struct Added: Equatable {
        public var id: String; public var created: Bool
        public init(id: String, created: Bool) { self.id = id; self.created = created }
    }

    /// "Add to HubSpot tasks" on an item owned by me: a task with `properties`, associated with `refs` (the call's
    /// deal, company and contacts), then crm_link with the portal. One add per item at a time; the register file is
    /// read again before the task is made. An open task with the same words on the call's deal or company (or contact)
    /// is linked instead, after a fresh read of its associations. `linkPortal` is the portal the call's CRM link was
    /// made in: refs of another portal, or of a link that does not say, are never sent (the PM refreshes the link).
    @discardableResult
    public func add(_ c: Commitment, properties: [String: Any], refs: [CRMRef], linkPortal: String,
                    silent: Bool = false) async -> Added? {
        guard ready, c.mine, c.notionPage.isEmpty, c.crmTask.isEmpty, let hs = connector() else { return nil }
        let key = c.folder + "|" + c.id
        guard adding.insert(key).inserted else { return nil }
        defer { adding.remove(key) }
        func unlinked() -> Commitment? {
            guard let now = current(c), now.status == "open", now.notionPage.isEmpty, now.crmTask.isEmpty else { return nil }
            return now
        }
        do {
            try await bind(hs)
            guard unlinked() != nil else { return nil }
            guard refs.isEmpty || (!linkPortal.isEmpty && linkPortal == portal) else {   // before the twin and the create
                if silent { relinkNeeded = true } else { notify(.relinkCall) }
                return nil
            }
            if let cand = CRMTasks.twin(shown, text: c.text, refs: refs),
               let fresh = try await hs.taskWithAssociations(id: cand.id),
               CRMTasks.twin([fresh], text: c.text, refs: refs) != nil {
                guard unlinked() != nil else { return nil }
                link(c, fresh.id)
                return Added(id: fresh.id, created: false)
            }
            guard unlinked() != nil else { return nil }
            let at = portal
            let t = try await hs.createTask(properties, associations: refs)
            guard !t.id.isEmpty else { return nil }
            link(c, t.id, at: at)
            if at == portal { tasks.append(t) }
            saveCache()
            if !silent { notify(.added(1, undo: nil)) }
            return Added(id: t.id, created: true)
        } catch {
            if !silent { notify(.refused(line(error))) } else { self.error = "HubSpot: " + line(error) }
            return nil
        }
    }

    private func link(_ c: Commitment, _ id: String, at: String? = nil) {
        CommitmentRegister.mark(folder: c.folder, id: c.id, op: "crm_link", crmTask: id, crmPortal: at ?? portal)
        onChange(c.folder)
    }

    // MARK: after a call

    /// rules/hubspot-tasks.yaml > after_call: with the switch on (and HubSpot the destination), every open item owned
    /// by me of the call in `folder` becomes a task, once; one toast with Undo for the new ones. Switch off: nothing
    /// is read from or written to HubSpot.
    public func afterCall(file: String, folder: String, destination: String?,
                          draft: (Commitment) -> (properties: [String: Any], refs: [CRMRef], portal: String)) async {
        guard ready, settings().autoAdd, destination == "hubspot" else { return }
        let key = CommitmentRegister.callKey(file)
        let list = CommitmentRegister.load(folder: folder).filter {
            $0.mine && $0.status == "open" && $0.notionPage.isEmpty && $0.crmTask.isEmpty && CommitmentRegister.callKey($0.call) == key }
        var made: [String] = []
        relinkNeeded = false
        for c in list {
            let d = draft(c)
            if let r = await add(c, properties: d.properties, refs: d.refs, linkPortal: d.portal, silent: true), r.created { made.append(r.id) }   // linked twins are not undone
        }
        if relinkNeeded { relinkNeeded = false; notify(.relinkCall) }            // once for the call
        guard !made.isEmpty else { return }
        let at = portal
        notify(.added(made.count, undo: { [weak self] in Task { await self?.undoAdds(made, folder: folder, at: at) } }))
    }

    /// Undo of the after-call add: each new task is deleted, and its item unlinked only once HubSpot deleted it. A
    /// refused delete leaves the item linked (and says so); offline the delete waits in the queue.
    @discardableResult
    public func undoAdds(_ ids: [String], folder: String, at: String) async -> [String: Outcome] {
        guard let hs = connector() else { return [:] }
        var keys: [String: String] = [:]
        for c in CommitmentRegister.load(folder: folder) where ids.contains(c.crmTask) && c.crmPortal == at {
            hidden.insert(c.crmTask)
            keys[c.crmTask] = enqueue(["op": "delete", "kind": "undo", "task": c.crmTask, "portal": at, "folder": folder, "item": c.id])
        }
        onChange(nil)
        let out = await flushPending(hs)
        var result: [String: Outcome] = [:], refused = 0, why = ""
        for (id, key) in keys {
            let o = out[key] ?? .queued
            result[id] = o
            switch o {
            case .refused(let r): refused += 1; why = r
            case .changed: refused += 1; why = Self.changedLine
            default: break
            }
        }
        if refused > 0 { notify(.undoRefused(refused, reason: why)) }
        return result
    }
}
