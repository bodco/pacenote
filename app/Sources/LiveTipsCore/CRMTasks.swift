import Foundation

/// PB-17 (INT7, rules/hubspot-tasks.yaml): the pure part of the PM's HubSpot tasks, the same task management as the
/// Notion tasks (NotionTasksCore): the search body, a task read from HubSpot, whether HubSpot closed it, the
/// properties of a new task and which tasks a call shows. The HubSpot calls are an extension of HubSpotConnector at
/// the end of this file, so the client, its retries and its error lines are the same ones as Update HubSpot.

/// Settings > Integrations > HubSpot > Tasks. Off by default; ids are HubSpot ids, never written to git.
public struct CRMTasksSettings: Codable, Equatable {
    public var enabled = false
    /// Whose tasks are shown: "me" (the owner chosen in Matching), "anyone", or a HubSpot owner id.
    public var whose = "me"
    public var whoseName = ""
    public var queueId = ""                 // "" = any queue; else only this queue, and new tasks go into it
    public var queueName = ""
    public var type = "TODO"                // hs_task_type of a new task
    public var priority = "NONE"            // hs_task_priority of a new task
    public var dueDays = 1                  // a new task without a due date is due in this many days (0 = today)
    /// After each call, the PM's open commitments of that call become HubSpot tasks. Off by default (spec rule 1).
    public var autoAdd = false
    /// When Notion tasks and HubSpot tasks are both set up: where new tasks go ("notion" or "hubspot").
    public var destination = "notion"
    public init() {}
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        enabled = (try? c.decode(Bool.self, forKey: .enabled)) ?? false
        whose = (try? c.decode(String.self, forKey: .whose)) ?? "me"
        whoseName = (try? c.decode(String.self, forKey: .whoseName)) ?? ""
        queueId = (try? c.decode(String.self, forKey: .queueId)) ?? ""
        queueName = (try? c.decode(String.self, forKey: .queueName)) ?? ""
        type = (try? c.decode(String.self, forKey: .type)) ?? "TODO"
        priority = (try? c.decode(String.self, forKey: .priority)) ?? "NONE"
        dueDays = max(0, (try? c.decode(Int.self, forKey: .dueDays)) ?? 1)
        autoAdd = (try? c.decode(Bool.self, forKey: .autoAdd)) ?? false
        destination = (try? c.decode(String.self, forKey: .destination)) == "hubspot" ? "hubspot" : "notion"
    }

    /// The owner id the search filters by ("" = anyone) and new tasks get ("" = the Matching owner, else HubSpot's default).
    public func ownerFilter(matchingOwner: String) -> String {
        switch whose { case "anyone": return ""; case "me", "": return matchingOwner; default: return whose }
    }
}

public struct CRMTask: Equatable, Identifiable, Codable {
    public var id: String
    public var title: String
    public var status: String          // hs_task_status: NOT_STARTED, IN_PROGRESS, WAITING, COMPLETED, DEFERRED
    public var due: String             // YYYY-MM-DD or empty
    public var priority: String
    public var type: String
    public var owner: String
    public var companyIds: [String] = []
    public var dealIds: [String] = []
    public var contactIds: [String] = []
    public var created: String = ""
    public var open: Bool { !CRMTasks.closed.contains(status) }
    public init(id: String, title: String, status: String = "NOT_STARTED", due: String = "", priority: String = "", type: String = "",
                owner: String = "", companyIds: [String] = [], dealIds: [String] = [], contactIds: [String] = [], created: String = "") {
        self.id = id; self.title = title; self.status = status; self.due = due; self.priority = priority; self.type = type
        self.owner = owner; self.companyIds = companyIds; self.dealIds = dealIds; self.contactIds = contactIds; self.created = created
    }
}

