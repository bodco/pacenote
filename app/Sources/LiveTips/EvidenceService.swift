import AppKit
import LiveTipsCore

/// PB-52 G2 (S5): project memory, the app side (docs/specs/PB-52-g2-evidence-store.md, section 17 S5). Runs
/// `python -m livetips_m0.evidence` for each project; the pure rules are LiveTipsCore/Evidence.swift. With the flag
/// `evidence_store` off nothing here starts a process, writes a file or shows a row; the one exception is removing a
/// `call-active.json` left over from a time the flag was on.
@MainActor
final class EvidenceService: ObservableObject {
    static let shared = EvidenceService()

    /// Per project uid: the last `evidence status`, and whether a pass of it runs now.
    @Published private(set) var status: [String: [String: Any]] = [:]
    @Published private(set) var running: Set<String> = []
    /// Stores of projects no longer in Pacenote, listed when Settings opens and after a delete (not in the view body).
    @Published private(set) var orphanUIDs: [String] = []
    private var timer: Timer?
    private var lastChange: [String: Date] = [:]
    private var changeWait: Set<String> = []
    private var lastPassAt: [String: Date] = [:]
    private var queue = EvidenceQueue()
    private var stateQueue = EvidenceStateQueue()
    private var busy = false

    /// The settings in memory (the Settings model keeps config.json and itself in step), so a folder event with the
    /// flag off costs no file read.
    var config: AppConfig { SettingsWindow.model.config }
    var on: Bool { EvidenceFlag.on(config) }

    // MARK: launch, calls

    /// At launch: a stale call-active.json goes (also with the flag off); with the flag on, every project gets its uid,
    /// requests not yet applied are sent again and the idle check starts.
    func start() {
        Self.cleanCallActive()
        NotificationCenter.default.addObserver(forName: CallEngine.callEnded, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated {
                Self.callEnded()
                EvidenceService.shared.drain()             // a pass queued during the call starts now (notes may never come)
            }
        }
        startWorker()
    }

    /// With the flag on, at launch (config.json is read once at launch and Settings can only turn the flag off): uids, requests not yet applied, the idle check. Every
    /// project with a store also gets one fresh `exclude` with its current hidden and revoked lists: a pass the app
    /// left running when it quit may have applied older lists, and nothing else would send them again.
    func startWorker() {
        guard on, timer == nil else { return }
        ensureUIDs()
        for p in projects() {
            refreshNotionRevoked(p)                       // Notion pages lost while the app was closed
            resendPending(p, all: true)
        }
        timer = Timer.scheduledTimer(withTimeInterval: 600, repeats: true) { _ in
            MainActor.assumeIsolated { EvidenceService.shared.tick() }
        }
    }

    nonisolated static var callActivePath: String { CallActive.path(statePath: AIRoute.statePath) }

    /// Written at call start (only with the flag on): the worker sends no request while it exists (spec section 8.5).
    nonisolated static func callStarted() {
        guard EvidenceFlag.on(AppConfig.load(from: AppPaths.config)) else { return }
        let path = callActivePath
        try? FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        try? CallActive.payload(.init(startedAt: Date(), pid: getpid())).write(to: URL(fileURLWithPath: path), options: .atomic)
    }

    /// Removed when the call ends (whatever the flag says now) and when the app quits.
    nonisolated static func callEnded() {
        try? FileManager.default.removeItem(atPath: callActivePath)
    }

    nonisolated static func cleanCallActive() {
        let path = callActivePath
        let d = FileManager.default.contents(atPath: path)
        if CallActive.shouldRemoveAtLaunch(d, now: Date(), ownPid: getpid(), pidAlive: writer(d.flatMap(CallActive.parse))) {
            try? FileManager.default.removeItem(atPath: path)
        }
    }

    nonisolated static var callActiveNow: Bool {
        let d = FileManager.default.contents(atPath: callActivePath)
        guard let d = d else { return false }
        let i = CallActive.parse(d)
        return !CallActive.isStale(i, now: Date(), pidAlive: writer(i))
    }

