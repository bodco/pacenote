import Foundation

// PB-52 G2 (S5): the app side of project memory, the local evidence store (docs/specs/PB-52-g2-evidence-store.md,
// sections 4, 8.5, 9.1, 10, 13 and 17 S5). Everything here is pure, for CoreChecks; the app wiring is
// LiveTips/EvidenceService.swift. Nothing runs and nothing is shown while the flag `evidence_store` is off.
// Owner decisions 24.11 (spec section 15): the cheapest model of the route reads, the after-call model writes topic
// summaries; no consent prompt before the first index, turning the flag on is the consent.

/// The flag `evidence_store` in config.json `features` (the engine reads the same file through `--config`).
public enum EvidenceFlag {
    public static let key = "evidence_store"

    public static func on(_ c: AppConfig) -> Bool { c.features?[key] == true }

    /// Turning the flag off writes `false` and keeps any other flag; nothing else in the config changes.
    public static func set(_ c: inout AppConfig, on: Bool) {
        var f = c.features ?? [:]
        f[key] = on
        c.features = f
    }
}

/// The per-project store key (spec section 4): a random UUID in projects.json, minted only with the flag on.
public enum EvidenceUID {
    /// Mints a uid for every project without one. Returns whether the registry changed (the caller saves it).
    @discardableResult
    public static func mint(_ reg: inout ProjectRegistry, make: () -> String = { UUID().uuidString.lowercased() }) -> Bool {
        var changed = false
        for i in reg.projects.indices where (reg.projects[i].evidenceUid ?? "").isEmpty {
            reg.projects[i].evidenceUid = make()
            changed = true
        }
        return changed
    }

    /// Store files (`<uid>.sqlite` names in `<support>/evidence/`) whose uid no project carries: a folder removed and
    /// added again, or an import whose slug changed (spec section 13.4). Listed in status, deleted only after the PM
    /// confirms.
    public static func orphans(storeFiles: [String], registry: ProjectRegistry) -> [String] {
        let known = Set(registry.projects.compactMap { $0.evidenceUid })
        return storeFiles.map { ($0 as NSString).lastPathComponent }
            .filter { $0.hasSuffix(".sqlite") }
            .map { String($0.dropLast(".sqlite".count)) }
            .filter { !$0.isEmpty && !$0.contains(".corrupt-") && !known.contains($0) }
            .sorted()
    }
}

/// `call-active.json` in the state folder next to ai-routes.json (spec section 8.5): written at call start, removed
/// at the end; a file older than 6 hours or with a dead pid is stale and removed at the next launch.
public enum CallActive {
    public static let fileName = "call-active.json"
    public static let staleAfter: TimeInterval = 6 * 3600

    public struct Info: Equatable {
        public var startedAt: Date
        public var pid: Int32
        public init(startedAt: Date, pid: Int32) { self.startedAt = startedAt; self.pid = pid }
    }

    /// `statePath` is the path of ai-routes.json (LT_AI_STATE), not a folder.
    public static func path(statePath: String) -> String {
        ((statePath as NSString).deletingLastPathComponent as NSString).appendingPathComponent(fileName)
    }

    public static func payload(_ i: Info) -> Data {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        let o: [String: Any] = ["started_at": f.string(from: i.startedAt), "pid": Int(i.pid)]
        return (try? JSONSerialization.data(withJSONObject: o, options: [.sortedKeys])) ?? Data()
    }

    public static func parse(_ d: Data) -> Info? {
        guard let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
              let s = o["started_at"] as? String, let t = ISO8601DateFormatter().date(from: s),
              let pid = (o["pid"] as? NSNumber)?.int32Value else { return nil }
        return Info(startedAt: t, pid: pid)
    }

    /// Stale: unreadable, older than 6 hours, or its process is gone.
    public static func isStale(_ i: Info?, now: Date, pidAlive: (Int32) -> Bool) -> Bool {
        guard let i = i else { return true }
        return now.timeIntervalSince(i.startedAt) > staleAfter || !pidAlive(i.pid)
    }

