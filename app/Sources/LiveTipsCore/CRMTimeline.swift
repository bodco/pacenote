import Foundation

/// H5 (PB-61, docs/specs/hubspot-crm-integration.md): the call's Meeting and the sent follow-up Email on the CRM
/// timeline. Own file on purpose: the connector protocol in CRM.swift is not changed; the app reaches this through
/// `connector() as? CRMTimelineWriter`. Nothing here writes on its own: the app calls it after the PM's Apply, never
/// from Auto, and never with transcript text.

/// The Meeting of a finished call: the call page's notes on the calendar slot the call held.
public struct CRMMeetingLog: Equatable {
    public var key: String              // the Pacenote call id (<stem>.crm.json, callKey), also named in the notes
    public var title: String
    public var start: Date
    public var end: Date
    public var notes: String            // Summary, Decisions, Next steps, link back; see `notes(...)`
    /// How far apart two starts may be and still be the same meeting (calendar sync and the call start differ by minutes).
    public static let tolerance: TimeInterval = 15 * 60
    public static let heading = "<p><b>Pacenote notes</b></p>"

    public init(key: String, title: String, start: Date, end: Date, notes: String) {
        self.key = key; self.title = title; self.start = start; self.end = max(end, start); self.notes = notes
    }

    /// The text comes from the call page as edited; the transcript is not an input on purpose.
    public static func notes(summary: [String], decisions: [String], nextSteps: [String], link: String, key: String) -> String {
        var b = heading
        func list(_ h: String, _ xs: [String]) { if !xs.isEmpty { b += "<p><b>\(h)</b></p><ul>" + xs.map { "<li>\(CRMCallLog.esc($0))</li>" }.joined() + "</ul>" } }
        list("Summary", summary); list("Decisions", decisions); list("Next steps", nextSteps)
        if !link.isEmpty { b += "<p>Call page: <a href=\"\(CRMCallLog.esc(link))\">\(CRMCallLog.esc(link))</a></p>" }
        b += "<p>Pacenote call id: \(CRMCallLog.esc(key))</p>"
        return b
    }

    /// Our block is always the last part of the field: a re-run replaces it, the PM's or HubSpot's own text stays above it.
    public static func merge(existing: String, notes: String) -> String {
        var keep = existing
        // HubSpot may rewrite the markup of a rich-text field (<strong> for <b>), so find the heading by its words and
        // cut at the tag that opens it
        if let r = existing.range(of: "Pacenote notes") {
            let head = existing[..<r.lowerBound]
            let cut = head.range(of: "<p", options: .backwards)?.lowerBound ?? r.lowerBound
            keep = String(existing[..<cut])
        }
        return keep.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? notes : keep + notes
    }

    var properties: [String: Any] {
        let f = ISO8601DateFormatter()
        return ["hs_timestamp": f.string(from: start), "hs_meeting_title": title, "hs_meeting_start_time": f.string(from: start),
                "hs_meeting_end_time": f.string(from: end), "hs_meeting_outcome": "COMPLETED"]
    }
}

/// The follow-up email the PM sent (Pacenote itself never sends mail). The text is the draft as the PM edited it.
public struct CRMEmailLog: Equatable {
    public var key: String
    public var subject: String
    public var text: String
    public var sent: Date
    public var from: String             // optional; never stored, only sent
    public var to: [String]
    public init(key: String, subject: String, text: String, sent: Date = Date(), from: String = "", to: [String] = []) {
        self.key = key; self.subject = subject; self.text = text; self.sent = sent; self.from = from; self.to = to
    }
    /// A plain text draft shown as HTML paragraphs in the timeline.
    var html: String {
        text.components(separatedBy: "\n\n").map { "<p>" + CRMCallLog.esc($0).replacingOccurrences(of: "\n", with: "<br>") + "</p>" }.joined()
    }
    var headers: String? {
        guard !from.isEmpty || !to.isEmpty else { return nil }
        var h: [String: Any] = [:]
        if !from.isEmpty { h["from"] = ["email": from] }
        if !to.isEmpty { h["to"] = to.map { ["email": $0] } }
        return (try? JSONSerialization.data(withJSONObject: h)).flatMap { String(data: $0, encoding: .utf8) }
    }
    var properties: [String: Any] {
        var p: [String: Any] = ["hs_timestamp": ISO8601DateFormatter().string(from: sent), "hs_email_direction": "EMAIL", "hs_email_status": "SENT",
                                "hs_email_subject": subject, "hs_email_text": text, "hs_email_html": html]
        if let h = headers { p["hs_email_headers"] = h }
        return p
    }
}

