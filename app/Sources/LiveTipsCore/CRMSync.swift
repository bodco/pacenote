import Foundation

/// H6 (PB-62, docs/specs/hubspot-crm-integration.md section 12): sync settings, field and owner mapping and the per-call
/// sync log. Other branches read the switches only through the small API at the bottom (`CRMSettings.autoWrites`,
/// `property`, `mapped`, `ownerId(forPacenoteUser:)`, `CRMSyncLog.append`).

/// The HubSpot objects with their own Auto switch. Notes have none on purpose (decision 9): they stay behind a click.
public enum CRMObject: String, CaseIterable, Codable {
    case calls, tasks, contacts, deals, meetings      // contacts = contacts and companies

    /// The scopes a write of this object needs (section 4): the reads its write path makes (matching, the deal read
    /// before an update, the owner), which the live check can prove, then the write scopes, which only a write proves.
    /// Calls, tasks and meetings are engagements that HubSpot creates with the contacts scopes; meetings may also need
    /// the meetings read scope on some portals.
    public var scopes: [String] {
        switch self {
        case .calls, .tasks: return ["crm.objects.contacts.read", "crm.objects.owners.read", "crm.objects.contacts.write"]
        case .contacts: return ["crm.objects.contacts.read", "crm.objects.companies.read", "crm.objects.contacts.write", "crm.objects.companies.write"]
        case .deals: return ["crm.objects.deals.read", "crm.objects.deals.write"]
        case .meetings: return ["crm.objects.contacts.read", "crm.objects.owners.read", "crm.objects.contacts.write", "crm.objects.meetings.read"]
        }
    }

    /// Mappable fields: the spec's HubSpot property (section 3) for each Pacenote field. Dedupe keys, the owner and
    /// statuses are fixed and never mapped (`fixed`).
    public var defaultFields: [(field: String, property: String)] {
        switch self {
        case .calls: return [("title", "hs_call_title"), ("body", "hs_call_body"), ("duration", "hs_call_duration"), ("direction", "hs_call_direction")]
        case .tasks: return [("subject", "hs_task_subject")]
        case .contacts: return [("firstName", "firstname"), ("lastName", "lastname"), ("role", "jobtitle"), ("company", "name")]
        case .deals: return [("nextStep", "hs_next_step"), ("closeDate", "closedate")]
        case .meetings: return [("title", "hs_meeting_title"), ("body", "hs_meeting_body"), ("start", "hs_meeting_start_time"), ("end", "hs_meeting_end_time")]
        }
    }

    public var fixed: [String] {
        switch self {
        case .calls: return ["hs_timestamp", "hs_call_status", "hs_call_disposition", "hs_call_external_id", "hubspot_owner_id"]
        case .tasks: return ["hs_timestamp", "hs_task_status", "hubspot_owner_id"]
        case .contacts: return ["email", "domain"]
        case .deals: return ["dealstage"]
        case .meetings: return ["hs_timestamp", "hubspot_owner_id"]
        }
    }

    /// The HubSpot object type whose properties can be targets (contacts and companies share one switch).
    public func objectType(field: String) -> String { self == .contacts && field == "company" ? "companies" : rawValue }

    /// Built today on this branch; the others are read by their own branches (H3 contacts, H4 deals, H5 meetings, Tracker tasks).
    public var hasWriter: Bool { self == .calls }
}