    /// Whether a live process with this pid can be the one that wrote the file: it started before `started_at` (a few
    /// seconds of slack). A pid reused after a reboot belongs to a process that started later, so the file is stale.
    /// An unknown start time trusts the pid.
    public static func writtenBy(_ i: Info, processStart: Date?) -> Bool {
        guard let s = processStart else { return true }
        return s <= i.startedAt.addingTimeInterval(5)
    }

    /// When a process started (sysctl), or nil when it cannot be read.
    public static func processStart(_ pid: Int32) -> Date? {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
        guard sysctl(&mib, 4, &info, &size, nil, 0) == 0, size > 0 else { return nil }
        let tv = info.kp_proc.p_un.__p_starttime
        guard tv.tv_sec > 0 else { return nil }
        return Date(timeIntervalSince1970: Double(tv.tv_sec) + Double(tv.tv_usec) / 1e6)
    }

    /// At launch: the file is removed when stale; a file of a live process (another running copy) stays.
    public static func shouldRemoveAtLaunch(_ d: Data?, now: Date, ownPid: Int32, pidAlive: (Int32) -> Bool) -> Bool {
        guard let d = d else { return false }
        let i = parse(d)
        if let i = i, i.pid == ownPid { return true }       // left by this process (cannot be in a call at launch)
        return isStale(i, now: now, pidAlive: pidAlive)
    }
}

/// The state requests the app sent (hide, unhide, revoke, supersede) per project, kept until `evidence status` lists
/// them as applied and re-sent otherwise (spec section 13.1). Also the project's current revoked list (the CLI takes
/// the full list each time).
public struct EvidenceRequests: Codable, Equatable {
    public struct Request: Codable, Equatable {
        public var id: String
        public var args: [String]          // the CLI arguments after the command name's base, re-sent as they were
        public var sentAt: Date
        public init(id: String, args: [String], sentAt: Date) { self.id = id; self.args = args; self.sentAt = sentAt }
    }
    public var pending: [Request] = []
    public var revoked: [String] = []
    public init() {}

    public mutating func add(_ r: Request) {
        guard !pending.contains(where: { $0.id == r.id }) else { return }
        pending.append(r)
    }

    /// Drops the requests `evidence status` lists as applied.
    public mutating func applied(_ ids: Set<String>) { pending.removeAll { ids.contains($0.id) } }

    /// The requests to send again as they were: overrides not listed as applied (a re-sent applied id is a no-op in
    /// the CLI). An `exclude` is never replayed as stored: an old list could land after a newer one; `staleExcludes`
    /// tells the caller to send one fresh request per list with the current lists instead.
    public func toResend(applied ids: Set<String>) -> [Request] {
        pending.filter { !ids.contains($0.id) && !Self.isExclude($0) }
    }

    /// Whether an `exclude` request is still unapplied: the caller drops them all and sends the current lists fresh.
    public func staleExcludes(applied ids: Set<String>) -> Bool {
        pending.contains { !ids.contains($0.id) && Self.isExclude($0) }
    }

    /// Drops every unapplied `exclude` request (replaced by a fresh one with the current lists).
    public mutating func dropExcludes() { pending.removeAll { Self.isExclude($0) } }

    public static func isExclude(_ r: Request) -> Bool { r.args.count > 2 && r.args[2] == "exclude" }

    /// After the project's memory is deleted: the sent requests go with the store, the revoked list stays (a document
    /// that lost access must not come back as active in the next index).
    public func afterDelete() -> EvidenceRequests {
        var r = EvidenceRequests()
        r.revoked = revoked
        return r
    }

    /// The ids `evidence status` reports as applied (`applied_requests`); a `-revoked` suffix the CLI adds to a
    /// combined request counts for its base id too.
    public static func appliedIDs(status: [String: Any]) -> Set<String> {
        let ids = (status["applied_requests"] as? [String]) ?? []
        return Set(ids + ids.filter { $0.hasSuffix("-revoked") }.map { String($0.dropLast("-revoked".count)) })
    }
}

/// The argument lists of `python -m livetips_m0.evidence` (spec section 11.6). Lists go as one JSON array each, so a
/// path with a comma stays one item and an empty list is the full, empty list (an unhide of the last hidden call).
public enum EvidenceCommand {
    public struct Target: Equatable {
        public var uid: String
        public var root: String
        public var slug: String
        public var config: String           // config.json, where the engine reads the flag
        public init(uid: String, root: String, slug: String, config: String) {
            self.uid = uid; self.root = root; self.slug = slug; self.config = config
        }
    }