/// created: a new engagement (Undo deletes it). attached: HubSpot already had it (its own calendar sync or an email
/// logged by hand), so only the notes were added. old: the values an update replaced, for Undo; empty when nothing
/// was changed, in which case Undo must not touch the object.
public struct CRMTimelineResult: Equatable { public var id: String; public var created: Bool; public var attached: Bool; public var old: [String: String]
    public init(id: String, created: Bool, attached: Bool = false, old: [String: String] = [:]) { self.id = id; self.created = created; self.attached = attached; self.old = old } }

public protocol CRMTimelineWriter {
    func upsertMeeting(_ log: CRMMeetingLog, knownId: String, associations: [CRMRef]) async throws -> CRMTimelineResult
    func upsertEmail(_ log: CRMEmailLog, knownId: String, associations: [CRMRef]) async throws -> CRMTimelineResult
    func restore(type: String, id: String, fields: [String: String]) async throws
}

extension HubSpotConnector: CRMTimelineWriter {
    static let meetingAssoc: [String: Int] = ["deals": 212, "companies": 188, "contacts": 200]      // meeting -> object (HUBSPOT_DEFINED)
    static let emailAssoc: [String: Int] = ["deals": 210, "companies": 186, "contacts": 198]        // email -> object (HUBSPOT_DEFINED)
    static let meetingNotes = "hs_internal_meeting_notes"                                          // internal notes: the invite text stays the PM's

