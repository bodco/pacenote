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
on main `d84f227`. For section 5.1 (owner addendum of 26.11): `spikes/m0/livetips_m0/evidence/` (module docstrings
and imports of `extract.py`, `store.py`, `search.py`, `worker.py`, `__init__.py`, a grep of `ingest.py`),
`spikes/m0/livetips_m0/judge.py` (docstring), `rules/evidence.yaml`,
`spikes/m0/bench/g0/` (`run.py` imports, `gold.yaml`, `gold_v2.yaml`, fixtures, results), PB-52 sections 11.4, 11.5 and 14,
and the CQ1 to CQ5 task titles (PB-90 to PB-94) in the team plan.

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
  Voice prints, keys and settings stay on the Mac. Admins and Pacenote staff never read content. Private calls stay
  private.
- **Where the evidence graph is built is open (decision 19).** Section 5.1 answers the owner's doubt (why build a
  local graph if the server holds everything, and how quality is kept) and compares a graph built on the Mac over
  server-synced data (A, recommended), on the server (B) and a hybrid (C). With A, preparation and live search keep
  running on the local evidence store (GraphRAG G2, G3) and the G0 gold set and CQ metrics stay the quality gate
  wherever the data lives.
- **What it overrides.** D27 ("transcripts stay on the Mac, never on our server") and the matching ADR lines (0.1, 2,
  9 "the Mac is the source of truth"). ADR 13 ("nothing in AI on the server") stays unless decision 19 picks B or C.
  Section 12 lists every decision as overridden, amended or still valid.
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

Goals of this spec (named SG1 to SG6; in this document G0 to G7 always mean the GraphRAG phases of block G):

- SG1. A copy of record on the server for every call artifact and the project register listed in section 2.1, except
  voice prints and the Verbatim text (decision 15). Project folder files only if decision 6 says so; with the
  recommended 6(a) they are not covered by SG1 and stay in the person's folder or the shared Drive folder.
- SG2. Access to it by the same roles as today (D36, D37, D40, D47), with one extra rule for transcripts and recordings
  that the owner decides (decision 4).
- SG3. The Mac keeps a bounded cache and works fully offline for calls and preparation of pinned calls.
- SG4. Migration of existing local and Drive data without loss, verified by checksums, reversible. The one planned
  difference: files uploaded stripped (Verbatim text or voice vectors removed, 2.4) are verified against their
  stripped bytes, and the Mac keeps the full local file (it is never evicted).
- SG5. Rollback at every phase.
- SG6. Quality maintenance (G0 gold set, CQ metrics, regression gate) keeps working when the content lives on the
  server, not only when it is local (section 5.1).

Non-goals (this spec): designing server-side AI (preparation on the server stays B11; a server-built graph is only
compared in 5.1 and decided by decision 19), a web or phone client (B11 PWA), a team
graph (G7), video retention by deal stage (SX13 builds on this spec), the lead calendar (SX1, small rows in Postgres,
not object storage), billing for storage (B10).

## 2. Data model

### 2.1 What goes where

| Artifact (today on the Mac) | Today on the server | After this spec | Store |
|---|---|---|---|
| Call metadata, summary, decisions, tasks, risks, Meeting Sheet JSON, numbers | yes (Postgres, [enc]) | unchanged | Postgres |
| Transcript `<stem>.md`, `<stem>.app.md` | no (link to Drive copy only) | yes | object, `content` bucket |
| Call notes `.notes.md` and `.app.notes.md`, brief `.brief.md` and `.app.brief.md`, `.sheet.md` | notes as summary only | yes (files) | object, `content` |
| `.work/` sidecars: `.tips.jsonl`, `.inbox.jsonl`, `.speakers.json`, `.sheet.json`, `.sheet.request.json`, cards, facts, the HubSpot snapshot and the rest of the 2.4 table | no | yes, one archive per call, **stripped**, only the files listed in 2.4 (default deny): `.speakers.json` by a field allowlist without voice vectors (`centroid`, `embs`, `lib_added`), Verbatim blocks and lines removed, `.voices.json`, semantic vectors, model logs and `<stem>.judge/` left out | object, `content` |
| `.work/web/<host>` stored site pages, `live/google/` picked copies | no | yes, owner only (`docs/en/live-tips.md:2612-2613` says researched pages never reach the server; decision 18) | object, `content` |
| Project register `live/commitments.jsonl`, `live/lessons.md`, `live/sources/` | register items only (D40, org projects) | yes (files) | object, `content` |
| Recording `video/<stem>.recording*.mp4` or `.m4a`, `.recording.json` | Drive link only | yes | object, `media` bucket |
| Project folder files the app reads (docs, PDFs, notes in the folder) | no (D40 file index: name, date, 600-char description, vector, org projects only) | optional, per project (decision 6) | object, `content` |
| Voice prints `.work/<stem>.voices.json`, the voice vectors inside `.speakers.json` (`centroid`, up to 10 `embs` and the name-to-vectors map `lib_added`, written by `recorder/src/fromfile.py` for calls from a file or recording), voice library | no | **no, stays on the Mac** (biometric data, decision 9); removed from the uploaded archive (2.4) | Mac |
| Keys, tokens, settings, local usage log | no | no | Mac, Keychain |
| Local evidence store `evidence/<uid>.sqlite` (G2) | no | optional encrypted snapshot (decision 8) | object, `content` |
| Meeting Sheet Verbatim blocks (PB-102, Unreleased: "The block stays on this Mac: call sync does not send it") | no | **no**: the sheet files and the `.work` archive are uploaded with the whole Verbatim blocks and the `kind: verbatim` tip lines removed, as call sync does today, with a fail-closed content check (2.4), unless the owner changes that promise (decision 15); such files are never evicted (2.4) | Mac |
| HubSpot snapshot next to the brief | no | yes, inside the `.work` archive, owner only | object, `content` |
| Live tip text | no (counts only) | in `.tips.jsonl` archive, owner only (decision 4) | object, `content` |

Rule of thumb: rows the API needs to filter, list or check stay in Postgres; whole files go to object storage; the
Postgres `objects` table (2.3) is the only index of the buckets.

### 2.2 Buckets

Hetzner Object Storage has three locations (Falkenstein, Nuremberg, Helsinki); the API server is in Helsinki.

| Bucket | Location | Holds | Settings |
|---|---|---|---|
| `pn-prod-content` | Helsinki (same as API) | text artifacts, archives, project files, store snapshots | private, versioning on, lifecycle: noncurrent versions and expired delete markers removed after 28 days (a deletion is gone within 30 days, below) |
| `pn-prod-media` | Helsinki | recordings | private, versioning on, lifecycle per prefix for retention (SX13 later) |
| `pn-prod-backup` | another location (Falkenstein or Nuremberg) | nightly incremental copy of both buckets (new and changed objects, already encrypted) and the encrypted Postgres dump | private, versioning on; the copy job's credentials allow put, list, head and get but **no delete** (scoped credentials on Hetzner: to verify); object lock if available (to verify), with a retention of at most 30 days (3.1) |
| `pn-staging-*` | any | the same for staging | never real data |

The backup copy, step by step:

- **Verify.** After each PUT the copy job reads the object back with HEAD (size, and the S3 checksum if Hetzner
  returns it, to verify) and compares it with `bytes` and `sha256_cipher` of the `objects` row; without a checksum it
  reads the object once and hashes it. Only then does it set `backup_verified_at`. List, head and get are read
  rights plus put; the job never needs delete.
- **Deletions.** The copy job cannot delete. Deletions reach the backup through the server's deletion sweep (the
  same one that clears `pn-prod-content` and `pn-prod-media`, 3.1), which uses a separate delete-only credential for
  `pn-prod-backup` and writes a delete marker for each deleted object. The sweep runs nightly, so a deleted object is
  marked in all three buckets within a day; a lifecycle rule on each bucket then removes noncurrent versions and
  expired delete markers after 28 days, so the bytes are gone everywhere within 30 days (if Hetzner lifecycle rules do
  not support this, to verify, the sweep deletes the versions itself). Never a blanket 30-day expiry of current
  objects: that would delete live backups.
- **Leftovers.** If the sweep misses an object, crypto-delete still makes it unreadable once the last dump with the
  wrapped key expires (3.1); the monthly restore test also lists backup objects without a live `objects` row.