public enum CRMTasks {
    /// HubSpot's task statuses that are not open. A tick writes COMPLETED; Undo writes the status before the tick.
    public static let closed: Set<String> = ["COMPLETED", "DEFERRED"]
    public static let statuses = ["NOT_STARTED", "IN_PROGRESS", "WAITING", "COMPLETED", "DEFERRED"]
    /// The defaults HubSpot documents for hs_task_type and hs_task_priority; a portal's own options come from the
    /// property definitions when the settings load them.
    public static let types = ["TODO", "CALL", "EMAIL"]
    public static let priorities = ["NONE", "LOW", "MEDIUM", "HIGH"]
    static let props = ["hs_task_subject", "hs_task_status", "hs_timestamp", "hs_task_priority", "hs_task_type", "hubspot_owner_id",
                        "hs_queue_membership_ids", "hs_createdate"]

    /// POST /crm/v3/objects/tasks/search: open tasks of the owner (when set) and the queue (when set), soonest first.
    public static func searchBody(owner: String, queue: String, after: String = "", limit: Int = 100) -> [String: Any] {
        var f: [[String: Any]] = [["propertyName": "hs_task_status", "operator": "NOT_IN", "values": Array(closed).sorted()]]
        if !owner.isEmpty { f.append(["propertyName": "hubspot_owner_id", "operator": "EQ", "value": owner]) }
        if !queue.isEmpty { f.append(["propertyName": "hs_queue_membership_ids", "operator": "CONTAINS_TOKEN", "value": queue]) }
        var b: [String: Any] = ["filterGroups": [["filters": f]], "properties": props,
                                "sorts": [["propertyName": "hs_timestamp", "direction": "ASCENDING"]], "limit": limit]
        if !after.isEmpty { b["after"] = after }
        return b
    }

    static func str(_ o: [String: Any], _ k: String) -> String {
        guard let v = (o["properties"] as? [String: Any])?[k] else { return "" }
        if v is NSNull { return "" }
        return (v as? String) ?? "\(v)"
    }

    public static func task(_ o: [String: Any]) -> CRMTask? {
        guard let id = o["id"].map({ "\($0)" }), !id.isEmpty else { return nil }
        let title = str(o, "hs_task_subject").trimmingCharacters(in: .whitespacesAndNewlines)
        return CRMTask(id: id, title: title.isEmpty ? "Untitled" : title, status: str(o, "hs_task_status").isEmpty ? "NOT_STARTED" : str(o, "hs_task_status"),
                       due: String(str(o, "hs_timestamp").prefix(10)), priority: str(o, "hs_task_priority"), type: str(o, "hs_task_type"),
                       owner: str(o, "hubspot_owner_id"), created: str(o, "hs_createdate"))
    }

    /// The day a new task is due: the item's own due date when it has one, else the default from the settings.
    public static func dueDate(_ due: Date?, defaultDays: Int, now: Date = Date()) -> Date {
        if let d = due { return d }
        let day = Calendar.current.startOfDay(for: now).addingTimeInterval(Double(max(0, defaultDays)) * 86400)
        return day.addingTimeInterval(9 * 3600)     // 09:00 local, so the task is not overdue at midnight
    }

    /// The text a new task carries. The call's title and date and the link back; never transcript text.
    public static func body(callTitle: String, date: String, link: String) -> String {
        var b = "From the call " + CRMCallLog.esc(callTitle) + (date.isEmpty ? "" : ", " + CRMCallLog.esc(date))
        if !link.isEmpty { b += "<br>Call page: <a href=\"\(CRMCallLog.esc(link))\">\(CRMCallLog.esc(link))</a>" }
        return b
    }

    /// The properties of a new task (rules/hubspot-tasks.yaml > link). The owner is added by HubSpotConnector.owned.
    public static func newTask(_ s: CRMTasksSettings, title: String, due: Date, body: String) -> [String: Any] {
        var p: [String: Any] = ["hs_task_subject": String(title.prefix(250)), "hs_task_status": "NOT_STARTED",
                                "hs_timestamp": ISO8601DateFormatter().string(from: due), "hs_task_body": body,
                                "hs_task_type": s.type.isEmpty ? "TODO" : s.type, "hs_task_priority": s.priority.isEmpty ? "NONE" : s.priority]
        if !s.queueId.isEmpty { p["hs_queue_membership_ids"] = s.queueId }
        return p
    }