    public static func json(_ list: [String]) -> String {
        guard let d = try? JSONSerialization.data(withJSONObject: list, options: [.sortedKeys, .withoutEscapingSlashes]),
              let s = String(data: d, encoding: .utf8) else { return "[]" }
        return s
    }

    public static func base(_ cmd: String, _ t: Target) -> [String] {
        ["-m", "livetips_m0.evidence", cmd, "--project-uid", t.uid, "--root", t.root, "--slug", t.slug, "--config", t.config]
    }

    /// A pass: deterministic ingest now, LLM extraction once S3 lands. The full lists every time.
    public static func pass(_ t: Target, hidden: [String], revoked: [String], webDirs: [String], tracker: String?) -> [String] {
        var a = base("pass", t) + ["--hidden-calls", json(hidden), "--revoked", json(revoked), "--web-dirs", json(webDirs)]
        if let tr = tracker, !tr.isEmpty { a += ["--tracker", tr] }
        return a
    }

    /// The state-only pass for a hide or unhide (the full current list of hidden calls), at once, also during a call.
    public static func excludeHidden(_ t: Target, hidden: [String], requestID: String) -> [String] {
        base("exclude", t) + ["--hidden-calls", json(hidden), "--request-id", requestID]
    }

    /// The state-only pass for a document that lost access (the full current revoked list).
    public static func excludeRevoked(_ t: Target, revoked: [String], requestID: String) -> [String] {
        base("exclude", t) + ["--revoked", json(revoked), "--request-id", requestID]
    }

    /// One state-only pass with the current lists (nil leaves that list out), sent fresh after a pass or in place of
    /// stale `exclude` requests. Nil when both are nil.
    public static func exclude(_ t: Target, hidden: [String]?, revoked: [String]?, requestID: String) -> [String]? {
        guard hidden != nil || revoked != nil else { return nil }
        var a = base("exclude", t)
        if let h = hidden { a += ["--hidden-calls", json(h)] }
        if let r = revoked { a += ["--revoked", json(r)] }
        return a + ["--request-id", requestID]
    }

    /// The supersede click (spec section 7.1): the override only after `decision_notion.py --supersede` returned ok.
    public static func supersede(_ t: Target, notionResult: [String: Any], old: String, new: String, requestID: String,
                                 at: Date) -> [String]? {
        guard (notionResult["ok"] as? Bool) == true, !old.isEmpty, !new.isEmpty else { return nil }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return base("override", t) + ["--key", notionKey(old), "--field", "supersede", "--value", notionKey(new),
                                      "--at", f.string(from: at), "--via", "app", "--request-id", requestID]
    }

    public static func status(_ t: Target) -> [String] { base("status", t) }
    public static func delete(_ t: Target) -> [String] { base("delete", t) }

    /// "notion:<32 hex>" from a page id with or without dashes, or one already prefixed.
    public static func notionKey(_ id: String) -> String {
        let raw = id.hasPrefix("notion:") ? String(id.dropFirst("notion:".count)) : id
        return "notion:" + raw.replacingOccurrences(of: "-", with: "").lowercased()
    }

    /// The environment of a CLI run: the store under Application Support (never the work folder the app root may sit
    /// in), the state file for call-active.json and the Tracker snapshot, and the project uid.
    public static func env(base: [String: String], support: String, statePath: String, uid: String) -> [String: String] {
        var e = base
        e["LT_SUPPORT_DIR"] = support
        e["LT_AI_STATE"] = statePath
        e["LT_PROJECT_UID"] = uid
        e.removeValue(forKey: "LT_EVIDENCE")          // the flag comes from config.json only (LT_EVIDENCE is for tests)
        return e
    }
}

/// The lists passed to each pass (spec section 17 S5).
public enum EvidenceLists {
    /// Hidden calls of this project (history.json): their transcript file names; the engine reduces them to stems.
    public static func hiddenCalls(_ records: [(file: String, project: String, hidden: Bool)], slug: String, root: String) -> [String] {
        let r = ProjectRegistry.norm(root)
        return Array(Set(records.filter { rec in
            rec.hidden && (rec.project == slug || (!r.isEmpty && ProjectRegistry.norm(rec.file).hasPrefix(r + "/")))
        }.map { ($0.file as NSString).lastPathComponent })).sorted()
    }