One bucket per kind, not per organization: bucket names are global and limited per project, and access is enforced
by the API, not by bucket policy. No bucket is public; the bucket credentials (`PN_S3_ACCESS_KEY`,
`PN_S3_SECRET_KEY`, `PN_S3_BACKUP_ACCESS_KEY`, `PN_S3_BACKUP_SECRET_KEY` for the copy job,
`PN_S3_BACKUP_DELETE_ACCESS_KEY`, `PN_S3_BACKUP_DELETE_SECRET_KEY` for the deletion sweep) live only in the server
`.env` and the owner's secrets file. The Mac never holds bucket credentials.

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
| kind | `transcript` (`<stem>.md`), `transcript_app` (`<stem>.app.md`), `notes` (`.notes.md`), `notes_app` (`.app.notes.md`), `brief` (`.brief.md`), `brief_app` (`.app.brief.md`), `sheet_md`, `work_archive`, `web_pages`, `google_copy`, `register`, `lessons`, `recording`, `recording_meta`, `project_file`, `evidence_snapshot` |
| rel_path **[enc]** | the path inside the project folder, so the Mac can restore the file at the same place (the content is exact except for stripped files, 2.4) |
| rev, current | a new revision for every change; `current` marks the live one. Each revision is its own row with its own `object_id` (a new object key; an object key is never overwritten, so S3 versioning only holds deleted objects). A revision that is no longer current is kept 30 days after it was replaced (so a bad release or a wrong edit can be undone), then the nightly sweep deletes it like a deleted object (2.2); the current revision is kept until the call or the account is deleted or a retention rule removes it (4.5, decision 11) |
| bytes, sha256_cipher | for verify and dedup on upload |
| hmac_plain | HMAC-SHA256, with the person's **dedup key**, of the plaintext bytes **as uploaded** (for a stripped file, after the strip, 2.4): lets a Mac skip unchanged files without sending the plaintext or a plain hash. It is not hidden from the server itself: the server stores the dedup key wrapped and can unwrap it, like every other key (3.1, not end-to-end). The dedup key is a new random per-person key (not the DEK), stored wrapped in `user_keys` and handed to that person's signed-in devices, kept in the Keychain; it never reaches leads |
| stripped | true when the uploaded bytes differ from the local file on purpose (Verbatim blocks or tip lines removed, voice vectors removed, files left out of the `.work` archive, 2.4) |
| verified_at, backup_verified_at | when the server confirmed the checksum (3.1), and when the nightly copy job confirmed the object in `pn-prod-backup` (same `sha256_cipher`); both are eviction conditions (4.2) |
| wrapped_key, key_version | the object's data key, wrapped (section 3) |
| bucket, created_at, deleted_at | |
| visibility_class | `owner_only`, `call` (follows the call's visibility), `project_shared` (D40 items). The project register files (`register`, `lessons`, `live/sources/` as `register`) are `owner_only` (3.2) |

`call_assets` (ADR 6.2) keeps its Drive links for history; a new `kind = server_object` row points to `objects`.

### 2.4 Files uploaded stripped (Verbatim text, voice vectors)

Two things stay on the Mac: the Meeting Sheet Verbatim blocks (PB-102, decision 15) and voice prints (decision 9).
They are kept out by **allowlists, never denylists**, so a file or field added later is not uploaded by accident.

**The `.work` archive holds only the files in this table.** Every other file in `.work/` (and every subfolder:
`<stem>.judge/` and `web/`, which is its own kind `web_pages`, decision 18) is left out, whatever its name. A
`<stem>.app.<rest>` file follows the rule of `<stem>.<rest>`. The list comes from the suffixes the code writes on
25.11 (`sidecar_of`, `sidecar` and `CallLayout.sidecar` calls in `recorder/src`, `spikes/m0/livetips_m0` and
`app/Sources`, plus the suffixes the code only reads or skips); a new suffix stays out until it is added here.
Files outside the call folder are not in the archive at all: the preparation run copies (`prep-runs/` in
Application Support, `PrepRunner.swift:77`) and the evidence store with its `<uid>.state-requests.jsonl` log
(`evidence/store.py:348`, decision 8).

| Files | Rule | Why |
|---|---|---|
| `.speakers.json` | **stripped**: rebuilt from an allowlist at every level (below) | `recorder/src/fromfile.py` writes voice vectors into it: `centroid` and up to 10 `embs` per speaker (lines 291-292) and, after an edit, `lib_added`, a map from each name to that person's vectors (line 485) |
| `.tips.jsonl` | **stripped**: lines with `"kind": "verbatim"` are dropped | `recorder/src/transcriber.py:2505` writes the sheet's Verbatim blocks, text included, as such lines |
| `.sheet.json`, `.sheet.v<N>.json`, `.sheet.request.json` | **stripped**: every block with `"type": "verbatim"` is removed whole (its copy, pasted text, file path, sections and `sha256`) | as call sync does today (`CallSyncCore.swift:387-388`); the strip function is the one the evidence store already uses, `sheet_file_text` in `spikes/m0/livetips_m0/sheet/verbatim.py:261` |
| `.sheet-live.json` | as is, with the content check below | holds the sheet file name, the question ids and texts and which were asked (`transcriber.py:2581`) |
| `.inbox.jsonl`, `.feedback.jsonl`, `.edits.jsonl`, `.cards.json`, `.cards-fired.jsonl`, `.facts.json`, `.outline.json`, `.slides.json`, `.deck.json`, `.verify.json`, `.brief.preverify.md`, `.sales.json`, `.crm.json` (ids of the linked company, deal and contacts), `.crm.md` (the HubSpot snapshot, `CRMFlow.swift:5-9`), `.crm-log.json` | as is, with the content check below | call working files the call page, the after-call steps and a later preparation read |
| `.voices.json`, `.semantic.npz`, `.semantic.jsonl`, `.semantic-summary.json`, `.llm.jsonl`, `.timing.jsonl`, `.context.json`, `.context-correction.txt`, `.context.md`, `.run.json`, `.summary.json`, `.sheet.sources.json`, `.sheet-check.json`, `.prep.json`, `.sync.json`, `.drive.json`, `.notion.json`, `.notion-blocks.json`, `.named.md`, `.meet.md`, `.clean.md`, `.speech.md`, `.digest.md`, `.instructions.md`, `.app-requests.json`, `.claude.json`, `.norec.json`, `.outlang.json` | **left out** | voice prints (decision 9); text vectors and their logs (derived, and a vector needs its own PRIVACY line, section 5); raw model and timing logs (may hold any prompt text, the Verbatim copy included); engine, sync, Drive and Notion state, sheet sources and derived transcript variants, whose writers were not read for this spec: leaving them out is the safe default, and whether a restore needs any of them is checked in the phase 1 design |
| `.brief.orig.md` | as is, with the content check below | the brief as the model wrote it, copied before the person's first edit (`PrepDoc.swift:154`, `Prompter.swift:440`); a later preparation compares it with the brief to keep the person's edits (`PrepRunner.swift:485`), so a restore needs it |
| `.brief.prev.md`, `.slides.prev.json`, `.outline.prev.json`, `.agenda.json` | **left out** | the previous version kept for Restore previous version (`PrepRunner.swift:116`, `Deck.swift:550`) and the agenda that `sheet/derive.py:317` writes from the sheet (a fallback file for the recorder, `derive.py:1-7`); a restored call has no Restore previous version for its last preparation; whether the agenda must be derived again on restore is checked in the phase 1 design |
| `.speakers.md`, `.fired.jsonl`, `.status.json`, `.stdout`, `.stderr`, any `.npz`, `.entries.json` | **left out** | named in readers and skip lists (`pn_tools.py:26`, `sheet/lookup.py:33-34`, `47-48`) or written by the semantic eval tool (`semantic_eval.py:149`), not by a call; listed so the suffix test has a rule for them |
| any file not named in this table | **left out** | default deny |

**`.speakers.json` allowlist.** Top level: `clusters`, `roster`, `overrides`, `merged`, `skipped`, `rev`, `updated`,
`source`, `remember`, `edited_at`; everything else is dropped (`lib_added` today). Per speaker in `clusters`:
`name`, `side`, `seconds`, `first`, `how`, `score`, `named_by`, `sample`, `first_quote`, `last_quote`,
`suggestions`; everything else is dropped (`centroid` and `embs` today). Nested values keep a fixed shape:
`sample` only `start` and `end` (numbers); `first_quote` and `last_quote` only `t` and `text`; each item of
`suggestions` only `name` and `score` (what `VoiceLibrary.suggest` returns, `transcriber.py:3872`). The other
top-level values keep the shapes the code writes (`transcriber.py:4409-4421`, `4745-4746`, `fromfile.py:308-310`):
`roster` items only `name`, `side` and `src` (strings); `overrides` and `merged` maps of strings to strings;
`remember` a map of strings to booleans; `skipped` a list of strings; `rev` a number, `updated`, `source` strings,
`edited_at` a number. A value of another shape drops the whole key. `side` (team or client) is uploaded on purpose, and PRIVACY says so (section 10).

**Content check (fail closed).** Before the archive is built, the strip step reads every Verbatim block of the call's
sheet files (`verbatim.text` and `verbatim.source.text`) and cuts each into its lines and into runs of 40
characters. It then checks every entry on its **decoded** text: for JSON and JSONL every string value at any depth
(so `\n` and `\"` escapes do not hide a match), for Markdown and text the file text, all compared after whitespace
is collapsed. An entry that holds any such line (of 12 characters or more) or run is dropped from the archive and
its file name is logged. A Verbatim text shorter than 12 characters is not checked (too many false matches). A file
dropped this way is a bug to fix in this table, not a silent loss: the Mac keeps it (stripped files are never
evicted, below), and Settings > Storage and the restore screen show how many files of which calls the check left
out ("3 files of 2 calls kept only on this Mac: they repeat Verbatim text"), so the person sees it.

Such files are uploaded **stripped**: one deterministic strip function per kind, the row marked `stripped`. The
`.sheet.md` is a separate object (`sheet_md`), stripped by the same `sheet_file_text` (rendered again from its
`.sheet.json` without the Verbatim blocks). The `.work` archive is always stripped. It is built deterministically
(entries sorted by path, fixed timestamps and permissions, no compression metadata that changes between runs), so
the same local files always give the same bytes and the same `hmac_plain`. Rules:

- `hmac_plain` and `sha256_cipher` describe the stripped bytes. To compare a local file with the server, the Mac runs
  the same strip function (for the `.work` archive: the same deterministic build) on the local files first and
  computes `hmac_plain` over the result. This holds for the
  folder-sync dedup (4.3) and the location flag (4.4).
- A stripped file is **never evicted** from the Mac, whatever the phase or the cache budget, and text eviction
  (decision 7b, phase 4) skips it. The Mac copy is the only full copy.
- What a new or lost Mac gets: the sheets come back without their Verbatim blocks (the block, its source and its
  text are not on the server), so the person adds the block again; a file source fills it on Refresh, pasted text
  must be pasted again (CHANGELOG, PB-102). The `.work` folder comes back with the files of the table above only:
  the semantic vectors are rebuilt by the app, the logs are gone, and for the other left-out files the restore
effect is checked in the phase 1 design. Settings >
  Storage and the restore screen say so. Keeping a full copy would need decision 15(b). A restored `.speakers.json`
  has names, sides and quotes but no voice vectors and no `lib_added`, and a new Mac re-learns voices (decision 9).

**Phase 1 acceptance test (archive builder, pytest).** Fixtures for one call folder: a Verbatim block with a marker
string (pasted text and a file copy) in `.sheet.json`, `.sheet.v1.json`, `.sheet.request.json` and `.sheet.md`, a
`"kind": "verbatim"` line with the marker in `.tips.jsonl` and `.app.tips.jsonl`, the marker in `.app.llm.jsonl`;
voice vectors in `centroid`, `embs` and `lib_added` of `.speakers.json`, and a `.voices.json`; one unknown file and
one unknown top-level and per-speaker key in `.speakers.json`; and, for the content check, a multi-line marker
with quotes in it placed in an as-is file (`.facts.json`). The test builds the archive and checks the
**unpacked plaintext entries before encryption** (a search in compressed or encrypted bytes would pass by itself):
the entry names are a subset of the table above, the marker appears in no entry and in no other uploaded object
(`sheet_md`), no number list appears in `.speakers.json`, the unknown file and keys are absent, `.facts.json` is dropped and logged by
the content check, and building twice gives the same bytes. A second test lists the sidecar suffixes the code writes (the grep above) and fails when one
is not named in any row of the table (the default-deny row does not count), so a new file gets a rule before it
ships. The visible call files outside `.work/` (`VISIBLE_CALL` and `VISIBLE_PREP` in `recorder/src/call_layout.py:13-14`:
`.md`, `.app.md`, `.notes.md`, `.app.notes.md`, `.brief.md`, `.app.brief.md`, `.sheet.md`) are not archive entries:
the test takes them from those two sets and checks that each has its own kind in 2.3 instead.

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
- Deleting one call from the server (per-object crypto-delete): after the choice of 4.5 (a Download to this Mac
  first finishes before anything is cleared), the API at once sets `deleted_at` on every `objects` row of the call
  (every revision, not only `current`), clears their `wrapped_key` in the live database and starts no new download.
  A download already started (its 5-minute URL and its unwrapped key were handed out together, 3.1 Download) can
  still finish. The Mac's manifest marks the call excluded, so its files are not uploaded again. The
  person's DEK stays (other calls need it), so this is not a full crypto-delete: the wrapped object keys stay in the
  Postgres dumps and the bytes stay as noncurrent versions and in `pn-prod-backup` until the nightly sweep and the
  lifecycle rules remove them, within 30 days (2.2). PRIVACY must say so (section 10).
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
| Transcript, notes, brief (and their `.app.` variants) | yes | **policy `share_transcripts`** (decision 4); the brief only if it carries no researched-page text (to check in phase 1 design, decision 18), otherwise owner only | no | no | no |
| Recording | yes | **policy `share_recordings`** (decision 4) | no | no | no |
| `.work` archive (the files of the 2.4 table: tips text, inbox, speakers, cards, facts, HubSpot snapshot) | yes | no | no | no | no |
| `.sheet.md` file, stored web pages (`web_pages`), Google copies (`google_copy`) | yes | no (leads see the sheet copy in Postgres, which call sync already sends without researched pages) | no | no | no |
| Project register files (`register`: `live/commitments.jsonl`; `lessons`: `live/lessons.md`; `live/sources/`) | yes | no | no: the file can hold items from private calls; members keep seeing the register items of non-private calls in Postgres (D40, as today) | no | no |
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
  Those temporary files live in one app temp folder that is also emptied at every app start, so a crash leaves them
  at most until the next launch.

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
| Stripped files (sheets, every `.work` archive) | never (2.4) | |
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
  first** (default) or **Delete everywhere**. Without a choice nothing is deleted. Then the call's `objects` rows
  are marked deleted and their keys cleared at once, and the files are removed from storage and backups within 30
  days (3.1).
- `retention_months` and plan `history_months` never delete server files that are the only copy without the same
  offer by email and in the app 30 days before.
- D44 auto-delete of an inactive account with data (365 days, two emails) now deletes files too; the emails say so and
  link to Export.

## 5. Deep search: server vs local GraphRAG

| Need | Where it runs | Why |
|---|---|---|
| Preparation context (GraphRAG G3), live lookup (GraphRAG G4), after-call links (GraphRAG G5) | **Mac, local G2 store** with the recommended decision 19(a) (D57) and with 19(c); 19(b) changes this row (5.1) | zero network during a call, works offline, cost and route chosen by the person, already measured on G0 |
| A person's own search over all their calls, including evicted ones | Mac store (it keeps snippet text of evicted sources, 4.4) on the Mac that extracted them; calls this Mac never held need the extract-from-server step (5.1.2) | no server change needed |
| Lead or C-level search over the team's calls (B9) | server, as today: decrypt within the visible set (summaries, decisions, tasks, risks; 90 days, cap 2000 calls) | unchanged in phases 1 to 4 |
| Lead search over the team's **transcripts** | not in this spec's phases 1 to 4 (decision 10) | decrypting 2000 transcripts (about 120 MB) per query does not scale on the current server |
| Team graph (G7) | later, on the server objects of org projects (D40); this is the server graph of decision 19(c) | out of scope |

Options for team transcript search later (decision 10): (a) none, leads open a call and use find on the page;
(b) a blind keyword index: the owner's Mac uploads HMAC-hashed word tokens per call, the server matches hashed query
tokens within the visible set, no plaintext on the server, exact words only; (c) Mac-computed bge-m3 vectors per
transcript chunk in pgvector, as D40 already allows for project files: better recall, but vectors are derived from
the plaintext and partly invertible, so it needs its own PRIVACY line. Recommendation: (a) now, (b) when a team asks,
(c) only with G7.

None of the decision 10 options puts an AI model on the server. A graph built on the server is a separate choice
(option B of 5.1, decision 19); only that option or the hybrid C would change ADR section 13 ("the server sends
nothing to AI"). With the recommended 19(a), ADR 13 stays valid.

The evidence store is derived data, but rebuilding it on a new Mac re-runs LLM extraction (G2 section 13.4: rebuild
redoes extraction under the cost ceiling). An
encrypted weekly snapshot of the store file in `pn-prod-content`, owner only, lets a new Mac restore it instead
(decision 8; this is the "optional snapshot" of option A in 5.1).

### 5.1 Quality when the server is the source of truth

**The owner's doubt, plainly.** If the server becomes the source of truth and may hold every transcript, note and
sheet, why spend a week on a local evidence graph (GraphRAG G2 to G5) on the Mac? And when the content is on the
server, how is quality still measured and kept from regressing? The requirement: quality maintenance must keep working
when the copy of record is on the server, not only when the content is local (SG6).

**Short answer.** Most of the GraphRAG work is not about where the files sit. It is about what is extracted, from
which sources, with which evidence and how it is scored. That part works the same over server-held content, because
the Mac downloads (or already caches, 4.1) the text it extracts from. What is tied to the Mac is the SQLite store
class and the live path (GraphRAG G4). So: keep G2 and G3, keep G5 after G3, and hold G4 until decision 19 is taken.

#### 5.1.1 What is storage-independent and what is tied to the local store

Checked in this step (read in the PB-122 worktree, no code run):

| Part | Where it is | Tied to storage? | Checked how |
|---|---|---|---|
| Extraction prompt, schema and rules (hedge patterns, rules version) | `rules/evidence.yaml`, `rules/evidence_schema.json`; `extract.load_rules`, `extract.prefix`, `extract.extractor_id` (`<rules_version>:<sha256 of the prompt prefix>:10`) | **No**: files plus pure functions; the same prompt runs on any machine | read `extract.py:1-15` and `:39-41`, listed `rules/` |
| Evidence model (entities, claims, edges, snippets with line numbers, modality, quotes checked against snippet text) | the extraction contract of PB-52 sections 8.3, 8.4 and 8.6, coded in `extract.py` | **The model is not**; its code is today: `segment_view`, `catalog` and `validate` take a `store.Store` and call `store._fold`, `store._find_quote`, `store.claim_nkey`, `search.prepare` (`extract.py:102`, `:165-206`, `:313-473`) | read the imports and the call sites by grep |
| Applying the result (`apply_extraction`), the state-request log (`state_request` table), locks | `store.py:100`, `:475`; `store.py` uses `sqlite3` and `fcntl` directly (`:14`, `:19`, `:275-278`) | **Yes**: SQLite on the Mac | grep of `store.py` |
| Source selection and revoke (hide, exclude, revoked Notion pages, the state-only pass) | `ingest.ingest(store, root, hidden_calls, revoked, ...)` and `ingest.apply_state` (`ingest.py:3-4`), the hidden and revoked lists kept in store meta and `source.state` (`ingest.py:297-307`), `store.py:147-150` | **Rules are not, the state is**: which sources count is decided from lists the app passes and project folder paths; the state lives in SQLite | grep of `ingest.py` and `store.py`; the revoke code paths were not read line by line |
| Tip scoring (blind judge, rubric by call type) | `livetips_m0/judge.py` (reads a call `.md` and a tips `.jsonl` by path, writes `judge.jsonl` and `judge.summary.json`) | **No**: it reads files; it needs the call's transcript and tips log on the Mac when it runs | read the `judge.py` docstring only; a grep for `rubric` or `judge` also hits `signals.py`, `sheet/aftercheck.py`, `sales.py`, `routing.py`, `fact_quality.py`, `engine.py` (not read); `rules/` not grepped |
| Search (`search`, `entity_card`, access and time in SQL before ranking) | `search.py:340`, `:507` | **Yes**: SQL over FTS5 in the store | read the docstring and signatures |
| Extraction worker (route, cost ceiling, call-active abort) | `worker.py` imports `routing` and `cost` | **No** for the route: requests go to the AI the person chose (`evidence/__init__.py:5-7`); runs on the Mac | read imports and the package docstring |
| G0 gold set and benchmark | `spikes/m0/bench/g0/gold.yaml`, `gold_v2.yaml` (39 questions, CHANGELOG), synthetic fixtures (`fixtures/northpoint`, `v2`, `extract`, `topics`), `run.py` | **No** for the data (synthetic, D57, PB-52 line 75; never the owner's real calls). `run.py` drives the evidence CLI (`from livetips_m0.evidence import __main__`), so variant C today runs through the SQLite store | listed `bench/g0`, read `run.py` imports |
| Live path (GraphRAG G4) | PB-52 section 11.4: snapshot loaded into memory at call start, `search(mode="live")` opens the SQLite file read-only, answers within `budget.ms` (150 ms default), no query embedding | **Yes**: a local read-only SQLite file and an in-memory snapshot, by design (zero network on the tip path) | read PB-52 `:1326-1346`; `docs/specs/` has no G4 spec (only PB-37, PB-52, PB-53) |
| CQ1 to CQ5 (rubric v2, local metrics and `quality.json`, selective judge on the person's route, `call_quality` and `tip_scored` events, console Quality page) | tasks PB-90 to PB-94 | **Not verified in code here**: the names come from the team plan; a grep for `tip_scored`, `call_quality`, `quality.json` and `CQ1` to `CQ5` on this branch finds nothing. By their titles they score tips and calls on the Mac and send scores as events, which does not depend on where the files are stored | grep (no hits) and task titles only |

Not verified: whether any extraction step reads a file path directly instead of segment text (only the docstrings and
signatures say so), and how long a store-agnostic split of `extract.validate` and `catalog` would take (estimate
below).

What is wasted if the content moves to the server: nothing of the list above, with option A. With option B, the
SQLite store class and search SQL would be rewritten for Postgres (or kept only as the Mac's local copy, see C), and
GraphRAG G4 would be designed around a server-made snapshot pulled to the Mac (5.1.3); the prompt, schema, evidence model, gold set and CQ metrics carry over.

#### 5.1.2 The quality loop with server-held content

- **Where extraction runs.** A: on the Mac, as today. A source that is evicted (decision 7, text eviction is off by
  default) is already extracted before eviction; its snippets stay in the store (4.4). **Open gap this spec adds to
  option A:** a source that exists only on the server for this Mac (a call made on a second Mac, or history on a
  new Mac, 6.1 item 5 downloads only pinned objects) is not in this Mac's store today. A needs an **extract from
  server** step. Scope: only the person's own objects, never calls they see through a lead policy (decision 4), so a
  lead's Mac never sends a rep's transcript to the lead's AI route. Mechanism (not checked against `ingest.py`
  beyond its signature; `ingest(store, root, ...)` walks the project folder, `ingest.py:3` and `:225`): download the
  file to its normal path in the project folder, run the normal ingest and extraction pass, then let the per-file
  eviction rule (4.2) evict it and set the G2 location flag to `server` (4.4). Phase: with phase 4 (section 8); until
  then a second Mac's store covers only the files that Mac holds. On a new Mac the decision 8 snapshot replaces this
  for everything older than the snapshot. Two Macs of one person each extract new calls they did not record,
  so extraction is paid twice unless the snapshot is shared (the cost per call is not estimated in this step; S3
  calibrates it). B: on the server, which decrypts the content (it can today, 3.1 "What the server can read") and sends
  it to a model. C: both.
- **Where the gold set and CQ metrics run.** The G0 gold set is synthetic and lives in the repo, so it runs wherever
  the code runs (developer Mac, CI, or a server job for B); it never needs real content. CQ metrics score the person's
  own calls where they are computed: on the Mac for A and C (the tips log and transcript are local during and after
  the call). For B, a server-side CQ run on real content would need its own consent line; not recommended.
- **What the Mac pulls to evaluate.** For the gold set: nothing (fixtures are in the repo). For CQ and the judge on
  its own calls: only the transcript and tips log of the calls it scores. Scoring runs right after the call, when
  everything of the call is pinned (4.1: everything of a call until its upload is confirmed). A call scored later
  after eviction is downloaded on demand.
- **What telemetry carries.** Scores and counts only (`call_quality`, `tip_scored` as named in CQ4), the model,
  route and release ids, and a `store` field (`sqlite_local`, later `server` for B or C) and a `source_location`
  share (local or extracted from server) so a regression can be split by store and data location; never transcript,
  note or tip text. This is a requirement of this spec for any option, not a claim about existing code (CQ4 is not
  on this branch).
- **How a regression is caught when the store or the data location changes.** Two cases:
  - **A location change** (option A: phase 4 text eviction, the extract-from-server step) keeps the SQLite store. The
    gate: the same extraction code and `extractor_id` (same rules version and prompt hash) on both sides, the same G0
    gold hash (G0 already fails a C run on any small-gold miss as "fixture stale", PB-52 section 14), and a G0 run
    with the fixture's sources marked evicted or extracted from server, under the PB-52 G0 gate wording "no existing
    column regresses" (PB-52 `:1739-1740`). No new interface is needed.
  - **A store change** (option B, or the server graph of C) adds, on top of that gate, a store-agnostic interface:
    the store calls of `extract.segment_view`, `catalog`, `validate` and `search` go through one small interface
    (`fold`, `find_quote`, `claim_nkey`, `visible_entities`, `search`) with the SQLite store as the first
    implementation; the second store is a new G0 variant that must not regress any column of the SQLite variant.
    Estimate, not measured: 2 to 4 days for the interface, mostly moving calls (store code about 590 lines, extract
    and search about 1230, counted with `wc -l`).
  - In both cases the CQ scores per release, per model and route and per `store` value on the console Quality page
    (CQ5) catch a drop in real calls that the synthetic gold set misses.

#### 5.1.3 Three architectures compared

| | (A) Graph on the Mac over server-synced data | (B) Graph on the server | (C) Hybrid: Mac graph for live and prep, server graph for team search later (G7) |
|---|---|---|---|
| Quality | same extraction and gold set as today; one store per person, built from what that Mac can read (own calls, plus the extract-from-server step of 5.1.2); the extraction model is each person's route, so G0 and CQ are reported per model and route | same prompt and gold set can run; one model for everyone, so one set of scores; the server sees every synced call of the person, so a full rebuild is easier; no extra quality from location alone | A for the person; team graph quality measured separately on G0 plus a team gold set (none exists) |
| Live latency | local read-only SQLite and an in-memory snapshot, 150 ms budget (PB-52 11.4) | a network call on the tip path, which breaks "zero new network calls" (D57, section 7), or a server-made snapshot pulled to the Mac before the call and answered locally; GraphRAG G4 would then be designed around that pulled snapshot instead of the local SQLite file of PB-52 11.4 | as A for live |
| Privacy | content decrypted only on the Mac; the AI route is the person's (D57); store snapshot optional, owner only (decision 8) | the server decrypts content and sends it to a model: **an ADR 13 change**, a new PRIVACY line and a consent (the person agrees that the server sends their calls to the named model); D57 overridden | as A for the person; the team graph needs B's changes but only for org projects that opt in |
| Cost | extraction on the person's route and ceiling, as today; storage: the snapshot only (small) | **a model on the server**: per-token cost of every extraction for every user (not estimated in this step; it would be on the order of today's per-person extraction cost, multiplied by users, paid by Pacenote); plus Postgres and compute growth | A's cost plus the team graph cost for org projects only |
| Offline | full: prep and live work offline (section 7) | prep needs the server; live needs a pre-pulled snapshot | as A |
| Who pays | the person (their route or the model on their Mac) | Pacenote, unless plans price it (B10) | the person for their graph, the organization's plan for the team graph |
| What it needs | the location flag (4.4), the extract-from-server step (5.1.2), decision 8, the location-change gate (5.1.2) | a model choice for the server (a hosted API under a DPA or a self-hosted model on a GPU host), an ADR 13 change, a consent and cost decision, PRIVACY and TERMS text | A, plus B's needs limited to org projects, after G7 is specified |

#### 5.1.4 Recommendation and what it means for GraphRAG G2 to G5

Recommendation: **(A) now, with (C) as the later path for team search (G7)**. Reasons: the live path must stay local
for latency and offline in every option, so a Mac-side store is needed anyway; the person's route keeps the cost and
consent model of today; and the store is derived data that a new Mac can restore from the decision 8 snapshot or
rebuild from server-held text.

Order of GraphRAG phases:

1. **G2 (store) keep**, finish as planned; add the location flag of 4.4 before text eviction (phase 4).
2. **G3 (related evidence in preparation) keep**: preparation runs on the Mac in every option and benefits first.
3. **G5 (after-call links) after G3**: it runs after the call, so it does not touch the live path.
4. **G4 (live lookup) hold** until decision 19 is taken. G4 is the only phase tied to the local SQLite file by
   design (PB-52 11.4); with B it would be redesigned around a pulled snapshot. Today's live lookup over the in-memory
   `FolderIndex` keeps working meanwhile.
5. Before any of these reads server-held data (text eviction, the extract-from-server step): the location-change
   gate of 5.1.2. The store-agnostic interface is needed only if decision 19 picks B or C.

What stays valid if the owner picks B or C: the prompt, schema and rules, the evidence model, source selection and
revoke rules, the G0 gold set and fixtures, tip scoring (`judge.py`), CQ1 to CQ5 and the telemetry rule (scores,
never content), GraphRAG G3 preparation logic (it reads search results, not SQLite rows directly; not checked in
code, G3 is a spec on main). What would be redone with B: the SQLite store class, the search SQL, and GraphRAG G4
(held, so nothing is lost). With C the Mac store, its search and G4 stay as in A; a server store and its search are
added for org projects only.

Estimates in this section (2 to 4 days for the interface, B's cost order) are estimates, not measurements.

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
  (4.4) on the Mac that extracted it (otherwise 5.1.2), so project memory can still use it.
- D50 stays: with a saved session the app works offline; the first launch offline shows the explanation and Retry.
- Server down for days: nothing is lost on the Mac (not yet uploaded files are pinned); evicted files open again when
  the server is back. The health line (D53) shows "Pacenote server unreachable".
- A lead offline sees only what is in memory; the team list is not cached (D47).

## 8. Phases and prerequisites

| Phase | What | Prerequisites | Can be rolled back by |
|---|---|---|---|
| 0 | Requirements before any upload: Postgres backups kept off the API server, nightly incremental bucket copy (new and changed objects only) to `pn-prod-backup` in a second location that sets `backup_verified_at`, a monthly restore test that includes a sample of objects, two offline copies of the master key; buckets created, `objects` table and API behind a flag | none | not needed |
| 1 | Upload only: new calls and Upload history copy to the server; nothing evicted; owner-only access | phase 0 green; the archive tests of 2.4 (marker and vectors absent from the plaintext entries, every written suffix has a rule) pass; the section 10 acceptance grep is clean (also before phases 2 and 3) | flag off; delete server objects (crypto-delete) |
| 2 | Lead and C-level access to transcripts and recordings by policy (decision 4); call page opens from the server; Drive upload optional | phase 1 on the owner's own calls for 2 weeks with zero checksum mismatches | policy off; nothing is evicted in phase 2, so every file is still on the owner's Mac, and Drive upload keeps today's setting until phase 3 is stable (6.2), so leads keep Drive links where the person shares them |
| 3 | Eviction of recordings under the per-file rule of 4.2 (app media folder, 4.3); deletion offers (4.5); Settings > Storage | a restore of a sample of recordings from `pn-prod-backup` passed; the copy job sets `backup_verified_at` reliably for 2 weeks | eviction off and **Restore all to this Mac** (re-download) |
| 4 | Optional text eviction (decision 7) with the G2 location flag (4.4), store snapshot (decision 8), the extract-from-server step for the person's own calls (5.1.2) | phase 3 stable for a month; the regression gate of 5.1.2 (same `extractor_id` and gold hash, the evicted-source G0 variant matches) | same as 3 |
| 5 | Team transcript search (decision 10), G7 (the server graph of option C, decision 19) | owner decision; for a server graph also the ADR 13 change, consent and model choice (5.1.3) | flag off |

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
bucket, add one `P_base` per extra bucket (not verified). Replaced revisions (kept 30 days, 2.3) are left out: they
are text only (a recording is written once), and every text-only row stays far under the included 1 TB, so even
several rebuilds of each `.work` archive within 30 days change no row.

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

- What counts: every current object revision of the person (replaced revisions kept 30 days, 2.3, S3 noncurrent
  versions of deleted objects and the backup copy do not count).
- At quota: uploads stop with a clear line in Settings > Storage and on the call page; **nothing is evicted that is
  not uploaded**, so the Mac keeps everything new and the person is never left without a copy. Text keeps uploading
  before recordings, so quota is hit by recordings first.
- `history_months` on Solo limits what the server **shows**, not what it keeps: older files stay downloadable to the
  person's own Mac and in Export, and are never deleted by the plan alone (4.5).
- New numbers are decision 16; they depend on the retention default (decision 11).

## 10. PRIVACY and TERMS changes (draft EN; UK text is written with the release, mirrored)

PRIVACY.md lines to replace or add, in the release that ships phase 1 (and the site pages with them). Quotes are
verbatim from `PRIVACY.md` and `TERMS.md` on main (line numbers of 25.11); the UK files get the same changes.

- `PRIVACY.md:7`, the first promise: "**Audio stays on your Mac.** Speech is recognised locally with Whisper; audio is
  not sent to any AI service." Phase 1 breaks the first sentence for recorded calls. Replace with: "**Audio is never
  sent to an AI.** Speech is recognised locally with Whisper. If you record a call, the recording also goes to the
  Pacenote server, encrypted, when sync is on for the project (below); without recording, no audio leaves your Mac."
- `PRIVACY.md:8`, "Call recording": "saves ... as a file in the project's `video/` folder on your Mac" and "It goes to
  Google Drive only when you upload it". Name the Pacenote server (encrypted, when sync is on) as the default second
  place and Drive as optional, and say that from phase 3 new recordings are saved in the app's own folder on the Mac
  (not in `video/`, 4.3) and older recordings can be removed from the Mac and opened again from the server.
- `PRIVACY.md:9`: "**Transcripts, voice prints, settings and logs** stay in your project folders, the app's folder and
  the macOS Keychain." Replace with: "**Your calls on the Pacenote server.** For each project where sync is on, the
  app also sends the call's transcript, notes, brief, Meeting Sheet, the call's working files (including the text of
  the live tips, seen only by you) and, if you record, the recording. They are
  encrypted on your Mac before they leave it, with a key of their own that is protected by your content key; the
  storage (Hetzner Object Storage, EU) holds only encrypted files. Your Mac keeps what your next calls need; older
  recordings can be removed from the Mac and opened again from the server. Voice prints (also the voice data in a
  call's speaker list), settings, keys and the local usage log stay on your Mac." With decision 18(a) add after "the
  text of the live tips": "and the web pages researched for it"; with 18(b) the researched pages stay on the Mac
  and the sentence stays as written.
- `PRIVACY.md:13`, "Google": "Copies of the documents stay in the project folder on your Mac" becomes: "Copies of the
  documents read for a preparation stay in the project folder on your Mac and are stored with the call on the
  Pacenote server, encrypted, used only for your preparations."
- `PRIVACY.md:14`, "Pacenote account and server":
  - Remove "Audio, video, transcripts, voice prints and the text of live tips never go to the Pacenote server" and
    point to the new paragraph above; say that voice prints never go.
  - "Participants' names, whether they are from your company and how long each one spoke are not sent yet; this
    policy will say so before they are." This becomes false: transcripts carry speaker names and the `.work` archive
    carries `.speakers.json` (without voice vectors, but with each speaker's side, 2.4). Replace with: "Participants'
    names, whether they are from your company and how long each one spoke are sent inside the call's files,
    encrypted, visible only to the people who may open those files. Voice prints are not sent."
  - "Participants' email addresses are never sent." Transcripts, briefs, sheets and the HubSpot snapshot can contain
    attendee email addresses from the calendar invitation and the CRM. Either the line changes to "are sent only
    inside the call's files, encrypted, visible to the people who may open those files" or the upload removes email
    addresses from the files (decision 17).
  - "Sync to Pacenote server (Settings > Projects > the project's menu; off by default)" and "Signing in alone sends
    nothing from your calls: calls go to the server only for projects where you turned Sync on." With decision 3(a)
    sync is on by default for new projects. Replace with: "Sync is on for new projects; you can turn it off per
    project or leave one call out. Signing in alone sends nothing from projects where Sync is off." With 3(c) both
    lines stay.
  - "The server and its backups are in the EU (Hetzner, Finland)": the backup bucket is in Germany (2.2). Replace with
    "in the EU (Hetzner, Finland, with backups in Germany)".
  - Deletion. Today: "deleting all your data: your content key is destroyed at once and the rest is removed within
    30 days, also from backups." The key is still destroyed at once in the live database, but the wrapped key stays
    in database backups for up to 30 days (3.1), so add: "The files of your calls are removed from the storage and its
    backups within 30 days, and cannot be read by anyone once the last backup expires." Where PRIVACY says deleting
    server data leaves the Mac copy, say instead: Pacenote shows what exists only on the server and offers to
    download it first (4.5).
  - Deleting one call. Today: "A call's page can leave that one call out, or delete that call from the server (it is
    removed at once and not sent again)." (UK `PRIVACY.uk.md:14`: "видалити його із сервера (він зникає одразу і
    більше не надсилається)".) Once its files are on the server, they are not removed at once (3.1). Replace the
    bracket with: "(it disappears from your account at once and is not sent again; its files are removed from the
    storage and its backups within 30 days)". The UK line changes the same way.
- `PRIVACY.md:11`, HubSpot: "a local snapshot next to the brief" becomes "a snapshot stored with the call, on your Mac
  and, encrypted, on the Pacenote server".
- `PRIVACY.md:16`, "Organizations":
  - Add: "If your organization turns it on, your team lead (and C-level on Enterprise) can also open the transcripts
    and recordings of your calls that are not private. Each opening is recorded and you see it in Who viewed my calls.
    Admins and Pacenote staff never open them."
  - "the files themselves never go to the Pacenote server" (the project file index) stays true for project folder
    files with decision 6(a); add "except the copies a preparation read (Google, above)". With 6(b) or 6(c) it
    changes.
  - "(not the calls, summaries, Meeting Sheets or transcripts)" for project members stays true (3.2).
- `PRIVACY.md:14`, "(never the text of tips)" in the list of numbers sent: stays true for those numbers, but the
  text of live tips now goes inside the call's working files, encrypted, seen only by you. Say so next to it.
- `PRIVACY.md:10`, "**Text leaves the Mac only to services you set up with your own keys or accounts:**": add "and
  to the Pacenote server, encrypted, for projects with sync on (below)".
- `PRIVACY.md:12`, Notion: "the read-only local copy of Notion if you turn it on (files in your work folder, never
  sent anywhere)" stays true: the local Notion copy is not a call file and is not uploaded (it is a project folder
  file only if it sits in a project folder, then decision 6 applies; with 6(a) it stays).
- `PRIVACY.md:20`, "**Otherwise nothing is sent to the author** (apart from the Pacenote account, the service emails
  and the product analytics above)": stays true; the account paragraph now names the files.
- `PRIVACY.md:21`: "Summaries and Meeting Sheets can hold confidential or personal information about them and your
  clients" becomes "Summaries, Meeting Sheets, transcripts and recordings can hold ...".
- The Verbatim block (PB-102) stays off the server, so its line in CHANGELOG and docs stays true (decision 15).

TERMS.md (`TERMS.md:12`):

- Replace "keep your own copies, your calls stay on your Mac" with: "The server keeps the copy of record of your calls
  for projects with sync on. It is in beta without an uptime promise: export your data regularly (Settings > Account
  > Export)."
- "including confidential and personal information in summaries, Meeting Sheets and (with organizations) shared
  project cards, project state and file descriptions": add transcripts, recordings and the call's files.
- Add to the organization part: the organization decides whether leads open transcripts and recordings, and is
  responsible for telling its people.
- "if the service ends, you get at least 30 days to export": stays, now including files (export becomes a zip with
  the folder layout).

Docs and README lines to update in the same release (EN and UK together; line numbers of 25.11):

| EN | UK | Today | Change |
|---|---|---|---|
| `README.md:7-8` | `README.uk.md:7-8` | "Audio is transcribed on the Mac and never leaves it" | audio is transcribed on the Mac and never sent to an AI; a recording you turn on is kept on the Pacenote server, encrypted, when sync is on |
| `README.md:44` | `README.uk.md:44` | "saved on your Mac for watching a call again" and "Upload to your own Google Drive by a click or after every call, to share it." | kept with the call on the Pacenote server, encrypted, when sync is on; from phase 3 older recordings may leave the Mac (4.2); Drive stays optional for sharing |
| `README.md:111` | `README.uk.md:112` | "your audio stays on your Mac (a call recording, if you turn it on, too, unless you upload it to your own Google Drive)" | audio is never sent to an AI; a recording goes to the Pacenote server encrypted when sync is on, and to Drive if you upload it |
| `docs/en/live-tips.md:2879` | `docs/uk/live-tips.md:2896` | "Audio and transcripts stay on your Mac." | transcripts and recordings go to the Pacenote server encrypted for projects with sync on; voice prints stay on the Mac |
| `docs/en/live-tips.md:2300` | `docs/uk/live-tips.md:2322` | "The recording stays on the Mac" | from phase 3 new recordings are saved in the app media folder, not `video/` (4.3); a recording stays on the Mac until it is evicted (4.2), then opens from the server |
| `docs/en/live-tips.md:2612-2613` | `docs/uk/live-tips.md:2631-2632` | "nothing goes to Pacenote servers" and "their titles, addresses and quotes stay on this Mac (only the site name goes, so a token in a page link is never sent)" | with 18(a) the stored pages and the full `.sheet.md` go with the call's files, encrypted, owner only; the sheet copy that others may see still leaves them out. With 18(b) the line stays |
| `docs/en/live-tips.md:2909` | `docs/uk/live-tips.md:2925` | "Participants' names are not sent yet, their emails never." | as the PRIVACY lines above (decision 17) |
| `docs/team/README.md:5-7` | `docs/team/README.uk.md:5-7` | "in Stage 1 transcripts stay with the person (on the Mac and, if they choose, in their Google Drive)" | with this spec transcripts go to the server, encrypted, for projects with sync on |
| `docs/team/README.md:14` | `docs/team/README.uk.md:15` | "Recording, speech recognition, live tips and voice prints stay on each person's Mac." | speech recognition, live tips during the call and voice prints stay on the Mac; recordings and the tips text after the call go to the server, encrypted |
| `docs/team/README.md:77-79` | `docs/team/README.uk.md:79-81` | "Transcripts stay in the project's `live/` folder in Drive" | transcripts are on the Pacenote server; the Drive copy is optional |
| `docs/team/README.md:130` | `docs/team/README.uk.md:133` | "The Mac stays the source of truth for its own calls; the server holds a copy." | the server is the copy of record; the Mac is a cache (section 12) |
| `docs/team/README.md:17` | `docs/team/README.uk.md:18` | "Audio never leaves the Mac. That is the privacy promise" | audio never goes to an AI; recordings go to the team's server encrypted, by policy (decision 4) |
| `docs/team/README.md:121-125` | `docs/team/README.uk.md:124-128` | table "What goes to the server, and what never does": audio **never**, video and transcript only the link, live tips never | audio and video: the recording, encrypted, when sync is on; transcript: yes, encrypted; live tips: the text in the owner-only `.work` archive; voice prints: still never |
| `docs/en/live-tips.md` sync section | `docs/uk/live-tips.md` | the list of what call sync sends | add the files of section 2.1 |

`docs/en/live-tips.md:1165` ("the voice library stays on the Mac") stays true.

**App text (EN and UK together).** The app makes the same promises in its own text strings, which users see at sign-in,
in Settings and on the call page. They change in the release of the phase that makes them false, in
`app/Resources/i18n/en.json` and `uk.json` (same keys), the tips window `app/Resources/tips/tips.js` (EN and UK tables)
and the macOS permission text `app/Resources/InfoPlist/{en,uk}.lproj/InfoPlist.strings`. Keys and fragments are
verbatim as of 25.11 (EN, then UK); the new wording follows PRIVACY above and is written EN and UK together.

| Phase | Key | Today (EN / UK fragment) | Change |
|---|---|---|---|
| 1 | `gate.intro` (sign-in screen) | "Audio and transcripts stay on this Mac" / "Аудіо й транскрипти лишаються на цьому Маці" | audio is never sent to an AI; for projects with sync on, transcripts, the call's files and recordings are kept on the server, encrypted |
| 1 | `account.what.never` (Settings > Account) | "Never: audio, video, transcripts, voice prints or the text of live tips." / "Ніколи: аудіо, відео, транскрипти, голосові відбитки і тексти живих підказок." | never: voice prints (also inside the speaker list), settings, keys, the local usage log |
| 1 | `account.what.goes` | "links to your recording and transcript in Google Drive" and "Participants' names do not go yet; their email addresses never do." / "Імена учасників поки не надсилаються, їхні адреси не надсилаються ніколи." | add the files of 2.1 (transcript, notes, brief, sheet file, the call's working files with the tips text, the recording), encrypted; names and emails as `PRIVACY.md:14` (decision 17); researched pages only with decision 18(a) |
| 1 | `settings.about.dataBody` | "Audio stays on this Mac. Transcripts, voice prints, settings and logs stay in your folders and the Keychain." / "Аудіо лишається на цьому Mac. Транскрипти, голосові відбитки, налаштування і логи лишаються у ваших папках і Keychain." | as `PRIVACY.md:7` and `:9` above |
| 1 | `wiz.folder.sub` (new project) | "Nothing in it goes to the server." / "Нічого з неї не йде на сервер." | the calls in it go to the server, encrypted, when sync is on; other files of the folder only with decision 6 |
| 1 | `page.has.note` (call page) | "The transcript and the audio stay on this Mac." / "Транскрипт і аудіо лишаються на цьому Маці." | say where this call is: on this Mac and on the Pacenote server, or on this Mac only (sync off or call left out) |
| 1 | `onb.calls.rec.sound.sub` | "Stays on this Mac." / "Лишається на цьому Маку." | kept with the call; on the server, encrypted, when sync is on |
| 1 | `page.rec.driveManual` | "Stays on this Mac; upload from the call page" / "Лишається на цьому Маці; завантаження зі сторінки дзвінка" | this line is the Drive status: "Not in Drive; upload from the call page" |
| 1 | `rec.set.drive.sub` | "The recording stays on this Mac." / "Запис лишається на цьому Mac." | the Drive copy is for sharing; the recording is kept with the call (on the server when sync is on; from phase 3 it may leave this Mac, 4.2) |
| 1 | `tracker.localBanner`, `tracker.kept` | "Everything here is kept in Pacenote on this Mac." and "Kept on this Mac" / "Усе тут зберігається в Pacenote на цьому Маку." and "Зберігається на цьому Маку" | the project register goes to the server as a file, owner only (2.1, 3.2): "kept in Pacenote on this Mac and, encrypted, on the Pacenote server" |
| 1 | `tr.privacy`, `tr.nothingSent` (Transcribe window), and the rule text `rules/transcribe.yaml:15` | "Nothing leaves it until you click Add to Notion." and "Nothing is sent without a click." / "Нічого не виходить назовні, поки ви не натиснете Додати в Notion." and "Без кліку нічого не надсилається." | with a project chosen the transcript joins that project's `live/` folder as a call (`TranscribeWindow.swift:377`), so with sync on it also goes to the server, encrypted, without another click; say so |
| 1 | the rule text `rules/google.yaml:42` | "kept on this Mac" (the Google copy) | as `PRIVACY.md:13`: also stored with the call on the server, encrypted, owner only (3.2) |
| 1 | `sync.call.delete.m` | "Its notes, decisions, tasks, Meeting Sheet and Drive links are removed from the server" and "The files on this Mac and in Google Drive stay." / "Файли на цьому Маці і в Google Drive лишаються." | add the call's files, removed from the storage and its backups within 30 days (as `PRIVACY.md:14`); from phase 3 the download offer of 4.5 |
| 1 | `account.export.sub` | "export.json with every call and part, and a readable Markdown file per call" / "export.json з кожним дзвінком і його частинами" | add the call's files in the folder layout (TERMS above) |
| 1 | Audio promises: `onb.welcome.audio`, `home.welcome.body`, `how.12`, `setup.step.recorder.why`, `set.audioAndVoicePrints`, `page.transcript.local`, `tips.js` `onDevice` (EN line 101, UK line 192), `NSMicrophoneUsageDescription` (both `InfoPlist.strings`) | EN: "Audio stays on this Mac.", "The sound stays on this Mac", "Audio never leaves the Mac", "audio never leaves the Mac", "Audio and voice prints never leave the Mac", "Audio stayed on this Mac.", 'Audio stays on this Mac', "Audio never leaves it." / UK: "Аудіо лишається на цьому Маку.", "Звук лишається на цьому Маці", "Аудіо ніколи не залишає Mac", "аудіо ніколи не залишає Mac", "Аудіо й голосові відбитки ніколи не залишають Mac", "Аудіо лишилось на цьому Маку.", 'Аудіо лишається на цьому Mac', "Аудіо його не залишає." | false for a recorded call with sync on. New core: "Speech is recognised on this Mac; audio is never sent to an AI", plus, where the text has room, "a recording you turn on is kept on the Pacenote server, encrypted, when sync is on". Voice prints keep "never leave the Mac". The tips window footer `onDevice` becomes "Transcribed on this Mac" / "Розпізнається на цьому Mac" |
| 2 | `team.mark.rec`, `team.drive.rec`, `team.drive.transcript` (lead's team list) | "Has a recording in Drive", "Open the recording in Drive", "Open the transcript in Drive" / "Є запис у Drive", "Відкрити запис у Drive", "Відкрити транскрипт у Drive" | with `share_recordings` or `share_transcripts` on (decision 4), new labels that open from the server; the Drive labels stay for calls that only have a Drive link |
| 2 | `rec.saved.notify`, `rec.set.folder.sub` | "Upload it to Google Drive from the call page." and "To keep recordings on a shared drive or show them to your lead, move or share this folder in Google Drive." / "Вивантажити в Google Drive можна зі сторінки дзвінка." and "Щоб записи лежали на спільному диску або їх бачив керівник" | Drive becomes optional (6.2): the lead sees recordings through the server by policy; the Drive upload stays as an option |
| 3 | `rec.set.where`, `call.record.help`, `su.opt.record.sub`, `tips.js` recording `hint` (EN line 21, UK line 114) | "Saved on this Mac in <project>/video/", "Saved on this Mac in the project's video folder.", "Saved on this Mac.", 'Saved on this Mac in the project' / "Зберігається на цьому Mac у <проєкт>/video/", "Зберігається на цьому Mac у папці video проєкту.", "Зберігається на цьому Маку.", 'Зберігається на цьому Mac у папці video проєкту' | new recordings go to the app media folder (4.3) and older ones may be removed from this Mac and open from the server; "Never sent to an AI" stays |
| 3 | `account.delete.sub`, `account.delete.m1`, `account.notice.deleted` | "Calls on this Mac and files in your Google Drive stay.", "Calls on this Mac and files in Google Drive are not touched." and "Calls on this Mac and files in Google Drive stay." / "Дзвінки на цьому Маці і файли у вашому Google Drive лишаються.", "Дзвінки на цьому Маці і файли в Google Drive не зачіпаються." and "Дзвінки на цьому Маці і файли в Google Drive лишаються." | files that exist only on the server are shown first with Download to this Mac (4.5) |
| 3 | `plans.local`, `proj.remove.keepSub`, `set.removesAProjectOr` | "Everything on your Mac: tips, Meeting Sheet, recording and the rest.", "The folder {path} stays on your Mac with its calls and notes.", "The folders stay on your Mac" / "Усе на вашому Маці: підказки, Meeting Sheet, запис та інше.", "Папка {path} лишається на вашому Маці з дзвінками і нотатками.", "Папки лишаються на Mac" | add that recordings removed from this Mac (4.2) stay on the server and open from there |
| decision 3 | `account.syncLater` | "Off by default" / "За замовчуванням вимкнено" | with 3(a): on for new projects; with 3(c): stays |

Stay true, no change (with the reason): `setup.step.voices.why`, `lai.voicesOn`, `set.removesThisPersonS` (voice
prints stay on the Mac, decision 9); `set.builtInBgeM3`, `mem.consent.local`, `ai.group.sub.mac` (the local model
and search, no server embeddings, section 5, D57); `usage.toggle.sub` (the local usage log is not uploaded);
`htask.footer`, `int.hubspot.sync.autoFooter`, `crm.deal.evidence`, `crm.unverified` (HubSpot, not the Pacenote
server); `ntset.autoAdd.i`, `ntset.autoAddRow.tasks.i`, `nlc.body` (Notion and the local Notion copy, `PRIVACY.md:12`
stays); `account.optional` (without signing in nothing is uploaded); `account.notice.out`,
`account.notice.outEverywhere`, `account.everywhere.m` (signing out deletes nothing, on the Mac or the server);
`sync.page.deleted`, `sync.page.removed`, `sync.line.deleted`, `sync.line.removed` ("not sent again" holds);
`wiz.card.sub`, `wiz.card.shared`, `proj.sample.sub` (the project card and the sample project are not call files);
`orgsetup.projects.sub` (calls move with their files, D36); `onboarding.permissions.body`, `feat.transcribe.what`,
`module.transcribe.what`, `home.transcriptSaved`, `settings.pane.recording` (recognition and recording happen on the
Mac); `module.drive.what`, `rec.drive.done`, `rec.set.transcript`, `rec.page.transcriptInDrive` (the optional Drive
copy); `proj.leaves`, `gen.group.sub.data` (headings); `after.draftTo`, `orgsetup.invite.failed`,
`call.whatMatters.info`, `ai.chain.footer` (not about where call files live); `rules/whatsnew.yaml:154` and `:155` (a past
release note). Strings that only say text goes to an AI (`onb.claude.without`) are not affected.
Also hit by the grep below and true after this spec: `how.10`, `how.32` (nothing is sent during the call or to the
client; uploads run after the call); `tracker.foot.local`, `dec.footnote`, `page.trackerNote`, `mem.useAI.off`
(decisions and the Tracker stay on the call page or go to Notion); keys and tokens in the Keychain:
`settings.pane.keys`, `keys.claude.hint`, `int.hubspot.keyFooter`, `int.keysNote`, `ntauth.hint`, `google.intro`,
`onb.tools.foot`, `rules/crm.yaml:3`, `rules/google.yaml:2`; nothing is mailed or posted for the person:
`after.neverSends`, `onb.calls.consent.on`, `feat.followup.what`, `crm.row.email`, `sheet.ui.export.nothingSent`,
`page.fu.reviewHelp`, `orgsetup.invite.sub`, `tips.js` `draftTo` and `neverSends`; recording status lines:
`menubar.status.audioFailing`, `tips.noAudio.title`, `sys.notRecording` and their `tips.js` twins; unrelated wording:
`pm.left`, `local.fallback.help`, `economy.what.jobs`, `sheet.ui.s4.reserve`, `rec.set.folder.defaultAsk.sub`,
`crm.log.unreadable`, `set.activeOfferedInStart`, `again.workingHint`, `modules.tracker.sub`,
`account.methods.disconnect.m`, `org.join.defaultSub` (personal space, D36), `rules/voices.yaml:47` (the voice
library); `tw.endInfo` (the transcript and tips stay in the project folder; text eviction is
phase 4 with decision 7 only, and then this line changes with it); `team.private.page` (a private call stays
private, 3.2); rules and skill lines about Notion, HubSpot and the local model (`rules/ai.yaml:61`,
`rules/usage.yaml:13`, `rules/analytics.yaml:61`, `rules/notion-tasks.yaml:67`, `rules/hubspot-tasks.yaml:4`, `:52`
and `:96`, `rules/commitments.yaml:102`, `rules/whatsnew.yaml:7`, `skill/SKILL.md:104` and `:359`). `rules/generated/`
follows its sources and is not edited by hand.

Not user facing, updated with the code of the phase (no promise to users): `recorder/README.md:98` and `:175`
("Everything stays on this Mac", about the recorder's network use), and the code comments `DriveRecordings.swift:4`,
`Projects.swift:30` and `CallSyncCore.swift:349` (PB-37, decision 18). The existing checks that pin related strings
stay valid: `EvidenceChecks.swift:347` (`mem.consent.local`) and `main.swift:2716` (the Verbatim block); a check that
pins a changed key is updated with it.

**Acceptance grep (each phase release, closes the class).** Before the release of phases 1, 2 and 3, run over
`PRIVACY*.md`, `TERMS*.md`, `README*.md`, `docs/`, `app/Resources/i18n`, `app/Resources/tips`,
`app/Resources/InfoPlist`, `rules/` and `skill/`:

```
grep -rniE "(stay(s|ed)?|kept|saved|lives?) (on|in) (this|your|the) Mac|stay(s)? in your|never (leave|leaves|go|goes|sent)|(leave|leaves) (this|the) Mac|not sent|not touched|nothing (in it )?(goes|leaves)|only the link|transcripts?.{0,80}server|\*\*never\*\*"
grep -rniE "лиша(є|ю)ться (на|в|у) (цьому|вашому|ваших|папці|папках|Mac|Мак)|лиша(є|ю)ться\.|лишилось на|залиша(є|ють)|не покида|не надсила|не йде|не виходить|не зачіпа|лише посилання|\*\*ніколи\*\*"
```

The step has two parts:

1. **Every key and line in the tables of this section is changed** in the release of its phase. This part carries the
   label rows the grep does not catch (the Drive labels, `account.export.sub`, `plans.local`, `account.syncLater`).
2. **Every grep hit is triaged**: changed in that release, or named in the stay-true list with its reason; a hit in
   neither fails the release. The pull request lists the hits and their status.

Exempt as whole files: `rules/whatsnew.yaml` (past release notes) and `rules/generated/` (follows its sources). UK
files follow their EN line (the same change). For the app text (i18n, tips window, InfoPlist) the tables above are the
full list as of 25.11. For PRIVACY, TERMS, the READMEs and `docs/team` the hits of 25.11 are in the tables or stay true
(`PRIVACY.md:18`, the local usage log). `docs/en` and `docs/uk` (32 and 51 hit lines on 25.11)
are not fully triaged here: the docs table above is the baseline and the phase 1 pull request triages the rest.

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
- **Bad release**: the server keeps the deploy rollback by tag (ADR 4.2); object writes are new revisions and never
  overwrite an object, and a replaced revision stays 30 days (2.3), so a release found bad within that time can go
  back to the previous revisions.

## 12. What this overrides and what stays valid

| Decision or document | Status after approval | Note |
|---|---|---|
| D27 (transcripts stay on the Mac; video only on Drive; server gets summaries, decisions, tasks, metadata, sheet) | **overridden** | transcripts, recordings and call files go to the server; Drive optional |
| ADR 14.11 section 0.1 and section 2 table | **overridden** | replaced by section 2.1 here |
| ADR section 9 "the Mac stays the source of truth for its calls" | **overridden** | the server is the copy of record; the Mac is a cache |
| ADR section 3 (Drive upload, `drive.file`, folder move) | amended | stays as an optional export; the server never gets Drive tokens |
| ADR section 11 (per-user DEK, crypto-delete) | still valid, extended | object keys wrapped by the DEK |
| ADR section 13 (no AI on the server) | still valid unless decision 19 picks (b) or (c) | no server AI in this spec's phases; a server-built graph (5.1, option B) would change it |
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
| ADR 12.2 and PRIVACY ("Delete my server data" leaves the Mac and Drive copies) | amended | section 4.5: offer to download first; a deleted call's files are removed within 30 days (3.1) |
| D44 (auto-delete of inactive accounts with data after 365 days) | still valid, wider effect | now deletes files too; the emails say so |
| PB-102 (Verbatim block stays on the Mac) | still valid | whole blocks removed from uploaded sheet files, `kind: verbatim` tip lines removed, a fail-closed content check on the `.work` archive; those files are never evicted (2.4) |
| D57 (GraphRAG fully local, nothing new leaves the Mac) | still valid for the store with decision 19(a); overridden by 19(b), amended for org projects by 19(c) | G2 adds a location flag (`local` or `server`) on the source row, not a new state (4.4); its optional snapshot is decision 8; quality loop in 5.1 |
| PRIVACY and TERMS | to change | section 10 |

## 13. Risks

| Risk | Mitigation |
|---|---|
| Only one person administers a server that now holds full transcripts and recordings | phase 0 prerequisites, restore tests that include objects, alerts (D53) |
| Master key loss now loses everything not on a Mac | two offline copies, eviction only from phase 3 |
| A lead sees more than people expect | organization policy (decision 4), never for private calls or the personal organization, a visible "Visible to your team lead" line on the call page, Who viewed my calls |
| Storage cost grows with video | retention rules (SX13), sound-only by default for sales calls, cost shown in the console |
| Eviction breaks preparation or the evidence store | pinning (4.1), the G2 location flag (4.4) before text eviction, text eviction off by default |
| Extraction or tip quality drops when content moves to the server or the store changes, and nobody sees it | the regression gate of 5.1.2 (same `extractor_id` and G0 gold hash, a G0 variant per location or store), CQ scores split by model, route and `store` (5.1.2), GraphRAG G4 held until decision 19 |
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
   project. Recommendation: (a) now, (b) with G7. With (a), goal SG1 does not cover project folder files: a lost Mac
   loses them unless the folder is in Google Drive or iCloud or backed up otherwise.
7. **What the Mac evicts.** Options: (a) recordings only, text never by default; (b) recordings and text older than
   90 days not pinned; (c) nothing evicted automatically, only by the person. Recommendation: (a); text is about 50 MB
   per heavy user per month and keeps preparation and offline full.
8. **Encrypted weekly snapshot of the local evidence store on the server.** Options: (a) yes, owner only; (b) no,
   rebuild on a new Mac (re-extraction cost). Recommendation: (a) in phase 4 (it is the optional snapshot of option A
   in 5.1; with decision 19(b) it is not needed). The store keeps sheet files without
   their Verbatim copies (`spikes/m0/livetips_m0/evidence/ingest.py:54`); whether any other source it ingests can
   carry Verbatim text is not checked yet, so phase 4 runs the content check of 2.4 on the decoded rows of the snapshot (not on the sqlite bytes) before
   upload.
9. **Voice prints.** Options: (a) stay on the Mac only: `.voices.json` and the voice library are not uploaded, and
   `.speakers.json` goes by its field allowlist without `centroid`, `embs` and `lib_added` (2.4); (b) on the server
   like other files. Recommendation: (a), biometric data; a new Mac re-learns voices.
10. **Team search over transcripts.** Options: (a) none in phases 1 to 4; (b) blind keyword index; (c) Mac-computed
    vectors in pgvector. Recommendation: (a), then (b) when a team asks. A server-built team graph is decision 19(c),
    not an option here.
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
    or a legal text the person already has. (a) removes the whole block (copy, pasted text, file path, sections),
    like call sync, so the CHANGELOG line "The block stays on this Mac" stays literally true. Consequence of (a)
    (2.4): stripped files are never evicted, and a new or lost Mac gets sheets without the Verbatim blocks; the
    person adds them again (a file source fills on Refresh, pasted text is lost with the Mac). A middle way, upload
    the block's settings (title, file path, sections) without its text so a file block refills by itself, would
    change that CHANGELOG and docs line to "its text stays on this Mac"; not recommended for the small gain. The
    block's source file is a separate file: the source can be any path (`verbatim.py:52`), and if the person picks a
    file that is itself uploaded (for example a Google copy in `live/google/`, `google_copy`), that file goes to the
    server whole as its own kind, owner only. The block stays off the server either way.
16. **Storage quotas per plan.** Options: (a) count text only against today's `storage_mb` and give recordings a
    separate quota in hours (for example 20 hours stored on Solo, 100 hours per seat on Team, kept by the retention
    rule); (b) raise `storage_mb` to cover video (for example 25 GB per seat on Team); (c) keep today's numbers.
    Recommendation: (a): hours are what people understand, and it keeps Solo free of large costs. Numbers are
    examples for the owner to set.
17. **Participants' email addresses inside uploaded files.** Options: (a) upload files as they are and change the
    PRIVACY line; (b) remove email addresses from transcripts, briefs and sheets on upload. Recommendation: (a): the
    files are encrypted and seen only by the owner and, by policy, the lead; (b) would make a restored file differ
    from the original.
18. **Researched web pages on the server.** Docs promise that researched pages, their titles, addresses and quotes
    stay on the Mac (`docs/en/live-tips.md:2612-2613`), and the pages may hold a token in a link. Options: (a) upload
    `.work/web/<host>`, the full `.sheet.md` and the brief as owner-only objects (encrypted; leads keep seeing only
    the Postgres sheet copy without researched pages, and the brief only if it carries no researched-page text), and
    change the docs line; (b) keep the promise: do not upload `web_pages` (`.work/web` is never in the `.work`
    archive either way, 2.4) and strip researched-page text
    from uploaded sheet and brief files, the brief copies `.brief.orig.md` and `.brief.preverify.md` in the `.work`
    archive included, like the Verbatim text (2.4), so those files are never evicted and a new Mac
    gets them without it. Recommendation: (a): the pages are public text, the person is the only reader, and (b) adds a
    second strip function with the same restore loss as decision 15; links are uploaded inside the encrypted archive
    only, never into Postgres or logs.
19. **Where the evidence graph is built, and how quality is kept, when the server is the source of truth** (owner
    addendum 26.11, section 5.1). Options: (a) graph built on the Mac over server-synced data (the store stays local
    and derived, optional snapshot by decision 8; ADR 13 and D57 stay; needs an extract-from-server step for calls
    this Mac did not record, 5.1.2); (b) graph built on the server (needs a model
    on the server and its choice, an ADR 13 change, a consent and who-pays decision, a PRIVACY line; the live path
    still needs a local snapshot); (c) hybrid: (a) for preparation and live, a server graph for team search over
    org projects later with G7. Recommendation: (a) now, (c) when G7 is specified. Either way: GraphRAG G2 and G3
    continue, G5 after G3, **GraphRAG G4 (live lookup) is held** until this decision is taken; the G0 gold set, CQ1
    to CQ5 and the regression gate of 5.1.2 (same extraction code and gold hash; for (b) or (c) also a store-agnostic
    interface and a G0 variant per store) are the quality check for any of the three. With (b) or (c), the prompt,
    schema, evidence model, source selection and revoke rules, gold set, tip scoring and CQ metrics stay valid. With
    (b) the SQLite store class, the search SQL and GraphRAG G4 would be redone; with (c) the Mac store stays and a
    server store is added for org projects.
