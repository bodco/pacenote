# HubSpot CRM integration: spec (PB-57)

Status: draft for the owner. The Call engagement (H2, PB-58) and the Meeting and Email engagements (H5, PB-61) are
built on their branches; the rest is planned. Source of truth for behavior today: `app/Sources/LiveTipsCore/CRM.swift`,
`app/Sources/LiveTips/CRMFlow.swift`, `rules/crm.yaml`.

## 1. Where we are

Pacenote already connects to HubSpot with a service key (a private app key) kept in the Keychain
(`SecretStore.Key.hubspotKey`). The key is sent as `Authorization: Bearer <key>` to `https://api.hubapi.com`.

- **Read** (before a call): `CRMConnector.findCompany` (domain, then name), `findContacts` (emails), `deals`
  (open deal updated last), `recentActivity`, `pipelines`, `properties`, `owners`. The result is the local snapshot
  `<stem>.crm.md` and the link `<stem>.crm.json` (ids only: company, deal, contacts).
- **Write** (after a call): "Update HubSpot" on the call page. A reviewed list of checkboxes (note, deal fields,
  follow-up task, and since H2 the Call), **Apply**, then **Undo** for 8 seconds. Nothing is written without a click.
- **Not yet**: Leads, creating contacts and companies, OAuth, several portals.

## 2. Objects and associations

HubSpot association type ids below are HUBSPOT_DEFINED, from the object on the left to the object on the right.

| Object | Pacenote use | Read | Write | Associates with |
|---|---|---|---|---|
| Contact | the call's participants | yes, by email | later: create when missing (decision 4) | company (HubSpot does this itself by email domain) |
| Company | the account of the call | yes, by domain then name | later: create when missing (decision 4) | contacts, deals |
| Deal | the sales opportunity | yes (open deal, stage, amount, next step) | yes: next step, close date, stage only if the PM types one, qualification fields | company, contacts |
| Call | the logged call (H2) | no | yes (H2, built) | contact 194, company 182, deal 206 |
| Note | summary, decisions, next steps | yes (recent activity) | yes (built in 0.8.7) | contact 202, company 190, deal 214 |
| Task | the follow-up | open tasks | yes (built in 0.8.7) | contact 204, company 192, deal 216 |
| Meeting | the call's calendar slot | recent activity; searched by the call's contacts and start time to find HubSpot's own | yes (H5, decision 6): a new Meeting, or the notes added to the one HubSpot's calendar sync made | contact 200, company 188, deal 212 |
| Email | the follow-up email the PM sent | recent activity | yes (H5): an email engagement that records an email the PM sent. Pacenote never sends mail, through HubSpot or otherwise | contact 198, company 186, deal 210 |
| Lead | an unqualified prospect | no | later, opt in (decision 5) | contact, company |

## 3. Field mapping from Pacenote

### Call (H2)

