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
  writes it; vectors G2 needs are stored in the SQLite file.

## 5. Sources, revisions and change detection

### 5.1 Source types

| Kind | Where (today) | Read by | Origin | Locator | LLM extraction |
|---|---|---|---|---|---|
| `file` | project folder files (the `folder_files` rules: skip `live`, `_notion`, `meeting-sheet`, hidden, generated, sidecars, older `-vN`) | `sheet/readers.py` (text, md, html, pdf with page marks, docx, pptx, xlsx, doc/rtf/odt/pages) | primary | rel path + char range + page (pdf) or sheet/slide | yes |
| `transcript` | `live/<YYYY-MM-DD>-<slug>/<stem>.md` (and the flat layout) | text | primary | rel path + line range + `[HH:MM:SS]` timestamp of the first line | yes, after the call ends |
| `notes` | `<stem>.notes.md`, `.app.notes.md` | text | derived (model-written); a line the PM edited is `pm` | rel path + heading + line range | yes, marked derived |
| `brief`, `sheet` | `.brief.md`, `.sheet.md`, `.sheet.json` | text / JSON | derived | rel path + block id or line range | no (deterministic facts only) |
| `register` | `live/commitments.jsonl` | `commitments.fold()` | pm / deterministic | rel path + event line number + event id | no |
| `tracker` | built live by `TR.collect` (Notion, HubSpot) | snapshot at ingest | primary (system of record) | `notion:<page id>` or `crm:<portal>:<task>` + field | no |
| `project_state` | `pacenote/decisions.md`, `_notion/... Current State.md`, `pacenote/answer-cards.md` | text | primary (decisions) / derived (Current State is written by a skill) | rel path + heading + line range | yes |
| `web_page` | `<live>/<date>-<slug>/.work/web/<host>/*.txt` (G6, title line, URL line, body) | text | primary (external) | URL + stored path + char range | yes |
| `gdoc` / `notion` copies | `live/google/google-file-<id16>.<ext>`, `_notion/` mirror files | as `file` | primary | as `file` + original URL | yes |

The column "origin" is what the confirmation rule (section 7.4) counts on. Git commits and the 14-day digest stay
prep-only sources and are not ingested.

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
5. The Tracker has no file: each ingest takes a snapshot through `TR.collect` (only when the app is not in a call
   and the network is already allowed for the Tracker), hashes the normalised item list, and diffs item by item.
   An unreachable Tracker keeps the last snapshot with its `observed_at`; it is never treated as "all items deleted".
6. A file that disappears gets `state = deleted`; a Google or Notion document that the API reports as gone or no
   longer shared gets `state = revoked`. Both stay in the store for history and audit but never reach search
   (section 11.2). Claims that rest only on such a source are hidden with it.
7. Transcripts of the call in progress are never read: a transcript whose call is active (section 8.5), or that was
   modified in the last 10 minutes, waits for the next pass.

Snippet size: the `FolderIndex` chunker (`lookup.chunks`, paragraphs merged up to 900 characters, the last heading
kept, paragraphs over 1800 characters split). One chunker keeps G0 numbers comparable between A and C. Extraction
uses bigger segments (section 8.2) made of whole snippets, so every quote maps back to snippets.

### 5.3 Client label at ingest

Each snippet gets a `client` label at ingest with the PB-70 functions (`client_scope.text_side`, `side` for paths,
`item_side` for register and Tracker items): the client folder it sits in, the single client a heading section names,
or empty (shared). A snippet that names two clients keeps both in `client_set` and the search filters it like
`client_scope.filter_text` does today (a line that names only another client never passes). Claims and edges inherit
the union of their evidence snippets' labels. When the list of clients of a project changes, labels are recomputed
deterministically (no model call).

## 6. SQLite schema

One file per project, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `user_version` = schema version. All times are
UTC ISO strings; `said_at` is the time the thing was said or written (call start plus the transcript offset, the file
date by the existing `file_day` rule, the Tracker `last_edited`), `observed_at` the time Pacenote first saw it.

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
  doc_date       TEXT,                  -- the date the content speaks for (file_day, call date)
  observed_at    TEXT NOT NULL,
  superseded_at  TEXT,                  -- NULL = active revision
  extract_state  TEXT NOT NULL DEFAULT 'pending',  -- pending, done, failed, skipped_off, not_needed
  extractor      TEXT,                  -- rules version + prompt hash used
  UNIQUE (source_id, content_hash)
);
CREATE INDEX revision_active ON revision(source_id, superseded_at);
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
                                        -- client:<slug>, e-<sha1(type|norm name|client):10>
  type        TEXT NOT NULL,            -- person, client, task, decision, risk, topic, document, system, call, date_event
  name        TEXT NOT NULL,
  client      TEXT NOT NULL DEFAULT '',
  status      TEXT,                     -- open, proposal, done, dropped (tasks, from the register)
  origin      TEXT NOT NULL,            -- g1, tracker, voices, llm, pm
  merged_into TEXT REFERENCES entity(id),
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
  said_by     TEXT REFERENCES entity(id),
  said_at     TEXT NOT NULL,
  due         TEXT,
  client_set  TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL,            -- accepted, suggestion, rejected, superseded
  origin      TEXT NOT NULL,            -- deterministic, llm, pm
  confidence  REAL,
  run_id      INTEGER REFERENCES run(id)
);
CREATE INDEX claim_subject ON claim(subject_id, status, said_at);

