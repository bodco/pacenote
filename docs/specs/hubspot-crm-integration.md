# HubSpot CRM integration: spec (PB-57)

Status: draft for the owner. Part 2 of the first slice (H2, PB-58, the Call engagement) is built on this branch; the
rest is planned. Source of truth for behavior today: `app/Sources/LiveTipsCore/CRM.swift`,
`app/Sources/LiveTips/CRMFlow.swift`, `rules/crm.yaml`.

## 1. Where we are

Pacenote already connects to HubSpot with a service key (a private app key) kept in the Keychain
(`SecretStore.Key.hubspotKey`). The key is sent as `Authorization: Bearer <key>` to `https://api.hubapi.com`.

- **Read** (before a call): `CRMConnector.findCompany` (domain, then name), `findContacts` (emails), `deals`
  (open deal updated last), `recentActivity`, `pipelines`, `properties`, `owners`. The result is the local snapshot
  `<stem>.crm.md` and the link `<stem>.crm.json`: the ids of the company, deal and contacts, the company, deal and
  participant names, and since H2 the Pacenote call id (`callKey`), the HubSpot Call id (`callId`) and the portal id
  (`portalId`). The names stay on the Mac; they go to HubSpot only inside the Call.
- **Write** (after a call): "Update HubSpot" on the call page. A reviewed list of checkboxes (note, deal fields,
  follow-up task, and since H2 the Call), **Apply**, then **Undo** for 8 seconds. Every write needs that click, except
  the Call when the PM turns on the Auto switch (off by default, section 9).
- **Not yet**: Leads, Meetings, Emails, creating contacts and companies, OAuth, several portals.

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
| Meeting | the scheduled event | recent activity | later (decision 6) | contact 200, company 188, deal 212 |
| Email | the follow-up email | recent activity | no, never: Pacenote does not send mail through HubSpot | |
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
   write needs its own switch, also off by default: the switches of Settings > Integrations > HubSpot > Sync, read
   through `CRMSettings.autoWrites` (section 12). Notes have no switch (decision 9).
2. **Transcript text is never sent** to the Call, the Task, the Deal or any field. The note may carry it only when the
   PM turns on "Add the transcript to the CRM note" (off by default). Today (built in 0.8.7) the setting puts the
   transcript into the note when the Update HubSpot window opens; the "Attach the transcript" row only shows that, and
   unticking it does not take the transcript out of the note. Privacy follow-up: make the row the per-Apply consent
   (the note gets the transcript only when the row is ticked at Apply).
3. **Texts come from the call page as the PM edited it.** Pacenote never writes a text the PM could not see on the call
   page.
