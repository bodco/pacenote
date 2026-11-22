import Foundation
import LiveTipsCore

/// PB-17: a small in-memory HubSpot for tasks (search, create, read, status, delete, associations). Never the network.
final class FakeHubSpotTasks: CRMTransport {
    var tasks: [String: [String: String]] = [:]       // id -> properties
    var assoc: [String: [String]] = [:]                // task id -> "type:id"
    var log: [String] = []
    var bodies: [[String: Any]] = []
    var next = 500
    func send(_ method: String, _ path: String, _ body: [String: Any]?) async throws -> (Int, [String: Any], [String: String]) {
        log.append(method + " " + path.split(separator: "?")[0])
        if let b = body { bodies.append(b) }
        let props = { (b: [String: Any]?) -> [String: String] in ((b?["properties"] as? [String: Any]) ?? [:]).mapValues { "\($0)" } }
        func object(_ id: String) -> [String: Any] { ["id": id, "properties": tasks[id] ?? [:]] }
        if method == "POST" && path == "/crm/v3/objects/tasks/search" {
            let filters = (((body?["filterGroups"] as? [[String: Any]])?.first?["filters"]) as? [[String: Any]]) ?? []
            let hits = tasks.keys.sorted().filter { id in
                let p = tasks[id]!
                return filters.allSatisfy { f in
                    let k = f["propertyName"] as? String ?? "", v = p[k] ?? ""
                    switch f["operator"] as? String {
                    case "EQ": return v == (f["value"] as? String)
                    case "NOT_IN": return !((f["values"] as? [String]) ?? []).contains(v)
                    case "CONTAINS_TOKEN": return v.split(separator: ";").map(String.init).contains((f["value"] as? String) ?? "")
                    default: return false
                    }
                }
            }
            return (200, ["results": hits.map(object)], [:])
        }
        if method == "POST", path.hasPrefix("/crm/v4/associations/tasks/"), path.hasSuffix("/batch/read") {
            let type = String(path.split(separator: "/")[4])
            let ids = ((body?["inputs"] as? [[String: Any]]) ?? []).compactMap { $0["id"] as? String }
            let rows: [[String: Any]] = ids.compactMap { id in
                let to = (assoc[id] ?? []).filter { $0.hasPrefix(type + ":") }.map { ["toObjectId": String($0.split(separator: ":")[1])] }
                return to.isEmpty ? nil : ["from": ["id": id], "to": to]
            }
            return (207, ["results": rows], [:])
        }
        if method == "POST" && path == "/crm/v3/objects/tasks" {
            next += 1; let id = "\(next)"
            tasks[id] = props(body)
            let to = ((body?["associations"] as? [[String: Any]]) ?? []).map { a -> (String, Int) in
                ("\((a["to"] as! [String: Any])["id"]!)", (((a["types"] as! [[String: Any]])[0])["associationTypeId"] as! Int)) }
            let typeOf = [216: "deals", 192: "companies", 204: "contacts"]
            assoc[id] = to.map { (typeOf[$0.1] ?? "type\($0.1)") + ":" + $0.0 }
            return (201, object(id), [:])
        }
        let id = String(path.split(separator: "?")[0].split(separator: "/")[4])
        guard tasks[id] != nil else { return (404, ["message": "not found"], [:]) }
        switch method {
        case "GET": return (200, object(id), [:])
        case "PATCH": for (k, v) in props(body) { tasks[id]![k] = v }; return (200, object(id), [:])
        case "DELETE": tasks[id] = nil; return (204, [:], [:])
        default: return (400, [:], [:])
        }
    }
}