/// Stored in CRMSettings.sync. Every new key has a default, so an old settings file keeps working.
public struct CRMSyncSettings: Codable, Equatable {
    public var auto: [String: Bool] = [:]                  // object -> automatic write (calls live in CRMSettings.autoLogCall)
    public var fields: [String: [String: String]] = [:]    // object -> spec property -> property to write ("" = don't write)
    public var owners: [String: String] = [:]              // Pacenote user email (lowercase) -> HubSpot owner id
    public init() {}
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        auto = (try? c.decode([String: Bool].self, forKey: .auto)) ?? [:]
        fields = (try? c.decode([String: [String: String]].self, forKey: .fields)) ?? [:]
        owners = (try? c.decode([String: String].self, forKey: .owners)) ?? [:]
    }

    /// Properties no mapped field may write to: the object's fixed ones (dedupe keys, owner, status), HubSpot's own
    /// bookkeeping, and the default property of every other field of the object (that field still writes there).
    public static func reserved(_ o: CRMObject, except specProperty: String) -> Set<String> {
        Set(o.fixed + ["hubspot_owner_id", "hs_object_id", "hs_createdate", "hs_lastmodifieddate", "hs_call_external_id"]
            + o.defaultFields.map(\.property).filter { $0 != specProperty })
    }

    /// Every mappable field of the object and where it writes, in the spec's order. A stored target that is reserved,
    /// or that an earlier field already writes to, falls back to the field's default property.
    func resolved(_ o: CRMObject) -> [String: String] {
        var out: [String: String] = [:], used = Set<String>()
        for f in o.defaultFields {
            var t = fields[o.rawValue]?[f.property] ?? f.property
            if !t.isEmpty, t != f.property, Self.reserved(o, except: f.property).contains(t) || used.contains(t) { t = f.property }
            out[f.property] = t
            if !t.isEmpty { used.insert(t) }
        }
        return out
    }

    /// The property a spec property is written to; "" = don't write. Fixed and unknown properties stay as they are.
    public func property(_ o: CRMObject, _ specProperty: String) -> String {
        resolved(o)[specProperty] ?? specProperty
    }

    /// Whether a field may write to this property: its default, Don't write, or a property that is neither reserved
    /// nor the target of another field of the object (the picker offers only these; `setProperty` refuses the rest).
    public func canTarget(_ o: CRMObject, _ specProperty: String, _ target: String) -> Bool {
        if target == specProperty || target.isEmpty { return true }
        if Self.reserved(o, except: specProperty).contains(target) { return false }
        let r = resolved(o)
        return !o.defaultFields.contains { $0.property != specProperty && r[$0.property] == target }
    }

    /// Renames the mappable properties of a write and drops the ones set to Don't write. Properties that are not
    /// mappable keep their names and win: a mapped field that would land on one of them writes to its default instead,
    /// so no input overwrites another.
    public func mapped(_ o: CRMObject, _ props: [String: Any]) -> [String: Any] {
        let r = resolved(o)
        var out: [String: Any] = [:]
        for (k, v) in props where r[k] == nil { out[k] = v }
        for (k, v) in props {
            guard var t = r[k] else { continue }
            if t.isEmpty { continue }
            if out[t] != nil { t = k }
            out[t] = v
        }
        return out
    }

    public mutating func setProperty(_ o: CRMObject, _ specProperty: String, to target: String) {
        guard canTarget(o, specProperty, target) else { return }
        var m = fields[o.rawValue] ?? [:]
        if target == specProperty { m[specProperty] = nil } else { m[specProperty] = target }
        fields[o.rawValue] = m.isEmpty ? nil : m
    }

    public static func key(_ email: String) -> String { email.lowercased().trimmingCharacters(in: .whitespaces) }
}

/// The settings API other branches use. Calls read the existing H2 switches, so there is one source for the Call.
public extension CRMSettings {
    func autoWrites(_ o: CRMObject) -> Bool {
        o == .calls ? (autoLogCall && writeCall) : (sync.auto[o.rawValue] ?? false)
    }
    mutating func setAutoWrites(_ o: CRMObject, _ on: Bool) {
        if o == .calls { autoLogCall = on; if on { writeCall = true } } else { sync.auto[o.rawValue] = on ? true : nil }
    }
    func property(_ o: CRMObject, _ specProperty: String) -> String { sync.property(o, specProperty) }
    func mapped(_ o: CRMObject, _ props: [String: Any]) -> [String: Any] { sync.mapped(o, props) }

    /// The HubSpot owner of what Pacenote writes for a Pacenote user. Order: the mapping in Settings > Integrations >
    /// HubSpot > Sync; for the user of this Mac (one of `ownEmails`, or an empty email) the owner from Matching; a HubSpot
    /// owner with the same email; else "" (the field is left out and HubSpot applies its default, section 7).
    func ownerId(forPacenoteUser email: String, ownEmails: [String], owners: [CRMOwner]) -> String {
        let k = CRMSyncSettings.key(email)
        if let id = sync.owners[k], !id.isEmpty { return id }
        let mine = k.isEmpty || ownEmails.map(CRMSyncSettings.key).contains(k)
        if mine && !ownerId.isEmpty { return ownerId }
        if !k.isEmpty, let o = owners.first(where: { CRMSyncSettings.key($0.email) == k }) { return o.id }
        return ""
    }
}