    /// A Google file id from a Docs, Sheets, Slides or Drive link; nil for anything else.
    public static func googleID(_ uri: String) -> String? {
        guard let u = URLComponents(string: uri), let host = u.host, host.hasSuffix("google.com") else { return nil }
        let parts = u.path.split(separator: "/").map(String.init)
        if let i = parts.firstIndex(of: "d"), i + 1 < parts.count, !parts[i + 1].isEmpty { return parts[i + 1] }
        return u.queryItems?.first { $0.name == "id" }?.value.flatMap { $0.isEmpty ? nil : $0 }
    }

    /// A Notion page id (32 hex, no dashes) from a notion.so or notion.site link; nil for anything else.
    public static func notionID(_ uri: String) -> String? {
        guard let u = URLComponents(string: uri), let host = u.host, host.hasSuffix("notion.so") || host.hasSuffix("notion.site") else { return nil }
        let last = (u.path.split(separator: "/").last.map(String.init) ?? "").replacingOccurrences(of: "-", with: "")
        guard last.count >= 32 else { return nil }
        let id = String(last.suffix(32)).lowercased()
        return id.allSatisfy({ $0.isHexDigit }) ? id : nil
    }

    /// The project's revoked list after a Meeting Sheet reported its sources' access: a Google or Notion document
    /// that is `denied` or `not_found` joins it, one that reads `ok` again leaves it. Other sources change nothing.
    public static func revoked(current: [String], sources: [[String: Any]]) -> [String] {
        var out = Set(current)
        for s in sources {
            guard let uri = s["uri"] as? String, let id = googleID(uri) ?? notionID(uri) else { continue }
            switch (s["access"] as? String) ?? "" {
            case "denied", "not_found": out.insert(id)
            case "ok": out.remove(id)
            default: break
            }
        }
        return out.sorted()
    }

    /// After a pass: the lists to send again because they changed since the app built the pass. The engine keeps a
    /// request applied while the pass ran, but one applied after the app read the lists and before the pass applied
    /// them at its start is overwritten by the older lists (and counts as applied), so each list that differs now goes
    /// again as a fresh request; nil for a list that did not change.
    public static func resync(passedHidden: [String], passedRevoked: [String], hidden: [String], revoked: [String])
        -> (hidden: [String]?, revoked: [String]?) {
        (Set(passedHidden) == Set(hidden) ? nil : hidden.sorted(), Set(passedRevoked) == Set(revoked) ? nil : revoked.sorted())
    }

    /// Stored page folders of this project's sheets outside the project folder (`<sheet state>/web/<site>`).
    public static func webDirs(sources: [[String: Any]], root: String, webBase: String) -> [String] {
        let r = ProjectRegistry.norm(root), w = ProjectRegistry.norm(webBase)
        var out = Set<String>()
        for s in sources where ["web", "web_page"].contains((s["type"] as? String) ?? "") {
            guard let p = s["path"] as? String, !p.isEmpty else { continue }
            let n = ProjectRegistry.norm(p)
            guard !w.isEmpty, n.hasPrefix(w + "/"), r.isEmpty || !n.hasPrefix(r + "/") else { continue }
            let site = n.dropFirst(w.count + 1).split(separator: "/").first.map(String.init) ?? ""
            if !site.isEmpty { out.insert((w as NSString).appendingPathComponent(site)) }
        }
        return out.sorted()
    }
}

/// The Tracker snapshot file (`tracker-snapshot/<uid>.json` next to ai-routes.json, spec section 5.2 item 5): the
/// Notion and HubSpot items the app already holds, never a new request. The engine diffs it item by item.
public enum TrackerSnapshot {
    public struct Item: Equatable {
        public var id: String            // a Notion page id, or "crm:<portal>:<task id>" for a HubSpot task (crmID)
        public var text: String
        public var status: String
        public var owner: String
        public var due: String
        public var kind: String
        public var lastEdited: String    // the Notion page's last edit (the mirror header); "" for a HubSpot task
        public var project: String       // the project's slug
        public init(id: String, text: String, status: String = "", owner: String = "", due: String = "", kind: String = "task",
                    lastEdited: String = "", project: String = "") {
            self.id = id; self.text = text; self.status = status; self.owner = owner; self.due = due; self.kind = kind
            self.lastEdited = lastEdited; self.project = project
        }
    }