    /// The pid of the file is alive and started before the file was written (a pid reused after a reboot is not).
    nonisolated static func writer(_ i: CallActive.Info?) -> (Int32) -> Bool {
        { pid in
            guard kill(pid, 0) == 0 || errno == EPERM else { return false }
            return i.map { CallActive.writtenBy($0, processStart: CallActive.processStart(pid)) } ?? true
        }
    }

    // MARK: triggers

    /// After a call's notes are written (or failed to be written): a pass of its project.
    nonisolated static func notesWritten(file: String) {
        Task { @MainActor in
            let s = EvidenceService.shared
            guard s.on, let p = PyTool.project(of: file).project.map(s.current) else { return }
            s.request(p, .afterNotes)
        }
    }

    /// The folder watcher saw a change: a pass 10 minutes after the last change.
    func folderChanged(root: String) {
        guard on else { return }
        ensureUIDs()
        guard let p = projects().first(where: { ProjectRegistry.norm($0.root) == ProjectRegistry.norm(root) }), let uid = p.evidenceUid else { return }
        lastChange[uid] = Date()
        guard !changeWait.contains(uid) else { return }       // one wait per project; it moves with later changes
        changeWait.insert(uid)
        waitQuiet(p, uid)
    }

    private func waitQuiet(_ p: Project, _ uid: String) {
        let due = (lastChange[uid] ?? Date()).addingTimeInterval(EvidenceSchedule.debounce + 1)
        DispatchQueue.main.asyncAfter(deadline: .now() + max(1, due.timeIntervalSinceNow)) { [weak self] in
            MainActor.assumeIsolated {
                guard let self = self else { return }
                if let l = self.lastChange[uid], Date().timeIntervalSince(l) < EvidenceSchedule.debounce { self.waitQuiet(p, uid); return }
                self.changeWait.remove(uid)
                self.request(p, .folderChange)
            }
        }
    }

    func indexNow(_ p: Project) { request(p, .indexNow) }

    private func tick() {
        mirrorFinished()                      // a mirror the PM runs with their own job is picked up within 10 minutes
        guard on, Self.idleSeconds() >= 300, Self.onPower() else { return }
        ensureUIDs()
        for p in projects() { request(p, .idleHourly) }
    }

    private func request(_ p: Project, _ t: EvidenceSchedule.Trigger) {
        guard on, let uid = p.evidenceUid else { return }
        let prefs = config.evidencePrefs ?? EvidencePrefs()
        let last = lastPassAt[uid] ?? (status[uid]?["last_pass"] as? String).flatMap { ISO8601DateFormatter().date(from: $0) }
        guard EvidenceSchedule.shouldRun(t, now: Date(), lastPass: last, lastChange: lastChange[uid], callActive: Self.callActiveNow,
                                         idle: Self.idleSeconds() >= 300, onPower: Self.onPower(), paused: prefs.paused) else { return }
        queue.add(uid, t, running: running)
        drain()
    }

    /// One pass at a time across all projects (the engine also holds a global worker lock). A queued pass waits while
    /// a call runs and starts when it ends.
    func drain() {
        guard on, let next = queue.next(busy: busy, callActive: Self.callActiveNow) else { return }
        guard let p = projects().first(where: { $0.evidenceUid == next.uid }), let t = target(p) else { drain(); return }
        busy = true
        running.insert(next.uid)
        writeTrackerSnapshot(p)
        refreshNotionRevoked(p)                           // the pass carries the current revoked list
        let c = config
        let passed = (hidden: hiddenCalls(p), revoked: loadRequests(next.uid).revoked)
        let args = EvidenceCommand.pass(t, hidden: passed.hidden, revoked: passed.revoked, webDirs: webDirs(p),
                                        tracker: TrackerSnapshot.path(statePath: AIRoute.statePath, uid: next.uid))
        var env = AIRoute.env(job: AIChain.evidenceJob, config: c)
        if let d = try? JSONSerialization.data(withJSONObject: (c.evidencePrefs ?? EvidencePrefs()).limits),
           let s = String(data: d, encoding: .utf8) { env["LT_EVIDENCE_LIMITS"] = s }
        run(args, env: env, uid: next.uid, nice: true) { [weak self] o in
            guard let self = self else { return }
            self.running.remove(next.uid)
            // the hourly idle pass counts from here, except when the engine was busy (its worker lock held, e.g. by the
            // skill): then nothing ran and the next idle check tries again
            if EvidenceSchedule.countsAsPass(o) { self.lastPassAt[next.uid] = Date() }
            self.busy = false
            // A hide, unhide or revoke that landed between reading the lists here and the pass applying them was
            // overwritten by them: the lists that changed go again (spec section 13.1).
            let now = self.projects().first(where: { $0.evidenceUid == next.uid }) ?? p
            self.resendPending(now, passed: passed)
            self.drain()
        }
    }