| HubSpot property | From Pacenote |
|---|---|
| `hs_call_title` | the call title |
| `hs_timestamp` | the call's start, ISO 8601 |
| `hs_call_duration` | end minus start, in milliseconds |
| `hs_call_direction` | Settings > Integrations > HubSpot > Direction of logged calls (default OUTBOUND; Pacenote cannot tell in which direction a conference call went) |
| `hs_call_status` | `COMPLETED` |
| `hs_call_disposition` | the outcome id; `Connected` (HubSpot's built-in outcome id, `CRMCallLog.connected`, the same in every portal) because a recorded call was a conversation. No-answer outcomes are not logged by Pacenote |
| `hs_call_body` | participants' names; Summary = the call page's Summary (the meeting notes' In short and Recap); Next steps = what the PM and the other side took on (else the sales next step), never a copy of the Summary; the link back to the call (the Notion page when there is one), and the line `Pacenote call id: <id>`. Never transcript text |
| `hs_call_external_id` | `pacenote:<Pacenote call id>`, the dedupe key |
| `hubspot_owner_id` | the owner chosen in Matching (section 7) |

### Meeting (H5)

| HubSpot property | From Pacenote |
|---|---|
| `hs_meeting_title`, `hs_timestamp`, `hs_meeting_start_time`, `hs_meeting_end_time` | the call's title, start and end (the call is the meeting) |
| `hs_meeting_outcome` | `COMPLETED` |
| `hs_internal_meeting_notes` | Pacenote's block: the line `Pacenote notes (call id <id>)`, Summary (the call page's Summary, as in the Call), Decisions, Next steps, the link back, the line `End of Pacenote notes (<id>)`. Never transcript text, even when "Add the transcript to the CRM note" is on |
| `hubspot_owner_id` | the owner chosen in Matching (section 7), for a new Meeting only |

The notes go to the internal notes field, not to `hs_meeting_body` (the invite text), which belongs to the PM and to
HubSpot's calendar sync. The block is bounded by its start and end lines, both naming the Pacenote call id. A re-run
replaces only the text from this call's start line to its end line (with the tags that open and close them) and keeps
everything before and after it; a PM's mention of "Pacenote notes", or another call's block, is never touched. With no
block for this call, the new one is added at the end of the field.

### Email (H5)

| HubSpot property | From Pacenote |
|---|---|
| `hs_email_subject`, `hs_email_text`, `hs_email_html` | the follow-up draft on the call page as the PM edited it (subject and text) |
| `hs_email_direction` | `EMAIL` (sent by the PM) |
| `hs_email_status` | `SENT` |
| `hs_timestamp` | the moment of Apply; Pacenote does not know when the PM really sent it |
| `hs_email_headers` | `from` and `to` only when known; today Pacenote leaves them out |
| `hubspot_owner_id` | the owner chosen in Matching |

### Note, Task, Deal (already built)

| HubSpot | From Pacenote |
|---|---|
| Note `hs_note_body` | Summary, Decisions, Next steps, the Notion link; the transcript only when "Add the transcript to the CRM note" is on |
| Task `hs_task_subject`, `hs_timestamp` | the next step, due date when one was said (else tomorrow) |
| Deal `hs_next_step`, `closedate`, `dealstage` | the call page's next step; close date when said; stage only when the PM types one |
| Deal qualification properties | per method criterion to a deal property chosen in Settings (or "Don't write") |

## 4. Required scopes

### Service key (private app key), today

Already listed in Settings > Integrations > HubSpot (ten scopes):

`crm.objects.companies.read`, `crm.objects.companies.write`, `crm.objects.contacts.read`,
`crm.objects.contacts.write`, `crm.objects.deals.read`, `crm.objects.deals.write`, `crm.objects.owners.read`,
`crm.schemas.companies.read`, `crm.schemas.contacts.read`, `crm.schemas.deals.read`.

Calls, notes, tasks and meetings are engagement objects. HubSpot documents the contacts scopes for creating them with
contact associations, so H2 asks for **no new scope**. This must be confirmed on a real portal before release (see
section 11); if a portal answers 403, the error line already names the missing scope.

### Added later, only with the matching feature

| Feature | Added scope |
|---|---|
| Leads | `crm.objects.leads.read`, `crm.objects.leads.write` |
| Meetings as objects, if portals refuse them with the contacts scopes | `crm.objects.meetings.read` (HubSpot may also need the calendar scope) |
| Custom Pacenote property on Calls (decision 7) | `crm.schemas.custom.read`, `crm.schemas.custom.write` |

### OAuth (later, decision 1)

A HubSpot public app can request the same objects as scopes: the ten above as required scopes (plus `oauth`), and the
added ones as optional scopes. HubSpot's token exchange needs the app's client secret, so a Pacenote OAuth app should
use a small token broker on the Pacenote server (the client secret never ships in the Mac app); to be confirmed
against HubSpot's current docs before building. Refresh tokens go to the Keychain like the key. Until the owner
decides, the service key stays the only path.

## 5. Write rules

1. **Automatic only with a switch.** The default is a click: Update HubSpot shows every change as a row, the PM ticks,
   presses Apply, and Undo works for 8 seconds. The only automatic write is the Auto switch for the Call
   (Settings > Integrations > HubSpot > "Log the call in HubSpot automatically after it ends"), off by default, per
   installation. Auto writes only the Call. Notes, deal fields and tasks stay behind a click. Every other automatic
   write needs its own switch, also off by default. **Meetings and Emails have no Auto switch (H5)**: both are rows of
   Update HubSpot behind Apply and Undo. The Meeting row is ticked by default; the Email row is never ticked for the PM,
   because it records an email the PM says was sent, and Pacenote cannot know that.
2. **Transcript text is never sent** to the Call, the Task, the Deal or any field. The note may carry it only when the
   PM turns on "Add the transcript to the CRM note" (off by default). Today (built in 0.8.7) the setting puts the
   transcript into the note when the Update HubSpot window opens; the "Attach the transcript" row only shows that, and
   unticking it does not take the transcript out of the note. Privacy follow-up: make the row the per-Apply consent
   (the note gets the transcript only when the row is ticked at Apply).
3. **Texts come from the call page as the PM edited it.** Pacenote never writes a text the PM could not see on the call
   page.
4. **Undo.** A new object is deleted; an updated object gets its old property values back. Associations added during a
   re-run are not removed by Undo (they are harmless and HubSpot has no history for them); the Undo failure line says
   what is left. A re-run of the Call adds the associations first and changes the fields last, so a failed association
   leaves the Call's fields as they were and nothing changed is outside Undo. Meetings and Emails (H5) go further:
   they add only the associations the object does not have yet, before any field; if an association or the field
   update then fails, the associations just added are removed again, so a failed write leaves the object as it was.
   Their Undo also removes the associations the write added. Undo of a Meeting's notes never writes back the whole
   field: its record holds only Pacenote's block before and after Apply, and Undo reads the notes again, puts the old
   block back in place of Pacenote's block (or removes it) and keeps all other text, also text added after Apply. If
   Pacenote's block was edited or removed in HubSpot since Apply, the notes are kept as they are and Undo says so.
   Undo runs in the call's turn (the same queue as the writes and Relink). Undo checks the portal like Apply (section 8).
5. **Errors are one line where the action was**, never a modal; 429 waits (Retry-After, else 1, 2, 4 s) and retries three
   times; 401 and 403 say what to fix (key, or the named scope).
6. **No silent overwrites of the PM's own edits in HubSpot.** A re-run updates only the Pacenote-owned properties of the
   Call (section 3), and the old values are kept for Undo.
7. Usage log and analytics record only counts and an error code, never names, texts or ids.

## 6. Dedupe rules

| Object | Key | Rule |
|---|---|---|
| Contact | email (lowercase, trimmed) | search `email EQ`; found: use it; several: take the one on the matched company, else the oldest; never create a second contact with the same email. Creating a missing contact is a later, opt-in step (decision 4) |
| Company | domain, then name | search `domain EQ`, then `name EQ`; free-mail domains (gmail.com, outlook.com, icloud.com, yahoo.com, hotmail.com) and the PM's own domains never match a company |
| Call | the Pacenote call id | the id is made once and stored with the call (`<stem>.crm.json`, `callKey`) together with the HubSpot Call id (`callId`). A re-run PATCHes `callId`. If `callId` is missing, HubSpot is searched by `hs_call_external_id = pacenote:<id>`. If the stored id was deleted in HubSpot (404), the same search runs, then a new Call is created. Auto and Apply of one call take turns (a queue per call id) and each reads the stored `callId` when its turn comes. A create that gets no answer or a 5xx is checked by the same search before it is reported as failed. A second Call for one call id is never created |
| Meeting | the Pacenote call id, then HubSpot's own meeting | the HubSpot Meeting id is stored with the call (`<stem>.crm.json`, `meetingId`). A re-run updates it. Without it (or on a 404), HubSpot is searched for Meetings associated with one of the call's **contacts** (a shared company or deal alone is never enough) whose `hs_timestamp` is within 15 minutes of the call's start. A Meeting holding this call's block is ours from an earlier run and is used; a Meeting holding another call's block is never used. Any other Meeting is never written to without the PM's pick, whatever its title or start: nothing is written to any Meeting and Update HubSpot lists every Meeting near the call that the search returned, and the PM picks one, which is then used without a search and kept as `meetingId`, or picks a new Meeting. A title or a start alone cannot decide, because the calendar event comes from the call's Meeting Sheet (`meeting_ref.event_id` of `<stem>.sheet.json`, title and start from the calendar or, once the calendar dropped it, from the sheet), and Start call copies the sheet of whichever preparation the PM chose, possibly another invite's. So the event only orders the list: Meetings whose title is the call's or the event's (case and spaces aside), or whose `hs_meeting_start_time` is within a minute of the event's start, come first, then the rest closest first. The event nearest the call is never taken for it. The Email row of the same Apply still goes. Used: only this call's block in `hs_internal_meeting_notes` is replaced or appended (section 3), the title, invite and every other property stay, the missing associations are added, and Undo takes back only Pacenote's block (section 5, rule 4) and removes the added associations. Not found: a new Meeting is created. The Meeting writes take turns with the Call writes of the same call (the same queue) and the ids are read from and stored to `<stem>.crm.json` inside the turn |
| Email | the stored id and who made it, then contact, subject and text | the HubSpot Email id is stored with the call (`emailId`) together with `emailOurs`: whether Pacenote created it. A re-run updates the email only when Pacenote created it. Without a stored id, HubSpot is searched for emails on one of the call's contacts in the last day with the same subject whose text holds the start of the draft (HubSpot's own mail logging adds signatures and quotes). Exactly one: it is stored as not ours and stays read only: only the missing associations are added, now and on every re-run, and Undo removes only those. Several: the email is logged already; nothing is written. None (or no contact): a new Email is created and stored as ours. The same subject with another text is never claimed |
| Note, Task | none | created per Apply; the 8 second Undo and the "already applied" lock protect against a double click. No dedupe across sessions (decision 8) |
| Deal | the PM's link | the open deal updated last, or what the PM chose in Start call; "No deal" is kept for the series |

