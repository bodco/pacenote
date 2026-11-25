# PB-122 (SX3): Server as the single source of truth

Status: spec only (D58: no code before the owner's OK). Date: 25.11.2026. Nothing in this document changes the app,
the server or the public PRIVACY and TERMS until the owner approves it; the decision list is in section 14.

Sources read for this spec: the sales pilot feedback of 25.11 (section 2, task SX3, and decision D58), the sales mode
review of 25.11 (sections 5.2 and 8, questions on retention and visibility), the server ADR of 14.11 (B4, sections
0, 2, 6, 7, 9, 11, 12, 13, 14, 15, 17, 20), the org projects context spec of 16.11 (D40, file index), the B9 team
calls spec of 17.11 (D47, server search), the decision log D20 to D58 of the private roadmap,
`docs/specs/PB-52-g2-evidence-store.md` (sections 1 to 4, 5.2, 12, 13.4), `docs/specs/PB-53-g3-related-evidence-in-preparation.md`
(sections 1 to 3, 8, 10), `PRIVACY.md`, `TERMS.md`, `CHANGELOG.md` (Unreleased, PB-102 Verbatim block),
`docs/en/live-tips.md` (call folders, recording), `app/Sources/LiveTipsCore/RecordingCore.swift` (recording presets)
on main `d84f227`.

Not verified in this step: current Hetzner Object Storage and Cloud prices (no web access in this step; every price
below is an unverified example and a named parameter), whether Hetzner Object Storage offers all three locations,
object lock and versioning in the owner's project today, whether the base price is charged once or per bucket,
whether Hetzner returns or checks a content checksum on upload (S3 `x-amz-checksum-*`) and how presigned multipart
upload behaves for multi-GB recordings, the real call hours of a sales rep (the only grounded number is about 95
calls a month for one heavy user, ADR section 15).

## 0. Summary

- **What changes.** Every artifact of a call and the project register (transcripts, notes, Meeting Sheets, briefs,
  tips logs, recordings, `commitments.jsonl`, `lessons.md`, `sources/`) gets its copy of record on the Pacenote
  server: small structured data in Postgres as today, files in Hetzner Object Storage (S3-compatible), encrypted by
  the app layer before they reach the bucket. Other project folder files go only if the owner chooses so (decision 6,
  recommended: not now). Two exceptions stay on the Mac only: voice prints and the Meeting Sheet Verbatim text
  (decision 15). The Mac becomes a cache that keeps what the next calls need.
- **What stays.** Live calls never depend on the server (zero new network calls on the tip path, offline works).
  Preparation and live search keep running on the local evidence store (G2, G3). Voice prints, keys and settings stay
  on the Mac. Admins and Pacenote staff never read content. Private calls stay private.
- **What it overrides.** D27 ("transcripts stay on the Mac, never on our server") and the matching ADR lines (0.1, 2,
  9 "the Mac is the source of truth", 13 "nothing in AI on the server" stays). Section 12 lists every decision as
  overridden, amended or still valid.
- **How it ships.** Five phases. Phase 1 only copies (nothing leaves the Mac's disk); eviction starts in phase 3,
  after off-server backups and a restore test, and then only file by file under one rule (4.2): verified on the
  server, at least 30 days since that, and present in the backup bucket. Rollback before phase 3 loses nothing.
- **Cost.** Text alone is tiny (about 50 MB per heavy user per month). Recordings dominate: about 0.57 GB per hour of
  standard video. For 50 users who record every call, storage grows by about 1.1 TB a month without a retention rule
  (section 9).

## 1. Problem and goals

The sales pilot (one rep for a month, then a team) needs three things the current design does not give:

1. **Nothing is lost with a laptop.** Today transcripts, notes, sheets and recordings live in the project folder on
   one Mac (and, for recordings and an optional transcript file, in the person's Google Drive). The server holds only
   summaries, decisions, tasks, risks, the Meeting Sheet and metadata (D27, ADR section 2).
2. **One place for the team.** A head of sales reviewing a rep's calls today opens summaries from the server and the
   recording and transcript from Drive, if the Drive folder is shared with them. Two permission systems, two places.
3. **A second Mac or a new Mac starts where the last one stopped**, without copying folders by hand.

Goals:

- G1. A copy of record on the server for every call artifact and the project register listed in section 2.1, except
  voice prints and the Verbatim text (decision 15). Project folder files only if decision 6 says so; with the
  recommended 6(a) they are not covered by G1 and stay in the person's folder or the shared Drive folder.
- G2. Access to it by the same roles as today (D36, D37, D40, D47), with one extra rule for transcripts and recordings
  that the owner decides (decision 4).
- G3. The Mac keeps a bounded cache and works fully offline for calls and preparation of pinned calls.
- G4. Migration of existing local and Drive data without loss, verified by checksums, reversible. The one planned
  difference: files uploaded with the Verbatim text removed (2.4) are verified against their stripped bytes, and the
  Mac keeps the full local file (it is never evicted).
- G5. Rollback at every phase.

Non-goals (this spec): server-side AI (preparation on the server stays B11), a web or phone client (B11 PWA), a team
graph (G7), video retention by deal stage (SX13 builds on this spec), the lead calendar (SX1, small rows in Postgres,
not object storage), billing for storage (B10).

## 2. Data model

### 2.1 What goes where

| Artifact (today on the Mac) | Today on the server | After this spec | Store |
|---|---|---|---|
| Call metadata, summary, decisions, tasks, risks, Meeting Sheet JSON, numbers | yes (Postgres, [enc]) | unchanged | Postgres |
| Transcript `<stem>.md`, `<stem>.app.md` | no (link to Drive copy only) | yes | object, `content` bucket |
| Call notes `.notes.md`, brief `.brief.md`, `.sheet.md` | notes as summary only | yes (files) | object, `content` |
| `.work/` sidecars: `.tips.jsonl`, `.inbox.jsonl`, `.speakers.json`, sheet request, sources, judge | no | yes, one archive per call | object, `content` |
| `.work/web/<host>` stored site pages, `live/google/` picked copies | no | yes | object, `content` |
| Project register `live/commitments.jsonl`, `live/lessons.md`, `live/sources/` | register items only (D40, org projects) | yes (files) | object, `content` |
| Recording `video/<stem>.recording*.mp4` or `.m4a`, `.recording.json` | Drive link only | yes | object, `media` bucket |
| Project folder files the app reads (docs, PDFs, notes in the folder) | no (D40 file index: name, date, 600-char description, vector, org projects only) | optional, per project (decision 6) | object, `content` |
| Voice prints `.work/<stem>.voices.json`, voice library | no | **no, stays on the Mac** (biometric data, decision 9) | Mac |
| Keys, tokens, settings, local usage log | no | no | Mac, Keychain |
| Local evidence store `evidence/<uid>.sqlite` (G2) | no | optional encrypted snapshot (decision 8) | object, `content` |
| Meeting Sheet Verbatim block text (PB-102, Unreleased: "stays on this Mac, call sync does not send it") | no | **no**: the sheet files and the `.work` archive are uploaded with the Verbatim text removed, as call sync does today, unless the owner changes that promise (decision 15); such files are never evicted (2.4) | Mac |
| HubSpot snapshot next to the brief | no | yes, inside the `.work` archive, owner only | object, `content` |
| Live tip text | no (counts only) | in `.tips.jsonl` archive, owner only (decision 4) | object, `content` |

Rule of thumb: rows the API needs to filter, list or check stay in Postgres; whole files go to object storage; the
Postgres `objects` table (2.3) is the only index of the buckets.

### 2.2 Buckets

Hetzner Object Storage has three locations (Falkenstein, Nuremberg, Helsinki); the API server is in Helsinki.

| Bucket | Location | Holds | Settings |
|---|---|---|---|
| `pn-prod-content` | Helsinki (same as API) | text artifacts, archives, project files, store snapshots | private, versioning on, lifecycle: noncurrent versions deleted after 30 days |
| `pn-prod-media` | Helsinki | recordings | private, versioning on, lifecycle per prefix for retention (SX13 later) |
| `pn-prod-backup` | another location (Falkenstein or Nuremberg) | nightly incremental copy of both buckets (new and changed objects; the job sets `backup_verified_at`) (already encrypted objects) and the encrypted Postgres dump | private, write-only credentials for the copy job, object lock if available (to verify), with a retention of at most 30 days (3.1) |
| `pn-staging-*` | any | the same for staging | never real data |

One bucket per kind, not per organization: bucket names are global and limited per project, and access is enforced
by the API, not by bucket policy. No bucket is public; the bucket credentials (`PN_S3_ACCESS_KEY`,
`PN_S3_SECRET_KEY`, `PN_S3_BACKUP_ACCESS_KEY`, `PN_S3_BACKUP_SECRET_KEY`) live only in the server `.env` and the
owner's secrets file. The Mac never holds bucket credentials.

### 2.3 Object keys and the `objects` table

Object keys carry no names, titles or paths (those are content), and no `org_id`, because D36 moves calls between
organizations and an object key never changes:

```
u/<owner_id>/<object_id>        call artifacts and personal project files of one person
op/<org_project_id>/<object_id> org project files (D40, only if decision 6 allows them)
```

New Postgres table `objects` (RLS like `calls`, ADR 7.3):

| Field | Note |
|---|---|
| id, org_id, owner_id | UUID; `org_id` and `owner_id` from the session, never from the request |
| call_id or project_id | exactly one |
| kind | `transcript`, `transcript_app`, `notes`, `brief`, `sheet_md`, `work_archive`, `web_pages`, `google_copy`, `register`, `lessons`, `recording`, `recording_meta`, `project_file`, `evidence_snapshot` |
| rel_path **[enc]** | the path inside the project folder, so the Mac can restore the file at the same place (the content is exact except for stripped files, 2.4) |
| rev, current | a new revision for every change; `current` marks the live one |
| bytes, sha256_cipher | for verify and dedup on upload |
| hmac_plain | HMAC-SHA256, with the person's **dedup key**, of the plaintext bytes **as uploaded** (for a stripped file, after the Verbatim text is removed, 2.4): lets a Mac skip unchanged files without sending the plaintext or a plain hash. It is not hidden from the server itself: the server stores the dedup key wrapped and can unwrap it, like every other key (3.1, not end-to-end). The dedup key is a new random per-person key (not the DEK), stored wrapped in `user_keys` and handed to that person's signed-in devices, kept in the Keychain; it never reaches leads |
| stripped | true when the uploaded bytes differ from the local file on purpose (Verbatim text removed, 2.4) |
| verified_at, backup_verified_at | when the server confirmed the checksum (3.1), and when the nightly copy job confirmed the object in `pn-prod-backup` (same `sha256_cipher`); both are eviction conditions (4.2) |
| wrapped_key, key_version | the object's data key, wrapped (section 3) |
| bucket, created_at, deleted_at | |
| visibility_class | `owner_only`, `call` (follows the call's visibility), `project_shared` (D40 items) |

`call_assets` (ADR 6.2) keeps its Drive links for history; a new `kind = server_object` row points to `objects`.

### 2.4 Files uploaded without the Verbatim text

The Meeting Sheet Verbatim text (PB-102) stays on the Mac (decision 15). So `.sheet.md`, the Meeting Sheet files and
the `.work` archive (which holds the sheet request) are uploaded **stripped**: the app removes the Verbatim block
text with one deterministic function before encryption and marks the row `stripped`. Rules:

- `hmac_plain` and `sha256_cipher` describe the stripped bytes. To compare a local file with the server, the Mac runs
  the same strip function on the local file first and computes `hmac_plain` over the result. This holds for the
  folder-sync dedup (4.3) and the location flag (4.4).
- A stripped file is **never evicted** from the Mac, whatever the phase or the cache budget, and text eviction
  (decision 7b, phase 4) skips it. The Mac copy is the only full copy.
- What a new or lost Mac gets: the sheets come back without the Verbatim text. A Verbatim block whose source is a file is
  filled again on the next Refresh if that file is on the new Mac; pasted Verbatim text cannot be re-created
  (CHANGELOG, PB-102). Settings
  > Storage and the restore screen say so. Keeping a full copy would need decision 15(b).

## 3. Encryption and access

### 3.1 Encryption

- Every object is encrypted on the **Mac** before upload with a fresh random key (AES-256-GCM, chunked for
  recordings so a video can be read in parts). The object key is wrapped with the owner's content key (DEK,
  `user_keys`, ADR 11) by the API and stored as `wrapped_key`. The bucket only ever holds ciphertext.
- Upload: the app asks the API for short-lived presigned URLs for a new `object_id` after an RLS check (one PUT for
  small files, presigned multipart parts for recordings), uploads directly to the bucket, then confirms with
  `sha256_cipher`; the API checks the size with a HEAD request and the hash through the S3 checksum header if Hetzner
  supports it (not verified), otherwise by reading the object once in the background before the row counts as
  `synced`. Nothing is evicted before that check.
- Download: the API checks access (3.2), returns a presigned GET URL (5 minutes) and the object key unwrapped for this
  viewer over TLS. The app decrypts locally. Large recordings never pass through the API server.
- Deleting a person still destroys their DEK (crypto-delete) in the live database at once. The wrapped DEK still
  exists in the Postgres dumps, which are kept 30 days, so the objects become unreadable everywhere only when the last
  dump that holds it expires, within 30 days. Object lock on `pn-prod-backup` must therefore keep objects no longer
  than the dumps (30 days), or it extends this window. The bucket objects themselves are removed within 30 days by a
  sweep. PRIVACY must say "within 30 days", not "at once" (section 10).
- `PN_MASTER_KEY` loss would now lose transcripts and recordings, not only summaries. Before phase 3 (eviction):
  two offline copies of the master key in different places and a written recovery procedure (decision 12).
- What the server can read: like today, the API can unwrap keys (it does for leads), so this is protection against a
  leaked bucket, dump or backup and against staff browsing storage, not end-to-end encryption. End-to-end (keys only
  on Macs) would block lead access and any server search; not recommended now.

### 3.2 Who sees what

The roles are unchanged: owner, admin, member (org); lead (team, D37); C-level (Enterprise, D37); project members
(D40); Pacenote staff (B6a). New is only which kinds each role may open.

| Kind | Call owner | Lead of the owner's team, C-level | Org project member (D40) | Admin | Staff |
|---|---|---|---|---|---|
| Metadata, summary, decisions, tasks, risks, sheet (Postgres) | yes | yes (as today) | decisions, tasks, risks of non-private calls of the project (as today) | metadata counts only | metadata counts only |
| Transcript, notes, brief | yes | **policy `share_transcripts`** (decision 4) | no | no | no |
| Recording | yes | **policy `share_recordings`** (decision 4) | no | no | no |
| `.work` archive (tips text, inbox, sources) | yes | no | no | no | no |
| Project files | yes | no | org project files: members of the org project (D40, decision 6) | no | no |
| Evidence store snapshot | yes | no | no | no | no |
| Private call (any kind) | yes | fact and length only (D37) | no | no | no |
| Personal organization (D36) | yes | no | no | no | no |

- Leaving an organization (D36): the person's calls and objects move to their personal organization; the API rewrites
  `org_id` on the rows; object keys stay (they carry no `org_id`, and the key path is not used for access).
- Every opening of another person's transcript or recording by a lead or C-level is a `call_views` row (D47 "who viewed
  my calls") and an audit event without content.
- D49 (lead sees quotes up to 20 words) stays the rule for the scorecard text, which is shown to leads even where
  `share_transcripts` is off. With `share_transcripts` on, a lead can open the whole transcript anyway.
- The team list stays uncached on the lead's disk (D47): a lead's Mac downloads other people's transcripts and
  recordings into memory or a temporary file removed when the call page closes, never into the cache of section 4.

## 4. What the Mac caches and when it is evicted

Today's project folder layout stays (`<project>/live/<date>-<title>/`, `video/`), so the engine, the skill and Finder
keep working. The cache is the project folder itself plus a manifest `cache/<project uid>.json` in Application
Support (never in the Drive-synced folder) that records, per object, `local | uploading | synced | evicted`.

### 4.1 Always kept (pinned)

- Everything of a call until its upload is confirmed by the server (`synced`), and everything during a call.
- Upcoming calls in the next 14 days: their series' last 5 calls (transcript, notes, sheet), the project's register,
  `lessons.md`, `sources/`, project state, and files the last preparation of the series used. This is a superset of
  what `auto_sources` reads (G3 section 1: last 3 calls of the series, 7 days of folder files).
- Files with local edits not yet uploaded.
- Everything the person marks **Keep on this Mac** (project or call).

### 4.2 Evicted (one per-file rule)

A file may leave the Mac only when **all** of these hold. This is the only eviction rule; section 6.1 and the phase
table (section 8) refer to it.

1. The phase allows its kind (recordings from phase 3, text from phase 4 and only with decision 7b).
2. It is not pinned (4.1) and not stripped (2.4).
3. Its current revision is `synced` and verified on the server (`verified_at` set, 3.1), and the local file still has
   the same `hmac_plain` (no local change since the upload).
4. At least `N` days have passed since `verified_at` (dual-keep, `N` = 30 by default).
5. The object is present in `pn-prod-backup` (`backup_verified_at` set by the nightly copy job).

Being over the cache budget never skips a condition: it only picks the oldest eligible files first. If nothing is
eligible, eviction pauses and Settings > Storage warns ("Over the storage budget: N GB wait for their server copy to
be verified and backed up").

| Kind | Eligible when the rule above holds and | Default |
|---|---|---|
| Recordings | always (oldest first) | on from phase 3 |
| Text of calls (transcripts, notes, brief) | older than 90 days | **off by default** (decision 7): text is about 50 MB per heavy user per month, eviction saves little and costs offline reach |
| Stripped files (sheets, `.work` archive with the sheet request) | never (2.4) | |
| Project files | never evicted by Pacenote; they are the person's folder | |

Cache budget: Settings > Storage shows the size on this Mac and on the server and a budget (default 20 GB or 10% of
the disk, whichever is smaller). An evicted file shows on the call page as "On the Pacenote server" with Download;
opening a call page downloads its text on demand.

### 4.3 Project folders inside Google Drive or iCloud

Many project folders are synced by Google Drive for desktop or iCloud (G2 treats the project folder as Drive-synced).
Removing a file there removes it in that cloud and on every other Mac that syncs the folder, and a second Mac would
receive the same files twice (through the folder sync and through the server). Rules:

- Before evicting, the app checks whether the file sits under a cloud-synced location (the known Google Drive for
  desktop and iCloud Drive paths, and the File Provider attribute). There, Pacenote **never evicts** from the project
  folder; Settings > Storage says why.
- From phase 3, new recordings are written to an app media folder in Application Support (not synced) with the same
  file names, and the call page looks there and in `video/`; only that media folder is evicted. Existing recordings
  in `video/` are evicted only when `video/` is not cloud-synced.
- A Mac that already received a file through the folder sync and finds the same `hmac_plain` on the server marks it
  `synced` and downloads nothing.

### 4.4 Eviction and the local evidence store (G2, G3)

G2 section 5.2 item 6 marks a vanished file `deleted`, and G3 (section 3, point 3) rewrites a call's snapshot
without the items of any source that leaves `active`. So eviction must not change the source's state:

- Evicted sources stay `active`. G2 gets a location flag on the source row (`local` or `server`), not a new state.
  The app passes the list of evicted paths with each pass, as it passes hidden calls (G2 S5); the path walk skips a
  vanished path that is on that list instead of marking it `deleted`, and does not re-read it.
- Snippets, claims, edges and G3 snapshot items of an evicted source stay searchable (the store keeps snippet text).
  The G2 vacuum (section 13.4) purges only revoked, superseded and purged rows, so it never touches an evicted source.
- A downloaded file with the same `hmac_plain` sets the flag back to `local`: no new revision, no model cost.
  Stripped files (2.4) are never evicted, so their flag stays `local`.
- A file the person deletes for real (not evicted) is still `deleted`, as today.
- This is a small G2 change (one column, one input list) in phase 3; no eviction of text (decision 7) before it
  ships. Recordings are not store sources, so recording eviction does not need it.

### 4.5 Deletion when the Mac no longer has a copy

Today "Delete my server data", deleting a call from the server, `retention_months` and D44 auto-delete all leave the
Mac copy (ADR 12.2, PRIVACY). After eviction the server copy can be the only one. Rules:

- Deleting a call or all server data first shows how much exists only on the server and offers **Download to this Mac
  first** (default) or **Delete everywhere**. Without a choice nothing is deleted.
- `retention_months` and plan `history_months` never delete server files that are the only copy without the same
  offer by email and in the app 30 days before.
- D44 auto-delete of an inactive account with data (365 days, two emails) now deletes files too; the emails say so and
  link to Export.

## 5. Deep search: server vs local GraphRAG

| Need | Where it runs | Why |
|---|---|---|
| Preparation context (G3), live lookup (G4), after-call links (G5) | **Mac, local G2 store** (unchanged, D57) | zero network during a call, works offline, cost and route chosen by the person, already measured on G0 |
| A person's own search over all their calls, including evicted ones | Mac store (it keeps snippet text of evicted sources, 4.4) | no server change needed |
| Lead or C-level search over the team's calls (B9) | server, as today: decrypt within the visible set (summaries, decisions, tasks, risks; 90 days, cap 2000 calls) | unchanged in phases 1 to 4 |
| Lead search over the team's **transcripts** | not in this spec's phases 1 to 4 (decision 10) | decrypting 2000 transcripts (about 120 MB) per query does not scale on the current server |
| Team graph (G7) | later, on the server objects of org projects (D40) | out of scope |

Options for team transcript search later (decision 10): (a) none, leads open a call and use find on the page;
(b) a blind keyword index: the owner's Mac uploads HMAC-hashed word tokens per call, the server matches hashed query
tokens within the visible set, no plaintext on the server, exact words only; (c) Mac-computed bge-m3 vectors per
transcript chunk in pgvector, as D40 already allows for project files: better recall, but vectors are derived from
the plaintext and partly invertible, so it needs its own PRIVACY line. Recommendation: (a) now, (b) when a team asks,
(c) only with G7.

No option puts an AI model on the server; ADR section 13 ("the server sends nothing to AI") stays valid.

The evidence store is derived data, but rebuilding it on a new Mac re-runs LLM extraction (G2 section 13.4: rebuild
redoes extraction under the cost ceiling). An
encrypted weekly snapshot of the store file in `pn-prod-content`, owner only, lets a new Mac restore it instead
(decision 8).

## 6. Migration of existing data

### 6.1 Local data (Mac)

1. A background **Upload history** job per project, started when the project's sync is on (decision 3) and the
   person confirms the size shown ("About 2.4 GB, of which recordings 2.1 GB").
2. Order: newest calls first, text before recordings, so the most useful part is on the server first.
3. Per file: encrypt, presigned PUT, confirm with `sha256_cipher`, the server verifies, the manifest marks `synced`.
   A restart resumes from the manifest. Never during a call; paused on battery under 20% and on a metered network.
4. Nothing is evicted by the migration. Eviction starts only in phase 3 and only under the per-file rule of 4.2
   (verified, `N` = 30 days of dual-keep since verification, present in `pn-prod-backup`).
5. A second Mac of the same person: `GET /v1/sync?since=` (ADR 8) lists objects; the Mac downloads pinned ones and
   shows the rest as "On the Pacenote server". Dedup by `hmac_plain` stops two Macs from uploading the same file
   twice.

### 6.2 Google Drive data

- Recordings and transcript `.md` files that Pacenote uploaded are app-created files, so `drive.file` can read them.
  The Mac uploads its **local** copy when it still has one; only when the local file is gone does it download from
  Drive (the call's `recording.json` has the file id) and then upload. The server never gets Drive tokens (ADR 3.5
  stays).
- Drive files are not deleted. "Upload recordings to Google Drive" becomes optional (decision 5): it keeps today's
  setting through phases 1 and 2, so a rollback of phase 2 still has Drive links for new calls, and its default for
  new calls turns off only once phase 3 is stable (section 11), so leads then have one place.
- Google Docs copies in `live/google/` and stored site pages go to the server as files. The Google Limited Use line
  allows storage that serves a user-facing feature of the app; PRIVACY must say these copies are stored on the
  Pacenote server (section 10).
- D40 org project files stay in the project's shared Drive folder; the server keeps the file index as today. Copying
  those files to the server is decision 6.

### 6.3 Server data already there

Postgres rows (summaries, decisions, tasks, risks, sheets, metadata) are not moved: they stay in Postgres. The notes
file and the summary row describe the same notes; the summary row stays the one the API searches.

## 7. Offline mode

- A live call never waits for the server: recording, transcription, tips, Help Now and the local store need no
  network (unchanged). Uploads queue and resume.
- Preparation offline uses pinned data. A source that is evicted and cannot be downloaded shows in Sources as "On the
  Pacenote server, offline" and the preparation runs without it; the local evidence store still holds its snippets
  (4.4), so project memory can still use it.
- D50 stays: with a saved session the app works offline; the first launch offline shows the explanation and Retry.
- Server down for days: nothing is lost on the Mac (not yet uploaded files are pinned); evicted files open again when
  the server is back. The health line (D53) shows "Pacenote server unreachable".
- A lead offline sees only what is in memory; the team list is not cached (D47).

## 8. Phases and prerequisites

| Phase | What | Prerequisites | Can be rolled back by |
|---|---|---|---|
| 0 | Requirements before any upload: Postgres backups kept off the API server, nightly incremental bucket copy (new and changed objects only) to `pn-prod-backup` in a second location that sets `backup_verified_at`, a monthly restore test that includes a sample of objects, two offline copies of the master key; buckets created, `objects` table and API behind a flag | none | not needed |
| 1 | Upload only: new calls and Upload history copy to the server; nothing evicted; owner-only access | phase 0 green | flag off; delete server objects (crypto-delete) |
| 2 | Lead and C-level access to transcripts and recordings by policy (decision 4); call page opens from the server; Drive upload optional | phase 1 on the owner's own calls for 2 weeks with zero checksum mismatches | policy off; nothing is evicted in phase 2, so every file is still on the owner's Mac, and Drive upload keeps today's setting until phase 3 is stable (6.2), so leads keep Drive links where the person shares them |
| 3 | Eviction of recordings under the per-file rule of 4.2 (app media folder, 4.3); deletion offers (4.5); Settings > Storage | a restore of a sample of recordings from `pn-prod-backup` passed; the copy job sets `backup_verified_at` reliably for 2 weeks | eviction off and **Restore all to this Mac** (re-download) |
| 4 | Optional text eviction (decision 7) with the G2 location flag (4.4), store snapshot (decision 8) | phase 3 stable for a month | same as 3 |
| 5 | Team transcript search (decision 10), G7 | owner decision | flag off |

## 9. Cost for 5 and 50 users

### 9.1 Inputs

| Input | Value | Source |
|---|---|---|
| Call hours per user per month, `H` | 40 (heavy), 20 (typical) | estimate; about 95 calls a month for one heavy user (ADR 15) at about 25 min |
| Text per call | about 0.5 MB with `.work` sidecars | estimate; ADR 15 gives 15 to 40 KB for the server part today |
| Recording, standard preset, H.264 | about 569 MB per hour (1200 + 64 kbps) | `RecordingCore.swift` `mbPerHour` |
| Recording, standard preset, HEVC | about 366 MB per hour | same |
| Sound only | about 29 MB per hour (64 kbps) | same |
| Share of calls recorded with video, `r` | 0, 0.5, 1 | scenario |
| `P_base`: Object Storage base price per month, including 1 TB stored and 1 TB egress | example EUR 4.99 | **unverified**, check in the Hetzner console |
| `P_tb`: each extra TB stored per month | example EUR 5 | **unverified** |
| `P_egress`: each extra TB of egress | example EUR 1 | **unverified** |
| Backup bucket in a second location | a second base price | same unverified prices |

Stored after `m` months, with no retention rule: `users x m x H x (text + r x video)`. Monthly storage cost:
`2 x (P_base + max(0, TB - 1) x P_tb)` (content and media together with their copy in the backup bucket). This
assumes the base price is charged once per account group of buckets and its included TB is shared; if it is per
bucket, add one `P_base` per extra bucket (not verified).

### 9.2 Results (H = 40, standard H.264, unverified example prices)

| Scenario | Growth per month | Stored after 12 months | Storage cost per month at month 12 (with backup copy) |
|---|---|---|---|
| 5 users, text only | about 0.24 GB | about 3 GB | about EUR 10 (two base prices) |
| 5 users, sound only for every call | about 6 GB | about 70 GB | about EUR 10 |
| 5 users, video for every call | about 114 GB | about 1.4 TB | about EUR 14 |
| 50 users, text only | about 2.4 GB | about 29 GB | about EUR 10 |
| 50 users, video for half the calls | about 570 GB | about 6.8 TB | about EUR 68 |
| 50 users, video for every call | about 1.1 TB | about 13.7 TB | about EUR 137 |
| 50 users, video for every call, recordings kept 90 days (SX13) | about 1.1 TB | about 3.4 TB (steady) | about EUR 34 |

With H = 20 every number halves.

Egress (not in the table above):

- The nightly copy to `pn-prod-backup` must be incremental (new and changed objects only); a full copy of up to
  13.7 TB every night is not possible. Incremental, it moves about one month's growth a month: about 114 GB for 5
  users with video, about 1.1 TB for 50 users with video for every call. Whether traffic between two Hetzner
  locations counts as egress is not verified; if it does, the backup copy alone uses or exceeds the included 1 TB at
  50 users.
- Reads: a lead watching 10 hours of recordings a month is about 6 GB; Mac downloads of text are small.
- Restores: **Restore all to this Mac** and a new or second Mac downloading recordings can be large (a heavy user
  with a year of video is about 270 GB). Budget them as rare events at `P_egress` per extra TB.
- So: for 5 users the included 1 TB covers egress; for 50 users with video plan about 1 to 2 TB of egress a month,
  that is up to about EUR 1 over the included TB at the example `P_egress`, more in a month with several restores.

### 9.3 Compute

- 5 users: the current CX23 (2 vCPU, 4 GB) is enough: the API only signs URLs and wraps keys; files go straight
  between the Mac and the bucket.
- 50 users: the same server holds for uploads; B9 search over summaries for 50 people and Postgres growth suggest one
  step up (8 GB RAM) when the team plan has more than about 20 active seats. Server-side embeddings are not planned
  (section 5), so no larger machine is needed for search.
- Rough total, 50 users, video for every call, 90-day retention, 8 GB server: about EUR 45 to 60 a month before VAT
  (unverified; storage about EUR 34 from the table plus the server and IPv4), about EUR 1 per user. For 5 users:
  about EUR 16 to 20 (storage about EUR 10 to 14 plus today's EUR 5.99 server).
- Text rows use 95 calls x 0.5 MB per user per month; video rows add `H x r x 569 MB`; text is negligible next to
  video.

### 9.4 Plan quotas

The plans (ADR 10, D35, D41) set `storage_mb` at 500 for Solo, 2,000 per seat for Team and 5,000 per seat for
Enterprise, and Solo shows 12 months of history (`history_months`); over quota the API answers `402`. These numbers were
set for summaries and sheets only. With video one heavy user adds about 23 GB a month, so:

- What counts: every current object revision of the person (noncurrent versions and the backup copy do not count).
- At quota: uploads stop with a clear line in Settings > Storage and on the call page; **nothing is evicted that is
  not uploaded**, so the Mac keeps everything new and the person is never left without a copy. Text keeps uploading
  before recordings, so quota is hit by recordings first.
- `history_months` on Solo limits what the server **shows**, not what it keeps: older files stay downloadable to the
  person's own Mac and in Export, and are never deleted by the plan alone (4.5).
- New numbers are decision 16; they depend on the retention default (decision 11).

## 10. PRIVACY and TERMS changes (draft EN; UK text is written with the release, mirrored)

PRIVACY.md lines to replace or add, in the release that ships phase 1 (and the site pages with them):

- Replace "Transcripts, voice prints, settings and logs stay in your project folders..." with: "**Your calls on the
  Pacenote server.** For each project where sync is on, the app also sends the call's transcript, notes, brief,
  Meeting Sheet, the call's working files (including the text of the live tips, seen only by you) and, if you record,
  the recording. They are encrypted on your Mac before they leave it, with a key of their own that is protected by
  your content key; the storage (Hetzner Object Storage, EU) holds only encrypted files. Your Mac keeps what your next
  calls need; older recordings can be removed from the Mac and opened again from the server. Voice prints, settings,
  keys and the local usage log stay on your Mac."
- In "Pacenote account and server", remove "Audio, video, transcripts, voice prints and the text of live tips never
  go to the Pacenote server" and say instead which of them go (above) and that voice prints never do.
- In "Organizations", add: "If your organization turns it on, your team lead (and C-level on Enterprise) can also open
  the transcripts and recordings of your calls that are not private. Each opening is recorded and you see it in Who
  viewed my calls. Admins and Pacenote staff never open them."
- In "Google", add: "Copies of Google documents read for a preparation are stored with the call on the Pacenote
  server, encrypted, and are used only for your preparations."
- In "Call recording", change "It goes to Google Drive only when you upload it" to name the Pacenote server as the
  default place and Drive as optional.
- Deletion: deleting a call or your data removes the files from the storage too, old versions and backups within 30
  days. Today's line "your content key is destroyed at once" stays true for the live database, but the files become
  unreadable everywhere only within 30 days, when the last backup that holds the wrapped key expires (3.1); say that.
  Where PRIVACY says deleting server data leaves the Mac copy, say instead: Pacenote shows what exists only on the
  server and offers to download it first (4.5).
- "Participants' names, whether they are from your company and how long each one spoke are not sent yet": this
  becomes false, because transcripts carry speaker names and the `.work` archive carries `.speakers.json`. Replace
  with: "Participants' names and how long each one spoke are sent inside the call's files, encrypted, visible only to
  the people who may open those files."
- "Sync to Pacenote server ... off by default" and "Signing in alone sends nothing from your calls: calls go to the
  server only for projects where you turned Sync on": with decision 3(a) sync is on by default for new projects.
  Replace with: "Sync is on for new projects; you can turn it off per project or leave one call out. Signing in alone
  sends nothing from projects where Sync is off." With 3(c) both lines stay.
- "The server and its backups are in the EU (Hetzner, Finland)": the backup bucket is in Germany (2.2). Replace with
  "in the EU (Hetzner, Finland, with backups in Germany)".
- "Participants' email addresses are never sent": transcripts, briefs, sheets and the HubSpot snapshot can contain
  attendee names and email addresses from the calendar invitation and the CRM. Either the line changes to "are sent
  only inside the call's files, encrypted, visible to the people who may open those files" or the upload removes
  email addresses from the files (decision 17).
- HubSpot: "a local snapshot next to the brief" becomes "a snapshot stored with the call, on your Mac and, encrypted,
  on the Pacenote server".
- The Verbatim block (PB-102) stays off the server, so its line in CHANGELOG and docs stays true (decision 15).

TERMS.md:

- Replace "keep your own copies, your calls stay on your Mac" with: "The server keeps the copy of record of your calls
  for projects with sync on. It is in beta without an uptime promise: export your data regularly (Settings > Account
  > Export)."
- Add to the organization paragraph: the organization decides whether leads open transcripts and recordings, and is
  responsible for telling its people.
- Ending: at least 30 days to export, now including files (export becomes a zip with the folder layout).

Lawyer questions this adds to ADR 14.5: storing transcripts and recordings makes Pacenote a processor of call content
for organizations (DPA needed before the first paying team, decision 13); employer client data (ADR 18 question 7,
still open) now includes full transcripts; lead review of recordings plus the B17 scorecard points closer to EU AI
Act employee evaluation rules.

## 11. Rollback

- **Before phase 3** every file is still on the Mac. Turning the server flag off stops uploads; the server copy can
  be deleted by destroying object keys and sweeping the buckets. The app keeps today's Drive upload path until phase
  3 is stable, so leads fall back to Drive links.
- **After phase 3**: turning eviction off and running **Restore all to this Mac** re-downloads every evicted file of
  the person (the size is shown first). The organization flag `server_sot` (policy) turns the whole feature off for
  an organization; per project, sync off stops new uploads but does not delete server files unless the person asks.
- **Server loss**: Postgres from the off-server dump, buckets from `pn-prod-backup`, keys from the offline master key
  copies; the monthly restore test covers a sample of objects, not only Postgres.
- **Bad release**: the server keeps the deploy rollback by tag (ADR 4.2); object writes are append-only revisions, so
  a rollback never loses an uploaded file.

## 12. What this overrides and what stays valid

| Decision or document | Status after approval | Note |
|---|---|---|
| D27 (transcripts stay on the Mac; video only on Drive; server gets summaries, decisions, tasks, metadata, sheet) | **overridden** | transcripts, recordings and call files go to the server; Drive optional |
| ADR 14.11 section 0.1 and section 2 table | **overridden** | replaced by section 2.1 here |
| ADR section 9 "the Mac stays the source of truth for its calls" | **overridden** | the server is the copy of record; the Mac is a cache |
| ADR section 3 (Drive upload, `drive.file`, folder move) | amended | stays as an optional export; the server never gets Drive tokens |
| ADR section 11 (per-user DEK, crypto-delete) | still valid, extended | object keys wrapped by the DEK |
| ADR section 13 (no AI on the server) | still valid | no server AI in this spec |
| D25 (leads review recordings and transcripts) | partly restored | through the server, by policy (decision 4) |
| D30 (sync off by default, per project, "Don't sync this call") | amended by decision 3 | |
| D34 (live tip fragments only in transit through the proxy) | still valid | the tips log is stored after the call, owner only |
| D36 (personal organization private, people take their calls when they leave) | still valid | objects move with the calls |
| D37 (teams, leads, C-level, admin sees no content) | still valid | new kinds follow the same visibility |
| D40 (org project sharing: decisions, tasks, risks; files in Drive, index on the server) | still valid | decision 6 may add files to the server |
| D47 (B9 server search by decryption, team list not cached) | still valid | transcripts not searched by leads in phases 1 to 4 |
| D49 (quotes up to 20 words for leads as an exception to D27) | amended | stays the scorecard rule; full transcript only by policy |
| D50 (account required, offline with a session works) | still valid | |
| ADR 10, D35, D41 (plan storage quotas, `history_months`) | amended | section 9.4, decision 16 |
| ADR 12.2 and PRIVACY ("Delete my server data" leaves the Mac and Drive copies) | amended | section 4.5: offer to download first |
| D44 (auto-delete of inactive accounts with data after 365 days) | still valid, wider effect | now deletes files too; the emails say so |
| PB-102 (Verbatim block stays on the Mac) | still valid | removed from uploaded sheet files; those files are never evicted (2.4) |
| D57 (GraphRAG fully local, nothing new leaves the Mac) | still valid for the store | G2 adds a location flag (`local` or `server`) on the source row, not a new state (4.4); its optional snapshot is decision 8 |
| PRIVACY and TERMS | to change | section 10 |

## 13. Risks

| Risk | Mitigation |
|---|---|
| Only one person administers a server that now holds full transcripts and recordings | phase 0 prerequisites, restore tests that include objects, alerts (D53) |
| Master key loss now loses everything not on a Mac | two offline copies, eviction only from phase 3 |
| A lead sees more than people expect | organization policy (decision 4), never for private calls or the personal organization, a visible "Visible to your team lead" line on the call page, Who viewed my calls |
| Storage cost grows with video | retention rules (SX13), sound-only by default for sales calls, cost shown in the console |
| Eviction breaks preparation or the evidence store | pinning (4.1), the G2 location flag (4.4) before text eviction, text eviction off by default |
| The only copy of a file is in one bucket in one location | per-file eviction rule (4.2): verified, 30 days of dual-keep, present in `pn-prod-backup`; over budget only warns |
| Employer client data on the server | per project sync off stays available (decision 3), lawyer answer before work projects are synced |
| Upload eats bandwidth on calls | never during a call, resumable, paused on metered networks |

## 14. Decisions for the owner

1. **Approve the target: the server holds the copy of record of call and project files**, and D27 is overridden.
   Options: (a) approve with the phases of section 8; (b) approve only phase 1 (copy, no eviction) for now;
   (c) do not approve. Recommendation: (a), with a check after phase 1.
2. **Bucket layout.** Options: (a) three buckets (content, media, backup in a second location) as in 2.2; (b) one
   bucket per organization. Recommendation: (a).
3. **Is sync mandatory?** Today it is off by default per project (D30) with "Don't sync this call". Options: (a) on
   by default for new projects, per-project off and per-call off kept; (b) mandatory in organizations, optional in
   the personal one; (c) keep off by default. Recommendation: (a); (b) as an organization policy later.
4. **Lead access to transcripts and recordings.** Options: (a) organization policies `share_transcripts` and
   `share_recordings`, default on for Team and Enterprise, never for private calls, always logged; (b) default off;
   (c) never, leads see summaries and 20-word quotes only. Recommendation: (a) for the sales pilot, because reviewing
   calls is the point of the pilot; the person sees the line on every call page.
5. **Google Drive upload of recordings.** Options: (a) optional, today's setting kept through phase 2 (the phase 2
   rollback relies on it), off by default for new calls once phase 3 is stable; (b) keep on by default as a second
   copy; (c) remove. Recommendation: (a).
6. **Project folder files on the server.** Options: (a) no, only call artifacts and the register (files stay in the
   person's folder or the shared Drive folder of D40); (b) yes, for org projects only; (c) yes, for every synced
   project. Recommendation: (a) now, (b) with G7. With (a), goal G1 does not cover project folder files: a lost Mac
   loses them unless the folder is in Google Drive or iCloud or backed up otherwise.
7. **What the Mac evicts.** Options: (a) recordings only, text never by default; (b) recordings and text older than
   90 days not pinned; (c) nothing evicted automatically, only by the person. Recommendation: (a); text is about 50 MB
   per heavy user per month and keeps preparation and offline full.
8. **Encrypted weekly snapshot of the local evidence store on the server.** Options: (a) yes, owner only; (b) no,
   rebuild on a new Mac (re-extraction cost). Recommendation: (a) in phase 4.
9. **Voice prints.** Options: (a) stay on the Mac only; (b) on the server like other files. Recommendation: (a),
   biometric data; a new Mac re-learns voices.
10. **Team search over transcripts.** Options: (a) none in phases 1 to 4; (b) blind keyword index; (c) Mac-computed
    vectors in pgvector. Recommendation: (a), then (b) when a team asks.
11. **Retention of recordings.** Options: (a) keep while the account is active until SX13 adds deal-stage rules;
    (b) a default of 90 days for recordings, text kept; (c) organization setting only. Recommendation: (b) as the
    default with (c) to override, after the head of sales answers question 11 of the sales review.
12. **Phase 0 before any upload**: Postgres backups off the API server, a bucket copy in a second location, monthly
    restore tests including objects, two offline copies of the master key. Options: (a) required before phase 1
    (as section 8 says); (b) required only before phase 3. Recommendation: (a), because phase 1 already puts
    transcripts on the server.
13. **Lawyer and DPA.** Options: (a) DPA and lawyer review before the first paying team uses phase 2; (b) before any
    organization other than the owner's uses phase 1. Recommendation: (b) for the sales pilot company, since it is a
    real organization with client data.
14. **Work projects of the owner's employer.** ADR question 18.7 is still open. Options: (a) keep sync off for those
    projects until the lawyer answers; (b) sync them. Recommendation: (a).
15. **Verbatim block (PB-102) on the server.** Options: (a) keep the promise: uploaded sheet files leave it out;
    (b) upload it like the rest and change the CHANGELOG and docs line. Recommendation: (a); it is usually a script
    or a legal text the person already has. Consequence of (a) (2.4): stripped files are never evicted, and a new or
    lost Mac gets sheets without the Verbatim text; a file-based block fills again on Refresh if the file is there,
    pasted Verbatim text is lost with the Mac.
16. **Storage quotas per plan.** Options: (a) count text only against today's `storage_mb` and give recordings a
    separate quota in hours (for example 20 hours stored on Solo, 100 hours per seat on Team, kept by the retention
    rule); (b) raise `storage_mb` to cover video (for example 25 GB per seat on Team); (c) keep today's numbers.
    Recommendation: (a): hours are what people understand, and it keeps Solo free of large costs. Numbers are
    examples for the owner to set.
17. **Participants' email addresses inside uploaded files.** Options: (a) upload files as they are and change the
    PRIVACY line; (b) remove email addresses from transcripts, briefs and sheets on upload. Recommendation: (a): the
    files are encrypted and seen only by the owner and, by policy, the lead; (b) would make a restored file differ
    from the original.