    // MARK: state-only passes (at once, also during a call)

    /// A call hidden or shown again on Home: the full hidden list of its project, with a request id.
    func hiddenChanged(file: String) {
        guard on, let p = PyTool.project(of: file).project.map(current), target(p) != nil else { return }
        stateRequest(p, .exclude(hidden: true, revoked: false))
    }

    /// A Meeting Sheet reported the access of its sources: a Google or Notion document that turned denied or not found
    /// joins the project's revoked list (and one readable again leaves it).
    func sheetFinished(sheet: String?, root: String) {
        guard on, let sheet = sheet else { return }
        ensureUIDs()
        guard let p = projects().first(where: { ProjectRegistry.norm($0.root) == ProjectRegistry.norm(root) }),
              let uid = p.evidenceUid, target(p) != nil else { return }
        let json = sheet.hasSuffix(".md") ? String(sheet.dropLast(3)) + ".json" : sheet
        guard let d = FileManager.default.contents(atPath: json), let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
              let sources = o["sources"] as? [[String: Any]] else { return }
        var reqs = loadRequests(uid)
        let next = EvidenceLists.revoked(current: reqs.revoked, sources: sources)
        guard next != reqs.revoked else { return }
        reqs.revoked = next
        saveRequests(uid, reqs)
        stateRequest(p, .exclude(hidden: false, revoked: true))
    }

    /// The Notion mirror finished a run: a page it can no longer read joins its project's revoked list, one read again
    /// leaves it, and a project whose list changed gets the state-only pass at once (owner decision 24.11, option a).
    func mirrorFinished() {
        guard on else { return }
        ensureUIDs()
        for p in projects() where target(p) != nil && refreshNotionRevoked(p) {
            stateRequest(p, .exclude(hidden: false, revoked: true))
        }
    }

    /// The project's revoked list after reading the mirror (its `lost` map and this project's `_notion/` files);
    /// true when it changed. Local reads only. Also run before every pass and at launch, so a mirror the PM runs with
    /// their own job is picked up too.
    @discardableResult
    private func refreshNotionRevoked(_ p: Project) -> Bool {
        guard let uid = p.evidenceUid else { return false }
        let notionDir = ((p.root as NSString).expandingTildeInPath as NSString).appendingPathComponent("_notion")
        let ids = EvidenceLists.mirrorIDs(notionDir: notionDir)
        let state = (NotionMirrorService.dir(workRoot: config.workRoot) as NSString).appendingPathComponent("state/state.json")
        let lost = config.workRoot.isEmpty ? [] : EvidenceLists.mirrorLost(state: FileManager.default.contents(atPath: state))
        var reqs = loadRequests(uid)
        let next = EvidenceLists.notionRevoked(current: reqs.revoked, lost: lost, archived: ids.archived, live: ids.live)
        guard next != reqs.revoked else { return false }
        reqs.revoked = next
        saveRequests(uid, reqs)
        return true
    }

    /// The supersede click: the override only after `decision_notion.py --supersede` returned ok (spec section 7.1).
    func superseded(file: String, old: String, new: String, notionResult: [String: Any]) {
        guard on, let p = PyTool.project(of: file).project.map(current), let t = target(p),
              let args = EvidenceCommand.supersede(t, notionResult: notionResult, old: old, new: new,
                                                   requestID: UUID().uuidString.lowercased(), at: Date()) else { return }
        record(uid: p.evidenceUid, args)                 // kept at once, so a quit before it runs still re-sends it
        stateRequest(p, .args(args))
    }

    /// Queues a state-only request; one process at a time per project (EvidenceStateQueue).
    private func stateRequest(_ p: Project, _ job: EvidenceStateQueue.Job) {
        guard let uid = p.evidenceUid else { return }
        stateQueue.add(uid, job)
        pumpState(uid)
    }