    private static func assocBody(_ refs: [CRMRef], _ ids: [String: Int]) -> [[String: Any]] {
        refs.compactMap { a in ids[a.type].map { ["to": ["id": a.id], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": $0]]] } }
    }

    private func associate(_ type: String, _ id: String, _ refs: [CRMRef], _ ids: [String: Int]) async throws {
        for a in refs where ids[a.type] != nil { _ = try await call("PUT", "/crm/v4/objects/\(type)/\(id)/associations/default/\(a.type)/\(a.id)") }
    }

    /// One search group per linked object (HubSpot allows five): the engagement is on the timeline of any of them.
    private func timelineFilters(_ refs: [CRMRef], _ extra: [[String: Any]]) -> [[String: Any]] {
        let kinds = ["contacts": "contact", "companies": "company", "deals": "deal"]
        return refs.filter { kinds[$0.type] != nil }.prefix(5).map { ["filters": extra + [["propertyName": "associations." + kinds[$0.type]!, "operator": "EQ", "value": $0.id]]] }
    }

    /// HubSpot returns ISO 8601 with milliseconds, or epoch milliseconds.
    public static func time(_ v: String, or fallback: Date) -> Date {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.date(from: v) ?? ISO8601DateFormatter().date(from: v) ?? Double(v).map { Date(timeIntervalSince1970: $0 / 1000) } ?? fallback
    }

    private func read(_ type: String, _ id: String, _ keys: [String]) async throws -> [String: String] {
        let r = try await call("GET", "/crm/v3/objects/\(type)/\(id)?properties=" + keys.sorted().joined(separator: ","))
        var out: [String: String] = [:]
        for k in keys { out[k] = Self.p(r, k) }
        return out
    }

    /// The known id (kept with the call) first; else a Meeting already on the same contacts, company or deal within 15
    /// minutes of the start (HubSpot's own calendar sync, or ours from an earlier run): the notes go into it. Else a new one.
    public func upsertMeeting(_ log: CRMMeetingLog, knownId: String, associations: [CRMRef]) async throws -> CRMTimelineResult {
        let keys = [Self.meetingNotes, "hs_meeting_title", "hs_meeting_start_time", "hs_meeting_end_time", "hs_meeting_outcome"] + (ownerId.isEmpty ? [] : ["hubspot_owner_id"])
        var id = knownId
        var old: [String: String] = [:]
        if !id.isEmpty {
            do { old = try await read("meetings", id, keys) } catch let e as CRMError where e.code == 404 { id = "" }
        }
        var attached = false
        if id.isEmpty {
            let lo = Int((log.start.timeIntervalSince1970 - CRMMeetingLog.tolerance) * 1000), hi = Int((log.start.timeIntervalSince1970 + CRMMeetingLog.tolerance) * 1000)
            let groups = timelineFilters(associations, [["propertyName": "hs_timestamp", "operator": "GTE", "value": String(lo)],
                                                        ["propertyName": "hs_timestamp", "operator": "LTE", "value": String(hi)]])
            if !groups.isEmpty {
                let r = try await call("POST", "/crm/v3/objects/meetings/search", ["filterGroups": groups, "properties": ["hs_timestamp"], "limit": 10])
                let hits = ((r["results"] as? [[String: Any]]) ?? []).compactMap { h -> (String, Double)? in
                    guard let hid = h["id"] as? String else { return nil }
                    return (hid, abs(Self.time(Self.p(h, "hs_timestamp"), or: log.start).timeIntervalSince(log.start)))
                }
                if let best = hits.min(by: { $0.1 < $1.1 }) { id = best.0; attached = true; old = try await read("meetings", id, keys) }
            }
        }
        if id.isEmpty {
            let r = try await call("POST", "/crm/v3/objects/meetings", [
                "properties": owned(log.properties.merging([Self.meetingNotes: log.notes]) { a, _ in a }),
                "associations": Self.assocBody(associations, Self.meetingAssoc)])
            return CRMTimelineResult(id: (r["id"] as? String) ?? "", created: true)
        }
        let merged = CRMMeetingLog.merge(existing: old[Self.meetingNotes] ?? "", notes: log.notes)
        _ = try await call("PATCH", "/crm/v3/objects/meetings/\(id)", ["properties": [Self.meetingNotes: merged]])
        try await associate("meetings", id, associations, Self.meetingAssoc)
        return CRMTimelineResult(id: id, created: false, attached: attached, old: [Self.meetingNotes: old[Self.meetingNotes] ?? ""])
    }

    /// Known id: update it. Else an email with the same subject already on the same contacts, company or deal in the
    /// last day (HubSpot's own mail logging) is left alone: nothing is written and Undo has nothing to undo. Else new.
    public func upsertEmail(_ log: CRMEmailLog, knownId: String, associations: [CRMRef]) async throws -> CRMTimelineResult {
        let keys = ["hs_email_subject", "hs_email_text", "hs_email_html", "hs_email_headers", "hs_timestamp"]
        var id = knownId
        var old: [String: String] = [:]
        if !id.isEmpty {
            do { old = try await read("emails", id, keys) } catch let e as CRMError where e.code == 404 { id = "" }
        }
        if id.isEmpty {
            let since = Int((log.sent.timeIntervalSince1970 - 86400) * 1000)
            let groups = timelineFilters(associations, [["propertyName": "hs_email_subject", "operator": "EQ", "value": log.subject],
                                                        ["propertyName": "hs_timestamp", "operator": "GTE", "value": String(since)]])
            if !groups.isEmpty {
                let r = try await call("POST", "/crm/v3/objects/emails/search", ["filterGroups": groups, "properties": ["hs_email_subject"], "limit": 1])
                if let found = ((r["results"] as? [[String: Any]])?.first?["id"]) as? String {
                    try await associate("emails", found, associations, Self.emailAssoc)
                    return CRMTimelineResult(id: found, created: false, attached: true)
                }
            }
            let r = try await call("POST", "/crm/v3/objects/emails", ["properties": owned(log.properties), "associations": Self.assocBody(associations, Self.emailAssoc)])
            return CRMTimelineResult(id: (r["id"] as? String) ?? "", created: true)
        }
        _ = try await call("PATCH", "/crm/v3/objects/emails/\(id)", ["properties": owned(log.properties)])
        try await associate("emails", id, associations, Self.emailAssoc)
        return CRMTimelineResult(id: id, created: false, old: old)
    }

    public func restore(type: String, id: String, fields: [String: String]) async throws {
        _ = try await call("PATCH", "/crm/v3/objects/\(type)/\(id)", ["properties": fields])
    }
}