CREATE TABLE edge (                     -- a relation between two entities
  id            INTEGER PRIMARY KEY,
  src_id        TEXT NOT NULL REFERENCES entity(id),
  rel           TEXT NOT NULL,          -- blocks, depends_on, owns, about, part_of, mentions, decided_in, risk_for,
                                        -- waiting_on, attended, same_as (always a suggestion until the PM confirms)
  dst_id        TEXT NOT NULL REFERENCES entity(id),
  modality      TEXT NOT NULL,
  client_set    TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL,          -- accepted, suggestion, rejected, superseded
  origin        TEXT NOT NULL,
  confidence    REAL,
  first_said_at TEXT NOT NULL,
  last_said_at  TEXT NOT NULL,
  run_id        INTEGER REFERENCES run(id),
  UNIQUE (src_id, rel, dst_id, client_set)
);
CREATE INDEX edge_src ON edge(src_id, status);
CREATE INDEX edge_dst ON edge(dst_id, status);

CREATE TABLE evidence (                 -- every claim and every edge has at least one row
  target      TEXT NOT NULL,            -- 'claim' or 'edge'
  target_id   INTEGER NOT NULL,
  snippet_id  INTEGER NOT NULL REFERENCES snippet(id) ON DELETE CASCADE,
  quote       TEXT NOT NULL,            -- verbatim from the snippet, at most 300 characters
  q_start     INTEGER NOT NULL, q_end INTEGER NOT NULL,  -- offsets inside snippet.text
  PRIMARY KEY (target, target_id, snippet_id, q_start)
);
CREATE INDEX evidence_snippet ON evidence(snippet_id);

CREATE TABLE pm_override (              -- PM edits; always applied last, never overwritten by extraction
  id         INTEGER PRIMARY KEY,
  target     TEXT NOT NULL,             -- entity, claim, edge, alias
  target_id  TEXT NOT NULL,
  field      TEXT NOT NULL,             -- status, name, merge, split, reject, confirm, value
  value      TEXT,
  at         TEXT NOT NULL,
  via        TEXT NOT NULL              -- register, app, skill
);
CREATE INDEX pm_override_target ON pm_override(target, target_id);

CREATE TABLE topic (
  id           INTEGER PRIMARY KEY,
  label        TEXT NOT NULL,
  client_set   TEXT NOT NULL DEFAULT '',
  member_hash  TEXT NOT NULL,           -- hash of the sorted accepted claim and edge ids it covers
  summary      TEXT,                    -- NULL until summarised
  summary_at   TEXT, summary_run INTEGER REFERENCES run(id),
  state        TEXT NOT NULL            -- fresh, stale, pending
);
CREATE TABLE topic_member (topic_id INTEGER REFERENCES topic(id) ON DELETE CASCADE, entity_id TEXT, weight REAL,
  PRIMARY KEY (topic_id, entity_id));
CREATE TABLE topic_cite (topic_id INTEGER REFERENCES topic(id) ON DELETE CASCADE, target TEXT, target_id INTEGER,
  PRIMARY KEY (topic_id, target, target_id));
CREATE VIRTUAL TABLE topic_fts USING fts5(label, summary, content='topic', content_rowid='id');