    /// Which tasks a call shows: the ones associated with the call's deal, company or contacts. A call without a CRM
    /// link shows none (as a Notion call without a project).
    public static func forCall(_ tasks: [CRMTask], refs: [CRMRef]) -> [CRMTask] {
        let deals = Set(refs.filter { $0.type == "deals" }.map(\.id)), cos = Set(refs.filter { $0.type == "companies" }.map(\.id))
        let people = Set(refs.filter { $0.type == "contacts" }.map(\.id))
        guard !deals.isEmpty || !cos.isEmpty || !people.isEmpty else { return [] }
        return tasks.filter { t in t.open && (!deals.isDisjoint(with: t.dealIds) || !cos.isDisjoint(with: t.companyIds) || !people.isDisjoint(with: t.contactIds)) }
    }

    /// Dedupe (rules/hubspot-tasks.yaml > after_call): an open task with 60% of the words and the same deal or company
    /// (the same contact when the call has neither) is the same task; it is linked instead of created. A call without
    /// a CRM link has no twin: a task of another customer is never reused.
    public static func twin(_ tasks: [CRMTask], text: String, refs: [CRMRef]) -> CRMTask? {
        let deals = Set(refs.filter { $0.type == "deals" }.map(\.id)), cos = Set(refs.filter { $0.type == "companies" }.map(\.id))
        let people = Set(refs.filter { $0.type == "contacts" }.map(\.id))
        return tasks.first { t in
            guard t.open, CommitmentRegister.similar(t.title, text) else { return false }
            if !deals.isEmpty || !cos.isEmpty { return !deals.isDisjoint(with: t.dealIds) || !cos.isDisjoint(with: t.companyIds) }
            return !people.isEmpty && !people.isDisjoint(with: t.contactIds)
        }
    }

    /// Where new tasks of a commitment go: nil when neither is set up; the PM's choice when both are.
    public static func destination(notionReady: Bool, hubspotReady: Bool, choice: String) -> String? {
        switch (notionReady, hubspotReady) {
        case (true, true): return choice == "hubspot" ? "hubspot" : "notion"
        case (true, false): return "notion"
        case (false, true): return "hubspot"
        default: return nil
        }
    }

    /// What the next sync does with a commitment linked to a HubSpot task (rules/hubspot-tasks.yaml > both_ways).
    /// `task` nil = HubSpot answered 404 (the task was deleted there): the item is unlinked, open or done, and keeps
    /// its status.
    public static func syncOp(commitmentStatus: String, doneSource: String, task: CRMTask?) -> String? {
        guard let t = task else { return "crm_unlink" }
        if commitmentStatus == "open" && !t.open { return "done" }
        if commitmentStatus == "done" && t.open && doneSource == "hubspot" { return "reopen" }
        return nil
    }

    /// The task's page in HubSpot. The EU data region uses its own app host.
    public static func url(portal: String, region: String, id: String) -> String {
        guard !portal.isEmpty else { return "" }
        let host = region.lowercased().hasPrefix("eu") ? "app-eu1.hubspot.com" : "app.hubspot.com"
        return "https://\(host)/tasks/\(portal)/view/all/task/\(id)"
    }
}

/// The HubSpot calls of the tasks, on the same client as everything else (retries, 401/403 lines).
extension HubSpotConnector {
    /// Open tasks for the settings, with their deal, company and contact ids. At most `max` tasks.
    public func openTasks(owner: String, queue: String, max: Int = 500) async throws -> [CRMTask] {
        var out: [CRMTask] = [], after = ""
        repeat {
            let r = try await call("POST", "/crm/v3/objects/tasks/search", CRMTasks.searchBody(owner: owner, queue: queue, after: after))
            out += ((r["results"] as? [[String: Any]]) ?? []).compactMap(CRMTasks.task)
            after = (((r["paging"] as? [String: Any])?["next"] as? [String: Any])?["after"] as? String) ?? ""
        } while !after.isEmpty && out.count < max
        try await addAssociations(&out)
        return out
    }