    /// Starts the next state-only request of this project once the previous one has exited. An `exclude` reads the
    /// current lists and gets its request id only now.
    private func pumpState(_ uid: String) {
        guard let job = stateQueue.next(uid) else { return }
        guard let p = projects().first(where: { $0.evidenceUid == uid }), let t = target(p) else {
            stateQueue.finished(uid); pumpState(uid); return
        }
        let args: [String]?
        switch job {
        case let .exclude(h, r):
            args = EvidenceCommand.exclude(t, hidden: h ? hiddenCalls(p) : nil, revoked: r ? loadRequests(uid).revoked : nil,
                                           requestID: UUID().uuidString.lowercased())
            if let a = args { record(uid: uid, a) }
        case let .args(a):
            args = a
        }
        guard let a = args else { stateQueue.finished(uid); pumpState(uid); return }
        run(a, env: [:], uid: uid, nice: false) { [weak self] _ in
            guard let self = self else { return }
            self.stateQueue.finished(uid)
            self.refreshStatus(p) {}
            self.pumpState(uid)
        }
    }

    /// Keeps a sent request until `evidence status` lists it as applied.
    private func record(uid: String?, _ args: [String]) {
        guard let uid = uid, let i = args.firstIndex(of: "--request-id"), i + 1 < args.count else { return }
        var reqs = loadRequests(uid)
        reqs.add(.init(id: args[i + 1], args: args, sentAt: Date()))
        saveRequests(uid, reqs)
    }

    /// Overrides not listed as applied by `evidence status` are sent again (a re-sent applied id is a no-op). An
    /// unapplied hide, unhide or revoke is not replayed (an old list could land after a newer one): one fresh request
    /// with the current lists replaces them. After a pass (`passed`), each list that changed while it ran goes again;
    /// with `all` (at launch) both lists go again whenever the project has a store. Everything goes through the
    /// per-project queue.
    private func resendPending(_ p: Project, passed: (hidden: [String], revoked: [String])? = nil, all: Bool = false) {
        guard let uid = p.evidenceUid else { return }
        refreshStatus(p) { [weak self] in
            guard let self = self, self.target(p) != nil else { return }
            var reqs = self.loadRequests(uid)
            let applied = EvidenceRequests.appliedIDs(status: self.status[uid] ?? [:])
            reqs.applied(applied)
            var send = (hidden: false, revoked: false)
            if reqs.staleExcludes(applied: applied) { reqs.dropExcludes(); send = (true, true) }
            if all, FileManager.default.fileExists(atPath: self.storePath(uid)) { send = (true, true) }
            if let passed = passed {
                let r = EvidenceLists.resync(passedHidden: passed.hidden, passedRevoked: passed.revoked,
                                             hidden: self.hiddenCalls(p), revoked: reqs.revoked)
                send.hidden = send.hidden || r.hidden != nil
                send.revoked = send.revoked || r.revoked != nil
            }
            self.saveRequests(uid, reqs)
            for r in reqs.toResend(applied: applied) { self.stateRequest(p, .args(r.args)) }
            if send.hidden || send.revoked { self.stateRequest(p, .exclude(hidden: send.hidden, revoked: send.revoked)) }
        }
    }

    // MARK: status, delete

    func refreshStatus(_ p: Project, then: @escaping () -> Void = {}) {
        guard on, let uid = p.evidenceUid, let t = target(p) else { then(); return }
        guard FileManager.default.fileExists(atPath: storePath(uid)) else { status[uid] = [:]; then(); return }
        run(EvidenceCommand.status(t), env: [:], uid: uid, nice: false) { [weak self] o in
            self?.status[uid] = o
            then()
        }
    }

    func delete(_ p: Project) {
        guard on, let uid = p.evidenceUid, let t = target(p), !running.contains(uid) else { return }   // not under a running pass
        run(EvidenceCommand.delete(t), env: [:], uid: uid, nice: false) { [weak self] _ in
            guard let self = self else { return }
            self.status[uid] = [:]
            // The sent requests and the Tracker snapshot go with the store; the revoked list stays, so a document that
            // lost access is not indexed again as active.
            self.saveRequests(uid, self.loadRequests(uid).afterDelete())
            try? FileManager.default.removeItem(atPath: TrackerSnapshot.path(statePath: AIRoute.statePath, uid: uid))
        }
    }