    /// A HubSpot task's id as the engine keys it (`identity.tracker_id`): task ids repeat across portals, so the portal
    /// is part of it; nil without a portal or a task.
    public static func crmID(portal: String, task: String) -> String? {
        portal.isEmpty || task.isEmpty ? nil : "crm:\(portal):\(task)"
    }

    /// The mirror's `last_edited` (Notion's ISO time, with or without fractions) as a plain UTC time the engine reads;
    /// "" for anything else, so a malformed header never stops a pass.
    public static func edited(_ raw: String) -> String {
        let s = raw.trimmingCharacters(in: CharacterSet(charactersIn: " \"'"))
        let plain = ISO8601DateFormatter(), frac = ISO8601DateFormatter()
        frac.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let d = plain.date(from: s) ?? frac.date(from: s) else { return "" }
        return plain.string(from: d)
    }

    /// A Tracker item's project (the mirror's Project value, or a register item's slug) is this project when it is
    /// its name, slug or Notion value, any case.
    public static func belongs(_ project: String, name: String, slug: String, notionValue: String) -> Bool {
        let p = project.trimmingCharacters(in: .whitespaces).lowercased()
        return !p.isEmpty && [name, slug, notionValue].contains { !$0.isEmpty && $0.trimmingCharacters(in: .whitespaces).lowercased() == p }
    }

    public static func path(statePath: String, uid: String) -> String {
        (((statePath as NSString).deletingLastPathComponent as NSString).appendingPathComponent("tracker-snapshot") as NSString)
            .appendingPathComponent(uid + ".json")
    }

    /// Sorted by id, so the same items give the same bytes and the engine sees no change.
    public static func json(_ items: [Item]) -> Data {
        let rows: [[String: Any]] = items.filter { !$0.id.isEmpty && !$0.text.isEmpty }.sorted { $0.id < $1.id }.map { i in
            var o: [String: Any] = ["id": i.id, "text": i.text, "kind": i.kind]
            if !i.status.isEmpty { o["status"] = i.status }
            if !i.owner.isEmpty { o["owner"] = i.owner }
            if !i.due.isEmpty { o["due"] = i.due }
            if !i.lastEdited.isEmpty { o["last_edited"] = i.lastEdited }
            if !i.project.isEmpty { o["project"] = i.project }
            return o
        }
        return (try? JSONSerialization.data(withJSONObject: ["items": rows], options: [.sortedKeys, .prettyPrinted])) ?? Data()
    }
}

/// When a pass may start (spec section 8.5): after a call's notes are written, 10 minutes after the last folder
/// change, at most once an hour while the Mac is idle and on power, and on "Index now". Never while a call runs.
public enum EvidenceSchedule {
    public enum Trigger: String { case afterNotes, folderChange, idleHourly, indexNow }
    public static let debounce: TimeInterval = 600
    public static let hourly: TimeInterval = 3600

    public static func shouldRun(_ t: Trigger, now: Date, lastPass: Date?, lastChange: Date?, callActive: Bool,
                                 idle: Bool, onPower: Bool, paused: Bool) -> Bool {
        if callActive { return false }
        if t == .indexNow { return true }
        if paused { return false }
        switch t {
        case .afterNotes: return true
        case .folderChange: return lastChange.map { now.timeIntervalSince($0) >= debounce } ?? false
        case .idleHourly: return idle && onPower && (lastPass.map { now.timeIntervalSince($0) >= hourly } ?? true)
        case .indexNow: return true
        }
    }
}

/// The passes waiting to start, one per project, one at a time. A queued pass never starts while a call runs (the
/// check at request time is not enough: a pass may wait behind another one into a call); it stays queued and starts
/// when the call ends.
public struct EvidenceQueue: Equatable {
    public struct Entry: Equatable {
        public var uid: String
        public var trigger: EvidenceSchedule.Trigger
        public init(uid: String, trigger: EvidenceSchedule.Trigger) { self.uid = uid; self.trigger = trigger }
    }
    public private(set) var entries: [Entry] = []
    public init() {}

