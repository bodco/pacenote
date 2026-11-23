# HubSpot CRM integration: spec (PB-57)

Status: draft for the owner. Part 2 of the first slice (H2, PB-58, the Call engagement), H3 (PB-59, find or create
contacts, companies and leads), H4 (PB-60, deals) and H5 (PB-61, the Meeting and Email engagements) are built on this
branch; the rest is planned. Source of truth for behavior today: `app/Sources/LiveTipsCore/CRM.swift`,
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
  follow-up task, since H2 the Call, since H5 the Meeting and the sent Email), **Apply**, then **Undo** for 8 seconds.
  Every write needs that click, except the Call when the PM turns on the Auto switch (off by default, section 9) and
  the H3 people objects, each with its own Auto switch (off by default, section 12). The Meeting and the Email have no
  Auto switch.
- **H3 (built)**: Update HubSpot matches the invitees and proposes missing contacts, the company and Leads (section 12).
- **H4 (built)**: Update HubSpot suggests deal fields with their evidence, offers the stages of the deal's own
  pipeline and moves the Call's deal association with the call's deal (section 3).
- **H5 (built)**: the call as a Meeting with the call page's notes, and the follow-up the PM sent as an Email
  engagement (section 3).
- **Not yet**: OAuth, several portals.

## 2. Objects and associations

HubSpot association type ids below are HUBSPOT_DEFINED, from the object on the left to the object on the right.

| Object | Pacenote use | Read | Write | Associates with |
|---|---|---|---|---|
| Contact | the call's participants | yes, by email, then by name inside the company | H3: create when missing, unticked row or its own Auto switch | company (HubSpot does this itself by email domain) |
| Company | the account of the call | yes, by domain then name | H3: create when missing, unticked row or its own Auto switch | contacts, deals |
| Deal | the sales opportunity | yes (open deal, stage, amount, next step) | yes: next step, close date, stage only if the PM types one, qualification fields | company, contacts |
| Call | the logged call (H2) | no | yes (H2, built) | contact 194, company 182, deal 206 |
| Note | summary, decisions, next steps | yes (recent activity) | yes (built in 0.8.7) | contact 202, company 190, deal 214 |
| Task | the follow-up | open tasks | yes (built in 0.8.7) | contact 204, company 192, deal 216 |
| Meeting | the call's calendar slot | recent activity; searched by the call's contacts and start time to find HubSpot's own | yes (H5, decision 6): a new Meeting, or the notes added to the one HubSpot's calendar sync made | contact 200, company 188, deal 212 |
| Email | the follow-up email the PM sent | recent activity | yes (H5): an email engagement that records an email the PM sent. Pacenote never sends mail, through HubSpot or otherwise | contact 198, company 186, deal 210 |
| Lead | an unqualified prospect | contact's leads (only to avoid a second one) | H3: opt in (Settings), unticked row or its own Auto switch | contact 578, company 610 |

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
| Deal `hs_next_step`, `closedate`, `dealstage` (H4, `LiveTipsCore/CRMDeals.swift`) | next step: the agreed step on the call page. Close date: a mutual-plan step with a closing word (close, sign, signature) and a real `YYYY-MM-DD` date not in the past, written as UTC midnight; the first such step is chosen and only then compared with the deal, so a re-run never alternates between two steps. Stage: only when a signal of the call reports that the milestone happened (a completion word such as sent, signed, presented, надіслали; no negation, plan, wish, request or question; "agreed to", "decided to" and the UK infinitive after погодились and the like are plans) and names a later open stage of the deal's own pipeline (generic stage words from `rules/sales.yaml > stages`, matched to the portal's stage labels). The signal must establish that stage's own milestone (`CRMDealSuggester.establishes`): it names the same thing as the label (contract, proposal or quote, demo or presentation, decision, negotiation, signature, closing), and when the label says what happened to it ("Contract sent") the signal says that or something that implies it (signed, received, shared, delivered or submitted imply sent; reviewed, discussed or agreed to send do not), both in the same clause of the signal, with the stage's thing as the object of that completion word: the first milestone noun after it, else the first of the clause, never one inside a phrase such as "for the contract" or "щодо контракту" ("we sent the proposal and reviewed the contract" and "we sent the proposal for the contract" establish Proposal sent, not Contract sent); plan steps and the next step are still to do and never move the stage; never a closed stage; or a stage the PM picks from that pipeline's list |
| Deal qualification properties | per method criterion to a deal property chosen in Settings (or "Don't write"), only for a known or partial criterion that has a quote |

