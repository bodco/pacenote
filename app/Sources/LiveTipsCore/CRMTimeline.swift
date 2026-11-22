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
    /// The calendar event the call held, known only by an identity captured for the call (`event(ref:...)`), never by
    /// being the nearest event. Only an order hint: a Meeting with its title or start is offered first, never written
    /// to without the PM's pick.
    public var eventTitle: String = ""
    public var eventStart: Date? = nil
    /// The PM's pick after Update HubSpot asked (`CRMTimelineResult.pick`): a Meeting id to add the notes to, or
    /// `newTarget` for a new Meeting. Empty: HubSpot is searched.
    public var target: String = ""
    /// The portal whose search offered the pick (`CRMTimelineSync.Outcome.portal`). A target is written only in that
    /// portal: after a Relink the same id may name an unrelated Meeting of another portal.
    public var targetPortal: String = ""
    public static let newTarget = "new"
    /// How far apart two starts may be and still be the same meeting (calendar sync and the call start differ by minutes).
    public static let tolerance: TimeInterval = 15 * 60
    /// A synced Meeting starts when the invite does; within this it is the calendar event's own start.
    public static let sameStart: TimeInterval = 60

    public init(key: String, title: String, start: Date, end: Date, notes: String, eventTitle: String = "", eventStart: Date? = nil) {
        self.key = key; self.title = title; self.start = start; self.end = max(end, start); self.notes = notes
        self.eventTitle = eventTitle; self.eventStart = eventStart
    }

    /// The call's calendar event by the id its Meeting Sheet stored (`meeting_ref.event_id`, set only when the sheet was
    /// made for that event): its title and start from the calendar, or from the sheet once the calendar no longer has
    /// it. No id: none. The event nearest the call is never taken for it: an ad hoc call at the time of another invite
    /// is not that invite, so a nearby Meeting is then only offered to pick.
    public static func event(ref: MeetingRef?, sheetTitle: String, calendar: [(id: String, title: String, start: Date)]) -> (title: String, start: Date?) {
        guard let id = ref?.eventId, !id.isEmpty else { return ("", nil) }
        if let e = calendar.first(where: { $0.id == id }) { return (e.title, e.start) }
        return (sheetTitle, ref.flatMap { ISO8601DateFormatter().date(from: $0.start) })
    }

    /// Pacenote's block in the notes field starts and ends with a line that names the call id, so a re-run finds its
    /// own block and nothing else (a PM writing "Pacenote notes", or another call's block, is not it).
    public static func startMark(_ key: String) -> String { "Pacenote notes (call id \(key))" }
    public static func endMark(_ key: String) -> String { "End of Pacenote notes (\(key))" }
    static let anyStart = "Pacenote notes (call id "

    /// The text comes from the call page as edited; the transcript is not an input on purpose.
    public static func notes(summary: [String], decisions: [String], nextSteps: [String], link: String, key: String) -> String {
        var b = "<p><b>\(CRMCallLog.esc(startMark(key)))</b></p>"
        func list(_ h: String, _ xs: [String]) { if !xs.isEmpty { b += "<p><b>\(h)</b></p><ul>" + xs.map { "<li>\(CRMCallLog.esc($0))</li>" }.joined() + "</ul>" } }
        list("Summary", summary); list("Decisions", decisions); list("Next steps", nextSteps)
        if !link.isEmpty { b += "<p>Call page: <a href=\"\(CRMCallLog.esc(link))\">\(CRMCallLog.esc(link))</a></p>" }
        b += "<p>\(CRMCallLog.esc(endMark(key)))</p>"
        return b
    }

    /// Replaces only this call's block (start line to end line, with the tags that open and close them) and keeps the
    /// text before and after it. Without this call's block the new one is added at the end.
    public static func merge(existing: String, notes: String, key: String) -> String {
        // HubSpot may rewrite the markup of a rich-text field (<strong> for <b>), so the lines are found by their words
        if let er = existing.range(of: endMark(key), options: .backwards),
           let sr = existing.range(of: startMark(key), options: .backwards, range: existing.startIndex..<er.lowerBound) {
            var lo = sr.lowerBound        // the opening tags right before the start line (never a closing tag of the text above)
            while lo > existing.startIndex, existing[existing.index(before: lo)] == ">",
                  let lt = existing[..<lo].lastIndex(of: "<"), !existing[lt...].hasPrefix("</") { lo = lt }
            var hi = er.upperBound        // the closing tags right after the end line
            while hi < existing.endIndex, existing[hi...].hasPrefix("</"), let gt = existing[hi...].firstIndex(of: ">") { hi = existing.index(after: gt) }
            return String(existing[..<lo]) + notes + String(existing[hi...])
        }
        return existing.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? notes : existing + notes
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
    /// The same email in HubSpot's own logging: its text holds the start of the draft (a signature may follow). The
    /// quoted history of a reply does not count: only the text above the first quoted line is compared.
    public static func sameText(_ theirs: String, _ ours: String) -> Bool {
        func norm(_ s: String) -> String { s.lowercased().split(whereSeparator: { $0.isWhitespace }).joined(separator: " ") }
        let o = String(norm(ours).prefix(200))
        return !o.isEmpty && norm(unquoted(theirs)).contains(o)
    }
    static func unquoted(_ s: String) -> String {
        let lines = s.components(separatedBy: "\n")
        let cut = lines.firstIndex { l in
            let t = l.trimmingCharacters(in: .whitespaces)
            return t.hasPrefix(">") || t.hasSuffix("wrote:") || t.hasPrefix("-----Original Message") || t.hasPrefix("________________")
        }
        return lines[..<(cut ?? lines.count)].joined(separator: "\n")
    }
}