    /// Associations v4 batch read, per object type, 100 ids at a time.
    func addAssociations(_ tasks: inout [CRMTask]) async throws {
        guard !tasks.isEmpty else { return }
        for type in ["deals", "companies", "contacts"] {
            var map: [String: [String]] = [:]
            for chunk in stride(from: 0, to: tasks.count, by: 100).map({ Array(tasks[$0..<Swift.min($0 + 100, tasks.count)]) }) {
                let r = try await call("POST", "/crm/v4/associations/tasks/\(type)/batch/read", ["inputs": chunk.map { ["id": $0.id] }])
                for row in (r["results"] as? [[String: Any]]) ?? [] {
                    guard let from = ((row["from"] as? [String: Any])?["id"]).map({ "\($0)" }) else { continue }
                    map[from, default: []] += ((row["to"] as? [[String: Any]]) ?? []).compactMap { ($0["toObjectId"] ?? $0["id"]).map { "\($0)" } }
                }
            }
            for i in tasks.indices {
                let ids = map[tasks[i].id] ?? []
                switch type { case "deals": tasks[i].dealIds = ids; case "companies": tasks[i].companyIds = ids; default: tasks[i].contactIds = ids }
            }
        }
    }

    /// One task by id; nil when HubSpot deleted it (404).
    public func task(id: String) async throws -> CRMTask? {
        do {
            let r = try await call("GET", "/crm/v3/objects/tasks/\(id)?properties=" + CRMTasks.props.joined(separator: ","))
            return CRMTasks.task(r)
        } catch let e as CRMError where e.code == 404 { return nil }
    }

    /// One task by id with its deal, company and contact ids, read now (a twin is checked on this, not on the cache).
    public func taskWithAssociations(id: String) async throws -> CRMTask? {
        guard let t = try await task(id: id) else { return nil }
        var one = [t]
        try await addAssociations(&one)
        return one[0]
    }

    /// A new task associated with the call's deal, company and contacts (task -> deal 216, company 192, contact 204).
    public func createTask(_ properties: [String: Any], associations: [CRMRef]) async throws -> CRMTask {
        let r = try await call("POST", "/crm/v3/objects/tasks", [
            "properties": owned(properties),
            "associations": associations.compactMap { a in Self.taskAssoc[a.type].map { ["to": ["id": a.id], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": $0]]] } }])
        var t = CRMTasks.task(r) ?? CRMTask(id: (r["id"] as? String) ?? "", title: (properties["hs_task_subject"] as? String) ?? "")
        if t.title == "Untitled", let s = properties["hs_task_subject"] as? String { t.title = s }
        if t.due.isEmpty, let d = properties["hs_timestamp"] as? String { t.due = String(d.prefix(10)) }
        t.dealIds = associations.filter { $0.type == "deals" }.map(\.id)
        t.companyIds = associations.filter { $0.type == "companies" }.map(\.id)
        t.contactIds = associations.filter { $0.type == "contacts" }.map(\.id)
        return t
    }

    /// A tick (COMPLETED) or its Undo (the status before).
    public func setTaskStatus(id: String, _ status: String) async throws {
        _ = try await call("PATCH", "/crm/v3/objects/tasks/\(id)", ["properties": ["hs_task_status": status]])
    }

    /// The options of a task property (hs_task_type, hs_task_priority, hs_queue_membership_ids) for the settings.
    public func taskOptions(_ property: String) async throws -> [(value: String, label: String)] {
        let r = try await call("GET", "/crm/v3/properties/tasks/\(property)")
        return ((r["options"] as? [[String: Any]]) ?? []).compactMap { o in
            guard let v = o["value"].map({ "\($0)" }), !v.isEmpty, (o["hidden"] as? Bool) != true else { return nil }
            return (v, (o["label"] as? String) ?? v)
        }
    }

    /// The portal id and data region, for the link to a task in HubSpot.
    public func portal() async throws -> (id: String, region: String) {
        let info = try await call("GET", "/account-info/v3/details")
        return (info["portalId"].map { "\($0)" } ?? "", (info["dataHostingLocation"] as? String) ?? "")
    }
}