H4 rules: every suggestion carries its evidence (the quote, or the agreed step itself for the next step and the close
date) and a field without evidence is absent, not unticked. The evidence is shown in Update HubSpot and never sent
(rule 2 of section 5). One `updateDeal` call writes the ticked fields and returns the old values for Undo. The stage id
must belong to the deal's `pipeline`. The PM can switch the call to another open deal of the company or to No deal in
the same window. A switch clears the old deal and its suggestions at once; Apply is enabled only when the preview on
screen was read for the deal the call is linked to now, and a late answer for an earlier pick is dropped. The preview
checks the link's portal first (section 8) and reads nothing from another portal; a failed read of the company's
deals or the stages is shown with Try again, not as an empty list.

Deal association of the Call (H4): `<stem>.crm.json` keeps `callDealId`, the deal Pacenote associated the Call with.
When the call moves to another deal or to No deal, the next Call write (Apply or Auto, in the same turn of the per-call
queue) removes only that association (`DELETE /crm/v4/objects/calls/{id}/associations/deals/{dealId}`, a 404 counts
as removed) and stores the new deal only when Pacenote added it: a picked deal that was already on the Call (a person
added it in HubSpot) is not stored, so a later No deal or another pick leaves it. Associations Pacenote did not make (a deal a person added, the company, the
contacts) stay. Before an existing Call moves, its deal associations are read (`GET
/crm/v4/objects/calls/{id}/associations/deals`), so the new deal counts as added only when it was not on the Call
already. The Apply's Undo row (`callDeal`) keeps the removed deal, the added deal and the managed deal before the
move; Undo takes the added deal off, associates the removed one again and stores the old managed deal, so a switch
from D1 to D2 ends with D1 only and a deal a person had already added stays. A Call logged before `callDealId` existed
has no managed deal, so nothing is removed for it. A Call found by search after `<stem>.crm.json` lost its id has no
managed deal either; its deal associations are read before the new deal is added, as for any Call.

Write order (review round 6): the deal is never part of the Call write. The Call is written first (company and
contacts only), then the old managed deal is taken off, then the new deal is added; `callDealId` is stored after each
step, so it always names the deal Pacenote has on the Call. A failure at any step stops there: a failed Call update
changes no deal; a failed removal keeps the old deal managed and adds nothing; a failed add after the removal is in the
Apply's Undo (which links the old deal again). If the add answered an error but may have reached HubSpot, Pacenote takes
it off again; when that fails too, the new deal is stored as managed and kept in Undo, so Undo or the next No deal
removes it. A new Call gets its deal by the same add after it is created, and its Undo deletes the whole Call.

With the H2 fix round 8 rules (integration of PB-58 and PB-60): the deal move takes the same turn of the per-call
queue as the Call write and Relink. The deal the Call should have is the link's deal read when the turn comes, with the
other associations, never a value captured when Apply or Auto started. The Call id and `callDealId` are stored only
while the link still names the portal and Pacenote call id the write used; if the link was relinked in between,
nothing is stored and no deal is moved on that Call. Undo of a `callDeal` row goes through the same Undo as the rest of
the batch (H3): the portal is checked first and, on another portal, nothing is sent and the batch is kept. A link
carried from a preparation to a recorded call leaves out `callDealId` together with `callKey` and `callId`.

