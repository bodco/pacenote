# PB-52: Evidence store with LLM extraction (GraphRAG G2)

Status: spec, step 1 of PB-52. No product code in this step. Everything below is behind a feature flag that is off by
default until the owner turns it on.

Sources read for this spec: the GraphRAG plan `research/pacenote-evidence-graph-plan-2026-11-21.md` (sections 7, 8,
10 and the owner's decision D57), the private roadmap block G, `docs/en/bench/g0-baseline.md` and the bench
`spikes/m0/bench/g0/` (`run.py`, `gold.yaml`, fixtures), and the code on this branch (main `384456e`, PB-51 and PB-70
merged): `livetips_m0/identity.py`, `commitments.py`, `client_scope.py`, `sheet/sources.py` (`auto_sources`,
`expand_folders`), `sheet/lookup.py` (`FolderIndex`), `sheet/readers.py`, `sheet/web.py`, `sheet/size.py`,
`sheet/build.py`, `engine.py` (`_project_snippets`), `memory.py`, `recorder/src/semantic.py`, `embed_server.py`,
`routing.py`, `providers.py`, `cost.py`, `prepcontext.py`, `localprep.py`, `rules/ai.yaml`, `rules/commitments.yaml`,
`tools/gen_skill_section.py`, and in Swift `Projects.swift`, `SettingsView.swift` (`AppPaths.support`),
`MeetingSheetDraft.swift` (web page storage).

Owner decisions this spec implements (D57, 21.11 and 23.11):

- Full GraphRAG scope (variant V3 of the plan): LLM extraction of entities and relations from all sources, topic
  summaries (communities) and global questions are part of G2, not a later step.
- Everything local: SQLite in Application Support keyed by project identity, never inside the Drive-synced project
  folder. No graph database, no cloud embeddings, no Microsoft GraphRAG pipeline.
- Extraction runs on the PM's chosen AI route (every route, not only Claude) with a local model as fallback,
  incrementally, in the background, never during a call, with a cost ceiling per pass, the cost shown before and after,
  and an off switch.
- Every edge carries a quote and a locator; ambiguous edges stay suggestions; modality (proposal, expectation,
  confirmation); PM edits always win; generated summaries never count as independent confirmation.
- Access and time control before ranking: an excluded source, a revoked document, another client (PB-70) and the
  future in a historical replay never appear.
- Nothing new leaves the Mac. During a live call: zero new network or model calls on the normal tip path (G4 decides
  how the live path uses the store).

## 1. Problem

Pacenote picks context by recency and by series, not by what the call is about:

- `auto_sources` takes the last 3 earlier calls of the same series (`sheet/sources.py`: `previous_calls(live,
  title, 8)` filtered to calls before this one and not of another client, then `[:3]`),
  the Tracker, project state and folder files from the last 7 days (`FOLDER_DAYS=7`) up to the per-pass budget.
- The live lookup (`engine._project_snippets` -> `lookup.snippets` over the in-memory BM25 `FolderIndex`) finds
  words, not things. A question in other words or in the other language finds the right file only by luck.
- G1 (PB-51) gave commitments a stable id across calls, but decisions and risks still have none, and nothing links a
  task to the document, the system or the person it is about.

The G0 baseline names the misses (variant A after PB-70 and G1, `results/A-2026-11-23-g1-pb70.json`): prep coverage
80% (item 13 from the other series, a change freeze from the 4th call back, payment terms in a file older than 7
days), answer text in the top 3 for 80% of live questions, 5 duplicate register items, 4 wrong identity outcomes.

## 2. Goals

1. A local evidence store per project: sources and their revisions, snippets with locators, entities with stable
   ids (G1 ids where they exist), claims with modality, edges between entities, aliases, topic summaries, and the
   record of every extraction run with its cost.
2. Ingest of every source type the app already reads (files, transcripts, notes, the commitments register, the
   Tracker, project state, sheets, stored site pages), incremental by content hash.
3. Deterministic links first (register, Tracker, G1 identity, people from the voice library): free and exact.
4. LLM extraction of entities, claims and relations from all text sources on the PM's AI route with a local
   fallback, in the background, never during a call, under a per-pass cost ceiling, with cost shown before and after
   and an off switch.
5. Topic summaries (communities of entities) and global questions ("what about topic X over all time").
6. One search API with client scope, time window (`as_of`), exclusions and a budget, applied before ranking, for G3
   (preparation), G4 (live) and G5 (after the call).
7. A G0 variant C and harder gold that can show whether the store helps, and B (wider text search without a graph)
   so that "the graph helped" is separated from "more context helped".

## 3. Non-goals

- No change to what preparation, the live call or the after-call step use. G3, G4 and G5 wire the store in. G2 ships
  the store, the extraction, the search API, CLI commands and a settings row, all behind the flag.
- No graph database, no Microsoft GraphRAG, no LightRAG, no cloud embeddings, no new service or endpoint.
- No storage inside the project folder (not in `live/`, not in `.work/`, not next to `.livetips-index/`).
- No team or server level (that is G7, over D40 shared registers).
- No change to the commitments register format. The register stays the source of truth for tasks; the store reads
  it.
- No new UI design. Section 10 gives the text of the settings row and the cost prompts; Design shapes it later.
- No reading of the owner's real calls in tests or benchmarks. G0 stays synthetic (D57).

## 4. Where the store lives and project identity

There is no project id in the Python sidecar today (`project` is a free slug string) and the Swift `Project` has a
`slug` (a clash gets a `-2` suffix, `Projects.swift`) and a `root` (changes when the folder moves). Neither is safe as
a key. G2 adds one:

- **`evidence_uid`**: a random UUID minted once per project in `projects.json` (Application Support, never synced),
  kept when the project is renamed or its folder is changed through Settings. The app passes it to the sidecar as
  `LT_PROJECT_UID` together with the root, next to `LT_AI_STATE`.
- **File**: `<AppPaths.support>/evidence/<evidence_uid>.sqlite` (`~/Library/Application Support/LiveTips/evidence/`,
  overridable through `LT_SUPPORT_DIR` like the rest of the app data), plus SQLite's `-wal` and `-shm` files and a
  `<evidence_uid>.lock` writer lock.
- **Inside the file**: `meta` holds the uid, the last known root and slug, the schema version and the rules version.
  A root that differs from the last known one is a move (section 13), not a new project.
- **CLI and tests without the app**: `--project-uid` and `--store` arguments; with neither, the CLI refuses to run
  (it never derives a key from the path on its own, so two folders never share one store by accident).
- The existing semantic index `.livetips-index/` sits inside the project root (Drive-synced). G2 neither reads nor
  writes it; vectors G2 needs are stored in the SQLite file. This repeats bge-m3 vectors for the same files on
  purpose: `.livetips-index/` uses its own chunker, so its vectors do not line up with the store's snippets and
  locators, and it sits in the Drive-synced folder, where the store must never write. G4 decides whether
  `semantic.py` later reads from the store and `.livetips-index/` goes away.

## 5. Sources, revisions and change detection

### 5.1 Source types

| Kind | Where (today) | Read by | Origin | Locator | LLM extraction |
|---|---|---|---|---|---|
| `file` | project folder files (the `folder_files` rules: skip `live`, `_notion`, `meeting-sheet`, hidden, generated, sidecars, older `-vN`) | `sheet/readers.py` (text, md, html, pdf with page marks, docx, pptx, xlsx, doc/rtf/odt/pages) | primary | rel path + char range + page (pdf) or sheet/slide | yes |
| `transcript` | `live/<YYYY-MM-DD>-<slug>/<stem>.md` (and the flat layout) | text | primary | rel path + line range + `[HH:MM:SS]` timestamp of the first line | yes, after the call ends |
| `notes` | `<stem>.notes.md`, `.app.notes.md` | text | derived (model-written), for the whole file: the app has no per-line signal of a PM edit, and PM task edits reach `pm` through the register (section 7.3) | rel path + heading + line range | yes, marked derived |
| `brief`, `sheet` | `.brief.md`, `.sheet.md`, `.sheet.json` | text / JSON | derived | rel path + block id or line range | no (deterministic facts only) |
| `link_sidecar` | `<stem>.notion.json` next to a call (`notion_links.py`: `{"decisions": {key: {"id", "url"}}, "risks": {...}, "tasks": {...}}`, the key is `notion_links.norm(text)`) | JSON | pm / deterministic (a key file, not a claim source) | rel path + kind + key | no; it only adds `notion:<page id>` aliases to the `d-`, `r-` and task entities of that call (section 7.1) and has no snippets |
| `register` | `live/commitments.jsonl` | `commitments.fold()` | pm / deterministic | rel path + event line number + event id | no |
| `tracker` | the Notion and HubSpot tasks the app already holds in Swift (`CRMTasks.swift`, `OrgRegisters.swift`), exported by the app to a snapshot file (section 5.2, item 5) | snapshot file | primary (system of record) | `notion:<page id>` or `crm:<portal>:<task>` + field | no |
| `project_state` | `pacenote/decisions.md`, `_notion/... Current State.md`, `pacenote/answer-cards.md` | text | primary (decisions) / derived (Current State is written by a skill) | rel path + heading + line range | yes |
| `web_page` | `<live>/<date>-<slug>/.work/web/<host>/*.txt` and, for a sheet without a call folder, `<SheetCLI.stateDir>/web/<host>/*.txt` (G6, `MeetingSheetDraft.swift:279`; title line, URL line, body); the app passes the second location's folders of this project's sheets with each pass (S5) | text | primary (external) | URL + stored path + char range | yes |
| `gdoc` / `notion` copies | `live/google/google-file-<id16>.<ext>`, `_notion/` mirror files | as `file` | primary | as `file` + original URL | yes |

**The `_notion/` mirror.** The `file` rules skip `_notion/` only so it is not read twice: the whole mirror is ingested
once, through the `notion` copies row (and the Current State page through `project_state`), as today's live lookup
includes `_notion/`. It is extracted and sent to the `evidence` route like any file, so the first-index confirmation
(section 10) counts its sources and characters.

The column "origin" is what the confirmation rule (section 7.4) counts on. Git commits and the 14-day digest stay
prep-only sources and are not ingested. `sheet/tracker.py` `collect()` is not a source of its own: it folds the same
local commitments registers (`CM.load`) that the `register` kind already reads, so ingesting it would duplicate
every task.

**Hidden calls.** A call the PM hid on Home (`CallHistory.Record.hidden`, `HomeData.swift:274`, kept in
`history.json` in Application Support) has its files never extracted and never searched, like `rules/decisions.yaml` says
for decisions ("Never for a hidden call"). Its transcript, notes, brief, sheet and `<stem>.notion.json` sidecar get
`source.state = excluded` with
`state_reason = "hidden call"`. The worker cannot read `history.json` itself, so the app passes the list of hidden
call files with each pass (S5). Hiding or unhiding a call starts an **immediate state-only pass** (`evidence
exclude --hidden-calls ...`): it only sets `source.state` and marks the topics that cite those sources `stale`
(section 8.7), makes no model, network or embed request and takes well under a second, so the call stops being
searchable at once. It is a local SQLite write only, so it runs at once **also during a call** (the live path reads
the file `mode=ro` through WAL and is never blocked, section 11.4); it is not the extraction worker that section 8.5
keeps out of calls. When a full pass holds the project lock (outside a call), the change is appended to the
state-request log (section 13.1) and applied by the pass; until then every `search` (the app's, the skill's, any
CLI caller's) reads the log itself and treats the sources of a pending hide or revoke as **denied**, exactly as
after it applies (section 11.2 step 1). So the result is the same before and after the request applies, nothing
quoted from the hidden call is returned in between, and no caller has to remember to pass the files in `exclude`.
Unhiding moves the sources back to `active`; a pending unhide changes nothing until it applies (the safe side).

Only the call's own files are excluded. Register events whose `call` is the hidden call stay `active`: the app's
Tracker keeps showing a hidden call's tasks today (`HomeData.swift:290` filters only the Home list), and the store
matches it. So a task first said in a hidden call stays searchable as a register item (its text, owner, due,
status), while the transcript and notes quotes behind it do not. The G0 hidden-call fact (section 14.2 item 6) is
therefore one that is not in the register.

The docs say a page under `.work/web` is "not searched"; with the flag on, G2 changes that for the store (the pages
stay out of the folder lookup and the call files list). S5 updates the docs EN and UK.

### 5.2 Change detection

No content hashing exists in the app today (`file_day` uses the date in the name or mtime). G2 adds it:

1. Pre-check per path: `(mtime, size)` against the active revision. Equal: nothing to do.
2. Otherwise read and normalise the text (the same reader the prep uses, line endings normalised, trailing spaces
   dropped) and take `sha256` of it. Equal to the hash of the **active** revision: update `mtime`/`size` only, no
   new revision.
3. Different hash: a new revision is built in one transaction (snippets, deterministic entities and claims), marked
   `extract_state = pending`; the previous revision gets `superseded_at` and `valid_to`. Readers keep seeing the
   previous complete revision until the transaction commits. **A revert** (content A, then B, then A again) is a new
   revision row with its own `valid_from`, even though an older row has the same hash, so each period keeps its own
   validity and a replay in either A period or the B period sees the right text. In the common case nothing is paid twice:
   the new snippets are rebuilt deterministically, a snippet whose text equals a snippet of an earlier revision of
   the same source copies its vector (no embed request), and every segment whose cache row survives (section 13.4;
   it does while the earlier revision is kept, which is the common case) hits the segment cache (section 8.2), which writes
   the evidence rows onto the new snippets at no model cost (`run_item.status = cached`, then `extract_state =
   done`). Without those rows every claim of the A text would count as history (section 11.2 step 2).
4. The register is append-only: the store keeps the byte offset of the last read line and the `sha256` of the
   bytes before it, and reads only new lines. The file sits in the Drive-synced folder, so it can be replaced under
   the store: when the file is smaller than the offset, or the hash of its first `offset` bytes differs, the store
   does a full refold. An `op migrate` or `op rollback` line (G1) also triggers a full refold.
5. The Tracker: the worker makes no Notion or HubSpot request (section 12). The app writes what it already fetched
   for its own Tracker view to `tracker-snapshot/<evidence_uid>.json` in the state folder next to `ai-routes.json`
   (`LT_AI_STATE` is the path of that file, `routing.default_state_path()`) (items with id, text, status,
   owner, due, `last_edited`, project), at most once per pass trigger (S5). The worker hashes the normalised item
   list and diffs item by item. No snapshot file yet, or an older one, keeps the last ingested state with its
   `observed_at`; a missing file is never treated as "all items deleted". Tracker ids already present on register
   items (`identity.tracker_id`: `notion:` and `crm:` links) are linked from the register alone, without the
   snapshot.
6. A file that disappears gets `state = deleted`, unless it moved. **Move detection**: at the end of the path walk
   of a pass, a vanished path and a new path match when their normalised text has the same `sha256` and the match
   is one to one (exactly one vanished active source and exactly one new path with that hash; two identical files
   never "move" into each other). A match keeps the source row (`id`, revisions, snippets, evidence, overrides) and
   only updates `uri` and `call_key`, so it costs nothing and nothing disappears from search even for one pass. This
   covers `call_layout.migrate` (flat call files moved into call folders with `os.rename`, `call_layout.py:167`;
   names stay the same, `call_layout.py:2`) and a PM who reorganises the Drive folder. A move combined with an edit,
   or an ambiguous match, falls back to deleted plus new: the facts survive anyway because a deleted source is an
   **absent** source, not a denied one (section 11.2 step 1): its rows are ignored, a claim it shares with another
   source stays and is dated by the remaining rows, and only the facts it alone stated disappear. Order inside one
   transaction: the walk first collects the vanished paths and hashes the new ones, then matches, then ingests the
   unmatched new paths and marks the unmatched vanished sources `deleted`; nothing is marked `deleted` before the
   match, so a moved file never drops out of search between two steps. **Revoked**: the worker makes no Google or Notion request, and the
   picked copy `live/google/google-file-<id16>.*` stays on disk after access is lost, so the signal comes from the
   app. The prep already knows each Google source's `google.status: ok | denied | not_found` (`sheet/sources.py`,
   the `access` field), and the app knows the Notion pages it can no longer read. The app passes the list of
   `denied` and `not_found` documents (Google file id, Notion page id) with each pass, the same way it passes hidden
   calls (S5), and a document's change to `denied` or `not_found` starts the same immediate state-only pass as a
   hide (section 5.1). The worker maps each id to its sources (the picked copy by `<id16>`, the `_notion/` mirror file
   by page id) and sets `state = revoked`; a later `ok` sets it back to `active`. Deleted and revoked sources stay in
   the store for history and audit but never reach search (section 11.2). The two differ for a claim or edge that
   other sources also state: a **revoked** row is denied (strict: the claim is dropped, the same as a hidden call or
   another client), a **deleted** row is ignored (the claim passes on its other rows). Claims that rest only on a
   deleted source are hidden with it.
7. Transcripts of the call in progress are never read: a transcript whose call is active (section 8.5), or that was
   modified in the last 10 minutes, waits for the next pass.

8. Hidden calls: a source of a call in the hidden list the app passed is set to `excluded` ("hidden call") before
   any extraction of it (section 5.1).

Snippet size: the rules of the `FolderIndex` chunker (`lookup.chunks`, paragraphs merged up to 900 characters, the
last heading kept, paragraphs over 1800 characters split), in a new variant `lookup.chunks_at` owned by S1 that
differs in these ways:

- it returns the character and line range of every chunk in the normalised text (`lookup.chunks` returns only the
  text, so it cannot give locators);
- it returns the full heading path of every chunk (all ancestor headings, not only the last one): `filter_text`
  drops every nested subheading under a client heading (`skip_level`), so a snippet under "Globex > Timeline" takes
  its marks from the ancestor "Globex" too (section 5.3);
- it finds headings exactly as `filter_text` does: `client_scope.HEADING` on every stripped line, not only on the
  first line of a paragraph (`lookup.chunks` checks `startswith("#")` on a paragraph's first line, so a heading with
  no blank line above it would not close the chunk there);
- it never cuts inside a line. The 1800-character split cuts at a line end; one line longer than that stays one
  snippet (rare; a segment is whole snippets, so such a segment may pass 6000 characters). `lookup.chunks` cuts a
  long paragraph with no newline at a fixed width, which would put the tail of a Globex line, without the word
  "Globex", into a snippet of its own: that is a leak, not coverage loss;
- **the chunk invariant**: every snippet is a run of consecutive non-blank lines with one heading path and one
  **line mark set** (the folder slugs of `meta.label_universe` that the whole line carries, by
  `client_scope._names`, section 5.3). A line whose mark set differs from the previous non-blank line's starts a new
  snippet; the 900 and 1800 size rules only split a run further (at line ends), they
  never merge two runs. A heading line belongs to its own section (its marks are in the path, its line mark set
  counts as empty), so headings do not fragment the text under them. Blank lines are not part of the comparison.

Why the invariant gives line-exact parity with `filter_text` for every scope: `filter_text` keeps or drops a
non-blank line from two inputs only, the names of its ancestor headings and the names of the line itself, and it
consults only `scope.client` and `scope.others`. Every label `decide()` can return is a folder slug from
`_client_dirs` (call-name words are only hints that pick among folders), so every name it consults is in the label
universe. Two lines with the same heading path and the same line mark set therefore get the same keep or drop for
every scope; a snippet holds only such lines, and the per-snippet rule of section 5.3 step 3 equals the per-line
rule. The cuts do not depend on `labels(root)` or on any call, so a client that only becomes "other" at query time
(late client) is already cut out. A cut on `labels(root)` at client changes only fails this: in a
shared file with L1 (no client), L2 (Globex), L3 (no client), it gave `[L1] [L2 L3]`, and the Acme scope lost L3.

Cost of the invariant: lines that name any folder of the universe (also topic folders such as `design`) become their
own snippets, so a shared file can have more, smaller snippets. This is a quality cost (less context per snippet),
never an access one, and it adds no segments (section 8.2). S1 reports the snippet count of `chunks_at` against
`lookup.chunks` and the share of lines in marked runs on the G0 fixture.

**Universe change splits snippets.** Only an **added** slug (a folder added or renamed) needs work: a removed slug is
in no scope any more, and a cut that is finer than needed stays exact. The store keeps no full revision text, so
the local relabel works on `snippet.text` alone, which also covers superseded revisions kept for `as_of` replay: for
every snippet of every kept revision it computes each line's marks with `client_scope._names` against the new
universe (not through `snippet_fts`: an added two-word folder `acme-corp` is the bigram `acmecorp`, which the FTS
tokens `acme` and `corp` never match), and splits a snippet where the invariant now asks for a cut, in one
transaction per revision: new snippet rows with `ord` renumbered, their `snippet_mark` rows, and every evidence row
re-anchored by its offset inside the old snippet to the new piece that holds the whole quote. A quote that crosses a
new cut is dropped, and a claim or edge left with no evidence row has its segment queued for extraction at the next
pass (under the ceiling, shown in the estimate). Claims, edges and `pm_override` rows keep their natural keys. A new
piece has `vec` NULL until the next pass embeds it (a text-equal snippet copies its vector at once, section 7.6);
until then it ranks by FTS only. The relabel makes no model, network or embed request. The segment cache misses
only where a segment's snippet texts changed (`seg_hash` covers them).

Variant A keeps `lookup.chunks` unchanged. B and C use `chunks_at`; the chunk boundaries therefore differ slightly
from A, which the G0 report states next to the A versus B numbers (B exists to show the effect of more context, so
A versus B includes the chunker change, and B versus C isolates the graph). Extraction uses bigger segments
(section 8.2) made of whole snippets, so every quote maps back to snippets.

### 5.3 Client marks at ingest, client decision at query time

**Rule: no access decision reads a client label computed at ingest.** The client of a call is known only at query
time: `decide()` also counts the call's own companies (`client_scope.py:125`), so a folder can be "another client"
for one call while nothing at ingest could know it. Checked in this step with the real `decide()` on a temporary
project (`docs/acme`, `docs/globex`, a `live/2026-11-04-globex-weekly` call, no sheets): the ingest-time hints
(`_series_words`, `_recorded_companies`) give only `globex`, one client folder, while `decide(root, "Kickoff",
["Acme"])` returns client `acme`, others `["globex"]`, and `side()` of `docs/globex/plan.md` is `other`. A store that
stamped labels from the hints would have called every Globex snippet shared and leaked it into the Acme call. So the
store keeps **marks** (which names a row carries) at ingest and decides **own / other / shared** at query time from the
request's scope, with the same rules as `side`, `side_of_name`, `series_side`, `text_side` and `filter_text`.

- **Label universe.** S1 adds `client_scope.label_universe(root)`: the slug of every folder `_client_dirs` could ever
  return, that is every folder within `MAX_DEPTH` outside `SKIP_DIRS` and the skipped prefixes, **without** the hint
  filter. Every label `decide()` can return for the same tree is in it, whatever companies the call brings, because
  `decide()` only returns folder slugs (`client_scope.py:126-141`); call-name first words are hints that pick among
  folders and are not needed in it. The set is stored in `meta` (`label_universe`, JSON) at the end of every pass,
  with the tree's folder list hash. `chunks_at` cuts on it (section 5.2).
- **Marks.** For each snippet the store writes `snippet_mark` rows (section 6): every name (`client_scope._names`, not
  cut to the universe) of the source path's folder and file-name words (`kind = path`, the word rule of `side`), the
  universe names of each ancestor heading with its level (`kind = heading`), and of each line (`kind = line`, with the
  line number). For a deterministic entity (register and Tracker items) the store writes `entity_mark` rows: the
  series slug (`kind = series`), the first word of its call's file name (`kind = call`) and every name of its text
  (`kind = text`, not cut to the universe), the inputs of `item_side`. Extra marks are harmless: only names in the
  request's `scope.client` and `scope.others` are ever consulted. Path and entity text marks stay uncut because the
  relabel of an added slug (section 5.2) finds work only through snippet text: a call folder or a Tracker item that
  names a client before its folder exists must carry that name already (PB-84 review round 2). Heading titles are
  snippet text, so heading and line marks are redone by the relabel. A register event's snippet carries the
  `item_side` inputs of every `add` event with **its own id** (`kind = rseries`, `rcall`, `rtext`, uncut, `pos` = the
  index of that `add`), never the folded item's: `filter_register` decides by the event's own id and the `add` fields
  only, so a moved item (dropped by `fold()`), an `add` joined by `op same` to another client's item and an item edited
  later to name a client all keep the reference side (PB-84 review round 3). Tracker snippets keep their `item` mark
  to the item's entity.
- **Query-time side of a snippet** (section 11.2 step 1), evaluated in SQL with the scope bound as parameters, in the
  order of `side()` then `filter_text`:
  1. the source path under a folder of `scope.folders[scope.client]`: own; under a folder of `scope.folders[o]` for an
     `o` in `scope.others`: **other** (denied). This is the PB-70 path rule of `sheet/lookup.py:277` and
     `sheet/sources.py:554`, `:865`, applied to `source.uri` directly, so it needs no mark at all;
  2. else the `path` marks: `scope.client` among them: own; an `o` among them: other;
  3. else (a shared file) the text rule of `filter_text`: the snippet is other when any ancestor heading's marks hold
     an `o` and not `scope.client`, or any line's marks hold an `o` and not `scope.client` (a line that names the own
     client passes even if it names another).
  4. a register event: other when, for any `pos`, its `rseries`, then `rcall`, then `rtext` marks give **other** (the
     `item_side` order), as `filter_register` drops every event of an id with an `add` on another client's side; a
     Tracker snippet: other when its item's entity is other.
  A deterministic entity's side is `series_side`, then `side_of_name`, then `text_side` on its `entity_mark` rows,
  except a register task: it is visible when an event with one of its ids (its own or a g1 alias) passes, and its
  name, status and ids come only from `fold()` over the register events the request may see, never from the stored
  entity (folded from every event). A mention of another client joined by `op same` is seen by that client under its
  own id with its own text and status; the task's own id, the mention's id and the mention's later edit or `done`
  never reach the other side. A g1 alias shows only when its id's events pass, a Tracker alias only when the visible
  item carries it (PB-84 review round 4).
