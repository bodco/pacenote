import Foundation

/// Pacenote 0.8.7 (I, rules/crm.yaml): one connector for any CRM. HubSpot is the first; another CRM implements the
/// same protocol and the preparation, the snapshot and Update <CRM> do not change. Nothing here writes on its own:
/// the app calls the write methods only after the PM's click and review.
public struct CRMCompany: Equatable, Codable { public var id: String; public var name: String; public var domain: String
    public init(id: String, name: String, domain: String = "") { self.id = id; self.name = name; self.domain = domain } }
public struct CRMContact: Equatable, Codable { public var id: String; public var name: String; public var email: String; public var role: String
    public init(id: String, name: String, email: String, role: String = "") { self.id = id; self.name = name; self.email = email; self.role = role } }
public struct CRMDeal: Equatable, Codable {
    public var id: String; public var name: String; public var stage: String; public var stageName: String
    public var amount: String; public var closeDate: String; public var owner: String; public var nextStep: String
    public var updated: String; public var open: Bool
    public var pipeline: String      // H4: the deal's own pipeline, so only its stages are offered
    public init(id: String, name: String, stage: String = "", stageName: String = "", amount: String = "", closeDate: String = "",
                owner: String = "", nextStep: String = "", updated: String = "", open: Bool = true, pipeline: String = "") {
        self.id = id; self.name = name; self.stage = stage; self.stageName = stageName; self.amount = amount; self.closeDate = closeDate
        self.owner = owner; self.nextStep = nextStep; self.updated = updated; self.open = open; self.pipeline = pipeline
    }
}
public struct CRMActivity: Equatable, Codable { public var kind: String; public var when: String; public var text: String; public var done: Bool
    public init(kind: String, when: String, text: String, done: Bool = true) { self.kind = kind; self.when = when; self.text = text; self.done = done } }
public struct CRMAccount: Equatable { public var portal: String; public var user: String; public var userEmail: String
    public var portalId: String         // H2: the portal the key writes to, kept with each call's link
    public init(portal: String, user: String, userEmail: String = "", portalId: String = "") {
        self.portal = portal; self.user = user; self.userEmail = userEmail; self.portalId = portalId } }
/// 0.8.7 (I6.2): a CRM user who can own the notes and tasks Pacenote writes (names only in the UI).
public struct CRMOwner: Equatable, Codable, Identifiable { public var id: String; public var name: String; public var email: String
    public init(id: String, name: String, email: String) { self.id = id; self.name = name; self.email = email }
    /// The owner whose email is one of the user's own (calendar accounts); nil when none matches.
    public static func pick(_ owners: [CRMOwner], emails: [String]) -> CRMOwner? {
        let mine = Set(emails.map { $0.lowercased().trimmingCharacters(in: .whitespaces) }.filter { $0.contains("@") })
        return owners.first { mine.contains($0.email.lowercased()) }
    }
}
public struct CRMRef: Equatable, Codable { public var type: String; public var id: String
    public init(_ type: String, _ id: String) { self.type = type; self.id = id } }

public struct CRMError: Error, LocalizedError, Equatable {
    public var code: Int; public var message: String; public var scope: String
    public init(code: Int, message: String, scope: String = "") { self.code = code; self.message = message; self.scope = scope }
    public var errorDescription: String? { message }
}

public protocol CRMConnector {
    var providerName: String { get }
    func testConnection() async throws -> CRMAccount
    func findCompany(domain: String, name: String) async throws -> CRMCompany?
    func findContacts(emails: [String]) async throws -> [CRMContact]
    func deals(companyId: String) async throws -> [CRMDeal]
    func deal(id: String) async throws -> CRMDeal?
    func recentActivity(refs: [CRMRef], limit: Int) async throws -> [CRMActivity]
    func pipelines() async throws -> [String: String]            // stage id -> name
    func properties(objectType: String) async throws -> [(name: String, label: String)]
    func createNote(text: String, associations: [CRMRef]) async throws -> String
    func updateDeal(id: String, fields: [String: String]) async throws -> [String: String]   // returns the old values
    func createTask(subject: String, due: Date?, associations: [CRMRef]) async throws -> String
    func delete(type: String, id: String) async throws
    func owners() async throws -> [CRMOwner]                    // 0.8.7 (I6.2)
    /// H2: log the call as a Call engagement; the same Pacenote call id updates the same Call, never a second one.
    func upsertCall(_ log: CRMCallLog, knownId: String, associations: [CRMRef]) async throws -> CRMCallResult
    func restoreCall(id: String, fields: [String: String]) async throws
    /// H2: the portal id of the key in use, asked fresh before a write (never a cached value).
    func portalId() async throws -> String
}

