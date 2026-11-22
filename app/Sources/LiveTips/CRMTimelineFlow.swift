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

    /// The call is the meeting: its start and end, the call page's summary, decisions and next steps, the link back.
    static func meeting(file: String, link: CRMLink) -> CRMMeetingLog? {
        guard !link.callKey.isEmpty, let rec = CallHistory.shared.records.first(where: { $0.file == file }) else { return nil }
        let o = CallOutcome.read(transcript: file) ?? CallOutcome()
        let steps = o.onMe + o.theirs.map { $0.who + ": " + $0.what }
        let end = rec.end ?? rec.start.addingTimeInterval((rec.minutes ?? 0) * 60)
        return CRMMeetingLog(key: link.callKey, title: CallPageView.title(file), start: rec.start, end: end,
                             notes: CRMMeetingLog.notes(summary: o.onMe, decisions: o.decisions, nextSteps: steps, link: rec.notionURL ?? "", key: link.callKey))
    }

    /// Only once a follow-up draft was written in this session (the draft is not saved to a file): the subject and text as edited.
    static func email(file: String, link: CRMLink) -> CRMEmailLog? {
        guard !link.callKey.isEmpty, let m = FollowUpModel.byFile[file] else { return nil }
        let body = m.body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return nil }
        return CRMEmailLog(key: link.callKey, subject: m.subject, text: body)
    }

    /// One Apply step for the two rows; returns what Undo needs as (type, id, old fields) and the ids to keep with the call.
    static func apply(_ w: CRMTimelineWriter, meeting: CRMMeetingLog?, email: CRMEmailLog?, link: CRMLink, refs: [CRMRef],
                      on: (String) -> Bool) async -> (undo: [(String, String, [String: String])], link: CRMLink, error: Error?) {
        var l = link
        var undo: [(String, String, [String: String])] = []
        do {
        if on("meeting"), let m = meeting {
            let r = try await w.upsertMeeting(m, knownId: l.meetingId, associations: refs)
            l.meetingId = r.id
            if r.created || !r.old.isEmpty { undo.append(("meetings", r.id, r.old)) }
        }
        if on("email"), var e = email {
            e.sent = Date()
            let r = try await w.upsertEmail(e, knownId: l.emailId, associations: refs)
            l.emailId = r.id
            if r.created || !r.old.isEmpty { undo.append(("emails", r.id, r.old)) }
        }
        } catch { return (undo, l, error) }      // what was written before the failure stays undoable
        return (undo, l, nil)
    }
}