/// created: a new engagement (Undo deletes it). attached: not Pacenote's (HubSpot's own calendar sync, or an email
/// logged by HubSpot or by hand): only the notes, or only the associations, were added. old: what Undo puts back,
/// empty when nothing was changed, in which case Undo must not touch the object. pick: meetings near the call that
/// may be its own but are not sure to be; nothing was written and the PM picks one, or a new Meeting.
public struct CRMTimelineResult: Equatable { public var id: String; public var created: Bool; public var attached: Bool; public var old: [String: String]; public var pick: [CRMMeetingCandidate]
    public init(id: String, created: Bool, attached: Bool = false, old: [String: String] = [:], pick: [CRMMeetingCandidate] = []) {
        self.id = id; self.created = created; self.attached = attached; self.old = old; self.pick = pick } }

/// A HubSpot Meeting near the call that may be the call's own, shown to the PM to pick.
public struct CRMMeetingCandidate: Equatable { public var id: String; public var title: String; public var start: Date
    public init(id: String, title: String, start: Date) { self.id = id; self.title = title; self.start = start } }

/// A write failed and taking its associations off again failed too: `fields` (`CRMTimeline.addedKey`) names the ones
/// still on the object, so Undo can take them off later. `error` is the first failure.
public struct CRMTimelineLeftover: Error { public var type: String; public var id: String; public var fields: [String: String]; public var error: Error }

public protocol CRMTimelineWriter {
    func upsertMeeting(_ log: CRMMeetingLog, knownId: String, associations: [CRMRef]) async throws -> CRMTimelineResult
    /// ours: Pacenote created knownId. An email that is not ours only gets the missing associations, never new content.
    func upsertEmail(_ log: CRMEmailLog, knownId: String, ours: Bool, associations: [CRMRef]) async throws -> CRMTimelineResult
    /// Puts the old fields back and removes the associations the write added (`CRMTimeline.addedKey` in fields).
    func restore(type: String, id: String, fields: [String: String]) async throws
}

public enum CRMTimeline {
    /// In an Undo record: the associations a write added, "type/id" joined by commas. Not a HubSpot property.
    public static let addedKey = "pacenote.associations"
    static func encode(_ refs: [CRMRef]) -> String { refs.map { $0.type + "/" + $0.id }.joined(separator: ",") }
    public static func decode(_ s: String) -> [CRMRef] {
        s.split(separator: ",").compactMap { p in let x = p.split(separator: "/", maxSplits: 1); return x.count == 2 ? CRMRef(String(x[0]), String(x[1])) : nil }
    }
}

/// The ids kept with a call (<stem>.crm.json) that the timeline writes read and update.
public struct CRMTimelineIds: Equatable {
    public var portalId: String; public var meetingId: String; public var emailId: String; public var emailOurs: Bool
    public init(portalId: String, meetingId: String = "", emailId: String = "", emailOurs: Bool = false) {
        self.portalId = portalId; self.meetingId = meetingId; self.emailId = emailId; self.emailOurs = emailOurs }
}