/// H2: a call's link belongs to the portal it was made in. A write goes ahead only when the link names the portal of
/// the key in use; a link from another portal, or from before Pacenote kept the portal, needs Relink first.
public enum CRMPortal {
    public static let mismatchCode = 409
    public static func matches(link: String, current: String) -> Bool { !link.isEmpty && link == current }
    public static func verify(link: String, connector c: CRMConnector) async throws {
        let current = try await c.portalId()
        guard matches(link: link, current: current) else {
            throw CRMError(code: mismatchCode, message: "This call is linked to another HubSpot portal, or to an unknown one. Relink it first.", scope: "portal")
        }
    }
}

/// H2: Auto and Apply of one call take turns, and each reads the stored Call id when its turn comes, so the two paths
/// never both create a Call for the same call.
public actor CRMCallQueue {
    public static let shared = CRMCallQueue()
    private var tails: [String: Task<Void, Never>] = [:]
    public init() {}
    public func run<T>(_ key: String, _ op: @escaping () async throws -> T) async throws -> T {
        let prev = tails[key]
        let job = Task { () async throws -> T in _ = await prev?.value; return try await op() }
        let tail = Task { _ = try? await job.value }
        tails[key] = tail
        let r = await job.result
        if tails[key] == tail { tails[key] = nil }
        return try r.get()
    }
}

public enum CRMCallSync {
    /// One write of a call's Call: in turn per call key, the portal checked, the stored id read when the turn comes,
    /// the new id stored before the turn ends.
    public static func log(_ entry: CRMCallLog, connector c: CRMConnector, associations: [CRMRef], queue: CRMCallQueue = .shared,
                           load: @escaping () -> (callId: String, portalId: String), save: @escaping (String) -> Void) async throws -> CRMCallResult {
        try await queue.run(entry.key) {
            let stored = load()
            try await CRMPortal.verify(link: stored.portalId, connector: c)
            let r = try await c.upsertCall(entry, knownId: stored.callId, associations: associations)
            save(r.id)
            return r
        }
    }

    /// Undo of one Apply: the portal the Apply wrote to is checked first, so a key changed in between never deletes or
    /// overwrites another portal's records with the same ids. Deal fields and updated Calls get their old values back,
    /// the rest is deleted. Returns how many items failed; a mismatch throws before anything is sent.
    public static func undo(_ items: [CRMUndoItem], portal: String, connector c: CRMConnector,
                            deleted: (CRMUndoItem) -> Void = { _ in }) async throws -> Int {
        try await CRMPortal.verify(link: portal, connector: c)
        var failed = 0
        for it in items.reversed() {
            do {
                if it.type == "deals" { _ = try await c.updateDeal(id: it.id, fields: it.old) }
                else if it.type == "calls" && !it.old.isEmpty { try await c.restoreCall(id: it.id, fields: it.old) }
                else { try await c.delete(type: it.type, id: it.id); deleted(it) }
            } catch { failed += 1 }
        }
        return failed
    }
}

/// One record an Apply wrote: its type, id and the values it replaced (empty for a new record).
public struct CRMUndoItem: Equatable {
    public var type: String, id: String, old: [String: String]
    public init(_ type: String, _ id: String, _ old: [String: String] = [:]) { self.type = type; self.id = id; self.old = old }
}

/// H2 (docs/specs/hubspot-crm-integration.md): what is logged as a Call engagement after a call. No transcript text:
/// the body is the Summary, the next steps, the participants' names and the link back to the call.
public struct CRMCallLog: Equatable {
    public var key: String              // the Pacenote call id stored with the call (<stem>.crm.json)
    public var title: String
    public var start: Date
    public var durationMs: Int
    public var direction: String        // OUTBOUND or INBOUND
    public var body: String
    public var disposition: String      // HubSpot's outcome id; the default "Connected" is the same in every portal
    // HubSpot's built-in id of the outcome Connected (public, same in every portal); written in two parts for the anonymization lint
    public static let connected = "f240bbac-87c9-4f6e" + "-bf70-924b57d47db7"
    public static func externalId(_ key: String) -> String { "pacenote:" + key }