Known gap: if both `<stem>.crm.json` is lost and the Call has no `hs_call_external_id` (for example a portal that
rejects the property), a re-run creates a second Call. Mitigation: the body carries `Pacenote call id: <id>`; a
manual search in HubSpot finds it.

## 7. Owner mapping

HubSpot owners are CRM users (`/crm/v3/owners`). Today: the owner is picked automatically by the user's own email
(calendar accounts) until the PM chooses one in Settings > Integrations > HubSpot > Matching; the chosen owner is
written as `hubspot_owner_id` on notes, tasks and (H2) Calls. Rules:

- No email match and no choice: the field is left out and HubSpot applies its default (the key's user).
- Deal owner is never changed by Pacenote.
- Contacts' and companies' owners are never changed.
- Calls with several Pacenote users on one portal: each user's Pacenote owns the Calls it logs (each Mac has its own
  key or sign-in); a call attended by two Pacenote users can produce two Calls (decision 8).

## 8. Multi-portal behavior

Today: one service key means one portal; the portal's name (from account-info, else "HubSpot <portalId> (<region>)")
is shown on the card. Behavior to keep and to define:

- A key belongs to exactly one portal. Changing the key switches the portal. Each link in `<stem>.crm.json` keeps the
  `portalId` it was made in (built in H2). Before every write (Apply and Auto) Pacenote asks account-info for the
  portal of the key in use; when the link names another portal, or none (links made before H2), nothing is written:
  Auto skips the call and Update HubSpot says so and offers **Relink**. Relink and Refresh drop the old ids, keep the
  Pacenote call id, and find the company, deal and contacts again in the current portal (by the attendees' domains
  while the calendar still has the event, else by the linked company's name). H5: the Meeting and Email writes check
  the portal again inside their turn, and Undo checks it before it writes. The Meeting pick and the Undo records are
  bound to the portal that produced them (not to the link's portal of now): a pick from another portal writes nothing,
  Undo runs only while the key is of the portal it was written in, and a successful Relink clears both. A new id is
  stored only while the call's link is still of the portal verified for the write (a Relink may finish mid-write).
- Several portals at once (an agency with client portals): one key per project in Settings > Projects, kept in the
  Keychain under a per-project name. Not built; decision 3.
- Data hosting regions (US, EU) use the same base URL for the API; the region is only displayed.
- Never merge data across portals: the snapshot, the link and the Call ids are per call, and a call has one portal.

## 9. Auto, review and Undo flow (H2)

1. Start call: Refresh links company, deal and contacts (`.crm.json`).
2. The call ends. If Auto is on and the call is linked in the portal of the current key (section 8), Pacenote waits for the call page's notes (about 2.5 minutes),
   then logs the Call. No review and no Undo in this path; the switch is the consent. The PM sees it in HubSpot and in
   the call's Update HubSpot window (Call row, ticked).
3. Otherwise the PM opens Update HubSpot on the call page: the Call row is ticked by default (Settings can untick it),
   Apply writes, Undo works for 8 seconds.
4. A re-run (Apply again later, or Auto after an edit) updates the same Call.

## 10. Decisions for the owner (with recommendations)

1. **OAuth or the service key?** Service key stays the only path until HubSpot customers ask for it; OAuth needs a
   token broker on the server. *Recommend: keep the key for now; plan OAuth with the Pacenote server after the first 5
   paying HubSpot users.*
2. **Direction of logged calls.** Pacenote cannot tell inbound from outbound. *Recommend: a Settings choice with
   Outbound as the default (built).*
3. **Several portals.** *Recommend: not now; `portalId` in the link and no writes on a mismatch are built (H2),
   per-project keys only when an agency user asks.*
4. **Create missing contacts and companies?** *Recommend: yes, but only as an unticked row in Update HubSpot, never in
   Auto, and only for external participants of the call (not the PM's own domains or free-mail addresses without a
   name).*
5. **Leads.** *Recommend: later. The Call and the Deal cover the sales loop; Leads need extra scopes and a Sales Hub
   plan.*
6. **Meetings as HubSpot objects.** Calendar sync in HubSpot already creates Meetings. *Decided for H5 (owner task
   PB-61): log the Meeting, but when HubSpot's own sync made it, attach the notes to that Meeting and create nothing;
   never overwrite its title or invite.* Review round 3: which synced Meeting is the call's is confirmed by the PM's
   pick (section 6), never decided by title or time alone.
7. **A custom Pacenote Call id property** instead of `hs_call_external_id`. *Recommend: keep the external id
   (needs no extra scope); add the custom property only if a portal rejects it.*
8. **Two Pacenote users on one call.** *Recommend: accept two Calls for now (each user's own record); revisit with
   organization features.*
9. **Auto for Notes and Tasks.** *Recommend: no. The note and the task are texts a PM wants to review; keep them
   behind Apply and Undo.*
10. **Call outcome.** *Recommend: always `Connected`; map No answer and Voicemail only when Pacenote one day starts
    logging calls that never connected.*

## 11. Known gaps and not verified yet (before release)

- Auto has no place to show an error (rule 5 says "where the action was"). A failed Auto log is recorded only as an analytics error code; the Call is then logged by Update HubSpot. Auto does nothing for a call that has no CRM link or no history record 150 seconds after the call ended.
- The link back is the Notion page; a call without a Notion page has only the `Pacenote call id` line in the Call.
- HubSpot search is not instant after a create: a create that timed out may not be found at once, and the next run
  (Apply or Auto) finds it by the Pacenote call id.
- Relink of an old call finds contacts only while the calendar still holds the event (3 hours back); otherwise it
  links the company by name and the deal, without contacts.

- A live portal check of the Call create, update and association calls, and of `hs_call_external_id` in search. The
  H2 tests use a mocked HubSpot API only and never call the real one.
- The scopes: that calls need no scope beyond the ten listed.
- The outcome id `Connected` and `hs_call_direction` on a portal with a custom calling app.
- H5, on a live portal: that a Meeting and an Email engagement can be created with the contacts scopes alone (otherwise
  `crm.objects.meetings.*` or the email scopes are needed, and the 403 line names them); the association type ids
  (meeting 200, 188, 212; email 198, 186, 210); that `hs_internal_meeting_notes`, `hs_email_html` and `hs_email_headers`
  are accepted; that the search by `associations.contact` together with `hs_timestamp` finds a calendar-synced Meeting;
  that a GET with `associations=contacts,companies,deals` lists the associations and that the v4 association DELETE
  removes one; and how HubSpot shows an email that has no `hs_email_headers`.
- H5: a calendar-synced Meeting is never written to without the PM's pick. Nearby Meetings are found by the call's
  contacts and start; the call's title and the calendar event's title and start only order the list, because the
  event comes from the call's Meeting Sheet, which may be another meeting's preparation reused at Start call. The call
  record does not keep the calendar event it was started from (a follow-up could store it with the call), and which
  HubSpot meeting property holds the calendar's event id is not verified on a live portal. A meeting that was moved in
  the calendar by more than 15 minutes, or a synced meeting without any of the call's contacts, is not offered. Update
  HubSpot lists every nearby meeting and "Make a new Meeting instead"; the pick is kept with the call (meetingId).
- H5: an email is matched when HubSpot's logged text holds the first 200 characters of the draft (spaces and case
  ignored). An email the PM changed after copying the draft is not matched, and a second email engagement is created.
- H5: the follow-up draft is kept only in memory, so the Email row exists only once a draft was written in this session of the app.