/// One line of the call's sync log <stem>.crm-log.json: what was written, when and the result. Property names and
/// the old values Undo needs; never the key, never transcript text.
public struct CRMSyncEntry: Codable, Equatable, Identifiable {
    public var id = UUID().uuidString.lowercased()
    public var at: Date
    public var object: String              // calls, notes, tasks, deals, contacts, companies, meetings
    public var action: String              // create, update, undo
    public var source: String              // click, auto, retry
    public var hubspotId: String
    public var fields: [String]            // property names written
    public var old: [String: String]       // for an update: the values it replaced (Undo puts them back)
    public var ok: Bool
    public var error: String               // one line, as shown where the action was
    public var undone = false
    public var revision = ""               // HubSpot's updatedAt from the answer to this write; Undo needs it unchanged
    public var portal = ""                 // the HubSpot portal written to; Undo needs the key's portal to be this one
    public init(at: Date = Date(), object: String, action: String, source: String, hubspotId: String = "", fields: [String] = [],
                old: [String: String] = [:], ok: Bool = true, error: String = "", revision: String = "", portal: String = "") {
        self.at = at; self.object = object; self.action = action; self.source = source; self.hubspotId = hubspotId
        self.fields = fields; self.old = old; self.ok = ok; self.error = error; self.revision = revision; self.portal = portal
    }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString.lowercased()
        at = (try? c.decode(Date.self, forKey: .at)) ?? Date(timeIntervalSince1970: 0)
        object = (try? c.decode(String.self, forKey: .object)) ?? ""
        action = (try? c.decode(String.self, forKey: .action)) ?? ""
        source = (try? c.decode(String.self, forKey: .source)) ?? ""
        hubspotId = (try? c.decode(String.self, forKey: .hubspotId)) ?? ""
        fields = (try? c.decode([String].self, forKey: .fields)) ?? []
        old = (try? c.decode([String: String].self, forKey: .old)) ?? [:]
        ok = (try? c.decode(Bool.self, forKey: .ok)) ?? false
        error = (try? c.decode(String.self, forKey: .error)) ?? ""
        undone = (try? c.decode(Bool.self, forKey: .undone)) ?? false
        revision = (try? c.decode(String.self, forKey: .revision)) ?? ""
        portal = (try? c.decode(String.self, forKey: .portal)) ?? ""
    }
}

public enum CRMSyncLog {
    /// Undo in Update HubSpot right after Apply (unchanged since 0.8.7).
    public static let applyUndoSeconds: TimeInterval = 8
    /// Undo from the sync log: within a day of the write, and only while nobody changed the object in HubSpot since.
    public static let logUndoSeconds: TimeInterval = 24 * 3600
    public static let maxEntries = 200

    public static func path(stem: String) -> String { CallLayout.sidecar(stem, ".crm-log.json") }

    static func coder() -> (JSONEncoder, JSONDecoder) {
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; e.outputFormatting = [.prettyPrinted, .sortedKeys]
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        return (e, d)
    }

    public static func load(path: String) -> [CRMSyncEntry] {
        guard let data = FileManager.default.contents(atPath: path) else { return [] }
        return (try? coder().1.decode([CRMSyncEntry].self, from: data)) ?? []
    }

    /// false when the log could not be written: the HubSpot write itself still happened, so callers say both.
    @discardableResult
    public static func save(_ entries: [CRMSyncEntry], path: String) -> Bool {
        do {
            try FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
            try coder().0.encode(Array(entries.suffix(maxEntries))).write(to: URL(fileURLWithPath: path), options: .atomic)
            return true
        } catch { return false }
    }

    /// Appends lines to the log of the call with this stem; the only writer other branches need. false: not saved.
    @discardableResult
    public static func append(_ new: [CRMSyncEntry], stem: String) -> Bool {
        guard !new.isEmpty else { return true }
        let p = path(stem: stem)
        return save(load(path: p) + new, path: p)
    }

    /// Only the latest good write of an object can be undone (older old values would overwrite a newer write), within
    /// the log window, not twice, and only when HubSpot's revision of the write was kept (a line without one, such as
    /// a Call found again after a create with no answer, is never undone from the log), and only with the portal it
    /// was written to (Undo checks it is still the key's portal).
    public static func canUndo(_ e: CRMSyncEntry, in log: [CRMSyncEntry], now: Date) -> Bool {
        guard e.ok, !e.undone, e.action != "undo", !e.hubspotId.isEmpty, !e.revision.isEmpty, !e.portal.isEmpty,
              now.timeIntervalSince(e.at) <= logUndoSeconds else { return false }
        if e.action == "update" && e.old.isEmpty { return false }
        return !log.contains { $0.object == e.object && $0.hubspotId == e.hubspotId && $0.ok && $0.action != "undo" && $0.id != e.id && $0.at >= e.at }
    }

    /// The line as it is in the log on disk now, if it can still be undone (the click rechecks; the button may be stale).
    public static func undoable(id: String, stem: String, now: Date = Date()) -> CRMSyncEntry? {
        let log = load(path: path(stem: stem))
        guard let e = log.first(where: { $0.id == id }), canUndo(e, in: log, now: now) else { return nil }
        return e
    }