func crmTasksChecks() async {
    // settings: off by default, an old CRM config keeps working, a bad destination falls back to Notion
    let old = try? JSONDecoder().decode(CRMSettings.self, from: Data("{\"provider\":\"hubspot\"}".utf8))
    expect(old?.tasks.enabled == false && old?.tasks.autoAdd == false && old?.tasks.whose == "me" && old?.tasks.dueDays == 1
           && old?.tasks.destination == "notion", "htasks: off by default, auto add off")
    let bad = try? JSONDecoder().decode(CRMTasksSettings.self, from: Data("{\"destination\":\"slack\",\"dueDays\":-4}".utf8))
    expect(bad?.destination == "notion" && bad?.dueDays == 0, "htasks: destination and due days are clamped")
    var s = CRMTasksSettings()
    expect(s.ownerFilter(matchingOwner: "9") == "9", "htasks: mine = the Matching owner")
    s.whose = "anyone"; expect(s.ownerFilter(matchingOwner: "9") == "", "htasks: anyone has no owner filter")
    s.whose = "77"; expect(s.ownerFilter(matchingOwner: "9") == "77", "htasks: a chosen owner")

    // where new tasks go
    expect(CRMTasks.destination(notionReady: true, hubspotReady: true, choice: "hubspot") == "hubspot"
           && CRMTasks.destination(notionReady: true, hubspotReady: true, choice: "notion") == "notion"
           && CRMTasks.destination(notionReady: false, hubspotReady: true, choice: "notion") == "hubspot"
           && CRMTasks.destination(notionReady: true, hubspotReady: false, choice: "hubspot") == "notion"
           && CRMTasks.destination(notionReady: false, hubspotReady: false, choice: "hubspot") == nil, "htasks: the PM's choice when both are set up")

    // the read: open tasks of the owner and the queue, with their deal, company and contacts
    let t = FakeHubSpotTasks()
    t.tasks = ["1": ["hs_task_subject": "Send the pricing sheet", "hs_task_status": "NOT_STARTED", "hubspot_owner_id": "9",
                     "hs_timestamp": "2026-12-01T09:00:00Z", "hs_queue_membership_ids": "3;5"],
               "2": ["hs_task_subject": "Old follow-up", "hs_task_status": "COMPLETED", "hubspot_owner_id": "9"],
               "3": ["hs_task_subject": "Someone else's", "hs_task_status": "IN_PROGRESS", "hubspot_owner_id": "12"],
               "4": ["hs_task_subject": "Other queue", "hs_task_status": "WAITING", "hubspot_owner_id": "9", "hs_queue_membership_ids": "7"]]
    t.assoc = ["1": ["deals:D1", "companies:C1", "contacts:P1"], "4": ["companies:C2"]]
    let hs = HubSpotConnector(transport: t, ownerId: "9", sleep: { _ in })
    let mine = (try? await hs.openTasks(owner: "9", queue: "")) ?? []
    expect(Set(mine.map(\.id)) == ["1", "4"], "htasks: only open tasks of the owner")
    let one = mine.first { $0.id == "1" }
    expect(one?.title == "Send the pricing sheet" && one?.due == "2026-12-01" && one?.dealIds == ["D1"] && one?.companyIds == ["C1"]
           && one?.contactIds == ["P1"], "htasks: title, due and associations")
    let q = (try? await hs.openTasks(owner: "9", queue: "5")) ?? []
    expect(q.map(\.id) == ["1"], "htasks: the queue filter")
    let anyone = (try? await hs.openTasks(owner: "", queue: "")) ?? []
    expect(Set(anyone.map(\.id)) == ["1", "3", "4"], "htasks: anyone's tasks")
    let sb = CRMTasks.searchBody(owner: "9", queue: "5")
    expect("\(sb)".contains("hubspot_owner_id") && "\(sb)".contains("hs_queue_membership_ids") && "\(sb)".contains("COMPLETED"), "htasks: the search body")
    let anyFilters = (((CRMTasks.searchBody(owner: "", queue: "")["filterGroups"] as? [[String: Any]])?.first?["filters"]) as? [[String: Any]]) ?? []
    expect(anyFilters.count == 1 && !anyFilters.contains { ($0["propertyName"] as? String) == "hubspot_owner_id" }, "htasks: no owner filter for anyone")

    // which tasks a call shows: its deal, company or contacts; a call without a link none
    expect(CRMTasks.forCall(mine, refs: [CRMRef("deals", "D1")]).map(\.id) == ["1"]
           && CRMTasks.forCall(mine, refs: [CRMRef("companies", "C2")]).map(\.id) == ["4"]
           && CRMTasks.forCall(mine, refs: [CRMRef("contacts", "P1")]).map(\.id) == ["1"]
           && CRMTasks.forCall(mine, refs: []).isEmpty, "htasks: a call shows the tasks of its deal, company and contacts")

    // add: the properties of the settings, the three association types, the owner; never transcript text
    var ns = CRMTasksSettings(); ns.type = "CALL"; ns.priority = "HIGH"; ns.queueId = "5"; ns.dueDays = 3
    let now = Date(timeIntervalSince1970: 1_780_000_000)
    let due = CRMTasks.dueDate(nil, defaultDays: ns.dueDays, now: now)
    expect(Calendar.current.dateComponents([.day], from: Calendar.current.startOfDay(for: now), to: due).day == 3, "htasks: the default due date")
    let given = Date(timeIntervalSince1970: 1_790_000_000)
    expect(CRMTasks.dueDate(given, defaultDays: 3, now: now) == given, "htasks: a said due date wins")
    let body = CRMTasks.body(callTitle: "Orbit <renewal>", date: "Nov 20, 2026", link: "https://notion.example/p1")
    let refs = [CRMRef("deals", "D1"), CRMRef("companies", "C1"), CRMRef("contacts", "P1"), CRMRef("notes", "X")]
    let made = try? await hs.createTask(CRMTasks.newTask(ns, title: "Share the security review with Emily Clark", due: due, body: body), associations: refs)
    let mp = t.tasks[made?.id ?? ""] ?? [:]
    expect(made != nil && mp["hs_task_subject"] == "Share the security review with Emily Clark" && mp["hs_task_status"] == "NOT_STARTED"
           && mp["hs_task_type"] == "CALL" && mp["hs_task_priority"] == "HIGH" && mp["hs_queue_membership_ids"] == "5"
           && mp["hubspot_owner_id"] == "9", "htasks: a new task carries type, priority, queue and owner")
    expect(Set(t.assoc[made?.id ?? ""] ?? []) == ["deals:D1", "companies:C1", "contacts:P1"], "htasks: associated with the deal, company and contact only")
    expect((mp["hs_task_body"] ?? "").contains("Orbit &lt;renewal&gt;") && (mp["hs_task_body"] ?? "").contains("https://notion.example/p1"), "htasks: body says the call and links back, escaped")
    expect(made?.dealIds == ["D1"] && made?.due == String(ISO8601DateFormatter().string(from: due).prefix(10)), "htasks: the new task is known at once (search can lag)")
    expect(!"\(t.bodies)".lowercased().contains("transcript"), "htasks: nothing sent carries transcript text")

    // dedupe: the same words on the same deal is the same task; another deal is not
    expect(CRMTasks.twin(mine, text: "send pricing sheet", refs: [CRMRef("deals", "D1")])?.id == "1", "htasks: a twin on the same deal is linked")
    expect(CRMTasks.twin(mine, text: "send pricing sheet", refs: [CRMRef("deals", "D9")]) == nil, "htasks: another deal is a new task")
    expect(CRMTasks.twin(mine, text: "book the venue", refs: [CRMRef("deals", "D1")]) == nil, "htasks: other words are a new task")

    // tick and Undo: COMPLETED, then the status before
    try? await hs.setTaskStatus(id: "4", "COMPLETED")
    let afterTick = (try? await hs.openTasks(owner: "9", queue: "")) ?? []
    expect(t.tasks["4"]?["hs_task_status"] == "COMPLETED" && afterTick.allSatisfy { $0.id != "4" }, "htasks: a tick completes the task")
    try? await hs.setTaskStatus(id: "4", "WAITING")
    expect(t.tasks["4"]?["hs_task_status"] == "WAITING", "htasks: Undo writes the status before")

    // both ways: closed in HubSpot -> done; reopened there -> reopen; deleted there -> unlink (the item stays open)
    let closed = try? await hs.task(id: "2")
    expect(CRMTasks.syncOp(commitmentStatus: "open", doneSource: "", task: closed ?? nil) == "done", "htasks: closed in HubSpot closes the item")
    let reopened = try? await hs.task(id: "1")
    expect(CRMTasks.syncOp(commitmentStatus: "done", doneSource: "hubspot", task: reopened ?? nil) == "reopen", "htasks: reopened in HubSpot reopens the item")
    expect(CRMTasks.syncOp(commitmentStatus: "done", doneSource: "manual", task: reopened ?? nil) == nil, "htasks: an item closed here is not reopened")
    try? await hs.delete(type: "tasks", id: "3")
    var gone: Result<CRMTask?, Error> = .success(CRMTask(id: "x", title: "x"))
    do { gone = .success(try await hs.task(id: "3")) } catch { gone = .failure(error) }
    expect((try? gone.get()) == .some(nil) && CRMTasks.syncOp(commitmentStatus: "open", doneSource: "", task: nil) == "crm_unlink", "htasks: a deleted task unlinks the item")
    expect(t.tasks["3"] == nil && t.log.contains("DELETE /crm/v3/objects/tasks/3"), "htasks: Remove deletes the task")

    // the register: crm_link and crm_unlink, separate from the Notion link
    let dir = NSTemporaryDirectory() + "pb17-" + UUID().uuidString
    try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
    let cid = CommitmentRegister.add(folder: dir, text: "Send the pricing sheet", project: "orbit", title: "Orbit renewal", call: "2026-11-20-1000-orbit.md") ?? ""
    CommitmentRegister.mark(folder: dir, id: cid, op: "crm_link", crmTask: "1")
    var c = CommitmentRegister.load(folder: dir).first { $0.id == cid }
    expect(c?.crmTask == "1" && c?.notionPage == "", "htasks: crm_link keeps the HubSpot task id")
    CommitmentRegister.mark(folder: dir, id: cid, op: "done", source: "hubspot")
    c = CommitmentRegister.load(folder: dir).first { $0.id == cid }
    expect(c?.status == "done" && c?.doneSource == "hubspot" && c?.crmTask == "1", "htasks: done from HubSpot")
    CommitmentRegister.mark(folder: dir, id: cid, op: "reopen")
    CommitmentRegister.mark(folder: dir, id: cid, op: "crm_unlink", source: "removed")
    c = CommitmentRegister.load(folder: dir).first { $0.id == cid }
    expect(c?.crmTask == "" && c?.notionNote == "crmRemoved" && c?.status == "open", "htasks: crm_unlink keeps the item open and says why")
    CommitmentRegister.mark(folder: dir, id: cid, op: "crm_link", crmTask: "8")
    CommitmentRegister.mark(folder: dir, id: cid, op: "crm_unlink", source: "undo")
    c = CommitmentRegister.load(folder: dir).first { $0.id == cid }
    expect(c?.crmTask == "" && c?.notionNote == "", "htasks: an undone add leaves no removed note")
    try? FileManager.default.removeItem(atPath: dir)

    // the link to the task in HubSpot follows the data region
    expect(CRMTasks.url(portal: "123", region: "eu1", id: "9").hasPrefix("https://app-eu1.hubspot.com/") && CRMTasks.url(portal: "", region: "", id: "9") == "",
           "htasks: the task link")
}
