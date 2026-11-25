# PB-53: Related evidence in call preparation (GraphRAG G3)

Status: spec, step 1 of PB-53. No product code in this step. Everything below is behind the flag `evidence_store`
(`features.evidence_store` in the app's `config.json`, off by default). With the flag off nothing in this spec runs:
no store file is opened, no source row is added, the prompt is byte-equal to today's.

Sources read for this spec: `docs/specs/PB-52-g2-evidence-store.md` (sections 1-17, in particular 5.3, 7.4, 8.6,
8.7, 11, 12, 13.1, 14 and the S1-S5 implementation notes), the private roadmap block G (row G3), the GraphRAG plan
`research/pacenote-evidence-graph-plan-2026-11-21.md` (G3 row and the "synthetic gold" risk), and the code on this
branch (main `5226526`, PB-87 merged): `evidence/search.py` (`search`, `entity_card`, `ask_global`, `Budget`),
`evidence/__main__.py` (`status`, `delete`), `evidence/__init__.py` (`enabled`), `sheet/build.py` (`build`,
`report`, `CLAUDE`, `PACKED`), `sheet/sources.py` (`auto_sources`, `candidates`, `expand_folders`, `check`),
`sheet/size.py` (`per_pass`, `_prio`, `report`), `sheet/prompt.py` (`user_packed`, `agent_prompt`),
`sheet/schema.py` (`SOURCE_TYPES`, `_fact`), `sheet/lookup.py` (`snippets`, `sheet_passages`,
`sheet_source_paths`), `engine.py` (`_project_snippets`, `_sheet`), `memory.py`, `prepcontext.py`, `localprep.py`,
`routing.py` (`ROUTES`, `JOBS`, `chain_order`, `chain_allowed`), `rules/ai.yaml` (`windows`), `rules/evidence.yaml`,
`skill/SKILL.md`, and in Swift `MeetingSheetDraft.swift`, `MeetingSheetPrep.swift`, `MeetingSheetView.swift`
(`SheetSourceReportRow`), `SheetRunner.swift`, `PrepRunner.swift`, `Evidence.swift`, `EvidenceService.swift`. The
G0 bench `spikes/m0/bench/g0/run.py` (variants A, B, C, `hop_items`, gates), `gold.yaml`, `gold_v2.yaml` and the
results `A|B|C-2026-11-25-s4r2.json`. One probe was run in this step on a store built from the G0 fixture (section
12.1).

Owner decisions this spec implements (D57, 21.11, 24.11):

- Fully local: the store, the selection, the ranking and the snapshot run on the Mac. Selection makes no model call
  and no network request except the loopback embed server that G2 already uses for query vectors.
- Everything behind the flag `evidence_store`; flag off means no behaviour change at all.
- Nothing new leaves the Mac: the evidence block goes only to the preparation route the PM already chose, which
  today receives project text, and holds only text from the project's own sources (section 10).
- Client scope and exclusions are applied before ranking (G2 section 11.2, unchanged; G3 adds the PM's per-file and
  per-item choices in Sources to the request's `exclude`).
- PM edits win; generated summaries never count as confirmation; every evidence item carries a quote and a locator.

## 1. Problem

Preparation picks context by recency and series, not by what the call is about. `auto_sources`
(`sheet/sources.py:532-618`) takes the last 3 calls of the same series (`previous_calls(..., 8)` then `[:3]`), the
Tracker, project state, and folder files of the last 7 days (`FOLDER_DAYS=7`) up to the pass budget. On the G0
fixture this gives prep coverage 80% (16 of 20 facts) for A, B and C alike (`results/*-2026-11-25-s4r2.json`,
B and C reuse A's preparation). The 4 misses:

| Target | Missed fact | Why A misses it |
|---|---|---|
| Acme weekly status, Nov 17 | "blocks the SSO integration" | in the Acme technical sync notes (other series) |
| Acme weekly status, Nov 17 | "change freeze from December 15" | in the 4th call back of the series |
| Globex weekly, Nov 18 | "net 30" | in a folder file older than 7 days |
| Acme technical sync, Nov 19 | "waiting for client confirmation" | in the Acme weekly notes (other series) |

G2 put a store on main that can find these by entity, path and time, with access applied before ranking, but no
preparation step calls it. G2 section 11.3 sketched the call; this spec makes it concrete and corrects three points
of that sketch that the code does not support (section 3).

## 2. Goals and non-goals

Goals:

1. A **related evidence block** in the Meeting Sheet build: items from the store that today's sources do not
   already carry, chosen for this call, within a fixed budget, each with a quote, a locator, a date and a visible
   **reason** (why this item is here).
2. The block is the **same on every AI route** (api, code, codex, openai, gemini, local): built once in Python before
   the route branch, byte-equal across routes, tested.
3. The block appears in **Sources** as one parent row with one child row per item, so the PM sees each item's reason
   and can untick an item or the whole block, and the model's facts can cite an item.
4. A **snapshot** of the block and the cards of the call's entities is written to Application Support for G4 (live)
   and the skill. Loading it needs zero network or model calls.
5. Defined behaviour for an **empty, partial, stale or unavailable** store: fall back to today's path unchanged
   (prompt bytes equal), and say so in Sources.
6. **G0 replay** of A, B and C preparation on a new prep gold, plus a plan to measure the first real index on the
   owner's own projects.

Non-goals:

- No change to the live path (G4), the after-call path (G5) or the store itself (G2) beyond the additions listed in
  section 16. G3 adds no new store table; it adds a `coverage` field to `evidence status`, a selection module, a
  snapshot writer and loader, and the snapshot rewrite in the state-only pass.
- No new UI design. Section 6 gives the reason line and the Sources texts; Design shapes them later.
- No evidence in the legacy Live Brief path (`PrepRunner.swift:380`: transcript calls and forced routes through
  `prepcontext.py`/`localprep.py`). That path is used only for `transcript` calls; it stays as it is and its
  Sources say nothing about project memory.
- No prose answer to global questions in preparation. `ask_global` sentences go into the block as derived items;
  the preparation model writes the sheet as today.
- No index pass started by preparation. Preparation reads the store as it is (section 8). G2's triggers keep it fresh.
- No reading of the owner's real calls in tests or the bench. The real-index plan (section 12.5) keeps results on
  the owner's Mac, counts only.

## 3. What G3 changes in G2's section 11.3 sketch

G2 section 11.3 said: `mode="prep"`, `as_of` = call start, scope from the sheet, `hops=2`, budget from the route's
`size.per_pass * near_share` minus what `auto_sources` took, snapshot at
`<AppPaths.support>/evidence/snapshots/<evidence_uid>/<stem>.evidence.json`, deleted by the state-only pass. The
code read in this step changes three points:

1. **Budget per route breaks route parity.** `per_pass * near_share` is 200,000 tokens on api, code, codex, openai and
   gemini but 44,028 on local (`size.py:75-84`, `rules/ai.yaml` `windows`, `near_share: 0.8`, `pass_cap: 250000`), and
   characters per token differ per route. A route-dependent budget would give local a different block. G3 uses a
   **fixed budget in characters and items** (section 5), the same on every route, and lets the existing size plan
   (`size.plan_passes`, `compress_plan`) handle a pack that is too big, as it does for every other source.
2. **`search()` does not walk hops.** `Budget.hops` exists (`search.py:39-41`) but `search` never reads it; the
   2-hop walk exists only in the bench (`run.py` `hop_items`, line 1058). G3 S1 moves a hop walk into product code
   (`evidence/related.py`), and G3 S2 switches the bench to it under the gate "S3 and S4 columns unchanged".
3. **Deleting the whole snapshot on a state change is coarser than needed.** G3 rewrites the snapshot without the
   items of the source that left `active` (section 7.4); the rest of the call's evidence stays usable. A snapshot
   whose every item is removed is deleted, as G2 said.

Everything else in 11.3 stands: `mode="prep"`, `as_of` = the call start, the scope the build decides (`CS.with_scope`
at `build.py:974`, kept in `sheet["client_scope"]`), `include_suggestions=False`, `query_vec` allowed, the snapshot
path in Application Support and never in the project folder.

## 4. Where evidence enters the build

### 4.1 Today's build, in the order that matters here (`sheet/build.py` `build`, lines 966-1498)

1. `CS.with_scope` decides the client scope (974).
2. `raw` sources: `s-meeting` (1066), `req.sources` (the PM's ticked sources from the wizard), `s-context` (1070).
3. `auto_sources(req)` appended (1077-1103), deduplicated by path; then `auto_pick` folder files (1105-1127).
4. `SRC.normalize` (1131), `check_one`/`SRC.check` per source (1139-1184), `expand_folders` (1203).
5. Texts dropped from the sheet's source list (1213-1215): "the texts live in the files, not in the sheet".
6. `Z.report` size check (1225-1234).
7. Route branch: `CLAUDE = ("code", "api")` runs `claude -p` with Read/Grep/Glob and inlines pathless sources under
   "## Sources given here" (`prompt.py` `agent_prompt`, 150-167); `PACKED = ("local", "codex", "openai", "gemini",
   "fake")` packs every source under `## Sources` as `=== SOURCE id | type | title | dated | uri ===`
   (`prompt.py` 106-114, `user_packed` 133-142); api without Claude Code and a mid-build chain switch fall back to
   packed passes (1251-1271).
8. Writes `.sheet.json`, `.sheet.v{n}.json`, `.sheet.sources.json` (`report()`), `.sheet.request.json` (the request
   as received), `.sheet.md`, and `derive` outputs (brief, cards, facts) next to the call, in the project folder.

### 4.2 Where G3 enters

Between step 3 and step 4, with the flag on: `related.select(...)` (section 5) returns the block, and the build
appends to `raw`, **only when the block has items**:

- one child per item, `s-ev-1` .. `s-ev-N`, type `evidence_item` (new in `schema.SOURCE_TYPES` and in
  `sources.TEXT_TYPES`, so `SRC.check` takes its text like a `text` source, `sources.py:231`), `parent:
  "s-evidence"`, `auto: true`, `text` = the rendered item (section 6.2), `title` = "Project memory E3: <source
  title>, <date>" (no entity names: the title is what `derive._src_label` copies into cards, `derive.py:53-55`),
  `reason` and `why` = `["related evidence", <reason line>]`, `modified_at` = the item's `said_at`.

The parent source `s-evidence`, type `evidence` (new), title "Related evidence (project memory)", with the readiness
line as `reason`, is **not** put into `raw`: the build adds it to `srcs` after the answer, just before `report()` and
the writes (step 8), with `used: "ok"` when it has children and `used: "not_used"` otherwise. So it is never sized,
packed or inlined, and no route sees a row without text.

Why one source per item: sheet facts cite sources by id (`schema._fact`, `source_ids`, checked against the sheet's
source list at `schema.py:246-260`), and `report()` counts facts per source id (`build.py:1503-1513`). One child per
item lets a fact cite the item it came from, lets the PM untick one item, and lets a later revoke find the facts
that rest on that item (section 7.4). The parent/child shape is the one folder sources already use (`parent`,
`expand_folders`), so the sheet schema and the Sources rows need one new type each, not a new structure.

Why this reaches every route identically: a pathless `text` source in `raw` is (a) size-counted like any source,
(b) packed under `## Sources` on packed routes and (c) inlined under "## Sources given here" on the Claude routes,
and (d) survives a chain switch because the switch re-packs the same `srcs`. The block is built before the branch,
from the store, without any route input, so its bytes do not depend on the route.

What does **not** reach the project folder: step 5 drops `text` from every source, so `.sheet.json` keeps for each
child its id, title, reason, `why`, item kind, `said_at`, and the store key (`item_key`, section 7.1), not the quote.
The reason line does land there (it names entities, like a folder file's title does today); a later revoke cleans
it with the facts (owner question 1).
`.sheet.request.json` is written from `req`, and the block is never put into `req`, so it holds no quote either. A
test asserts that no quote of the block appears in any file under the project root after a build. Model-written
facts that use an item do land in the sheet, brief and cards, as facts from any other source do today (section 10).

### 4.3 Order in the pack and `auto`

- **Pack priority**: `size._prio` gives rank 0 to the PM's sources, 1 to `project_state`, `prev_call`, `tracker`,
  `digest`, `crm`, 2 to the rest. `evidence_item` joins rank 1: it is chosen for this call and small, and a pass
  plan must not push it behind the folder files. Inside rank 1 the items keep their rank order (`n`).
- **`auto: true`**: `check()` runs the PB-70 `filter_text` on an auto pathless text source (`sources.py:324-329`).
  The block is built from rows that already passed the store's access rule, which is line-exact with `filter_text`
  (G2 section 5.3), so `filter_text` should drop nothing. It stays on as a second lock: a dropped line is counted
  (`evidence_lines_dropped`) and is a G0 failure (a store leak or a mangled item), never silently accepted.

### 4.4 Flag off and fallback: byte-equal prompts

With the flag off, `related.select` is not imported and no `s-evidence` row exists. With the flag on and no items
(store unavailable, empty, or nothing related, section 8), nothing is added to `raw`; only the parent row with
`used: "not_used"` and the readiness line is added after the answer (4.2). So the prompt bytes on every route are
equal to the flag-off build. A test pins this on all six routes with a recording responder (the bench's
`prep_context` pattern, `run.py:245-263`).

## 5. Selection and budget

### 5.1 Inputs

`related.select(store, *, meeting, scope, as_of, taken, exclude, user_note, vectors) -> Block`, called by the build:

- `meeting`: title, series slug, start, `theirs` (names and companies), agenda lines if any.
- `scope`: the build's client scope (`CS.with_scope`), the same dict the sheet keeps; `None` passes everything not
  denied (G2 section 11.2).
- `as_of`: the meeting start in UTC (G2 section 11.3). A refresh after the call started still uses the start, so
  nothing said in the call itself enters its own preparation.
- `taken`: the sources the build already holds in full after step 3 (uris of series notes and transcripts, the
  Tracker items, `pacenote/decisions.md`, Current State, folder files with `used = ok` and not `cut`).
- `exclude`: this call's own files (its brief, sheet and notes stems), every file the PM unticked in the wizard
  (new request field `evidence.exclude_paths`, section 9.2) and every item the PM unticked (`evidence.exclude_items`,
  by `item_key`). Paths go to the store's `exclude` (the **ignored** class of G2 section 11.2 step 1: a fact also
  stated by another passing source stays); item keys are dropped after selection.
- `user_note`: the PM's note for this sheet, if any.
- `vectors`: whether the loopback embed server answered; recorded in the block and the snapshot.

### 5.2 Steps (no model call)

1. **Readiness** (section 8). `unavailable` or `empty`: return an empty block with the reason.
2. **Seeds**: the entities this call is about, deterministic, at most 12, in this order:
   - open register tasks visible to the scope at `as_of` whose `add` call is of this series or that name the
     meeting's client (from `IG.register_fold_at(store, as_of, passing=True)`), newest first, at most 6;
   - decisions and risks (`d-`, `r-`) carried by the last 3 calls of the series (`entity_rev` of their notes
     revisions valid at `as_of`), at most 4;
   - people on the invite resolved through visible aliases (`person:<casefold>`), at most 2;
   - entities whose visible alias occurs in the title, the agenda or the user note (the alias match of
     `search.py:371-381`), filling the remaining places.
   Each seed keeps its **seed reason** (section 6.1).
3. **Graph walk**: from each seed, `entity_card(store, id, scope, as_of, exclude)` and up to 2 hops over accepted
   edges (no `same_as`, no suggestions, both ends visible), the bench's `hop_items` logic moved into
   `evidence/related.py`, with the neighbour's claims newest first (the PB-87 rule). Each walked item keeps its
   path (seed, edges, end entity).
4. **Text search**: `search(store, query, scope, as_of, exclude, kinds=("snippet", "claim", "edge"), mode="prep",
   budget=Budget(k=12, chars=12000, ms=1000), query_vec=...)`, with `query` = title, agenda, user note and the seed
   names. Items keep the matched words.
5. **Topics**: `ask_global(store, <names of the first 3 seeds>, scope, as_of, exclude, max_topics=2)`, passing
   sentences only (G2 section 8.7 filters per sentence). Skipped when no seed has a topic.
6. **Drop what preparation already has**: an item whose every passing quote sits in a source in `taken` is dropped
   (it would repeat text the pack already holds). An item with one quote outside `taken` stays.
7. **Rank and cut** (5.3, 5.4).

### 5.3 Ranking

A score per item, all inputs from the item's passing rows (G2 section 11.2: quotes, dates and confirmations come
only from rows this request sees):

| Signal | Points |
|---|---|
| a seed's own claim (status, due, owner, decision) | +3 |
| one hop from a seed | +2 |
| two hops from a seed | +1 |
| text match only | +0, plus the BM25 rank normalised to 0..1 |
| `modality = confirmation` with at least one primary or pm row (G2 section 7.4) | +1 |
| 2 or more distinct primary sources | +0.5 |
| said within 14 days of `as_of` / within 60 days | +1 / +0.5 |
| an open task or a current decision | +1 |
| derived-only (notes, briefs, Current State) | -1 |
| topic sentence | fixed 0.5, own section, never above a fact |

Ties: `said_at` newest first, then the store id. At most 2 items per source (the `FolderIndex` rule), counted
across the whole block. A superseded decision is never an item (G2 section 7.1); a proposal and its later
confirmation are shown as one item with both rows, confirmation first (G2 section 7.2).

### 5.4 Budget: fixed, the same on every route

| Section of the block | Items | Characters per item (rendered) |
|---|---|---|
| Linked facts (seed claims and hops) | at most 10 | reason up to 160, quote up to 300 (the store's quote cap), source and date up to 100: about 560 |
| Related passages (snippets from the text search) | at most 4 | reason up to 160, passage cut at a line end at 900, source and date up to 100: about 1,160 |
| Topic summaries (derived, labelled) | at most 6 sentences of at most 2 topics | sentence up to 400 (the S4 cap): about 460 |
| Whole block | at most 20 items | **12,000 characters** |

With every section full the items would need about 5,600 + 4,640 + 2,760 = 13,000 characters, so the
12,000-character cap binds first and cuts from the lowest score up. The budget is in `rules/evidence.yaml` under a
new `prep:` key (`chars: 12000`, `items: 20`, the per-section limits, `seeds: 12`, `hops: 2`, `ms: 3000`), so the
app and the skill read one table.

What 12,000 characters cost per route (characters per token from `rules/ai.yaml`; latin / cyrillic):

| Route | Tokens of a full block | Share of the pass at `near_share` |
|---|---|---|
| api, code (2.5 / 1.6) | 4,800 / 7,500 | 2.4% / 3.8% of 200,000 |
| codex, openai (4.0 / 2.6) | 3,000 / 4,615 | 1.5% / 2.3% of 200,000 |
| gemini (4.0 / 2.8) | 3,000 / 4,286 | 1.5% / 2.1% of 200,000 |
| local (3.8 / 2.4) | 3,158 / 5,000 | 7.2% / 11.4% of 44,028 |

For scale: today's folder budget alone is `FOLDER_TOKENS = 60000` (`sources.py:47`). On local a full block is
about a tenth of the pass; the size plan handles a pack over the pass as today (passes or compress), equally for
every source. Prep cost rises by the block's tokens on every preparation; G0 reports the increase (section 12.3),
the real-index plan measures it (section 12.5).

Time: `ms: 3000` for the whole selection (the search budget is 1,000 ms of it). Over the limit, selection stops,
keeps what it ranked so far and marks the block `truncated` (said in the parent row, never in the prompt).

## 6. The reason line and the rendered item (text only, Design later)

### 6.1 Reason line

One line per item, built from the item's path, never written by a model:

- Seed's own fact: "About item 13 (open on Mike, from the Nov 10 Acme weekly status)".
- One or two hops: "Linked to item 13 (open on Mike) through: item 13 -> blocks -> SSO integration".
- Decision of the series: "Decision from the Nov 12 Acme technical sync".
- Invite person: "Emily is on the invite; said by Emily on Nov 5".
- Text match: "Matches the call: rollout, checklist" or, found by its vector only, "Close in meaning to the call
  topic".
- Topic sentence: "AI summary of the topic SSO integration, written from 4 facts. Not evidence."

Suffixes, in this order when they apply: "proposal only" (modality `proposal`), "expected, not agreed"
(`expectation`), "from notes only" (derived-only, G2 section 7.4), "said in 2 sources" (2 or more primary sources),
"changed on Nov 19" (a later revision of the same source states something else; the current one is shown).

Every element of a path is a visible entity of this request (G2 section 11.2 step 1: an entity that is not visible is
never a `why` path element, and its name is not returned). Strings go to `rules/i18n/en.json` and `uk.json`
together in S3; the reason line in the prompt is rendered in the sheet's output language.

### 6.2 Rendered item (the child source's text, the bytes the model sees)

```
[E3] Linked to item 13 (open on Mike) through: item 13 -> blocks -> SSO integration
"item 13 is blocking the SSO integration" (statement)
Acme technical sync, Nov 12 2026, live/2026-11-12-acme-technical-sync/2026-11-12-1400-acme-technical-sync.notes.md, lines 12-12
```

A topic sentence carries "(AI summary, not evidence)" after its text and the claim refs it cites (`f1, f3`), and
never a confirmation suffix. The prompt contract gets one sentence in `rules/meeting-sheet.yaml` (generated into the
skill section): "Items marked [E..] are quotes from the project memory, chosen because of the reason line; cite the
item's source id; an AI summary is never a confirmation."

## 7. Snapshot

### 7.1 Format

`<AppPaths.support>/evidence/snapshots/<evidence_uid>/<stem>.evidence.json` (G2 section 11.3), file mode 0600,
written to a temp file in the same folder and renamed, never in the project folder:

```json
{
  "schema": "pacenote.evidence.snapshot/1",
  "evidence_uid": "…", "stem": "2026-11-17-1000-acme-weekly-status",
  "sheet_id": "…", "sheet_version": 3, "built_at": "2026-11-16T18:02:11Z", "as_of": "2026-11-17T10:00:00Z",
  "scope": {"client": "acme", "others": ["globex"], "folders": {"acme": ["docs/acme"]}, "why": "series"},
  "vectors": false,
  "readiness": {"class": "partial", "done": 412, "total": 678, "last_pass": "2026-11-16T14:10:00Z",
                "extractor": "…", "label_universe_sha": "…"},
  "budget": {"chars": 12000, "items": 20, "used_chars": 9310, "truncated": false},
  "seeds": [{"id": "c-1a2b3c4d5e", "name": "item 13 SSO user mapping", "type": "task", "why": "open item of this series"}],
  "sources": [{"uri": "live/…notes.md", "doc_id": null, "title": "Acme technical sync", "kind": "notes",
               "origin": "derived", "source_id": 41, "revision_id": 97}],
  "items": [{"n": "E3", "sheet_source_id": "s-ev-3", "item_key": "edge:…", "kind": "edge", "section": "linked",
             "text": "…", "quotes": [{"quote": "…", "source_id": 41, "revision_id": 97, "locator": {}, "said_at": "…"}],
             "modality": "statement", "status": "accepted", "confirmations": 1, "derived_only": false,
             "path": ["c-1a2b3c4d5e", "blocks", "e-…"], "reason": "…", "score": 5.0}],
  "cards": [{"id": "c-1a2b3c4d5e", "name": "…", "status": "open", "aliases": ["item 13"], "claims": ["claim:…"]}],
  "removed": [],
  "sha256": "…"
}
```

- `item_key` is the store's natural key of the item (`claim:<nkey>`, `edge:<edge key>`, `snippet:<source
  id>:<revision id>:<ord>`, `topic:<topic id>:<sentence ord>`), stable across rebuilds of derived tables (G2 section
  13.4), so the PM's untick and the snapshot survive a store rebuild.
- `sources` are matched by `uri` (with the Google file id or Notion page id where the source has one), never by the
  store's row ids, which a `rebuild` of the store can renumber. `source_id` and `revision_id` are kept for
  diagnostics only. The state-only pass and the loader find a snapshot's items for a source by `uri`.
- `cards`: the seeds' entity cards (name, status, visible aliases, at most 5 claim keys each), at most 12. These are
  what G4 needs to match a live question to the call's entities without a query vector (G2 section 11.4).
- Size: at most 20 items and 12 cards; the file stays under 64 KB (a test with every limit full).
- `sha256` over `items` and `cards`, so G4 and the skill can tell a changed snapshot from the one they loaded.

### 7.2 When it is written and replaced

- Written by every successful build or refresh of a sheet with the flag on, also when the block is empty (then
  `items` is empty and `readiness` says why), so a loader can tell "no evidence" from "no snapshot".
- Replaced whole by the next build of the same stem. A failed build leaves the previous snapshot.
- **Re-check at write time.** A build can select before a hide or revoke and write after the state-only pass ran,
  and that pass found no snapshot to clean. So `write_snapshot`, right before the rename, reads the current state of
  every source the items cite (read-only, by `uri`) and the unapplied lines of the state-request log, and moves to
  `removed` every item with a quote on a source that is no longer `active` or that a pending hide or revoke names.
  The same check runs on the sheet's child rows before the sheet files are written (section 7.4 and owner question
  1 then apply). The window left is the few milliseconds between that check and the rename; the state-only pass
  takes the snapshot folder's lock (`snapshots/<uid>/.lock`, flock) for its rewrite and `write_snapshot` holds the
  same lock across check and rename, so the two never interleave.
- Not rewritten by an index pass: a pass after the build does not change what the preparation used. The parent row in
  Sources then says "Project memory changed after this preparation" (section 9.1) and the PM can refresh.
- Read-only for G4 and the skill. G3 writes; G4 loads at call start (its decision, G2 section 11.4).
- Removed with "Delete this project's memory" (already in `cmd_delete`, `evidence/__main__.py:329-332`) and by the
  weekly vacuum 30 days after its call.

### 7.3 Loader

`related.load_snapshot(uid, stem) -> dict | None`: reads the file, checks `schema` and `sha256`, reads the
state-request log (G2 section 13.1) and drops every item with a quote on a source that a pending hide or revoke
names (strict, as `search` does), and every item in `removed`. No store open, no network, no model: it is the call
G4 makes on the normal tip path.

### 7.4 A source that changes after the snapshot

| Change | Snapshot | Sheet and Sources |
|---|---|---|
| Hidden call, revoked document, PM exclusion (denied, G2 section 11.2) | the state-only pass rewrites the snapshot of every stem in `snapshots/<uid>/` that has a quote on the source: those items move to `removed` (key, reason, time) and their text and quotes are deleted from the file; a snapshot left with no item is deleted | the parent row says "1 item was removed: its source is no longer available"; what happens to model facts that cite the item is owner question 1 (section 15) |
| Source deleted (ignored, not denied) | items whose every quote is on the deleted source are removed as above; an item with another passing quote keeps it and drops the deleted one | as above, "its file was deleted" |
| Source edited (new revision) | unchanged: the quote is what the PM prepared with; the item's `revision_id` differs from the active one | the parent row says "2 items changed in their files since this preparation"; G4 decides whether to show a changed item |
| Pending hide or revoke not yet applied (lock held by a pass) | the loader drops the items (7.3) | as denied |
| Another client becomes "other" after the build (late client) | unchanged in G3: the scope is the build's; the next build applies the new scope | none |

The rewrite runs inside the state-only pass, which already runs at once and also during a call (G2 section 5.1). It
is a local file write only. A test hides a call after a build and asserts the snapshot holds none of its quotes.

## 8. Empty, partial, stale and unavailable store

G2's `evidence status` has no "N of M" field (`__main__.py:81-102`). S1 adds `coverage`: `done` = current revisions
of active text sources with `extract_state` in (`done`, `not_needed`); `total` = current revisions of active
sources. Readiness classes, decided at the start of `select`:

| Class | Condition | Block | Parent row in Sources (never in the prompt) |
|---|---|---|---|
| off | flag off | none, no row | none |
| unavailable | no store file, `quick_check` fails, schema newer than the code, store open fails, or `select` raises | none; build continues | "Project memory was not used: <reason>. The usual sources were used." |
| empty | store exists, no completed pass (`last_pass` null) or no active source | none | "Project memory is not indexed yet. The usual sources were used." |
| links only | extraction never ran or is switched off ("Use AI to read sources" off): `last_extract` null or `done` covers only deterministic sources | register, Tracker, decision and sidecar items only, same budget | "Project memory has links from the task register only." |
| partial | `done < total` | items from what is indexed | "Project memory covers 412 of 678 parts; older files may be missing." |
| stale | `last_pass` older than 3 days, or a source in `taken` has an mtime after `last_pass` | items as found | "Project memory was last updated Nov 20, 14:10. Newer changes are in the usual sources only." |
| ready | none of the above | items | "Project memory: 9 related items." |

`partial` and `stale` can both hold; both lines show. A scope the store cannot answer even after the prep-mode
relabel (`scope_stale`, G2 section 5.3) adds "Kept to Acme's own folders: the folder list changed after the last
index." The PB-70 note mechanism (`build.py:1205-1212`) already writes such lines to `notes`; G3 adds its lines
there and to the parent row's `reason`.

In every class the rest of the build is today's: the usual sources are taken first, and the block only adds. The
fallback test (section 4.4) proves "unchanged" byte for byte.

Locking: `select` opens the store read-only through WAL (G2 section 13.1: readers never wait on the writer), so a
pass in progress never blocks or fails preparation. The prep-mode relabel needs the project lock for 2 seconds at
most (`search.py:332-334`); without it the search answers like the live reader (coverage loss, never a leak).

## 9. Sources and PM control

### 9.1 After the build (`SheetSourceReportRow`, `MeetingSheetView.swift:636-667`)

- Parent row: "Related evidence (project memory)", the readiness line, the count ("9 items, about 3,700 tokens"),
  and, when they apply, "changed after this preparation", "1 item removed".
- Child rows (collapsed by default under the parent, like folder children): the reason line as the row's text, the
  source title and date, the `auto` pill, the facts citing it (from `report()`). Opening a row shows the quote, read
  from the snapshot (Application Support), not from the sheet.

### 9.2 Before the build (the wizard)

- The wizard (`MeetingSheetPrep.swift`) gets one row "Related evidence (project memory)" with the readiness line and
  the item count from a new CLI call `evidence related --preview` (the same `select`, no write), run when the wizard
  opens and after the PM changes the sources. It shows the items with their reason lines; each has a tick, ticked by
  default.
- An unticked item goes into `evidence.exclude_items` of the request; an unticked block sets `evidence.off: true`
  for this build. Every source the PM unticks in the wizard goes into `evidence.exclude_paths`, so the block never
  brings back a file the PM took out. (Today an unticked source is simply not sent, `MeetingSheetDraft.swift:332`,
  and the build can re-add an auto candidate by path, `build.py:1078-1101`; G3 does not change that existing
  behaviour, it only makes sure evidence respects the PM's choice.)
- Series memory (`MeetingSheetDraft.swift:530`) remembers unticked item keys and the block switch per series, as it
  remembers ticked sources.

PM edits win in two more ways, both from G2 and unchanged: a PM edit to a task is a register event and reaches the
block through `fold()`; a PM rejection of a claim, edge or alias (`pm_override`) removes it in every request. An
unticked item is not a rejection: it only leaves this sheet (and the series memory); the fact stays in the store.

## 10. Privacy

- The selection, the ranking, the reason lines and the snapshot run on the Mac with no network request except the
  loopback embed server (`127.0.0.1:11435`) for `query_vec`, which G2 already uses. No model call.
- The block goes to the preparation route only, the one the PM chose for `prep`, through the existing build. Its
  text comes only from the project's own sources already in the store (files of the project folder, its calls and
  notes, the register, the Tracker snapshot, stored site pages). It opens no destination. On the local route
  nothing leaves the Mac.
- What changes for the prep provider: it can now receive quotes from files and calls that today's 7-day window and
  3-call series would not have sent in this preparation (that is the point of G3). Those files are project files
  that preparation sends on other days, and the indexing route has already read them. Hidden calls, revoked
  documents, PM-excluded files and other clients' rows never pass (G2 section 11.2), and the PM's unticked files and
  items are excluded (section 9.2). The docs EN and UK say this in one sentence (S3).
- Quotes are never written into the project folder by G3: not in `.sheet.json`, not in `.sheet.request.json`, not
  in `.sheet.sources.json` (section 4.2). The snapshot with quotes lives in Application Support, mode 0600.
  Model-written facts based on an item land in the sheet like facts from any source today (owner question 1 covers
  a later revoke).
- Telemetry (`usage.py`): one `evidence_prep` event with counts only (items per section, readiness class, chars,
  ms, vectors on or off). Never text, names, paths or reasons.
- Tests and G0 use invented data only.

## 11. Same behaviour on every AI route

What must be equal: the set and order of items, the rendered text of each child source, the snapshot (except
`built_at`), and the readiness lines. What may differ, as today: the wrapper around sources (packed `=== SOURCE`
header or the Claude route's "Sources given here"), the number of passes, the model's answer.

How it is ensured:

- `select` takes no route argument and reads no route state. The budget is in characters and items (section 5.4).
- `query_vec` depends on the embed server, not the route. `vectors` is recorded; the parity test runs with vectors
  off (CI) and, where the bge-m3 model folder is present, once with vectors on for all six routes.
- The block is built once per build. A chain switch mid-build (`Asker.switch`, `build.py:133-162`) re-packs the same
  `srcs`, so the block is the same after the switch.

How it is tested (S1):

- **Route parity test**: one fixture project, one meeting, six builds (`route` = api, code, codex, openai, gemini,
  local) with recording responders (`fake` provider for packed routes; a stubbed `run_claude` that captures the
  agent prompt for `code` and `api`). Extract each `s-ev-*` source text from the captured prompt and compare: byte
  equal on all six. Compare the six snapshots without `built_at`: equal.
- **Chain switch test**: a build on `code` whose Claude step fails over to `codex`: the block in the packed prompt
  equals the block of a direct `codex` build.
- **Fallback test** (section 4.4): store missing, corrupt, empty, and flag off: on all six routes the captured
  prompt equals the flag-off prompt byte for byte.
- **Skill**: `evidence related --stem <stem>` prints the snapshot's block as the same rendered text, so the skill's
  sweep (`skill/SKILL.md` step 6) can read the same items. A test compares its output with the build's child texts.

## 12. G0: how G3 is measured

### 12.1 What the probe in this step showed

A B-style store (ingest only, no extraction, no graph) on `fixtures/northpoint` with the v1 targets' scope and
`as_of`, searched with the call title alone (`mode="prep"`, kinds snippet, claim, edge):

| Target | Missed by A | Found at k=6 | k=12 | k=24 |
|---|---|---|---|---|
| Acme weekly status | blocks the SSO integration, change freeze | both | both | both |
| Globex weekly | net 30 | no | yes | yes |
| Acme technical sync | waiting for client confirmation | no | no | no (yes at k=12 with "item 13 open items" added to the query) |

So plain text search over the whole project already reaches 3 of A's 4 misses on the v1 gold, because the fixture's
files share words with the titles. **The v1 prep facts cannot show that the graph helps**; they can only show that G3
does not lose anything. G3 therefore needs prep gold that text search on the same query cannot reach.

### 12.2 Variants and gold

- **A-prep**: today's build (unchanged).
- **B-prep**: the same block shape and budget, filled only by step 4 of section 5.2 (`search` with
  `kinds=("snippet",)`, no seeds, no hops, no topics), the same query and the same `taken` and `exclude`.
- **C-prep**: the full selection of section 5.2.

B and C use the same budget, the same query text, the same vector setting and the same store, so B to C isolates
seeds, hops and topics; A to B isolates "more context from the store".

New gold file `bench/g0/gold_prep.yaml` (`version: 1`), separate from `gold.yaml` and `gold_v2.yaml` so their hashes
stay (gold v2 is frozen, PB-52 section 14.2). Its `sha256` goes into every results JSON (`prep.gold_sha256`).
It runs on the gold v2 project (`fixtures/northpoint` plus `fixtures/v2/files`, built by `build_v2`), with the
extraction fixture and the topic fixture replayed, so C-prep has fixture hits; fixture misses on the small set
fail the run as "fixture stale", as for C.

Feasibility and independence: the frozen fixtures already hold six hop chains (gold v2 rows h1 to h6, hop 5 of 6
for C). S2 first builds the prep rows from those chains (a chain's end fact becomes an `expect` fact of the target
whose series holds the chain's start). Where 8 facts or the Initech target need more, S2 adds an overlay
`fixtures/prep/` (files and their authored extraction answers) with its own manifest and hash; the frozen
`fixtures/v2`, `fixtures/extract` and `fixtures/topics` are not changed. The prep gold is written by the team that
knows the authored fixture, so, like the S3a fixture, it is **not independent** of the extraction it measures; the
results say so.

Rows, all invented, US names, the same clients:

1. **Prep targets**: 4 calls: Acme weekly status (Nov 17), Globex weekly (Nov 18), Acme technical sync (Nov 19),
   Initech kickoff (Nov 20, the late client). Each with `as_of`, `theirs`, an optional user note, and:
   - `expect`: facts that must be in the block, each with `via` (`seed`, `hop1`, `hop2`, `topic`, `text`), at least
     8 hop facts over the 4 targets (at least 2 for Globex) whose quotes share **no content word** with the title, the
     series name, the invite names or the user note (checked by the bench when the gold loads, so B cannot pass
     them by word match);
   - `reason`: for each hop fact, the seed name and one path element the reason line must contain;
   - `in_a`: facts A already carries (must not be repeated in the block: the "drop what preparation already has"
     rule).
2. **Leaks**: every row checks the other clients' `forbidden` facts and the `excluded_facts` of gold v2 (hidden call,
   revoked page, PM-excluded file, deleted-only file); a replay row checks `future` facts (said after `as_of`,
   for example the Nov 19 go-live change in a Nov 17 preparation).
3. **PM choices**: one row unticks a file in `exclude_paths` whose fact is also stated elsewhere (it must still
   appear, quoted from the other source) and one whose fact is stated only there (it must not); one row unticks an
   item (it must not appear).
4. **Revoke after build**: the bench builds the Acme weekly preparation, then runs the state-only pass with the
   hidden call; the snapshot must hold none of the call's quotes, and `removed` must name the items.
5. **Fallback**: the four readiness cases unavailable, empty, links only, partial, each with the prompt checked
   against A-prep (unavailable and empty: byte-equal) and the parent row's reason.
6. **v1 targets**: the 20 v1 prep facts are evaluated for A, B and C on the gold v2 project (labelled as such in the
   results; A's v1 column on the v1 project stays as it is).

### 12.3 Metrics (added to the results JSON under `prep`)

| Metric | Meaning |
|---|---|
| Prep coverage v1 | v1 prep facts in the `## Sources` text, A, B, C |
| Prep gold coverage | `expect` facts in the block, per `via`, B and C |
| Hop prep rate | hop facts (`hop1`, `hop2`) found, B and C |
| Reason validity | hop facts whose reason line names the seed and a path element (must be 100% for C) |
| Quote validity | block quotes verbatim at their locator (must be 100%) |
| Repeated from A | block items whose quotes are all in A's sources (must be 0) |
| Client, future, excluded leaks in the block | in the block and the snapshot (each must be 0) |
| Leaks in the whole prompt | reported, not gated: A's own sources are read from disk without `as_of` and without the gold v2 states (a hidden call's transcript, a PM-excluded file), so the prompt on the gold v2 project carries A's leaks with or without the block; the gate is "no leak added by the block" |
| Lines dropped by `filter_text` | must be 0 |
| Block size | chars, items, tokens per route (deterministic count) |
| Prompt growth | tokens of the pack with the block minus without, per target and route |
| Route parity | six routes byte-equal (yes or no) |
| Fallback equal | prompts equal to A-prep in the unavailable and empty cases (yes or no) |
| Selection ms | p50 and p95, small and scale sets |
| Snapshot after revoke | quotes of the hidden call left in any snapshot (must be 0) |

### 12.4 Gates

Gate of G3 S2 (bench), on the small and scale sets without vectors, and with vectors where the model folder is
present:

- prep coverage v1: C at least 19 of 20 and not below A on any target (a no-loss check: section 12.1 shows B can
  reach most of A's misses by words alone);
- hop prep rate: C at least 6 of 8 hop facts and at least B + 4;
- reason validity 100%, quote validity 100%, repeated from A 0;
- client, future and excluded leaks 0 in the block and the snapshot, and the prompt's leak count with the block
  equal to the count without it; lines dropped by `filter_text` 0; snapshot after revoke 0;
- route parity yes; fallback equal yes;
- block at most 12,000 characters and 20 items on every target;
- selection p95 at most 3,000 ms on the scale set;
- every existing column of A, B and C (v1 and gold v2) unchanged, and the S2, S3 and S4 gates still pass (the bench
  now calls the product hop walk instead of `hop_items`).

**Caveat stated with every C number**: the extraction and topic fixtures are authored, not a real model's output
(PB-52 S3a and S4 notes, `route: authored`). C-prep's numbers measure selection, ranking, budget and access on that
extraction; they do not measure a real model's extraction quality. Section 12.5 is where that is measured.

### 12.5 First real index: measurement plan on the owner's own projects

Real extraction has not run on a real project yet (`run.py --extract` was not run on a real route in PB-103). G3's
usefulness on real calls is therefore "not verified" until this plan runs. It needs the owner's word, because the
first index sends project text to the indexing provider and costs money (owner question 2).

1. **Index**: the owner picks one or two active client projects, turns the flag on, and lets the first index run on
   the chosen route. Recorded from `evidence status` (counts only): sources, parts, `coverage` done of total,
   passes, cost or percent, `segments_invalid`, `segments_truncated`, days to complete. The first `--extract`
   calibration (PB-103) runs on invented data before, as G2 planned; this step does not replace it.
2. **Shadow preparation**: for the next 10 calls of those projects, every build with the flag on writes one line to
   `<support>/evidence/g3-measure/<uid>.jsonl` (Application Support, never synced, never sent): readiness class,
   items per section and per `via`, chars, tokens added per route, selection ms, items dropped as already taken,
   items whose sources are outside A's window (another series, more than 3 calls back, a folder file older than 7
   days), facts in the sheet citing an `s-ev-*` source. No text, no names, no paths.
3. **Owner review**: after each of those calls the owner runs `evidence related --review <stem>` locally, which
   prints the block and asks per item: useful, not useful, wrong (wrong client, wrong fact, outdated). Answers go to
   the same local file as counts per item kind.
4. **Report**: after 10 calls the team reads only the counts (the owner shares the file or a summary) and reports:
   precision (useful / shown), wrong items (must be 0 for wrong client), share of items from outside A's window,
   calls where at least one sheet fact cites an item, prompt growth, selection p95.
5. **Bar for offering the flag beyond the owner** (team proposal, to be confirmed after the data): at least half of
   the items useful, 0 wrong-client items, 0 items from hidden or revoked sources, at least one cited item in 6 of
   10 calls, selection p95 at most 3 s, prompt growth at most 12,000 characters per preparation.

## 13. Failure modes

| Failure | Behaviour |
|---|---|
| Flag off | nothing runs; prompt byte-equal to today |
| No store file, corrupt store, schema newer than the code | readiness `unavailable`, block empty, build continues, parent row says why |
| A pass holds the project lock | read through WAL, no wait; the prep-mode relabel is skipped after 2 s (coverage loss, no leak) |
| `select` raises | caught in the build, logged with counts only, readiness `unavailable`; the build never fails because of G3 |
| Selection over `ms` | ranked part kept, `truncated` in the parent row |
| Embed server down | `vectors: false`, text and graph only; recorded in the snapshot |
| Pack too big with the block | the existing size plan (passes or compress) on all sources; `evidence_item` keeps rank 1 |
| Snapshot write fails (disk full, permissions) | build result unchanged; the parent row says "Saved for the call: no"; G4 finds no snapshot and runs as today |
| Snapshot older schema or bad `sha256` | the loader returns None; G4 runs as today |
| State change during a build | `write_snapshot` re-checks source states and the state-request log under the snapshot folder's lock before the rename (7.2); the state-only pass takes the same lock; a request still pending is applied by the loader (7.3) |
| `filter_text` drops a line of the block | counted, logged, the item is dropped whole; G0 fails on any drop |
| Late client after the build | the next build uses the new scope; the snapshot keeps the build's scope (7.4) |

## 14. Risks

- **Synthetic gold.** The fixture's files share words with call titles (section 12.1), and the extraction is
  authored. Mitigation: hop gold with no shared content word, B-prep under the same budget, the caveat on every C
  number, and the real-index plan (12.5) before the flag is offered.
- **Prompt growth and noise.** Up to 12,000 characters per preparation on every route; on local about a tenth of a
  pass. A noisy block can make the sheet worse. Mitigation: ranking favours seeds and confirmed facts, drop of what
  A already has, the PM's per-item untick, the useful/not-useful review, and the growth metric.
- **Facts from a revoked source in an already built sheet.** The snapshot is cleaned at once (7.4); model facts in
  the sheet are owner question 1.
- **Older facts presented as current.** Only current claims pass `search`; a changed value shows the current one
  with "changed on <date>"; a superseded decision is never an item. Replay is exact only from the second revision a
  pass observed (G2 section 11.2, known limit).
- **Selection latency on a large store.** The scale set measures p95; the cap stops at 3 s and keeps what it has.
- **Divergence between the app and the skill.** Both read the same snapshot and `rules/evidence.yaml` `prep:`; the
  skill's text is tested against the build's.
- **Wizard preview cost.** `--preview` runs the selection while the wizard is open; it is local and capped at 3 s,
  and it is run again only when the sources change.

## 15. Questions for the owner (product choices only)

1. **Sheet facts that rest on an item whose source is later hidden or revoked.** The snapshot is cleaned at once in
   every option. Options: (a) at the same moment, remove from the built sheet files (`.sheet.json`,
   `.sheet.v{n}.json`, `.sheet.sources.json`, `.sheet.md`, brief, cards) every fact whose only sources are removed
   items, and blank the removed items' reason lines and titles there, locally, with no model call, and say so in
   Sources; (b) keep the sheet as built, mark those facts in the Sheet view and offer "Refresh preparation"; (c)
   leave the sheet as built (today's behaviour for a Google document that loses access after a build).
   **Recommendation: (a)**: the same promise as G2 ("a revoked source disappears at once") then holds for what the
   PM sees and for the live call, which reads sheet facts as passages; facts with another source stay.
2. **Start the first real index for measurement.** Turning the flag on is the consent to index (decision 24.11),
   so the question is when and where: (a) now, on one active client project the owner picks, on Claude key with
   Haiku reading sources (G2's estimate for a typical project: $1.34 to $1.85 for the first index, a lower bound),
   and the 10-call review of section 12.5; (b) after G3 S1 to S4 are merged (S4 holds the review tooling), on the
   same terms; (c) not before G4. **Recommendation: (b)**: the index then feeds a real preparation at once, and the
   10-call review starts with the first call.

Team defaults, not owner questions: the 12,000-character and 20-item budget, the ranking weights, the readiness
thresholds (3 days for stale), the block ticked by default when the flag is on, the bar in 12.5 step 5 (to be
confirmed with data).

## 16. Implementation split

Order: S1 -> S2 and S3 (in parallel) -> S4. Every sub-task merges behind the flag; with the flag off each one changes
no prompt byte, no file and no screen.

### S1. Selection, block in the build, snapshot (core)

Scope: `evidence/related.py` (`select`, seeds, the hop walk moved from the bench, `search` and `ask_global` calls,
drop of `taken`, ranking, budget, reason lines, rendering, `load_snapshot`, `write_snapshot`); `coverage` in
`evidence status`; the snapshot rewrite in the state-only pass (`evidence exclude`) for denied and deleted sources;
`evidence related --stem|--preview|--json` CLI; `sheet/build.py` wiring (parent and children, after `auto_sources`,
before `normalize`, never in `req`); `schema.SOURCE_TYPES` `evidence` and `evidence_item`; `size._prio` rank 1;
`rules/evidence.yaml` `prep:`; the prompt sentence in `rules/meeting-sheet.yaml` and its generated skill section.

Acceptance:
- [ ] Flag off: no store opened, no row, prompt byte-equal on all six routes (recording responders).
- [ ] Store unavailable, empty: prompt byte-equal to flag off on all six routes; the parent row's reason names the
      class.
- [ ] Route parity: the `s-ev-*` texts are byte-equal on api, code, codex, openai, gemini and local, and after a
      chain switch from code to codex; snapshots equal without `built_at`.
- [ ] No quote of the block in any file under the project root after a build (`.sheet.json`, `.sheet.request.json`,
      `.sheet.sources.json`, `.sheet.md` scanned).
- [ ] Hiding a call after a build: the state-only pass moves its items to `removed` and deletes their quotes; a
      pending hide in the state-request log makes `load_snapshot` drop them; a snapshot with no item left is deleted.
- [ ] `exclude_paths` hides a fact stated only by that file and keeps a fact also stated elsewhere;
      `exclude_items` removes the item; an item never repeats a source in `taken`.
- [ ] Budget: never more than 12,000 characters or 20 items; the section limits hold; `ms` stops selection.
- [ ] `filter_text` drops 0 lines of the block on every scope `decide()` returns on the G0 fixture.
- [ ] Unit tests for seeds, hop walk (no `same_as`, no suggestion, both ends visible), ranking ties, reason lines.
- [ ] A build that selected before a hide and writes after the state-only pass ran: the snapshot and the sheet's
      child rows hold none of the hidden call's items (the write-time re-check under the snapshot lock, 7.2).
- [ ] The app opens a sheet with `evidence` and `evidence_item` rows without a decode error (a CoreChecks decode
      test; if the Swift source type needs a new case, that change is part of S1).

G0 gate: no change to any existing column of A, B or C and the S2, S3, S4 gates still pass (the bench is not wired
to the block yet).

Files: `evidence/related.py` (new), `evidence/__main__.py`, `evidence/ingest.py` (state-only pass hook),
`sheet/build.py`, `sheet/sources.py` (`TEXT_TYPES`), `sheet/schema.py`, `sheet/size.py`, `rules/evidence.yaml`,
`rules/meeting-sheet.yaml`, `rules/generated/`, `skill/SKILL.md` (generated section), the Swift sheet source type if
the decode test needs it, tests. Estimate: 16 team hours.

### S2. G0 prep gold, B-prep and C-prep, gates (core)

Scope: `bench/g0/gold_prep.yaml` (section 12.2, frozen with its `sha256` when S2 merges), `run.py` variants
B-prep and C-prep on the gold v2 project with both fixtures, the metrics of 12.3, the gate of 12.4, the bench's
`hop_items` replaced by `related`'s walk, the probe of 12.1 kept as a reported line ("B reaches N of A's v1
misses"), `docs/en/bench/g0-baseline.md` and the UK copy.

Acceptance:
- [ ] Every hop fact of the gold is checked at load to share no content word with its target's title, series,
      invite names or note; a violating row fails the load.
- [ ] A, B and C v1 and gold v2 columns byte-equal to `*-2026-11-25-s4r2.json` except the new `prep` block.
- [ ] The results JSON holds `prep.gold_sha256`, fixture hits and misses, and the authored-fixture caveat.
- [ ] The gate of section 12.4 passes on the small and scale sets.

G0 gate: section 12.4. Files: `bench/g0/run.py`, `bench/g0/gold_prep.yaml` (new), `bench/g0/results/`,
`docs/en/bench/g0-baseline.md`, `docs/uk/bench/g0-baseline.md`. Estimate: 12 team hours.

### S3. Sources in the app, PM choices, texts, docs

Scope: Swift: the parent and child rows after the build (`SheetSourceReportRow`), the quote opened from the
snapshot, the wizard row with `evidence related --preview` and per-item ticks, `evidence.exclude_paths` from the
PM's unticked sources, `evidence.exclude_items` and `evidence.off`, series memory of unticked items, the readiness
and change lines; `sheet/build.py` and `SheetRunner.swift` request fields; i18n en and uk together; docs EN and UK
(Meeting Sheet sources, project memory in preparation, the privacy sentence of section 10); CHANGELOG (Unreleased);
owner question 1 as decided.

Acceptance:
- [ ] Flag off: no row, no CLI call (pure-logic test on the row builder; `swift build`, CoreChecks).
- [ ] A source unticked in the wizard reaches `exclude_paths`; an unticked item reaches `exclude_items`; both are
      remembered per series (pure-logic tests).
- [ ] The parent row shows each readiness line of section 8 and the "changed" and "removed" lines from the snapshot
      (pure-logic tests on the formatter); screenshots only through the harness gate (L33).
- [ ] The quote is read from the snapshot file, never from the sheet (test on the reader).

G0 gate: no change to any G0 column; CoreChecks and `swift build` green. Files: `MeetingSheetPrep.swift`,
`MeetingSheetView.swift`, `MeetingSheetDraft.swift`, `SheetRunner.swift`, `sheet/build.py` (request fields only),
`rules/i18n/en.json`, `rules/i18n/uk.json`, `docs/en/`, `docs/uk/`, `CHANGELOG.md`. Estimate: 14 team hours.

### S4. Real-index measurement tooling

Scope: the count-only measure log `<support>/evidence/g3-measure/<uid>.jsonl` written by the build, `evidence
related --review <stem>` (local, prints the block, records useful, not useful, wrong per item as counts), `evidence
measure --report` (the summary of 12.5 step 4), the telemetry event `evidence_prep` (counts only), docs EN and UK
for the owner's review step, CHANGELOG.

Acceptance:
- [ ] The measure log and the telemetry event hold no text, name, path or reason (test greps a log written from the
      G0 fixture for every fixture name and path: 0 hits).
- [ ] `--review` writes only to Application Support and makes no network or model request (blocked-host test).
- [ ] `--report` reproduces the counts of a hand-made log.

G0 gate: no change to any G0 column. Files: `evidence/related.py`, `evidence/__main__.py`, `sheet/build.py` (one
call), `usage.py`, `docs/en/`, `docs/uk/`, `CHANGELOG.md`. Estimate: 6 team hours.

### Shared files: do not run these in parallel

| Pair | Shared files | Rule |
|---|---|---|
| S1 and S3 | `sheet/build.py`, possibly the Swift sheet source type | S3 after S1 |
| S1 and S4 | `evidence/related.py`, `evidence/__main__.py`, `sheet/build.py` | S4 after S1 |
| S3 and S4 | `sheet/build.py`, `docs/en/`, `docs/uk/`, `CHANGELOG.md` | S4 after S3 |
| S2 and S3 | none (`docs/*/bench/` vs other docs pages) | may run in parallel |

Total: 48 team hours for the four sub-tasks, plus review rounds (S1 and S2 are core: full review every round).

## Підсумок українською

1. G3 додає до збірки Meeting Sheet блок повʼязаних доказів зі сховища G2, усе за прапорцем `evidence_store`; без прапорця жоден байт промпту не змінюється.
2. Блок будується один раз у Python до вибору маршруту, тож він однаковий байт у байт на всіх шести маршрутах AI; це перевіряє тест на api, code, codex, openai, gemini і local.
3. Відбір без жодного виклику моделі: зерна (відкриті пункти серії, рішення останніх дзвінків, люди із запрошення), до двох кроків графом, текстовий пошук, підсумки тем з позначкою "не доказ".
4. Бюджет фіксований: до 12 000 символів і 20 елементів (10 фактів, 4 уривки, 6 речень підсумку); це 1.5-3.8% проходу на хмарних маршрутах і близько 7-11% на локальній моделі.
5. Кожен елемент має цитату, локатор, дату і рядок причини, побудований зі шляху в графі, без моделі; у Sources один батьківський рядок і рядок на кожен елемент, PM може зняти елемент або файл.
6. Знімок для живого дзвінка лежить в Application Support (ніколи в папці проєкту), читається без мережі; прихований дзвінок чи відкликаний документ одразу прибирає свої елементи зі знімка.
7. Порожнє, часткове, застаріле чи недоступне сховище: збірка йде як сьогодні (промпт байт у байт, якщо елементів немає), а Sources каже чому.
8. G0: проба показала, що простий текстовий пошук уже знаходить 3 з 4 пропусків A на старому еталоні, тому потрібен новий `gold_prep.yaml` з фактами в кілька кроків без спільних слів із назвою дзвінка; гейт C щонайменше 6 з 8 і B + 4, витоків 0.
9. Числа C спираються на авторську фікстуру екстракції; справжню користь міряємо на 10 дзвінках власних проєктів Bohdan (лише лічильники локально, оцінка корисно/ні/хибно).
10. Розбиття: S1 відбір, блок і знімок (16 год), S2 еталон і гейти G0 (12 год), S3 Sources у застосунку і документація (14 год), S4 інструменти вимірювання (6 год); S2 і S3 можна паралельно, решта по черзі.