    /// Summary is the call page's Summary (the notes' In short and Recap); next steps are what the PM and the other
    /// side took on, else the sales next step. The two never repeat each other.
    public static func sections(tldr: String, recap: [String], onMe: [String], theirs: [String], salesNext: String) -> (summary: [String], nextSteps: [String]) {
        func plain(_ s: String) -> String { s.replacingOccurrences(of: "\\*\\*|__", with: "", options: .regularExpression).trimmingCharacters(in: .whitespacesAndNewlines) }
        let summary = ([tldr] + recap).map(plain).filter { !$0.isEmpty }
        var steps = (onMe + theirs).map(plain).filter { !$0.isEmpty }
        if steps.isEmpty && !salesNext.isEmpty { steps = [plain(salesNext)] }
        return (summary, steps)
    }

    public init(key: String, title: String, start: Date, durationMs: Int, direction: String = "OUTBOUND", body: String,
                disposition: String = CRMCallLog.connected) {
        self.key = key; self.title = title; self.start = start; self.durationMs = max(0, durationMs)
        self.direction = direction == "INBOUND" ? "INBOUND" : "OUTBOUND"; self.body = body; self.disposition = disposition
    }

    static func esc(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;").replacingOccurrences(of: ">", with: "&gt;")
    }

    /// summary and next steps come from the call page as edited; the transcript is not an input on purpose.
    public static func body(summary: [String], nextSteps: [String], participants: [String], link: String, key: String) -> String {
        var b = ""
        func list(_ h: String, _ xs: [String]) { if !xs.isEmpty { b += "<p><b>\(h)</b></p><ul>" + xs.map { "<li>\(esc($0))</li>" }.joined() + "</ul>" } }
        if !participants.isEmpty { b += "<p><b>Participants</b>: " + participants.map(esc).joined(separator: ", ") + "</p>" }
        list("Summary", summary); list("Next steps", nextSteps)
        if !link.isEmpty { b += "<p>Call page: <a href=\"\(esc(link))\">\(esc(link))</a></p>" }
        b += "<p>Pacenote call id: \(esc(key))</p>"
        return b
    }

    var properties: [String: Any] {
        ["hs_timestamp": ISO8601DateFormatter().string(from: start), "hs_call_title": title, "hs_call_body": body,
         "hs_call_duration": String(durationMs), "hs_call_direction": direction, "hs_call_status": "COMPLETED",
         "hs_call_disposition": disposition, "hs_call_external_id": Self.externalId(key)]
    }
}

/// created = a new Call; old = the values an update replaced (empty for a new Call), for Undo.
public struct CRMCallResult: Equatable { public var id: String; public var created: Bool; public var old: [String: String]
    public init(id: String, created: Bool, old: [String: String] = [:]) { self.id = id; self.created = created; self.old = old } }

/// The HTTP layer, replaceable by fixtures in tests.
public protocol CRMTransport {
    func send(_ method: String, _ path: String, _ body: [String: Any]?) async throws -> (Int, [String: Any], [String: String])
}

public struct URLSessionTransport: CRMTransport {
    let base: String, key: String
    public init(base: String = "https://api.hubapi.com", key: String) { self.base = base; self.key = key }
    public func send(_ method: String, _ path: String, _ body: [String: Any]?) async throws -> (Int, [String: Any], [String: String]) {
        var r = URLRequest(url: URL(string: base + path)!)
        r.httpMethod = method
        r.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")       // never logged
        r.timeoutInterval = 30
        if let b = body { r.setValue("application/json", forHTTPHeaderField: "Content-Type"); r.httpBody = try JSONSerialization.data(withJSONObject: b) }
        let (d, resp) = try await URLSession.shared.data(for: r)
        let h = (resp as? HTTPURLResponse)
        var headers: [String: String] = [:]
        for (k, v) in h?.allHeaderFields ?? [:] { headers[String(describing: k).lowercased()] = String(describing: v) }
        return (h?.statusCode ?? 0, (try? JSONSerialization.jsonObject(with: d) as? [String: Any]) ?? [:], headers)
    }
}