    /// A failed write can be retried until a later write of the same object succeeded.
    public static func canRetry(_ e: CRMSyncEntry, in log: [CRMSyncEntry]) -> Bool {
        guard !e.ok, e.action != "undo" else { return false }
        return !log.contains { $0.object == e.object && $0.ok && $0.action != "undo" && $0.at > e.at }
    }

    /// Undo may change the object only when HubSpot's updatedAt now is the one its answer to our write had: any later
    /// change (by a person, a workflow or another write) moves it. Missing or unreadable on either side: not verified, no Undo.
    public static func unchanged(revision: String, current: String) -> Bool {
        func date(_ s: String) -> Date? {
            let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            return f.date(from: s) ?? ISO8601DateFormatter().date(from: s)
        }
        guard let a = date(revision), let b = date(current) else { return false }
        return abs(a.timeIntervalSince(b)) < 0.001
    }

    public static let expiredCode = 410, changedCode = 412

    /// Checked by both Undo paths (right after Apply and in the log) before an object is changed: the Undo window is
    /// still open, and HubSpot's updatedAt is the one its answer to Pacenote's write had (nobody changed the object
    /// since). A revision that is missing or cannot be read stops the Undo. The window is checked again after the read.
    public static func guardUndo(type: String, id: String, revision: String, deadline: Date, connector c: CRMConnector,
                                 now: () -> Date = Date.init) async throws {
        let expired = CRMError(code: expiredCode, message: "The Undo window has passed.", scope: "undo")
        let changed = CRMError(code: changedCode, message: "Changed in HubSpot after Pacenote's write.", scope: "undo")
        guard now() < deadline else { throw expired }
        guard let hs = c as? HubSpotConnector, unchanged(revision: revision, current: try await hs.updatedAt(type: type, id: id)) else { throw changed }
        guard now() < deadline else { throw expired }
    }

    /// The lines of one Apply in Update HubSpot: one per written object (done, in write order) and, on an error, one
    /// failed line for `failed`, the object whose request was being sent when it failed ("" or nil: none was).
    public static func applyEntries(plan p: CRMUpdatePlan, done: [CRMUndoItem], error: String?, failed: String?, portal: String,
                                    source: String, sync: CRMSyncSettings, revisions: [String: String] = [:], now: Date = Date()) -> [CRMSyncEntry] {
        func fields(_ type: String, _ old: [String: String]) -> [String] {
            switch type {
            case "notes": return ["hs_note_body"]
            case "calls": return p.callLog.map { Array(sync.mapped(.calls, $0.properties).keys).sorted() } ?? []
            case "deals": return old.isEmpty ? Array(sync.mapped(.deals, p.dealFields).keys).sorted() : old.keys.sorted()
            case "tasks": return Array(sync.mapped(.tasks, ["hs_task_subject": "", "hs_task_status": "", "hs_timestamp": ""]).keys).sorted()
            default: return old.keys.sorted()
            }
        }
        // old values mean an update (Undo restores, never deletes); deals are always updates
        var out = done.map { d in
            CRMSyncEntry(at: now, object: d.type, action: d.type == "deals" || !d.old.isEmpty ? "update" : "create", source: source,
                         hubspotId: d.id, fields: fields(d.type, d.old), old: d.old, revision: revisions[d.type + "/" + d.id] ?? "", portal: portal)
        }
        // the row that was being sent, as Apply recorded it; never guessed from the plan (a skipped row is not a failure)
        if let e = error, let f = failed, !f.isEmpty {
            out.append(CRMSyncEntry(at: now, object: f, action: f == "deals" ? "update" : "create", source: source,
                                    fields: fields(f, [:]), ok: false, error: e, portal: portal))
        }
        return out
    }

    /// The line of an Auto or Retry Call write.
    public static func callEntry(_ r: CRMCallResult, log: CRMCallLog?, source: String, sync: CRMSyncSettings, revision: String = "",
                                 portal: String = "", now: Date = Date()) -> CRMSyncEntry {
        CRMSyncEntry(at: now, object: "calls", action: r.created ? "create" : "update", source: source, hubspotId: r.id,
                     fields: log.map { Array(sync.mapped(.calls, $0.properties).keys).sorted() } ?? [], old: r.old, revision: revision, portal: portal)
    }