### The PM's tasks in Tracker (PB-17)

The PM's open HubSpot tasks in On you, the Tracker and the call window, with Add to HubSpot tasks, ticks, Remove and
the after-call add (off by default). Fields, sync, dedupe and the choice between Notion and HubSpot for new tasks are in
`rules/hubspot-tasks.yaml`. A new task: `hs_task_subject` the item's text, `hs_task_status` NOT_STARTED,
`hs_timestamp` its due date (else the default from the settings), `hs_task_type`, `hs_task_priority` and
`hs_queue_membership_ids` from the settings, `hs_task_body` the call's title, date and link (never transcript text),
`hubspot_owner_id` per section 7.

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
| Leads (H3, only when "Propose a Lead" is on in Settings; not in the ten scopes above) | `crm.objects.leads.read`, `crm.objects.leads.write` |
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
   re-run are not removed by Undo (they are harmless and HubSpot has no history for them), except the deal
   association Pacenote itself added on a deal move (H4, below) and the associations a Meeting or Email write added
   (H5, below); the Undo failure line says
   what is left. Undo first checks that the key still belongs to the portal the Apply wrote to (H3 keeps that portal
   with the batch); after a key change, or when the portal cannot be read, it sends nothing, says so and keeps the
   batch, as the same ids in another portal are other records. A re-run of the Call adds the associations first and
   changes the fields last, so a failed association leaves the Call's fields as they were. Associations added before
   the one that failed stay on the Call and are outside Undo, like every association a re-run adds; the Apply reports
   the failure. The people lookup of the review (H3) checks the portal the same way before its first request: on
   another portal no participant email is sent, no match is kept for Apply and the call asks for Relink. Relink takes
   its turn in the call's write queue; the people write reads and saves the stored link in its own turn and, when the
   link now names another portal, adds nothing to the Call and saves nothing on the link (what it made stays for
   Undo). The save merges into the link read in that turn. The call key (the queue key) is made and stored before
   Auto, Apply or Relink starts any request, so all three share one queue even for a call that was never logged.
   (H4) The one association Pacenote removes is the Call's link to the deal Pacenote itself associated, when the
   call moves to another deal or to No deal (section 3); Undo of that Apply takes the deal Pacenote added off and
   associates the old deal again, with the same portal check and batch keeping.
   Meetings and Emails (H5) go further:
   they add only the associations the object does not have yet, before any field; if an association or the field
   update then fails, the associations just added are removed again, so a failed write leaves the object as it was.
   Their Undo also removes the associations the write added. Undo of a Meeting's notes never writes back the whole
   field: its record holds only Pacenote's block before and after Apply, and Undo reads the notes again, puts the old
   block back in place of Pacenote's block (or removes it) and keeps all other text, also text added after Apply. If
   Pacenote's block was edited or removed in HubSpot since Apply, the notes are kept as they are and Undo says so. An
   edit is any change to the block's words, tags or link targets (href, src); only HubSpot's own rewrite of the markup
   (`<strong>` for `<b>`, `<em>` for `<i>`, entities, spacing, `style` or `target` attributes) is not. A link target
   is compared as written: only the quotes that open and close the attribute value are dropped. A Meeting Pacenote
   created is not deleted blindly either: its record holds the block Apply wrote, and Undo reads the notes again and
   deletes the Meeting only while they are that block and nothing else (empty tags aside). With notes added in HubSpot
   since, the Meeting stays with them, only Pacenote's block is removed, its stored id stays, and Undo says so; with
   the block edited, nothing is changed and Undo says so. A Meeting already deleted in HubSpot counts as undone.
   Undo of the whole batch (Call, deal moves, people, Meeting, Email) is one path (`CRMUndo.run`) and runs in the
   call's turn (the same queue as the writes and Relink), with the same portal check and batch keeping.
