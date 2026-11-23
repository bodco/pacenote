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

- `auto_sources` takes the last 3 earlier calls of the same series (`sheet/sources.py` `previous_calls(...)[:3]`),
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
| `notes` | `<stem>.notes.md`, `.app.notes.md` | text | derived (model-written); a line the PM edited is `pm` | rel path + heading + line range | yes, marked derived |
| `brief`, `sheet` | `.brief.md`, `.sheet.md`, `.sheet.json` | text / JSON | derived | rel path + block id or line range | no (deterministic facts only) |
| `register` | `live/commitments.jsonl` | `commitments.fold()` | pm / deterministic | rel path + event line number + event id | no |
| `tracker` | the Notion and HubSpot tasks the app already holds in Swift (`CRMTasks.swift`, `OrgRegisters.swift`), exported by the app to a snapshot file (section 5.2, item 5) | snapshot file | primary (system of record) | `notion:<page id>` or `crm:<portal>:<task>` + field | no |
| `project_state` | `pacenote/decisions.md`, `_notion/... Current State.md`, `pacenote/answer-cards.md` | text | primary (decisions) / derived (Current State is written by a skill) | rel path + heading + line range | yes |
| `web_page` | `<live>/<date>-<slug>/.work/web/<host>/*.txt` and, for a sheet without a call folder, `<SheetCLI.stateDir>/web/<host>/*.txt` (G6, `MeetingSheetDraft.swift:279`; title line, URL line, body); the app passes the second location's folders of this project's sheets with each pass (S5) | text | primary (external) | URL + stored path + char range | yes |
| `gdoc` / `notion` copies | `live/google/google-file-<id16>.<ext>`, `_notion/` mirror files | as `file` | primary | as `file` + original URL | yes |

The column "origin" is what the confirmation rule (section 7.4) counts on. Git commits and the 14-day digest stay
prep-only sources and are not ingested. `sheet/tracker.py` `collect()` is not a source of its own: it folds the same
local commitments registers (`CM.load`) that the `register` kind already reads, so ingesting it would duplicate
every task.

**Hidden calls.** A call the PM hid on Home (`CallHistory.Record.hidden`, `HomeData.swift:274`, kept in
`history.json` in Application Support) is never extracted and never searched, like `rules/decisions.yaml` says
for decisions ("Never for a hidden call"). Its transcript, notes, brief and sheet get `source.state = excluded` with
`state_reason = "hidden call"`. The worker cannot read `history.json` itself, so the app passes the list of hidden
call files with each pass (S5). Hiding or unhiding a call starts an **immediate state-only pass** (`evidence
exclude --hidden-calls ...`): it only sets `source.state` and marks the topics that cite those sources `stale`
(section 8.7), makes no model, network or embed request and takes well under a second, so the call stops being
searchable at once. During a call the worker does not run (section 8.5), so a hide made during a call is applied by
that state-only pass right after the call ends; until it has run, every `search` the app or the skill starts passes
the hidden call's files in `exclude` (section 11.1), so the hidden call never appears in between. Unhiding moves the
sources back to `active`.

The docs say a page under `.work/web` is "not searched"; with the flag on, G2 changes that for the store (the pages
stay out of the folder lookup and the call files list). S5 updates the docs EN and UK.

### 5.2 Change detection

No content hashing exists in the app today (`file_day` uses the date in the name or mtime). G2 adds it:

1. Pre-check per path: `(mtime, size)` against the active revision. Equal: nothing to do.
2. Otherwise read and normalise the text (the same reader the prep uses, line endings normalised, trailing spaces
   dropped) and take `sha256` of it. Equal hash: update `mtime`/`size` only, no new revision.
3. New hash: a new revision is built in one transaction (snippets, deterministic entities and claims), marked
   `extract_state = pending`; the previous revision gets `superseded_at`. Readers keep seeing the previous complete revision
   until the transaction commits.
4. The register is append-only: the store keeps the byte offset of the last read line and reads only new lines. An
   `op migrate` or `op rollback` line (G1) triggers a full refold of the register.
5. The Tracker: the worker makes no Notion or HubSpot request (section 12). The app writes what it already fetched
   for its own Tracker view to `tracker-snapshot/<evidence_uid>.json` in the state folder next to `ai-routes.json`
   (`LT_AI_STATE` is the path of that file, `routing.default_state_path()`) (items with id, text, status,
   owner, due, `last_edited`, project), at most once per pass trigger (S5). The worker hashes the normalised item
   list and diffs item by item. No snapshot file yet, or an older one, keeps the last ingested state with its
   `observed_at`; a missing file is never treated as "all items deleted". Tracker ids already present on register
   items (`identity.tracker_id`: `notion:` and `crm:` links) are linked from the register alone, without the
   snapshot.
6. A file that disappears gets `state = deleted`. **Revoked**: the worker makes no Google or Notion request, and the
   picked copy `live/google/google-file-<id16>.*` stays on disk after access is lost, so the signal comes from the
   app. The prep already knows each Google source's `google.status: ok | denied | not_found` (`sheet/sources.py`,
   the `access` field), and the app knows the Notion pages it can no longer read. The app passes the list of
   `denied` and `not_found` documents (Google file id, Notion page id) with each pass, the same way it passes hidden
   calls (S5), and a document's change to `denied` or `not_found` starts the same immediate state-only pass as a
   hide (section 5.1). The worker maps each id to its sources (the picked copy by `<id16>`, the `_notion/` mirror file
   by page id) and sets `state = revoked`; a later `ok` sets it back to `active`. Deleted and revoked sources stay in
   the store for history and audit but never reach search (section 11.2). Claims that rest only on such a source are
   hidden with it.
7. Transcripts of the call in progress are never read: a transcript whose call is active (section 8.5), or that was
   modified in the last 10 minutes, waits for the next pass.

8. Hidden calls: a source of a call in the hidden list the app passed is set to `excluded` ("hidden call") before
   any extraction of it (section 5.1).

Snippet size: the rules of the `FolderIndex` chunker (`lookup.chunks`, paragraphs merged up to 900 characters, the
last heading kept, paragraphs over 1800 characters split), in a new variant `lookup.chunks_at` owned by S1 that
differs in two ways:

- it returns the character and line range of every chunk in the normalised text (`lookup.chunks` returns only the
  text, so it cannot give locators);
