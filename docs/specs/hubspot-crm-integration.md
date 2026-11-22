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
| Meeting | the call's calendar slot | recent activity; searched by contacts, company, deal and start time to find HubSpot's own | yes (H5, decision 6): a new Meeting, or the notes added to the one HubSpot's calendar sync made | contact 200, company 188, deal 212 |
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
| `hs_call_body` | participants' names, Summary, Next steps, the link back to the call (the Notion page when there is one), and the line `Pacenote call id: <id>`. Never transcript text |
| `hs_call_external_id` | `pacenote:<Pacenote call id>`, the dedupe key |
| `hubspot_owner_id` | the owner chosen in Matching (section 7) |

### Meeting (H5)

| HubSpot property | From Pacenote |
|---|---|
| `hs_meeting_title`, `hs_timestamp`, `hs_meeting_start_time`, `hs_meeting_end_time` | the call's title, start and end (the call is the meeting) |
| `hs_meeting_outcome` | `COMPLETED` |
| `hs_internal_meeting_notes` | Pacenote's block: Summary, Decisions, Next steps, the link back, the line `Pacenote call id: <id>`. Never transcript text, even when "Add the transcript to the CRM note" is on |
| `hubspot_owner_id` | the owner chosen in Matching (section 7), for a new Meeting only |

The notes go to the internal notes field, not to `hs_meeting_body` (the invite text), which belongs to the PM and to
HubSpot's calendar sync. The block starts with the heading `Pacenote notes` and is always the last part of the field.

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
   PM turns on "Add the transcript to the CRM note" (off by default, per click shown as a row "Attach the transcript").
3. **Texts come from the call page as the PM edited it.** Pacenote never writes a text the PM could not see on the call
   page.
4. **Undo.** A new object is deleted; an updated object gets its old property values back. Associations added during a
   re-run are not removed by Undo (they are harmless and HubSpot has no history for them); the Undo failure line says
   what is left.
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
| Call | the Pacenote call id | the id is made once and stored with the call (`<stem>.crm.json`, `callKey`) together with the HubSpot Call id (`callId`). A re-run PATCHes `callId`. If `callId` is missing, HubSpot is searched by `hs_call_external_id = pacenote:<id>`. If the stored id was deleted in HubSpot (404), the same search runs, then a new Call is created. A second Call for one call id is never created |
| Meeting | the Pacenote call id, then HubSpot's own meeting | the HubSpot Meeting id is stored with the call (`<stem>.crm.json`, `meetingId`). A re-run PATCHes it. Without it (or on a 404), HubSpot is searched for a Meeting associated with any of the linked contacts, company or deal whose `hs_timestamp` is within 15 minutes of the call's start; the nearest one is used (HubSpot's calendar sync made it, or an earlier Pacenote run). Found: only Pacenote's notes block is replaced or appended in `hs_internal_meeting_notes`, the title, invite and every other property stay, the missing associations are added, and Undo restores the old notes. Not found: a new Meeting is created. Pacenote never creates a second Meeting of its own for one call (a calendar move over 15 minutes is the gap in section 11) |
| Email | the stored id, then subject and contacts | the HubSpot Email id is stored with the call (`emailId`). A re-run PATCHes it. Without it, HubSpot is searched for an email with the same subject on the same contacts, company or deal in the last day; found: it is left as it is (HubSpot's own mail logging may have made it) and only the missing associations are added; Undo has nothing to undo. Not found: a new Email is created |
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

- A key belongs to exactly one portal. Changing the key switches the portal; links in `<stem>.crm.json` of old calls
  keep their ids, which do not exist in the new portal, so Refresh reports "not linked" and the update plan is empty
  rather than writing to wrong ids. (To add: `portalId` in the link, and refuse to write when it differs from the
  connected portal.)
- Several portals at once (an agency with client portals): one key per project in Settings > Projects, kept in the
  Keychain under a per-project name. Not built; decision 3.
- Data hosting regions (US, EU) use the same base URL for the API; the region is only displayed.
- Never merge data across portals: the snapshot, the link and the Call ids are per call, and a call has one portal.

## 9. Auto, review and Undo flow (H2)

1. Start call: Refresh links company, deal and contacts (`.crm.json`).
2. The call ends. If Auto is on and the call is linked, Pacenote waits for the call page's notes (about 2.5 minutes),
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
3. **Several portals.** *Recommend: not now; add `portalId` to the link and refuse writes on a mismatch (cheap
   protection), per-project keys only when an agency user asks.*
4. **Create missing contacts and companies?** *Recommend: yes, but only as an unticked row in Update HubSpot, never in
   Auto, and only for external participants of the call (not the PM's own domains or free-mail addresses without a
   name).*
5. **Leads.** *Recommend: later. The Call and the Deal cover the sales loop; Leads need extra scopes and a Sales Hub
   plan.*
6. **Meetings as HubSpot objects.** Calendar sync in HubSpot already creates Meetings. *Decided for H5 (owner task
   PB-61): log the Meeting, but when HubSpot's own sync made it, attach the notes to that Meeting and create nothing;
   never overwrite its title or invite.*
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

- A live portal check of the Call create, update and association calls, and of `hs_call_external_id` in search. The
  H2 tests use a mocked HubSpot API only and never call the real one.
- The scopes: that calls need no scope beyond the ten listed.
- The outcome id `Connected` and `hs_call_direction` on a portal with a custom calling app.
- H5, on a live portal: that a Meeting and an Email engagement can be created with the contacts scopes alone (otherwise
  `crm.objects.meetings.*` or the email scopes are needed, and the 403 line names them); the association type ids
  (meeting 200, 188, 212; email 198, 186, 210); that `hs_internal_meeting_notes`, `hs_email_html` and `hs_email_headers`
  are accepted; that the search by `associations.contact`, `associations.company`, `associations.deal` together with
  `hs_timestamp` finds a calendar-synced Meeting; and how HubSpot shows an email that has no `hs_email_headers`.
- H5: the Meeting uses the call's own start and end, not the calendar event's (the event is not kept with the call). A
  meeting that was moved in the calendar by more than 15 minutes is not recognized and a second Meeting is created.
- H5: the follow-up draft is kept only in memory, so the Email row exists only once a draft was written in this session of the app.
