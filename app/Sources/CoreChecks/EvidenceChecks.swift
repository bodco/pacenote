import Foundation
import LiveTipsCore

/// PB-88 (PB-52 G2 S5): the app side of project memory, one check group per acceptance line of spec section 17 S5.
func evidenceS5PB88() {
    // 1. Flag off: nothing in config.json or projects.json changes, the setting is hidden, the AI picker keeps three jobs
    var cfg = AppConfig()
    expect(!EvidenceFlag.on(cfg), "S5: the flag is off by default")
    let cfgJSON = String(data: (try? JSONEncoder().encode(cfg)) ?? Data(), encoding: .utf8) ?? ""
    expect(!cfgJSON.contains("\"features\"") && !cfgJSON.contains("\"evidence\"") && !cfgJSON.contains("evidencePrefs"), "S5: flag off: config.json gets no features key and no evidence route")
    let p0 = Project(name: "Northpoint", slug: "northpoint", root: "/w/Northpoint")
    let pJSON = String(data: (try? JSONEncoder().encode(p0)) ?? Data(), encoding: .utf8) ?? ""
    expect(!pJSON.contains("evidence_uid"), "S5: flag off: projects.json gets no evidence_uid")
    if let d = "{\"name\": \"A\", \"evidence_uid\": \"\"}".data(using: .utf8), let p = try? JSONDecoder().decode(Project.self, from: d) {
        expect(p.evidenceUid == nil, "S5: an empty uid reads as none")
    } else { expect(false, "S5: a project with an empty uid decodes") }
    expect(AIChain.jobs == ["tips", "prep", "after"], "S5: the job list Settings and status iterate stays three jobs")
    EvidenceFlag.set(&cfg, on: true)
    expect(EvidenceFlag.on(cfg) && cfg.features == ["evidence_store": true], "S5: the flag on is features.evidence_store")
    if let d = try? JSONEncoder().encode(cfg), let back = try? JSONDecoder().decode(AppConfig.self, from: d) {
        expect(EvidenceFlag.on(back), "S5: the flag survives a save and load")
    } else { expect(false, "S5: config round trip") }
    EvidenceFlag.set(&cfg, on: false)
    expect(!EvidenceFlag.on(cfg), "S5: the off switch turns the flag off")

    // 2. Project uid: rename and folder change keep it; import by slug keeps it; a changed slug or a re-added folder
    // gets a new one and the old store is listed as orphaned
    var reg = ProjectRegistry()
    reg.projects = [Project(name: "Northpoint", slug: "northpoint", root: "/w/Northpoint"), Project(name: "Globex", slug: "globex", root: "/w/Globex")]
    var n = 0
    let maker = { () -> String in n += 1; return "uid-\(n)" }
    expect(EvidenceUID.mint(&reg, make: maker) && reg.projects.map { $0.evidenceUid } == ["uid-1", "uid-2"], "S5: minted once per project")
    expect(!EvidenceUID.mint(&reg, make: maker), "S5: a second mint changes nothing")
    reg.projects[0].name = "Northpoint Pilot"; reg.projects[0].slug = "northpoint-pilot"; reg.projects[0].root = "/w/Moved/Northpoint"
    expect(reg.projects[0].evidenceUid == "uid-1", "S5: rename and folder change in the app keep the uid")
    var imp = Project(name: "Globex", slug: "globex", root: "/w/Globex"); imp.config = "/ct/globex.md"
    reg.merge(imported: [imp])
    expect(reg.projects.first { $0.slug == "globex" }?.evidenceUid == "uid-2", "S5: merge(imported:) keeps the uid of the project it matches by slug")
    reg.merge(imported: [Project(name: "Globex Inc", slug: "globex-inc", root: "/w/GlobexInc")])
    EvidenceUID.mint(&reg, make: maker)
    let newUid = reg.projects.first { $0.slug == "globex-inc" }?.evidenceUid
    expect(newUid == "uid-3", "S5: an import whose slug changed is a new project with a new uid")
    reg.projects.removeAll { $0.slug == "globex" }
    reg.merge(folders: [Project(name: "Globex", slug: "globex", root: "/w/Globex")])
    EvidenceUID.mint(&reg, make: maker)
    expect(reg.projects.first { $0.slug == "globex" }?.evidenceUid == "uid-4", "S5: a folder removed and added again gets a new uid")
    let files = ["/s/evidence/uid-1.sqlite", "/s/evidence/uid-2.sqlite", "/s/evidence/uid-2.sqlite-wal", "/s/evidence/uid-9.corrupt-20261124.sqlite",
                 "/s/evidence/uid-3.sqlite", "/s/evidence/uid-4.sqlite", "/s/evidence/worker.lock"]
    expect(EvidenceUID.orphans(storeFiles: files, registry: reg) == ["uid-2"], "S5: the old store is listed as orphaned")
    var dup = ProjectRegistry()
    var withUid = Project(name: "Acme", slug: "acme", root: "/w/Acme"); withUid.evidenceUid = "uid-a"
    var withCfg = Project(name: "Acme", slug: "acme-2", root: "/w/Acme/"); withCfg.config = "/ct/acme.md"
    dup.projects = [withUid, withCfg]
    dup.dedupe()
    expect(dup.projects.count == 1 && dup.projects[0].evidenceUid == "uid-a", "S5: one folder twice keeps the uid of either entry")

    // 3. call-active.json for the whole call; a stale one is removed at the next launch
    let state = "/app/state/ai-routes.json"
    expect(CallActive.path(statePath: state) == "/app/state/call-active.json", "S5: call-active.json sits next to ai-routes.json")
    let t0 = Date(timeIntervalSince1970: 1_795_000_000)
    let info = CallActive.Info(startedAt: t0, pid: 4242)
    expect(CallActive.parse(CallActive.payload(info)) == info, "S5: started_at and pid round trip")
    let alive: (Int32) -> Bool = { $0 == 4242 }
    expect(!CallActive.isStale(info, now: t0.addingTimeInterval(3600), pidAlive: alive), "S5: a running call is not stale")
    expect(CallActive.isStale(info, now: t0.addingTimeInterval(6 * 3600 + 1), pidAlive: alive), "S5: older than 6 hours is stale")
    expect(CallActive.isStale(info, now: t0.addingTimeInterval(60), pidAlive: { _ in false }), "S5: a dead pid is stale")
    expect(CallActive.shouldRemoveAtLaunch(CallActive.payload(info), now: t0.addingTimeInterval(60), ownPid: 7, pidAlive: { _ in false }),
           "S5: after a crash the next launch removes the file")
    expect(!CallActive.shouldRemoveAtLaunch(CallActive.payload(info), now: t0.addingTimeInterval(60), ownPid: 7, pidAlive: alive),
           "S5: a file of another live process stays")
    expect(CallActive.shouldRemoveAtLaunch(Data("garbage".utf8), now: t0, ownPid: 7, pidAlive: alive), "S5: an unreadable file is removed")
    expect(!CallActive.shouldRemoveAtLaunch(nil, now: t0, ownPid: 7, pidAlive: alive), "S5: no file, nothing to do")

    // 4. Hide on Home: the state-only pass with a request id, re-sent until listed as applied
    let tg = EvidenceCommand.Target(uid: "uid-1", root: "/w/Northpoint", slug: "northpoint", config: "/s/config.json")
    let recs: [(file: String, project: String, hidden: Bool)] = [
        ("/w/Northpoint/live/2026-11-10-acme-weekly/2026-11-10-1000-acme-weekly.md", "northpoint", true),
        ("/w/Northpoint/live/2026-11-12-1000-sync.md", "", true),
        ("/w/Globex/live/2026-11-12-1100-globex.md", "globex", true),
        ("/w/Northpoint/live/2026-11-13-1000-open.md", "northpoint", false)]
    let hidden = EvidenceLists.hiddenCalls(recs, slug: "northpoint", root: "/w/Northpoint")
    expect(hidden == ["2026-11-10-1000-acme-weekly.md", "2026-11-12-1000-sync.md"], "S5: hidden calls of this project only: \(hidden)")
    let hide = EvidenceCommand.excludeHidden(tg, hidden: hidden, requestID: "r-1")
    expect(Array(hide.prefix(3)) == ["-m", "livetips_m0.evidence", "exclude"] && hide.contains("--request-id") && hide.last == "r-1",
           "S5: hide runs exclude with a request id")
    expect(hide.contains("--config") && hide[hide.firstIndex(of: "--project-uid")! + 1] == "uid-1", "S5: the uid and the config with the flag go along")
    let unhide = EvidenceCommand.excludeHidden(tg, hidden: [], requestID: "r-2")
    expect(unhide[unhide.firstIndex(of: "--hidden-calls")! + 1] == "[]", "S5: unhiding the last call sends the full, empty list")
    var reqs = EvidenceRequests()
    reqs.add(.init(id: "r-1", args: hide, sentAt: t0)); reqs.add(.init(id: "r-1", args: hide, sentAt: t0)); reqs.add(.init(id: "r-2", args: unhide, sentAt: t0))
    expect(reqs.pending.count == 2, "S5: one request per id")
    let applied = EvidenceRequests.appliedIDs(status: ["applied_requests": ["r-2"]])
    expect(reqs.toResend(applied: applied).isEmpty && reqs.staleExcludes(applied: applied),
           "S5 (PB-88 r2): an unapplied hide is not replayed as stored; the current list goes fresh instead")
    reqs.applied(applied)
    expect(reqs.pending.map(\.id) == ["r-1"], "S5: applied ids are dropped")
    expect(EvidenceRequests.appliedIDs(status: ["applied_requests": ["r-3-revoked"]]).contains("r-3"), "S5: a combined request counts by its base id")
    if let d = try? JSONEncoder().encode(reqs), let back = try? JSONDecoder().decode(EvidenceRequests.self, from: d) {
        expect(back == reqs, "S5: the tracker survives a restart")
    } else { expect(false, "S5: request tracker round trip") }

    // PB-88 review round 2. A stale "hide A" (unapplied) must never land after the newer, applied "hide A and B":
    // excludes are dropped and replaced by one fresh request; an unapplied override still replays as it was.
    var mixed = EvidenceRequests()
    let ov = EvidenceCommand.supersede(tg, notionResult: ["ok": true], old: "a", new: "b", requestID: "r-ov", at: t0) ?? []
    mixed.add(.init(id: "r-old", args: EvidenceCommand.excludeHidden(tg, hidden: ["a.md"], requestID: "r-old"), sentAt: t0))
    mixed.add(.init(id: "r-ov", args: ov, sentAt: t0))
    let appliedNew: Set<String> = ["r-new"]
    expect(mixed.toResend(applied: appliedNew).map(\.id) == ["r-ov"], "S5 r2: only the override replays verbatim")
    expect(mixed.staleExcludes(applied: appliedNew), "S5 r2: an unapplied exclude asks for a fresh one")
    mixed.dropExcludes()
    expect(mixed.pending.map(\.id) == ["r-ov"] && !mixed.staleExcludes(applied: []), "S5 r2: the stale exclude is dropped")
    let fresh = EvidenceCommand.exclude(tg, hidden: ["a.md", "b.md"], revoked: ["doc1"], requestID: "r-f") ?? []
    expect(fresh[fresh.firstIndex(of: "--hidden-calls")! + 1] == "[\"a.md\",\"b.md\"]" && fresh[fresh.firstIndex(of: "--revoked")! + 1] == "[\"doc1\"]"
           && fresh.last == "r-f" && EvidenceRequests.isExclude(.init(id: "r-f", args: fresh, sentAt: t0)), "S5 r2: one fresh exclude with both current lists")
    expect(EvidenceCommand.exclude(tg, hidden: nil, revoked: nil, requestID: "r-x") == nil, "S5 r2: nothing changed, nothing sent")
    let onlyRev = EvidenceCommand.exclude(tg, hidden: nil, revoked: [], requestID: "r-y") ?? []
    expect(!onlyRev.contains("--hidden-calls") && onlyRev[onlyRev.firstIndex(of: "--revoked")! + 1] == "[]", "S5 r2: a list left out stays as it is")

    // PB-88 r2/r3: a hide that landed before the pass applied its (older) lists is overwritten by them; the changed
    // list goes again after the pass, an unchanged one does not.
    let rs = EvidenceLists.resync(passedHidden: ["a.md"], passedRevoked: ["doc1"], hidden: ["b.md", "a.md"], revoked: ["doc1"])
    expect(rs.hidden == ["a.md", "b.md"] && rs.revoked == nil, "S5 r2: a hide during the pass is sent again after it: \(rs)")
    let rs2 = EvidenceLists.resync(passedHidden: ["a.md"], passedRevoked: [], hidden: [], revoked: ["doc2"])
    expect(rs2.hidden == [] && rs2.revoked == ["doc2"], "S5 r2: an unhide of the last call and a revoke during the pass go again")
    let rs3 = EvidenceLists.resync(passedHidden: ["b.md", "a.md"], passedRevoked: ["doc1"], hidden: ["a.md", "b.md"], revoked: ["doc1"])
    expect(rs3.hidden == nil && rs3.revoked == nil, "S5 r2: nothing changed during the pass, nothing sent")

    // PB-88 r2: deleting a project's memory keeps its revoked list (else the next index reads the document as active)
    var withRev = EvidenceRequests()
    withRev.revoked = ["doc1"]
    withRev.add(.init(id: "r-9", args: ov, sentAt: t0))
    let afterDel = withRev.afterDelete()
    expect(afterDel.revoked == ["doc1"] && afterDel.pending.isEmpty, "S5 r2: delete keeps the revoked list, drops the sent requests")

    // PB-88 r2: a queued pass never starts during a call; it stays queued and starts after the call
    var q = EvidenceQueue()
    q.add("uid-1", .afterNotes, running: [])
    q.add("uid-1", .indexNow, running: [])
    q.add("uid-2", .indexNow, running: ["uid-2"])
    expect(q.entries.count == 1, "S5 r2: one queued pass per project, none for a project that runs")
    expect(q.next(busy: false, callActive: true) == nil && q.entries.count == 1, "S5 r2: a call started while the pass waited: it stays queued")
    expect(q.next(busy: true, callActive: false) == nil && q.entries.count == 1, "S5 r2: one pass at a time")
    expect(q.next(busy: false, callActive: false)?.uid == "uid-1" && q.entries.isEmpty, "S5 r2: after the call the pass starts")

    // PB-88 r3: state-only requests run one at a time per project, in the order they came; a hide that comes while
    // another hide runs waits, and queued excludes merge into one (its lists are read when it starts, so it carries
    // the newest lists: "hide A" then "hide A and B" can never end as [A]).
    var sq = EvidenceStateQueue()
    sq.add("uid-1", .exclude(hidden: true, revoked: false))                       // hide A
    expect(sq.next("uid-1") == .exclude(hidden: true, revoked: false), "S5 r3: the first hide starts at once")
    sq.add("uid-1", .exclude(hidden: true, revoked: false))                       // hide B while A runs
    expect(sq.next("uid-1") == nil, "S5 r3: a second request of the same project waits for the first to exit")
    sq.add("uid-1", .args(ov))                                                     // a supersede
    sq.add("uid-1", .exclude(hidden: false, revoked: true))                       // a revoke: joins the waiting exclude
    sq.add("uid-1", .args(ov))                                                     // the same override again: one
    expect(sq.waiting["uid-1"] == [.exclude(hidden: true, revoked: true), .args(ov)], "S5 r3: queued excludes merge, in place: \(sq.waiting)")
    sq.add("uid-2", .exclude(hidden: true, revoked: false))
    expect(sq.next("uid-2") == .exclude(hidden: true, revoked: false), "S5 r3: another project does not wait")
    sq.finished("uid-1")
    expect(sq.next("uid-1") == .exclude(hidden: true, revoked: true), "S5 r3: after the first exits, one exclude with both lists")
    expect(sq.next("uid-1") == nil, "S5 r3: still one at a time")
    sq.finished("uid-1")
    expect(sq.next("uid-1") == .args(ov) && sq.waiting["uid-1"] == nil, "S5 r3: then the override, and the queue is empty")
    sq.finished("uid-1")
    expect(sq.next("uid-1") == nil && !sq.running.contains("uid-1"), "S5 r3: nothing left to start")

    // PB-88 r2: a pid reused after a reboot (its process started after the file) does not keep call-active.json
    expect(CallActive.writtenBy(info, processStart: t0.addingTimeInterval(-120)), "S5 r2: the app that wrote the file started before it")
    expect(!CallActive.writtenBy(info, processStart: t0.addingTimeInterval(3600)), "S5 r2: a later process with the same pid did not write it")
    expect(CallActive.writtenBy(info, processStart: nil), "S5 r2: an unknown start time trusts the pid")
    // the real start time of this process: known, in the past, and a file it writes now counts as its own
    let ownStart = CallActive.processStart(getpid())
    expect(ownStart != nil && ownStart! <= Date() && ownStart! > Date().addingTimeInterval(-86400),
           "S5 r2: the start time of this process reads: \(String(describing: ownStart))")
    expect(CallActive.writtenBy(CallActive.Info(startedAt: Date(), pid: getpid()), processStart: CallActive.processStart(getpid())),
           "S5 r2: the running app's own call-active.json is not stale")

    // supersede: the override only after Notion returned ok
    expect(EvidenceCommand.supersede(tg, notionResult: ["ok": false, "error": "403"], old: "a", new: "b", requestID: "r-4", at: t0) == nil,
           "S5: Notion failed: no override")
    expect(EvidenceCommand.supersede(tg, notionResult: [:], old: "a", new: "b", requestID: "r-4", at: t0) == nil, "S5: no answer: no override")
    let old = "01234567-89abcdef-01234567-89abcdef", new = "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
    if let sup = EvidenceCommand.supersede(tg, notionResult: ["ok": true], old: old, new: new, requestID: "r-4", at: t0) {
        func arg(_ k: String) -> String? { sup.firstIndex(of: k).map { sup[$0 + 1] } }
        expect(sup[2] == "override" && arg("--key") == "notion:0123456789abcdef0123456789abcdef" && arg("--field") == "supersede"
               && arg("--value") == "notion:ffffffffffffffffffffffffffffffff" && arg("--request-id") == "r-4" && arg("--at") != nil,
               "S5: the override names both pages as notion keys: \(sup)")
    } else { expect(false, "S5: Notion ok: the override is built") }

    // a Google document turning denied goes to --revoked; one readable again leaves the list
    let srcs: [[String: Any]] = [["uri": "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit", "access": "denied"],
                                 ["uri": "https://www.notion.so/team/SSO-plan-0123456789abcdef0123456789abcdef", "access": "not_found"],
                                 ["uri": "https://drive.google.com/open?id=1ZzZz", "access": "ok"],
                                 ["uri": "https://northwindlabs.example/about", "access": "denied"]]
    let rv = EvidenceLists.revoked(current: ["1ZzZz", "keep-me"], sources: srcs)
    expect(rv == ["0123456789abcdef0123456789abcdef", "1AbCdEfGhIjKlMnOpQrStUv", "keep-me"], "S5: revoked list: \(rv)")
    let rvArgs = EvidenceCommand.excludeRevoked(tg, revoked: rv, requestID: "r-5")
    expect(rvArgs.contains("--revoked") && !rvArgs.contains("--hidden-calls"), "S5: a lost document runs exclude --revoked")
    expect(EvidenceLists.googleID("https://docs.google.com/spreadsheets/d/abc123/edit#gid=0") == "abc123"
           && EvidenceLists.googleID("https://example.com/d/abc") == nil, "S5: Google ids only from Google links")

    // stored pages outside the project folder
    let web = EvidenceLists.webDirs(sources: [["type": "web", "path": "/app/state/sheet/web/northwindlabs.com/index.md"],
                                              ["type": "web_page", "path": "/app/state/sheet/web/northwindlabs.com/about.md"],
                                              ["type": "web", "path": "/w/Northpoint/live/x/.work/web/a.com/i.md"],
                                              ["type": "gdoc", "path": "/app/state/sheet/web/x/y.md"]],
                                    root: "/w/Northpoint", webBase: "/app/state/sheet/web")
    expect(web == ["/app/state/sheet/web/northwindlabs.com"], "S5: web dirs: \(web)")

    // the Tracker snapshot only from items the app holds, stable bytes
    // a HubSpot task is keyed with its portal, as identity.tracker_id keys a register item linked to it
    expect(TrackerSnapshot.crmID(portal: "111", task: "9001") == "crm:111:9001", "S5: a HubSpot task is crm:<portal>:<task>")
    expect(TrackerSnapshot.crmID(portal: "", task: "9001") == nil && TrackerSnapshot.crmID(portal: "111", task: "") == nil,
           "S5: no portal or no task, no snapshot item")
    expect(TrackerSnapshot.belongs("Northpoint", name: "Northpoint", slug: "northpoint", notionValue: "")
           && TrackerSnapshot.belongs("northpoint", name: "Northpoint", slug: "northpoint", notionValue: "")
           && TrackerSnapshot.belongs("Northpoint Labs", name: "Northpoint", slug: "northpoint", notionValue: "Northpoint Labs")
           && !TrackerSnapshot.belongs("Globex", name: "Northpoint", slug: "northpoint", notionValue: "")
           && !TrackerSnapshot.belongs("", name: "Northpoint", slug: "northpoint", notionValue: ""),
           "S5: a Tracker item belongs by the project's name, slug or Notion value")
    expect(TrackerSnapshot.edited("2026-11-20T10:15:00.000Z") == "2026-11-20T10:15:00Z"
           && TrackerSnapshot.edited("\"2026-11-20T12:15:00+02:00\"") == "2026-11-20T10:15:00Z"
           && TrackerSnapshot.edited("yesterday") == "" && TrackerSnapshot.edited("") == "",
           "S5: last_edited only as a UTC time the engine reads")
    let items = [TrackerSnapshot.Item(id: TrackerSnapshot.crmID(portal: "111", task: "9001") ?? "", text: "Send the revised SOW",
                                      status: "NOT_STARTED", owner: "Mike", project: "northpoint"),
                 TrackerSnapshot.Item(id: "1234abcd", text: "Plover checklist", status: "Done", due: "2026-11-30",
                                      lastEdited: "2026-11-20T10:15:00Z", project: "northpoint"),
                 TrackerSnapshot.Item(id: "", text: "no id"), TrackerSnapshot.Item(id: "x", text: "")]
    let snap = TrackerSnapshot.json(items)
    expect(snap == TrackerSnapshot.json(items.reversed()), "S5: the snapshot is the same bytes for the same items")
    if let o = (try? JSONSerialization.jsonObject(with: snap)) as? [String: Any], let rows = o["items"] as? [[String: Any]] {
        expect(rows.map { $0["id"] as? String ?? "" } == ["1234abcd", "crm:111:9001"] && rows[1]["owner"] as? String == "Mike"
               && rows[0]["due"] as? String == "2026-11-30", "S5: items with id and text, in the engine's keys")
        expect(rows[0]["last_edited"] as? String == "2026-11-20T10:15:00Z" && rows[1]["last_edited"] == nil
               && rows.allSatisfy { $0["project"] as? String == "northpoint" }, "S5: items carry last_edited and project (spec 5.2 item 5)")
    } else { expect(false, "S5: the snapshot parses") }
    expect(TrackerSnapshot.path(statePath: state, uid: "uid-1") == "/app/state/tracker-snapshot/uid-1.json", "S5: the snapshot path")
    let env = EvidenceCommand.env(base: ["PATH": "/usr/bin", "LT_EVIDENCE": "1"], support: "/Users/you/Library/Application Support/LiveTips",
                                  statePath: state, uid: "uid-1")
    expect(env["LT_SUPPORT_DIR"] == "/Users/you/Library/Application Support/LiveTips" && env["LT_AI_STATE"] == state
           && env["LT_PROJECT_UID"] == "uid-1" && env["LT_EVIDENCE"] == nil, "S5: the store under Application Support, the flag only from config")
    let pass = EvidenceCommand.pass(tg, hidden: hidden, revoked: rv, webDirs: web, tracker: "/app/state/tracker-snapshot/uid-1.json")
    expect(pass[2] == "pass" && ["--hidden-calls", "--revoked", "--web-dirs", "--tracker", "--slug"].allSatisfy(pass.contains), "S5: a pass gets every list")

    // schedule: never during a call; after notes; folder change debounced 10 minutes; idle hourly on power; Index now
    func run(_ t: EvidenceSchedule.Trigger, last: Date? = nil, change: Date? = nil, call: Bool = false, idle: Bool = true, power: Bool = true,
             paused: Bool = false, now: Date = t0) -> Bool {
        EvidenceSchedule.shouldRun(t, now: now, lastPass: last, lastChange: change, callActive: call, idle: idle, onPower: power, paused: paused)
    }
    expect(!run(.indexNow, call: true) && !run(.afterNotes, call: true), "S5: no pass while a call runs")
    expect(run(.afterNotes) && run(.indexNow, paused: true) && !run(.afterNotes, paused: true), "S5: Pause stops the automatic passes only")
    expect(!run(.folderChange, change: t0.addingTimeInterval(-300)) && run(.folderChange, change: t0.addingTimeInterval(-600)), "S5: folder change after 10 quiet minutes")
    expect(run(.idleHourly, last: t0.addingTimeInterval(-3600)) && !run(.idleHourly, last: t0.addingTimeInterval(-1800))
           && !run(.idleHourly, idle: false) && !run(.idleHourly, power: false), "S5: idle hourly only on power")

    // 5. AIChain: the evidence job runs on any AI and never steps to another cloud AI
    let conn: Set<String> = ["code", "codex", "api", "openai", "gemini", "local"]
    expect(AIChain.allowed("local", job: "evidence") && AIChain.allowed("codex", job: "evidence"), "S5: evidence may run on any AI")
    for chosen in AIChain.defaultOrder {
        let o = AIChain.order(job: "evidence", chosen: chosen, chain: AIChain.defaults(), connected: conn)
        expect(o.first == chosen && o.dropFirst().allSatisfy { $0 == "local" } && o.count <= 2, "S5: \(chosen) steps only to this Mac: \(o)")
    }
    expect(AIChain.order(job: "evidence", chosen: "api", chain: AIChain.setOn(AIChain.defaults(), route: "local", on: false), connected: conn) == ["api"],
           "S5: the model on this Mac off in the chain: no step")
    expect(AIChain.order(job: "evidence", chosen: "api", chain: AIChain.defaults(), connected: ["api", "codex"]) == ["api"], "S5: the model not installed: no step")
    expect(AIChain.order(job: "after", chosen: "api", chain: AIChain.defaults(), connected: conn).count > 2, "S5: other jobs keep the full chain")
    var routes = AppConfig.AIRoutes()
    routes.after = "openai"
    expect(routes.route("evidence") == "openai", "S5: indexing follows the after-call AI by default")
    routes.evidence = "local"
    expect(routes.route("evidence") == "local", "S5: a chosen indexing AI wins")
    let moved = ComponentCatalog.afterRemovingLocal(prep: "code", tips: "api", after: "code", evidence: "local", chain: AIChain.defaults(), connected: ["codex", "api"])
    expect(moved.evidence == nil, "S5: removing the model on this Mac moves indexing back to the after-call AI, not to another cloud AI")

    // 6. Cost text: a range typical to cap from S3's calibrated estimate; until then "not available yet"
    typealias T = EvidenceText
    expect(T.Estimate.parse(nil) == nil && T.Estimate.parse(["typical": 0.15, "cap": 0.19, "unit": "usd"]) == nil,
           "S5: no calibrated estimate (S3 not on main): no number")
    let est = T.Estimate.parse(["typical": 0.19, "cap": 0.15, "unit": "usd", "parts": 48, "calibrated": true])
    expect(est == T.Estimate(typical: 0.15, cap: 0.19, unit: .usd, parts: 48), "S5: a calibrated estimate is a range typical to cap")
    expect(T.consent(provider: "Anthropic", local: false, estimate: nil) == T.Ranged(T.Line("mem.consent.noEstimate", ["provider": "Anthropic"])),
           "S5: the consent line without an estimate")
    expect(T.consent(provider: "Anthropic", local: false, estimate: est)
           == T.Ranged(T.Line("mem.consent", ["provider": "Anthropic"]), range: T.Line("mem.range.usd", ["from": "$0.15", "to": "$0.19"])),
           "S5: the consent line with the estimate")
    expect(T.consent(provider: "", local: true, estimate: est).line.key == "mem.consent.local", "S5: this Mac only: nothing leaves the Mac")
    expect(T.before(pending: 12, estimate: nil) == T.Ranged(T.Line("mem.before.noEstimate", ["parts": "12"])), "S5: before a pass without an estimate")
    expect(T.before(pending: 12, estimate: est).range == T.Line("mem.range.usd", ["from": "$0.15", "to": "$0.19"])
           && T.before(pending: 12, estimate: est).line.vars["parts"] == "48", "S5: before a pass with the estimate")
    expect(T.range(T.Estimate(typical: 5, cap: 6, unit: .pct)) == T.Line("mem.range.pct", ["from": "5%", "to": "6%"])
           && T.range(T.Estimate(typical: 30, cap: 30.2, unit: .min)) == T.Line("mem.range.min.one", ["v": "30"]), "S5: plan and local units")
    expect(T.provider(route: "code") == "Anthropic" && T.provider(route: "codex") == "OpenAI" && T.provider(route: "gemini") == "Google"
           && T.provider(route: "local").isEmpty, "S5: the provider the consent line names")
    expect(T.unit(route: "codex") == .pct && T.unit(route: "local") == .min && T.unit(route: "gemini") == .usd, "S5: the unit of each route")
    let done = T.Run.parse(["status": "done", "segments_done": 230, "in_tokens": 700_000, "cached_tokens": 100_000, "out_tokens": 100_000,
                            "usd": 0.29, "est_usd": 0.31])
    expect(done.map(T.after) == T.Line("mem.after.doneEst", ["parts": "230", "tokens": "0.9M", "cost": "$0.29", "est": "$0.31"]),
           "S5: after a pass: the run row against its estimate: \(String(describing: done.map(T.after)))")
    expect(T.Run.parse(["status": "ceiling", "ceiling_kind": "usd", "ceiling_value": 1.0]).map(T.after) == T.Line("mem.after.ceiling", ["v": "$1.00"])
           && T.Run.parse(["status": "ceiling", "ceiling_kind": "usd_day", "ceiling_value": 3.0]).map(T.after) == T.Line("mem.after.ceilingDay", ["v": "$3.00"])
           && T.Run.parse(["status": "paused_call"]).map(T.after) == T.Line("mem.after.pausedCall")
           && T.Run.parse(["status": "off"]).map(T.after) == T.Line("mem.after.off"), "S5: a stopped pass says why")
    expect(T.Run.parse(["status": "done", "segments_done": 3]).map(T.after) == T.Line("mem.after.done", ["parts": "3", "tokens": "0"]),
           "S5: a deterministic pass shows no cost")
    let limits = EvidencePrefs().limits
    expect((limits["pass"] as? [String: Double])?["usd"] == 1.0 && (limits["all_day"] as? [String: Double])?["pct"] == 30.0
           && (limits["day"] as? [String: Double])?["min"] == 360.0, "S5: team default ceilings (spec 9.1)")

    // every key the texts use is in both catalogs, with no long dash
    let base = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    func load(_ l: String) -> [String: String] {
        guard let d = try? Data(contentsOf: base.appendingPathComponent("Resources/i18n/\(l).json")),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return [:] }
        return o.compactMapValues { $0 as? String }
    }
    let en = load("en"), uk = load("uk")
    let keys = en.keys.filter { $0.hasPrefix("mem.") }
    expect(keys.count >= 20, "S5: the project memory texts are in en.json (\(keys.count))")
    expect(Set(keys) == Set(uk.keys.filter { $0.hasPrefix("mem.") }), "S5: en and uk carry the same project memory keys")
    expect(keys.allSatisfy { !(en[$0] ?? "").contains("\u{2014}") && !(uk[$0] ?? "").contains("\u{2014}") }, "S5: no long dash")
    for k in ["mem.consent", "mem.consent.noEstimate", "mem.consent.local", "mem.before", "mem.before.noEstimate", "mem.after.done",
              "mem.after.doneEst", "mem.after.doneCost", "mem.after.donePct", "mem.after.ceiling", "mem.after.ceilingDay",
              "mem.after.pausedCall", "mem.after.off", "mem.after.failed", "mem.range.usd", "mem.range.usd.one", "mem.range.pct",
              "mem.range.pct.one", "mem.range.min", "mem.range.min.one"] {
        expect(en[k] != nil && uk[k] != nil, "S5: \(k) in both catalogs")
    }
    expect((en["mem.consent.noEstimate"] ?? "").contains("{provider}") && (en["mem.consent"] ?? "").contains("{range}"), "S5: the consent line names the provider and the cost")
}