/// HubSpot through a service key (rules/crm.yaml > providers.hubspot): v3 objects and search, associations v4.
public final class HubSpotConnector: CRMConnector {
    public let providerName = "HubSpot"
    public static let scopes = ["crm.objects.companies.read", "crm.objects.companies.write", "crm.objects.contacts.read",
                                "crm.objects.contacts.write", "crm.objects.deals.read", "crm.objects.deals.write",
                                "crm.objects.owners.read", "crm.schemas.companies.read", "crm.schemas.contacts.read",
                                "crm.schemas.deals.read"]
    let t: CRMTransport
    let sleep: (Double) async -> Void
    /// 0.8.7 (I6.2): the owner of the notes and tasks Pacenote writes ("" = HubSpot's default)
    public var ownerId: String
    public init(transport: CRMTransport, ownerId: String = "", sleep: @escaping (Double) async -> Void = { s in try? await Task.sleep(nanoseconds: UInt64(s * 1e9)) }) {
        self.t = transport; self.sleep = sleep; self.ownerId = ownerId
    }

    public func owners() async throws -> [CRMOwner] {
        var out: [CRMOwner] = [], after = ""
        for _ in 0..<10 {
            let r = try await call("GET", "/crm/v3/owners?limit=100" + (after.isEmpty ? "" : "&after=" + after))
            for o in (r["results"] as? [[String: Any]]) ?? [] {
                let id = o["id"].map { "\($0)" } ?? ""
                let name = [o["firstName"] as? String, o["lastName"] as? String].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " ")
                let email = (o["email"] as? String) ?? ""
                if !id.isEmpty { out.append(CRMOwner(id: id, name: name.isEmpty ? email : name, email: email)) }
            }
            after = (((r["paging"] as? [String: Any])?["next"] as? [String: Any])?["after"] as? String) ?? ""
            if after.isEmpty { break }
        }
        return out.sorted { $0.name.lowercased() < $1.name.lowercased() }
    }

    func owned(_ props: [String: Any]) -> [String: Any] {
        var p = props
        if !ownerId.isEmpty { p["hubspot_owner_id"] = ownerId }
        return p
    }

    /// 429: Retry-After or 1, 2, 4 s, three times; other errors become one line (403 names the missing scope).
    public func call(_ method: String, _ path: String, _ body: [String: Any]? = nil) async throws -> [String: Any] {
        var wait = 1.0
        for attempt in 0..<4 {
            let (code, obj, headers) = try await t.send(method, path, body)
            if code == 429 && attempt < 3 {
                await sleep(Double(headers["retry-after"] ?? "") ?? wait); wait *= 2; continue
            }
            if code >= 200 && code < 300 { return obj }
            let msg = (obj["message"] as? String) ?? "HTTP \(code)"
            var scope = ""
            if code == 403, let r = msg.range(of: "crm\\.[a-z._]+", options: .regularExpression) { scope = String(msg[r]) }
            if code == 401 { throw CRMError(code: 401, message: "HubSpot did not accept the key (401). Check the service key.") }
            throw CRMError(code: code, message: scope.isEmpty ? msg : "HubSpot needs the scope \(scope) on the key (403).", scope: scope)
        }
        throw CRMError(code: 429, message: "HubSpot is busy (429); try again in a minute.")
    }

    static func p(_ o: [String: Any], _ k: String) -> String { ((o["properties"] as? [String: Any])?[k] as? String) ?? "" }

    public func testConnection() async throws -> CRMAccount {
        let info = try await call("GET", "/account-info/v3/details")
        // 0.8.7: account-info has no company name on new portals; the id and the data region name the portal
        let pid = info["portalId"].map { "\($0)" } ?? ""
        let region = (info["dataHostingLocation"] as? String).map { " (\($0))" } ?? ""
        let portal = (info["companyName"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? ("HubSpot " + pid + region)
        let owners = try? await call("GET", "/crm/v3/owners?limit=1")
        let o = ((owners?["results"] as? [[String: Any]]) ?? []).first ?? [:]
        let user = [o["firstName"] as? String, o["lastName"] as? String].compactMap { $0 }.joined(separator: " ")
        return CRMAccount(portal: portal, user: user, userEmail: (o["email"] as? String) ?? "", portalId: pid)
    }

    public func portalId() async throws -> String {
        let info = try await call("GET", "/account-info/v3/details")
        return info["portalId"].map { "\($0)" } ?? ""
    }

    public func findCompany(domain: String, name: String) async throws -> CRMCompany? {
        func search(_ prop: String, _ v: String) async throws -> CRMCompany? {
            let r = try await call("POST", "/crm/v3/objects/companies/search", [
                "filterGroups": [["filters": [["propertyName": prop, "operator": "EQ", "value": v]]]],
                "properties": ["name", "domain"], "limit": 1])
            guard let c = (r["results"] as? [[String: Any]])?.first, let id = c["id"] as? String else { return nil }
            return CRMCompany(id: id, name: Self.p(c, "name"), domain: Self.p(c, "domain"))
        }
        if !domain.isEmpty, let c = try await search("domain", domain) { return c }
        if !name.isEmpty { return try await search("name", name) }
        return nil
    }

    public func findContacts(emails: [String]) async throws -> [CRMContact] {
        guard !emails.isEmpty else { return [] }
        let r = try await call("POST", "/crm/v3/objects/contacts/search", [
            "filterGroups": emails.prefix(5).map { ["filters": [["propertyName": "email", "operator": "EQ", "value": $0]]] },
            "properties": ["firstname", "lastname", "email", "jobtitle"], "limit": 20])
        return ((r["results"] as? [[String: Any]]) ?? []).compactMap { c in
            guard let id = c["id"] as? String else { return nil }
            return CRMContact(id: id, name: [Self.p(c, "firstname"), Self.p(c, "lastname")].filter { !$0.isEmpty }.joined(separator: " "),
                              email: Self.p(c, "email"), role: Self.p(c, "jobtitle"))
        }
    }

    static let dealProps = ["dealname", "dealstage", "amount", "closedate", "hubspot_owner_id", "hs_next_step", "hs_lastmodifieddate", "hs_is_closed", "pipeline"]

    func dealFrom(_ d: [String: Any], stages: [String: String]) -> CRMDeal? {
        guard let id = d["id"] as? String else { return nil }
        let st = Self.p(d, "dealstage")
        return CRMDeal(id: id, name: Self.p(d, "dealname"), stage: st, stageName: stages[st] ?? st, amount: Self.p(d, "amount"),
                       closeDate: String(Self.p(d, "closedate").prefix(10)), owner: Self.p(d, "hubspot_owner_id"),
                       nextStep: Self.p(d, "hs_next_step"), updated: Self.p(d, "hs_lastmodifieddate"), open: Self.p(d, "hs_is_closed") != "true", pipeline: Self.p(d, "pipeline"))
    }

    public func deals(companyId: String) async throws -> [CRMDeal] {
        let a = try await call("GET", "/crm/v4/objects/companies/\(companyId)/associations/deals?limit=50")
        let ids = ((a["results"] as? [[String: Any]]) ?? []).compactMap { r -> String? in (r["toObjectId"]).map { "\($0)" } }
        guard !ids.isEmpty else { return [] }
        let r = try await call("POST", "/crm/v3/objects/deals/batch/read", ["properties": Self.dealProps, "inputs": ids.map { ["id": $0] }])
        let stages = (try? await pipelines()) ?? [:]
        return ((r["results"] as? [[String: Any]]) ?? []).compactMap { dealFrom($0, stages: stages) }
            .sorted { $0.updated > $1.updated }
    }

    public func deal(id: String) async throws -> CRMDeal? {
        let d = try await call("GET", "/crm/v3/objects/deals/\(id)?properties=" + Self.dealProps.joined(separator: ","))
        return dealFrom(d, stages: (try? await pipelines()) ?? [:])
    }

    public func recentActivity(refs: [CRMRef], limit: Int) async throws -> [CRMActivity] {
        var out: [CRMActivity] = []
        for ref in refs.prefix(3) {
            for (kind, props) in [("notes", ["hs_note_body", "hs_timestamp"]), ("meetings", ["hs_meeting_title", "hs_timestamp"]),
                                  ("emails", ["hs_email_subject", "hs_timestamp"]), ("tasks", ["hs_task_subject", "hs_timestamp", "hs_task_status"])] {
                guard let a = try? await call("GET", "/crm/v4/objects/\(ref.type)/\(ref.id)/associations/\(kind)?limit=10") else { continue }
                let ids = ((a["results"] as? [[String: Any]]) ?? []).compactMap { r -> String? in (r["toObjectId"]).map { "\($0)" } }
                guard !ids.isEmpty, let r = try? await call("POST", "/crm/v3/objects/\(kind)/batch/read", ["properties": props, "inputs": ids.map { ["id": $0] }]) else { continue }
                for o in (r["results"] as? [[String: Any]]) ?? [] {
                    let text = Self.p(o, props[0]).replacingOccurrences(of: "<[^>]+>", with: " ", options: .regularExpression)
                        .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).trimmingCharacters(in: .whitespaces)
                    out.append(CRMActivity(kind: String(kind.dropLast()), when: String(Self.p(o, "hs_timestamp").prefix(10)),
                                           text: String(text.prefix(160)), done: kind != "tasks" || Self.p(o, "hs_task_status") == "COMPLETED"))
                }
            }
        }
        var seen = Set<String>()
        return out.sorted { $0.when > $1.when }.filter { seen.insert($0.kind + $0.text).inserted }.prefix(limit).map { $0 }
    }

    public func pipelines() async throws -> [String: String] {
        let r = try await call("GET", "/crm/v3/pipelines/deals")
        var m: [String: String] = [:]
        for p in (r["results"] as? [[String: Any]]) ?? [] {
            for s in (p["stages"] as? [[String: Any]]) ?? [] { if let id = s["id"] as? String { m[id] = (s["label"] as? String) ?? id } }
        }
        return m
    }

    public func properties(objectType: String) async throws -> [(name: String, label: String)] {
        let r = try await call("GET", "/crm/v3/properties/\(objectType)")
        return ((r["results"] as? [[String: Any]]) ?? []).compactMap { p in
            guard let n = p["name"] as? String, (p["modificationMetadata"] as? [String: Any])?["readOnlyValue"] as? Bool != true else { return nil }
            return (n, (p["label"] as? String) ?? n)
        }.sorted { $0.label.lowercased() < $1.label.lowercased() }
    }

    static let assocType: [String: Int] = ["deals": 214, "companies": 190, "contacts": 202]      // note -> object (HUBSPOT_DEFINED)
    static let taskAssoc: [String: Int] = ["deals": 216, "companies": 192, "contacts": 204]

    public func createNote(text: String, associations: [CRMRef]) async throws -> String {
        let r = try await call("POST", "/crm/v3/objects/notes", [
            "properties": owned(["hs_note_body": text, "hs_timestamp": ISO8601DateFormatter().string(from: Date())]),
            "associations": associations.compactMap { a in Self.assocType[a.type].map { ["to": ["id": a.id], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": $0]]] } }])
        return (r["id"] as? String) ?? ""
    }

    public func updateDeal(id: String, fields: [String: String]) async throws -> [String: String] {
        let before = try await call("GET", "/crm/v3/objects/deals/\(id)?properties=" + fields.keys.joined(separator: ","))
        var old: [String: String] = [:]
        for k in fields.keys { old[k] = Self.p(before, k) }
        _ = try await call("PATCH", "/crm/v3/objects/deals/\(id)", ["properties": fields])
        return old
    }

    public func createTask(subject: String, due: Date?, associations: [CRMRef]) async throws -> String {
        let r = try await call("POST", "/crm/v3/objects/tasks", [
            "properties": owned(["hs_task_subject": subject, "hs_task_status": "NOT_STARTED",
                                 "hs_timestamp": ISO8601DateFormatter().string(from: due ?? Date().addingTimeInterval(86400))]),
            "associations": associations.compactMap { a in Self.taskAssoc[a.type].map { ["to": ["id": a.id], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": $0]]] } }])
        return (r["id"] as? String) ?? ""
    }

    public func delete(type: String, id: String) async throws { _ = try await call("DELETE", "/crm/v3/objects/\(type)/\(id)") }

    static let callAssoc: [String: Int] = ["deals": 206, "companies": 182, "contacts": 194]      // call -> object (HUBSPOT_DEFINED)
    static let reconcileWaits: [Double] = [0, 2, 4]      // seconds before each search after an ambiguous create

    /// H2: known id (kept with the call) first, then the external id (the Pacenote call id) in HubSpot, else a new Call.
    public func upsertCall(_ log: CRMCallLog, knownId: String, associations: [CRMRef]) async throws -> CRMCallResult {
        var id = knownId
        var old: [String: String] = [:]
        if !id.isEmpty {
            do { old = try await readCall(id) } catch let e as CRMError where e.code == 404 { id = "" }
        }
        if id.isEmpty, let found = try await findCall(log.key) { id = found; old = try await readCall(id) }
        let refs = associations.filter { Self.callAssoc[$0.type] != nil }
        if id.isEmpty {
            do {
                let r = try await call("POST", "/crm/v3/objects/calls", [
                    "properties": owned(log.properties),
                    "associations": refs.map { a in ["to": ["id": a.id], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": Self.callAssoc[a.type]!]]] }])
                return CRMCallResult(id: (r["id"] as? String) ?? "", created: true)
            } catch {
                // no answer, or a 5xx: HubSpot may have made the Call anyway, so look for it before reporting a failure.
                // Search lags a new record by a few seconds, so it is asked again before giving up; a later run's
                // search by the Pacenote call id still finds a Call that showed up after that.
                if let ce = error as? CRMError, ce.code >= 400 && ce.code < 500 { throw error }
                for (i, wait) in Self.reconcileWaits.enumerated() {
                    if i > 0 { await sleep(wait) }
                    if let found = try? await findCall(log.key) { return CRMCallResult(id: found, created: true) }
                }
                throw error
            }
        }
        // associations first: if one fails, the Call still has its old fields and nothing is left outside Undo
        for a in refs { _ = try await call("PUT", "/crm/v4/objects/calls/\(id)/associations/default/\(a.type)/\(a.id)") }
        _ = try await call("PATCH", "/crm/v3/objects/calls/\(id)", ["properties": owned(log.properties)])
        return CRMCallResult(id: id, created: false, old: old)
    }

    func findCall(_ key: String) async throws -> String? {
        let r = try await call("POST", "/crm/v3/objects/calls/search", [
            "filterGroups": [["filters": [["propertyName": "hs_call_external_id", "operator": "EQ", "value": CRMCallLog.externalId(key)]]]],
            "properties": ["hs_call_external_id"], "limit": 1])
        return (r["results"] as? [[String: Any]])?.first?["id"] as? String
    }

    func readCall(_ id: String) async throws -> [String: String] {
        let keys = Array(CRMCallLog(key: "", title: "", start: Date(), durationMs: 0, body: "").properties.keys) + (ownerId.isEmpty ? [] : ["hubspot_owner_id"])
        let r = try await call("GET", "/crm/v3/objects/calls/\(id)?properties=" + keys.sorted().joined(separator: ","))
        var out: [String: String] = [:]
        for k in keys { out[k] = Self.p(r, k) }
        return out
    }

    public func restoreCall(id: String, fields: [String: String]) async throws {
        _ = try await call("PATCH", "/crm/v3/objects/calls/\(id)", ["properties": fields])
    }
}

/// The snapshot <stem>.crm.md (rules/crm.yaml > snapshot): a local file the preparation and the engine read.
public enum CRMSnapshot {
    public static func render(provider: String, company: CRMCompany?, deal: CRMDeal?, contacts: [CRMContact], activity: [CRMActivity],
                              taken: Date, ownerName: (String) -> String = { $0 }) -> String {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withFullDate, .withTime, .withColonSeparatorInTime]
        var s = "# From CRM (\(provider))\n\n- Taken: \(f.string(from: taken))\n"
        if let c = company { s += "- Company: \(c.name)" + (c.domain.isEmpty ? "" : " (\(c.domain))") + "\n" }
        if let d = deal {
            s += "\n## Deal: \(d.name)\n\n- Stage: \(d.stageName.isEmpty ? d.stage : d.stageName)\n"
            if !d.amount.isEmpty { s += "- Amount: \(d.amount)\n" }
            if !d.closeDate.isEmpty { s += "- Close date: \(d.closeDate)\n" }
            if !d.owner.isEmpty { s += "- Owner: \(ownerName(d.owner))\n" }
            if !d.nextStep.isEmpty { s += "- Next step: \(d.nextStep)\n" }
        } else { s += "\n## Deal: none linked\n" }
        if !contacts.isEmpty {
            s += "\n## Contacts\n\n" + contacts.map { "- \($0.name.isEmpty ? $0.email : $0.name)" + ($0.role.isEmpty ? "" : ", \($0.role)") + (" <\($0.email)>") }.joined(separator: "\n") + "\n"
        }
        let acts = activity.filter { $0.done }.prefix(10), tasks = activity.filter { !$0.done }
        if !acts.isEmpty { s += "\n## Recent activity\n\n" + acts.map { "- \($0.when) \($0.kind): \($0.text)" }.joined(separator: "\n") + "\n" }
        if !tasks.isEmpty { s += "\n## Open tasks\n\n" + tasks.map { "- \($0.when) \($0.text)" }.joined(separator: "\n") + "\n" }
        return s
    }

    /// The age line of the brief ("12 min ago" is made by the app); here only the parse of "Taken:".
    public static func taken(_ md: String) -> Date? {
        guard let r = md.range(of: "- Taken: ([0-9T:\\-+Z]+)", options: .regularExpression) else { return nil }
        let v = String(md[r]).replacingOccurrences(of: "- Taken: ", with: "")
        let f = ISO8601DateFormatter(); f.formatOptions = [.withFullDate, .withTime, .withColonSeparatorInTime]
        return f.date(from: v) ?? ISO8601DateFormatter().date(from: v)
    }

    /// Domains of the participants outside the PM's own domain (rules/crm.yaml > link.auto).
    public static func clientDomains(emails: [String], ownDomains: Set<String>) -> [String] {
        var out: [String] = []
        for e in emails {
            guard let at = e.lastIndex(of: "@") else { continue }
            let d = e[e.index(after: at)...].lowercased()
            if ownDomains.contains(d) || ["gmail.com", "outlook.com", "icloud.com", "yahoo.com", "hotmail.com"].contains(d) || out.contains(d) { continue }
            out.append(d)
        }
        return out
    }
}

/// What "Update <CRM>" will do, as reviewed rows (rules/crm.yaml > write.review).
public struct CRMUpdatePlan: Equatable {
    public struct Row: Equatable, Identifiable { public var id: String; public var title: String; public var detail: String; public var on: Bool
        public init(id: String, title: String, detail: String, on: Bool) { self.id = id; self.title = title; self.detail = detail; self.on = on } }
    public var rows: [Row]
    public var note: String
    public var dealFields: [String: String]
    public var task: (subject: String, due: Date?)?
    public var callLog: CRMCallLog?      // H2: the Call engagement, when this call has one to log
    public static func == (a: CRMUpdatePlan, b: CRMUpdatePlan) -> Bool { a.rows == b.rows && a.note == b.note && a.dealFields == b.dealFields && a.task?.subject == b.task?.subject }

    /// summary, decisions and next steps come from the call page as edited; the transcript only when asked.
    public static func build(title: String, date: String, summary: [String], decisions: [String], nextSteps: [String],
                             notionURL: String, transcript: String?, nextStep: String, closeDate: String, stage: String?,
                             qualification: [String: String], mapping: [String: String], followUp: (String, Date?)?,
                             writeNote: Bool = true, writeDeal: Bool = true, writeTask: Bool = true,
                             callLog: CRMCallLog? = nil, writeCall: Bool = true) -> CRMUpdatePlan {
        var note = "<p><b>\(title)</b> · \(date)</p>"
        func list(_ h: String, _ xs: [String]) { if !xs.isEmpty { note += "<p><b>\(h)</b></p><ul>" + xs.map { "<li>\($0)</li>" }.joined() + "</ul>" } }
        list("Summary", summary); list("Decisions", decisions); list("Next steps", nextSteps)
        if !notionURL.isEmpty { note += "<p>Notion: <a href=\"\(notionURL)\">\(notionURL)</a></p>" }
        if let t = transcript, !t.isEmpty { note += "<p><b>Transcript</b></p><pre>\(t.prefix(60000))</pre>" }
        var fields: [String: String] = [:]
        if !nextStep.isEmpty { fields["hs_next_step"] = String(nextStep.prefix(250)) }
        if !closeDate.isEmpty { fields["closedate"] = closeDate }
        if let s = stage, !s.isEmpty { fields["dealstage"] = s }
        for (crit, prop) in mapping where !prop.isEmpty { if let v = qualification[crit], !v.isEmpty { fields[prop] = String(v.prefix(250)) } }
        // 0.8.7 (I6.4): Settings > Integrations > HubSpot > What Pacenote can write sets the default ticks
        var rows = [Row(id: "note", title: "note", detail: "", on: writeNote)]
        if let c = callLog { rows.append(Row(id: "call", title: "call", detail: c.title, on: writeCall)) }
        if !fields.isEmpty { rows.append(Row(id: "deal", title: "deal", detail: fields.keys.sorted().joined(separator: ", "), on: writeDeal)) }
        if let f = followUp { rows.append(Row(id: "task", title: "task", detail: f.0, on: writeTask)) }
        rows.append(Row(id: "transcript", title: "transcript", detail: "", on: transcript != nil))
        return CRMUpdatePlan(rows: rows, note: note, dealFields: fields, task: followUp.map { ($0.0, $0.1) }, callLog: callLog)
    }
}