5. **Errors are one line where the action was**, never a modal; 429 waits (Retry-After, else 1, 2, 4 s) and retries three
   times; 401 and 403 say what to fix (key, or the named scope).
6. **No silent overwrites of the PM's own edits in HubSpot.** A re-run updates only the Pacenote-owned properties of the
   Call (section 3), and the old values are kept for Undo.
7. Usage log and analytics record only counts and an error code, never names, texts or ids.

## 6. Dedupe rules

| Object | Key | Rule |
|---|---|---|
| Contact | email (lowercase, trimmed) | search `email EQ`; found: use it; several: take the one on the matched company, else the oldest; never create a second contact with the same email. A contact is also matched by first and last name among the contacts of the matched company (the email may differ). Creating is H3 (section 12) |
| Company | domain, then name | search `domain EQ`, then `name EQ`; free-mail domains (gmail.com, outlook.com, icloud.com, yahoo.com, hotmail.com) and the PM's own domains never match a company. A missing company is created (H3) with the domain, after searching the domain again |
| Call | the Pacenote call id | the id is made once and stored with the call (`<stem>.crm.json`, `callKey`) together with the HubSpot Call id (`callId`). A re-run PATCHes `callId`. If `callId` is missing, HubSpot is searched by `hs_call_external_id = pacenote:<id>`. If the stored id was deleted in HubSpot (404), the same search runs, then a new Call is created. Auto and Apply of one call take turns (a queue per call id) and each reads the stored `callId` when its turn comes. A create that gets no answer or a 5xx is checked by the same search, three times over about 6 seconds (HubSpot's search lags a new record), before it is reported as failed; the next run searches first, so a Call that shows up later is updated, not made again. A second Call is possible only if the search still misses it on that next run (see the known gaps) |
| Meeting | the Pacenote call id, then HubSpot's own meeting | the HubSpot Meeting id is stored with the call (`<stem>.crm.json`, `meetingId`). A re-run updates it. Without it (or on a 404), HubSpot is searched for Meetings associated with one of the call's **contacts** (a shared company or deal alone is never enough) whose `hs_timestamp` is within 15 minutes of the call's start. A Meeting holding this call's block is ours from an earlier run and is used; a Meeting holding another call's block is never used. Any other Meeting is never written to without the PM's pick, whatever its title or start: nothing is written to any Meeting and Update HubSpot lists every Meeting near the call that the search returned, and the PM picks one, which is then used without a search and kept as `meetingId`, or picks a new Meeting. A title or a start alone cannot decide, because the calendar event comes from the call's Meeting Sheet (`meeting_ref.event_id` of `<stem>.sheet.json`, title and start from the calendar or, once the calendar dropped it, from the sheet), and Start call copies the sheet of whichever preparation the PM chose, possibly another invite's. So the event only orders the list: Meetings whose title is the call's or the event's (case and spaces aside), or whose `hs_meeting_start_time` is within a minute of the event's start, come first, then the rest closest first. The event nearest the call is never taken for it. The Email row of the same Apply still goes. Used: only this call's block in `hs_internal_meeting_notes` is replaced or appended (section 3), the title, invite and every other property stay, the missing associations are added, and Undo takes back only Pacenote's block (section 5, rule 4) and removes the added associations. Not found: a new Meeting is created. The Meeting writes take turns with the Call writes of the same call (the same queue, with Relink and Undo); the ids, the portal and the associations are read from `<stem>.crm.json` inside the turn and the ids stored there |
| Email | the stored id and who made it, then contact, subject and text | the HubSpot Email id is stored with the call (`emailId`) together with `emailOurs`: whether Pacenote created it. A re-run updates the email only when Pacenote created it. Without a stored id, HubSpot is searched for emails on one of the call's contacts in the last day with the same subject whose text holds the start of the draft (HubSpot's own mail logging adds signatures and quotes). Exactly one: it is stored as not ours and stays read only: only the missing associations are added, now and on every re-run, and Undo removes only those. Several: the email is logged already; nothing is written. None (or no contact): a new Email is created and stored as ours. The same subject with another text is never claimed |
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
  Pacenote call id and the calendar occurrence, and find the company, deal and contacts again in the current portal
  (by the attendees' domains of the occurrence the link was saved for while the calendar still has it, else by the
  linked company's name). Relink never takes attendees from a meeting near the recording's start: an overlapping
  meeting can be another customer's.
- A call's link has one writer at a time. Call writes (Auto and Apply), the Meeting and Email writes (H5), Relink and
  Undo of the same call take turns on one per-call queue, so Relink waits for a write that is waiting for HubSpot. Each
  write reads the link (Call, Meeting and Email ids, portal, associations) when its turn comes, and stores the id
  HubSpot returns only if the link still names the same portal (and, for the Call, the same Pacenote call id) when the
  answer comes; otherwise the id is dropped, never put into another portal's link. A Meeting or Email row made from a
  link of one portal writes nothing once the stored link names another.
- (H4) The same check runs before the deal preview is read and before Undo, so stored deal and Call ids are never
  used against another portal.
- (H5) The Meeting and Email writes check the portal again inside their turn, and Undo checks it before it writes. The
  Meeting pick and the Undo records are bound to the portal that produced them (not to the link's portal of now): a
  pick from another portal writes nothing, Undo runs only while the key is of the portal it was written in, and a
  successful Relink clears both.
- Several portals at once (an agency with client portals): one key per project in Settings > Projects, kept in the
  Keychain under a per-project name. Not built; decision 3.
- Data hosting regions (US, EU) use the same base URL for the API; the region is only displayed.
- Never merge data across portals: the snapshot, the link and the Call ids are per call, and a call has one portal.

## 9. Auto, review and Undo flow (H2)

1. Start call: Refresh links company, deal and contacts (`.crm.json` of the preparation). Refresh saves the link with
   the same preparation Start call copies (the one chosen by hand, else the planned one, else where Prepare will write)
   and reads the link stored there at the moment it runs, so picking another preparation never moves the first one's
   company and deal into it. The call request names that preparation (`crm_base`); when the recording starts the
   link is copied from it to the call's own `<stem>.crm.json`, without `callKey`, `callId`, `callDealId` and the H5 `meetingId`, `emailId` and
   `emailOurs` (they belong to one call), only when its identity
   is verified: the PM chose the preparation by hand (`crm_chosen`), or the link's `occurrence` (the calendar event id
   and start Refresh saved it for) equals the call's (`crm_event`). The planned preparation is found by title and
   occurrence time, which never proves whose call it is, so its link alone is not carried; Refresh does not keep an
   unverified link's company and deal, and the Start call row shows only a link that will be carried. A call without a
   calendar event and without a chosen preparation stays unlinked. The preparation Prepare is writing for this call when
   it starts (late prep) carries its link only on the same occurrence match. A link the call already
   has is kept. A title is not a call's identity, so the recorder's automatic pick by title (`lt prep-check
   --adopt-prep`) never carries a link: a call started without `crm_base` (another start path) stays unlinked, and
   its Summary never goes to another customer's records.
2. The call ends. If Auto is on and the call is linked in the portal of the current key (section 8), Pacenote waits for the call page's notes (about 2.5 minutes),
   then logs the Call. No review and no Undo in this path; the switch is the consent. The PM sees it in HubSpot and in
   the call's Update HubSpot window (Call row, ticked).
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
- Relink of an old call finds contacts only while the calendar still holds the occurrence the link was saved for (3
  hours back); otherwise, and for links saved without an occurrence, it links the company by name and the deal,
  without contacts.

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

## 12. Contacts, companies and leads (H3, PB-59)

Code: `app/Sources/LiveTipsCore/CRMPeople.swift` (matching, proposals, writes; the `CRMConnector` protocol is not
changed, `HubSpotConnector` adds `CRMPeopleConnector`) and `app/Sources/LiveTips/CRMPeopleFlow.swift` (rows, Apply
step, Auto). Checks: CoreChecks `crmPeople`, with a mocked HubSpot only.

**Participants.** The invitees of the call's calendar event (the PM left out). The event is only the occurrence the
link was saved for (`occurrence`, section 8), never a meeting near the call's time or with its title, which may be
another customer's; a link without an occurrence, or one the calendar no longer has, gives no invitees and no rows.
The emails are lowercased and deduped, with
names paired by position when both lists have the same length, else the name comes from the email. The calendar has
no job titles, so the job title (`jobtitle`) comes from the call's project: the "Team - Client" table of the project
config (name and role, the same people list the call page uses), matched by the full name (two words at least). The
PM's own team is never used; no match leaves the job title empty, it is never guessed. The stakeholder rows of the
sales data hold only a buying role (decider, champion), which is not a job title and is not written.

**Match, reads only.** (1) Contacts by email, five at a time. (2) Each invitee's company comes from their own email
domain (never the PM's own domains or free-mail domains). The company the call is linked to, or the one found by the
account's name, stands in only on a call with a single client domain. (3) An invitee whose email was not found is
looked up by exact first and last name (two words at least) among all the contacts of their own company (every page,
read once per company); a hit is the same person and is reused. Matched contacts and the company go into
`<stem>.crm.json` (ids and names only), so the Call of H2 is associated with them.

**Propose.** A company when none matched and an invitee has a client domain: the account's name, else the domain's
first label ("orbit.example" gives "Orbit"). A contact for each unmatched external invitee with an email, with name,
email, job title and company; a free-mail address is skipped unless the invitee has a real name (decision 4). A Lead
per proposed contact, and per matched contact that has no Lead yet, only when "Propose a Lead" is on and the call has
no open deal. Rows are unticked.

**Write.** Order: company, contacts, leads, then the Call, the note and the task with the new ids. Before every create
HubSpot is searched again (domain, email, the contact's leads), and a 409 on a contact reuses the id HubSpot names, so a
stale review, a double click or a second Mac never makes a duplicate; what existed is reused and not undone. Contact
to company uses association type 1; Lead to contact 578 and to company 610 (HUBSPOT_DEFINED). A Call logged earlier
gets the people through the default association `PUT /crm/v4/objects/calls/{id}/associations/default/{type}/{id}`.
These PUTs are idempotent and every run (Apply or Auto) sends them again for all the companies and contacts of the
link, so an association that failed once is made on the next run even though the link already lists the id. A call
with people from several companies is associated with every one of them: the link keeps the other companies in
`companyIds`, and the Call of H2, the note and the task get them too. A failed lead lookup (no Leads scopes) drops only
the Lead rows; the contact and company rows stay and the review names the error.
Owners are never set on contacts and companies (section 7). Undo deletes the created objects and takes them out of the
link; the usage log keeps counts only.

**Auto.** Four switches in Settings > Integrations > HubSpot, all off by default: create contacts, create the company,
propose Leads, create Leads. Each Auto switch writes only its own object, after the call ends, for a call that has a CRM
link, before the Call is logged. Create Leads never creates a contact: without Create contacts, a new contact's Lead
stays an unticked row in Update HubSpot (ticking it creates the contact with it); a contact already in HubSpot gets
its Lead automatically. The owner's decisions 4 and 5 recommended no Auto and later for Leads; the task for
H3 asked for both behind their own switches, so they are built that way. Turn them off by default and keep them off.

**Not verified yet (needs a live portal):** `hs_lead_name` and association type ids 578 and 610 for Leads, whether a
portal without Sales Hub Leads answers 403 or 404, the 409 message format ("Existing ID: n") on contacts, that a contact
created with a company association (type 1) is accepted with the ten scopes, and `hs_additional_emails` (a second email
of the same person is matched by name only).
