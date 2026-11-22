# HubSpot CRM integration: spec (PB-57)

Status: draft for the owner. Part 2 of the first slice (H2, PB-58, the Call engagement) and H3 (PB-59, find or create
contacts, companies and leads) are built on this branch; the rest is planned. Source of truth for behavior today: `app/Sources/LiveTipsCore/CRM.swift`,
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
  the Call when the PM turns on the Auto switch (off by default, section 9) and the H3 people objects, each with its
  own Auto switch (off by default, section 12).
- **H3 (built)**: Update HubSpot matches the invitees and proposes missing contacts, the company and Leads (section 12).
- **Not yet**: Meetings, Emails, OAuth, several portals.

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
| Meeting | the scheduled event | recent activity | later (decision 6) | contact 200, company 188, deal 212 |
| Email | the follow-up email | recent activity | no, never: Pacenote does not send mail through HubSpot | |
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
   write needs its own switch, also off by default.
2. **Transcript text is never sent** to the Call, the Task, the Deal or any field. The note may carry it only when the
   PM turns on "Add the transcript to the CRM note" (off by default). Today (built in 0.8.7) the setting puts the
   transcript into the note when the Update HubSpot window opens; the "Attach the transcript" row only shows that, and
   unticking it does not take the transcript out of the note. Privacy follow-up: make the row the per-Apply consent
   (the note gets the transcript only when the row is ticked at Apply).
3. **Texts come from the call page as the PM edited it.** Pacenote never writes a text the PM could not see on the call
   page.
4. **Undo.** A new object is deleted; an updated object gets its old property values back. Associations added during a
   re-run are not removed by Undo (they are harmless and HubSpot has no history for them); the Undo failure line says
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
- A call's link has one writer at a time. Call writes (Auto and Apply) and Relink of the same call take turns on one
  per-call queue, so Relink waits for a write that is waiting for HubSpot. Each write reads the link (Call id, portal,
  associations) when its turn comes, and stores the Call id HubSpot returns only if the link still names the same
  portal and Pacenote call id when the answer comes; otherwise the id is dropped, never put into another portal's link.
- Several portals at once (an agency with client portals): one key per project in Settings > Projects, kept in the
  Keychain under a per-project name. Not built; decision 3.
- Data hosting regions (US, EU) use the same base URL for the API; the region is only displayed.
- Never merge data across portals: the snapshot, the link and the Call ids are per call, and a call has one portal.

## 9. Auto, review and Undo flow (H2)

1. Start call: Refresh links company, deal and contacts (`.crm.json` of the preparation). Refresh saves the link with
   the same preparation Start call copies (the one chosen by hand, else the planned one, else where Prepare will write)
   and reads the link stored there at the moment it runs, so picking another preparation never moves the first one's
   company and deal into it. The call request names that preparation (`crm_base`); when the recording starts the
   link is copied from it to the call's own `<stem>.crm.json`, without `callKey` and `callId`, only when its identity
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