    public mutating func add(_ uid: String, _ t: EvidenceSchedule.Trigger, running: Set<String>) {
        guard !entries.contains(where: { $0.uid == uid }), !running.contains(uid) else { return }
        entries.append(Entry(uid: uid, trigger: t))
    }

    /// The next pass to start, or nil (and the queue kept) while one runs or a call is active.
    public mutating func next(busy: Bool, callActive: Bool) -> Entry? {
        guard !busy, !callActive, !entries.isEmpty else { return nil }
        return entries.removeFirst()
    }
}

/// The state-only requests of each project (hide, unhide, revoke, supersede), one CLI process at a time per project,
/// so a newer hidden or revoked list never lands before an older one (the engine's lock keeps no waiting order; spec
/// section 13.1). An `exclude` waits as a marker of the lists it wants, not as arguments: queued excludes merge into
/// one, and its lists and request id are read when it starts, so it always carries the current lists.
public struct EvidenceStateQueue: Equatable {
    public enum Job: Equatable {
        case exclude(hidden: Bool, revoked: Bool)
        case args([String])                 // a supersede, or a stored override sent again, as it was
    }
    public private(set) var running: Set<String> = []
    public private(set) var waiting: [String: [Job]] = [:]
    public init() {}

    /// Queues a job; an `exclude` joins one already waiting (its lists are ORed), so it keeps that place.
    public mutating func add(_ uid: String, _ job: Job) {
        var w = waiting[uid] ?? []
        if case let .exclude(h, r) = job, let i = w.firstIndex(where: { if case .exclude = $0 { return true }; return false }),
           case let .exclude(h0, r0) = w[i] {
            w[i] = .exclude(hidden: h0 || h, revoked: r0 || r)
        } else if case .args = job, w.contains(job) {
            return
        } else {
            w.append(job)
        }
        waiting[uid] = w
    }

    /// The job to start now for this project, or nil while one of its jobs runs or none waits.
    public mutating func next(_ uid: String) -> Job? {
        guard !running.contains(uid), var w = waiting[uid], !w.isEmpty else { return nil }
        let j = w.removeFirst()
        waiting[uid] = w.isEmpty ? nil : w
        running.insert(uid)
        return j
    }

    /// The running job of this project ended (however it ended: exit, failed start, flag off).
    public mutating func finished(_ uid: String) { running.remove(uid) }
}

/// The settings of project memory (Settings > AI, only with the flag on): ceilings per pass, per project and day and
/// for all projects a day, in the unit of the route; topic summaries eager or lazy; AI reading on or off; pause.
public struct EvidencePrefs: Codable, Equatable {
    public var passUSD = 1.0, dayUSD = 3.0, allUSD = 3.0
    public var passPct = 10.0, dayPct = 30.0, allPct = 30.0
    public var passMin = 120.0, dayMin = 360.0, allMin = 360.0
    public var topicsEager = true
    public var useAI = true
    public var paused = false
    public var localOverflow = false
    public init() {}
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = EvidencePrefs()
        func v(_ k: CodingKeys, _ def: Double) -> Double { (try? c.decodeIfPresent(Double.self, forKey: k)) .flatMap { $0 } ?? def }
        func b(_ k: CodingKeys, _ def: Bool) -> Bool { (try? c.decodeIfPresent(Bool.self, forKey: k)).flatMap { $0 } ?? def }
        passUSD = v(.passUSD, d.passUSD); dayUSD = v(.dayUSD, d.dayUSD); allUSD = v(.allUSD, d.allUSD)
        passPct = v(.passPct, d.passPct); dayPct = v(.dayPct, d.dayPct); allPct = v(.allPct, d.allPct)
        passMin = v(.passMin, d.passMin); dayMin = v(.dayMin, d.dayMin); allMin = v(.allMin, d.allMin)
        topicsEager = b(.topicsEager, d.topicsEager); useAI = b(.useAI, d.useAI)
        paused = b(.paused, d.paused); localOverflow = b(.localOverflow, d.localOverflow)
    }

    /// The ceilings in the engine's terms (LT_EVIDENCE_LIMITS), read by the S3 worker.
    public var limits: [String: Any] {
        ["pass": ["usd": passUSD, "pct": passPct, "min": passMin], "day": ["usd": dayUSD, "pct": dayPct, "min": dayMin],
         "all_day": ["usd": allUSD, "pct": allPct, "min": allMin], "topics": topicsEager ? "eager" : "lazy",
         "extract": useAI, "local_overflow": localOverflow]
    }
}