CREATE TABLE run (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL,         -- ingest, extract, summarize, rebuild, relabel
  route          TEXT, model TEXT,      -- routing.ROUTES: api, code, local, openai, codex, gemini
  fallback_from  TEXT,                  -- the route it fell back from, if any
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
  status       TEXT NOT NULL,           -- done, invalid, failed, skipped
  in_tokens INTEGER, out_tokens INTEGER, dropped INTEGER, error TEXT,
  PRIMARY KEY (run_id, revision_id, segment)
);
```

Notes on the schema:

- Claims and edges point at snippets, snippets at revisions. A superseded revision keeps its snippets as long as an
  accepted claim or edge cites them (history and replay need them); others are deleted by the vacuum step
  (section 13.4).
- `entity.merged_into` records a confirmed merge (alias of a G1 `same`, or a PM merge). Readers follow it one step.
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
- **Decisions and risks**: G1 has no id for them. G2 adds `d-` and `r-` ids with the same key functions as G1
  (`identity.tracker_id`, `ref_of`, `scope_match`): a Tracker id first, then an explicit reference, otherwise a
  suggestion. New code in `identity.py`, owned by S1.
- **People**: `identity.person_id` (the voice library aliases, `me` for the PM), as `person:<casefold>`.
- **Clients**: `client:<slug>` from the client folders `client_scope.decide` finds.
- **Everything the LLM finds** (topics, systems, documents, people not in the voice library):
  `e-<sha1(type|normalised name|client)>`. A new name that matches an existing alias of the same type and client
  joins it; a near match (same type, alias vector cosine at least 0.85, or edit distance at most 2 on names of 6+
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
into the rules file, not re-invented). A `proposal` never becomes `confirmation` without a later quote that confirms
it from a primary source or a PM edit.

### 7.3 PM edits win

- Every PM action already in the register (`edit`, `same`, `split`, `reject`, `drop`, `done`, `agree`) is ingested
  as a `pm_override` row and applied after every extraction. Extraction never writes over a field with a
  `pm_override` and never re-suggests a pair the PM rejected (the G1 `reject` rule, extended to `same_as` edges and
  aliases).
- `entity.pm_locked = 1` freezes name, type and merges of that entity for extraction.
- PM actions done on the store itself (confirm or reject a suggested edge or alias, later in G3 or G5 UI) are written
  as `pm_override` rows with `via = app`.

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

A segment is whole snippets of one revision up to 6000 characters, with the previous snippet repeated for context
(not extracted from twice: only quotes inside the segment's own snippets are accepted). The segment text is sent
with line numbers. A transcript segment keeps speaker names and timestamps.

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
  text, people with aliases, recent topics), chosen by FTS and alias match on the segment;
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
9. Modality guard: `confirmation` from a derived source becomes `statement`; a hedge word inside the quote makes
   `confirmation` a `proposal`.

All of this is pure code and runs the same for every route.

### 8.5 Background, never during a call

- The worker is a sidecar command (`evidence pass`) the app starts: after a call ends and its notes are written,
  after the folder watcher sees changes (debounced 10 minutes), at most once per hour while the Mac is idle and on
  power, and by the PM's "Index now".
- A call in progress is a file the app writes: `<LT_AI_STATE>/call-active.json` (`{"started_at", "pid"}`), created
  at call start and removed at the end, next to `embed-server.json`. A file older than 6 hours or with a dead pid is
  stale. The worker checks it before every segment and before every model request; when it appears, the worker
  finishes writing the segment it already received, records `status = paused_call` and exits. No request is sent
  while the file is present. The worker does not take the embed server either while it is present.
- The worker runs at low priority (`nice 10`), one at a time per project (the writer lock), at most one model request
  in flight per route (the plan limits are shared with the PM's prep work).

### 8.6 Routing

- A new routing job `index` joins `JOBS = ("prep", "tips", "after")`. Its route defaults to the route of `after`
  and can be set separately in Settings; `LT_AI_ROUTE` works as for other jobs, but the general fallback chain
  (`CHAIN_DEFAULT`) is not used: extraction falls back only to `local`, never to another cloud route.
- Model per route: the cheaper model of each route for extraction (api `haiku-5-5`, openai `gpt-6-luna`, gemini
  `gemini-3.5-flash-lite`, codex `codex/default`, code with its smallest model, local `LT_LOCAL_MODEL` or
  `gemma4:26b`) and the route's `after` model for topic summaries. The model names come from `rules/ai.yaml`, not
  code. Section 15, question 2 asks the owner about this default.
- Fallback to local (Ollama, `num_ctx` 65536, `127.0.0.1:11434`): when the chosen route is not connected, is capped
  by `cost.ProviderCaps`, or the pass ceiling is reached and the PM allowed local overflow (question 1). If Ollama
  or the model is not installed, the pass stops with `status = off` and says so; it never picks another cloud route
  the PM did not choose.

### 8.7 Topic summaries and global questions

- **Topics**: deterministic communities over accepted edges and co-mentions (entities cited by the same snippet),
  weighted, by label propagation with a fixed seed and a size cap of 40 entities, per client label (a topic never
  mixes two clients; shared entities may sit in both). Pure Python, no graph library.
- **Summary**: per topic whose `member_hash` changed, one model request on the `index` route's summary model with the
  topic's accepted claims and edges (with dates, modality, quotes) up to 6000 tokens; the output must cite claim ids
  and is validated like section 8.4 (every sentence cites at least one claim; uncited sentences are dropped). Stored
  in `topic.summary`, marked derived, never evidence.
- **When**: eager at the end of a pass for topics touched by it, inside the same ceiling (question 3 offers lazy).
- **Global questions** ("what about SSO over all time"): `evidence.ask_global(query, scope, as_of)` finds topics
  by `topic_fts` and alias match, returns their summaries plus the dated claims behind them as a timeline. Answering
  in prose is the caller's model request (G3 preparation or G5), never the live path.

## 9. Cost model

All numbers below come from the script in Appendix A, run in this step (`python3 -I cost_model.py`); anyone can
rerun it. Prices are `cost.py` `PRICES` (USD per 1M tokens: input, cache read, output);
characters per token per route are `rules/ai.yaml` (latin / cyrillic: api and code 2.5 / 1.6, codex and openai 4.0 /
2.6, gemini 4.0 / 2.8, local 3.8 / 2.4); the plan-route anchor is from the plan doc: one Codex preparation of about
250K tokens took 4% of the 5-hour window. Measured: prices, ratios, the anchor. Assumed: the project shape and the
per-segment overhead, marked below.

**Assumed typical project** (no real folder was read): 40 past calls with a 45,000-character transcript each (the
follow-up step already caps transcripts at 60,000, `followup.py:49`) and 4,000 characters of notes; 150 folder
documents of 12,000 characters of extracted text; 15 site pages of 5,000; 30,000 of project state; half the text in
Ukrainian. That is 3.87M characters and 678 segments. Per segment: a 1,500-token cacheable prefix, a 500-token
entity catalog, 500 output tokens (JSON with quotes). 40 topic summaries of 6,000 input and 600 output tokens,
priced in the table on the same model as extraction (question 3 gives the after-call model price).
A typical week after the first index: 3 calls, 10 changed documents, 5,000 characters of state, 6 topics: 272,000
characters, 48 segments.

| Route and model | First full index | One week incremental |
|---|---:|---:|
| tokens on api (fresh in / cached prefix / out) | 2.56M / 1.08M / 0.36M | 0.20M / 0.08M / 0.03M |
| api `haiku-5-5` | $0.45 (no cache $0.55) | $0.03 |
| api `sonnet-5-5` | $8.86 (no cache $10.90) | $0.68 |
| openai `gpt-6-luna` | $0.37 | $0.03 |
| openai `gpt-6.1-sol` | $7.35 | $0.58 |
| gemini `gemini-3.5-flash-lite` | $1.47 | $0.11 |
| gemini `gemini-3.8-flash` now / from 2027-01-01 | $2.76 / $5.51 | $0.22 / $0.43 |
| codex plan, 3.25M tokens | about 52% of a 5-hour window | about 4% |
| code plan (Claude Code), 4.0M tokens | not measured: no anchor for the Claude plan yet | not measured |
| local `gemma4:26b`, 3.33M tokens | $0, 4.7 to 9.5 hours (assumed 300 to 600 tokens/s prefill, 15 to 30 tokens/s output; not measured) | 0.4 to 0.7 hours |

What the numbers say:

- Output is 40% to 62% of the dollar cost on every cloud route (output prices are 5 to 8 times input), so the
  output cap per segment and short quotes matter more than input trimming.
- The cacheable prefix saves about 18% to 20% on api and openai (e.g. Sonnet $10.90 -> $8.86). S2 sends the
  prefix first and identical on every request so the providers' prompt cache applies.
- On the cheap model of each route the first full index costs under $1.50; on the `after` models of api and openai
  it costs $7 to $9. The weekly incremental is cents.
- On the Codex plan the first full index does not fit one 5-hour window next to normal work; it has to spread over
  several passes under the ceiling. On local it is an overnight job.
- The G0 fixture is 10,383 bytes (20 files): about 3 segments, far below one cent on any route; it is not a cost
  reference.

### 9.1 Ceiling

- One ceiling per pass, in the unit of the route: USD on `api`, `openai`, `gemini` (default $1.00 per pass), percent
  of the 5-hour window on `codex` and `code` (default 10%, read from `ai-accounts.json` before the pass and after
  every 10 segments through the existing `account_usage_*` functions), wall-clock minutes on `local` (default 120).
  Defaults are question 1.
- The estimate before each segment uses `size.estimate` on the segment plus the prefix and the output cap; the pass
  stops before the request that would cross the ceiling (`status = ceiling`). The monthly `cost.ProviderCaps` and
  `BudgetGuard` stay in force on top.
- Plan routes also stop when the 5-hour window is above 80% used (the same `near_share` idea as prep), so indexing
  never eats the limit the PM needs for the next preparation.
- Spend is written to the existing `spend-ledger.jsonl` with job `index`.

## 10. Settings and cost text (text only, Design later)

Settings > AI, a new row under the routes (only when the flag is on):

- Title: "Project memory". Help line: "Pacenote reads your project's calls, notes and files on this Mac and keeps
  who and what they mention, with quotes. It never runs during a call."
- Per project: "Indexed 412 of 678 parts. Last pass: Nov 23, 14:10, 1.2M tokens, $0.31 on Claude key (Haiku).
  Next pass: about 48 parts, about $0.03." On plan routes: "about 4% of your 5-hour ChatGPT limit". On local: "about
  25 minutes on this Mac, free".
- Controls: "Index now", "Pause", the route ("Use for indexing: same as after the call / <route list>"), the ceiling
  ("Stop each pass at $1.00 / 10% of the 5-hour limit / 120 minutes on this Mac"), the switch "Use AI to read
  sources" (off = deterministic links only, no model requests), and "Delete this project's memory".
- Before the first full index of a project, a confirmation: "First index of Northpoint: 192 sources, about 3.9M
  characters. On Claude key (Haiku): about $0.45, in 1 pass. On your ChatGPT plan: about 52% of a 5-hour limit, in
  6 passes. On this Mac: 5 to 10 hours, free. Your files are sent only to the AI you chose for this job." Buttons:
  "Start", "Use this Mac only", "Not now".
- After a pass, in the same row: "Done: 230 parts, 0.9M tokens, $0.29 (estimate was $0.31)". A stopped pass says
  why: "Stopped at your $1.00 ceiling", "Paused: a call started", "Local model not installed".

Strings go to `rules/i18n/en.json` and `uk.json` together when S3 builds them.

## 11. Search API

### 11.1 Signature (Python, `livetips_m0/evidence/search.py`)

```python
search(store, query, *,
       scope,                    # Scope(project_uid, client, others, allow_shared=True) from client_scope.decide/from_sheet
       as_of=None,               # datetime; None = now. Replay passes the call start.
       window=None,              # (from, to) on said_at; None = all time
       exclude=(),               # source ids or uris excluded for this request (Sources "skip", this call's own files)
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

1. **Access**: `source.state = 'active'` (excluded, deleted and revoked never pass); not in `exclude`; the snippet's
   `client_set` is empty, equals `scope.client`, or contains it and the line does not name only another client (the
   `client_scope.filter_text` rule); claims and edges need every evidence row to pass the same check, otherwise the
   claim is dropped (not shown with fewer quotes). No scope (a one-client project) passes everything of the project.
2. **Time**: with `as_of`, only revisions with `observed_at <= as_of` and (`superseded_at IS NULL` or
   `superseded_at > as_of`), snippets, claims and edges with `said_at <= as_of`, and topic summaries with
   `summary_at <= as_of` (a summary written later never appears in a replay, even if its claims are old). `window`
   narrows on `said_at`.
3. **Candidates**: FTS5 on snippets and topics; alias match of query words to entities (casefolded, both languages);
   with `query_vec`, cosine over the allowed snippets' vectors.
4. **Graph step**: from matched entities, `hops` steps over accepted edges whose both ends and evidence passed 1
   and 2 (suggestions only with `include_suggestions`).
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

`python -m livetips_m0 evidence status|ingest|pass|search|card|ask|rebuild|vacuum|delete --project-uid ... --root ...`
with JSON output, so the Swift app and the skill use one interface.

## 12. Privacy

- The store is one SQLite file per project in `~/Library/Application Support/LiveTips/evidence/`, never in the
  project folder, never synced by Drive, never sent to the Pacenote server (call sync, D27 and D47 unchanged).
- Model requests go only to the route the PM chose for the `index` job (or local). The same provider already
  receives these files during preparation; G2 adds no new destination. The first full index asks before it starts
  (section 10) because it sends more text than one preparation does.
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
  2 seconds for it and otherwise reads the last committed state.
- Many readers (prep, the live engine, the skill CLI) use WAL snapshots and never block the writer.
- Every source revision is one transaction; a crash leaves the previous revision active and the segment
  `pending`.

### 13.2 Failures

| Failure | Behaviour |
|---|---|
| Route error, timeout, rate limit | segment stays `pending`, back-off doubles from 1 minute to 1 hour, the pass ends with `failed` after 3 consecutive errors |
| Invalid output twice | segment `invalid`, retried only after a rules or route change; counted in `evidence status` |
| Ceiling reached | `status = ceiling`, the rest stays `pending` for the next pass |
| Call starts | `paused_call`, section 8.5 |
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
  `pm_override` rows are kept and re-applied, so PM edits survive a rebuild.
- **Project renamed**: same uid, nothing to do. **Folder moved** through Settings: same uid; `root_hint` updated;
  source uris are relative so nothing is re-read (hashes are equal). A folder re-added as a new project gets a new
  uid and a new store; the old one is listed in status as orphaned and deleted after confirmation.
- **Vacuum** (weekly, outside calls): purge snippets of superseded revisions nobody cites, purged sources, old
  `run_item` rows (keep 90 days), then `VACUUM`.

## 14. G0: how the store is measured

### 14.1 Variants

- **A**: current code (exists).
- **B**: wider text search without a graph: the same store's snippets and FTS5 over all calls of all series and all
  folder files without the 7-day window, scope and time filters on, no entities, no edges, no topics. It answers "is
  it just more context?".
- **C**: B plus entities, claims, edges, `hops=1..2`, topics and `ask_global`.

The bench makes no model call and blocks every host (`run.py`). Variant C therefore uses a **checked-in extraction
fixture**: `bench/g0/fixtures/extract/<content_hash>.json`, the validated output for every fixture segment, written
once by a real extraction run and reviewed by hand. The hand-reviewed fixture covers only the small set (about 3
segments). The scale set's noise generator writes, with the same seed, the extraction output of each noise document
next to it (entities and claims built from the template it filled), so the scale set needs no review and the bench
stays deterministic and model-free. C replays it through the same validator (section 8.4), so the
validator and the store are measured, not the model. A separate opt-in command, `run.py --variant C --extract
<route>`, runs real extraction on a route outside CI and reports per-route extraction precision against the fixture
(entities, claims, edges with quotes), tokens and cost; it is the only G0 run that calls a model.

### 14.2 Why harder gold

The live answer rate is already 100% because the fixture is 10 KB and every question shares words with its file.
New gold (`gold.yaml` version 2, all invented, US names, same two clients) that A cannot pass by word match:

1. **Cross-series and multi-hop**: "What is blocking the pilot launch?" needs pilot -> depends on SSO -> blocked by
   item 13 (technical sync, Emily) -> waiting for client confirmation (weekly, Mike). Answer needs quotes from 2
   calls of 2 series.
2. **Paraphrase and other language**: "Is the login problem still open?" (no "SSO", no "item 13" in the question);
   a Ukrainian question about the English notes where no word matches, only the entity alias does.
3. **Global**: "What happened with SSO since October?" expects a dated timeline of at least 4 facts from 3 sources
   in order (topic summary plus claims).
4. **Time replay**: a go-live date changed on Nov 19; replayed as of Nov 17 the answer must be the old date and the
   Nov 19 fact must not appear (future leak).
5. **Modality**: "Did Globex agree to 39 hours?" where 39 hours was only proposed: the answer must carry
   `proposal`; a fact said only in generated notes must not count as confirmed.
6. **Exclusion and revocation**: one file excluded by the PM and one deleted file, each with a unique fact; neither
   may ever appear.
7. **Scale**: a seeded noise generator adds about 2 MB of plausible project text (40 extra documents, 10 extra
   calls) so that ranking and latency are measured with distractors; the 10 KB set stays as the "small" set.

### 14.3 New metrics (added to the results JSON, A and B report them too)

| Metric | Meaning |
|---|---|
| Hop answer rate | multi-hop questions with every needed quote in the top 6 |
| Paraphrase answer rate | paraphrase and cross-language questions answered in the top 3 |
| Global timeline recall | share of expected timeline facts returned by `ask_global`, and order correct (yes/no) |
| Evidence recall | gold facts returned as a claim or edge with a valid quote and locator |
| Quote validity | returned quotes that are verbatim at their locator (must be 100%) |
| Modality accuracy | modality questions with the expected modality; derived-only facts counted as confirmed (must be 0) |
| Future leaks | items with `said_at` or `summary_at` after `as_of` (must be 0) |
| Excluded leaks | items from excluded, deleted or revoked sources (must be 0) |
| Store p50 / p95 ms | `search(mode="live")` and `mode="prep"`, small and scale sets |
| Index cost | segments, tokens (deterministic count from the fixture), re-extracted segments after changing one file (must be that file's only) |

Existing columns stay: prep coverage, prep leaks, live answer rate, answer text, live leaks, lookup p50/p95,
duplicates, wrong identity, item 13 cross-client, false agreed, false proposal.

## 15. Questions for the owner (product choices only)

1. **Ceiling per pass and overflow.** Options: (a) $1.00 per pass on paid keys, 10% of the 5-hour window on plans,
   120 minutes on local, and on reaching the ceiling wait for the next pass; (b) the same, but continue on the local
   model when the ceiling is hit; (c) no automatic ceiling, ask before every pass. **Recommendation: (a)**, with
   local overflow as a setting the PM can turn on. A first index of a typical project then finishes in 1 to 2 passes
   on a cheap paid model and in about 6 passes on the Codex plan.
2. **Which model reads sources.** Options: (a) the cheapest model of the chosen route (Haiku, GPT-6 Luna, Gemini
   3.5 Flash-Lite: $0.37 to $1.47 for a first index); (b) the route's after-call model (Sonnet, GPT-6.1 Sol: $7 to
   $9). **Recommendation: (a)** for extraction, the after-call model only for topic summaries; G0 `--extract`
   compares quality and we switch if the cheap model misses too much.
3. **Topic summaries now or on demand.** Options: (a) eager: summarise changed topics at the end of each pass, inside
   the ceiling (global questions answer at once); (b) lazy: only when a preparation or
   the PM asks a global question (cheaper, the first answer waits for a model request). 40 topics cost about $0.04
   on Haiku and about $0.73 on Sonnet (240K fresh input, 24K output). **Recommendation: (a)**.
4. **Consent for the first full index on a cloud route.** Options: (a) ask once per project before the first full
   index, showing the volume, provider and cost (section 10); (b) no prompt, the flag is consent enough; (c) local
   only for the first index, cloud for increments. **Recommendation: (a)**.

## 16. Risks

- **Invented links** (V3's main risk): every edge needs a verbatim quote and a word of both ends, ambiguous ones stay
  suggestions, the G0 extraction fixture is reviewed by hand, and `--extract` measures precision per route.
- **Synthetic gold proves correctness, not usefulness**: gains on the owner's real calls stay "not verified" until
  G3 runs on live calls (the plan's section 10 risk, unchanged).
- **Cost of the first index on plan routes**: about half a Codex 5-hour window for a typical project; the ceiling
  and the 80% stop keep it from blocking preparation, at the price of an index that takes days to complete.
- **Local model speed and quality**: not measured; S2 measures tokens per second on the owner's Mac class before the
  fallback is offered in the UI.
- **Two sources of truth for tasks**: the register stays canonical; the store only reads it and writes PM actions as
  register events, never its own task state.
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

### S1. Store, deterministic ingest, search, G0 variants B and C plumbing (core)

Scope: `livetips_m0/evidence/` (schema, migrations, store open/lock, ingest per source type with change detection and
client labels, deterministic entities and claims from the register, Tracker snapshot, sheets, voice aliases, `d-`/`r-`
ids in `identity.py`, `pm_override` from register events), `search` / `entity_card` with access and time filters,
the CLI, `gold.yaml` v2 and the scale set, VariantB and VariantC (deterministic part) in `run.py`.

Acceptance:
- [ ] The store is created only under `<support>/evidence/<uid>.sqlite`; a test asserts nothing is written under the
      project root.
- [ ] Changing one file creates exactly one new revision; touching mtime without content change creates none; a
      deleted file is `deleted` and absent from search; register `migrate`/`rollback` triggers a refold.
- [ ] Access tests: excluded, deleted, revoked, another client's file, another client's line in a shared file, a
      claim with one out-of-scope evidence row: none returned, and each case is filtered in the SQL candidate query
      (checked by a test that ranks nothing it was not allowed).
- [ ] Time tests: `as_of` before a revision, before a claim and before a summary returns none of them.
- [ ] PM edits from the register survive a rebuild.
- [ ] Two processes: a reader during a write sees the previous complete revision.

G0 gate: B and C run on the small and scale sets; C: prep and live leaks 0, future leaks 0, excluded leaks 0, quote
validity 100%, evidence recall for register and Tracker facts 100%; A's columns unchanged; `mode="live"` p95 on the
scale set at most 150 ms.

### S2. LLM extraction, validation, routing, ceiling (core)

Scope: `rules/evidence.yaml`, `rules/evidence_schema.json`, generated `skill_evidence.md` and the skill copy; segmenter,
prompt builder with cacheable prefix, validator (section 8.4), the `index` routing job on all six routes with local
fallback, the worker (`evidence pass`) with the call-active check, the ceiling (USD, percent, minutes), estimates and
run accounting, the extraction fixture for G0 and `--extract <route>`.

Acceptance:
- [ ] Validator unit tests: a non-verbatim quote, a wrong line range, an unknown enum, a cross-client quote, a hedge
      under `confirmation`, a derived `confirmation`, an override field, a rejected pair: each dropped or downgraded
      as specified.
- [ ] A fake provider that returns oversized, invalid and slow answers: caps hold, the repair retry happens once,
      the pass ends `failed` after 3 errors.
- [ ] `call-active.json` present: no request is sent (fake provider counts 0) and the run ends `paused_call`.
- [ ] Ceiling: the pass stops before the request that would cross it; `est_usd`/`usd`, `pct_before`/`pct_after` and
      `seconds` are filled per route kind; spend goes to `spend-ledger.jsonl` with job `index`.
- [ ] Re-running a pass with no changes sends 0 requests.

G0 gate: C with the extraction fixture: hop answer rate at least 2 of 3 multi-hop questions (A and B: expected 0 to
1), paraphrase answer rate above B, modality accuracy 100%, derived counted as confirmed 0, all S1 zeros still 0;
`--extract` on one cheap route reports precision and cost (recorded, not gated).

### S3. Topic summaries and global questions (core)

Scope: deterministic communities per client, summary requests inside the pass ceiling, summary validation with claim
citations, `topic_fts`, `ask_global`, staleness by `member_hash`.

Acceptance:
- [ ] Same input, same topics (seeded); no topic mixes two clients.
- [ ] A summary sentence without a valid claim citation is dropped; summaries never appear as evidence or as a
      confirmation.
- [ ] Only topics whose members changed are re-summarised.
- [ ] `ask_global` with `as_of` never returns a summary written after `as_of`.

G0 gate: global timeline recall at least 75% with correct order on the global gold, future leaks 0, everything from
S1 and S2 unchanged.

### S4. App integration: flag, project uid, scheduler, settings and cost text

Scope: Swift: `evidence_uid` in `projects.json`, `LT_PROJECT_UID`, `call-active.json` written at call start and
removed at the end, triggers (after notes, folder change debounce, idle hourly, "Index now"), the settings row and
the first-index confirmation with the texts of section 10 (en and uk i18n), delete per project, status line; docs EN
and UK; CHANGELOG.

Acceptance:
- [ ] Flag off: no store file is created, no worker runs, no setting is shown (test and `swift build`).
- [ ] Project rename and folder change keep the uid; re-adding a folder creates a new uid and lists the old store
      as orphaned.
- [ ] `call-active.json` exists for the whole call and is removed after it, including after a crash on the next
      launch.
- [ ] The cost text before and after a pass shows the numbers from the `run` row (pure-logic tests on the
      formatter); screenshots only through the harness gate (L33).

G0 gate: no change to any G0 column (the app wiring is not on the bench); `CoreChecks` and `swift build` green.

Order: S1 -> S2 -> S3, S4 can start after S1 (it needs only the CLI and the flag) and must land before the flag is
offered to the owner. S1 goes first because it makes every later gate measurable.

## Appendix A. Cost model script

Saved as `cost_model.py` anywhere outside the repo and run with `python3 -I cost_model.py`; it prints the numbers of
section 9. Inputs: `cost.py` prices, `rules/ai.yaml` characters per token, the 4% per 250K Codex anchor, and the
assumed project shape of section 9.

```python
SEG = 6000; PREFIX = 1500; CATALOG = 500; OUT = 500
CYR = 0.5
CPT = {"api": (2.5, 1.6), "codex": (4.0, 2.6), "openai": (4.0, 2.6), "gemini": (4.0, 2.8), "local": (3.8, 2.4)}


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
    print("  codex plan: %.2fM tokens = %.0f%% of a 5-hour window at 4%% per 250K" % ((f + c + o) / 1e6, (f + c + o) / 250000 * 4))
    f, c, o = parts("local", ch, segs, topics)
    for pre, gen in ((300, 15), (600, 30)):
        print("  local gemma4:26b at %d tok/s prefill, %d tok/s out: %.1f h" % (pre, gen, ((f + c) / pre + o / gen) / 3600))
```

## Підсумок українською

1. PB-52 (GraphRAG G2): локальне сховище доказів у SQLite в Application Support, окремий файл на проєкт за новим
   `evidence_uid`, ніколи в папці проєкту, за прапорцем `evidence_store`.
2. Схема: джерела з ревізіями (sha256 тексту), уривки з локаторами, сутності з ідентичністю G1, твердження з
   модальністю, ребра з цитатами, аліаси, правки PM, теми з підсумками, проходи з вартістю.
3. Спершу детерміновані звʼязки (реєстр, Tracker, люди, шити), потім LLM-екстракція з усіх джерел у фоні, ніколи
   під час дзвінка (файл `call-active.json`), інкрементально.
4. Контракт промпту і схема виходу в `rules/evidence.yaml` і `rules/evidence_schema.json`, спільні зі скілом;
   валідатор перевіряє дослівність цитат, клієнта, модальність, правки PM.
5. Модальність: proposal / expectation / confirmation / statement, мапінг зі значень G1; підсумки й нотатки ніколи
   не рахуються незалежним підтвердженням.
6. Пошук: доступ, клієнт (PB-70) і час (`as_of`) фільтруються в SQL до ранжування; режим live без нових мережевих
   і модельних викликів.
7. Вартість першого індексу типового проєкту (оцінка): $0.37-1.47 на дешевих моделях, $7-9 на Sonnet або GPT-6.1
   Sol, близько 52% 5-годинного вікна Codex, 5-10 годин локально; тиждень інкрементально: центи.
8. Стеля на прохід у долари, відсотки ліміту або хвилини, показ вартості до і після, вимикач і видалення памʼяті.
9. G0: варіанти B і C, фікстура екстракції без викликів моделі, складніший еталон (кілька кроків, перефразування,
   глобальні питання, реплей у часі, модальність, виключення, масштаб).
10. Розбиття: S1 сховище і пошук, S2 екстракція і стеля, S3 теми і глобальні питання, S4 інтеграція в застосунок;
    4 питання до Bohdan: стеля, модель, підсумки тем, згода на перший індекс.