/// One Apply of the Meeting and Email rows, in turn with the Call writes of the same call (the same queue key): the
/// stored ids and the portal are read when the turn comes, each new id is stored as soon as it is known, and what was
/// written before a failure is returned for Undo.
public enum CRMTimelineSync {
    /// portal: the portal checked before the writes; the Undo records and the pick belong to it.
    public struct Outcome { public var undo: [(String, String, [String: String])] = []; public var ids: CRMTimelineIds; public var pick: [CRMMeetingCandidate] = []; public var error: Error?
        public var portal = "" }
    /// A pick from another portal: nothing was written, the PM applies again to search the portal of the link.
    public static let stalePickCode = 410

    public static func log(key: String, writer w: CRMTimelineWriter, connector c: CRMConnector, meeting: CRMMeetingLog?, email: CRMEmailLog?,
                           associations: [CRMRef], queue: CRMCallQueue = .shared,
                           load: @escaping () -> CRMTimelineIds, save: @escaping (CRMTimelineIds) -> Void) async -> Outcome {
        final class Box { var out: Outcome; init(_ o: Outcome) { out = o } }
        let box = Box(Outcome(ids: load()))
        guard meeting != nil || email != nil else { return box.out }
        // an id is stored only while the call is still linked to the portal it was written in: a Relink during the
        // write points the call at another portal, where the same id may be an unrelated record
        func store(_ ids: CRMTimelineIds) {
            if CRMPortal.matches(link: load().portalId, current: ids.portalId) { save(ids) }
            box.out.ids = ids
        }
        do {
            try await queue.run(key) {
                var ids = load()
                box.out.ids = ids
                try await CRMPortal.verify(link: ids.portalId, connector: c)
                box.out.portal = ids.portalId
                // a pick offered by another portal (the call was relinked since) is not a Meeting of this one
                if let m = meeting, !m.target.isEmpty, !CRMPortal.matches(link: m.targetPortal, current: ids.portalId) {
                    throw CRMError(code: stalePickCode, message: "This Meeting was offered by another HubSpot portal. Apply again to search this one.", scope: "portal")
                }
                if let m = meeting {
                    let r = try await w.upsertMeeting(m, knownId: ids.meetingId, associations: associations)
                    if r.pick.isEmpty {
                        ids.meetingId = r.id; store(ids)
                        if r.created || !r.old.isEmpty { box.out.undo.append(("meetings", r.id, r.old)) }
                    } else { box.out.pick = r.pick }        // nothing written yet; the Email row still goes
                }
                if let e = email {
                    let r = try await w.upsertEmail(e, knownId: ids.emailId, ours: ids.emailOurs, associations: associations)
                    ids.emailId = r.id; ids.emailOurs = !r.id.isEmpty && !r.attached; store(ids)
                    if r.created || !r.old.isEmpty { box.out.undo.append(("emails", r.id, r.old)) }
                }
            }
        } catch let l as CRMTimelineLeftover {
            box.out.undo.append((l.type, l.id, l.fields)); box.out.error = l.error
        } catch { box.out.error = error }
        return box.out
    }
}

/// Undo of one Update HubSpot: its records name ids of the portal they were written in, so they are undone only while
/// the key in use is of that portal (never by the link's portal of now: a Relink changes it, the ids stay the old portal's).
public enum CRMUndo {
    /// Notes and tasks are deleted, deal fields and an updated Call, Meeting or Email get their old values back.
    /// Throws the portal mismatch before any write; otherwise returns how many records failed and the (type, id) of
    /// the ones deleted (their stored ids must go too, so the next Apply makes them again).
    public static func run(_ items: [(String, String, [String: String])], portal: String, connector c: CRMConnector) async throws -> (failed: Int, deleted: [(String, String)]) {
        try await CRMPortal.verify(link: portal, connector: c)
        var failed = 0, deleted: [(String, String)] = []
        for (type, id, old) in items.reversed() {
            do {
                if type == "deals" { _ = try await c.updateDeal(id: id, fields: old) }
                else if type == "calls" && !old.isEmpty { try await c.restoreCall(id: id, fields: old) }
                else if (type == "meetings" || type == "emails") && !old.isEmpty { try await (c as? CRMTimelineWriter)?.restore(type: type, id: id, fields: old) }
                else { try await c.delete(type: type, id: id); deleted.append((type, id)) }
            } catch { failed += 1 }
        }
        return (failed, deleted)
    }
}