- **Stale universe.** When `scope.client` or a label of `scope.others` is not in the stored `label_universe` (a
  folder added or renamed after the last pass), marks cannot answer the text rule for that label. Non-live callers
  (`mode = prep`, `after`, the CLI) first run a local relabel (the snippet split of section 5.2: snippets, marks and
  evidence offsets for the added slugs; no model, network or embed request; S1 measures its time)
  and then search. The live reader (`mode=ro`, section 11.4) cannot write: it
  passes only rows whose source path is under the own client's folders and reports `scope_stale`.
  Coverage loss for that call, never a leak.
- **Labels for segments, topics and display only.** `client_scope.labels(root)` still exists, now labelling every
  client folder it finds, even when there is only one (the "fewer than 2 means no scope" rule stays in `decide()`).
  It no longer shapes the chunks (`chunks_at` cuts on the label universe, section 5.2). It shapes the segments (one
  client per segment, section 8.2) and the topic split (section 8.7), and fills the `client_set` cache columns for
  display. Those are quality choices: a wrong label there costs coverage or a mixed segment, never access, because
  step 1 never reads `client_set` or `entity.client`. When `labels(root)` changes, the caches are recomputed
  deterministically (no model call).

S1 tests the rule against the query-time functions, not against ingest labels (section 17, S1): for every scope
`decide()` can return on the G0 fixture **and** on the case above (a client folder backed only by the call's
company), the set of non-blank line numbers covered by passing snippets equals the set of non-blank line numbers
that `side()` and `filter_text` keep for that scope (both run on the revision's normalised text), and nothing under
another client's folder passes as a snippet, claim, edge, entity, alias or topic sentence. The negative controls
are named in the test: a filter on `client_set` from ingest labels fails the second case, and chunking on
`labels(root)` at client changes only fails the line set equality.

## 6. SQLite schema

One file per project, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `user_version` = schema version. All times are
UTC ISO strings. Two clocks, used for different things:

- **Content time** has two parts, and replay (section 11.2) runs on them only. All of them are full UTC date and
  time values (`2026-11-19T14:05:31Z`), never a day: a day would start every revision of a file edited after a
  call on the call day at midnight, before the call.
  - **`revision.valid_from`: since when this text existed.** Revision 1 of a source:
    - transcript: the call start (its lines are gated one by one by `said_at` below);
    - every other file-backed kind (notes, brief, sheet, project state, file, site page, copies): the later of the
      mtime recorded at ingest and the date in its name taken at 00:00 local time of the Mac at ingest, converted to
      UTC. This is the `file_day` rule of `sheet/sources.py:752` with the time kept (`file_day` returns a day). Call
      notes are written after the call ends, so their revision 1 starts after the call, not at the call start: a
      replay of that call never sees its own notes, and a notes file the PM edited before the first index starts at
      the edit (the safe side, see the known limit in section 11.2 step 2);
    - Tracker item: `last_edited`; register event: its `at`. These two are dated per item on every pass, never by a
      file mtime or `observed_at`; the rule for revision 2 and later below does not apply to them.
    **The call start** is the transcript header line `- Start: YYYY-MM-DD HH:MM:SS` (`- Початок:` in a Ukrainian
    window), which the recorder writes in the Mac's local time without a zone (`transcriber.py:5318` and `:5326`);
    the worker reads it from the transcript, not from `history.json`, and converts it with the Mac's time zone at
    ingest. A replay "as of a call" uses the same value as `as_of`. A transcript without that header (an old or hand-made file) falls back to the date in its folder
    or file name at 00:00 local, and its lines then keep their own clock times on that date.
    Revision 2 and later of a file-backed kind: the mtime recorded at ingest when it is later than the previous revision's
    `valid_from` (and not later than `observed_at`; a future mtime from a skewed clock is cut to `observed_at`);
    otherwise `observed_at`. The fallback covers a file restored from a backup or a sync with an older mtime: the
    change happened at some point up to `observed_at`, so `observed_at` never shows it too early, and `valid_to` of
    the previous revision (= this `valid_from`) never goes backwards or gives it an empty validity.
  - **`snippet.said_at`: when the snippet's content was said or written.** Transcript lines: the line's `[HH:MM:SS]`,
    which is a local clock time (`transcriber.hhmmss`), not an offset, on the call start's date (the next date once
    the clock goes past midnight, seen as a time lower than the line before), converted to UTC like the call start;
    on every revision (a transcript re-saved with renamed speakers does not redate its lines). Every other kind has no per-line time: a snippet whose text equals a snippet of an earlier revision
    of the same source (the same match that copies its vector, section 5.2 item 3) keeps that snippet's `said_at`;
    any other snippet gets its revision's `valid_from`, so a snippet is dated by the revision its text first
    appeared in. A line the PM added to the notes three days after the call is dated at the edit, and an unchanged
    line keeps its earlier date. A notes snippet is therefore dated when the notes were written (minutes after the
    call), not at the moment in the call it describes; the transcript rows of the same claim carry that moment.
  - `claim.said_at` is a cache of the earliest passing evidence row's snippet `said_at` (section 11.2).
- **Observation time** (`revision.observed_at`, `source.first_seen`): when Pacenote first saw it. Audit, "new since
  the last pass", and the one fallback above for revision 2 and later. It is never the content time of a revision
  1 (on a first index every revision would be observed "now", and a replay of an older call would see no files at
  all).

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, rules_version, evidence_uid, root_hint, slug_hint, created_at, flag_state,
-- label_universe (JSON list of slugs, section 5.3), label_tree_hash

CREATE TABLE source (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,          -- file, transcript, notes, brief, sheet, link_sidecar, register, tracker,
                                        -- project_state, web_page
  uri           TEXT NOT NULL UNIQUE,   -- rel path, notion:<id>, crm:<portal>:<task>, https://...
  title         TEXT,
  origin        TEXT NOT NULL,          -- primary, derived, pm
  call_key      TEXT,                   -- commitments.series_key + call file for call sources
  state         TEXT NOT NULL DEFAULT 'active',  -- active, excluded, deleted, revoked
  state_reason  TEXT,                   -- "hidden call", "file deleted", "no longer shared", ".livetipsignore"
  state_at      TEXT,
  first_seen    TEXT NOT NULL,
  last_checked  TEXT
);
CREATE INDEX source_state ON source(state, kind);

CREATE TABLE revision (
  id             INTEGER PRIMARY KEY,
  source_id      INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
  content_hash   TEXT NOT NULL,         -- sha256 of normalised text
  mtime          REAL, size INTEGER, chars INTEGER,
  valid_from     TEXT NOT NULL,         -- UTC date and time since when this text existed (see above); replay filter
  valid_to       TEXT,                  -- valid_from of the next revision; NULL = current content
  observed_at    TEXT NOT NULL,         -- audit only
  superseded_at  TEXT,                  -- observation time of the next revision; NULL = active revision
  extract_state  TEXT NOT NULL DEFAULT 'pending',  -- pending, done, failed, skipped_off, not_needed
  extractor      TEXT                   -- rules version + prompt hash used
);                                      -- no UNIQUE on (source_id, content_hash): a revert is a new row (section 5.2)
CREATE INDEX revision_hash ON revision(source_id, content_hash);
CREATE INDEX revision_active ON revision(source_id, superseded_at);
CREATE INDEX revision_valid ON revision(source_id, valid_from, valid_to);
CREATE INDEX revision_extract ON revision(extract_state);