    /// The files the app keeps next to a store: its request list and its Tracker snapshot (task text).
    private func removeAppFiles(_ uid: String) {
        try? FileManager.default.removeItem(atPath: requestsPath(uid))
        try? FileManager.default.removeItem(atPath: TrackerSnapshot.path(statePath: AIRoute.statePath, uid: uid))
    }

    /// Stores of projects that are no longer in Pacenote (spec section 13.4); deleted only after the PM confirms.
    /// Listed when Settings opens and after a delete, never in the view body.
    func refreshOrphans() {
        guard on else { if !orphanUIDs.isEmpty { orphanUIDs = [] }; return }
        let dir = (AppPaths.support.path as NSString).appendingPathComponent("evidence")
        let files = (try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []
        let next = EvidenceUID.orphans(storeFiles: files, registry: SettingsWindow.model.registry)
        if next != orphanUIDs { orphanUIDs = next }
    }

    func deleteOrphans() {
        let c = config
        for uid in orphanUIDs {
            let t = EvidenceCommand.Target(uid: uid, root: "", slug: "", config: AppPaths.config.path)
            run(EvidenceCommand.delete(t), env: [:], uid: uid, nice: false, engine: c.engine) { [weak self] _ in
                self?.removeAppFiles(uid)
                self?.refreshOrphans()
            }
        }
    }

    // MARK: plumbing

    /// The registry Settings keeps in memory and saves, so a uid minted here is never written over by its older copy.
    func projects() -> [Project] { SettingsWindow.model.registry.projects.filter { $0.status != "archived" && !$0.root.isEmpty } }

    /// Every project gets its uid once the flag is on (projects.json is not touched while it is off). The Settings
    /// model saves its registry when it changes.
    func ensureUIDs() {
        guard on else { return }
        let m = SettingsWindow.model
        var reg = m.registry
        if EvidenceUID.mint(&reg) { m.registry = reg }
    }

    /// A project read from projects.json (PyTool) as the registry in memory holds it, with its uid: a project added
    /// since launch gets one here, so its first hide or supersede is not dropped.
    func current(_ p: Project) -> Project {
        ensureUIDs()
        let r = ProjectRegistry.norm(p.root)
        return projects().first(where: { !r.isEmpty && ProjectRegistry.norm($0.root) == r })
            ?? projects().first(where: { !p.slug.isEmpty && $0.slug == p.slug }) ?? p
    }

    private func target(_ p: Project) -> EvidenceCommand.Target? {
        guard let uid = p.evidenceUid, !uid.isEmpty else { return nil }
        return .init(uid: uid, root: p.root, slug: p.slug, config: AppPaths.config.path)
    }

    private func storePath(_ uid: String) -> String {
        ((AppPaths.support.path as NSString).appendingPathComponent("evidence") as NSString).appendingPathComponent(uid + ".sqlite")
    }

    private func requestsPath(_ uid: String) -> String {
        ((AppPaths.support.path as NSString).appendingPathComponent("evidence") as NSString).appendingPathComponent(uid + ".app-requests.json")
    }

    private func loadRequests(_ uid: String) -> EvidenceRequests {
        guard let d = FileManager.default.contents(atPath: requestsPath(uid)) else { return EvidenceRequests() }
        let dec = JSONDecoder(); dec.dateDecodingStrategy = .iso8601
        return (try? dec.decode(EvidenceRequests.self, from: d)) ?? EvidenceRequests()
    }

    private func saveRequests(_ uid: String, _ r: EvidenceRequests) {
        let path = requestsPath(uid)
        try? FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        let enc = JSONEncoder(); enc.dateEncodingStrategy = .iso8601; enc.outputFormatting = [.sortedKeys]
        try? enc.encode(r).write(to: URL(fileURLWithPath: path), options: .atomic)
    }

    private func hiddenCalls(_ p: Project) -> [String] {
        EvidenceLists.hiddenCalls(CallHistory.shared.records.map { ($0.file, $0.project, $0.hidden) }, slug: p.slug, root: p.root)
    }

    /// Stored page folders of this project's sheets outside its folder (`<sheet state>/web/<site>`).
    private func webDirs(_ p: Project) -> [String] {
        let live = (p.root as NSString).appendingPathComponent("live")
        var sources: [[String: Any]] = []
        for f in CallLayout.prepFiles(live: live, suffix: ".sheet.json") {
            guard let d = FileManager.default.contents(atPath: f), let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
                  let s = o["sources"] as? [[String: Any]] else { continue }
            sources += s
        }
        return EvidenceLists.webDirs(sources: sources, root: p.root, webBase: (SheetCLI.stateDir as NSString).appendingPathComponent("web"))
    }

    /// The Tracker snapshot from the Notion and HubSpot items the app already holds (no new request: the Notion items
    /// are the mirror the Tracker read, the HubSpot ones the list HubSpotTasks last synced). The engine reads a present
    /// file as the full list, so nothing is written before both lists have loaded (an empty list would delete every
    /// Tracker item); the older file stays meanwhile. Tasks only: the engine makes every snapshot item a task.
    private func writeTrackerSnapshot(_ p: Project) {
        guard let uid = p.evidenceUid else { return }
        let tm = TrackerModel.shared, hs = HubSpotTasks.shared
        guard !tm.loading, !tm.items.isEmpty else { if !tm.loading { tm.reload() }; return }
        guard !hs.ready || hs.syncedAt != nil else { return }
        let notion = tm.items.filter {
            $0.kind == .task && !$0.local && $0.orgItem == nil && $0.crmTask.isEmpty
                && TrackerSnapshot.belongs($0.project, name: p.name, slug: p.slug, notionValue: p.notionValue)
        }.map {
            TrackerSnapshot.Item(id: $0.id, text: $0.title, status: $0.status, owner: $0.owner, due: $0.due, kind: $0.kind.rawValue,
                                 lastEdited: TrackerSnapshot.edited($0.lastEdited), project: p.slug)
        }
        let items = notion + hs.snapshotItems(slug: p.slug)
        let path = TrackerSnapshot.path(statePath: AIRoute.statePath, uid: uid)
        try? FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        let data = TrackerSnapshot.json(items)
        if FileManager.default.contents(atPath: path) != data { try? data.write(to: URL(fileURLWithPath: path), options: .atomic) }
    }

    /// One CLI run off the main thread; the last JSON line of its output comes back on the main thread.
    private func run(_ args: [String], env extra: [String: String], uid: String, nice: Bool, engine e0: AppConfig.Engine? = nil,
                     done: @escaping @MainActor ([String: Any]) -> Void) {
        let e = e0 ?? config.engine
        guard on, !e.sidecarPython.isEmpty, !e.sidecarDir.isEmpty else { done([:]); return }
        let base = extra.isEmpty ? ProcessInfo.processInfo.environment : extra
        var env = EvidenceCommand.env(base: base, support: AppPaths.support.path, statePath: AIRoute.statePath, uid: uid)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: nice ? "/usr/bin/nice" : e.sidecarPython)
        p.arguments = (nice ? ["-n", "10", e.sidecarPython] : []) + args
        p.currentDirectoryURL = URL(fileURLWithPath: e.sidecarDir)
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.forAppending(path: (config.effectiveAppRoot(appSupport: AppPaths.support.path) as NSString)
            .appendingPathComponent("logs/evidence.log"))
        do { try p.run() } catch { done([:]); return }
        // The output is read while the process runs, so a long one (a status with many request ids) never fills the
        // pipe and blocks the process.
        DispatchQueue.global(qos: .utility).async {
            let data = out.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            let line = String(data: data, encoding: .utf8)?.split(separator: "\n").last.map(String.init) ?? ""
            let o = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any] ?? [:]
            DispatchQueue.main.async { MainActor.assumeIsolated { done(o) } }
        }
    }

    nonisolated static func idleSeconds() -> Double {
        CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: CGEventType(rawValue: ~0)!)
    }

    nonisolated static func onPower() -> Bool { IndexService.onACPower() }
}
