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

    /// The property a spec property is written to; "" = don't write. Fixed and unknown properties stay as they are.
    public func property(_ o: CRMObject, _ specProperty: String) -> String {
        guard o.defaultFields.contains(where: { $0.property == specProperty }) else { return specProperty }
        return fields[o.rawValue]?[specProperty] ?? specProperty
    }

    /// Renames the mappable properties of a write and drops the ones set to Don't write.
    public func mapped(_ o: CRMObject, _ props: [String: Any]) -> [String: Any] {
        var out: [String: Any] = [:]
        for (k, v) in props { let t = property(o, k); if !t.isEmpty { out[t] = v } }
        return out
    }

    public mutating func setProperty(_ o: CRMObject, _ specProperty: String, to target: String) {
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
    public init(at: Date = Date(), object: String, action: String, source: String, hubspotId: String = "", fields: [String] = [],
                old: [String: String] = [:], ok: Bool = true, error: String = "") {
        self.at = at; self.object = object; self.action = action; self.source = source; self.hubspotId = hubspotId
        self.fields = fields; self.old = old; self.ok = ok; self.error = error
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
    }
}

public enum CRMSyncLog {
    /// Undo in Update HubSpot right after Apply (unchanged since 0.8.7).
    public static let applyUndoSeconds: TimeInterval = 8
    /// Undo from the sync log: within a day of the write, and only while nobody changed the object in HubSpot since.
    public static let logUndoSeconds: TimeInterval = 24 * 3600
    /// Clock difference allowed between the Mac and HubSpot when telling "changed since".
    public static let changedSlackSeconds: TimeInterval = 120
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

    public static func save(_ entries: [CRMSyncEntry], path: String) {
        try? FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        if let d = try? coder().0.encode(Array(entries.suffix(maxEntries))) { try? d.write(to: URL(fileURLWithPath: path), options: .atomic) }
    }

    /// Appends lines to the log of the call with this stem; the only writer other branches need.
    public static func append(_ new: [CRMSyncEntry], stem: String) {
        guard !new.isEmpty else { return }
        let p = path(stem: stem)
        save(load(path: p) + new, path: p)
    }

    /// Only the latest good write of an object can be undone (older old values would overwrite a newer write), within
    /// the log window, and not twice.
    public static func canUndo(_ e: CRMSyncEntry, in log: [CRMSyncEntry], now: Date) -> Bool {
        guard e.ok, !e.undone, e.action != "undo", !e.hubspotId.isEmpty, now.timeIntervalSince(e.at) <= logUndoSeconds else { return false }
        if e.action == "update" && e.old.isEmpty { return false }
        return !log.contains { $0.object == e.object && $0.hubspotId == e.hubspotId && $0.ok && $0.action != "undo" && $0.at > e.at }
    }

    /// A failed write can be retried until a later write of the same object succeeded.
    public static func canRetry(_ e: CRMSyncEntry, in log: [CRMSyncEntry]) -> Bool {
        guard !e.ok, e.action != "undo" else { return false }
        return !log.contains { $0.object == e.object && $0.ok && $0.action != "undo" && $0.at > e.at }
    }

    /// HubSpot's updatedAt of the object later than our write (plus the slack): someone changed it, Undo would overwrite it.
    public static func changedSince(updatedAt: String, writtenAt: Date) -> Bool {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let u = f.date(from: updatedAt) ?? ISO8601DateFormatter().date(from: updatedAt) else { return false }
        return u.timeIntervalSince(writtenAt) > changedSlackSeconds
    }

    static let rowObject: [(row: String, object: String)] = [("note", "notes"), ("call", "calls"), ("deal", "deals"), ("task", "tasks")]

    /// The lines of one Apply in Update HubSpot: one per written object (done: type, id, old values, in write order) and,
    /// on an error, one failed line for the first ticked object that was not written.
    public static func applyEntries(plan p: CRMUpdatePlan, dealLinked: Bool, done: [(String, String, [String: String])], error: String?,
                                    source: String, sync: CRMSyncSettings, now: Date = Date()) -> [CRMSyncEntry] {
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
            CRMSyncEntry(at: now, object: d.0, action: d.0 == "deals" || !d.2.isEmpty ? "update" : "create", source: source,
                         hubspotId: d.1, fields: fields(d.0, d.2), old: d.2)
        }
        if let e = error {
            let applicable = rowObject.filter { r in
                guard p.rows.first(where: { $0.id == r.row })?.on == true else { return false }
                switch r.row {
                case "call": return p.callLog != nil
                case "deal": return dealLinked && !p.dealFields.isEmpty
                case "task": return p.task != nil
                default: return true
                }
            }
            // an error after every known row was written came from another writer, which logs its own failure
            if let failed = applicable.first(where: { r in !done.contains { $0.0 == r.object } })?.object {
                out.append(CRMSyncEntry(at: now, object: failed, action: failed == "deals" ? "update" : "create", source: source,
                                        fields: fields(failed, [:]), ok: false, error: e))
            }
        }
        return out
    }

    /// The line of an Auto or Retry Call write.
    public static func callEntry(_ r: CRMCallResult, log: CRMCallLog?, source: String, sync: CRMSyncSettings, now: Date = Date()) -> CRMSyncEntry {
        CRMSyncEntry(at: now, object: "calls", action: r.created ? "create" : "update", source: source, hubspotId: r.id,
                     fields: log.map { Array(sync.mapped(.calls, $0.properties).keys).sorted() } ?? [], old: r.old)
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

    public static func recordUndo(object: String, hubspotId: String, error: String?, source: String, stem: String) {
        let p = path(stem: stem)
        save(withUndo(load(path: p), object: object, hubspotId: hubspotId, error: error, source: source), path: p)
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

    /// The scopes whose probe was refused with 403. Other failures (network, 5xx) do not mark a scope missing.
    public static func missing(_ hs: HubSpotConnector) async -> [String] {
        var out: [String] = []
        for (scope, path) in probes {
            do { _ = try await hs.call("GET", path) } catch let e as CRMError where e.code == 403 { out.append(scope) } catch {}
        }
        return out
    }

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