CREATE TABLE snippet (
  id           INTEGER PRIMARY KEY,
  revision_id  INTEGER NOT NULL REFERENCES revision(id) ON DELETE CASCADE,
  ord          INTEGER NOT NULL,
  text         TEXT NOT NULL,
  locator      TEXT NOT NULL,           -- JSON: {"lines":[a,b],"chars":[a,b],"page":n,"t":"00:12:31","heading":"...","url":"..."}
  heading      TEXT,
  speaker      TEXT,                    -- person entity id for transcript lines when known
  said_at      TEXT,
  client_set   TEXT NOT NULL DEFAULT '',-- cache for chunking, segments, topics, display ('' shared, 'acme',
                                        -- 'acme|globex'); never read by access (section 5.3)
  vec          BLOB,                    -- bge-m3 1024 float16, computed at ingest (section 7.6), NULL until then
  UNIQUE (revision_id, ord)
);
CREATE INDEX snippet_rev ON snippet(revision_id);
CREATE TABLE snippet_mark (             -- the label-universe names a snippet carries; access decides with them at
  snippet_id  INTEGER NOT NULL REFERENCES snippet(id) ON DELETE CASCADE,  -- query time (section 5.3)
  kind        TEXT NOT NULL,            -- path (folder and file-name words), heading (ancestor), line
  pos         INTEGER NOT NULL,         -- 0 for path, heading level, or line number inside the snippet
  name        TEXT NOT NULL,            -- heading, line: a slug from meta.label_universe; path: any path name
  PRIMARY KEY (snippet_id, kind, pos, name)
);
CREATE INDEX snippet_mark_name ON snippet_mark(name, snippet_id);
CREATE VIRTUAL TABLE snippet_fts USING fts5(text, heading, content='snippet', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2');

CREATE TABLE entity (
  id          TEXT PRIMARY KEY,         -- c-<sha1:10> (G1 commitment), d-/r-<sha1(dedupe key):10>, person:<casefold>, me,
                                        -- client:<slug>, e-<sha1(type|norm name):10> (section 7.1)
  type        TEXT NOT NULL,            -- person, client, task, decision, risk, topic, document, system, call, date_event
  name        TEXT NOT NULL,            -- cache of the current name (section 11.2 step 2 derives it under as_of)
  client      TEXT NOT NULL DEFAULT '', -- cache: home client label of a deterministic entity for display and topics;
                                        -- '' for llm entities; never read by access (entity_mark is, section 5.3)
  status      TEXT,                     -- cache of the current status: open, proposal, done, dropped (tasks)
  origin      TEXT NOT NULL,            -- g1, tracker, voices, llm, pm
  merged_into TEXT REFERENCES entity(id),  -- cache of the current merge
  merged_at   TEXT,                     -- `at` of the register event or pm_override that merged it
  pm_locked   INTEGER NOT NULL DEFAULT 0,
  created_run INTEGER REFERENCES run(id),
  updated_at  TEXT NOT NULL
);
CREATE INDEX entity_type ON entity(type, client);
CREATE TABLE entity_mark (              -- the inputs of item_side for a deterministic entity (section 5.3)
  entity_id   TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,            -- series (full series slug), call (first word of the call file name), text
  name        TEXT NOT NULL,
  PRIMARY KEY (entity_id, kind, name)
);

CREATE TABLE alias (
  alias_norm  TEXT NOT NULL,            -- casefolded, whitespace collapsed
  entity_id   TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  lang        TEXT,                     -- en, uk, ''
  origin      TEXT NOT NULL,            -- voices, g1, tracker, notion (notion:<page id> from a link sidecar), llm, pm
  status      TEXT NOT NULL,            -- confirmed, suggested, rejected
  client_set  TEXT NOT NULL DEFAULT '', -- cache: labels of the snippets the alias was found in (display only)
  vec         BLOB,
  PRIMARY KEY (alias_norm, entity_id)
);
CREATE INDEX alias_entity ON alias(entity_id);
CREATE TABLE alias_seen (               -- the snippets an llm alias was found in; access uses these rows
  alias_norm  TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  snippet_id  INTEGER NOT NULL REFERENCES snippet(id) ON DELETE CASCADE,
  PRIMARY KEY (alias_norm, entity_id, snippet_id),
  FOREIGN KEY (alias_norm, entity_id) REFERENCES alias(alias_norm, entity_id) ON DELETE CASCADE
);
CREATE INDEX alias_seen_snippet ON alias_seen(snippet_id);

CREATE TABLE claim (                    -- a statement about one entity
  id          INTEGER PRIMARY KEY,
  subject_id  TEXT NOT NULL REFERENCES entity(id),
  predicate   TEXT NOT NULL,            -- status, due, owner, decided, risk_level, waiting_for, value, said
  value       TEXT,
  modality    TEXT NOT NULL,            -- proposal, expectation, confirmation, statement
  said_by     TEXT REFERENCES entity(id), -- cache: said_by of the earliest evidence row; search returns the one of
                                        -- the earliest row that passes the request (section 11.2 step 1)
  said_at     TEXT NOT NULL,            -- cache: earliest evidence said_at; search derives it from passing rows only
  due         TEXT,
  client_set  TEXT NOT NULL DEFAULT '', -- cache: union of the evidence snippets' labels (access uses the rows)
  status      TEXT NOT NULL,            -- cache of now: accepted, suggestion, rejected, superseded
  origin      TEXT NOT NULL,            -- deterministic, llm, pm
  confidence  REAL,
  run_id      INTEGER REFERENCES run(id),
  nkey        TEXT NOT NULL UNIQUE      -- subject_id|predicate|modality|sha1(normalised value):10, the pm_override
                                        -- key; the same statement found again adds evidence rows to this claim
);
CREATE INDEX claim_subject ON claim(subject_id, status, said_at);

CREATE TABLE edge (                     -- a relation between two entities
  id            INTEGER PRIMARY KEY,
  src_id        TEXT NOT NULL REFERENCES entity(id),
  rel           TEXT NOT NULL,          -- blocks, depends_on, owns, about, part_of, mentions, decided_in, risk_for,
                                        -- waiting_on, attended, supersedes (PM only, section 7.1),
                                        -- same_as (always a suggestion until the PM confirms)
  dst_id        TEXT NOT NULL REFERENCES entity(id),
  modality      TEXT NOT NULL,
  client_set    TEXT NOT NULL DEFAULT '',  -- cache: union of the evidence snippets' labels (access uses the rows)
  status        TEXT NOT NULL,          -- cache of now: accepted, suggestion, rejected, superseded
  origin        TEXT NOT NULL,
  confidence    REAL,
  first_said_at TEXT NOT NULL,          -- cache over all rows; search derives both from passing rows only
  last_said_at  TEXT NOT NULL,
  run_id        INTEGER REFERENCES run(id),
  UNIQUE (src_id, rel, dst_id, modality) -- one row per relation and modality (the key rule below); the same edge
);                                      -- with the same modality found in a shared and a client file adds
                                        -- evidence rows, not a second edge; a proposal and a confirmation of
                                        -- one relation are two rows
CREATE INDEX edge_src ON edge(src_id, status);
CREATE INDEX edge_dst ON edge(dst_id, status);

CREATE TABLE evidence (                 -- every claim and every edge has at least one row
  target      TEXT NOT NULL,            -- 'claim' or 'edge'
  target_id   INTEGER NOT NULL,
  snippet_id  INTEGER NOT NULL REFERENCES snippet(id) ON DELETE CASCADE,
  quote       TEXT NOT NULL,            -- verbatim from the snippet, at most 300 characters
  q_start     INTEGER NOT NULL, q_end INTEGER NOT NULL,  -- offsets inside snippet.text
  said_by     TEXT REFERENCES entity(id),  -- who said this quote (the snippet's speaker or the model's said_by)
  PRIMARY KEY (target, target_id, snippet_id, q_start)
);
CREATE INDEX evidence_snippet ON evidence(snippet_id);

CREATE TABLE pm_override (              -- PM edits; always applied last, never overwritten by extraction
  id         INTEGER PRIMARY KEY,
  target     TEXT NOT NULL,             -- entity, claim, edge, alias
  target_key TEXT NOT NULL,             -- natural key, stable across a rebuild (never an integer row id):
                                        --   entity: its id, or an alias key (notion:<page id>) resolved through
                                        --   `alias` whenever overrides are applied or read; alias: alias_norm|entity_id;
                                        --   claim: claim.nkey;
                                        --   edge: src_id|rel|dst_id|modality
  field      TEXT NOT NULL,             -- status, name, merge, split, reject, confirm, value, supersede
                                        -- (supersede: target notion:<old page id>, value notion:<new page id>,
                                        -- both resolved through `alias`, section 7.1)
  value      TEXT,
  at         TEXT NOT NULL,
  via        TEXT NOT NULL              -- register, app, skill
);
CREATE INDEX pm_override_target ON pm_override(target, target_key);

CREATE TABLE state_request (           -- hide, unhide, revoke and override requests from the log (section 13.1)
  request_id  TEXT PRIMARY KEY,         -- UUID minted by the app (or the CLI caller) when it appends the request
  kind        TEXT NOT NULL,            -- hidden_calls, revoked, override
  payload     TEXT NOT NULL,            -- JSON, the same arguments as the CLI command
  received_at TEXT NOT NULL,
  applied_at  TEXT NOT NULL             -- a row exists only once the request is applied
);

CREATE TABLE topic (
  id           INTEGER PRIMARY KEY,
  label        TEXT NOT NULL,
  client_set   TEXT NOT NULL DEFAULT '',
  member_hash  TEXT NOT NULL,           -- hash of the sorted accepted claim and edge ids it covers
  summary_at   TEXT, summary_run INTEGER REFERENCES run(id),  -- NULL until summarised
  state        TEXT NOT NULL            -- fresh, stale, pending
);
CREATE TABLE topic_member (topic_id INTEGER REFERENCES topic(id) ON DELETE CASCADE, entity_id TEXT, weight REAL,
  PRIMARY KEY (topic_id, entity_id));
CREATE TABLE topic_sentence (           -- the summary, one row per sentence, so access can drop single sentences
  id        INTEGER PRIMARY KEY,
  topic_id  INTEGER NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
  ord       INTEGER NOT NULL,
  text      TEXT NOT NULL,
  UNIQUE (topic_id, ord)
);
CREATE TABLE topic_cite (               -- the claims and edges each sentence cites (at least one per sentence)
  sentence_id INTEGER NOT NULL REFERENCES topic_sentence(id) ON DELETE CASCADE,
  target TEXT NOT NULL, target_id INTEGER NOT NULL,
  PRIMARY KEY (sentence_id, target, target_id));
CREATE INDEX topic_cite_target ON topic_cite(target, target_id);
CREATE VIRTUAL TABLE topic_fts USING fts5(text, content='topic_sentence', content_rowid='id');
-- topic labels are matched through the aliases of the topic's member entities, not through topic_fts

CREATE TABLE run (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL,         -- ingest, extract, summarize, rebuild, relabel
  route          TEXT, model TEXT,      -- routing.ROUTES: api, code, local, openai, codex, gemini
  fallback_from  TEXT,                  -- the route it fell back from, if any
  extractor      TEXT,                  -- rules version + prompt hash of this run
  started_at     TEXT NOT NULL, ended_at TEXT,
  status         TEXT NOT NULL,         -- running, done, ceiling, paused_call, off, failed, cancelled
  segments_plan  INTEGER, segments_done INTEGER,
  in_tokens      INTEGER, cached_tokens INTEGER, out_tokens INTEGER,
  est_usd        REAL, usd REAL,        -- NULL on plan routes (cost.cost_usd returns None for codex/, claude-code/, agy/)
  est_pct        REAL, pct_before REAL, pct_after REAL,  -- percent of the 5-hour window on plan routes
  seconds        REAL,                  -- wall time (the cost on local)
  ceiling_kind   TEXT, ceiling_value REAL,  -- usd, pct, minutes
  error          TEXT
);
CREATE TABLE run_item (
  run_id       INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  revision_id  INTEGER NOT NULL REFERENCES revision(id) ON DELETE CASCADE,
  segment      INTEGER NOT NULL,
  seg_hash     TEXT,                    -- content key of the segment; with run.extractor links to segment_cache
  status       TEXT NOT NULL,           -- done, cached, invalid, failed, skipped, cancelled
  in_tokens INTEGER, out_tokens INTEGER, dropped INTEGER, error TEXT,
  PRIMARY KEY (run_id, revision_id, segment)
);
CREATE TABLE segment_cache (             -- validated extraction output per segment content (section 8.2)
  seg_hash    TEXT NOT NULL,             -- sha256(client label | heading path | segment snippet texts); content only
  extractor   TEXT NOT NULL,             -- rules version + prompt hash that produced the output
  output      TEXT NOT NULL,             -- validated JSON
  run_id      INTEGER REFERENCES run(id),
  in_tokens INTEGER, out_tokens INTEGER,
  last_used   TEXT NOT NULL,
  PRIMARY KEY (seg_hash, extractor)
);
```

Notes on the schema:

- Claims and edges point at snippets, snippets at revisions. A superseded revision keeps its snippets as long as an
  accepted claim or edge cites them (history and replay need them); others are deleted by the vacuum step
  (section 13.4).
- **No claim or edge without evidence.** `snippet -> evidence` cascades on delete, so a purge can leave a claim or
  edge with zero evidence rows. The vacuum step deletes every claim and edge that has no evidence left (in the same
  transaction as the purge, together with every `llm` alias that has no `alias_seen` row left), and the search
  never relies on "every evidence row passes" alone: it also requires at
  least one passing row (section 11.2). A claim whose only evidence came from a revoked source therefore disappears,
  it is never shown without a quote.
- **One key rule for claims and edges.** The natural key of a claim or an edge holds every field that a PM edit,
  a request `exclude`, a revocation or an `as_of` can keep or drop separately, and modality is one of them: claim
  `nkey` = `subject_id|predicate|modality|sha1(normalised value):10`, edge key = `src_id|rel|dst_id|modality`.
  Modality is final when the key is built: the validator (section 8.4 item 9) applies the hedge and
  derived-source guards first, then the item is keyed. So a proposal in one file and a confirmation in another are
  two rows, each with its own evidence, status and overrides; excluding, revoking or rejecting the confirming source
  or row leaves the proposal row as it was, and the relation is never shown as confirmed by evidence that does not
  pass. Two edges have a fixed modality whatever the quote says: `supersedes` (PM only) is `confirmation` and
  `same_as` is `statement`, so a PM rejection of a `same_as` pair has one key and is never re-suggested under
  another modality.
- **Overrides by natural key.** Claim and edge row ids are reassigned by a rebuild; `pm_override.target_key` uses
  the natural keys above, so a PM edit re-applies to the same claim or edge after a rebuild. An override whose key no
  longer matches anything stays stored (orphaned) and applies again if extraction finds the same statement later.
- `entity.merged_into` records a confirmed merge (alias of a G1 `same`, or a PM merge). Readers follow it one step.
- **Columns that hold "now".** `entity.name`, `entity.status`, `entity.merged_into` and `claim.status` / `edge.status`
  are caches of the current state, used only without `as_of`. Under `as_of` they are derived, never read (section
  11.2 step 2), so they need no history columns and a rebuild gets them right for free.
- **Client access is decided at query time on evidence.** Every `client_set` column and `entity.client` are caches
  for segments, display and the topic split, filled from `labels(root)`; access never reads them. It
  decides each evidence snippet's side from the request's scope, `source.uri` and `snippet_mark` (deterministic
  entities: `entity_mark`; llm aliases: `alias_seen`), section 5.3 and section 11.2 step 1, so one
  edge found with the same modality in a shared file and in client A's file is one row that passes for A and is dropped for client B
  (strict denial); an edge quoted only in shared files passes for both (section 7.1).
- There is no table per relation type and no recursive graph engine. A graph walk is 1 or 2 indexed SQL lookups on
  `edge_src` / `edge_dst` (section 11).
- FTS5 availability: the Python `sqlite3` the sidecar runs with must have FTS5. S1 checks it at start; without it
  the store falls back to the `FolderIndex` BM25 code over the `snippet` rows (slower, same results shape) and says so
  in `evidence status`.

## 7. Identity, modality and authority

### 7.1 Entity ids

- **Tasks**: the G1 commitment id (`c-<sha1>`), with every alias id in `ids` and `history` from `commitments.fold()`.
  G1's `same`, `split`, `reject` and `edit` events are applied as they are, the store never decides a task merge on
  its own.
- **Decisions and risks**: G1 has no id for them, but the app already keys them. G2 adds `d-` and `r-` ids that
  **never change once minted**:
  - A decision or risk of a call: always `d-<sha1(call file | notion_links.norm(text)):10>` (`r-` for a risk; "call file" is the file stem, the base
    name without folder, which `call_layout.migrate` keeps, `call_layout.py:2`), the
    dedupe key of `rules/decisions.yaml`, which is exactly the key `notion_links.put(md, "decisions", text, ...)`
    writes into `<stem>.notion.json`. The id does not depend on whether the item was sent to Notion, so the
    after-call pass (which ingests the notes before the PM clicks "Add to Notion") and every later pass give the same
    entity, and its claims (whose `nkey` holds `subject_id`) never split.
  - The Notion page id is an **alias**, like `identity.tracker_id` for tasks: ingesting `<stem>.notion.json` (the
    `link_sidecar` kind, section 5.1) adds `alias_norm = notion:<page id>` with `origin = notion` and `status =
    confirmed` to the entity of the same key. A sidecar key with no matching item in the call's notes (the PM edited
    the text after sending) creates the `d-` entity from the sidecar key alone, named by the key, so the alias always
    has an entity. For `tasks`, the alias goes to the register item of that call whose `norm(text)` equals the key;
    with none it is skipped (tasks already carry `notion:` links through `identity.tracker_id`).
  - A line of `pacenote/decisions.md` (the Notion register snapshot, `decision_notion.parse_snapshot`: date, title,
    stated by, area; no page id and no call file) is keyed `d-<sha1("decisions.md" | date | notion_links.norm(title)):10>`.
    Its title is the Notion title, which the PM can edit, so joining it to a call's `d-` id is a **suggestion only**
    (`same_as`, through the G1 key functions `identity.ref_of` and `scope_match` plus the date), never a merge.
  - Across calls, the G1 key functions (`identity.tracker_id`, `ref_of`, `scope_match`) propose joins as suggestions
    only. New code in `identity.py`, owned by S1.
- **Superseded decisions**: the PM's click (`CallPage.swift:1312`, `PyTool.run("decision_notion", ... "--supersede", old, "--new", new ...)`)
  only updates Notion today; nothing on the Mac records it (the click counts usage, and `pacenote/decisions.md` lists
  active decisions only). The worker makes no Notion request (section 12), so G2 adds a local, dated record: after
  `--supersede` returns ok, the app runs `evidence override --target entity --key notion:<old page id> --field
  supersede --value notion:<new page id> --at <now> --via app` (section 11.6), which writes one `pm_override` row. S5 puts this call inside the completion
  handler of that `PyTool.run`, on `ok` only.
  `--supersede` and `--new` are Notion page ids, so the row keeps them as **alias keys**: `target_key` and `value`
  are resolved through the `notion:` aliases (above) every time overrides are applied and read, never once at write
  time. So the override lands on the call-keyed `d-` entity that holds the claims, whichever order the notes, the
  sidecar and the click arrive in; before `override` applies its row it ingests every changed `*.notion.json`
  sidecar (deterministic, free, the `(mtime, size)` pre-check of section 5.2), and a key that still resolves to
  nothing stays an orphaned override that applies as soon as the sidecar with that page id is ingested (section 6).
  The `status = superseded` of the old decision and the `supersedes` edge (new -> old, `origin = pm`) are both
  derived from that row, with its `at` as their date (section 11.2 step 2); without a resolved new decision the old
  one is still `superseded`, just without the edge. The write is local SQLite only (no model, network or embed
  request), so it runs at once, also during a call, like the state-only pass; with the flag on and no store file yet,
  it creates the store first (free), so a click before the first pass is not lost. Known limits: a supersede clicked
  before the flag was on, or with the flag off, is not recorded; a supersede of a decision never written from
  Pacenote (no `<stem>.notion.json` entry with that page id) stays orphaned unless that page id appears in a sidecar
  later; a decision that disappears from
  `pacenote/decisions.md` is never treated as superseded. A superseded decision is never returned as current; it is
  shown only in timelines and entity cards. Extraction never sets `superseded` on its own.
- **People**: `identity.person_id` (the voice library aliases, `me` for the PM), as `person:<casefold>`.
- **Clients**: `client:<slug>` from the client folders `client_scope.decide` finds.
- **Everything the LLM finds** (topics, systems, documents, people not in the voice library):
  `e-<sha1(type|normalised name)>`, without the client, so "the SSO integration" in a shared file and in a client file
  is one node and a hop from a shared file into that client's file works. Client scope is not on the node but on its
  evidence: every claim, edge and alias of it points at the snippets it was quoted from, whose side is decided for
  the request's scope at query time (section 5.3), and the entity itself is visible to a scope only through them
  (section 11.2 step 1), so a node found only in one client's files
  never shows in another client's call. Access denial is strict (section 11.2 step 1): an edge quoted in a shared
  file and in client A's file passes for A and is dropped for client B; only an edge quoted in shared files alone
  passes for both, so B reaches the shared node through its own or shared-only edges, never through A's. When the same name is used for two different things by two clients (the model resolves it to a known id of
  one client, or the PM splits it), the second gets `e-<sha1(type|normalised name|client)>` and a `same_as`
  suggestion between them is never created across two clients. A new name that matches an existing alias of the same
  type joins it; a near match (same type, alias vector cosine at least 0.85, or edit distance at most 2 on names of 6+
  characters) becomes an alias with `status = suggested`, never a merge.

### 7.2 Modality

The owner's vocabulary is canonical in the store; G1's register values map onto it. The mapping lives in a new
`rules/evidence.yaml` so the app and the skill read one table:

| Store (`modality`) | Meaning | G1 register |
|---|---|---|
| `proposal` | "we will try", "maybe", an offer not accepted | `modality: tentative`, `status: proposal` |
| `expectation` | someone waits for or expects something, a due date asked for, not agreed by the owner of the work | no G1 value; set from "waiting for", "by Friday?", "we expect" |
| `confirmation` | agreed, decided, done, confirmed by the person who owns it | `modality: agreed`; `status: done` gives a `status=done` claim with `confirmation` |
| `statement` | a plain fact (a number, a date, a description) | items with `modality: ""` |

The extraction prompt uses the same table and the hedge words of `identity.TENTATIVE_RX` and `HEDGE_RX` (copied
into the rules file, not re-invented). Those two patterns are anchored at the start of an item's text (`^\W*`),
which fits a register item but not a quote; `rules/evidence.yaml` therefore keeps the same word list twice: anchored
(`hedge_start`, for parity tests with `identity.py`) and unanchored with word boundaries (`hedge_anywhere`, used by
the validator in section 8.4 item 9 to find a hedge word anywhere inside a quote). A `proposal` never becomes `confirmation` without a later quote that confirms
it from a primary source or a PM edit. With modality in the key (section 6) that later quote is a second claim or
edge row next to the proposal, never a change of the proposal row: the proposal keeps its own evidence and date, a
consumer shows both rows of one relation together (the newest `confirmation` first, then the proposal with its
date), and if the confirming row is excluded, revoked, rejected or later than `as_of`, only the proposal is left.

### 7.3 PM edits win

- Every PM action already in the register (`edit`, `same`, `split`, `reject`, `drop`, `done`, `agree`) is ingested
  as a `pm_override` row and applied after every extraction. Extraction never writes over a field with a
  `pm_override` and never re-suggests a pair the PM rejected (the G1 `reject` rule, extended to `same_as` edges and
  aliases).
- `entity.pm_locked = 1` freezes name, type and merges of that entity for extraction.
- **Where a PM edit is written (the one rule):** a PM edit to a task (a register item: its text, status, owner, due,
  a merge or a split) is written as a register event in `live/commitments.jsonl` through the existing G1 commands,
  never only in the store; the store then ingests it like any register event. Every other PM edit (confirm or reject
  a suggested edge, alias, claim or `same_as` of non-task entities, later in G3 or G5 UI) is written as a
  `pm_override` row with `via = app`. Section 16 refers to this rule.

### 7.4 Confirmation counting

A claim is "confirmed" when it has `modality = confirmation` and at least one evidence row from a `primary` or `pm`
source. Evidence from `derived` sources (notes, briefs, sheets, Current State, topic summaries) is kept and shown
but never counts as an independent source: "said in 2 sources" counts distinct primary sources, and a claim whose
only evidence is derived is shown as "from notes" in every consumer. Topic summaries are never evidence for a claim
at all.

### 7.5 Ambiguity

An edge or claim is stored with `status = suggestion` when any of these holds: the model's confidence is under the
threshold in `rules/evidence.yaml` (default 0.7); an entity reference resolved to a suggested alias; the subject is a
`same_as`; two clients are named in the quote; the source is derived and no primary evidence exists. Suggestions are
returned by search only with `include_suggestions=true` and are always labelled.

### 7.6 Vectors

Vectors are computed at ingest with the bundled bge-m3 server (`embed_server.py`, ONNX int8 on CPU, 1024
dimensions, port 11435, loopback only) for snippets and aliases, and stored as float16 BLOBs in the SQLite file
(about 2 KB per snippet). This is not a cloud embedding and opens no new destination. When the server is not
running, ingest stores `vec = NULL` and a later pass fills it. Query vectors are computed only outside the live tip
path (section 11.4).

## 8. Extraction

### 8.1 Order of work in a pass

1. Deterministic ingest of all changed sources (free): register, Tracker snapshot, sheets, people from the voice
   library, client labels, snippets and FTS rows. Always runs when the flag is on, even with extraction switched off.
2. Vectors for new snippets (local, free).
3. LLM extraction of revisions in `extract_state = pending`, in this order: transcripts of the last 14 days, notes,
   project state, files changed in the last 30 days, site pages, then the rest from newest to oldest. A pass stops
   at the ceiling and the next pass continues where it stopped.
4. Topic detection and the summaries of topics whose `member_hash` changed (section 8.7).

### 8.2 Segments

A segment is whole snippets of **one heading section** of one revision, up to 6000 characters; a segment never
crosses a heading boundary and never packs two sections together (a section over 6000 characters becomes several
segments, a short section is a short segment). The previous snippet is sent before the segment for context (not
extracted from twice: only quotes inside the segment's own snippets are accepted). The segment text is sent with
line numbers. A transcript has no headings: it is cut into segments of whole snippets up to 6000 characters from
its start, and a transcript is extracted once, after the call, so its cuts do not need to survive edits. A transcript
segment keeps speaker names and timestamps. **Call notes** (`<stem>.notes.md`, `.app.notes.md`) are cut the same way
as a transcript, across their headings: they have 8 fixed short sections (`callnotes.py:183-185`) and are written
once after the call, so a per-section cut would pay the prefix and catalog 8 times for a cache that never helps.
The snippets keep their heading path. For every kind, a segment also ends where the snippets' `client_set`
changes, so each segment carries one client label (the one in its `seg_hash` and in the prompt's source meta).
A segment does **not** end at the finer snippet cuts of the chunk invariant (section 5.2): neighbouring snippets
with the same `client_set` (for example a line naming the topic folder `design` between shared lines) stay in one
segment, so the invariant adds no segments and the cost model of section 9 stands.

**Extraction cache per segment.** A small edit to a large file (Current State, `decisions.md`) must not re-extract
the whole file. Snippets are stable across an edit in another section (`lookup.chunks` closes a chunk at each
heading, and `chunks_at` keeps that rule with `client_scope.HEADING` on every line), and segments do not cross headings, so an edit in one section leaves the
segments of every other section byte-equal. Each segment has a content key
`seg_hash = sha256(client label | heading path | segment snippet texts)`: the context snippet and the extractor are
not in it (the context can change when the section above is edited, and quotes from it are never accepted). The
validated output of every segment is kept in `segment_cache`, keyed by `(seg_hash, extractor)`, for every kind
(a reverted file or a re-saved transcript reuses it too). When a new revision
is built, a segment whose `seg_hash` has a cache row with the current `extractor` reuses that output: it is
re-validated against the new snippets in full (section 8.4: quotes matched again, offsets recomputed, the client
check, and every `known_id` checked against the entities visible to this segment) at no model cost, and only the
other segments are sent.
`run_item` records reused segments as `status = cached`. "Re-read with the new rules" sends every segment again
because no cache row has the new `extractor`; old rows are vacuumed with the revisions that used them. Inside one
section longer than 6000 characters, an edit can shift the later segments of that section only.

Cost effect: files with many short sections produce more, smaller segments than the Appendix A model assumes
(`ceil(characters / 6000)` per document), each paying the prefix and catalog. The segment count in section 9 is a
lower bound for heading-dense files; S3 reports segments per document on the G0 scale set and in `--extract`, and
the estimate shown to the PM counts the real segments of the pending revisions, not the model's average.

### 8.3 Prompt contract (`rules/evidence.yaml`, shared with the live-tips skill)

The prompt and the output schema live in `rules/`: `rules/evidence.yaml` (types, relations, modality table, hedge
words, thresholds, the prompt text) and `rules/evidence_schema.json` (JSON schema of the output, next to
`tip_schema.json`). `tools/gen_skill_section.py` renders `rules/generated/skill_evidence.md`, and `skill/SKILL.md`
carries it like the other generated sections, so the skill extracts with the same contract when it writes notes.
The app bundle copies `rules/` without `generated` as today.

The prompt has a fixed, cacheable prefix (instructions, the type and relation lists, the modality table, the schema,
2 short invented examples) and a variable part:

- source meta: kind, origin, date, title, client label;
- the project's clients and a catalog of at most 60 known entities relevant to the segment (G1 task ids with their
  text, people with aliases, recent topics), chosen by FTS and alias match on the segment, and **only from entities
  and aliases visible to the segment's own scope**: the visibility rule of section 11.2 step 1 with a neutral
  scope built at the pass from the segment's client label (`client` = that label, `others` = every other label of
  `labels(root)`, which now labels a client folder even when it is the only one; a shared segment gets only entities
  and aliases with no label at all), so entities and aliases labelled another client are never sent with this
  segment. A client unknown at the pass (no series, no sheet yet) is labelled shared and its entities can appear in
  another segment's catalog until the next relabel: a prompt-quality limit on the same provider, not an access
  decision. Search access still decides on the request's scope at query time;
- the segment with line numbers.

Instructions in short: extract only what the text says; every entity, claim and relation must quote the text
verbatim (at most 300 characters) and give its line range; use a known id when the text clearly means that entity,
otherwise a new reference; never infer a relation from two separate sentences without a word that links them; mark
modality by the table; say `confidence` from 0 to 1; return JSON only.

Output (abridged schema):

```json
{
  "entities": [{"ref": "n1", "known_id": "c-1a2b3c4d5e", "type": "task", "name": "Item 13 SSO user mapping",
                "aliases": ["item 13"], "quote": "item 13 is blocking the SSO integration", "lines": [12, 12]}],
  "claims": [{"subject": "n1", "predicate": "status", "value": "blocked", "modality": "statement",
              "said_by": "person:emily", "due": null, "quote": "...", "lines": [12, 13], "confidence": 0.9}],
  "relations": [{"from": "n1", "rel": "blocks", "to": "n2", "modality": "statement", "quote": "...",
                 "lines": [12, 12], "confidence": 0.85}]
}
```

### 8.4 Output validation

Every item passes these checks, in this order, and a failure drops the item (counted in `run_item.dropped`), never
the whole segment:

1. JSON parses and matches `rules/evidence_schema.json`. A parse failure gets one repair retry with the error; a
   second failure marks the segment `invalid` (it is retried on the next rules or route change only). A reply cut
   at the output cap is not repaired but split (section 8.6).
2. Types, relations, predicates and modality are from the enums in `rules/evidence.yaml`.
3. The quote, after whitespace collapse and case fold, is a substring of the segment's own snippets, and the line
   range covers it. Offsets are recomputed by the store; the model's offsets are never trusted.
4. `known_id` exists in the catalog sent; otherwise it is ignored and the entity is resolved by name.
5. A relation whose two ends resolve to the same entity, or whose quote does not contain a word of each end's name or
   alias, becomes a suggestion.
6. Client check: a quote that `client_scope.text_side` labels as another client than the snippet's label is dropped.
7. Caps per segment: 40 entities, 60 claims, 60 relations; extras are dropped (a runaway answer cannot fill the
   store).
8. Fields under a `pm_override` and pairs the PM rejected are skipped.
9. Modality guard: `confirmation` from a derived source becomes `statement`; a hedge word anywhere inside the quote
   (`hedge_anywhere`, section 7.2) makes `confirmation` a `proposal`.

All of this is pure code and runs the same for every route.

### 8.5 Background, never during a call

- The worker is a sidecar command (`evidence pass`) the app starts: after a call ends and its notes are written,
  after the folder watcher sees changes (debounced 10 minutes), at most once per hour while the Mac is idle and on
  power, and by the PM's "Index now". Hiding or unhiding a call and a document turning `denied` or `not_found` start
  the immediate state-only pass (sections 5.1 and 5.2), which makes no model, network or embed request.
- A call in progress is a file the app writes: `call-active.json` (`{"started_at", "pid"}`) in the state folder next
  to `ai-routes.json` (`LT_AI_STATE` is the path of that file, not a folder), created at call start and removed at the
  end. This is the one path for it; it is not next to `embed-server.json`, which `semantic.py` (`launcher_path`, line
  80) reads from `<root>/state/` or `LT_EMBED_LAUNCHER`. A file older than 6 hours or with a dead pid is stale. The
  worker checks it before every segment and before every model request, and **watches it while a request is in
  flight** (a 1-second poll on a side thread). When it appears during a request, the worker aborts that request at
  once: it closes the HTTP connection on api, openai and gemini (Ollama stops generating when its client disconnects,
  so the local model stops loading the Mac too) and kills the process group of a `codex` or `code` CLI request. The
  segment stays `pending` (`run_item.status = cancelled`), nothing of the partial answer is kept, the run records
  the input tokens of the aborted request as spent (conservative, the provider may bill them) and ends with
  `status = paused_call`. No request is sent while the file is present. The worker does not take the embed server
  either while it is present.
- The worker runs at low priority (`nice 10`), one pass at a time across all projects (the global worker lock,
  section 13.1) and one writer per project (the project lock), at most one model request
  in flight per route (the plan limits are shared with the PM's prep work).

### 8.6 Routing

- A new routing job `evidence` joins `JOBS = ("prep", "tips", "after")` (not `index`, which would be confused with `rules/index.yaml` and the `lt index` folder index `.livetips-index`; not `memory`, which is `memory.py`). Its route defaults to the route of `after`
  and can be set separately in Settings; `LT_AI_ROUTE` works as for other jobs, but the general fallback chain
  (`CHAIN_DEFAULT`) is not used: extraction falls back only to `local`, never to another cloud route. The job still
  has to exist everywhere jobs are listed, so Settings can show it and the chain code does not reject it: Python
  `routing.JOBS`, Swift `AIChain` (`AIChain.allowed(route, job: "evidence")`, `ComponentCatalog.afterRemovingLocal` in
  `LiveTipsCore/Components.swift:78`, which today takes only `prep`, `tips` and `after`, so an `evidence` parameter
  is a signature change: its caller `OptionalComponents.swift:171` and the two CoreChecks calls
  `CoreChecks/main.swift:3861` and `:3864` change with it) and the
  `chain` entry of `rules/ai.yaml`, which states that `evidence` steps only to the model on this Mac (S5 for Swift, S3
  for Python and the rules).
- Model per route: the cheaper model of each route for extraction (api `haiku-5-5`, openai `gpt-6-luna`, gemini
  `gemini-3.5-flash-lite`, codex `codex/default`, code with its smallest model, local `LT_LOCAL_MODEL` or
  `gemma4:26b`) and the route's `after` model for topic summaries. Today `rules/ai.yaml` names the defaults only in
  prose; the model choices live in Python (`openai_provider.py:11` and `gemini_provider.py:17` `JOB_DEFAULTS`,
  `routing.PING_MODEL`, which already holds the cheap model of each route). S3 adds a real model map for the
  `evidence` job (extract and summary model per route) to `rules/ai.yaml` and the worker reads it from there, with
  `PING_MODEL` and the `after` entries of `JOB_DEFAULTS` as the fallback. Section 15, question 1 asks the owner
  about this default.
- **Output cap**: every extraction request sets a per-request output limit of 2,000 tokens (`max_tokens` on api
  and code, the equivalent field on openai, gemini and codex, `num_predict` on local), from `rules/evidence.yaml`
  (`max_output_tokens`). A reply cut at the cap (the provider's stop reason says so, or the JSON ends early) is
  not repaired by asking for fewer items, which would silently drop facts: the segment is split in two at a snippet
  boundary and each half is sent under the same cap, down to one snippet. Once every part validates, their merged
  output is stored in `segment_cache` under the **parent** `seg_hash` (with the summed tokens), so the next pass,
  whose segmenter yields the unsplit segment again, hits the cache instead of re-sending and re-splitting it. A single snippet still cut at the cap gets the one repair retry (section 8.4) asking for the items that
  fit; it is marked `truncated` in `run_item.error` and counted in `evidence status`, so the loss is visible. Each split request goes through the
  ceiling check like any other. Topic summaries use a cap of 800 tokens. These caps make the ceiling a hard bound (section
  9.1).
- Fallback to local (Ollama, `num_ctx` 65536, `127.0.0.1:11434`): when the chosen route is not connected, is capped
  by `cost.ProviderCaps`, or the pass ceiling is reached and the PM turned on local overflow (a setting, off by
  default, section 9.1). If Ollama or the model is not installed, the pass stops with `status = off` and says so;
  it never picks another cloud route
  the PM did not choose.

### 8.7 Topic summaries and global questions

- **Topics**: deterministic communities over accepted edges and co-mentions (entities cited by the same snippet),
  weighted, by label propagation with a fixed seed and a size cap of 40 entities, per client label (a topic never
  mixes two clients; shared entities may sit in both). Pure Python, no graph library.
- **Summary**: per topic whose `member_hash` changed, one model request on the `evidence` route's summary model with the
  topic's accepted claims and edges (with dates, modality, quotes) up to 6000 tokens; the output must cite claim ids
  and is validated like section 8.4 (every sentence cites at least one claim; uncited sentences are dropped). Stored
  one row per sentence in `topic_sentence`, with the claims and edges it cites in `topic_cite`, marked derived, never
  evidence.
- **Access on summaries is per sentence.** At query time a sentence is returned only when **every** claim or edge it
  cites passes section 11.2 steps 1 and 2 for the request (scope, source state, `as_of`) **and** has no **ignored**
  evidence row (a source in the request's `exclude`, or a deleted source): a sentence was written from all rows and
  cannot be split by row, so for sentences ignored rows are strict, unlike for claims and edges (section 11.2 step
  1). A sentence hidden because of a deleted source comes back after the staleness re-summarise below, written from
  the remaining rows. Other sentences of the same topic
  can still be returned. So a summary written before a source was excluded, revoked or hidden, or
  one that cites a file in this request's `exclude`, never returns that source's facts. A topic with no passing
  sentence is not returned at all.
- **Staleness on state change.** When a source changes state (excluded, hidden, deleted, revoked, or back to
  active), every topic with a sentence citing a claim or edge that has evidence on that source is marked `stale`
  (`topic_cite_target` index), and the next pass re-summarises it; until then the per-sentence filter above keeps it
  safe.
- **When**: team default eager, at the end of a pass for topics touched by it, inside the same ceiling and on the
  `after` model of the route (40 topics of the typical project: about $0.73 on Sonnet, $0.73 on GPT-6.1 Sol, $0.27 on
  Gemini 3.8 Flash before 2027-01-01, section 9); a setting switches to lazy (summarise only when a preparation or
  the PM asks a global question).
- **Global questions** ("what about SSO over all time"): `evidence.ask_global(query, scope, as_of)` finds topics
  by `topic_fts` and alias match, returns their summaries plus the dated claims behind them as a timeline. Answering
  in prose is the caller's model request (G3 preparation or G5), never the live path.

## 9. Cost model

All numbers below come from the script in Appendix A, run in this step (`python3 -I cost_model.py 500` and
`python3 -I cost_model.py 2000`); anyone can rerun it. Prices are `cost.py` `PRICES` (USD per 1M tokens: input,
cache read, output); characters per token per route are `rules/ai.yaml` (latin / cyrillic: api and code 2.5 / 1.6,
codex and openai 4.0 / 2.6, gemini 4.0 / 2.8, local 3.8 / 2.4). The plan-route anchor: the plan doc says one Codex
preparation took 4% of the 5-hour window; the size of that preparation (250K tokens) is **assumed**, not measured,
so every Codex percent below is an estimate on an assumed anchor. Measured: prices and ratios. Assumed: the project
shape, the per-segment overhead, the Codex anchor size, local speed.

**Assumed typical project** (no real folder was read): 40 past calls with a 45,000-character transcript each (the
follow-up step already caps transcripts at 60,000, `followup.py:49`) and 4,000 characters of notes; 150 folder
documents of 12,000 characters of extracted text; 15 site pages of 5,000; 30,000 of project state; half the text in
Ukrainian. That is 3.87M characters and 678 segments (a notes file is one segment, section 8.2; the script counts
the 15 site pages as one 75,000-character block, 13 segments, where per page it would be 15, a difference under
0.3%). Per segment: a 1,500-token cacheable prefix, a 2,000-token
entity catalog (60 entities with ids, text and aliases; `CATALOG` in the script, measured in S3), and output between **500 tokens (typical)** and **2,000 tokens (the per-request cap, section 8.6)**.
The contract allows 40 entities, 60 claims and 60 relations with quotes up to 300 characters, which would exceed the
cap; the cap, not the item limits, bounds the output. 40 topic summaries of 6,000 input and 600 output tokens, priced
in the table on the same model as extraction (summaries on the `after` model are added below the table). A typical week after the first index: 3 calls, 10 changed documents, 5,000
characters of state, 6 topics: 272,000 characters, 48 segments. Every figure is a range "typical to cap", where
"cap" means **one request per segment** answered at the full output cap. It is not a true maximum: a reply cut at
the cap is re-sent as two halves, each allowed up to the cap (section 8.6), so a segment can cost more than one
cap. The per-request ceiling check (section 9.1) still bounds what a pass spends; the
real output per segment is calibrated in S3 (G0 `--extract`) before S5 shows any number to the PM.

**Prompt cache on api Haiku.** The table assumes the provider caches the 1,500-token prefix. Anthropic documents a
minimum cacheable prompt length per model, and for Haiku models it has been higher than for Sonnet; whether 1,500
tokens qualify on `haiku-5-5` was **not verified** in this step. The Haiku row therefore uses the no-cache price.
S3 checks the provider's cached-token count on the first requests of every route and the estimate uses no cache on
any route where it stays 0.
The script prices the cached prefix at the cache-read rate only and leaves out the cache write: one write of the
1,500-token prefix per pass (again after any 5-minute gap), at the `cost.py` 5-minute write rate of 1.25 times
input, is under one cent per write on every route in the table (largest: Sonnet and gpt-6.1-sol at $2 input, about
$0.004). Gemini charges no write.

| Route and model | First full index (typical to cap) | One week incremental (typical to cap) |
|---|---:|---:|
| tokens on api (fresh in / cached prefix / out) | 3.58M / 1.08M / 0.36M to 1.38M | 0.27M / 0.08M / 0.03M to 0.10M |
| api `haiku-5-5` (no cache) | $0.65 to $1.16 | $0.05 to $0.09 |
| api `sonnet-5-5` | $10.89 to $21.06 (no cache $12.94 to $23.11) | $0.83 to $1.55 |
| openai `gpt-6-luna` | $0.47 to $0.98 | $0.04 to $0.07 |
| openai `gpt-6.1-sol` | $9.38 to $19.55 | $0.72 to $1.44 |
| gemini `gemini-3.5-flash-lite` | $1.77 to $4.31 | $0.14 to $0.32 |
| gemini `gemini-3.8-flash` now | $3.52 to $7.33 | $0.27 to $0.54 |
| gemini `gemini-3.8-flash` from 2027-01-01 | $7.04 to $14.67 | $0.54 to $1.08 |
| codex plan, 4.26M to 5.28M tokens | about 68% to 84% of a 5-hour window (assumed anchor) | about 5% to 6% |
| code plan (Claude Code), 5.0M to 6.0M tokens | not measured: no anchor for the Claude plan yet | not measured |
| local `gemma4:26b`, 4.35M to 5.37M tokens | $0, 5.2 to 29 hours (assumed 300 to 600 tokens/s prefill, 15 to 30 tokens/s output; not measured) | 0.4 to 2.1 hours |

Every row includes the 2,000-token catalog. One correction to the table, computed in this step with the second
script of Appendix A:

- **Topic summaries run on the `after` model** (section 8.7), not the extraction model. 40 summaries cost $0.73 on
  Sonnet or GPT-6.1 Sol and $0.27 on Gemini 3.8 Flash ($0.55 from 2027-01-01), against $0.04 on Haiku or GPT-6 Luna
  and $0.13 on Flash-Lite in the table. With recommendation (a) of section 15 (cheap model extracts, `after` model
  summarises), a first index costs **$1.34 to $1.85** on Claude key (Haiku plus Sonnet), **$1.16 to $1.67** on
  OpenAI (GPT-6 Luna plus GPT-6.1 Sol) and **$1.91 to $4.45** on Gemini (Flash-Lite plus 3.8 Flash); these are the
  ranges of sections 10 and 15. A typical week (6 topics) then costs $0.15 to $0.19 on Claude key, $0.14 to $0.17
  on OpenAI and $0.16 to $0.34 on Gemini. The S3 estimator prices each request on the model that sends it, so
  summaries are priced on the summary model. S3 measures the real catalog size and output per segment, and its
  calibration replaces every number of this section.

What the numbers say:

- Output is 33% to 51% of the dollar cost at the typical output and 65% to 80% at the cap (output prices are 5 to 8
  times input), so the output cap and short quotes matter as much as the catalog size. This is why the cap is part
  of the contract and S3 measures the real output per segment and the real catalog size.
- On api and openai, where the cache applies, the cacheable prefix saves about 15% to 18% at the typical output and
  about 9% at the cap (e.g. Sonnet $12.94 -> $10.89, $23.11 -> $21.06). S3 sends the prefix first and identical on
  every request so the providers' prompt cache applies.
- On the cheap model of each route the first full index costs $0.47 to $4.31 for extraction ($1.16 to $4.45 with
  summaries on the `after` model); on the `after` models of api and openai it costs $9.38 to $21.06. The weekly
  incremental is $0.14 to $0.34 with recommendation (a) (cents for extraction, the rest summaries) and up to $1.55
  on the `after` models.
- On the Codex plan the first full index does not fit one 5-hour window next to normal work; it has to spread over
  7 to 9 passes under the 10% pass ceiling, about 3 days at the 30% daily cap. On local it is an overnight job at
  best and more than a day at the cap.
- The G0 fixture is 10,383 bytes (20 files): about 3 segments, far below one cent on any route; it is not a cost
  reference.

### 9.1 Ceiling

- One ceiling per pass, in the unit of the route: USD on `api`, `openai`, `gemini` (default $1.00 per pass), percent
  of the 5-hour window on `codex` and `code` (default 10%, read from `ai-accounts.json` before the pass and after
  every request through the existing `account_usage_*` functions), wall-clock minutes on `local` (default 120).
  `ai-accounts.json` changes only when `note_account_usage` is called, so the worker's own `codex` and `code`
  requests feed it through `account_usage_codex` and `account_usage_claude` after each request (S3), or the
  percent would not move inside a pass.
  These are team defaults, each a setting (section 10); on reaching the ceiling the pass waits for the next one,
  and continuing on the local model is an opt-in setting (off by default).
- **Daily cap**: passes can run hourly, so a per-pass ceiling alone does not bound a day. A second cap per project
  and route per calendar day: 3 times the pass ceiling by default ($3.00, 30% of the 5-hour window summed over the
  day's passes, 360 minutes on local), also a setting. The worker sums today's `run` rows before every pass and
  before every request; at the daily cap the pass ends with `status = ceiling` and the row says "daily".
- **Global daily cap across projects**: with N projects the per-project cap alone allows N times the spend in a day.
  One more cap sums today's `evidence` spend of all projects per route from `spend-ledger.jsonl` (job `evidence`), which is
  shared by all projects; default the same $3.00, 30% of the 5-hour window or 360 minutes on local, a setting. A
  pass stops at whichever of the three caps (pass, project day, all projects day) comes first. Only one pass runs at
  a time across all projects (a global worker lock next to the per-project lock), so two projects never race past the
  global cap. The monthly `cost.ProviderCaps` and `BudgetGuard` stay in force on top and bound the month.
- The estimate before each request uses `size.estimate` on the segment plus the prefix plus the full output cap
  (2,000 tokens for extraction, 800 for a summary), never the typical output, so the pass stops before the request
  that would cross the ceiling (`status = ceiling`) even if that request returns the longest allowed answer. With the
  output cap set on every request (section 8.6), the ceiling is a hard bound on USD routes; on plan routes the
  percent is read back from the account after every request and can overshoot by at most one request.
- Plan routes also stop when the 5-hour window is above 80% used (the same `near_share` idea as prep), so indexing
  never eats the limit the PM needs for the next preparation.
- Spend is written to the existing `spend-ledger.jsonl` with job `evidence`.

## 10. Settings and cost text (text only, Design later)

Settings > AI, a new row under the routes (only when the flag is on):

- Title: "Project memory". Help line: "Pacenote reads your project's calls, notes and files on this Mac and keeps
  who and what they mention, with quotes. It never runs during a call."
- Per project: "Indexed 412 of 678 parts. Last pass: Nov 23, 14:10, 1.2M tokens, $0.31 on Claude key (Haiku).
  Next pass: about 48 parts and 6 topic summaries, about $0.15 to $0.19." On plan routes: "about 5% of your 5-hour ChatGPT limit". On
  local: "about 0.5 to 2 hours on this Mac, free" (the weekly local row of section 9: 0.4 to 2.1 hours).
- Controls: "Index now", "Pause", the route ("Use for indexing: same as after the call / <route list>"), the ceiling
  ("Stop each pass at $1.00 / 10% of the 5-hour limit / 120 minutes on this Mac", "and each day at $3.00 / 30% /
  360 minutes", and for all projects together "Never more than $3.00 / 30% / 360 minutes a day for all projects"),
  "Topic summaries: after each pass / only when asked", the switch "Use AI to read
  sources" (off = deterministic links only, no model requests), and "Delete this project's memory".
- **Owner decision 24.11 (section 15, question 2, option b): no confirmation before the first index.** Turning the
  flag on is the consent. Where it is turned on, the row's first line says in plain words that project text will be
  sent to the provider chosen for indexing and what the first index is estimated to cost: "Project text will be sent
  to Anthropic for indexing. Estimated first index: about $1.34 to $1.85." Until S3's calibration is on main the
  second sentence is "The estimated cost of the first index is not available yet." On the model on this Mac it says
  "Indexing runs on the model on this Mac. Project text does not leave the Mac." When the indexing provider is not
  the one used for preparation, the line below it stays: "Your project files have so far gone only to <prep
  provider>; indexing sends them to <index provider>." The per-pass, per-day and all-projects ceilings, the cost
  before and after a pass and the off switch stay as above. The dialog below is kept only as the record of what was
  proposed; S5 does not build it.
- (Superseded by the owner decision above.) Before the first full index of a project, a confirmation: "First index of Northpoint: 192 sources, about 3.9M
  characters. On Claude key (Haiku reads, Sonnet writes topic summaries): about $1.34 to $1.85, in 2 passes. On your
  ChatGPT plan: about 68% to 84% of a 5-hour limit, in 7 to 9 passes over about 3 days. On this Mac: 5 to 29 hours,
  free. Your files are sent only to the AI you chose
  for this job." When the indexing provider is not the one used for preparation, one more line: "Your project files
  have so far gone only to <prep provider>; indexing sends them to <index provider>." Buttons: "Start", "Use this Mac only", "Not now". The range comes from the estimate with typical
  output and with the output cap; after S3 calibration the typical end uses the measured output per segment.
- After a pass, in the same row: "Done: 230 parts, 0.9M tokens, $0.29 (estimate was $0.31)". A stopped pass says
  why: "Stopped at your $1.00 ceiling", "Paused: a call started", "Local model not installed".

Strings go to `rules/i18n/en.json` and `uk.json` together when S5 builds them.

## 11. Search API

### 11.1 Signature (Python, `livetips_m0/evidence/search.py`)

```python
search(store, query, *,
       scope,                    # Scope(project_uid, client, others, allow_shared=True) from client_scope.decide/from_sheet
       as_of=None,               # datetime; None = now. Replay passes the call start.
       window=None,              # (from, to) on said_at; None = all time
       exclude=(),               # source ids or uris whose evidence rows this request ignores (the PM's per-file
                                 # skip in Sources, this call's own files); section 11.2 step 1. Hidden and revoked
                                 # sources are never passed here: they are denied by state, or by a pending request
                                 # in the state-request log (section 13.1)
       kinds=None,               # snippet, claim, edge, topic
       mode="prep",              # prep, live, after
       budget=Budget(k=6, chars=14000, ms=1000, hops=1),
       include_suggestions=False,
       query_vec=None) -> Result # items: kind, text, quote, locator, source uri and title, said_at, client, modality,
                                 # status, origin, confirmations, why (the path: "item 13 -> blocks -> SSO"), score;
                                 # quotes, said_at, said_by, first/last said and confirmations of a claim or edge come
                                 # from its evidence rows that pass this request only, never from the cached columns
entity_card(store, entity_id, *, scope, as_of=None, window=None) -> Card   # claims and edges of one entity, dated
ask_global(store, query, *, scope, as_of=None, window=None, budget=...) -> GlobalResult  # section 8.7
```

### 11.2 Order: access and time before ranking

Every query is built in this order, and steps 1 and 2 are SQL `WHERE` clauses on the candidate query itself, never
filters on a ranked list:

1. **Access**: a snippet passes when its source has `state = 'active'` (a snippet of a source that is not `active`
   never passes on its own), its source is not in `exclude`, and its query-time side for the request's scope is not
   **other** (section 5.3: the source path against `scope.folders`, then the `path` marks, then the heading and line
   marks, the `side()` then `filter_text` outcome; with a stale label universe the live reader passes only rows under
   the own client's folders). No `client_set` column and no `entity.client` is an input here. A failing row belongs to exactly
   one of two classes, and every other section of this spec refers to the class, never to a list of states:
   - **Denied** (an access decision: source `state` in `excluded` (PM exclusion, `.livetipsignore`, hidden call) or
     `revoked`, a source named by a pending hide or revoke in the state-request log, section 13.1, or the side
     **other** for the request's scope): strict. The claim or edge passes only when `EXISTS` a passing evidence row and `NOT EXISTS` a
     denied row, so a claim with one out-of-scope or revoked quote is dropped, not shown with fewer quotes.
   - **Ignored** (absence, not access: source `state = 'deleted'`, or source in the request's `exclude`): those rows
     are left out and the claim or edge passes when a remaining row passes. So G5's "what changed" with this call's
     own files excluded still sees a fact the call repeated, dated by its earlier sources; a replay with the past
     call's transcript excluded keeps the earlier facts it touched; skipping one noisy file hides only what nothing
     else says; a deleted (or moved and edited, section 5.2 item 6) file hides only what nothing else says, and a
     fact it shared stays, dated by the other rows.
   - The SQL is one `CASE` on the evidence row (`denied`, `ignored`, `pass`), so a new source state must be put in
     one class in one place.
   - In both cases every returned field of a claim or edge (quotes, `said_at`, `said_by`, first and last said,
     confirmations, section 7.4) is computed from the passing rows only, never from the cached columns, so a fact is
     never dated or attributed by a row the request does not see. A claim or edge with no passing row never passes.
   No scope (`decide()` returned None, or a one-client project) passes everything of the project that is not denied
   or ignored. The same check covers the rows that have no evidence of their own:
   - **Entity**: visible when at least one of its claims or edges passes (with its evidence, as above), or it has a
     deterministic origin (`g1`, `tracker`, `voices`, `pm`) whose source passes and whose query-time side from its
     `entity_mark` rows (`series_side`, then `side_of_name`, then `text_side`, the `item_side` order) is not
     **other**. An entity that is not visible is never a match, a graph step end, a catalog entry (section
     8.3) or a `why` path element, and its name is not returned.
   - **Alias**: matched only when its entity is visible and, for an `llm` alias, `EXISTS` a passing `alias_seen`
     snippet and `NOT EXISTS` a denied one (strict, like a claim); a deterministic alias (`voices`, `g1`, `tracker`,
     `notion`, `pm`) follows its entity's visibility.
   - **Topic summary sentence**: returned only when every claim and edge it cites passes and none of them has an ignored
     row (a source in `exclude` or a deleted source: strict for sentences, section 8.7); a topic with no passing sentence is not returned.
2. **Time** (content time, section 6): with `as_of`, a revision is valid when `valid_from <= as_of` and
   (`valid_to IS NULL` or `valid_to > as_of`); without `as_of`, the active revision (`superseded_at IS NULL`).
   Snippets must sit on a valid revision and have `said_at <= as_of`. A claim or edge is **current** only while it
   has at least one passing evidence row on a revision valid at `as_of`; a claim whose only evidence sits on a
   replaced revision (an old go-live date in an edited file) is history: `entity_card` and `ask_global` return it in
   the timeline marked "changed later", `search` does not return it as current. Topic summaries need
   `summary_at <= as_of` (a summary written later never appears in a replay, even if its claims are old; so a replay
   of a call older than the first index gets claims and timelines but no topic summaries). `observed_at` is never
   used here except as the revision 2 fallback of section 6. `window` narrows on `said_at`. Both checks compare full
   UTC times, so a file edited on the day of a call but after the call started is not visible in a replay of that
   call (its new revision starts at the edit, not at midnight), and notes edited after a call are not visible in a
   replay of any call between the two.
   Known limit (coverage loss, never a leak): the store sees a file only from the first pass on, so revision 1 is
   whatever the file held then. A file or notes file edited after a call and first ingested after that edit has
   revision 1 starting at the edit, and a replay of that call does not see it even though most of it existed
   earlier. The same holds when a Drive re-sync or a copy to a new Mac rewrites mtimes before the first index: every
   single-revision file (notes included) is dated at the sync, and a replay of an older call sees only transcripts
   and register events. From the second revision a pass observes, replay is exact to the mtime.
   **State columns under `as_of`.** The "now" caches (`entity.name`, `entity.status`, `entity.merged_into`,
   `claim.status`, `edge.status`, section 6) are not read in a replay; they are derived from rows dated at or before
   `as_of`:
   - an entity's status and name come from its `status` and name claims with `said_at <= as_of` (for tasks these are
     the register events that pass step 1 folded up to `as_of`, the same `commitments.fold()` cut at that time, section
     5.3), so a task closed on
     Nov 19 is open in a replay as of Nov 17; a Tracker item's name and status come from the `text` and `status` its
     snippet locator keeps on the Tracker revision valid at `as_of` (the active revision without `as_of`, so each
     snapshot renames and closes the item; PB-84 review round 5); an entity with no name claim keeps the name of its earliest evidence at
     or before `as_of`, and an entity with no row at or before `as_of` is not visible at all; a `d-` or `r-` entity
     (call notes, `<stem>.notion.json`, `decisions.md`) is visible only through a revision that carries it
     (`entity_rev`): one valid at `as_of`, the active one without `as_of`, never just any revision of its file, so a
     decision a later revision adds is not in an earlier replay, and one a later revision removes is visible only in
     a replay at a time its revision was valid, not as current (PB-84 review round 7);
   - a merge applies only when its register event or `pm_override` has `at <= as_of`;
   - a claim or edge is `superseded` only when the superseding claim (or the PM's `supersedes` edge) is dated at or
     before `as_of`, and `rejected` only when the rejecting override has `at <= as_of`; one exception: a PM rejection
     of an extracted claim, edge or alias corrects the store, not the world, so it applies in every replay, and a
     rejected claim is not in an `entity_card` timeline either (PB-84 review round 5);
   - a decision superseded after `as_of` (its `supersede` override, section 7.1, has `at > as_of`) is current in
     that replay.
3. **Candidates**: FTS5 on snippets and topics; alias match of query words to entities (casefolded, both languages);
   with `query_vec`, cosine over the allowed snippets' vectors.
4. **Graph step**: from matched entities, `hops` steps over accepted edges (status derived under `as_of`) whose
   evidence passed 1 and 2 and whose both ends are visible entities by the rule in step 1 (suggestions only with
   `include_suggestions`).
5. **Rank**: BM25 score, entity match, path length, recency, primary confirmations; at most 2 items per source (the
   `FolderIndex` rule).
6. **Budget**: stop at `k`, `chars` or `ms`, whichever comes first, and say `truncated`.

### 11.3 How G3 preparation will call it

`mode="prep"`, `as_of` = the call start, scope from the sheet, `hops=2`, `include_suggestions=False`, `query_vec`
allowed (preparation is outside the call), budget from the route's per-pass share (`size.per_pass * near_share`
minus what `auto_sources` already took). The result enters Sources as an "evidence" block with the `why` path visible
("linked through item 13 with the Nov 12 technical sync"), and a snapshot (items, quotes, locators, entity cards of
the call's entities) is written to `<AppPaths.support>/evidence/snapshots/<evidence_uid>/<stem>.evidence.json` for the
live call and the skill (the skill gets the path from the app; G3 wires it). Never under `.work/` or anywhere in the
project folder: section 3 binds, because the snapshot holds quotes and would sync with Drive. The state-only pass
deletes every snapshot that cites a source leaving `active`, and "Delete this project's memory" removes the folder.
Same for every route.

### 11.4 How G4 live will call it

The normal tip path makes no new network or model call. G4 loads the prep snapshot into memory at call start and
answers from it. **Requirement for G4**: a hide or revoke that lands during the call (the state-only pass, section
5.1) must also reach that in-memory copy: G4 drops the snapshot items whose sources the state change names (or
reloads the snapshot through `search(mode="live")`) before the next tip, so a source hidden mid-call is not quoted
from the copy loaded at call start. The only store access G2 allows on the live path is `mode="live"`, which:

- opens the SQLite file read-only (`mode=ro` URI), with the WAL readers never waiting on the writer (the
  extraction worker is stopped during a call, section 8.5; the short state-only and `override` writes of sections
  5.1 and 11.6 are WAL transactions a `mode=ro` reader never waits on);
- refuses `query_vec` (a query embedding is a model call) and any `ask_global`;
- takes the scope the preparation kept in the sheet (`client_scope.from_sheet`) and applies it at query time like
  every caller (section 11.2 step 1); with a stale label universe it cannot relabel (read-only), so it returns only
  rows under the own client's folders and says `scope_stale` (section 5.3);
- answers within `budget.ms` (default 150 ms, measured in G0) from FTS5, aliases and one hop; vectors are used only
  between stored rows (the snapshot entities' alias vectors against stored snippet vectors, both computed at ingest),
  never for the question itself.

Whether HELP NOW and questions use `mode="live"` at all, and with what p95 cost, is G4's decision (its gate is p95
not worse than +5%).

### 11.5 G5 after the call

`mode="after"`, `as_of` = the call end, the call's own sources excluded when checking "what changed" (the plan:
reconcile against the previous state, not against this call's own records), `entity_card` for every register item
the notes touch.

### 11.6 CLI

`python -m livetips_m0 evidence status|ingest|pass|exclude|override|search|card|ask|rebuild|vacuum|delete
--project-uid ... --root ...` with JSON output, so the Swift app and the skill use one interface. `pass` and
`exclude` take the lists the app passes: `--hidden-calls`, `--revoked` (Google file ids and Notion page ids with
status `denied` or `not_found`) and `--web-dirs` (stored page folders outside call folders). `exclude` is the
state-only pass of section 5.1: no model, network or embed request. `override --target --key --field --value --at
--via` writes one `pm_override` row (the supersede click of section 7.1, later the G3 and G5 confirm and reject
actions); like `exclude` it is a local write only and runs also during a call. Both take `--request-id` (a UUID the
caller mints); when a pass holds the lock they append the request to the state-request log instead (section 13.1).
Applying the same `request_id` twice is a no-op, so a caller may always re-send. `status` lists the applied request
ids (`--request-id <id>` asks about one).

## 12. Privacy

- The store is one SQLite file per project in `~/Library/Application Support/LiveTips/evidence/`, never in the
  project folder, never synced by Drive, never sent to the Pacenote server (call sync, D27 and D47 unchanged).
- Model requests go only to the route the PM chose for the `evidence` job (or local). The `evidence` route defaults to
  the `after` route (section 8.6), which receives transcripts and notes today; folder files, project state and site
  pages go today only to the `prep` route. When the `evidence` route's provider differs from `prep`'s, indexing sends
  those files to a provider that has not seen them before, so the line where the flag is turned on (section 10) names
  that provider in that case. G2 adds no destination the PM did not choose for some job. The worker itself makes no
  Notion, HubSpot or web request: the Tracker comes from the snapshot file the app writes (section 5.2, item 5), site pages from
  what G6 already stored. The first full index asks before it starts (section 10) because it sends more text than
  one preparation does. (Owner decision 24.11, section 15: no question; the line where the flag is turned on names the
  provider and the estimated cost instead.)
- The files of a hidden call (section 5.1) are never sent to any model and never searched; its register events
  stay searchable as register items, as the app's Tracker shows them today (section 5.1). Hiding a call later starts the
  immediate state-only pass, which moves its sources to `excluded`, and from then on their claims, edges and the
  topic sentences citing them stop passing the access check. The pass runs at once, also during a call; only while
  a full pass holds the project lock is it appended to the state-request log (section 13.1), and every `search`,
  including the skill's, treats a pending hide as denied, so there is no window. A document that loses access
  (`denied` or
  `not_found`) goes the same way to `revoked` (section 5.2, item 6).
- Vectors come from the bundled local bge-m3 server; nothing is embedded in the cloud.
- Emails and phone numbers are already scrubbed from site pages (G6); the store keeps text as the reader returns it,
  so the store holds what the project folder holds. Secrets folders skipped by `semantic.py` and `.livetipsignore`
  are skipped by ingest too.
- "Delete this project's memory" removes the file, its WAL, the lock and the project's prep snapshots
  (`evidence/snapshots/<uid>/`, section 11.3). Removing a project from Pacenote offers
  the same. A revoked source disappears from search at once, with every claim or edge it quotes (denied, section
  11.2 step 1). A deleted source's snippets and the facts only it stated disappear at once; a fact also quoted
  elsewhere stays, dated by the other rows (ignored, section 11.2 step 1). Snippets are purged at the next vacuum
  unless the PM keeps history (default: purge revoked, keep deleted local files for 30 days for undo). The purge of
  a deleted source changes no search result: its rows were already ignored, and a claim left with no evidence was
  already not returned.
- Telemetry (`usage.py`) logs counts only (`evidence_pass` with segments, tokens, cost, status), never text, names or
  paths.
- Tests and the benchmark use invented data only.

## 13. Failure, migration and rebuild

### 13.1 Concurrency

- One writer per project: the worker takes `<uid>.lock` (flock) for a pass; ingest from the prep step waits at most
  2 seconds for it and otherwise reads the last committed state. The state-only pass (hide, unhide, revoke) and
  `evidence override` also wait at most 2 seconds for it.
- **State-request log.** When a full pass holds the lock, those commands append one JSON line
  (`{"request_id", "kind", "payload", "at"}`) to `<support>/evidence/<uid>.state-requests.jsonl`, append-only, under
  a short separate flock `<uid>.state-requests.lock` (held only for the append or a compaction, never across a
  model request), so two requests during one pass are two lines and neither overwrites the other.
  - Applying: every writer that takes the project lock (a pass or a state-only command) first applies every line
    whose `request_id` has no `state_request` row, in file order, each in one transaction that also writes its
    `state_request` row. A pass also does this between segments and before it exits, whatever its end status
    (`done`, `ceiling`, `paused_call`, `failed`). A pass killed with SIGKILL leaves no end status, but flock is
    released with the process, so the next writer applies the lines it left. Lines whose ids are applied are dropped
    by a compaction under both locks at the end of a pass.
  - Reading: `search`, `entity_card`, `ask_global` and the snapshot loaders (section 11.3) read the unapplied lines
    (a small file, read once per call) and treat the sources of a pending hide or revoke as denied (section 11.2
    step 1) and drop snapshot items citing them. A pending unhide, un-revoke or `override` has no effect until it
    applies. Readers take no lock: the appender writes each line with one `write()` call that includes its trailing
    newline, and a reader (and a writer applying lines) skips a last line that has no trailing newline or is not
    valid JSON, which is a line still being appended; it is read on the next call. A compaction writes a new file
    and `os.rename`s it over the old one, so a reader without the lock sees the old file or the new one, never a
    truncated one.
  - Retry: the app keeps the ids it sent until `evidence status` lists them as applied, and re-sends any id not
    listed after the pass ends (or at the next launch); a re-sent id that is already applied is a no-op.
- One pass at a time across all projects (`evidence/worker.lock`), so the global daily cap (section 9.1) is summed
  without a race.
- Many readers (prep, the live engine, the skill CLI) use WAL snapshots and never block the writer.
- Every source revision is one transaction; a crash leaves the previous revision active and the segment
  `pending`.

### 13.2 Failures

| Failure | Behaviour |
|---|---|
| Route error, timeout, rate limit | segment stays `pending`, back-off doubles from 1 minute to 1 hour, the pass ends with `failed` after 3 consecutive errors |
| Invalid output twice | segment `invalid`, retried only after a rules or route change; counted in `evidence status` |
| Ceiling reached | `status = ceiling`, the rest stays `pending` for the next pass |
| Call starts | the request in flight is aborted, the segment stays `pending`, `paused_call`, section 8.5 |
| Corrupt SQLite (`PRAGMA quick_check` fails at open) | the file is moved to `<uid>.corrupt-<stamp>.sqlite`, a fresh store is built deterministically (free), extraction starts again under the ceiling; the PM sees one line saying so |
| Disk full | the transaction rolls back, the pass ends `failed`, nothing half-written |
| Pass killed (SIGKILL, crash) | flock is released with the process; the run row stays `running` and the next pass marks it `failed`; unapplied lines of the state-request log are applied by the next writer before anything else (section 13.1), and readers treat pending hides and revokes as denied meanwhile |
| FTS5 missing | fallback search (section 6), noted in status |
| Embed server down | vectors stay NULL, search works without them |

### 13.3 Schema migration

`PRAGMA user_version` holds the schema version. Migrations are forward-only Python steps in one transaction each,
tested on a store built by the previous version. A store newer than the code is opened read-only and the worker does
not run. A change of the extraction contract (rules version or prompt hash in `revision.extractor`) does not
rebuild anything by itself: revisions keep their claims, and "Re-read with the new rules" is a PM action that marks
them `pending` (it costs money, so it is never automatic).

### 13.4 Rebuild, move, rename, vacuum

- **Rebuild** = delete derived tables and redo deterministic ingest (free) and extraction (under the ceiling).
  `pm_override` rows are kept and re-applied by their natural keys (section 6), so PM edits survive a rebuild;
  task edits survive because they live in the register.
- **Client folder added or renamed**: the label universe gains a slug and the relabel splits stored snippets locally
  (section 5.2, from `snippet.text`, superseded revisions included): evidence is re-anchored by its offset inside the
  old snippet, nothing is re-extracted unless a quote crosses a new cut. A removed folder needs no change.
- **Project renamed**: same uid, so the store is kept. But register items and Tracker items carry the `project`
  slug, and G1's `scope_match` and the Tracker scope compare it, so items written before and after a rename would
  not join. The store keeps every slug the project ever had in `meta` (`slug_history`, appended when the app passes a
  new slug with the same uid) and normalises every register event's `project` to the uid through it **before**
  `commitments.fold()` runs (fold joins items by scope itself, so normalising after it would still split an item
  across the rename) and before any other G1 key function. The register itself is not rewritten. **Folder moved** through Settings: same uid; `root_hint` updated;
  source uris are relative so nothing is re-read (hashes are equal). A folder re-added as a new project gets a new
  uid and a new store; the old one is listed in status as orphaned and deleted after confirmation. The same holds
  for a config import: `Projects.merge(imported:)` matches by slug, so S5 makes it keep the `evidence_uid` of the
  project it matches; an import whose slug changed adds a new project (today's behaviour, unchanged) and falls under
  the orphan handling above. "Rename keeps the uid" therefore means a rename inside the app.
- **Call moved to another project** (`CallReassignUI.change(file:to:)`): its sources become `deleted` in store A
  (ignored rows, section 11.2 step 1) and new sources in store B; `d-` ids are the same (stem and text) but
  `pm_override` rows stay in store A and do not follow the call.
- **Vacuum** (weekly, and right after a source is revoked; always outside calls: a revoke that arrives during a call
  is applied at once by the state-only pass, which already hides it, and its vacuum runs after the call ends,
  section 8.5): first delete every claim and edge that has an evidence row on a revoked source, with all its
  evidence and topic citations, even when another file quotes it too (a fact the revoked file states stays out of
  search after the purge as it was before); then purge snippets of superseded revisions
  nobody cites, purged sources, then, in the same transaction, every claim and edge left with no evidence row and
  every `segment_cache` row whose `seg_hash` no segment of a remaining revision has, or whose `extractor` is not the
  current one and is older than 30 days; old `run_item` and `state_request` rows (keep 90 days); then `VACUUM`.
  The vacuum command applies pending state requests first, so a revoke still in the log is purged by this run. The
  shared-claim purge is not undone if the document is shared again: the other file's copy of that fact comes back
  only after `rebuild` (deterministic claims) or re-extraction (S3).

## 14. G0: how the store is measured

### 14.1 Variants

- **A**: current code (exists).
- **B**: wider text search without a graph: the same store's snippets and FTS5 over all calls of all series and all
  folder files without the 7-day window, scope and time filters on, no entities, no edges, no topics. It answers "is
  it just more context?".
- **C**: B plus entities, claims, edges, `hops=1..2`, topics and `ask_global`.

B and C use **the same candidate sources and the same candidate signals**: both run with stored vectors and the
same `query_vec` setting (the bench runs both twice, without and with vectors, and reports both pairs), so the
difference B to C is the graph and nothing else. `offline()` in `run.py` allows loopback, so for the run with
vectors the bench starts the bundled `embed_server.py` itself when the bge-m3 model folder is present (and stops it
at the end); without the model folder (CI) the with-vectors pair is recorded as `skipped` with the reason, and gates
that name the run with vectors are not evaluated in that run (they are evaluated on a machine with the model).

The bench makes no model call and blocks every host (`run.py`). Variant C therefore uses a **checked-in extraction
fixture**: `bench/g0/fixtures/extract/<seg_hash>.json`, written once by a real extraction run on a cheap route
(route, model, date and `extractor` recorded in the file). It is keyed by the content-only `seg_hash` (section 8.2),
so a prompt or rules change does not invalidate it: C still replays it, and the report states when the fixture's
`extractor` differs from the current one. Re-recording (a paid run) is a deliberate step, needed only when a gold
file's text changes or the contract changes the output shape. The fixture is the **raw validated model output**: a hand review may
only delete items that are wrong (each deletion listed with its reason in the same file), never add or edit one, so
what C finds is what the model found. C replays the fixture through the same validator (section 8.4). C reports
**fixture hits and misses** (segments with and without a fixture file for their `seg_hash`): a change to the
chunker, the segmenter or the client labels changes `seg_hash`, and without this count C would quietly lose its
extraction and fall back to B. Any miss on the small gold set fails the C run as "fixture stale".

The scale set's noise generator writes, with the same seed, the extraction output of each noise document next to it
(entities and claims built from the template it filled). That output is perfect by construction, so the scale set
measures ranking, latency and leaks under distractors, **not** extraction quality; no G0 gate on hop or paraphrase
rates uses the scale set's generated extraction.

A separate opt-in command, `run.py --variant C --extract <route>`, runs real extraction on a route outside CI and
reports per route: extraction precision and recall against the fixture (entities, claims, edges with quotes), the
**hop and paraphrase answer rates computed on that run's own output** (not on the fixture), output tokens per
segment (the calibration for section 9), tokens and cost. It is the only G0 run that calls a model.

**Two revision states for replay gold.** The bench builds the store at run time, so a "file changed on Nov 19" needs
two states. The fixture holds `fixtures/revisions/<file>.v1` and `.v2` with a sidecar `revisions.yaml` giving each
state's content time as a full UTC date and time (Nov 12 and Nov 19); the reverted file of section 14.2 item 4 has
three states, the third (Nov 21) being `.v1` again. For these the bench ingests v1, then v2, passing the content time
explicitly (the CLI and the ingest API accept `--valid-from <UTC date and time>` for tests and the bench only), so
the store has two revisions with `valid_from` set as in real use, independent of the files' mtimes on the CI
machine. The two **same-day and notes cases** of item 4 instead test the content-time rule itself (section 6): the
bench copies each state into its temp folder, sets the mtime from `revisions.yaml` with `os.utime`, and runs a
normal pass without `--valid-from`, so the rule that turns mtimes into `valid_from` and `said_at` is what is
measured.

### 14.2 Why harder gold

The live answer rate is already 100% because the fixture is 10 KB and every question shares words with its file.
New gold (`gold.yaml` version 2, all invented, US names, same two clients) that A cannot pass by word match:

1. **Cross-series and multi-hop** (at least 6 questions, so a gate is never decided by one question): e.g. "What is
   blocking the pilot launch?" needs pilot -> depends on SSO -> blocked by item 13 (technical sync, Emily) -> waiting
   for client confirmation (weekly, Mike); the others follow the same shape over a document, a risk, a person and a
   decision, at least 2 of them for the second client and at least 2 needing 2 hops. Each answer needs quotes from
   at least 2 calls or files of 2 series, and none of its quotes shares a content word with the question except the
   start entity.
2. **Paraphrase and other language**: "Is the login problem still open?" (no "SSO", no "item 13" in the question);
   a Ukrainian question about the English notes where no word matches, only the entity alias does. FTS5 with
   `unicode61` has no Ukrainian stemming, so recall in Ukrainian rests on aliases and vectors; the other-language
   question is reported in the run without vectors too, so that weakness shows instead of hiding behind bge-m3.
3. **Global**: "What happened with SSO since October?" expects a dated timeline of at least 4 facts from 3 sources
   in order (topic summary plus claims).
4. **Time replay**: a go-live date changed on Nov 19 in an edited file (the two revision states of section 14.1);
   replayed as of Nov 17 the answer must be the old date and the Nov 19 fact must not appear (future leak); asked
   now, the answer is the new date and the old one appears only in the timeline marked "changed later". The same
   replay also covers state columns: a task the register closes on Nov 19 must be open as of Nov 17, and a decision
   superseded on Nov 19 (a `supersede` override dated Nov 19, written by the bench through `evidence override
   --key notion:<page id>`, section 7.1) must be current as of Nov 17 (section 11.2 step 2). The fixture holds the
   two decisions' `<stem>.notion.json` sidecars with invented page ids, and the bench ingests the notes first and
   the sidecars after, as in real use, so the override must land on the call-keyed `d-` entity.
   A second edited file is reverted (section 5.2): a budget figure with text A on Nov 12, B on Nov 19 and A again on
   Nov 21; as of Nov 17 and asked now the answer is A's figure, as of Nov 20 it is B's, and the revert re-extracts no
   segment (Index cost metric, section 14.3).
   Two cases test the mtime rule of section 6 (mtimes set with `os.utime`, no `--valid-from`, section 14.1):
   - **Notes edited after the call**: call X on Nov 12 at 15:00 UTC, its notes revision 1 with mtime Nov 12 16:10
     holding a staffing figure "4 engineers"; call Y on Nov 14 at 15:00; the PM edits the notes on Nov 15 at
     09:00 to "6 engineers" (revision 2). Replayed as of call Y's start, the answer is "4 engineers" and "6
     engineers" is a future leak; as of call X's start neither appears (the notes did not exist yet); asked now,
     "6 engineers" is current and dated Nov 15. Under the old rule (every notes revision at the call start)
     revision 2 starts Nov 12 15:00 and the replay as of Y returns "6 engineers".
   - **File edited later on the call day**: a folder file with revision 1 at mtime Nov 19 08:30 UTC ("pilot in
     January") and revision 2 at mtime Nov 19 16:45 ("pilot in March"); a call on Nov 19 at 12:00. Replayed as of
     that call, the answer is "January" and "March" is a future leak. Under the old day rule both revisions start
     at Nov 19 00:00, revision 1 has an empty validity and the replay returns "March".
5. **Modality**: "Did Globex agree to 39 hours?" where 39 hours was only proposed: the answer must carry
   `proposal`; a fact said only in generated notes must not count as confirmed.
6. **Exclusion and revocation**: one file excluded by the PM, one deleted file, one revoked document and one hidden
   call (its transcript and notes; its fact is not a register item, since register events of a hidden call stay
   searchable, section 5.1), each with a unique fact; none may ever appear, neither as a snippet nor as a
   claim, edge, entity name, alias or topic summary sentence. The topic summary that cites the fact is built
   **before** the exclusion, so the per-sentence filter of section 8.7 is what the gold tests. The bench passes the
   revoked document through `--revoked` and the hidden call through `--hidden-calls`, the same way the app does
   (section 11.6).
   Two more cases test that absence is not denial (section 11.2 step 1): a second deleted file whose fact is also
   stated in another file, and a call moved by `call_layout.migrate` between two passes whose fact is also stated in
   the register. Both facts must still be answered, quoted and dated by the surviving source (the moved call by its
   own source, which kept its id); the move re-extracts no segment.
   **Late client** (section 5.3, `needs: S2`): a third client folder (`docs/initech`) backed by no call and no
   sheet, and one question asked with the call's companies `["Initech"]`, so `decide()` makes Globex and Acme
   "other" only at query time. The answer must hold Initech's fact and none of the other two clients' unique facts
   (as a snippet, claim, edge, entity name, alias or topic sentence); the store is indexed before the question, when
   `labels(root)` does not yet know Initech.
7. **Scale**: a seeded noise generator adds about 2 MB of plausible project text (40 extra documents, 10 extra
   calls) so that ranking and latency are measured with distractors; the 10 KB set stays as the "small" set.

**Which gate evaluates which gold.** Every gold v2 row carries `needs: S2 | S3 | S4`, the first sub-task whose
code lets **C** answer it. A and B need no extraction and are evaluated on every row in every run, so the baseline
the S3 gate compares against ("at least 2 more than B") and the claim that A cannot pass by word match are
measured from S2 on. For C, a run before that sub-task records the row as `not_evaluated` with the missing part
named; it is never counted as passed or failed, and C's rates and zeros of section 14.3 are computed over its
evaluated rows only (the results JSON lists the counts of both).

| Gold (section 14.2) | Part | Evaluated from |
|---|---|---|
| 4 Time replay | snippets per period, the two mtime cases, register task state, supersede override, revert snippets | S2 |
| 6 Exclusion and revocation | snippets, register facts, entity names and aliases from deterministic sources; deleted and moved shared facts held by the register or a second register-backed source | S2 |
| 7 Scale | ranking, latency, leaks | S2 |
| 1 Cross-series and multi-hop | all | S3 (extraction fixture) |
| 2 Paraphrase and other language | all | S3 |
| 4 Time replay | file claims current or "changed later", revert re-extracts no segment | S3 |
| 5 Modality | all | S3 |
| 6 Exclusion and revocation | claims, edges, entity names and aliases from extracted facts; the deleted file's fact shared with another file | S3 |
| 3 Global | all | S4 |
| 4 Time replay | `summary_at` after `as_of` | S4 |
| 6 Exclusion and revocation | the topic sentence summarised before the exclusion | S4 |

**Gold freeze.** Gold v2 is frozen when S2 merges: its `sha256` is written to every results JSON, and the S3
extraction fixture records the gold hash it was recorded against. The S3 gate fails when the two differ, so the gold
is never tuned to what the model happened to find; a later gold change is a new version with a new fixture run.

### 14.3 New metrics (added to the results JSON, A and B report them too)

| Metric | Meaning |
|---|---|
| Hop answer rate | multi-hop questions (at least 6) with every needed quote in the top 6 |
| Paraphrase answer rate | paraphrase and cross-language questions answered in the top 3 |
| Global timeline recall | share of expected timeline facts returned by `ask_global`, and order correct (yes/no) |
| Evidence recall | gold facts returned as a claim or edge with a valid quote and locator |
| Quote validity | returned quotes that are verbatim at their locator (must be 100%) |
| Modality accuracy | modality questions with the expected modality; derived-only facts counted as confirmed (must be 0) |
| Future leaks | items with `said_at` or `summary_at` after `as_of`, items that sit on a revision with `valid_from` after `as_of`, and entity status, name, merge or claim and edge status values that come from a row dated after `as_of` (must be 0) |
| Excluded leaks | items (snippets, claims, edges, entity names, aliases, topic sentences) from excluded, hidden-call or revoked sources, and snippets or facts stated only by deleted sources (must be 0) |
| Shared facts kept | gold facts stated by a deleted or moved source and also by another source that are still answered (must be 100%) |
| Fixture hits / misses | C only: segments with and without a fixture file for their `seg_hash` (misses must be 0 on the small set) |
| Store p50 / p95 ms | `search(mode="live")` and `mode="prep"`, small and scale sets |
| Index cost | segments, tokens (deterministic count from the fixture), re-extracted segments after changing one section of one file (must be that section's segments only; the rest `cached`) and after reverting a file to an earlier text (must be 0) |

Existing columns stay: prep coverage, prep leaks, live answer rate, answer text, live leaks, lookup p50/p95,
duplicates, wrong identity, item 13 cross-client, false agreed, false proposal.

## 15. Questions for the owner (product choices only)

1. **Which model reads sources.** Options: (a) the cheapest model of the chosen route (Haiku, GPT-6 Luna, Gemini
   3.5 Flash-Lite) for extraction and the after-call model for topic summaries: $1.16 to $4.45 for a first index,
   typical to cap, in 2 to 5 passes (1 to 2 days at the daily cap); (b) the route's after-call model (Sonnet,
   GPT-6.1 Sol) for both: $9.38 to $21.06, which at the $3.00 daily cap takes **4 to 7 days** to finish. On the Codex
   plan either option takes 7 to 9 passes over **about 3 days** (68% to 84% of a window at 10% a pass and 30% a day). These figures
   are a **lower bound**: they assume one segment per 6,000 characters of a document, and a folder document with
   many short sections has one segment per section (section 8.2); the real count is shown before the first index.
   **Recommendation: (a)**; G0 `--extract` compares quality and we switch if the cheap model misses too much.
   **Owner decision 24.11: (a).** The cheapest model of the PM's route reads sources for extraction; the route's
   after-call model writes topic summaries.
2. **Consent before spending on a cloud route.** Options: (a) ask once per project before the first full index,
   showing the volume, provider and cost range (section 10), then run increments in the background without asking,
   under the per-pass and daily caps; (b) no prompt at all, the flag is consent enough; (c) local only for the first
   index, cloud for increments; (d) ask before every pass. **Recommendation: (a)**.
   **Owner decision 24.11: (b).** No consent prompt before the first cloud index: turning the flag `evidence_store`
   on is the consent. The per-pass, per-day and all-projects ceilings, the cost shown before and after a pass and the
   off switch stay. Where the flag is turned on, one plain line says that project text will be sent to the chosen AI
   provider for indexing and what the estimated first-index cost is (section 10). Section 12's "the first full index
   asks before it starts" is replaced by this line.

Team defaults, each a setting in section 10, not owner questions: $1.00 per pass and $3.00 per day on paid keys,
10% per pass and 30% per day of the 5-hour window on plans, 120 and 360 minutes on local, and the same day caps for
all projects together; at the ceiling wait for the next pass, local overflow off; topic summaries eager on the
after-call model (40 topics: about $0.73 on Sonnet or GPT-6.1 Sol, $0.27 on Gemini 3.8 Flash). With these defaults
and option (a), a first index of the typical project finishes in 2 passes on Claude key or OpenAI ($1.34 to $1.85,
$1.16 to $1.67), 2 to 5 passes (up to 2 days at the daily cap) on Gemini ($1.91 to $4.45), and in 7 to 9 passes
over about 3 days on the Codex plan.

## 16. Risks

- **Invented links** (V3's main risk): every edge needs a verbatim quote and a word of both ends, ambiguous ones stay
  suggestions, the G0 extraction fixture is raw model output (review may only delete), and `--extract` measures
  precision and the hop and paraphrase rates per route.
- **Output size drives cost**: the first-index range is wide ($0.47 to $4.31 for extraction on cheap models, $1.16
  to $4.45 with summaries on the `after` model) because output per segment and the catalog size are not measured
  yet; the per-request cap bounds it and S3 calibrates it before any number reaches the PM.
- **Synthetic gold proves correctness, not usefulness**: gains on the owner's real calls stay "not verified" until
  G3 runs on live calls (the plan's section 10 risk, unchanged).
- **Cost of the first index on plan routes**: about 68% to 84% of a Codex 5-hour window for a typical project (on an
  assumed anchor); the ceiling
  and the 80% stop keep it from blocking preparation, at the price of an index that takes days to complete.
- **Local model speed and quality**: not measured; S3 measures tokens per second on the owner's Mac class before the
  fallback is offered in the UI.
- **Two sources of truth for tasks**: the register stays canonical; the store only reads it. Where PM edits are
  written is the one rule in section 7.3.
- **Store size**: about 4,000 snippets for the typical project (3.87M characters / 900), about 8 MB of vectors plus
  text; fine for SQLite, watched with the scale set.
- **FTS5 or bge-m3 missing on a Mac**: fallbacks keep search working with lower quality; status says so.
- **Concurrency with the live call**: the extraction worker never runs while `call-active.json` exists (only the
  short local state-only and `override` writes do, which make no model, network or embed request); a crash that leaves the
  file stale is caught by the 6-hour and pid checks.
- **Scope drift**: the client of a call is known only at query time (`decide()` counts the call's own companies), so
  ingest stores marks over a label universe of every possible client folder and the search decides own / other per
  evidence row from the request's scope before ranking (section 5.3); chunks are cut on that universe so the
  per-snippet decision is line-exact (section 5.2), and ingest labels shape segments and topics only. A folder added after the last pass is a stale universe: non-live callers relabel first (free), the
  live reader keeps only the own client's folders (coverage loss for that call, never a leak).
- **Coverage loss across clients (follow-up task under GraphRAG, out of PB-52 scope)**: when client A's file and
  client B's file each state the same fact, LLM entity ids and `nkey` carry no client, so it becomes one claim with
  evidence from both; strict denial (section 11.2 step 1) then drops it for both clients. Nothing leaks, but each
  client loses a fact its own files support. The follow-up decides between a client-aware key for client-labelled
  evidence and per-client claim rows, and adds a gold row for it.

## 17. Implementation split

Each sub-task merges into the PB-52 integration branch behind the flag `evidence_store` (config `features`, off by
default; `LT_EVIDENCE=1` for tests), with tests, CHANGELOG (Unreleased) when user-visible, docs EN and UK, and
`rules/` plus the generated skill section where the contract changes. Each has a G0 gate: no existing column
regresses, its new columns meet the bar.

### S1. Store, deterministic ingest, search, CLI (core)

S1 is the largest sub-task. If it overruns, it may merge in two parts behind the same flag: S1a store and ingest
(schema, change detection with moves, state-only pass, CLI ingest), then S1b search with access, `as_of` and the
rest of the acceptance list below; the G0 gate applies to S1b.

Scope: `livetips_m0/evidence/` (schema, migrations, store open/lock and the global worker lock, ingest per source
type with change detection and client marks, deterministic entities and claims from the register, the Tracker
snapshot file reader, sheets, voice aliases, the state-only pass with hidden calls and revoked documents from the
passed lists and the append-only state-request log with request ids (apply at start, between segments and at
exit; readers deny pending hides and revokes), `d-`/`r-` ids from the dedupe key in `identity.py` with `notion:`
aliases from `<stem>.notion.json` and `decisions.md` lines keyed by date and title, `pm_override` from register
events with alias-key resolution), the segmenter of section 8.2 (deterministic, free, next to `chunks_at`, so S2's
fixture loader can compute `seg_hash`),
`client_scope.label_universe(root)` with `snippet_mark`, `entity_mark` and `alias_seen` (section 5.3),
`client_scope.labels(root)` for segments, topics and display only, `lookup.chunks_at` (offsets, `client_scope.HEADING`
on every line, no cut inside a line, the chunk invariant on the label universe) with the snippet split on an added slug
(section 5.2), snippet vectors at ingest through
the bundled `embed_server.py` with the copy of a text-equal snippet's vector (sections 5.2 item 3 and 7.6), content
time with full UTC `valid_from` and `said_at` (section 6), the write path `store.apply_extraction(revision_id,
items)` that S3 calls after validation (it writes `entity`, `alias`, `claim`, `edge` and `evidence` rows for
already validated items; S1 tests feed it hand-written items, which is how S1 tests claims and edges from files
without any model or segment cache), slug history for renamed projects,
`search` / `entity_card` with access (snippets, claims, edges, entities, aliases) and time filters including the
`as_of` derivation of state columns (including `supersede` overrides), the denied and ignored row rules with fields
from passing rows only, the CLI (`exclude`, `override`, `--hidden-calls`, `--revoked`, `--web-dirs` and, for tests
and the bench, `--valid-from`).

Every acceptance item below runs on S1 code alone: claims and edges of files come from `apply_extraction` with
hand-written items; nothing here needs the segment cache (S3) or topics (S4), and the checks that do are listed
under S3 and S4.

Acceptance:
- [ ] The store is created only under `<support>/evidence/<uid>.sqlite`; a test asserts nothing is written under the
      project root.
- [ ] Changing one file creates exactly one new revision; touching mtime without content change creates none; a
      deleted file is `deleted` and its snippets and unique facts are absent from search; register
      `migrate`/`rollback` triggers a refold.
- [ ] Deleted is ignored, not denied: a claim quoted in a deleted file and in another file is returned with only the
      other file's quote, dated and attributed by that row. A claim quoted in a revoked file and in another file is
      not returned (denied). The test fails if `deleted` is put in the denied class.
- [ ] Move: a file moved with unchanged content (a test runs `call_layout.migrate` on a flat call) keeps its source
      id, revisions, snippets, vectors, evidence rows and overrides, only `uri` changes, no new revision is built and
      no search between the two passes loses a fact; two identical new files for one vanished path are not a move
      (deleted plus two new).
- [ ] Revert: content A, then B, then A again gives three revision rows with their own validity; a replay in each
      period returns that period's snippets, and the third revision's snippets copy the vectors of the first (the
      embed server stub counts 0 requests for them).
- [ ] Content time (each fails on the old rule): (1) notes with revision 1 at mtime Nov 12 16:10 and revision 2 at
      mtime Nov 15 09:00, call X on Nov 12 at 15:00 and call Y on Nov 14 at 15:00: as of Y the store returns revision
      1's line and not revision 2's, as of X neither (the old rule started both at X's start); (2) a file with
      revision 1 at Nov 19 08:30 and revision 2 at Nov 19 16:45, replayed as of Nov 19 12:00: revision 1 only (the
      day rule started both at midnight); (3) a revision 2 whose mtime is older than revision 1's `valid_from` gets
      `observed_at`, and revision 1 keeps a non-empty validity; (4) a mtime after `observed_at` is cut to it; (5) a
      notes line unchanged between revisions keeps its `said_at`, a new line gets revision 2's `valid_from`; a
      transcript re-saved with renamed speakers keeps every line's `said_at`; (6) `valid_from` and `said_at` are
      stored with time of day, never as a day.
- [ ] Access tests: excluded, hidden call, a deleted file's unique fact, revoked (from the `--revoked` list: a Google picked copy by
      `<id16>` and a `_notion/` file by page id), another client's file, another client's line in a shared file, a
      claim with one out-of-scope evidence row, an entity known only from another client's files (no name, alias or
      graph step through it): none returned, and each case is filtered in the SQL candidate query (checked by a test
      that ranks nothing it was not allowed).
- [ ] `exclude --hidden-calls` makes no model, network or embed request and the hidden call is gone from the next
      `search`; with a full pass holding the lock, two requests during that pass are two log lines and both are
      applied before the pass's next segment and before the pass exits (also on `paused_call`); a pass killed with
      SIGKILL leaves its lines for the next writer, which applies them first; a re-sent `request_id` is a no-op;
      while a hide is pending, `search` (with no `exclude` from the caller, as the skill calls it) returns nothing
      of the hidden call (including the name of a decision known only from its `<stem>.notion.json`), and the same claim gives the same result before and after the request applies.
- [ ] Request `exclude` ignores rows: a claim quoted in an excluded file and in another file is returned with only
      the other file's quote, and its `said_at`, `said_by` and confirmations come from that row; a claim quoted only
      in excluded files is not returned.
- [ ] Decision ids: a decision ingested from the notes before `<stem>.notion.json` exists keeps the same `d-` id
      after the sidecar is written, with one entity, its claims unsplit and a `notion:<page id>` alias; a
      `decisions.md` line with the same title is only a `same_as` suggestion to it.
- [ ] Supersede: `evidence override --key notion:<old> --field supersede --value notion:<new>` (built by the test
      through the CLI, once with no store file yet and once before the sidecar is ingested) makes the call-keyed old
      decision `superseded` with a `supersedes` edge dated by the override's `at`; the decision is current in a
      replay before that date.
- [ ] Client parity at query time (section 5.3): on the G0 fixture and on the late-client project below, for every
      scope `decide()` returns (each client as own, with and without the call's companies), the set of non-blank
      line numbers covered by passing snippets equals the set that `client_scope.side()` and `filter_text` keep for
      that scope, both on the revision's normalised text; the scopes are taken from `decide()` at query time, never
      from `labels(root)`.
- [ ] Chunk invariant (section 5.2; fails on a `chunks_at` that cuts on `labels(root)` at client changes only): a
      shared file with L1 (no client), L2 (names Globex), L3 (no client) in one paragraph, and the same three lines
      under a heading with no blank line above it. For the Acme scope (others `["globex"]`) L1 and L3 are returned
      and L2 is not; for the Globex scope all three are returned. A unit check on `chunks_at` asserts no snippet
      holds two lines with different line mark sets or two heading paths.
- [ ] No cut inside a line (a leak control; fails on the fixed-width cut of `lookup.chunks`): a shared file with
      one 2,000-character line that names Globex only in its first 20 characters. For the Acme scope no character
      of that line is returned by `search`, as a snippet or as evidence.
- [ ] Universe change split: after a pass, `docs/initech` and `docs/acme-corp` are added; a shared line naming
      Initech and one naming "Acme Corp" each sit between two shared lines whose claims have evidence, in the current
      revision and in a superseded one. `mode="prep"` splits the snippets (no model, network or embed request), the
      neighbour claims keep their ids and evidence (re-anchored offsets), and each new client's line is denied for a
      scope with that client in `others` while its neighbours pass, also under an `as_of` that selects the
      superseded revision (fails on a finder through `snippet_fts`, which misses `acmecorp`).
- [ ] Late client (fails on a filter on ingest `client_set`, the named negative control): a project with
      `docs/acme` and `docs/globex`, Globex known from `live/2026-11-04-globex-weekly` calls, Acme backed by no call
      and no sheet, and a shared `pacenote/decisions.md` with one line that names only Globex and one that names
      only Acme, each in a paragraph with shared lines before and after it. Ingest runs first, so
      `labels(root)` at ingest knows only `globex`. Then `search` with the scope of `decide(root, "Kickoff",
      ["Acme"])` (client `acme`, others `["globex"]`) returns nothing from `docs/globex`, nothing from the Globex call,
      not the Globex line of `decisions.md`, and no claim, edge, entity, alias or name known only from them; the
      Acme folder's facts and the shared lines are returned. Second half: after an Acme sheet is recorded (so Acme is
      backed), a Globex call's scope (client `globex`, others `["acme"]`, a label `labels(root)` did not know at
      ingest) returns the Globex facts, nothing from `docs/acme` and not the Acme line of `decisions.md`. In both
      halves every shared line of `decisions.md` is returned (the line set equality of the parity test).
- [ ] Stale label universe: a client folder created after the last pass (its label not in `meta.label_universe`):
      `mode="prep"` relabels first (the snippet split of section 5.2; no model, network or embed request) and then applies
      the full rule; `mode="live"`
      returns only rows under the own client's folders and reports `scope_stale`.
- [ ] No access query reads `client_set` or `entity.client`: a test sets every `client_set` and `entity.client` to
      `''` after ingest and the access results of all access tests above are unchanged.
- [ ] Time tests on content time: a first index of files with past content times, replayed as of a past call, sees
      the files valid then; `as_of` before a revision or before a claim returns none of them, nor a decision or risk
      that revision adds (not in `entities`, no card); a claim whose evidence
      sits only on a replaced revision is not current; a task closed after `as_of` is open (register and Tracker), a merge after `as_of` is
      not applied, a decision superseded after `as_of` is current, and a PM rejection applies at every `as_of`.
- [ ] Edge modality (each sub-case fails on the old key `src_id|rel|dst_id`): "Globex blocks Northpoint" quoted as
      `proposal` in file 1 and as `confirmation` in file 2 gives two edge rows; `exclude` of file 2 returns only the
      proposal row, dated by file 1; `exclude` of file 1 returns only the confirmation row; revoking file 2 leaves
      the proposal row; a PM reject on the confirmation row (override key with `confirmation`) leaves the proposal
      row returned and survives a rebuild; as of a date between the two quotes only the proposal is returned.
- [ ] One edge found with the same modality in a shared file and in client A's file is one row; it passes for A and is dropped for client B
      (denied row, strict); an edge quoted only in shared files passes for both (section 11.2 step 1).
- [ ] A snippet under a nested subheading of a client heading carries that client's `heading` mark from the ancestor
      (heading path) and is denied for the other client's scope.
- [ ] PM edits from the register and `pm_override` rows on a claim and on an edge survive a rebuild (natural keys).
- [ ] Purge: after a source is revoked and vacuumed, no claim or edge is left without evidence, and none of its
      facts is returned.
- [ ] A register item written before a project rename joins one written after it.
- [ ] Two processes: a reader during a write sees the previous complete revision.

G0 gate: A's columns unchanged; the store unit tests above run on the current 10 KB G0 fixture and the late-client
project with leaks 0 (client, excluded, future); `mode="live"` p95 on that fixture at most 150 ms. (The new variants and gold come in S2, so S1
merges on its own without them.)

### S2. G0 gold v2, scale set, variants B and C (core)

Scope: `gold.yaml` v2 (section 14.2: at least 6 multi-hop questions, paraphrase and other-language questions, the
global timeline, the two revision states with a task closed and a decision superseded between them (the bench
writes the supersede through `evidence override --key notion:...`, with the decisions' `<stem>.notion.json`
sidecars in the fixture), the reverted file's three states, modality, the
excluded, deleted, revoked and hidden-call cases with a topic summary built before the exclusion, and the deleted
and moved sources whose facts other sources also state), the scale set's
seeded noise generator, VariantB and VariantC in `run.py` (deterministic part, with and without vectors), the new
metrics of section 14.3, the embed server start for the run with vectors (or `skipped` without the model
folder), and the extraction fixture loader keyed by content-only `seg_hash` from the S1 segmenter, with fixture hit
and miss counts (the fixture itself is recorded in S3).

Acceptance:
- [ ] `run.py --variant A|B|C` runs offline on the small and the scale set, blocks every host, and writes the new
      columns; A's existing columns are byte-equal to the last A result.
- [ ] Every gold v2 question states which variant is expected to pass it and why, so a later gain is attributable.
- [ ] The future-leak metric counts entity and claim state values from after `as_of`; the excluded-leak metric
      counts entity names, aliases and topic sentences.
- [ ] C reports fixture hits and misses; changing the chunker on a test copy turns hits into misses and fails the
      run as "fixture stale".
- [ ] The scale set is reproducible (same seed, same bytes) and its generated extraction is marked so no hop or
      paraphrase gate uses it.

- [ ] Every gold v2 row has `needs`; A and B are evaluated on every row; for C, a run without S3 or S4 code records
      items 1, 2, 3 and 5 and the S3 and S4 parts of items 4 and 6 (section 14.2 table) as `not_evaluated`, and C's
      rates are computed without them.
- [ ] The two mtime cases of item 4 run through `os.utime` and a normal pass, not `--valid-from`; the results JSON
      holds the gold `sha256`.

G0 gate: B on every row and C (deterministic part) over the rows marked `needs: S2` (section 14.2 table), on the
small and scale sets: prep and live leaks 0, future leaks 0 (including both mtime cases), excluded leaks 0, quote validity 100%,
evidence recall for register and Tracker facts 100%; A's columns unchanged; `mode="live"` p95 on the scale set at
most 150 ms.

### S3. LLM extraction, validation, routing, ceiling (core)

Scope: `rules/evidence.yaml`, `rules/evidence_schema.json`, generated `skill_evidence.md` and the skill copy;
prompt builder with cacheable prefix and the client-filtered catalog, validator (section 8.4, with the unanchored
`hedge_anywhere` words), the `evidence` routing job on all six routes with local fallback (Python `routing.JOBS` and
the `chain` entry of `rules/ai.yaml`), the per-request output cap with the split retry (section 8.6), the
segment cache (on the S1 segmenter) keyed by `(seg_hash, extractor)`, the worker (`evidence pass`) with the call-active check and the abort
of a request in flight, the pass, project-day and all-projects-day ceilings (USD, percent read after every request,
minutes), estimates priced per request on the model that sends it, run accounting, the extraction fixture for G0
(raw model output, deletions only, keyed by content) and `--extract <route>` with output-per-segment, catalog-size
and segments-per-document calibration.

Acceptance:
- [ ] Validator unit tests: a non-verbatim quote, a wrong line range, an unknown enum, a cross-client quote, a hedge
      word in the middle of a quote under `confirmation`, a derived `confirmation`, an override field, a rejected
      pair: each dropped or downgraded as specified.
- [ ] The catalog sent with a client's segment holds no entity or alias visible only to another client.
- [ ] A fake provider that returns oversized, invalid and slow answers: caps hold, the repair retry happens once,
      the pass ends `failed` after 3 errors.
- [ ] `call-active.json` present: no request is sent (fake provider counts 0) and the run ends `paused_call`.
      Created while a slow fake request is in flight: that request is cancelled within 2 seconds (the fake provider
      sees the disconnect, a fake CLI process group is killed), no further request is sent, the segment stays
      `pending` and the run ends `paused_call`.
- [ ] Ceiling: every request carries the output cap; the pass stops before the request that would cross the pass,
      the project-day or the all-projects-day cap when that request returns the full cap (a fake provider always
      answering at the cap never exceeds a USD ceiling; two projects together never exceed the global cap);
      `est_usd`/`usd`, `pct_before`/`pct_after` and `seconds` are filled per route kind; spend goes to
      `spend-ledger.jsonl` with job `evidence`.
- [ ] Re-running a pass with no changes sends 0 requests; editing one section of a large file (including a short
      section between two others, and the section right above another) sends only that section's segments, the rest
      are `cached`; a new `extractor` sends every segment again; reverting a file to an earlier text sends 0
      requests and asked now returns A's claims as current, written by the segment cache onto the third revision's
      snippets; a file moved with `call_layout.migrate` between two passes sends 0 requests; a notes file is cut like
      a transcript, not per section.
- [ ] The extraction fixture records the gold v2 `sha256` it was recorded against; a run whose current gold hash
      differs fails as "gold changed".
- [ ] A route whose cached-token count stays 0 is estimated without cache.
- [ ] The extract and summary model per route come from the new `evidence` map in `rules/ai.yaml` (section 8.6); a
      test changes the map and the next request uses the new name; without the map the worker falls back to
      `routing.PING_MODEL` and the `after` entry of `JOB_DEFAULTS`.

G0 gate: C with the extraction fixture, over the rows marked `needs: S2` or `S3` and with the fixture's gold hash
equal to the current one: hop answer rate at least 4 of 6 multi-hop questions and at least 2 more than
B (A and B: expected 0 to 2), fixture misses 0 on the small set, paraphrase answer rate not below B in the run with vectors (evaluated where the
model folder is present; `skipped` in CI) and above B in the run without vectors (B also has bge-m3, so with vectors both may already find the paraphrase; the Ukrainian question's
result without vectors is reported on its own line), modality accuracy 100%,
derived counted as confirmed 0, all S1 and S2 zeros still 0; `--extract` on one cheap route reports precision,
recall, its own hop and paraphrase rates, output tokens per segment, catalog tokens, segments per document and cost
(recorded, not gated; the measured values replace the 500-token typical output and the 2,000-token catalog
assumptions in the estimates
before S5 shows any number).

### S4. Topic summaries and global questions (core)

Scope: deterministic communities per client, summary requests on the `after` model inside the pass ceiling, summary
validation with claim citations, `topic_sentence` and per-sentence `topic_cite`, the per-sentence access filter,
`topic_fts`, `ask_global`, staleness by `member_hash` and by a change of state of a cited source.

Acceptance:
- [ ] Same input, same topics (seeded); no topic mixes two clients.
- [ ] A summary sentence without a valid claim citation is dropped; summaries never appear as evidence or as a
      confirmation.
- [ ] Only topics whose members changed, or whose cited sources changed state, are re-summarised.
- [ ] A topic summarised before one of its sources was excluded, revoked or hidden, or asked with that source in
      `exclude`, returns none of the sentences that cite it (even when the cited claim also has another passing
      row) and still returns its other sentences.
- [ ] A sentence citing a claim with one evidence row in a deleted file and one in another file is not returned
      until the topic is re-summarised (ignored rows are strict for sentences, section 8.7), and after the
      re-summarise the new sentence is returned; the same holds for a claim whose file is in the request's `exclude`.
- [ ] `ask_global` with `as_of` never returns a summary written after `as_of`.

G0 gate: every gold v2 row evaluated (no `not_evaluated` left); global timeline recall at least 75% with correct
order on the global gold, future leaks 0, excluded leaks 0 (including topic sentences), everything from S1, S2 and
S3 unchanged.

### S5. App integration: flag, project uid, scheduler, settings and cost text

Scope: Swift: `evidence_uid` in `projects.json` with the slug passed on every pass (slug history), `LT_PROJECT_UID`,
`call-active.json` written at call start and removed at the end, the Tracker snapshot file
(`tracker-snapshot/<uid>.json` in the state folder next to `ai-routes.json`, from the Notion and HubSpot items the
app already holds), the lists passed to each pass (hidden calls from `history.json`; Google and Notion documents with
status `denied` or `not_found`; stored page folders under `SheetCLI.stateDir/web` for this project's sheets), the
state-only pass on hide, unhide and loss of access, at once also during a call, each with a `--request-id` kept
until `evidence status` lists it applied and re-sent otherwise (section 13.1), the supersede click in
`CallPage.swift` running `evidence override --key notion:<old> --field supersede --value notion:<new>` after `decision_notion.py --supersede` returns ok (section 7.1), triggers (after notes, folder change debounce, idle hourly, "Index now"), the `evidence` job in Swift
`AIChain` (`allowed`, Settings list, stepping only to the model on this Mac; the `evidence` parameter of
`ComponentCatalog.afterRemovingLocal` with its three call sites, section 8.6), the settings row with the all-projects
day cap and the first-index confirmation with the texts of section 10 (en and uk i18n), delete per project, status
line; docs EN and UK (including that G2 searches stored site pages); CHANGELOG.

Acceptance:
- [ ] Flag off: no store file is created, no worker runs, no setting is shown (test and `swift build`).
- [ ] Project rename and folder change in the app keep the uid; `Projects.merge(imported:)` keeps the uid of the
      project it matches by slug; an import whose slug changed, and a folder removed and re-added, create a new uid
      and list the old store as orphaned (section 13.4).
- [ ] `call-active.json` exists for the whole call and is removed after it, including after a crash on the next
      launch.
- [ ] Hiding a call on Home starts the state-only pass at once (also during a call) with a request id, and an id
      not listed as applied after the pass is re-sent (pure-logic test on the request tracker); a supersede click writes the override only after Notion
      returned ok (pure-logic test on the command builder); a Google document turning
      `denied` does the same with `--revoked`; the Tracker snapshot file is written only from data the app already
      has (no new request; pure-logic tests on the list builders and the writer).
- [ ] `AIChain` accepts the `evidence` job and never steps it to another cloud route (pure-logic test).
- [ ] The cost text before and after a pass shows the numbers from the `run` row as a range (typical to cap), using
      the S3-calibrated output per segment (pure-logic tests on the formatter); screenshots only through the harness
      gate (L33).

G0 gate: no change to any G0 column (the app wiring is not on the bench); `CoreChecks` and `swift build` green.

S5 as built (PB-88): the flag is `features.evidence_store` in the app's `config.json`, the file the CLI reads through
`--config` (the app never sets `LT_EVIDENCE`); the store is passed `LT_SUPPORT_DIR` = Application Support so it is
never under the work folder the app root may sit in; `evidence_uid`, `features`, the `evidence` route and the
ceilings are written to `projects.json` and `config.json` only once the flag is on or the PM changes them. The
ceilings reach the worker as `LT_EVIDENCE_LIMITS` (JSON: `pass`, `day`, `all_day` with `usd`, `pct`, `min`, plus
`topics`, `extract`, `local_overflow`) for S3 to read. The cost text shows numbers only from an `estimate` object in
`evidence status` with `calibrated: true` (`typical`, `cap`, `unit`, `parts`) and the `run` row under `last_run`;
S3 adds both. With neither, every estimate line says it is not available yet. The first-index dialog is not built
(owner decision 24.11, section 15).

Order: S1 -> S2 -> S3 -> S4; S5 can start after S1 (it needs only the CLI and the flag), shows cost numbers only after
S3's calibration, and must land before the flag is offered to the owner. S1 and S2 go first because they make every
later gate measurable; S1 is split from S2 so the store merges without waiting for the new gold.

## Appendix A. Cost model script

Saved as `cost_model.py` anywhere outside the repo and run with `python3 -I cost_model.py 500` (typical output)
and `python3 -I cost_model.py 2000` (the per-request output cap); it prints the numbers of section 9. Inputs:
`cost.py` prices, `rules/ai.yaml` characters per token, the 4% Codex anchor with its assumed 250K size
(`CODEX_ANCHOR`), and the assumed project shape of section 9.

```python
import sys

SEG = 6000; PREFIX = 1500; CATALOG = 2000  # entity catalog per segment, section 9
OUT = int(sys.argv[1]) if len(sys.argv) > 1 else 500   # output tokens per segment; 2000 = the max_tokens cap
CYR = 0.5
CPT = {"api": (2.5, 1.6), "codex": (4.0, 2.6), "openai": (4.0, 2.6), "gemini": (4.0, 2.8), "local": (3.8, 2.4)}
CODEX_ANCHOR = 250000  # assumed size of the plan doc's "one Codex preparation = 4% of the window"


def toks(route, ch):
    lat, cyr = CPT[route]
    return ch * (1 - CYR) / lat + ch * CYR / cyr


def ceil(a, b):
    return -(-a // b)


def plan(calls, tx, notes, docs, doc_ch, web, state, topics):
    ch = calls * (tx + notes) + docs * doc_ch + web + state
    segs = calls * ceil(tx, SEG) + calls * ceil(notes, SEG) + docs * ceil(doc_ch, SEG) + ceil(web, SEG) + ceil(state, SEG)
    return ch, segs, topics


FULL = plan(40, 45000, 4000, 150, 12000, 15 * 5000, 30000, 40)
WEEK = plan(3, 45000, 4000, 10, 12000, 0, 5000, 6)
PRICES = {  # route, input, cache read, output (USD per 1M tokens, cost.py)
    "api haiku-5-5": ("api", 0.10, 0.01, 0.50),
    "api sonnet-5-5": ("api", 2.0, 0.10, 10.0),
    "openai gpt-6-luna": ("openai", 0.10, 0.01, 0.50),
    "openai gpt-6.1-sol": ("openai", 2.0, 0.10, 10.0),
    "gemini 3.5-flash-lite": ("gemini", 0.30, 0.03, 2.50),
    "gemini 3.8-flash now": ("gemini", 0.75, 0.075, 3.75),
    "gemini 3.8-flash 2027-01-01": ("gemini", 1.50, 0.15, 7.50),
}


def parts(route, ch, segs, topics):
    fresh = toks(route, ch) + segs * CATALOG + topics * 6000
    cached = segs * PREFIX + topics * PREFIX
    out = segs * OUT + topics * 600
    return fresh, cached, out


print("output tokens per segment: %d" % OUT)
for name, (ch, segs, topics) in (("FULL", FULL), ("WEEK", WEEK)):
    print("== %s: %s chars, %d segments, %d topic summaries" % (name, format(ch, ","), segs, topics))
    for r in ("api", "codex", "gemini", "local"):
        f, c, o = parts(r, ch, segs, topics)
        print("  tokens %s: fresh in %.3fM, cached prefix %.3fM, out %.3fM, total %.3fM" % (r, f / 1e6, c / 1e6, o / 1e6, (f + c + o) / 1e6))
    for k, (r, pi, pc, po) in PRICES.items():
        f, c, o = parts(r, ch, segs, topics)
        usd = (f * pi + c * pc + o * po) / 1e6
        nocache = ((f + c) * pi + o * po) / 1e6
        print("  %s: $%.2f (no cache $%.2f; output share %.0f%%)" % (k, usd, nocache, 100 * o * po / 1e6 / usd))
    f, c, o = parts("codex", ch, segs, topics)
    print("  codex plan: %.2fM tokens = %.0f%% of a 5-hour window at 4%% per %dK (assumed)" % ((f + c + o) / 1e6, (f + c + o) / CODEX_ANCHOR * 4, CODEX_ANCHOR // 1000))
    f, c, o = parts("local", ch, segs, topics)
    for pre, gen in ((300, 15), (600, 30)):
        print("  local gemma4:26b at %d tok/s prefill, %d tok/s out: %.1f h" % (pre, gen, ((f + c) / pre + o / gen) / 3600))
```

The second script (`python3 -I extra.py`) gives the correction under the section 9 table (topic summaries priced
on the `after` model), the days to finish at the daily cap and the Codex passes at the per-pass ceiling. The table rows it starts
from are the outputs of the first script.

```python
T, FRESH, PRE, OUT = 40, 6000, 1500, 600  # 40 topic summaries: fresh input, cached prefix, output tokens
SUM = {"sonnet-5-5": (2.0, 0.10, 10.0), "gpt-6.1-sol": (2.0, 0.10, 10.0),
       "gemini-3.8-flash now": (0.75, 0.075, 3.75), "gemini-3.8-flash 2027": (1.50, 0.15, 7.50),
       "haiku-5-5": (0.10, 0.01, 0.50), "gpt-6-luna": (0.10, 0.01, 0.50), "gemini-3.5-flash-lite": (0.30, 0.03, 2.50)}
s = {k: T * (FRESH * pi + PRE * pc + OUT * po) / 1e6 for k, (pi, pc, po) in SUM.items()}
for k, v in s.items():
    print("summaries %s: $%.4f" % (k, v))
rows = {"haiku-5-5 (no cache)": ((0.65, 1.16), "haiku-5-5", "sonnet-5-5"),  # first-script rows, typical to cap
        "gpt-6-luna": ((0.47, 0.98), "gpt-6-luna", "gpt-6.1-sol"),
        "gemini-3.5-flash-lite": ((1.77, 4.31), "gemini-3.5-flash-lite", "gemini-3.8-flash now")}
for k, ((lo, hi), cheap, after) in rows.items():
    d = s[after] - s[cheap]
    print("first index %s + after-model summaries: $%.2f to $%.2f" % (k, lo + d, hi + d))
week = {"haiku-5-5 (no cache)": ((0.05, 0.09), "haiku-5-5", "sonnet-5-5"),  # 6 topics a week, section 9
        "gpt-6-luna": ((0.04, 0.07), "gpt-6-luna", "gpt-6.1-sol"),
        "gemini-3.5-flash-lite": ((0.14, 0.32), "gemini-3.5-flash-lite", "gemini-3.8-flash now")}
for k, ((lo, hi), cheap, after) in week.items():
    d = (s[after] - s[cheap]) * 6 / T
    print("week %s + after-model summaries: $%.2f to $%.2f" % (k, lo + d, hi + d))
for k, lo, hi, cap in (("Sonnet $", 10.89, 21.06, 3.0), ("GPT-6.1 Sol $", 9.38, 19.55, 3.0), ("codex %", 68, 84, 30)):
    print("days at the daily cap, %s: %.1f to %.1f" % (k, lo / cap, hi / cap))
for k, lo, hi, per in (("codex %", 68, 84, 10),):
    print("passes at the per-pass ceiling, %s: %d to %d" % (k, -(-lo // per), -(-hi // per)))
```

## Підсумок українською

1. PB-52 (GraphRAG G2): локальне сховище доказів у SQLite в Application Support, окремий файл на проєкт за новим
   `evidence_uid`, ніколи в папці проєкту, за прапорцем `evidence_store`.
2. Схема: джерела з ревізіями (sha256 тексту), уривки з локаторами, сутності з ідентичністю G1, твердження з
   модальністю, ребра з цитатами, аліаси, правки PM, теми з підсумками, проходи з вартістю.
3. Спершу детерміновані звʼязки (реєстр, знімок Tracker від застосунку, люди, шити), потім LLM-екстракція з усіх
   джерел у фоні, ніколи під час дзвінка (файл `call-active.json`; запит у польоті переривається), інкрементально з
   кешем на сегмент за змістом (сегмент не перетинає заголовок); приховані дзвінки і документи без доступу
   виключаються одразу окремим проходом без моделі (під час повного проходу запит з id дописується в журнал, а пошук
   уже вважає ці джерела закритими) й ніколи не йдуть у модель.
4. Контракт промпту і схема виходу в `rules/evidence.yaml` і `rules/evidence_schema.json`, спільні зі скілом;
   валідатор перевіряє дослівність цитат, клієнта, модальність, правки PM.
5. Модальність: proposal / expectation / confirmation / statement, мапінг зі значень G1; модальність входить у
   ключ твердження і ребра, тож пропозиція і підтвердження одного звʼязку це два рядки з власними доказами; підсумки й нотатки ніколи
   не рахуються незалежним підтвердженням.
6. Пошук: доступ, клієнт (PB-70: при індексації лише позначки назв папок у шляху, заголовках і рядках, а свій чи
   чужий клієнт вирішується під час запиту за scope цього дзвінка, тож клієнт, відомий лише з компанії дзвінка, теж
   відсікається; мітки індексації ніколи не вирішують доступ; уривок тримає лише рядки з однаковим набором назв
   папок і одним шляхом заголовків і ніколи не ріже рядок, тож рішення на уривок точно збігається з `filter_text`
   по рядках) і час (`as_of` за часом змісту з точністю до секунди
   в UTC: ревізія 2 і далі від mtime, а не від дня чи початку дзвінка, тож пізніша правка не потрапляє в реплей;
   статуси й злиття теж на момент `as_of`) фільтруються в SQL до ранжування, також для сутностей, аліасів і кожного речення підсумку
   теми; виключення на запит і видалений файл лише ігнорують рядки доказів цих файлів (факт, який підтверджує
   інше джерело, лишається), а прихований дзвінок, документ без доступу і чужий клієнт закривають твердження
   повністю; переміщений файл з тим самим вмістом зберігає своє джерело; id рішення завжди з ключа дедупу, id сторінки
   Notion лише аліас; заміну рішення PM записує локально з датою;
   правки PM за природними ключами переживають перебудову; режим live без нових викликів.
7. Вартість першого індексу типового проєкту (оцінка, від типового виходу до ліміту 2000 токенів): $1.16-4.45 з
   дешевою моделлю для екстракції і моделлю after для підсумків, $9.38-21.06 на Sonnet або GPT-6.1 Sol (4-7 днів
   за денної стелі), 68-84% 5-годинного вікна Codex (7-9 проходів, близько 3 днів), 5-29 годин локально; усе з
   каталогом сутностей 2000 токенів.
8. Стеля на прохід, на день для проєкту і на день для всіх проєктів разом у долари, відсотки ліміту або хвилини,
   показ вартості до і після, вимикач і видалення.
9. G0: варіанти B і C з однаковими кандидатами, фікстура екстракції як сирий вихід моделі, складніший еталон (6+
   питань на кілька кроків, перефразування, глобальні, реплей у часі з двома випадками за mtime, модальність,
   виключення, масштаб); еталон заморожується з хешем до запису фікстури.
10. Розбиття: S1 сховище і пошук, S2 еталон G0 v2 і варіанти B/C, S3 екстракція і стеля, S4 теми і глобальні
    питання, S5 інтеграція в застосунок; кожен рядок еталону позначає, з якого S його оцінюють, і кожен S
    перевіряється лише своїм кодом;
    2 питання до Bohdan: яка модель читає джерела, згода перед першим хмарним індексом.
11. Рішення власника 24.11 (розділ 15): (1) варіант (a), найдешевша модель маршруту PM читає джерела, модель після
    дзвінка пише підсумки тем; (2) варіант (b), окремої згоди перед першим хмарним індексом немає, увімкнення
    прапорця `evidence_store` і є згодою. Стелі на прохід і на день, вартість до і після та вимикач лишаються; там, де
    прапорець вмикають, один рядок каже, що текст проєкту піде до обраного провайдера ШІ для індексації, і яка
    орієнтовна вартість першого індексу (до калібрування S3: "поки недоступна").