4. **Undo.** A new object is deleted; an updated object gets its old property values back. Associations added during a
   re-run are not removed by Undo (they are harmless and HubSpot has no history for them); the Undo failure line says
   what is left. Undo first checks that the key still belongs to the portal the Apply wrote to; after a key change
   it sends nothing and says so, as the same ids in another portal are other records. A re-run of the Call adds the associations first and changes the fields last, so a failed association
   leaves the Call's fields as they were and nothing changed is outside Undo.
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
| Call | the Pacenote call id | the id is made once and stored with the call (`<stem>.crm.json`, `callKey`) together with the HubSpot Call id (`callId`). A re-run PATCHes `callId`. If `callId` is missing, HubSpot is searched by `hs_call_external_id = pacenote:<id>`. If the stored id was deleted in HubSpot (404), the same search runs, then a new Call is created. Auto and Apply of one call take turns (a queue per call id) and each reads the stored `callId` when its turn comes. A create that gets no answer or a 5xx is checked by the same search, three times over about 6 seconds (HubSpot's search lags a new record), before it is reported as failed; the next run searches first, so a Call that shows up later is updated, not made again. A second Call is possible only if the search still misses it on that next run (see the known gaps) |
| Note, Task | none | created per Apply; the 8 second Undo and the "already applied" lock protect against a double click. No dedupe across sessions (decision 8) |
| Deal | the PM's link | the open deal updated last, or what the PM chose in Start call; "No deal" is kept for the series |

Known gap: if both `<stem>.crm.json` is lost and the Call has no `hs_call_external_id` (for example a portal that
rejects the property), a re-run creates a second Call. Mitigation: the body carries `Pacenote call id: <id>`; a
manual search in HubSpot finds it.

Known gap: a create whose answer is lost, followed by a retry while HubSpot's search still does not show the new
Call (longer than the 6 seconds above), makes a second Call. Auto runs once per call and Apply is a click, so this
needs a lost answer and a fast manual retry; the body's `Pacenote call id: <id>` finds both in HubSpot.

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
  while the calendar still has the event, else by the linked company's name).
- Several portals at once (an agency with client portals): one key per project in Settings > Projects, kept in the
  Keychain under a per-project name. Not built; decision 3.
- Data hosting regions (US, EU) use the same base URL for the API; the region is only displayed.
- Never merge data across portals: the snapshot, the link and the Call ids are per call, and a call has one portal.

## 9. Auto, review and Undo flow (H2)

1. Start call: Refresh links company, deal and contacts (`.crm.json` of the preparation). Refresh saves the link with
   the same preparation Start call copies (the one chosen by hand, else the planned one, else where Prepare will write)
   and reads the link stored there at the moment it runs, so picking another preparation never moves the first one's
   company and deal into it. The call request names that preparation (`crm_base`); when the recording starts the
   link is copied from it to the call's own `<stem>.crm.json`, without `callKey` and `callId`. The preparation Prepare
   is writing for this call when it starts (late prep) carries its link the same way. Known limit: the planned
   preparation is found by title and occurrence time (within 3 hours of the call), so two same-title calls of
   different customers that close together show the other one's company in the Start call row; the PM sees it there
   before starting. A link the call already
   has is kept. A title is not a call's identity, so the recorder's automatic pick by title (`lt prep-check
   --adopt-prep`) never carries a link: a call started without `crm_base` (another start path) stays unlinked, and
   its Summary never goes to another customer's records.
2. The call ends. If Auto is on and the call is linked in the portal of the current key (section 8), Pacenote waits for the call page's notes (about 2.5 minutes),
   then logs the Call. No review in this path; the switch is the consent. The PM sees it in HubSpot and in the call's
   Update HubSpot window (Call row, ticked, and a line in the sync log, where Undo works for a day, section 12).
3. Otherwise the PM opens Update HubSpot on the call page: the Call row is ticked by default (Settings can untick it),
   Apply writes, Undo works for 8 seconds.
4. A re-run (Apply again later) updates the same Call, keyed by the stored Call id and `hs_call_external_id`; the one
   exception is the lost create answer in section 6 (Dedupe), which can leave a second Call. Auto runs once, when the
   call ends; an edit of the call page afterwards reaches HubSpot only through Apply.

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
6. **Meetings as HubSpot objects.** Calendar sync in HubSpot already creates Meetings. *Recommend: do not write
   Meetings; link the Call to the same contacts instead.*
7. **A custom Pacenote Call id property** instead of `hs_call_external_id`. *Recommend: keep the external id
   (needs no extra scope); add the custom property only if a portal rejects it.*
8. **Two Pacenote users on one call.** *Recommend: accept two Calls for now (each user's own record); revisit with
   organization features.*
9. **Auto for Notes and Tasks.** *Recommend: no. The note and the task are texts a PM wants to review; keep them
   behind Apply and Undo.*
10. **Call outcome.** *Recommend: always `Connected`; map No answer and Voicemail only when Pacenote one day starts
    logging calls that never connected.*

## 11. Known gaps and not verified yet (before release)

- Auto shows its errors in the call's sync log (H6, section 12) with Retry; the analytics error code is still recorded. Auto does nothing for a call that has no CRM link or no history record 150 seconds after the call ended.
- The link back is the Notion page; a call without a Notion page has only the `Pacenote call id` line in the Call.
- HubSpot search is not instant after a create: a create that timed out may not be found at once, and the next run
  (Apply or Auto) finds it by the Pacenote call id.
- Relink of an old call finds contacts only while the calendar still holds the event (3 hours back); otherwise it
  links the company by name and the deal, without contacts.

- A live portal check of the Call create, update and association calls, and of `hs_call_external_id` in search. The
  H2 tests use a mocked HubSpot API only and never call the real one.
- The scopes: that calls need no scope beyond the ten listed.
- The outcome id `Connected` and `hs_call_direction` on a portal with a custom calling app.
- H6: that the `updatedAt` in HubSpot's answer to a create or update is the one a later read returns, and that it does
  not move by itself after the write (HubSpot recalculations). If it moves, Undo from the sync log refuses ("changed in
  HubSpot") more often than needed; it never undoes over a change.

## 12. Sync settings, field and owner mapping, sync log (H6, PB-62)

Code: `app/Sources/LiveTipsCore/CRMSync.swift` (model and API), `app/Sources/LiveTips/HubSpotSync.swift` (Settings
group and the log view). Settings > Integrations > HubSpot > **Sync** has four parts.

### Settings API for the other branches

Read the settings only through these calls on `CRMSettings` (`SettingsWindow.model.config.crm` in the app). Stored in
`CRMSettings.sync` (`CRMSyncSettings`); every key has a default, so an old settings file decodes with everything off.

| Call | Returns |
|---|---|
| `autoWrites(_ o: CRMObject) -> Bool` | whether `o` may be written without a click. `CRMObject`: `calls`, `tasks`, `contacts` (contacts and companies), `deals`, `meetings`. All false by default. `calls` is the H2 switch (`autoLogCall && writeCall`), so there is one switch for the Call |
| `setAutoWrites(_ o:, _ on:)` | sets it (for `calls`, also ticks `writeCall`) |
| `property(_ o:, _ specProperty:) -> String` | the HubSpot property the spec property (section 3) is written to; `""` = Don't write. Fixed properties (`CRMObject.fixed`: dedupe keys, owner, statuses) always come back unchanged. A stored target that is reserved or already used by another field resolves to the field's default |
| `mapped(_ o:, _ props:) -> [String: Any]` | a property dictionary renamed by the mapping, Don't write dropped. Inputs that are not mappable keep their names and win: a mapped field never overwrites one of them or another mapped field. `HubSpotConnector.sync` applies it to the Call, the task and the deal fields; a new writer calls `mapped` on its properties before sending |
| `sync.canTarget(_ o:, _ specProperty:, _ target:) -> Bool` | whether a field may be mapped to `target`: never a reserved property (`CRMSyncSettings.reserved`: the object's fixed ones, `hubspot_owner_id`, `hs_call_external_id`, `hs_object_id`, HubSpot's create and change dates, the default property of another field) or what another field already writes. The picker offers only these and `setProperty` refuses the rest |
| `CRMCallSync.log(..., allowed:)` | the one writer of a call's Call: a queue per call id, the portal checked, the stored Call id read when the turn comes. Auto passes `allowed`, which asks `autoWrites(.calls)` again when its turn starts; off by then, nothing is sent and nothing is logged |
| `ownerId(forPacenoteUser email:, ownEmails:, owners:) -> String` | the HubSpot owner for a Pacenote user: the mapping in Sync; for the user of this Mac (one of `ownEmails`, or an empty email) the owner from Matching (`ownerId`); else the HubSpot owner with the same email; else `""` (leave `hubspot_owner_id` out, section 7) |
| `CRMSyncLog.append(_ entries:, stem:) -> Bool` | adds lines to the call's sync log; `CRMSyncLog.recordUndo(...)` for an Undo. `false` = the file was not saved: say so next to the HubSpot result, never instead of it |
| `CRMCallSync.undo(_ items:, portal:, connector:, check:, result:)` | the one Undo of written records: the portal first (a mismatch sends nothing), then `check` before each record (`CRMSyncLog.guardUndo`: inside the window and HubSpot's `updatedAt` equal to the write's revision), then delete, `restoreCall` or `restoreDeal` |
| `CRMScopeCheck.blocking(_ o:, missing:)` | the scopes an Auto switch needs (`CRMObject.scopes`: the reads its write path makes, then the write scopes) that the last check found missing |

Rules for a branch that adds an automatic write: check `autoWrites(o)` at the moment of writing (not at start; behind
a queue, when the turn comes), write only the objects of its own switch, map properties with `mapped`, set the owner
with `ownerId(forPacenoteUser:)`, and append one log line per object (ok or failed, with the one-line error, and
`revision` = HubSpot's `updatedAt` from the answer to the write, `HubSpotConnector.revision(type:id:)`, and `portal` =
the portal the write was checked against, so Undo is possible). A failed line names the object whose request failed,
never one that was skipped. Transcript text is never an input.

### Auto per object

One switch per `CRMObject`, all off by default. Today only Calls have a writer (H2); the switches for Tasks, Contacts
and companies, Deals and Meetings are stored and shown with "not written automatically yet" until their branch (H3,
H4, H5, Tracker tasks) reads them. Notes have no switch (decision 9). Meetings stay subject to decision 6. Each switch
names the scope it needs when the last check found it missing.

### Field mapping

Per object, each mappable field (`CRMObject.defaultFields`, the spec's properties in section 3) can go to another
writable HubSpot property of that object (listed from `/crm/v3/properties/<object>`) or to Don't write. Only the
overrides are stored; "Back to defaults" clears them. Fixed: Call `hs_timestamp`, `hs_call_status`,
`hs_call_disposition`, `hs_call_external_id`, `hubspot_owner_id`; Task `hs_timestamp`, `hs_task_status`,
`hubspot_owner_id`; Contact `email`, Company `domain`; Deal `dealstage` (written only when typed); Meeting
`hs_timestamp`, `hubspot_owner_id`. A field can never be mapped onto a fixed property, `hubspot_owner_id`,
`hs_call_external_id`, another field's default property, or a property another field already writes
(`canTarget`); a settings file edited by hand with such a mapping is ignored on write (the field goes to its default).
Qualification fields keep their own mapping (Qualification group); a deal field mapped onto a property a
qualification field writes goes to its default instead.

### Owner mapping

The first row is the user of this Mac and is the same setting as Matching > Owner. Other Pacenote users are added by
email and mapped to a HubSpot owner (default: the owner with the same email). Deal, contact and company owners are
never changed (section 7).

### Sync log

One file per call, `<stem>.crm-log.json` next to `<stem>.crm.json`, at most 200 lines. A line: time, object
(`calls`, `notes`, `tasks`, `deals`, `contacts`, `companies`, `meetings`), action (`create`, `update`, `undo`), source
(`click`, `auto`, `retry`, `log`), HubSpot id, the portal id, the property names written, the old values an update
replaced, ok or the one-line error, undone, and the revision (HubSpot's `updatedAt` from the answer to the write).
When an Apply fails, its failed line is the object whose request was out (`CRMApply.run` keeps it); a row that sent
nothing, such as a deal whose fields are all Don't write, gets no line. Property names and,
for an updated Call or deal, the old values Undo needs (local, next to the call); never transcript text, never the
key. Shown at the bottom of the call's Update HubSpot window.

- **Retry** on a failed line until a later write of the same object succeeds. A Call is written again at once (it never
  makes a second Call, section 6). A note, deal fields or a task reopen Update HubSpot with only that row ticked, so
  the PM still reviews and presses Apply.
- **Undo** on the latest good write of an object, within 24 hours (`CRMSyncLog.logUndoSeconds`; Apply's own Undo stays
  8 seconds, `applyUndoSeconds`). The click reads the log from disk again and rechecks the window and that no later
  write of the object exists (the button may be stale). Then Pacenote reads the object's `updatedAt` and goes on only
  when it is exactly the revision kept at write time; any later change, or a revision missing or unreadable on
  either side, refuses and says so (rule 6). A line without a revision (for example a Call found again after a
  create with no answer) offers no Undo. A created object is deleted; an updated Call or deal gets its old values
  back on exactly the properties written (`restoreCall`, `restoreDeal`), never through today's field mapping.
- Both Undo paths (Apply's 8 seconds and the log's 24 hours) go through `CRMCallSync.undo`: the key's portal must be
  the one written to (a line without a portal, from before this check, offers no Undo), and right before each record
  changes, the window must still be open and the revision unchanged. A refused record is left as it is and the line
  says why; it adds no undo line to the log.
- Undo in Update HubSpot (also exact-property), Auto and Retry write log lines too. If the log file cannot be saved,
  the window says so next to the HubSpot result (the HubSpot write is not undone by that).
- Apply, Auto and Retry of the Call all go through `CRMCallSync.log` (one queue per call id). The Pacenote call id is
  stored in `<stem>.crm.json` before any request, so the three paths use one key.
- Deal fields that the mapping sets to Don't write are not a write: Apply sends nothing for them and logs no line.

### Live check

Test (Connection) and the Sync group run one read per read-provable scope (`CRMScopeCheck.probes`: contacts,
companies and deals objects, owners, the three schemas). Only a 403 marks a scope missing; a probe that failed another
way (network, server error) is shown separately as "Check not complete" with its scopes, so a connected account with
an incomplete check does not look fully verified (`CRMScopeCheck.check` returns both lists). The Sync group checks again when opened, at most every five minutes, and has Check now. Write scopes cannot be
proved without writing; a missing one shows as the one-line 403 error of the first write and in the sync log.

Decision for the owner (H6): **the Undo window of the sync log.** *Recommend: 24 hours with the exact revision
check; shorter if a PM finds a day too long.*