    /// An Undo (in Update HubSpot or in the log): an undo line, and the latest good write of that object is marked undone.
    public static func withUndo(_ log: [CRMSyncEntry], object: String, hubspotId: String, error: String?, source: String, now: Date = Date()) -> [CRMSyncEntry] {
        var all = log
        if error == nil, let i = all.lastIndex(where: { $0.object == object && $0.hubspotId == hubspotId && $0.ok && $0.action != "undo" && !$0.undone }) {
            all[i].undone = true
        }
        all.append(CRMSyncEntry(at: now, object: object, action: "undo", source: source, hubspotId: hubspotId, ok: error == nil, error: error ?? ""))
        return all
    }

    @discardableResult
    public static func recordUndo(object: String, hubspotId: String, error: String?, source: String, stem: String) -> Bool {
        let p = path(stem: stem)
        return save(withUndo(load(path: p), object: object, hubspotId: hubspotId, error: error, source: source), path: p)
    }
}

/// The writes of one Apply in Update HubSpot, in order: note, Call, deal fields, task. `failed` is the object whose
/// request was out when the error came, so the sync log never blames a row that was skipped (a deal whose fields are
/// all set to Don't write sends nothing) or one that was already written.
public enum CRMApply {
    public struct Outcome { public var done: [CRMUndoItem] = []; public var fields = 0; public var error: Error? = nil; public var failed: String? = nil }

    public static func run(plan p: CRMUpdatePlan, dealId: String, refs: [CRMRef], connector c: CRMConnector, sync: CRMSyncSettings,
                           call: (CRMCallLog) async throws -> CRMCallResult) async -> Outcome {
        var out = Outcome()
        func on(_ row: String) -> Bool { p.rows.first { $0.id == row }?.on == true }
        var sending = ""
        do {
            if on("note") {
                sending = "notes"
                out.done.append(CRMUndoItem("notes", try await c.createNote(text: p.note, associations: refs)))
            }
            if on("call"), let entry = p.callLog {
                sending = "calls"
                let r = try await call(entry)
                out.done.append(CRMUndoItem("calls", r.id, r.old))       // a new Call: old is empty and Undo deletes it; an update: Undo restores
            }
            // deal fields that the mapping sets to Don't write are not a write (no request, no log line)
            if on("deal"), !dealId.isEmpty, dealId != "none", !sync.mapped(.deals, p.dealFields).isEmpty {
                sending = "deals"
                out.done.append(CRMUndoItem("deals", dealId, try await c.updateDeal(id: dealId, fields: p.dealFields)))
                out.fields = p.dealFields.count
            }
            if on("task"), let t = p.task {
                sending = "tasks"
                out.done.append(CRMUndoItem("tasks", try await c.createTask(subject: t.subject, due: t.due, associations: refs)))
            }
        } catch { out.error = error; out.failed = sending }
        return out
    }
}

/// The live check of the connection (Test in Settings and the Sync group): a read per scope that a read can prove.
/// Write scopes cannot be checked without writing; a 403 on the first write names the missing scope (rule 5).
public enum CRMScopeCheck {
    public static let probes: [(scope: String, path: String)] = [
        ("crm.objects.contacts.read", "/crm/v3/objects/contacts?limit=1"),
        ("crm.objects.companies.read", "/crm/v3/objects/companies?limit=1"),
        ("crm.objects.deals.read", "/crm/v3/objects/deals?limit=1"),
        ("crm.objects.owners.read", "/crm/v3/owners?limit=1"),
        ("crm.schemas.contacts.read", "/crm/v3/properties/contacts"),
        ("crm.schemas.companies.read", "/crm/v3/properties/companies"),
        ("crm.schemas.deals.read", "/crm/v3/properties/deals"),
    ]

    /// missing: the scopes whose probe was refused with 403. unverified: the probes that failed another way (network,
    /// 5xx), so the check is not complete for them; they are not called missing.
    public static func check(_ hs: HubSpotConnector) async -> (missing: [String], unverified: [String]) {
        var missing: [String] = [], unverified: [String] = []
        for (scope, path) in probes {
            do { _ = try await hs.call("GET", path) } catch let e as CRMError where e.code == 403 { missing.append(scope) } catch { unverified.append(scope) }
        }
        return (missing, unverified)
    }

    public static func missing(_ hs: HubSpotConnector) async -> [String] { await check(hs).missing }

    /// For one Auto switch: the scopes it needs that the last check found missing.
    public static func blocking(_ o: CRMObject, missing: [String]) -> [String] { o.scopes.filter { missing.contains($0) } }
}

public extension HubSpotConnector {
    /// HubSpot's last change time of an object (for Undo from the sync log); "" when HubSpot does not say.
    func updatedAt(type: String, id: String) async throws -> String {
        let r = try await call("GET", "/crm/v3/objects/\(type)/\(id)")
        return (r["updatedAt"] as? String) ?? ""
    }
}