/// The texts of section 10 as i18n keys with values, so the formatter is tested without the catalog. Cost numbers
/// appear only when the engine reports an estimate calibrated by S3 (spec section 17: S5 shows cost numbers only
/// after S3's calibration); until then every estimate line says it is not available yet.
public enum EvidenceText {
    public typealias Line = AIStatus.Line

    /// The unit of a route's ceiling and estimate: USD on keys, percent of the 5-hour limit on plans, minutes on this Mac.
    public enum Unit: String { case usd, pct, min }

    /// The company a route sends text to, for the consent line; "" for the model on this Mac.
    public static func provider(route: String) -> String {
        switch route {
        case "code", "api": return "Anthropic"
        case "codex", "openai": return "OpenAI"
        case "gemini": return "Google"
        default: return ""
        }
    }

    public static func unit(route: String) -> Unit {
        switch route {
        case "codex", "code": return .pct
        case "local": return .min
        default: return .usd
        }
    }

    /// An estimate from the engine: typical to cap, in the route's unit. Only a calibrated one is shown.
    public struct Estimate: Equatable {
        public var typical: Double
        public var cap: Double
        public var unit: Unit
        public var parts: Int
        public init(typical: Double, cap: Double, unit: Unit, parts: Int = 0) {
            self.typical = typical; self.cap = cap; self.unit = unit; self.parts = parts
        }

        /// From `evidence status` → `estimate` ({"typical", "cap", "unit", "parts", "calibrated"}); nil when absent or
        /// not calibrated yet.
        public static func parse(_ o: Any?) -> Estimate? {
            guard let d = o as? [String: Any], (d["calibrated"] as? Bool) == true,
                  let t = (d["typical"] as? NSNumber)?.doubleValue, let c = (d["cap"] as? NSNumber)?.doubleValue,
                  let u = Unit(rawValue: (d["unit"] as? String) ?? "") else { return nil }
            return Estimate(typical: min(t, c), cap: max(t, c), unit: u, parts: (d["parts"] as? NSNumber)?.intValue ?? 0)
        }
    }

    /// The `run` row of the last pass (spec section 6), from `evidence status` → `last_run`.
    public struct Run: Equatable {
        public var status: String
        public var parts: Int
        public var tokens: Int
        public var usd: Double?
        public var estUSD: Double?
        public var pctBefore: Double?
        public var pctAfter: Double?
        public var seconds: Double?
        public var ceilingKind: String
        public var ceilingValue: Double?
        public var error: String
        public init(status: String, parts: Int = 0, tokens: Int = 0, usd: Double? = nil, estUSD: Double? = nil, pctBefore: Double? = nil,
                    pctAfter: Double? = nil, seconds: Double? = nil, ceilingKind: String = "", ceilingValue: Double? = nil, error: String = "") {
            self.status = status; self.parts = parts; self.tokens = tokens; self.usd = usd; self.estUSD = estUSD
            self.pctBefore = pctBefore; self.pctAfter = pctAfter; self.seconds = seconds
            self.ceilingKind = ceilingKind; self.ceilingValue = ceilingValue; self.error = error
        }

        public static func parse(_ o: Any?) -> Run? {
            guard let d = o as? [String: Any], let st = d["status"] as? String else { return nil }
            func n(_ k: String) -> Double? { (d[k] as? NSNumber)?.doubleValue }
            let tokens = Int((n("in_tokens") ?? 0) + (n("cached_tokens") ?? 0) + (n("out_tokens") ?? 0))
            return Run(status: st, parts: Int(n("segments_done") ?? 0), tokens: tokens, usd: n("usd"), estUSD: n("est_usd"),
                       pctBefore: n("pct_before"), pctAfter: n("pct_after"), seconds: n("seconds"),
                       ceilingKind: (d["ceiling_kind"] as? String) ?? "", ceilingValue: n("ceiling_value"), error: (d["error"] as? String) ?? "")
        }
    }