extension HubSpotConnector: CRMTimelineWriter {
    static let meetingAssoc: [String: Int] = ["deals": 212, "companies": 188, "contacts": 200]      // meeting -> object (HUBSPOT_DEFINED)
    static let emailAssoc: [String: Int] = ["deals": 210, "companies": 186, "contacts": 198]        // email -> object (HUBSPOT_DEFINED)
    static let meetingNotes = "hs_internal_meeting_notes"                                          // internal notes: the invite text stays the PM's

    private static func assocBody(_ refs: [CRMRef], _ ids: [String: Int]) -> [[String: Any]] {
        refs.compactMap { a in ids[a.type].map { ["to": ["id": a.id], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": $0]]] } }
    }

    /// Adds only the associations the object does not have yet. If one fails, the ones just added are taken off again.
    private func associateMissing(_ type: String, _ id: String, _ refs: [CRMRef], _ ids: [String: Int], have: Set<String>) async throws -> [CRMRef] {
        var added: [CRMRef] = []
        do {
            for a in refs where ids[a.type] != nil && !have.contains(a.type + "/" + a.id) {
                _ = try await call("PUT", "/crm/v4/objects/\(type)/\(id)/associations/default/\(a.type)/\(a.id)")
                added.append(a)
            }
        } catch { try await rollBack(type, id, added, error) }
        return added
    }

    /// Takes the associations just added off again and throws `error`; the ones that could not be taken off go with
    /// it (`CRMTimelineLeftover`), so they stay in Undo.
    private func rollBack(_ type: String, _ id: String, _ added: [CRMRef], _ error: Error) async throws -> Never {
        var left: [CRMRef] = []
        for a in added {
            do { _ = try await call("DELETE", "/crm/v4/objects/\(type)/\(id)/associations/\(a.type)/\(a.id)") }
            catch let e as CRMError where e.code == 404 {}       // already gone
            catch { left.append(a) }
        }
        if left.isEmpty { throw error }
        throw CRMTimelineLeftover(type: type, id: id, fields: [CRMTimeline.addedKey: CRMTimeline.encode(left)], error: error)
    }

    /// The contacts of the call: a Meeting or an Email is only taken as this call's when it is on one of them (a shared
    /// company or deal alone is not enough). One search group per contact (HubSpot allows five).
    private func contactFilters(_ refs: [CRMRef], _ extra: [[String: Any]]) -> [[String: Any]] {
        refs.filter { $0.type == "contacts" }.prefix(5).map { ["filters": extra + [["propertyName": "associations.contact", "operator": "EQ", "value": $0.id]]] }
    }

    /// HubSpot returns ISO 8601 with milliseconds, or epoch milliseconds.
    public static func time(_ v: String, or fallback: Date) -> Date {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.date(from: v) ?? ISO8601DateFormatter().date(from: v) ?? Double(v).map { Date(timeIntervalSince1970: $0 / 1000) } ?? fallback
    }

    /// The properties and the contacts, companies and deals the object is associated with ("type/id").
    private func read(_ type: String, _ id: String, _ keys: [String]) async throws -> ([String: String], Set<String>) {
        let r = try await call("GET", "/crm/v3/objects/\(type)/\(id)?properties=" + keys.sorted().joined(separator: ",") + "&associations=contacts,companies,deals")
        var out: [String: String] = [:]
        for k in keys { out[k] = Self.p(r, k) }
        var have = Set<String>()
        for (kind, v) in (r["associations"] as? [String: Any]) ?? [:] {
            for a in ((v as? [String: Any])?["results"] as? [[String: Any]]) ?? [] { if let aid = a["id"].map({ "\($0)" }) { have.insert(kind + "/" + aid) } }
        }
        return (out, have)
    }

    enum Match: Equatable { case none, one(String), several, pick([CRMMeetingCandidate]) }

    /// HubSpot's own Meeting of this call (its calendar sync, or ours from a run whose id was lost): on one of the
    /// call's contacts, starting within 15 minutes. Ours (our block in the notes) is used; a meeting holding another
    /// call's block is never it. Every other one goes to the PM to pick and nothing is written until then: a title or
    /// a start alone never decides (the calendar event may come from another meeting's sheet the PM reused). The ones
    /// with the call's or the calendar event's title, or starting with that event (within a minute), come first.
    func findMeeting(_ log: CRMMeetingLog, _ refs: [CRMRef]) async throws -> Match {
        let lo = Int((log.start.timeIntervalSince1970 - CRMMeetingLog.tolerance) * 1000), hi = Int((log.start.timeIntervalSince1970 + CRMMeetingLog.tolerance) * 1000)
        let groups = contactFilters(refs, [["propertyName": "hs_timestamp", "operator": "GTE", "value": String(lo)],
                                           ["propertyName": "hs_timestamp", "operator": "LTE", "value": String(hi)]])
        guard !groups.isEmpty else { return .none }
        let r = try await call("POST", "/crm/v3/objects/meetings/search", ["filterGroups": groups, "properties": ["hs_timestamp", "hs_meeting_title", "hs_meeting_start_time", Self.meetingNotes], "limit": 20])
        var seen = Set<String>()
        let hits = ((r["results"] as? [[String: Any]]) ?? []).compactMap { h -> (c: CRMMeetingCandidate, notes: String)? in
            guard let id = h["id"] as? String, seen.insert(id).inserted else { return nil }
            let ts = Self.time(Self.p(h, "hs_timestamp"), or: log.start)
            return (CRMMeetingCandidate(id: id, title: Self.p(h, "hs_meeting_title"), start: Self.time(Self.p(h, "hs_meeting_start_time"), or: ts)), Self.p(h, Self.meetingNotes))
        }
        if let ours = hits.first(where: { $0.notes.contains(CRMMeetingLog.startMark(log.key)) }) { return .one(ours.c.id) }
        let free = hits.filter { !$0.notes.contains(CRMMeetingLog.anyStart) }.map(\.c)
        if free.isEmpty { return .none }
        func norm(_ s: String) -> String { s.lowercased().split(whereSeparator: { $0.isWhitespace }).joined(separator: " ") }
        let titles = Set([log.title, log.eventTitle].map(norm).filter { !$0.isEmpty })
        func likely(_ m: CRMMeetingCandidate) -> Bool {
            titles.contains(norm(m.title)) || (log.eventStart.map { abs($0.timeIntervalSince(m.start)) <= CRMMeetingLog.sameStart } ?? false)
        }
        return .pick(free.sorted { a, b in
            likely(a) != likely(b) ? likely(a) : abs(a.start.timeIntervalSince(log.start)) < abs(b.start.timeIntervalSince(log.start))
        })
    }

    /// The known id (kept with the call) first; else the PM's pick (`log.target`); else HubSpot's own Meeting of this
    /// call when it holds this call's block (`findMeeting`): the notes go into it. Any other Meeting near the call:
    /// nothing is written and the candidates come back in `pick`. Else a new one. Associations are added before the notes change; a failure leaves nothing changed.
    public func upsertMeeting(_ log: CRMMeetingLog, knownId: String, associations: [CRMRef]) async throws -> CRMTimelineResult {
        let keys = [Self.meetingNotes, "hs_meeting_title", "hs_meeting_start_time", "hs_meeting_end_time", "hs_meeting_outcome"]
        var id = knownId
        var cur: ([String: String], Set<String>) = ([:], [])
        if !id.isEmpty {
            do { cur = try await read("meetings", id, keys) } catch let e as CRMError where e.code == 404 { id = "" }
        }
        var attached = false
        if id.isEmpty && log.target != CRMMeetingLog.newTarget {
            if !log.target.isEmpty { id = log.target; attached = true; cur = try await read("meetings", id, keys) }
            else {
                switch try await findMeeting(log, associations) {
                case .one(let found): id = found; attached = true; cur = try await read("meetings", id, keys)
                case .pick(let c): return CRMTimelineResult(id: "", created: false, pick: c)
                case .several, .none: break
                }
            }
        }
        if id.isEmpty {
            let r = try await call("POST", "/crm/v3/objects/meetings", [
                "properties": owned(log.properties.merging([Self.meetingNotes: log.notes]) { a, _ in a }),
                "associations": Self.assocBody(associations, Self.meetingAssoc)])
            return CRMTimelineResult(id: (r["id"] as? String) ?? "", created: true)
        }
        let before = cur.0[Self.meetingNotes] ?? ""
        let merged = CRMMeetingLog.merge(existing: before, notes: log.notes, key: log.key)
        let added = try await associateMissing("meetings", id, associations, Self.meetingAssoc, have: cur.1)
        if merged != before {
            do { _ = try await call("PATCH", "/crm/v3/objects/meetings/\(id)", ["properties": [Self.meetingNotes: merged]]) }
            catch { try await rollBack("meetings", id, added, error) }
        }
        var old: [String: String] = merged != before ? [Self.meetingNotes: before] : [:]
        if !added.isEmpty { old[CRMTimeline.addedKey] = CRMTimeline.encode(added) }
        return CRMTimelineResult(id: id, created: false, attached: attached, old: old)
    }

    /// HubSpot's own logging of the sent email: on one of the call's contacts in the last day, the same subject, and a
    /// text that holds the draft. Several such: it is logged already, and none is taken as Pacenote's.
    func findEmail(_ log: CRMEmailLog, _ refs: [CRMRef]) async throws -> Match {
        let since = Int((log.sent.timeIntervalSince1970 - 86400) * 1000)
        let groups = contactFilters(refs, [["propertyName": "hs_email_subject", "operator": "EQ", "value": log.subject],
                                           ["propertyName": "hs_timestamp", "operator": "GTE", "value": String(since)]])
        guard !groups.isEmpty else { return .none }
        let r = try await call("POST", "/crm/v3/objects/emails/search", ["filterGroups": groups, "properties": ["hs_email_subject", "hs_email_text"], "limit": 20])
        let ids = Set(((r["results"] as? [[String: Any]]) ?? []).filter { CRMEmailLog.sameText(Self.p($0, "hs_email_text"), log.text) }.compactMap { $0["id"] as? String })
        return ids.count == 1 ? .one(ids.first!) : (ids.isEmpty ? .none : .several)
    }

    /// Pacenote's own email (known id, ours): updated. An email Pacenote did not create (found, or a known id that is
    /// not ours) is read only: just the missing associations are added. Else a new one.
    public func upsertEmail(_ log: CRMEmailLog, knownId: String, ours: Bool, associations: [CRMRef]) async throws -> CRMTimelineResult {
        let keys = ["hs_email_subject", "hs_email_text", "hs_email_html", "hs_email_headers", "hs_timestamp"]
        var id = knownId, mine = ours
        var cur: ([String: String], Set<String>) = ([:], [])
        if !id.isEmpty {
            do { cur = try await read("emails", id, keys) } catch let e as CRMError where e.code == 404 { id = "" }
        }
        if id.isEmpty {
            mine = false
            switch try await findEmail(log, associations) {
            case .one(let found): id = found; cur = try await read("emails", id, keys)
            case .several, .pick: return CRMTimelineResult(id: "", created: false, attached: true)     // logged already; nothing to write
            case .none:
                let r = try await call("POST", "/crm/v3/objects/emails", ["properties": owned(log.properties), "associations": Self.assocBody(associations, Self.emailAssoc)])
                return CRMTimelineResult(id: (r["id"] as? String) ?? "", created: true)
            }
        }
        let added = try await associateMissing("emails", id, associations, Self.emailAssoc, have: cur.1)
        var old: [String: String] = [:]
        if mine {
            do { _ = try await call("PATCH", "/crm/v3/objects/emails/\(id)", ["properties": owned(log.properties)]) }
            catch { try await rollBack("emails", id, added, error) }
            old = cur.0
        }
        if !added.isEmpty { old[CRMTimeline.addedKey] = CRMTimeline.encode(added) }
        return CRMTimelineResult(id: id, created: false, attached: !mine, old: old)
    }

    public func restore(type: String, id: String, fields: [String: String]) async throws {
        var f = fields
        let added = f.removeValue(forKey: CRMTimeline.addedKey).map(CRMTimeline.decode) ?? []
        if !f.isEmpty { _ = try await call("PATCH", "/crm/v3/objects/\(type)/\(id)", ["properties": f]) }
        for a in added { _ = try await call("DELETE", "/crm/v4/objects/\(type)/\(id)/associations/\(a.type)/\(a.id)") }
    }
}