- it also cuts at client boundaries: a heading section that names a client (by `client_scope.text_side` against the
  project's labels, section 5.3) starts a new chunk, and a line that names a client other than the one of the
  current chunk ends it. So one snippet never holds the lines of two clients unless one line names both.

Variant A keeps `lookup.chunks` unchanged. B and C use `chunks_at`; the chunk boundaries therefore differ slightly
from A, which the G0 report states next to the A versus B numbers (B exists to show the effect of more context, so
A versus B includes the chunker change, and B versus C isolates the graph). Extraction uses bigger segments
(section 8.2) made of whole snippets, so every quote maps back to snippets.

### 5.3 Client label at ingest

The PB-70 functions (`side`, `text_side`, `item_side`) answer `own`, `other` or None relative to one call's scope from
`decide()`, and `decide()` returns None for a call without a backing company or title. There is no project-wide list
of clients today. S1 adds one:

- **`client_scope.labels(root)`**: every client label of the project, built from the same parts `decide()` uses
  (`_client_dirs` with the hints from `_series_words` and `_recorded_companies`), without picking an own client. It
  returns `{label: [rel paths]}`, empty when the project has fewer than 2 client folders.
- A label check at ingest builds a neutral scope per label (`{"client": label, "others": [all other labels], ...}`)
  and asks the existing `side`, `text_side` and `item_side`, so the store uses PB-70's rules, not a copy of them.

Each snippet gets `client_set` at ingest: the client folder it sits in, the client its heading section names, the
clients its lines name, or empty (shared). Because `chunks_at` cuts at client boundaries (section 5.2), a snippet holds
at most one client's lines plus shared lines, except a line that names two clients, which keeps both. Claims and
edges inherit the union of their evidence snippets' labels. When `labels(root)` changes, labels are recomputed
deterministically (no model call).

The search filter (section 11.2) uses the call's scope: a row passes when `client_set` is empty, contains
`scope.client`, or has no label in `scope.others`. This is the same outcome as `client_scope.filter_text` (a line that
names only another client never passes; a line that names the own client passes even if it names another), and S1
has a parity test: on the G0 fixture, the snippets that pass for a scope cover exactly the lines `filter_text`
keeps for that scope.

## 6. SQLite schema

One file per project, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `user_version` = schema version. All times are
UTC ISO strings. Two clocks, used for different things:

- **Content time** (`revision.valid_from`, `snippet.said_at`, `claim.said_at`): when the content was said or
  written. Transcript and its notes: the call start (plus the line's offset for `said_at`). File: the newer of the
  date in its name and the mtime of that revision (the `file_day` rule of `sheet/sources.py:752`, applied per
  revision with the mtime recorded at ingest, not the file's current mtime). Tracker item: `last_edited`. Register
  event: its `at`. Replay (section 11.2) runs on content time only.
- **Observation time** (`revision.observed_at`, `source.first_seen`): when Pacenote first saw it. Audit and
  "new since the last pass" only; never a replay filter (on a first index every revision would be observed "now",
  and a replay of an older call would see no files at all).

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, rules_version, evidence_uid, root_hint, slug_hint, created_at, flag_state

CREATE TABLE source (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,          -- file, transcript, notes, brief, sheet, register, tracker, project_state, web_page
  uri           TEXT NOT NULL UNIQUE,   -- rel path, notion:<id>, crm:<portal>:<task>, https://...
  title         TEXT,
  origin        TEXT NOT NULL,          -- primary, derived, pm
  call_key      TEXT,                   -- commitments.series_key + call file for call sources
  state         TEXT NOT NULL DEFAULT 'active',  -- active, excluded, deleted, revoked
  state_reason  TEXT,                   -- "skipped by you", "file deleted", "no longer shared", ".livetipsignore"
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
  valid_from     TEXT NOT NULL,         -- content time of this revision (see above); replay filter
  valid_to       TEXT,                  -- valid_from of the next revision; NULL = current content
  observed_at    TEXT NOT NULL,         -- audit only
  superseded_at  TEXT,                  -- observation time of the next revision; NULL = active revision
  extract_state  TEXT NOT NULL DEFAULT 'pending',  -- pending, done, failed, skipped_off, not_needed
  extractor      TEXT,                  -- rules version + prompt hash used
  UNIQUE (source_id, content_hash)
);
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
  client_set   TEXT NOT NULL DEFAULT '',-- '' shared, 'acme', 'acme|globex'
  vec          BLOB,                    -- bge-m3 1024 float16, computed at ingest (section 7.6), NULL until then
  UNIQUE (revision_id, ord)
);
CREATE INDEX snippet_rev ON snippet(revision_id);
CREATE VIRTUAL TABLE snippet_fts USING fts5(text, heading, content='snippet', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2');

CREATE TABLE entity (
  id          TEXT PRIMARY KEY,         -- c-<sha1:10> (G1 commitment), d-/r-<sha1:10>, person:<casefold>, me,
                                        -- client:<slug>, e-<sha1(type|norm name):10> (section 7.1)
  type        TEXT NOT NULL,            -- person, client, task, decision, risk, topic, document, system, call, date_event
  name        TEXT NOT NULL,            -- cache of the current name (section 11.2 step 2 derives it under as_of)
  client      TEXT NOT NULL DEFAULT '', -- home client label of a deterministic entity; '' for llm entities
  status      TEXT,                     -- cache of the current status: open, proposal, done, dropped (tasks)
  origin      TEXT NOT NULL,            -- g1, tracker, voices, llm, pm
  merged_into TEXT REFERENCES entity(id),  -- cache of the current merge
  merged_at   TEXT,                     -- `at` of the register event or pm_override that merged it
  pm_locked   INTEGER NOT NULL DEFAULT 0,
  created_run INTEGER REFERENCES run(id),
  updated_at  TEXT NOT NULL
);
CREATE INDEX entity_type ON entity(type, client);

CREATE TABLE alias (
  alias_norm  TEXT NOT NULL,            -- casefolded, whitespace collapsed
  entity_id   TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  lang        TEXT,                     -- en, uk, ''
  origin      TEXT NOT NULL,            -- voices, g1, tracker, llm, pm
  status      TEXT NOT NULL,            -- confirmed, suggested, rejected
  client_set  TEXT NOT NULL DEFAULT '', -- labels of the snippets the alias was found in; filtered like a snippet
  vec         BLOB,
  PRIMARY KEY (alias_norm, entity_id)
);
CREATE INDEX alias_entity ON alias(entity_id);

CREATE TABLE claim (                    -- a statement about one entity
  id          INTEGER PRIMARY KEY,
  subject_id  TEXT NOT NULL REFERENCES entity(id),
  predicate   TEXT NOT NULL,            -- status, due, owner, decided, risk_level, waiting_for, value, said
  value       TEXT,
  modality    TEXT NOT NULL,            -- proposal, expectation, confirmation, statement
  said_by     TEXT REFERENCES entity(id), -- of the earliest evidence row; each row keeps its own (evidence.said_by)
  said_at     TEXT NOT NULL,            -- earliest evidence said_at; timelines date each quote by its own row
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
  first_said_at TEXT NOT NULL,
  last_said_at  TEXT NOT NULL,
  run_id        INTEGER REFERENCES run(id),
  UNIQUE (src_id, rel, dst_id)          -- one row per relation; the same edge found in a shared and a client
);                                      -- file adds evidence rows, not a second edge
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
                                        --   entity: its id; alias: alias_norm|entity_id;
                                        --   claim: claim.nkey;
                                        --   edge: src_id|rel|dst_id
  field      TEXT NOT NULL,             -- status, name, merge, split, reject, confirm, value
  value      TEXT,
  at         TEXT NOT NULL,
  via        TEXT NOT NULL              -- register, app, skill
);
CREATE INDEX pm_override_target ON pm_override(target, target_key);

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
  transaction as the purge), and the search never relies on "every evidence row passes" alone: it also requires at
  least one passing row (section 11.2). A claim whose only evidence came from a revoked source therefore disappears,
  it is never shown without a quote.
- **Overrides by natural key.** Claim and edge row ids are reassigned by a rebuild; `pm_override.target_key` uses
  the natural keys above, so a PM edit re-applies to the same claim or edge after a rebuild. An override whose key no
  longer matches anything stays stored (orphaned) and applies again if extraction finds the same statement later.
- `entity.merged_into` records a confirmed merge (alias of a G1 `same`, or a PM merge). Readers follow it one step.
- **Columns that hold "now".** `entity.name`, `entity.status`, `entity.merged_into` and `claim.status` / `edge.status`
  are caches of the current state, used only without `as_of`. Under `as_of` they are derived, never read (section
  11.2 step 2), so they need no history columns and a rebuild gets them right for free.
- **Client labels live on evidence.** `claim.client_set`, `edge.client_set` and `alias.client_set` are caches for
  display and the topic split; access is decided on the evidence snippets' own labels (section 11.2 step 1), so one
  edge found in a shared file and in a client file is one row whose client evidence passes only for that client.
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
- **Decisions and risks**: G1 has no id for them, but the app already keys them. G2 adds `d-` and `r-` ids in this
  order: the Notion page id kept in `<stem>.notion.json` (`{"decisions": {key: {"id", "url"}}, "risks": {...}}`)
  when the item was sent to Notion; otherwise the dedupe key of `rules/decisions.yaml` (the call file plus the
  normalised text), hashed; then, across calls, the G1 key functions (`identity.tracker_id`, `ref_of`,
  `scope_match`) propose a join as a suggestion only. New code in `identity.py`, owned by S1.
- **Superseded decisions**: the PM's click in `decision_notion.py` (`--supersede`, line 278) sets Status Superseded
  with the Supersedes and Superseded By relations. The store reads it as `status = superseded` on the old decision
  and a `supersedes` edge (new -> old, `origin = pm`). A superseded decision is never returned as current; it is shown
  only in timelines and entity cards. Extraction never sets `superseded` on its own.
- **People**: `identity.person_id` (the voice library aliases, `me` for the PM), as `person:<casefold>`.
- **Clients**: `client:<slug>` from the client folders `client_scope.decide` finds.
- **Everything the LLM finds** (topics, systems, documents, people not in the voice library):
  `e-<sha1(type|normalised name)>`, without the client, so "the SSO integration" in a shared file and in a client file
  is one node and the cross-file hop works. Client scope is not on the node but on its evidence: every claim, edge and
  alias of it carries the labels of the snippets it was quoted from, and the entity itself is visible to a scope only
  through them (section 11.2 step 1), so a node found only in one client's files never shows in another client's
  call. When the same name is used for two different things by two clients (the model resolves it to a known id of
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
it from a primary source or a PM edit.

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
segment keeps speaker names and timestamps.

**Extraction cache per segment.** A small edit to a large file (Current State, `decisions.md`) must not re-extract
the whole file. Snippets are stable across an edit in another section (`lookup.chunks` closes a chunk at each
heading, and `chunks_at` keeps that rule), and segments do not cross headings, so an edit in one section leaves the
segments of every other section byte-equal. Each segment has a content key
`seg_hash = sha256(client label | heading path | segment snippet texts)`: the context snippet and the extractor are
not in it (the context can change when the section above is edited, and quotes from it are never accepted). The
validated output of every segment is kept in `segment_cache`, keyed by `(seg_hash, extractor)`. When a new revision
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
  and aliases visible to the segment's own scope**: the visibility rule of section 11.2 step 1 with
  `scope.client` = the segment's client label (shared only for a shared segment), so another client's task names,
  people or aliases are never sent with this segment;
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
   second failure marks the segment `invalid` (it is retried on the next rules or route change only).
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
  85) reads from `<root>/state/` or `LT_EMBED_LAUNCHER`. A file older than 6 hours or with a dead pid is stale. The
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

- A new routing job `index` joins `JOBS = ("prep", "tips", "after")`. Its route defaults to the route of `after`
  and can be set separately in Settings; `LT_AI_ROUTE` works as for other jobs, but the general fallback chain
  (`CHAIN_DEFAULT`) is not used: extraction falls back only to `local`, never to another cloud route. The job still
  has to exist everywhere jobs are listed, so Settings can show it and the chain code does not reject it: Python
  `routing.JOBS`, Swift `AIChain` (`AIChain.allowed(route, job: "index")`, `Components.afterRemovingLocal`) and the
  `chain` entry of `rules/ai.yaml`, which states that `index` steps only to the model on this Mac (S5 for Swift, S3
  for Python and the rules).
- Model per route: the cheaper model of each route for extraction (api `haiku-5-5`, openai `gpt-6-luna`, gemini
  `gemini-3.5-flash-lite`, codex `codex/default`, code with its smallest model, local `LT_LOCAL_MODEL` or
  `gemma4:26b`) and the route's `after` model for topic summaries. The model names come from `rules/ai.yaml`, not
  code. Section 15, question 1 asks the owner about this default.
- **Output cap**: every extraction request sets a per-request output limit of 2,000 tokens (`max_tokens` on api
  and code, the equivalent field on openai, gemini and codex, `num_predict` on local), from `rules/evidence.yaml`
  (`max_output_tokens`). A reply cut at the cap fails JSON parsing and gets the repair retry (section 8.4), which
  asks for fewer items. Topic summaries use a cap of 800 tokens. These caps make the ceiling a hard bound (section
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
- **Summary**: per topic whose `member_hash` changed, one model request on the `index` route's summary model with the
  topic's accepted claims and edges (with dates, modality, quotes) up to 6000 tokens; the output must cite claim ids
  and is validated like section 8.4 (every sentence cites at least one claim; uncited sentences are dropped). Stored
  one row per sentence in `topic_sentence`, with the claims and edges it cites in `topic_cite`, marked derived, never
  evidence.
- **Access on summaries is per sentence.** At query time a sentence is returned only when **every** claim or edge it
  cites passes section 11.2 steps 1 and 2 for the request (scope, `exclude`, source state, `as_of`); other sentences
  of the same topic can still be returned. So a summary written before a source was excluded, revoked or hidden, or
  one that cites a file in this request's `exclude`, never returns that source's facts. A topic with no passing
  sentence is not returned at all.
- **Staleness on access change.** When a source changes state (excluded, hidden, deleted, revoked, or back to
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
Ukrainian. That is 3.87M characters and 678 segments. Per segment: a 1,500-token cacheable prefix, a 500-token
entity catalog, and output between **500 tokens (typical)** and **2,000 tokens (the per-request cap, section 8.6)**.
The contract allows 40 entities, 60 claims and 60 relations with quotes up to 300 characters, which would exceed the
cap; the cap, not the item limits, bounds the output. 40 topic summaries of 6,000 input and 600 output tokens, priced
in the table on the same model as extraction (summaries on the `after` model are added below the table). A typical week after the first index: 3 calls, 10 changed documents, 5,000
characters of state, 6 topics: 272,000 characters, 48 segments. Every figure is a range "typical to cap"; the
real output per segment is calibrated in S3 (G0 `--extract`) before S5 shows any number to the PM.

**Prompt cache on api Haiku.** The table assumes the provider caches the 1,500-token prefix. Anthropic documents a
minimum cacheable prompt length per model, and for Haiku models it has been higher than for Sonnet; whether 1,500
tokens qualify on `haiku-5-5` was **not verified** in this step. The Haiku row therefore uses the no-cache price.
S3 checks the provider's cached-token count on the first requests of every route and the estimate uses no cache on
any route where it stays 0.

| Route and model | First full index (typical to cap) | One week incremental (typical to cap) |
|---|---:|---:|
| tokens on api (fresh in / cached prefix / out) | 2.56M / 1.08M / 0.36M to 1.38M | 0.20M / 0.08M / 0.03M to 0.10M |
| api `haiku-5-5` (no cache) | $0.55 to $1.05 | $0.04 to $0.08 |
| api `sonnet-5-5` | $8.86 to $19.03 (no cache $10.90 to $21.07) | $0.68 to $1.40 |
| openai `gpt-6-luna` | $0.37 to $0.88 | $0.03 to $0.07 |
| openai `gpt-6.1-sol` | $7.35 to $17.52 | $0.58 to $1.30 |
| gemini `gemini-3.5-flash-lite` | $1.47 to $4.01 | $0.11 to $0.29 |
| gemini `gemini-3.8-flash` now | $2.76 to $6.57 | $0.22 to $0.49 |
| gemini `gemini-3.8-flash` from 2027-01-01 | $5.51 to $13.14 | $0.43 to $0.97 |
| codex plan, 3.25M to 4.26M tokens | about 52% to 68% of a 5-hour window (assumed anchor) | about 4% to 5% |
| code plan (Claude Code), 4.0M to 5.0M tokens | not measured: no anchor for the Claude plan yet | not measured |
| local `gemma4:26b`, 3.33M to 4.35M tokens | $0, 4.7 to 28 hours (assumed 300 to 600 tokens/s prefill, 15 to 30 tokens/s output; not measured) | 0.4 to 2.1 hours |

Two corrections to the table, computed in this step with the second script of Appendix A:

- **Topic summaries run on the `after` model** (section 8.7), not the extraction model. 40 summaries cost $0.73 on
  Sonnet or GPT-6.1 Sol and $0.27 on Gemini 3.8 Flash ($0.55 from 2027-01-01), against $0.04 on Haiku or GPT-6 Luna
  and $0.13 on Flash-Lite in the table. With recommendation (a) of section 15 (cheap model extracts, `after` model
  summarises), a first index costs **$1.24 to $1.74** on Claude key (Haiku plus Sonnet), **$1.06 to $1.57** on OpenAI
  (GPT-6 Luna plus GPT-6.1 Sol) and **$1.61 to $4.15** on Gemini (Flash-Lite plus 3.8 Flash). A typical week (6
  topics) then costs $0.14 to $0.18 on Claude key, $0.13 to $0.17 on OpenAI and $0.13 to $0.31 on Gemini. The S3 estimator prices
  each request on the model that sends it, so summaries are priced on the summary model.
- **The entity catalog is closer to 2,000 tokens than 500** on api (60 entities with ids, text and aliases). That
  adds 1.02M fresh input tokens to a first index: +$0.10 on Haiku, +$2.03 on Sonnet, up to +16% of a 5-hour window
  on Codex (fewer tokens per character there). Output still dominates the dollar cost, so the ranges hold in shape;
  S3 measures the real catalog size with the output per segment.

What the numbers say:

- Output is 40% to 62% of the dollar cost at the typical output and 72% to 86% at the cap (output prices are 5 to 8
  times input), so the output cap and short quotes matter more than input trimming. This is why the cap is part of
  the contract and S3 measures the real output per segment.
- On api and openai, where the cache applies, the cacheable prefix saves about 18% to 22% at the typical output and
  about 10% at the cap (e.g. Sonnet $10.90 -> $8.86, $21.07 -> $19.03). S3 sends the prefix first and identical on
  every request so the providers' prompt cache applies.
- On the cheap model of each route the first full index costs $0.37 to $4.01 for extraction ($1.06 to $4.15 with
  summaries on the `after` model); on the `after` models of api and openai it costs $7.35 to $19.03. The weekly
  incremental is $0.13 to $0.31 with recommendation (a) (cents for extraction, the rest summaries) and up to $1.40
  on the `after` models.
- On the Codex plan the first full index does not fit one 5-hour window next to normal work; it has to spread over
  several passes under the ceiling. On local it is an overnight job at best and more than a day at the cap.
- The G0 fixture is 10,383 bytes (20 files): about 3 segments, far below one cent on any route; it is not a cost
  reference.

### 9.1 Ceiling

- One ceiling per pass, in the unit of the route: USD on `api`, `openai`, `gemini` (default $1.00 per pass), percent
  of the 5-hour window on `codex` and `code` (default 10%, read from `ai-accounts.json` before the pass and after
  every request through the existing `account_usage_*` functions), wall-clock minutes on `local` (default 120).
  These are team defaults, each a setting (section 10); on reaching the ceiling the pass waits for the next one,
  and continuing on the local model is an opt-in setting (off by default).
- **Daily cap**: passes can run hourly, so a per-pass ceiling alone does not bound a day. A second cap per project
  and route per calendar day: 3 times the pass ceiling by default ($3.00, 30% of the 5-hour window summed over the
  day's passes, 360 minutes on local), also a setting. The worker sums today's `run` rows before every pass and
  before every request; at the daily cap the pass ends with `status = ceiling` and the row says "daily".
- **Global daily cap across projects**: with N projects the per-project cap alone allows N times the spend in a day.
  One more cap sums today's `index` spend of all projects per route from `spend-ledger.jsonl` (job `index`), which is
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
- Spend is written to the existing `spend-ledger.jsonl` with job `index`.

## 10. Settings and cost text (text only, Design later)

Settings > AI, a new row under the routes (only when the flag is on):

- Title: "Project memory". Help line: "Pacenote reads your project's calls, notes and files on this Mac and keeps
  who and what they mention, with quotes. It never runs during a call."
- Per project: "Indexed 412 of 678 parts. Last pass: Nov 23, 14:10, 1.2M tokens, $0.31 on Claude key (Haiku).
  Next pass: about 48 parts and 6 topic summaries, about $0.14 to $0.18." On plan routes: "about 4% of your 5-hour ChatGPT limit". On
  local: "about 25 minutes on this Mac, free".
- Controls: "Index now", "Pause", the route ("Use for indexing: same as after the call / <route list>"), the ceiling
  ("Stop each pass at $1.00 / 10% of the 5-hour limit / 120 minutes on this Mac", "and each day at $3.00 / 30% /
  360 minutes", and for all projects together "Never more than $3.00 / 30% / 360 minutes a day for all projects"),
  "Topic summaries: after each pass / only when asked", the switch "Use AI to read
  sources" (off = deterministic links only, no model requests), and "Delete this project's memory".
- Before the first full index of a project, a confirmation: "First index of Northpoint: 192 sources, about 3.9M
  characters. On Claude key (Haiku reads, Sonnet writes topic summaries): about $1.24 to $1.74, in 2 passes. On your
  ChatGPT plan: about 52% to 68% of a 5-hour limit, in 6 to 7 passes over 2 to 3 days. On this Mac: 5 to 28 hours,
  free. Your files are sent only to the AI you chose
  for this job." Buttons: "Start", "Use this Mac only", "Not now". The range comes from the estimate with typical
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
       exclude=(),               # source ids or uris excluded for this request (Sources "skip", this call's own files,
                                 # and the files of a call hidden since the last state-only pass, section 5.1)
       kinds=None,               # snippet, claim, edge, topic
       mode="prep",              # prep, live, after
       budget=Budget(k=6, chars=14000, ms=1000, hops=1),
       include_suggestions=False,
       query_vec=None) -> Result # items: kind, text, quote, locator, source uri and title, said_at, client, modality,
                                 # status, origin, confirmations, why (the path: "item 13 -> blocks -> SSO"), score
entity_card(store, entity_id, *, scope, as_of=None, window=None) -> Card   # claims and edges of one entity, dated
ask_global(store, query, *, scope, as_of=None, window=None, budget=...) -> GlobalResult  # section 8.7
```

### 11.2 Order: access and time before ranking

Every query is built in this order, and steps 1 and 2 are SQL `WHERE` clauses on the candidate query itself, never
filters on a ranked list:

1. **Access**: `source.state = 'active'` (excluded, hidden calls, deleted and revoked never pass); not in
   `exclude`; the snippet's `client_set` is empty, contains `scope.client`, or has no label in `scope.others`
   (section 5.3, the `client_scope.filter_text` outcome). A claim or edge passes only when **both** hold:
   `EXISTS` an evidence row whose snippet passes, and `NOT EXISTS` an evidence row whose snippet fails (a claim with
   one out-of-scope or revoked quote is dropped, not shown with fewer quotes; a claim with no evidence left never
   passes). No scope (`decide()` returned None, or a one-client project) passes everything of the project that is
   not excluded. The same check covers the rows that have no evidence of their own:
   - **Entity**: visible when at least one of its claims or edges passes (with its evidence, as above), or it has a
     deterministic origin (`g1`, `tracker`, `voices`, `pm`) whose source passes and whose `entity.client` is empty
     or `scope.client`. An entity that is not visible is never a match, a graph step end, a catalog entry (section
     8.3) or a `why` path element, and its name is not returned.
   - **Alias**: matched only when its entity is visible and its `client_set` passes like a snippet's.
   - **Topic summary sentence**: returned only when every claim and edge it cites passes (section 8.7); a topic
     with no passing sentence is not returned.
2. **Time** (content time, section 6): with `as_of`, a revision is valid when `valid_from <= as_of` and
   (`valid_to IS NULL` or `valid_to > as_of`); without `as_of`, the active revision (`superseded_at IS NULL`).
   Snippets must sit on a valid revision and have `said_at <= as_of`. A claim or edge is **current** only while it
   has at least one passing evidence row on a revision valid at `as_of`; a claim whose only evidence sits on a
   replaced revision (an old go-live date in an edited file) is history: `entity_card` and `ask_global` return it in
   the timeline marked "changed later", `search` does not return it as current. Topic summaries need
   `summary_at <= as_of` (a summary written later never appears in a replay, even if its claims are old; so a replay
   of a call older than the first index gets claims and timelines but no topic summaries). `observed_at` is never
   used here. `window` narrows on `said_at`. Known limit: a file with only one revision whose content time is after
   the replayed call is not visible in that replay even if part of it existed earlier (the safe side: a coverage
   loss, never a leak); from its second revision on, replay is exact.
   **State columns under `as_of`.** The "now" caches (`entity.name`, `entity.status`, `entity.merged_into`,
   `claim.status`, `edge.status`, section 6) are not read in a replay; they are derived from rows dated at or before
   `as_of`:
   - an entity's status and name come from its `status` and name claims with `said_at <= as_of` (for tasks these are
     the register events folded up to `as_of`, the same `commitments.fold()` cut at that time), so a task closed on
     Nov 19 is open in a replay as of Nov 17; an entity with no name claim keeps the name of its earliest evidence at
     or before `as_of`, and an entity with no row at or before `as_of` is not visible at all;
   - a merge applies only when its register event or `pm_override` has `at <= as_of`;
   - a claim or edge is `superseded` only when the superseding claim (or the PM's `supersedes` edge) is dated at or
     before `as_of`, and `rejected` only when the rejecting override has `at <= as_of`; one exception: a PM rejection
     of an extracted claim, edge or alias corrects the store, not the world, so it applies in every replay;
   - a decision superseded after `as_of` is current in that replay.
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
the call's entities) is written to `.work/<stem>.evidence.json` for the live call and the skill. Same for every route.

### 11.4 How G4 live will call it

The normal tip path makes no new network or model call. G4 loads the prep snapshot into memory at call start and
answers from it. The only store access G2 allows on the live path is `mode="live"`, which:

- opens the SQLite file read-only (`mode=ro` URI), with the WAL readers never waiting on the writer (the worker is
  stopped anyway during a call, section 8.5);
- refuses `query_vec` (a query embedding is a model call) and any `ask_global`;
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

`python -m livetips_m0 evidence status|ingest|pass|exclude|search|card|ask|rebuild|vacuum|delete --project-uid ...
--root ...` with JSON output, so the Swift app and the skill use one interface. `pass` and `exclude` take the lists
the app passes: `--hidden-calls`, `--revoked` (Google file ids and Notion page ids with status `denied` or
`not_found`) and `--web-dirs` (stored page folders outside call folders). `exclude` is the state-only pass of
section 5.1: no model, network or embed request.

## 12. Privacy

- The store is one SQLite file per project in `~/Library/Application Support/LiveTips/evidence/`, never in the
  project folder, never synced by Drive, never sent to the Pacenote server (call sync, D27 and D47 unchanged).
- Model requests go only to the route the PM chose for the `index` job (or local). The same provider already
  receives these files during preparation; G2 adds no new destination. The worker itself makes no Notion, HubSpot
  or web request: the Tracker comes from the snapshot file the app writes (section 5.2, item 5), site pages from
  what G6 already stored. The first full index asks before it starts (section 10) because it sends more text than
  one preparation does.
- Hidden calls (section 5.1) are never sent to any model and never searched; hiding a call later starts the
  immediate state-only pass, which moves its sources to `excluded`, and from then on their claims, edges and the
  topic sentences citing them stop passing the access check. Until that pass has run (a hide during a call), the
  app passes the hidden call's files in `exclude` on every search. A document that loses access (`denied` or
  `not_found`) goes the same way to `revoked` (section 5.2, item 6).
- Vectors come from the bundled local bge-m3 server; nothing is embedded in the cloud.
- Emails and phone numbers are already scrubbed from site pages (G6); the store keeps text as the reader returns it,
  so the store holds what the project folder holds. Secrets folders skipped by `semantic.py` and `.livetipsignore`
  are skipped by ingest too.
- "Delete this project's memory" removes the file, its WAL and the lock. Removing a project from Pacenote offers
  the same. A deleted or revoked source disappears from search at once and its snippets are purged at the next
  vacuum unless the PM keeps history (default: purge revoked, keep deleted local files for 30 days for undo).
- Telemetry (`usage.py`) logs counts only (`index_pass` with segments, tokens, cost, status), never text, names or
  paths.
- Tests and the benchmark use invented data only.

## 13. Failure, migration and rebuild

### 13.1 Concurrency

- One writer per project: the worker takes `<uid>.lock` (flock) for a pass; ingest from the prep step waits at most
  2 seconds for it and otherwise reads the last committed state. The state-only pass (hide, unhide, revoke) also
  waits at most 2 seconds; when a full pass holds the lock, it writes its change to `<uid>.state-request.json` and
  the running pass applies it in its own transaction before its next segment (it checks between segments anyway).
  The app keeps the files in `exclude` until `evidence status` shows the change applied.
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
- **Project renamed**: same uid, so the store is kept. But register items and Tracker items carry the `project`
  slug, and G1's `scope_match` and the Tracker scope compare it, so items written before and after a rename would
  not join. The store keeps every slug the project ever had in `meta` (`slug_history`, appended when the app passes a
  new slug with the same uid) and normalises an item's `project` to the uid through it before any G1 key function
  runs. The register itself is not rewritten. **Folder moved** through Settings: same uid; `root_hint` updated;
  source uris are relative so nothing is re-read (hashes are equal). A folder re-added as a new project gets a new
  uid and a new store; the old one is listed in status as orphaned and deleted after confirmation.
- **Vacuum** (weekly, outside calls, and right after a source is revoked): purge snippets of superseded revisions
  nobody cites, purged sources, then, in the same transaction, every claim and edge left with no evidence row and
  every `segment_cache` row whose `seg_hash` no segment of a remaining revision has, or whose `extractor` is not the
  current one and is older than 30 days; old `run_item` rows (keep 90 days); then `VACUUM`.

## 14. G0: how the store is measured

### 14.1 Variants

- **A**: current code (exists).
- **B**: wider text search without a graph: the same store's snippets and FTS5 over all calls of all series and all
  folder files without the 7-day window, scope and time filters on, no entities, no edges, no topics. It answers "is
  it just more context?".
- **C**: B plus entities, claims, edges, `hops=1..2`, topics and `ask_global`.

B and C use **the same candidate sources and the same candidate signals**: both run with stored vectors and the
same `query_vec` setting (the bench runs both twice, without and with vectors, and reports both pairs), so the
difference B to C is the graph and nothing else.

The bench makes no model call and blocks every host (`run.py`). Variant C therefore uses a **checked-in extraction
fixture**: `bench/g0/fixtures/extract/<seg_hash>.json`, written once by a real extraction run on a cheap route
(route, model, date and `extractor` recorded in the file). It is keyed by the content-only `seg_hash` (section 8.2),
so a prompt or rules change does not invalidate it: C still replays it, and the report states when the fixture's
`extractor` differs from the current one. Re-recording (a paid run) is a deliberate step, needed only when a gold
file's text changes or the contract changes the output shape. The fixture is the **raw validated model output**: a hand review may
only delete items that are wrong (each deletion listed with its reason in the same file), never add or edit one, so
what C finds is what the model found. C replays the fixture through the same validator (section 8.4).

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
state's content time (Nov 12 and Nov 19). The bench ingests v1, then v2, passing the content time explicitly
(the CLI and the ingest API accept `--valid-from` for tests and the bench only), so the store has two revisions
with `valid_from` set as in real use, independent of the files' mtimes on the CI machine.

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
   a Ukrainian question about the English notes where no word matches, only the entity alias does.
3. **Global**: "What happened with SSO since October?" expects a dated timeline of at least 4 facts from 3 sources
   in order (topic summary plus claims).
4. **Time replay**: a go-live date changed on Nov 19 in an edited file (the two revision states of section 14.1);
   replayed as of Nov 17 the answer must be the old date and the Nov 19 fact must not appear (future leak); asked
   now, the answer is the new date and the old one appears only in the timeline marked "changed later". The same
   replay also covers state columns: a task the register closes on Nov 19 must be open as of Nov 17, and a decision
   superseded on Nov 19 must be current as of Nov 17 (section 11.2 step 2).
5. **Modality**: "Did Globex agree to 39 hours?" where 39 hours was only proposed: the answer must carry
   `proposal`; a fact said only in generated notes must not count as confirmed.
6. **Exclusion and revocation**: one file excluded by the PM, one deleted file, one revoked document and one hidden
   call (its transcript and notes), each with a unique fact; none may ever appear, neither as a snippet nor as a
   claim, edge, entity name, alias or topic summary sentence. The topic summary that cites the fact is built
   **before** the exclusion, so the per-sentence filter of section 8.7 is what the gold tests. The bench passes the
   revoked document through `--revoked` and the hidden call through `--hidden-calls`, the same way the app does
   (section 11.6).
7. **Scale**: a seeded noise generator adds about 2 MB of plausible project text (40 extra documents, 10 extra
   calls) so that ranking and latency are measured with distractors; the 10 KB set stays as the "small" set.

### 14.3 New metrics (added to the results JSON, A and B report them too)

| Metric | Meaning |
|---|---|
| Hop answer rate | multi-hop questions (at least 6) with every needed quote in the top 6 |
| Paraphrase answer rate | paraphrase and cross-language questions answered in the top 3 |
| Global timeline recall | share of expected timeline facts returned by `ask_global`, and order correct (yes/no) |
| Evidence recall | gold facts returned as a claim or edge with a valid quote and locator |
| Quote validity | returned quotes that are verbatim at their locator (must be 100%) |
| Modality accuracy | modality questions with the expected modality; derived-only facts counted as confirmed (must be 0) |
| Future leaks | items with `said_at` or `summary_at` after `as_of`, and entity status, name, merge or claim and edge status values that come from a row dated after `as_of` (must be 0) |
| Excluded leaks | items (snippets, claims, edges, entity names, aliases, topic sentences) from excluded, hidden-call, deleted or revoked sources (must be 0) |
| Store p50 / p95 ms | `search(mode="live")` and `mode="prep"`, small and scale sets |
| Index cost | segments, tokens (deterministic count from the fixture), re-extracted segments after changing one section of one file (must be that section's segments only; the rest `cached`) |

Existing columns stay: prep coverage, prep leaks, live answer rate, answer text, live leaks, lookup p50/p95,
duplicates, wrong identity, item 13 cross-client, false agreed, false proposal.

## 15. Questions for the owner (product choices only)

1. **Which model reads sources.** Options: (a) the cheapest model of the chosen route (Haiku, GPT-6 Luna, Gemini
   3.5 Flash-Lite) for extraction and the after-call model for topic summaries: $1.06 to $4.15 for a first index,
   typical to cap, in 2 to 5 passes (1 to 2 days at the daily cap); (b) the route's after-call model (Sonnet,
   GPT-6.1 Sol) for both: $7.35 to $19.03, which at the $3.00 daily cap takes **3 to 7 days** to finish. On the Codex
   plan either option takes 6 to 7 passes over **2 to 3 days** (52% to 68% of a window at 30% a day).
   **Recommendation: (a)**; G0 `--extract` compares quality and we switch if the cheap model misses too much.
2. **Consent before spending on a cloud route.** Options: (a) ask once per project before the first full index,
   showing the volume, provider and cost range (section 10), then run increments in the background without asking,
   under the per-pass and daily caps; (b) no prompt at all, the flag is consent enough; (c) local only for the first
   index, cloud for increments; (d) ask before every pass. **Recommendation: (a)**.

Team defaults, each a setting in section 10, not owner questions: $1.00 per pass and $3.00 per day on paid keys,
10% per pass and 30% per day of the 5-hour window on plans, 120 and 360 minutes on local, and the same day caps for
all projects together; at the ceiling wait for the next pass, local overflow off; topic summaries eager on the
after-call model (40 topics: about $0.73 on Sonnet or GPT-6.1 Sol, $0.27 on Gemini 3.8 Flash). With these defaults
and option (a), a first index of the typical project finishes in 2 passes on Claude key or OpenAI ($1.24 to $1.74,
$1.06 to $1.57), 2 to 5 passes (up to 2 days at the daily cap) on Gemini ($1.61 to $4.15), and in 6 to 7 passes
over 2 to 3 days on the Codex plan.

## 16. Risks

- **Invented links** (V3's main risk): every edge needs a verbatim quote and a word of both ends, ambiguous ones stay
  suggestions, the G0 extraction fixture is raw model output (review may only delete), and `--extract` measures
  precision and the hop and paraphrase rates per route.
- **Output size drives cost**: the first-index range is wide ($0.37 to $4.01 for extraction on cheap models, $1.06
  to $4.15 with summaries on the `after` model) because output per
  segment is not measured yet; the per-request cap bounds it and S3 calibrates it before any number reaches the PM.
- **Synthetic gold proves correctness, not usefulness**: gains on the owner's real calls stay "not verified" until
  G3 runs on live calls (the plan's section 10 risk, unchanged).
- **Cost of the first index on plan routes**: about 52% to 68% of a Codex 5-hour window for a typical project (on an
  assumed anchor); the ceiling
  and the 80% stop keep it from blocking preparation, at the price of an index that takes days to complete.
- **Local model speed and quality**: not measured; S3 measures tokens per second on the owner's Mac class before the
  fallback is offered in the UI.
- **Two sources of truth for tasks**: the register stays canonical; the store only reads it. Where PM edits are
  written is the one rule in section 7.3.
- **Store size**: about 4,000 snippets for the typical project (3.87M characters / 900), about 8 MB of vectors plus
  text; fine for SQLite, watched with the scale set.
- **FTS5 or bge-m3 missing on a Mac**: fallbacks keep search working with lower quality; status says so.
- **Concurrency with the live call**: the worker never runs while `call-active.json` exists; a crash that leaves the
  file stale is caught by the 6-hour and pid checks.
- **Scope drift**: client labels are computed at ingest; a change in the client list relabels everything (free), and
  the search re-checks labels on every evidence row before ranking.

## 17. Implementation split

Each sub-task merges into the PB-52 integration branch behind the flag `evidence_store` (config `features`, off by
default; `LT_EVIDENCE=1` for tests), with tests, CHANGELOG (Unreleased) when user-visible, docs EN and UK, and
`rules/` plus the generated skill section where the contract changes. Each has a G0 gate: no existing column
regresses, its new columns meet the bar.

### S1. Store, deterministic ingest, search, CLI (core)

Scope: `livetips_m0/evidence/` (schema, migrations, store open/lock and the global worker lock, ingest per source
type with change detection and client labels, deterministic entities and claims from the register, the Tracker
snapshot file reader, sheets, voice aliases, the state-only pass with hidden calls and revoked documents from the
passed lists and the state-request file, `d-`/`r-` ids in `identity.py`, `pm_override` from register events),
`client_scope.labels(root)`, `lookup.chunks_at` (offsets and client boundaries), slug history for renamed projects,
`search` / `entity_card` with access (snippets, claims, edges, entities, aliases) and time filters including the
`as_of` derivation of state columns, the CLI (`exclude`, `--hidden-calls`, `--revoked`, `--web-dirs` and, for tests
and the bench, `--valid-from`).

Acceptance:
- [ ] The store is created only under `<support>/evidence/<uid>.sqlite`; a test asserts nothing is written under the
      project root.
- [ ] Changing one file creates exactly one new revision; touching mtime without content change creates none; a
      deleted file is `deleted` and absent from search; register `migrate`/`rollback` triggers a refold.
- [ ] Access tests: excluded, hidden call, deleted, revoked (from the `--revoked` list: a Google picked copy by
      `<id16>` and a `_notion/` file by page id), another client's file, another client's line in a shared file, a
      claim with one out-of-scope evidence row, an entity known only from another client's files (no name, alias or
      graph step through it): none returned, and each case is filtered in the SQL candidate query (checked by a test
      that ranks nothing it was not allowed).
- [ ] `exclude --hidden-calls` makes no model, network or embed request and the hidden call is gone from the next
      `search`; with a full pass holding the lock, the change goes through the state-request file and is applied
      before the pass's next segment.
- [ ] Client parity: on the G0 fixture, for each scope, the snippets that pass cover exactly the lines
      `client_scope.filter_text` keeps.
- [ ] Time tests on content time: a first index of files with past content times, replayed as of a past call, sees
      the files valid then; `as_of` before a revision or before a claim returns none of them; a claim whose evidence
      sits only on a replaced revision is not current; a task closed after `as_of` is open, a merge after `as_of` is
      not applied, a decision superseded after `as_of` is current, and a PM rejection applies at every `as_of`.
- [ ] One edge found in a shared file and in a client file is one row; for the other client's scope it passes only
      through the shared evidence (and is dropped when it also has a failing row, section 11.2).
- [ ] PM edits from the register and `pm_override` rows on a claim and on an edge survive a rebuild (natural keys).
- [ ] Purge: after a source is revoked and vacuumed, no claim or edge is left without evidence, and none of its
      facts is returned.
- [ ] A register item written before a project rename joins one written after it.
- [ ] Two processes: a reader during a write sees the previous complete revision.

G0 gate: A's columns unchanged; the store unit tests above run on the current 10 KB G0 fixture with leaks 0 (client,
excluded, future); `mode="live"` p95 on that fixture at most 150 ms. (The new variants and gold come in S2, so S1
merges on its own without them.)

### S2. G0 gold v2, scale set, variants B and C (core)

Scope: `gold.yaml` v2 (section 14.2: at least 6 multi-hop questions, paraphrase and other-language questions, the
global timeline, the two revision states with a task closed and a decision superseded between them, modality, the
excluded, deleted, revoked and hidden-call cases with a topic summary built before the exclusion), the scale set's
seeded noise generator, VariantB and VariantC in `run.py` (deterministic part, with and without vectors), the new
metrics of section 14.3, and the extraction fixture loader keyed by content-only `seg_hash` (the fixture itself is
recorded in S3).

Acceptance:
- [ ] `run.py --variant A|B|C` runs offline on the small and the scale set, blocks every host, and writes the new
      columns; A's existing columns are byte-equal to the last A result.
- [ ] Every gold v2 question states which variant is expected to pass it and why, so a later gain is attributable.
- [ ] The future-leak metric counts entity and claim state values from after `as_of`; the excluded-leak metric
      counts entity names, aliases and topic sentences.
- [ ] The scale set is reproducible (same seed, same bytes) and its generated extraction is marked so no hop or
      paraphrase gate uses it.

G0 gate: B and C (deterministic part) on the small and scale sets: prep and live leaks 0, future leaks 0, excluded
leaks 0, quote validity 100%, evidence recall for register and Tracker facts 100%; A's columns unchanged;
`mode="live"` p95 on the scale set at most 150 ms.

### S3. LLM extraction, validation, routing, ceiling (core)

Scope: `rules/evidence.yaml`, `rules/evidence_schema.json`, generated `skill_evidence.md` and the skill copy; segmenter,
prompt builder with cacheable prefix and the client-filtered catalog, validator (section 8.4, with the unanchored
`hedge_anywhere` words), the `index` routing job on all six routes with local fallback (Python `routing.JOBS` and
the `chain` entry of `rules/ai.yaml`), the per-request output cap, segments that never cross a heading and the
segment cache keyed by `(seg_hash, extractor)`, the worker (`evidence pass`) with the call-active check and the abort
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
      `spend-ledger.jsonl` with job `index`.
- [ ] Re-running a pass with no changes sends 0 requests; editing one section of a large file (including a short
      section between two others, and the section right above another) sends only that section's segments, the rest
      are `cached`; a new `extractor` sends every segment again.
- [ ] A route whose cached-token count stays 0 is estimated without cache.

G0 gate: C with the extraction fixture: hop answer rate at least 4 of 6 multi-hop questions and at least 2 more than
B (A and B: expected 0 to 2), paraphrase answer rate not below B in the run with vectors and above B in the run
without vectors (B also has bge-m3, so with vectors both may already find the paraphrase), modality accuracy 100%,
derived counted as confirmed 0, all S1 and S2 zeros still 0; `--extract` on one cheap route reports precision,
recall, its own hop and paraphrase rates, output tokens per segment, catalog tokens, segments per document and cost
(recorded, not gated; the measured values replace the 500-token output and catalog assumptions in the estimates
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
      `exclude`, returns none of the sentences that cite it and still returns its other sentences.
- [ ] `ask_global` with `as_of` never returns a summary written after `as_of`.

G0 gate: global timeline recall at least 75% with correct order on the global gold, future leaks 0, excluded leaks 0
(including topic sentences), everything from S1, S2 and S3 unchanged.

### S5. App integration: flag, project uid, scheduler, settings and cost text

Scope: Swift: `evidence_uid` in `projects.json` with the slug passed on every pass (slug history), `LT_PROJECT_UID`,
`call-active.json` written at call start and removed at the end, the Tracker snapshot file
(`tracker-snapshot/<uid>.json` in the state folder next to `ai-routes.json`, from the Notion and HubSpot items the
app already holds), the lists passed to each pass (hidden calls from `history.json`; Google and Notion documents with
status `denied` or `not_found`; stored page folders under `SheetCLI.stateDir/web` for this project's sheets), the
state-only pass on hide, unhide and loss of access, with the hidden call's files in `exclude` on every search until
it has applied, triggers (after notes, folder change debounce, idle hourly, "Index now"), the `index` job in Swift
`AIChain` (`allowed`, Settings list, stepping only to the model on this Mac), the settings row with the all-projects
day cap and the first-index confirmation with the texts of section 10 (en and uk i18n), delete per project, status
line; docs EN and UK (including that G2 searches stored site pages); CHANGELOG.

Acceptance:
- [ ] Flag off: no store file is created, no worker runs, no setting is shown (test and `swift build`).
- [ ] Project rename and folder change keep the uid; re-adding a folder creates a new uid and lists the old store
      as orphaned.
- [ ] `call-active.json` exists for the whole call and is removed after it, including after a crash on the next
      launch.
- [ ] Hiding a call on Home starts the state-only pass at once (outside a call) or right after the call (during
      one), and the call's files are in `exclude` of every search until it has applied; a Google document turning
      `denied` does the same with `--revoked`; the Tracker snapshot file is written only from data the app already
      has (no new request; pure-logic tests on the list builders and the writer).
- [ ] `AIChain` accepts the `index` job and never steps it to another cloud route (pure-logic test).
- [ ] The cost text before and after a pass shows the numbers from the `run` row as a range (typical to cap), using
      the S3-calibrated output per segment (pure-logic tests on the formatter); screenshots only through the harness
      gate (L33).

G0 gate: no change to any G0 column (the app wiring is not on the bench); `CoreChecks` and `swift build` green.

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

SEG = 6000; PREFIX = 1500; CATALOG = 500
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

The second script (`python3 -I extra.py`) gives the corrections under the section 9 table: topic summaries priced
on the `after` model, the larger entity catalog and the days to finish at the daily cap. The table rows it starts
from are the outputs of the first script.

```python
T, FRESH, PRE, OUT = 40, 6000, 1500, 600  # 40 topic summaries: fresh input, cached prefix, output tokens
SUM = {"sonnet-5-5": (2.0, 0.10, 10.0), "gpt-6.1-sol": (2.0, 0.10, 10.0),
       "gemini-3.8-flash now": (0.75, 0.075, 3.75), "gemini-3.8-flash 2027": (1.50, 0.15, 7.50),
       "haiku-5-5": (0.10, 0.01, 0.50), "gpt-6-luna": (0.10, 0.01, 0.50), "gemini-3.5-flash-lite": (0.30, 0.03, 2.50)}
s = {k: T * (FRESH * pi + PRE * pc + OUT * po) / 1e6 for k, (pi, pc, po) in SUM.items()}
for k, v in s.items():
    print("summaries %s: $%.4f" % (k, v))
rows = {"haiku-5-5 (no cache)": ((0.55, 1.05), "haiku-5-5", "sonnet-5-5"),
        "gpt-6-luna": ((0.37, 0.88), "gpt-6-luna", "gpt-6.1-sol"),
        "gemini-3.5-flash-lite": ((1.47, 4.01), "gemini-3.5-flash-lite", "gemini-3.8-flash now")}
for k, ((lo, hi), cheap, after) in rows.items():
    d = s[after] - s[cheap]
    print("first index %s + after-model summaries: $%.2f to $%.2f" % (k, lo + d, hi + d))
extra = 678 * 1500  # catalog 2,000 instead of 500 tokens on 678 segments
for k, pi in (("haiku-5-5", 0.10), ("sonnet-5-5", 2.0)):
    print("catalog %s: +%.2fM tokens, +$%.2f" % (k, extra / 1e6, extra * pi / 1e6))
print("catalog codex: +%.0f%% of a window (upper bound)" % (extra / 250000 * 4))
week = {"haiku-5-5 (no cache)": ((0.04, 0.08), "haiku-5-5", "sonnet-5-5"),  # 6 topics a week, section 9
        "gpt-6-luna": ((0.03, 0.07), "gpt-6-luna", "gpt-6.1-sol"),
        "gemini-3.5-flash-lite": ((0.11, 0.29), "gemini-3.5-flash-lite", "gemini-3.8-flash now")}
for k, ((lo, hi), cheap, after) in week.items():
    d = (s[after] - s[cheap]) * 6 / T
    print("week %s + after-model summaries: $%.2f to $%.2f" % (k, lo + d, hi + d))
for k, lo, hi, cap in (("Sonnet $", 8.86, 19.03, 3.0), ("GPT-6.1 Sol $", 7.35, 17.52, 3.0), ("codex %", 52, 68, 30)):
    print("days at the daily cap, %s: %.1f to %.1f" % (k, lo / cap, hi / cap))
```

## Підсумок українською

1. PB-52 (GraphRAG G2): локальне сховище доказів у SQLite в Application Support, окремий файл на проєкт за новим
   `evidence_uid`, ніколи в папці проєкту, за прапорцем `evidence_store`.
2. Схема: джерела з ревізіями (sha256 тексту), уривки з локаторами, сутності з ідентичністю G1, твердження з
   модальністю, ребра з цитатами, аліаси, правки PM, теми з підсумками, проходи з вартістю.
3. Спершу детерміновані звʼязки (реєстр, знімок Tracker від застосунку, люди, шити), потім LLM-екстракція з усіх
   джерел у фоні, ніколи під час дзвінка (файл `call-active.json`; запит у польоті переривається), інкрементально з
   кешем на сегмент за змістом (сегмент не перетинає заголовок); приховані дзвінки і документи без доступу
   виключаються одразу окремим проходом без моделі й ніколи не йдуть у модель.
4. Контракт промпту і схема виходу в `rules/evidence.yaml` і `rules/evidence_schema.json`, спільні зі скілом;
   валідатор перевіряє дослівність цитат, клієнта, модальність, правки PM.
5. Модальність: proposal / expectation / confirmation / statement, мапінг зі значень G1; підсумки й нотатки ніколи
   не рахуються незалежним підтвердженням.
6. Пошук: доступ, клієнт (PB-70, точна мітка на кожен уривок) і час (`as_of` за часом змісту, статуси й злиття теж
   на момент `as_of`) фільтруються в SQL до ранжування, також для сутностей, аліасів і кожного речення підсумку
   теми; правки PM за природними ключами переживають перебудову; режим live без нових викликів.
7. Вартість першого індексу типового проєкту (оцінка, від типового виходу до ліміту 2000 токенів): $1.06-4.15 з
   дешевою моделлю для екстракції і моделлю after для підсумків, $7.35-19.03 на Sonnet або GPT-6.1 Sol (3-7 днів
   за денної стелі), 52-68% 5-годинного вікна Codex (2-3 дні), 5-28 годин локально.
8. Стеля на прохід, на день для проєкту і на день для всіх проєктів разом у долари, відсотки ліміту або хвилини,
   показ вартості до і після, вимикач і видалення.
9. G0: варіанти B і C з однаковими кандидатами, фікстура екстракції як сирий вихід моделі, складніший еталон (6+
   питань на кілька кроків, перефразування, глобальні, реплей у часі, модальність, виключення, масштаб).
10. Розбиття: S1 сховище і пошук, S2 еталон G0 v2 і варіанти B/C, S3 екстракція і стеля, S4 теми і глобальні
    питання, S5 інтеграція в застосунок;
    2 питання до Bohdan: яка модель читає джерела, згода перед першим хмарним індексом.
