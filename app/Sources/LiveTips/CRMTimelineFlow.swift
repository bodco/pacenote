import Foundation
import LiveTipsCore

// H5 (PB-61, docs/specs/hubspot-crm-integration.md): the call's Meeting and the sent follow-up Email as two more rows
// of Update HubSpot. Both are a click (Apply, Undo for 8 seconds); there is no Auto for them. The texts come from the
// call page and the follow-up draft as the PM edited them, never from the transcript.

@MainActor
enum CRMTimelineLogger {
    /// The Meeting row is ticked by default; the Email row never is, because Pacenote does not send mail: the PM ticks it
    /// to say "I sent this one".
    static func rows(meeting: CRMMeetingLog?, email: CRMEmailLog?) -> [CRMUpdatePlan.Row] {
        var r: [CRMUpdatePlan.Row] = []
        if let m = meeting { r.append(.init(id: "meeting", title: "meeting", detail: m.title, on: true)) }
        if let e = email { r.append(.init(id: "email", title: "email", detail: e.subject, on: false)) }
        return r
    }

    /// The call is the meeting: its start and end, the call page's Summary (In short and Recap), decisions and next
    /// steps (the same sections as the Call), the link back.
    static func meeting(file: String, link: CRMLink) -> CRMMeetingLog? {
        guard !link.callKey.isEmpty, let rec = CallHistory.shared.records.first(where: { $0.file == file }) else { return nil }
        let base = file.hasSuffix(".md") ? String(file.dropLast(3)) : file
        let o = CallOutcome.read(transcript: file) ?? CallOutcome()
        let m = CallNotes.meeting(file)
        let sales = (try? Data(contentsOf: URL(fileURLWithPath: CallLayout.sidecar(base, ".sales.json")))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        let next = (sales["next_step"] as? String) ?? ((sales["next_step"] as? [String: Any])?["what"] as? String) ?? ""
        let s = CRMCallLog.sections(tldr: m?.tldr ?? "", recap: m?.recap ?? [], onMe: o.onMe, theirs: o.theirs.map { $0.who + ": " + $0.what }, salesNext: next)
        let end = rec.end ?? rec.start.addingTimeInterval((rec.minutes ?? 0) * 60)
        // the calendar event the call held, only by the event id its Meeting Sheet kept (<stem>.sheet.json), never the
        // nearest event. The sheet may be another meeting's (reused at Start call), so this only puts a nearby Meeting with
        // its title or start first in the pick; HubSpot's own Meeting is never written to without the PM's pick
        let sheet = try? MeetingSheetIO.load(CallLayout.sidecar(base, ".sheet.json"))
        let ev = CRMMeetingLog.event(ref: sheet?.meetingRef, sheetTitle: sheet?.title ?? "",
                                     calendar: CalendarService.shared.events.map { ($0.id, $0.title, $0.start) })
        return CRMMeetingLog(key: link.callKey, title: CallPageView.title(file), start: rec.start, end: end,
                             notes: CRMMeetingLog.notes(summary: s.summary, decisions: o.decisions, nextSteps: s.nextSteps, link: rec.notionURL ?? "", key: link.callKey),
                             eventTitle: ev.title, eventStart: ev.start)
    }

    /// Only once a follow-up draft was written in this session (the draft is not saved to a file): the subject and text as edited.
    static func email(file: String, link: CRMLink) -> CRMEmailLog? {
        guard !link.callKey.isEmpty, let m = FollowUpModel.byFile[file] else { return nil }
        let body = m.body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return nil }
        return CRMEmailLog(key: link.callKey, subject: m.subject, text: body)
    }

    /// One Apply step for the two rows, in turn with the Call writes of this call (Auto included): the ids are read
    /// from <base>.crm.json when the turn comes and each new one is stored there at once, so neither path loses the
    /// other's ids. Returns what Undo needs, the ids now stored, what was written before a failure, and the Meetings
    /// to pick from when HubSpot's own one is not sure (nothing was written to a Meeting then).
    static func apply(_ w: CRMTimelineWriter, connector c: CRMConnector, meeting: CRMMeetingLog?, email: CRMEmailLog?, base: String, link: CRMLink,
                      refs: [CRMRef], on: (String) -> Bool) async -> (undo: [(String, String, [String: String])], link: CRMLink, pick: [CRMMeetingCandidate], portal: String, error: Error?) {
        var e = on("email") ? email : nil
        e?.sent = Date()
        let o = await CRMTimelineSync.log(key: link.callKey, writer: w, connector: c, meeting: on("meeting") ? meeting : nil, email: e, associations: refs,
            load: { ids(CRMLink.load(base: base) ?? link) },
            save: { i in
                // never into a link a Relink made while the write was pending: the ids are the verified portal's
                var d = CRMLink.load(base: base) ?? link
                guard CRMPortal.matches(link: d.portalId, current: i.portalId) else { return }
                d.meetingId = i.meetingId; d.emailId = i.emailId; d.emailOurs = i.emailOurs; d.save(base: base) })
        var l = link; l.meetingId = o.ids.meetingId; l.emailId = o.ids.emailId; l.emailOurs = o.ids.emailOurs
        return (o.undo, l, o.pick, o.portal, o.error)
    }

    nonisolated static func ids(_ l: CRMLink) -> CRMTimelineIds {
        CRMTimelineIds(portalId: l.portalId, meetingId: l.meetingId, emailId: l.emailId, emailOurs: l.emailOurs)
    }

    /// Undo deleted a Meeting or an Email Pacenote made in `portal`: the stored id goes, so the next Apply makes it
    /// again. A link of another portal (relinked since) keeps its ids: the same id there is another record.
    static func forget(type: String, id: String, portal: String, base: String) {
        guard var d = CRMLink.load(base: base), CRMPortal.matches(link: d.portalId, current: portal) else { return }
        if type == "meetings", d.meetingId == id { d.meetingId = "" }
        else if type == "emails", d.emailId == id { d.emailId = ""; d.emailOurs = false }
        else { return }
        d.save(base: base)
    }
}