    public static func money(_ v: Double) -> String { String(format: "$%.2f", v) }

    public static func tokens(_ n: Int) -> String {
        n >= 100_000 ? String(format: "%.1fM", Double(n) / 1e6) : n >= 1000 ? String(format: "%.0fK", Double(n) / 1e3) : "\(n)"   // "0.9M", spec 10
    }

    /// "$0.15 to $0.19", "5% to 6%", "30 to 120 minutes"; one number when both ends round the same.
    public static func range(_ e: Estimate) -> Line {
        switch e.unit {
        case .usd:
            let a = money(e.typical), b = money(e.cap)
            return a == b ? Line("mem.range.usd.one", ["v": a]) : Line("mem.range.usd", ["from": a, "to": b])
        case .pct:
            let a = String(format: "%.0f%%", e.typical), b = String(format: "%.0f%%", e.cap)
            return a == b ? Line("mem.range.pct.one", ["v": a]) : Line("mem.range.pct", ["from": a, "to": b])
        case .min:
            let a = String(format: "%.0f", e.typical), b = String(format: "%.0f", e.cap)
            return a == b ? Line("mem.range.min.one", ["v": a]) : Line("mem.range.min", ["from": a, "to": b])
        }
    }

    /// A line with a range inside it: the app renders `range` first and passes it as the variable "range".
    public struct Ranged: Equatable {
        public var line: Line
        public var range: Line?
        public init(_ line: Line, range: Line? = nil) { self.line = line; self.range = range }
    }

    /// The one plain line where the flag is turned on (owner decision 24.11, option b): project text goes to the
    /// chosen AI provider for indexing, and the estimated first-index cost (or that it is not available yet).
    public static func consent(provider: String, local: Bool, estimate: Estimate?) -> Ranged {
        if local { return Ranged(Line("mem.consent.local")) }
        guard let e = estimate else { return Ranged(Line("mem.consent.noEstimate", ["provider": provider])) }
        return Ranged(Line("mem.consent", ["provider": provider]), range: range(e))
    }

    /// Before the next pass: "Next pass: about N parts, about $X to $Y." or "Next pass: N parts. The cost estimate is
    /// not available yet."
    public static func before(pending: Int, estimate: Estimate?) -> Ranged {
        guard let e = estimate else { return Ranged(Line("mem.before.noEstimate", ["parts": "\(pending)"])) }
        return Ranged(Line("mem.before", ["parts": "\(e.parts > 0 ? e.parts : pending)"]), range: range(e))
    }

    /// After a pass, from its `run` row: what it did and cost against the estimate, or why it stopped.
    public static func after(_ r: Run) -> Line {
        switch r.status {
        case "paused_call": return Line("mem.after.pausedCall")
        case "off": return Line("mem.after.off")
        case "failed": return Line("mem.after.failed")
        case "ceiling":
            let unit = r.ceilingKind.contains("pct") ? Unit.pct : r.ceilingKind.contains("min") ? Unit.min : Unit.usd
            let v = r.ceilingValue.map { unit == .usd ? money($0) : unit == .pct ? String(format: "%.0f%%", $0) : String(format: "%.0f", $0) } ?? ""
            let day = r.ceilingKind.contains("day")
            return Line(day ? "mem.after.ceilingDay" : "mem.after.ceiling", ["v": v])
        default:
            var vars = ["parts": "\(r.parts)", "tokens": tokens(r.tokens)]
            if let u = r.usd {
                vars["cost"] = money(u)
                if let e = r.estUSD { vars["est"] = money(e); return Line("mem.after.doneEst", vars) }
                return Line("mem.after.doneCost", vars)
            }
            if let a = r.pctAfter, let b = r.pctBefore { vars["cost"] = String(format: "%.0f%%", max(0, a - b)); return Line("mem.after.donePct", vars) }
            return Line("mem.after.done", vars)
        }
    }
}
