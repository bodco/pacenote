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
   the route branch, byte-equal across routes, tested. The one allowed route difference: on a route where the block
   would change the plan of today's sources it is left out whole and Sources says so (section 4.2).
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
  snapshot writer and loader, a snapshot reconcile called by the store's writers (section 7.2), and a prepared-context
  entry point in `search.py` (section 5.2).
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
   **fixed budget in characters and items** (section 5), the same on every route. It never changes the plan of
   today's sources: on a route where the block would change it (too big, an extra pass, a summarized source), the
   block is left out whole (section 4.2).
2. **`search()` does not walk hops.** `Budget.hops` exists (`search.py:39-40`, default 1) but `search` never reads
   it; the 2-hop walk exists only in the bench (`run.py` `hop_items`, line 1058). G3 S1 moves a hop walk into product
   code (`evidence/related.py`), parameterised so the bench can call it with its own roots and limits, and G3 S2
   switches the bench to it under the gate "S3 and S4 columns unchanged". The walk reads its depth from
   `Budget.hops`, which `select` fills from `rules/evidence.yaml` `prep.hops` (2): one value, one reader.
3. **Deleting the whole snapshot on a state change is coarser than needed.** G3 rewrites the snapshot without the
   items of the source that left `active` (section 7.4); the rest of the call's evidence stays usable. A snapshot
   whose every item is removed is **kept** with `items: []` and its `removed` list, not deleted as G2 said: deleting it
   would lose `removed`, and a loader could not tell "everything was revoked" from "no snapshot".

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

- one child per item, id `s-ev-<item_id>`, where `item_id` is the first 16 hex characters of the SHA-256 of the
  item's `item_key` (section 7.1): a hash of a stable key, the convention folder children already use so that "a
  refresh keeps the facts on the same file" (`sources.py:883`); type `evidence_item` (new in `schema.SOURCE_TYPES`
  and in `sources.TEXT_TYPES`, so `SRC.check` takes its text like a `text` source, `sources.py:231`), `parent:
  "s-evidence"`, `auto: true`, `text` = the rendered item (section 6.2), `title` = "Project memory: <source title>,
  <date>" cut at 80 characters (no rank number and no entity names: the title is what `derive._src_label` copies into
  cards, `derive.py:53-55`), `reason` and `why` = `["related evidence", <reason line>]`, `modified_at` = the item's
  `said_at`, `item_id`. The plain `item_key` (it can hold a claim's natural key or a path) lives only in the snapshot
  in Application Support; everything in the project folder, the request and series memory uses `item_id`.

Before it appends, the build drops every `s-ev-*` and `s-evidence` row that arrives in `req.sources` (a request
re-sent from an older sheet or a stale series memory), so only this build's selection reaches the pack. Series memory
never stores these rows (section 9.2). On a "Rebuilt only" refresh a kept fact can cite an `s-ev-*` id that this
build did not select again; `keep_cited_sources` (`build.py:955-962`) re-adds the old row as it does for any source,
without text. Because the id is a hash of `item_key`, that row is the same item the fact was built from, never another
item that took its rank. The snapshot holds only this build's items; the reconcile (section 7.2) finds kept rows in
the sheet by `item_id`.

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
from the store, without any route input (its `taken` list too, section 5.1), so its bytes do not depend on the route.

The block must not push today's sources out, in two places:

- **Folder files.** `expand_folders` sizes the folder files with `room = per_pass(route) * near_share` minus the tokens
  of every other source already in `srcs` (`sources.py:845-849`), and the `s-ev-*` children are in `srcs` by then. S1
  leaves `evidence_item` sources out of that sum, so the folder files taken on every route are those of the flag-off
  build.
- **The size plan.** Folders fill the pack up to about `near_share` of the pass, so on local a full block can tip a
  pack that fitted one pass into `too_much` (which fails a one-pass build with no `on_too_much`, `build.py:1227-1234`),
  into an extra pass, or over `compress_plan`'s 0.9 line (a source of A gets summarized). S1 therefore runs the size
  report twice, without and with the block. When the block would change A's plan (the state, the number of passes,
  the pass each of A's sources lands in, or the set of summarized sources), the build **leaves the whole block out** on this route and says so in the parent
  row ("Project memory left out: no room on this model"). The snapshot is still written with the full block.

So the block, when present, is byte-equal on every route, and A's sources and plan are always exactly the flag-off
ones. What can differ per route is only whether the block is present; G0 counts that per route ("block left out for
room") and gates it on the cloud routes (section 12.4). A test asserts A's sources (ids, `used`, `cut`, folder files)
and plan are identical with the flag on and off on all six routes.

What does **not** reach the project folder: step 5 drops `text` from every source, so `.sheet.json` keeps for each
child its id, title, reason, `why`, item kind, `said_at`, and `item_id` (the hash of the store key, section 7.1),
not the quote and not the plain key.
The reason line does land there (it names entities, like a folder file's title does today); a later revoke cleans
it with the facts (owner question 1).
`.sheet.request.json` is written from `req`, and the block is never put into `req`, so it holds no quote either.
Model-written facts that use an item do land in the sheet, brief and cards, as facts from any other source do today
(section 10); `CONTRACT` (`prompt.py:25-27`) asks for a verbatim quote of up to 20 words in each fact's `locator`, so
a fact citing an item carries that part of its quote. The test therefore uses a recording responder that **cites an
`s-ev-*` id** in a fact, and asserts that after the build no file under the project root holds any block quote or
part of one longer than 20 words, and that a block quote appears only inside the `locator.quote` of a fact whose
`source_ids` include that item. A responder that cites nothing would pass without testing anything.

### 4.3 Order in the pack and `auto`

- **Pack priority**: `size._prio` gives rank 0 to the PM's sources, 1 to `project_state`, `prev_call`, `tracker`,
  `digest`, `crm`, 2 to the rest. `evidence_item` joins rank 1: it is chosen for this call and small, and a pass
  plan must not push it behind the folder files. Inside rank 1 the items keep their rank order (`n`).
- **Never summarized**: `compress_plan` (`size.py:151-161`) picks the biggest sources of the lowest rank but can still
  reach small ones. A model summary of an item would break quote fidelity and route parity (local compresses more
  often), so S1 leaves `evidence_item` out of `compress_plan`'s candidates. With the room rule of 4.2 the block is
  either packed whole on a route or left out whole; no item is ever cut or summarized.
- **`auto: true`**: `check()` runs the PB-70 `filter_text` on an auto pathless text source (`sources.py:324-329`).
  The block is built from rows that already passed the store's access rule, which is line-exact with `filter_text`
  (G2 section 5.3), so `filter_text` should drop nothing. It stays on as a second lock: a dropped line is counted
  (`evidence_lines_dropped`) and is a G0 failure (a store leak or a mangled item), never silently accepted.

### 4.4 Flag off and fallback: byte-equal prompts

With the flag off, `related.select` is not imported, no `s-evidence` row exists, no snapshot is written and
`related.load_snapshot` returns None (7.3). With the flag on and no items (store unavailable, empty, or nothing
related, section 8), nothing is added to `raw`; only the parent row with `used: "not_used"` and the readiness line is
added after the answer (4.2), and the prompt sentence of section 6.2 is added only when items are packed on the
route. So the prompt bytes on every route are equal to the flag-off build, and so are they on a route where the block
was left out for room.

The test does not compare the new code with itself (a change made in both paths would pass). S1 first captures
**golden prompts from main** at S1's branch point, before any S1 change: two fixture projects and meetings (the main
one, and the near-the-local-limit one of section 11, saved as `<route>.near.txt`), a fixed
`today`, the six routes with recording responders (the bench's `prep_context` pattern, `run.py:245-263`; a stubbed
`run_claude` for `code` and `api`), with the fixture root and cache paths replaced by a placeholder (`agent_prompt`
prints absolute "Read <path>" lines), saved under `tests/golden/prep_prompts/<route>.txt`. The flag-off build and the empty-block
build (store missing, corrupt, empty) must equal those files byte for byte. A golden file is regenerated only by a
change that is meant to alter today's prompt, in its own commit that says so.

## 5. Selection and budget

### 5.1 Inputs

`related.select(store, *, meeting, scope, as_of, taken, exclude, user_note, vectors) -> Block`, called by the build:

- `meeting`: title, series slug, start, `theirs` (names and companies), agenda lines if any.
- `scope`: the build's client scope (`CS.with_scope`), the same dict the sheet keeps; `None` passes everything not
  denied (G2 section 11.2).
- `as_of`: the meeting start in UTC (G2 section 11.3). A refresh after the call started still uses the start, so
  nothing said in the call itself enters its own preparation.
- `taken`: what today's build would read, defined **without any route input** so the block cannot depend on the
  route: the paths of the PM's ticked sources and of `auto_sources` after step 3 (series notes and transcripts, the
  Tracker, `pacenote/decisions.md`, Current State), plus, for each folder source, the files `expand_folders` would
  pick before any token budget: `folder_files()` with the folder's `folder_days`, the client-side filter (PB-70), the
  `FOLDER_FALLBACK` files when the window is empty, and the route-free `FOLDER_MAX_READS` count. No `room` and no
  `folder_tokens` cap is applied. S1 moves that part of `expand_folders` into a helper (`SRC.folder_window`) that
  both call, so the two lists cannot drift.
  The cost is stated openly: on a route whose budget cuts a folder file (mostly local), that file is in `taken` but not
  in the pack, so an item quoted only from it is dropped. That is a coverage loss, never a repeat; G0 and the measure
  log count it per route ("taken but not packed").
  **The Tracker has no path.** Its candidate is a pathless text source (`sources.py:580-583`, `text=TR.text(tr)`,
  `_tracker` kept on the candidate and popped by the build, `build.py:1089`), so a list of paths can never hold it.
  Yet it is the same data as the store's register source: `TR.collect` folds `commitments.jsonl` of the call's live
  folders (`tracker.py:95-130`), and `ingest_register` folds the same file into one snippet per event
  (`ingest.py:792-869`). Without a second key the top-ranked items (a seed's own status, due and owner, +3 in 5.3)
  would repeat lines the Tracker text already carries. So `taken` has two parts: `taken.paths` (above) and
  `taken.tasks`, the register task ids of every item the Tracker text renders: the items left after the scope filter,
  the open-status filter, the twin dedupe and the 25 and 25 caps (`ON_ME_MAX`, `WAITING_MAX`), each with its fold
  `ids` (merged ids too) plus the ids of the twins deduped into it. `TR.collect` does not keep ids today
  (`tracker.py:121-123`); S1 adds an `ids` field to each listed item. `TR.text` and `TR.for_sheet` pick their fields
  by name (`tracker.py:143-166`) and `fill_from_tracker` reads named fields only (`text`, `state`, `owner`,
  `due_date`; `standup.py:93-121`), so the new field changes no prompt, sheet or flag-off byte (the golden prompts of
  4.4 prove it). An item cut by a cap, or on another client's side, is not in `taken.tasks`: the Tracker does not
  show it. With no Tracker source in the build (auto sources off, or no open item) `taken.tasks` is empty. Every
  register event of a listed task counts as taken, also history events the Tracker line does not show (who moved a
  due date, an earlier text): a stated coverage loss, the price of never repeating the Tracker.
  `taken.tasks` is route-free for the same reason as `taken.paths`: `TR.collect` reads only the request and the files.
- `exclude`: this call's own files (its brief, sheet and notes stems), every file the PM unticked in the wizard
  (new request field `evidence.exclude_paths`, section 9.2) and every item the PM unticked (`evidence.exclude_items`,
  by `item_id`). Paths go to the store's `exclude` (the **ignored** class of G2 section 11.2 step 1: a fact also
  stated by another passing source stays); item keys are dropped after selection. The build reads these fields and
  `evidence.off` from the request in S1 (unknown or malformed values are ignored and logged); S3 only makes the app
  send them.
- `user_note`: the PM's note for this sheet, if any.
- `vectors`: whether the loopback embed server answered; recorded in the block and the snapshot.

### 5.2 Steps (no model call)

0. **Open and prepare once.** `select` opens the store **read-only** (`Store.open(..., readonly=True)`), so neither
   the build nor `--preview` ever writes the store. It then calls a new `search.prepared(store, scope, as_of, exclude,
   window)` once: it runs `stale_labels` and `prepare()` (and `prepare_topics` when step 5 runs) one time and returns a
   context that `search`, `entity_card` and `ask_global` accept instead of running their own `_relabel_if_stale` and
   `prepare()` (today each call does both, `search.py:347-348`, `512-513`, `664-665`). There is no relabel inside
   `select`: on a read-only store `_relabel_if_stale` already returns at once (`search.py:325`), the stale scope is
   answered like the live reader (coverage loss, never a leak) and reported as `scope_stale` (section 8). The next
   index pass relabels as it does today (`ingest.py:326`).
1. **Readiness** (section 8). `unavailable` or `empty`: return an empty block with the reason.
2. **Seeds**: the entities this call is about, deterministic, at most 12, in this order:
   - open register tasks visible to the scope at `as_of` whose `add` call is of this series or that name the
     meeting's client (from `IG.register_fold_at(store, as_of, passing=True)`), newest first, at most 6;
   - decisions and risks (`d-`, `r-`) carried by the last 3 calls of the series (`entity_rev` of their notes
     revisions valid at `as_of`), at most 4;
   - people on the invite resolved through visible aliases (`person:<casefold>`), at most 2;
   - entities whose visible alias occurs in the title, the agenda or the user note (the alias match of
     `search.py:372-381`), filling the remaining places.
   Each seed keeps its **seed reason** (section 6.1).
3. **Graph walk**: from each seed, `entity_card(store, id, scope, as_of, exclude)` and up to 2 hops over accepted
   edges (no `same_as`, no suggestions, both ends visible), the bench's `hop_items` logic moved into
   `evidence/related.py`, with the neighbour's claims newest first (the PB-87 rule). Each walked item keeps its
   path (seed, edges, end entity). All calls use the prepared context of step 0. The walk keeps `hop_items`' order
   (`run.py:1058-1088`): roots in seed order, for each root its card's edges in the order `entity_card` returns them
   (skipping `same_as` and entities already seen), then the new neighbours' claims newest first, the next frontier
   capped; the roots cap, the frontier cap and `k` are parameters (the bench passes its own 6, 6 and `k`). It also
   stops on a **count**: at most `prep.nodes` (40) entity cards per selection, so the cut is deterministic and the
   same on every run. `ms` is only a safety stop, checked before
   every card and every hop (not between whole calls), and parity tests and G0 run with it off (`ms: 0`).
4. **Text search**: `search(store, query, scope, as_of, exclude, kinds=("snippet", "claim", "edge"), mode="prep",
   budget=Budget(k=12, chars=12000, ms=<time left of the selection, 5.4>), query_vec=...)`, with `query` = title, agenda, user note and the seed
   names. Items keep the matched words.
5. **Topics**: `ask_global(store, <names of the first 3 seeds>, scope, as_of, exclude, max_topics=2)`, passing
   sentences only (G2 section 8.7 filters per sentence). Skipped when no seed has a topic.
6. **Drop what preparation already has**: a passing quote is **taken** when (a) its source's uri resolves to a path
   in `taken.paths`, or (b) it is a register event (`kind = register`, `locator.event_id`) whose task, resolved through
   the `ids` of `IG.register_fold_at(store, as_of, passing=True)`, is in `taken.tasks` (5.1). An item whose every
   passing quote is taken is dropped (it would repeat text the pack already holds). An item with one quote that is
   not taken stays. A seed whose own register task is in `taken.tasks` stays a seed: the walk still starts from it
   and its name still appears in reason lines; only its own status, due and owner item is dropped. Items of the
   store's `tracker` kind (the Notion Tracker snapshot, `ingest.py:908-965`) are not in A's Tracker text and are not
   taken.

A bench-only switch `parts=("text",)` runs steps 0, 1, 4 (with `kinds=("snippet",)`), 6 and 7 only: that is B-prep
(section 12.2). It lives in `related.py` (S1) so B-prep and C-prep share every other line of code.
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

Ties: `said_at` newest first, then `item_key` (7.1), never a store row id (a `rebuild` renumbers snippet, revision
and topic ids, so a row id could move the items at the cut). At most 2 items per source (the `FolderIndex` rule), counted
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
new `prep:` key (`chars: 12000`, `items: 20`, the per-section limits, `seeds: 12`, `hops: 2`, `nodes: 40`, `ms:
3000`), so the app and the skill read one table. `prep.hops` is the only hop setting: `select` passes it to the walk
as `Budget(hops=...)`, and the walk is the first reader of `Budget.hops`.

The 12,000 characters count the rendered item texts (6.2). Each item also gets the route's wrapper: on packed routes
the `=== SOURCE <id> | evidence_item | <title> | dated <said_at> ===` header (`prompt.py:106-114`), on the Claude
routes the same `source_block()` under "## Sources given here". The fixed part of that header is about 60 characters;
with a 21-character id, a title cut at 80 and the date it is at most about 180 characters per item, 3,600 for 20
items. So the cut is made on **two counts**: item texts at most 12,000 characters, and the whole block measured
through `source_block()` (headers included, so no estimate is needed) at most **15,000 characters**. Adding the
prompt sentence (about 250 characters, 6.2), the block grows the prompt by at most 15,300 characters; 15,000 is the
figure used in the table below.

What 15,000 characters cost per route (characters per token from `rules/ai.yaml`; latin / cyrillic):

| Route | Tokens of a full block with headers | Share of the pass at `near_share` |
|---|---|---|
| api, code (2.5 / 1.6) | 6,000 / 9,375 | 3.0% / 4.7% of 200,000 |
| codex, openai (4.0 / 2.6) | 3,750 / 5,769 | 1.9% / 2.9% of 200,000 |
| gemini (4.0 / 2.8) | 3,750 / 5,357 | 1.9% / 2.7% of 200,000 |
| local (3.8 / 2.4) | 3,947 / 6,250 | 9.0% / 14.2% of 44,028 |

For scale: today's folder budget alone is `FOLDER_TOKENS = 60000` (`sources.py:47`). On local a full block is
about a tenth of the pass; where it does not fit next to A's sources without changing their plan, it is left out on
that route (4.2). Prep cost rises by the block's tokens on every preparation; G0 reports the
increase (section 12.3), the real-index plan measures it (section 12.5).

Size and time: the block is cut by counts (items, characters, `nodes`, the search's `k`), never by the clock, so the
same store and request give the same block on every run and route. `ms: 3000` is a safety stop for the whole
selection, checked before every card, every hop and every call. The search has its own clock cut today
(`search.py:429`, `elapsed > budget["ms"]`); S1 makes `ms <= 0` mean "no limit" there (today `ms: 0` would cut at
once) and `select` passes the search the time left. Over the limit, selection stops, keeps what it ranked so far and
marks the block `truncated` (said in the parent row, never in the prompt). Parity tests and G0 (B-prep and C-prep)
set `ms: 0` for both the selection and the search; G0 reports the measured selection time separately.

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

`[E3]` is a display label for the PM and the model (the item's place in this block); it is never stored as an id and
never cited. Facts cite the child's source id (`s-ev-<hash>`), which the packed header and the Claude route's inline
header already carry. A topic sentence carries "(AI summary, not evidence)" after its text and the claim refs it
cites (`f1, f3`), and never a confirmation suffix.

The prompt sentence: "Sources of type evidence_item are quotes from the project memory, chosen because of their
first line (the reason); cite them by their source id like any source; an AI summary is never a confirmation." The
contract is the hardcoded `CONTRACT` in `sheet/prompt.py:25`, used by `system()` (the packed passes) and by
`agent_prompt` (the Claude routes, `prompt.py:150-167`). S1 adds the sentence in `prompt.py` through one argument
(`system(lang, call_langs, evidence=True)`, and `agent_prompt` passes it on) that is true **only when items are
packed on this route**: the block has items and was not left out for room (4.2). A build whose block was left out on
its route gets today's bytes, like a flag-off or empty build (4.4); the near-the-local-limit fixture of section 11
checks that its local prompt equals the golden one byte for byte. `rules/meeting-sheet.yaml` is not involved (`presets.py`
reads it for blocks only). The skill gets the same sentence as the first line of `evidence related --stem` output
(section 11), not through a generated section.

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
  "seeds": [{"id": "c-1a2b3c4d5e", "name": "item 13 SSO user mapping", "type": "task", "why": "open item of this series",
             "rests_on": ["live/…weekly-status.notes.md"]}],
  "store_created_at": "2026-11-02T08:15:00Z",
  "sources": [{"uri": "live/…notes.md", "call_key": "2026-11-12-1400-acme-technical-sync", "ext_id": null,
               "title": "Acme technical sync", "kind": "notes", "origin": "derived", "source_id": 41,
               "first_seen": "2026-11-12T15:02:00Z", "revision_id": 97}],
  "items": [{"n": "E3", "sheet_source_id": "s-ev-5f0c2a91d3e84b07", "item_id": "5f0c2a91d3e84b07", "item_key": "edge:…",
             "kind": "edge", "section": "linked",
             "text": "…", "quotes": [{"quote": "…", "uri": "live/…notes.md", "source_id": 41, "revision_id": 97,
                                      "locator": {}, "said_at": "…"}],
             "modality": "statement", "status": "accepted", "confirmations": 1, "derived_only": false,
             "path": [{"id": "c-1a2b3c4d5e", "rests_on": ["…"]}, {"edge": "blocks", "rests_on": ["…"]},
                      {"id": "e-…", "rests_on": ["…"]}],
             "reason": "…", "score": 5.0}],
  "cards": [{"id": "c-1a2b3c4d5e", "name": "…", "name_rests_on": ["…"], "status": "open",
             "aliases": [{"alias": "item 13", "rests_on": ["…"]}], "claims": ["claim:…"]}],
  "removed": [{"item_id": "…", "at": "…", "why": "hidden call"}],
  "sha256": "…"
}
```

- `item_key` is the store's natural key of the item, stable across rebuilds of derived tables (G2 section 13.4) and
  across refreshes, so the PM's untick, the child source id (4.2) and the snapshot survive both: `claim:<nkey>` and
  `edge:<src>|<rel>|<dst>|<modality>` (the same keys a PM rejection uses as `pm_override.target_key`, `search.py:184`,
  `190-191`), `snippet:<uri>:<first 16 hex of the SHA-256 of the passage text>` and `topic:<first 16 hex of the
  SHA-256 of the sentence text and its cited claim keys>`. Store row ids (snippet, revision, topic ids) are never
  part of a key: a `rebuild` or a regenerated topic summary renumbers them. `item_id` (16 hex of the SHA-256 of
  `item_key`) is what leaves the snapshot: in the sheet, the request, series memory and `removed`.
- `sources` are matched in two ways:
  - **The reconcile (it opens the store) matches by `source_id` first.** The `source` table is not among the tables a
    `rebuild` deletes and refills (`DERIVED`, `__main__.py:242-243`; the rebuild keeps the source rows,
    `__main__.py:246-249`), `vacuum` deletes revisions, snippets, claims and edges but never a source row
    (`__main__.py:261-295`), and a move keeps the row and rewrites its `uri` and `call_key` in place
    (`ingest.py:350-353`, `call_key` taken from the new file name; a local file has no `ext_id`). So `source_id` is
    the only key that survives a move. As a guard against a reused id (`source.id` is `INTEGER PRIMARY KEY` without
    `AUTOINCREMENT`, `store.py:31`, and a store file can be removed and created again under the same uid outside
    `cmd_delete`), each snapshot source also keeps the row's `first_seen`, and the snapshot keeps the store's
    `created_at` meta (`store.py:305`). A `source_id` counts as the same source only when both match. A snapshot whose
    `store_created_at` differs from the store's is reconciled strictly: every source counts as denied. A snapshot
    source with **no matching row** (no row with that id, or `first_seen` differs) also counts as denied (strict: its
    state is unknown, so the rules of the denied row of 7.4 apply, not the lenient "deleted" ones).
  - **The loader (no store) matches by `call_key` and `ext_id`**, the fields a hide and a revoke name in the
    state-request log. To keep these current, the reconcile, when the matched row's `uri` or `call_key` differs from
    the snapshot's, **rewrites the snapshot source's `uri` and `call_key`** (and every `rests_on` and quote `uri`
    that names it) to the row's current values, so a later hide by the new name matches in the loader too.
  `revision_id` is kept for diagnostics only. Residual window, stated openly: a file renamed and hidden by its new
  name before any index pass has seen the move is matched neither by the store (its `source` row still has the old
  `call_key`, so `apply_state` matches no row, `ingest.py:1012-1013`) nor by the loader. G2 has the same window; it
  closes at the next index pass: `apply_state` keeps the applied lists in meta (`ingest.py:1003`, `1010`), the pass
  reads them back (`deny_lists`, `ingest.py:379`), `born_state` applies the hide to the moved row
  (`ingest.py:302-303`, called at `354-357`), and the end-of-pass reconcile then removes the source's items from the
  snapshot by `source_id`. A snippet item's `item_key` holds its `uri` (above), so after a move the next build gives
  that item a new `item_id`: a PM untick of the old id no longer applies to it (a stated, rare coverage change).
- `rests_on`: every seed, every path element of an item, every card name and every alias lists the source uris it was
  read from (the passing rows of this request), newest first, at most 5. Invariant, checked by `write_snapshot`:
  every uri in any `rests_on` or quote has an entry in `sources`, so the reconcile can match it to a `source_id` and
  the loader to a `call_key` or `ext_id`. A name, alias or path element is dropped when **all** its listed uris are removed (strict:
  a name with more than 5 supports may be dropped while an unlisted support still holds it, a coverage loss, never a
  leak). This is what lets a hide remove a **name**, not only an item (section 7.4).
- `override_keys` (per item and per card alias): the `(target, target_key)` pairs a PM rejection would use, so the
  loader can match an `override` log line without opening the store (`resolve_key`, `search.py:215`, runs only in
  the store; a log line keyed by an alias the loader cannot resolve drops every card holding an alias with that
  normalised text, strict).
- `cards`: the seeds' entity cards (name, status, visible aliases, at most 5 claim keys each), at most 12. These are
  what G4 needs to match a live question to the call's entities without a query vector (G2 section 11.4).
- `removed`: one entry per removed item: the SHA-256 of its `item_key` (a key can hold an alias or a passage hash, so
  the plain key is not kept), the time and the class of the change. No text, no name.
- Size: at most 20 items, 12 cards with at most 8 aliases each, at most 5 uris per `rests_on`, so the `sources` list
  holds at most a few hundred entries; the file stays under 64 KB (a test with every limit full).
- `sha256` over `seeds`, `sources`, `items`, `cards` and `removed`, so G4 and the skill can tell a changed snapshot
  from the one they loaded.

### 7.2 When it is written and replaced

- Written by every successful build or refresh of a sheet with the flag on, also when the block is empty (then
  `items` is empty and `readiness` says why), so a loader can tell "no evidence" from "no snapshot". A snapshot is
  never deleted because its items were removed; it stays with `items: []` and `removed` (section 3).
- Replaced whole by the next build of the same stem. A failed build leaves the previous snapshot.
- **Reconcile, not event hooks.** One idempotent function, `related.reconcile_snapshots(store)`, compares every
  snapshot in `snapshots/<uid>/` with the store's **current** state: the `state` of each snapshot source (found by
  `source_id` with the `first_seen` guard, 7.1; no matching row counts as removed) and the `pm_override` rows on its
  item keys. It applies the rules of section 7.4 (remove items, names and seeds, never add anything) and brings a
  moved source's `uri` and `call_key` up to date (7.1), the only field it ever rewrites in place. Because it reads state rather than reacting to one command, it
  catches every way a source can leave `active`. It is called by the store's writers, each under the project lock
  they already hold:
  - after `apply_state` commits, outside its transaction (`ingest.py:1000-1033`); this covers `evidence exclude`, a
    request applied from the log by `apply_request` (called by `apply_pending`, `ingest.py:319`, `378`, `402`), and
    the state lists an index pass applies itself (`ingest.py:321`, `393`);
  - after `write_override` (a PM rejection, `ingest.py:1063`);
  - at the end of `ingest()`, which covers an index pass that marks vanished files `deleted` (`ingest.py:386`) or
    Tracker items `deleted` (`ingest.py:962-965`), and a move that rewrites a source's `uri` and `call_key` and can
    change its state through `born_state` (`ingest.py:350-357`);
  - before every `compact_log` (`__main__.py:135`, `221`), so a log line is never dropped before the snapshots
    reflect it.
  Lock order: the project lock, then the snapshot folder's lock (`snapshots/<uid>/.lock`, flock). `write_snapshot`
  takes only the snapshot lock, so the two never deadlock. The reconcile is a local file rewrite (temp file and rename,
  mode 0600) and the same function also cleans the sheet's `s-ev-*` child rows when owner question 1 is decided (S5).
- **Re-check at write time.** A build can select before a hide or revoke and write after the reconcile ran, which
  then found no snapshot to clean. So `write_snapshot`, holding the snapshot lock, runs the same reconcile rules on
  its own snapshot right before the rename: current source states (store opened read-only) plus every line of the
  state-request log (strict, as the loader, 7.3). The same check runs on the sheet's child rows before the sheet
  files are written.
- An index pass that **changes** a source (a new revision) does not rewrite the snapshot: the preparation used the old
  text. The reconcile only removes; it never adds or refreshes an item. The parent row in Sources then says "Project
  memory changed after this preparation" (section 9.1) and the PM can refresh.
- Read-only for G4 and the skill. G3 writes; G4 loads at call start (its decision, G2 section 11.4).
- Removed with "Delete this project's memory" (already in `cmd_delete`, `evidence/__main__.py:329-332`) and by
  `vacuum()` 30 days after its call's `as_of`. Today `vacuum()` (`__main__.py:261-295`) never touches snapshots; S1
  adds that step (after the store's `VACUUM`, under the snapshot lock) and its test.
- **Flag off and back on.** With the flag off nothing runs: no state pass, no reconcile, and a hide made then leaves
  no log line, so a snapshot written before the flag went off can be out of date. Therefore: (1) `load_snapshot`
  returns None while the flag is off (it reads `features.evidence_store` from the config, as the build does);
  (2) turning the flag off in Settings removes `snapshots/` of every project (G3's own files, nothing else; the app's
  flag handler, S3), so no snapshot outlives a period in which hides were not recorded, and after the flag comes back
  on the first build writes a fresh one. A flag switched off by editing the config by hand, with the app closed,
  leaves the folders; the build after it comes back on replaces the snapshot of its stem, and the others are covered
  by the first reconcile after flag-on, which the first state-only pass runs (G2 runs that pass when the flag is on).

### 7.3 Loader

`related.load_snapshot(uid, stem) -> dict | None`: returns None with the flag off (7.2); otherwise reads the file, checks `schema` and `sha256`, then reads the
state-request log (G2 section 13.1) without opening the store. It cannot know which log lines were already applied
(that is in the store's `state_request` table), so it treats **every line as pending** (a strict drop): the union of
every call stem in any `hidden_calls` line and every id in any `revoked` line, matched against the snapshot sources'
`call_key` and `ext_id` (kept current across moves by the reconcile, 7.1), and every item or alias whose
`override_keys` match an `override` line with `field: reject`. It then applies the rules of 7.4
to that in-memory copy (items, card names, aliases, seeds). A hide that was later undone still over-drops until the
next pass compacts the log; that is a coverage loss, never a leak. No store open, no network, no model: it is the call
G4 makes on the normal tip path.

### 7.4 A source that changes after the snapshot

| Change | Snapshot | Sheet and Sources |
|---|---|---|
| Hidden call or revoked document (denied, G2 section 11.2), also when the file was moved after the build (matched by `source_id`, 7.1); a snapshot source with no matching store row or a `store_created_at` mismatch (state unknown, treated as denied, 7.1) | the reconcile (7.2) rewrites every snapshot in `snapshots/<uid>/` with a quote, a name or a seed on the source: (1) every item with a quote on it is removed whole (its entry goes to `removed`, its text and quotes are deleted from the file); (2) every alias, card name, seed and path element whose `rests_on` is left with no source is dropped; (3) a card whose name is dropped is removed, and every item whose path or reason holds a dropped element is removed whole (strict: a reason line is never rewritten to hide a name); (4) the source's entry in `sources` is deleted (its title is a name). A snapshot left with no item is kept with `items: []` and `removed` | the parent row says "1 item was removed: its source is no longer available"; what happens to model facts that cite the item is owner question 1 (section 15) |
| Source deleted, PM exclusion of a file (ignored, not denied: an ignore-listed file leaves the store only at the next index pass, marked `deleted` at `ingest.py:386`; a Tracker item gone from the Tracker snapshot is marked at `ingest.py:964`; no log line, nothing happens at the moment of the exclusion), or a moved file deleted | the reconcile at the end of that pass: items whose every quote is on the deleted source are removed as above; an item with another passing quote keeps it and drops the deleted one; names resting only on it are dropped as above | as above, "its file was deleted" |
| PM rejection of a claim, edge or alias (`pm_override`) after the build | the reconcile after `write_override`: a rejected claim or edge item is removed; a rejected alias is dropped from its card, and items whose reason holds it are removed | as above, "1 item was removed: you rejected it" |
| Source moved or renamed, still active (`ingest.py:350-353`) | the reconcile at the end of that pass rewrites the source's `uri` and `call_key` (and the `rests_on` and quote uris naming it) to the new values, so a later hide by the new name matches in the loader; nothing is removed | none |
| Source edited (new revision) | unchanged: the quote is what the PM prepared with; the item's `revision_id` differs from the active one | the parent row says "2 items changed in their files since this preparation"; G4 decides whether to show a changed item |
| Pending hide, revoke or rejection not yet applied (lock held by a pass) | the loader drops the items and names (7.3, every log line counts) | as denied |
| A hidden or revoked source restored | nothing comes back: the reconcile never adds; the next build selects it again | "Refresh preparation to include restored sources" when `removed` is not empty |
| Another client becomes "other" after the build (late client) | unchanged in G3: the scope is the build's; the next build applies the new scope | none |

The reconcile runs right after the state-only pass, which already runs at once and also during a call (G2 section
5.1), and after every other writer listed in 7.2. It is a local file write only. Tests: a call hidden through
`evidence exclude` after a build; the same hide queued in the log and applied by an index pass, then compacted
(`apply_pending` at `ingest.py:319`, `compact_log` at `__main__.py:135`); a file deleted between two passes; a PM
rejection; a transcript moved to a new call stem after the build and then hidden under its new stem (both through
`evidence exclude` after the pass that saw the move, and queued in the log before that pass); a moved file then
deleted; a snapshot source whose store row is gone, and a store created again under the same uid (`first_seen` and
`store_created_at` guards); in each case the snapshot holds none of the source's quotes and none of the names that
rested only on it, and `load_snapshot` returns the same. A moved, still active source: the snapshot's `uri` and
`call_key` follow the move and nothing is removed.

## 8. Empty, partial, stale and unavailable store

G2's `evidence status` has no "N of M" field (`__main__.py:81-102`). S1 adds `coverage`, with `done` and `total`
over the **same** set: the current revisions of active sources. A revision counts as done when its `extract_state`
is `done` or `not_needed`; deterministic sources (the register, the Tracker, sidecars, decisions) are `not_needed`,
so they always count as done. Readiness classes, decided at the start of `select` and checked **in this order; the
first that holds wins**, except that `partial` and `stale` can both show:

| Class | Condition | Block | Parent row in Sources (never in the prompt) |
|---|---|---|---|
| off | flag off | none, no row | none |
| unavailable | no store file, `quick_check` fails, schema newer than the code, store open fails, or `select` raises | none; build continues | "Project memory was not used: <reason>. The usual sources were used." |
| empty | store exists, no completed pass (`last_pass` null) or no active source | none | "Project memory is not indexed yet. The usual sources were used." |
| links only | extraction is switched off ("Use AI to read sources" off) or never ran (`last_extract` null); `coverage` is not used in this class, since without extraction it would stay below `total` forever | register, Tracker, decision and sidecar items, and text-search snippets, same budget | "Project memory has links from the task register only." |
| partial | `done < total` | items from what is indexed | "Project memory covers 412 of 678 parts; older files may be missing." |
| stale | `last_pass` older than 3 days, or a notes or transcript file in `taken.paths` has an mtime after `last_pass` (Current State, decisions and the config change all the time and would keep this line on for good) | items as found | "Project memory was last updated Nov 20, 14:10. Newer changes are in the usual sources only." |
| ready | none of the above | items | "Project memory: 9 related items." |

`partial` and `stale` can both hold; both lines show. `links only` wins over `partial`: with extraction off the PM
sees one line, not two. A scope label the store does not know yet (`scope_stale`, G2 section 5.3; `select` never
relabels, 5.2 step 0) adds "Kept to Acme's own folders: the folder list changed after the last index." The PB-70
note mechanism (`build.py:1205-1212`) already writes such lines to `notes`; G3 adds its lines there and to the parent
row's `reason`.

In every class the rest of the build is today's: the usual sources and their size plan are the flag-off ones (the
folder budget ignores the block, and a block that would change the plan is left out, 4.2), and the block only adds. The fallback test (section 4.4) proves "unchanged" byte for byte against main.

Locking: `select` opens the store read-only (WAL, G2 section 13.1: readers never wait on the writer) and never takes
the project lock, so a pass in progress never blocks or fails preparation and `--preview` never writes. The cost is
that a stale scope is answered like the live reader until the next pass relabels (coverage loss, never a leak).

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
- The preview and the build can differ (the build sees the request's final sources, and the store can change in
  between), so unticks are matched by `item_id` (the hash of `item_key`), never by position: an unticked id that the build
  does not select is ignored, and an item the build selects that the preview did not show is ticked (the parent row
  then shows it; the PM can untick it and refresh).
- Series memory (`MeetingSheetDraft.swift:530`) remembers unticked item ids and the block switch per series, as it
  remembers ticked sources. It never stores `s-evidence` or `s-ev-*` rows as sources (the build drops them anyway,
  4.2).

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
- G3's own code never writes a block quote into the project folder: not into the sources of `.sheet.json`, not into
  `.sheet.request.json`, not into `.sheet.sources.json` (section 4.2). The snapshot with quotes lives in Application
  Support, mode 0600. The one exception is the model's own fact: `CONTRACT` asks for a verbatim quote of up to 20
  words in a fact's `locator`, so a fact citing an item carries that much of its quote, in the sheet like facts from
  any source today (owner question 1 covers a later revoke).
- Telemetry (`usage.py`): one `evidence_prep` event with counts only (items per section, readiness class, chars,
  ms, vectors on or off). Never text, names, paths or reasons.
- Tests and G0 use invented data only.

## 11. Same behaviour on every AI route

What must be equal: the set and order of items, the rendered text of each child source, the snapshot (except
`built_at`), and the readiness lines. What may differ, as today: the wrapper around sources (packed `=== SOURCE`
header or the Claude route's "Sources given here"), the number of passes, the model's answer.

How it is ensured:

- `select` takes no route argument and reads no route state; `taken` is route-free (5.1). The budget is in characters
  and items, and every cut is by count (section 5.4); parity tests run with `ms: 0`, so a slow test machine cannot cut
  one route's block and not another's.
- The block does not change what today's sources the route takes or how they are planned: the folder budget ignores
  it, a block that would change the plan is left out whole (4.2), and `compress_plan` never summarizes it (4.3). So
  the only route difference allowed is "block present or left out for room", which is reported, and the snapshot is
  the same either way.
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
  prompt equals the golden prompt captured from main byte for byte.
- **A unchanged**: with the flag on and a full block, A's sources (ids, `used`, `cut`, folder files taken) and the
  size plan (state, passes, summarized set, and **which pass each of A's sources lands in**: `evidence_item` at pack
  priority 1 could move A's sources between passes while the number of passes stays the same) equal the flag-off
  build's on all six routes, and no `evidence_item` is in `compress_plan`'s output. A plan whose pass membership of
  A's sources changes counts as "would change A's plan" and leaves the block out (4.2). A fixture whose pack is near the local pass checks that the block is left out there
  and present on the cloud routes.
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
- **B-prep**: the same block shape and budget, filled only by step 4 of section 5.2 (`select(...,
  parts=("text",))`: `search` with `kinds=("snippet",)`, no seeds, no hops, no topics), with the query title, agenda
  and user note, and the same `taken` and `exclude`.
- **C-prep**: the full selection of section 5.2. Its text search also adds the seed names to the query.

B and C use the same budget, the same vector setting and the same store. C's query differs from B's only by the seed
names, and the gold makes that difference harmless: a hop fact shares no content word with the seed names either
(row 1 below), so C cannot reach it through its text search by those names. B to C therefore isolates seeds, hops and
topics; A to B isolates "more context from the store".

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
     series name, the invite names, the user note or the names and visible aliases of the target's seeds (checked by
     the bench when the gold loads, with the seeds computed by `related`, so neither B nor C's text search can pass
     them by word match);
   - `reason`: for each hop fact, the seed name and one path element the reason line must contain;
   - `in_a`: facts A already carries (must not be repeated in the block: the "drop what preparation already has"
     rule), authored from A's actual pack, not from `taken`; each target with an open register task in A's Tracker
     text has at least one `in_a` fact that is that task's status, due or owner (the Tracker case of 5.1).
2. **Leaks**: every row checks the other clients' `forbidden` facts and the `excluded_facts` of gold v2 (hidden call,
   revoked page, PM-excluded file, deleted-only file); a replay row checks `future` facts (said after `as_of`,
   for example the Nov 19 go-live change in a Nov 17 preparation).
3. **PM choices**: one row unticks a file in `exclude_paths` whose fact is also stated elsewhere (it must still
   appear, quoted from the other source) and one whose fact is stated only there (it must not); one row unticks an
   item (it must not appear).
4. **Revoke after build**: the bench builds the Acme weekly preparation, then hides a call twice over: once through
   the state-only pass, once queued in the log and applied by an index pass that then compacts the log; in both cases
   the snapshot must hold none of the call's quotes and none of the names that rest only on it, and `removed` must
   count the items.
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
| Repeated from A | measured independently of `taken` (a check against the same list it is meant to test would always pass): (a) gold `in_a` facts found in the block (must be 0, gated); (b) block items every quote of which occurs, whitespace-normalised, in A's own packed `## Sources` text on that route (the block's sources taken out), which catches the Tracker text and any path `taken` misses (reported per route, not gated: a quote can also occur by chance in an unrelated file of A) |
| Taken but not packed | per route, items dropped because their only source is in `taken` but is not in A's pack on that route: cut by the folder budget, failed access, or skipped (reported, not gated; the honest cost of a route-free `taken`) |
| A unchanged | A's sources (ids, `used`, `cut`, folder files) and size plan with the flag on equal the flag-off ones, per route (yes or no) |
| Block left out for room | per route, targets where the block was left out because it would change A's plan (4.2) |
| Client, future, excluded leaks in the block | in the block and the snapshot (each must be 0) |
| Leaks in the whole prompt | reported, not gated: A's own sources are read from disk without `as_of` and without the gold v2 states (a hidden call's transcript, a PM-excluded file), so the prompt on the gold v2 project carries A's leaks with or without the block; the gate is "no leak added by the block" |
| Lines dropped by `filter_text` | must be 0 |
| Block size | chars (item texts, and with headers), items, tokens per route (deterministic count) |
| Prompt growth | characters and tokens of the pack with the block minus without, per target and route |
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
- reason validity 100%, quote validity 100%, repeated from A (gold `in_a` facts in the block) 0;
- client, future and excluded leaks 0 in the block and the snapshot, and the prompt's leak count with the block
  equal to the count without it; lines dropped by `filter_text` 0; snapshot after revoke 0;
- route parity yes; fallback equal yes; A unchanged yes on all six routes; block left out for room 0 on api, code,
  codex, openai and gemini (local reported);
- block at most 12,000 characters of item text, 15,000 characters with headers (counted through `source_block()`)
  and 20 items on every target; prompt growth at most 15,300 characters (the block with headers plus the prompt
  sentence of 6.2);
- selection p95 at most 3,000 ms on the scale set (measured with `ms: 0`, so the gate sees the real time, not the
  safety stop);
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
   days), items taken but not packed on the route used, facts in the sheet citing an `s-ev-*` source. No text, no
   names, no paths.
3. **Owner review**: after each of those calls the owner runs `evidence related --review <stem>` locally, which
   prints the block and asks per item: useful, not useful, wrong (wrong client, wrong fact, outdated). Answers go to
   the same local file as counts per item kind.
4. **Report**: after 10 calls the team reads only the counts (the owner shares the file or a summary) and reports:
   precision (useful / shown), wrong items (must be 0 for wrong client), share of items from outside A's window,
   calls where at least one sheet fact cites an item, prompt growth, selection p95.
5. **Bar for offering the flag beyond the owner** (team proposal, to be confirmed after the data): at least half of
   the items useful, 0 wrong-client items, 0 items from hidden or revoked sources, at least one cited item in 6 of
   10 calls, selection p95 at most 3 s, prompt growth at most 15,300 characters per preparation.

## 13. Failure modes

| Failure | Behaviour |
|---|---|
| Flag off | nothing runs; prompt byte-equal to today |
| No store file, corrupt store, schema newer than the code | readiness `unavailable`, block empty, build continues, parent row says why |
| A pass holds the project lock | `select` reads read-only through WAL and never takes the lock, so it never waits; a stale scope is answered like the live reader and reported as `scope_stale` (coverage loss, no leak) |
| `select` raises | caught in the build, logged with counts only, readiness `unavailable`; the build never fails because of G3 |
| Selection over `nodes`, `k` or the item and character caps | deterministic cut by count, the same on every run |
| Selection over `ms` (safety stop) | checked before every card and hop; ranked part kept, `truncated` in the parent row |
| Embed server down | `vectors: false`, text and graph only; recorded in the snapshot |
| Pack too big with the block | the block would change A's plan (too much, an extra pass, a summarized source): the block is left out whole on this route, said in the parent row; A runs as with the flag off (4.2) |
| Snapshot write fails (disk full, permissions) | build result unchanged; the parent row says "Saved for the call: no"; G4 finds no snapshot and runs as today |
| Snapshot older schema or bad `sha256` | the loader returns None; G4 runs as today |
| State change during a build | `write_snapshot` re-checks source states and every line of the state-request log under the snapshot folder's lock before the rename (7.2); the reconcile takes the same lock (after the project lock); a request still pending is applied by the loader (7.3) |
| A hide applied by an index pass and compacted away | the reconcile runs after `apply_state` and before every `compact_log` (7.2), so the snapshot is clean before the log line goes |
| Reconcile fails (disk full, permissions) | logged with counts; `compact_log` is skipped for that run, so the log line stays and the loader keeps dropping what it names (a request that `_state_request` applied directly, `__main__.py:168-172`, is appended to the log in that case for the same reason); the next writer runs the reconcile again (it is idempotent); S1 tests the retry |
| `filter_text` drops a line of the block | counted, logged, the item is dropped whole; G0 fails on any drop |
| Late client after the build | the next build uses the new scope; the snapshot keeps the build's scope (7.4) |

## 14. Risks

- **Synthetic gold.** The fixture's files share words with call titles (section 12.1), and the extraction is
  authored. Mitigation: hop gold with no shared content word, B-prep under the same budget, the caveat on every C
  number, and the real-index plan (12.5) before the flag is offered.
- **Prompt growth and noise.** Up to 15,300 characters per preparation on every route (12,000 of item text, the
  source headers and the prompt sentence); on local 9% to 14% of a pass, and left out where it would change A's plan
  (4.2), so local users with full folders may see no block. A noisy block can make the sheet worse. Mitigation: ranking favours seeds and confirmed facts, drop of what
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
   PM sees and for the live call, which reads sheet facts as passages; facts with another source stay. Option (a)
   is sub-task S5 (section 16); (b) is a smaller S5 (the mark and the button); (c) needs no sub-task.
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

The split of G3 is S1 to S4. S5 is a conditional follow-up that exists only if owner question 1 is answered (a) or
(b); it is listed here so its files and order are known. Order: S1 -> S2 and S3 (in parallel) -> S4 -> S5. Every
sub-task merges behind the flag; with the flag off each one changes no prompt byte, no file and no screen.

### S1. Selection, block in the build, snapshot (core)

Scope: `evidence/related.py` (`select` with the bench-only `parts` switch, seeds, the hop walk moved from the bench
and parameterised by roots and limits, `search` and `ask_global` calls through the prepared context, drop of
`taken`, ranking, budget with count-based cuts, reason lines, rendering, `load_snapshot`, `write_snapshot`,
`reconcile_snapshots`); `search.prepared(...)` and the matching argument of `search`, `entity_card` and
`ask_global`; `coverage` in `evidence status`; the reconcile calls after `apply_state`, `write_override`, the
`deleted` marking and before `compact_log`; `evidence related --stem|--preview|--json` CLI; `sheet/build.py` wiring
(parent and children, after `auto_sources`, before `normalize`, never in `req`, incoming `s-ev-*` and `s-evidence`
rows dropped, the request fields `evidence.exclude_paths`, `evidence.exclude_items` and `evidence.off` read);
`SRC.folder_window` shared by `taken` and `expand_folders`, and `evidence_item` left out of the folder `room`;
`TR.collect` keeping each listed item's `ids` for `taken.tasks`; the reconcile matching by `source_id` with the
`first_seen` and `store_created_at` guards and following moves; snapshot removal in `vacuum()`; `load_snapshot`
returning None with the flag off;
`schema.SOURCE_TYPES` `evidence` and `evidence_item`; `size._prio` rank 1 and `compress_plan` without
`evidence_item`; `rules/evidence.yaml` `prep:`; the conditional prompt sentence in `sheet/prompt.py`; golden prompts
captured from main first.

Acceptance:
- [ ] Golden prompts captured from main at S1's branch point for all six routes and both fixtures (the main one and
      the near-the-local-limit one), paths normalised, before any other S1 change (its own commit).
- [ ] Flag off: no store opened, no row, prompt byte-equal to the golden files on all six routes.
- [ ] Store unavailable, corrupt, empty: prompt byte-equal to the golden files on all six routes; the parent row's
      reason names the class.
- [ ] Route parity: the `s-ev-*` texts are byte-equal on api, code, codex, openai, gemini and local (with `ms: 0`),
      and after a chain switch from code to codex; snapshots equal without `built_at`.
- [ ] A unchanged: with a full block, A's sources (ids, `used`, `cut`, folder files) and size plan (state, passes,
      summarized set) equal the flag-off build's on all six routes; a block that would change the plan is left out
      whole and said in the parent row; no `evidence_item` is ever compressed.
- [ ] The prompt sentence is present only when items are packed on the route, on both the packed and the Claude
      prompts; on the near-the-local-limit fixture the local prompt (block left out) equals the golden one byte for
      byte.
- [ ] With a recording responder that cites an `s-ev-*` id: no block quote, and no part of one longer than 20 words,
      in any file under the project root after a build (`.sheet.json`, `.sheet.request.json`, `.sheet.sources.json`,
      `.sheet.md` scanned), except inside the `locator.quote` of a fact citing that item.
- [ ] Tracker: a register task listed in A's Tracker text gives no block item quoted only from its register events
      (`taken.tasks`); a task cut by the 25 cap or on another client's side is not taken; a twin deduped by the
      Tracker is taken; the seed still drives the walk; adding `ids` to `TR.collect` changes no golden prompt and no
      `.sheet.json` byte.
- [ ] Ids: a refresh that reorders the items keeps each item's `s-ev-*` id; a kept fact still cites the same item; an
      incoming `s-ev-*` row in `req.sources` is dropped; two builds over a store `rebuild` and over a regenerated
      topic summary give the same item keys; no plain `item_key` in any file under the project root.
- [ ] Reconcile: a call hidden through `evidence exclude`, the same hide applied by an index pass from the log and
      then compacted, a file deleted between passes, and a PM rejection each leave the snapshot with none of the
      source's quotes and none of the names, aliases and seeds that rested only on it; a snapshot with no item left
      is kept with `items: []` and `removed`; `load_snapshot` gives the same result from the log alone; a failed
      reconcile leaves the log uncompacted and is retried.
- [ ] Moves: a transcript moved to a new stem after the build, then hidden under the new stem (after the pass that
      saw the move, and queued before it), and a moved file then deleted, each leave the snapshot without the
      source's quotes and names; a moved active source gets its new `uri` and `call_key` in the snapshot; a snapshot
      source with no store row, a reused id (`first_seen` differs) and a store created again (`store_created_at`
      differs) count as removed.
- [ ] Flag off: no snapshot written, `load_snapshot` returns None; `vacuum()` removes snapshots 30 days after their
      call's `as_of`.
- [ ] Ties at the cut are broken by `item_key`: the same items are cut before and after a store `rebuild`.
- [ ] A build that selected before a hide and writes after the reconcile ran: the snapshot and the sheet's child rows
      hold none of the hidden call's items (the write-time re-check under the snapshot lock, 7.2).
- [ ] `select` and `--preview` never write the store (file hash and mtime unchanged) and never take the project lock;
      a stale scope gives `scope_stale`; `prepare()` runs once per `select` (counted in a test).
- [ ] `exclude_paths` hides a fact stated only by that file and keeps a fact also stated elsewhere;
      `exclude_items` removes the item; both read from the request by the build; an item never repeats a source in
      `taken`.
- [ ] Budget: never more than 12,000 characters of item text, 15,000 with headers through `source_block()`, or 20
      items; prompt growth at most 15,300 characters; the section limits and `nodes` hold; `ms` stops selection,
      checked inside the walk; `ms: 0` means no limit in both `select` and `search`.
- [ ] Snapshot: every `rests_on` uri has a `sources` entry; an `override` log line with `field: reject` makes
      `load_snapshot` drop the matching item or alias; the file stays under 64 KB with every limit full.
- [ ] `filter_text` drops 0 lines of the block on every scope `decide()` returns on the G0 fixture.
- [ ] Unit tests for seeds, hop walk (no `same_as`, no suggestion, both ends visible), ranking ties, reason lines,
      the readiness order (links only wins over partial).
- [ ] The product walk, called with the bench's roots, frontier and `k`, returns exactly `hop_items`' items on the
      G0 fixture (so S2 can switch the bench without moving a column).
- [ ] The app opens a sheet with `evidence` and `evidence_item` rows without a decode error (a CoreChecks decode
      test; if the Swift source type needs a new case, that change is part of S1).

G0 gate: no change to any existing column of A, B or C and the S2, S3, S4 gates still pass (the bench is not wired
to the block yet).

Files: `evidence/related.py` (new), `evidence/search.py` (prepared context), `evidence/__main__.py`,
`evidence/ingest.py` (reconcile calls), `sheet/build.py`, `sheet/sources.py` (`TEXT_TYPES`, `folder_window`,
`expand_folders` room), `sheet/tracker.py` (`ids` on listed items), `sheet/schema.py`, `sheet/size.py`,
`sheet/prompt.py`, `rules/evidence.yaml`, `tests/golden/prep_prompts/` (new), the Swift sheet source type if the
decode test needs it, tests. Estimate: 25 team hours (was 22: `taken.tasks`, the `source_id` match with moves,
snapshot vacuum and the flag-on marks). S1 stays one sub-task: the golden prompts are already its first commit, and
a split into S1a and S1b would take the G3 split past four sub-tasks plus the conditional S5.

### S2. G0 prep gold, B-prep and C-prep, gates (core)

Scope: `bench/g0/gold_prep.yaml` (section 12.2, frozen with its `sha256` when S2 merges), `run.py` variants
B-prep (`select(..., parts=("text",))`, from S1) and C-prep on the gold v2 project with both fixtures, the metrics of
12.3, the gate of 12.4, the bench's `hop_items` replaced by `related`'s walk (called with the bench's roots and
limits), the probe of 12.1 kept as a reported line ("B reaches N of A's v1 misses"), `docs/en/bench/g0-baseline.md`
and the UK copy.

Acceptance:
- [ ] Every hop fact of the gold is checked at load to share no content word with its target's title, series,
      invite names, note, or seed names and aliases; a violating row fails the load.
- [ ] A, B and C v1 and gold v2 columns byte-equal to `*-2026-11-25-s4r2.json` except the new `prep` block.
- [ ] The results JSON holds `prep.gold_sha256`, fixture hits and misses, and the authored-fixture caveat.
- [ ] The gate of section 12.4 passes on the small and scale sets.

G0 gate: section 12.4. Files: `bench/g0/run.py`, `bench/g0/gold_prep.yaml` (new), `bench/g0/results/`,
`docs/en/bench/g0-baseline.md`, `docs/uk/bench/g0-baseline.md`. Estimate: 12 team hours.

### S3. Sources in the app, PM choices, texts, docs

Scope: Swift: the parent and child rows after the build (`SheetSourceReportRow`), the quote opened from the
snapshot, the wizard row with `evidence related --preview` and per-item ticks, `evidence.exclude_paths` from the
PM's unticked sources, `evidence.exclude_items` and `evidence.off`, series memory of unticked items (by `item_id`,
never `s-ev-*` rows), the readiness and change lines; `SheetRunner.swift` sends the request fields (the build already
reads them, S1); i18n en and uk together; docs EN and UK (Meeting Sheet sources, project memory in preparation, the
privacy sentence of section 10); CHANGELOG (Unreleased).

Acceptance:
- [ ] Flag off: no row, no CLI call (pure-logic test on the row builder; `swift build`, CoreChecks). Turning the
      flag off in Settings removes every project's `snapshots/` folder and nothing else (7.2; a test on the handler's
      path list).
- [ ] A source unticked in the wizard reaches `exclude_paths`; an unticked item reaches `exclude_items`; both are
      remembered per series (pure-logic tests).
- [ ] The parent row shows each readiness line of section 8 and the "changed" and "removed" lines from the snapshot
      (pure-logic tests on the formatter); screenshots only through the harness gate (L33).
- [ ] The quote is read from the snapshot file, never from the sheet (test on the reader).
- [ ] An unticked item id the build does not select is ignored; an item the preview did not show arrives ticked
      (pure-logic test on the matcher).

G0 gate: no change to any G0 column; CoreChecks and `swift build` green. Files: `MeetingSheetPrep.swift`,
`MeetingSheetView.swift`, `MeetingSheetDraft.swift`, `SheetRunner.swift`, the `evidence_store` flag handler in
Settings (snapshot folders removed on flag off), `rules/i18n/en.json`,
`rules/i18n/uk.json`, `docs/en/`, `docs/uk/`, `CHANGELOG.md`. Estimate: 13 team hours (was 14: the build-side
request parsing moved to S1).

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

### S5. Sheet facts after a revoke (only after owner question 1)

Scope for answer (a): `reconcile_snapshots` also rewrites the built sheet files of each stem it cleans
(`.sheet.json`, every `.sheet.v{n}.json`, `.sheet.sources.json`, `.sheet.md`, and the `derive` outputs brief,
cards, facts) in the project folder: every fact whose `source_ids` are all removed `s-ev-*` items is removed, and the
removed child rows keep only their id, `item_id` and a blank title and reason. Local, no model call, temp file
and rename, under the project lock it already holds. Sources says "N facts were removed with their source". For
answer (b): the facts are marked in the Sheet view and "Refresh preparation" is offered; no file rewrite.

Acceptance (a):
- [ ] After a hide, no sheet file of an affected stem holds a fact whose only sources are removed items, nor the
      removed items' titles or reason lines; a fact with another passing source stays unchanged.
- [ ] Every `.sheet.v{n}.json` version of the stem is cleaned, not only the latest (the revoke promise covers history).
- [ ] The rewrite makes no network or model request (blocked-host test) and is idempotent.
- [ ] The app reloads a rewritten sheet without a decode error (CoreChecks).

G0 gate: the bench's revoke-after-build check (row 4 of `gold_prep.yaml`, the gold file itself unchanged) also reads
the sheet files: 0 facts resting only on the hidden call. Files: `evidence/related.py`, `bench/g0/run.py` (that
check), `sheet/derive.py` (rewrite of brief and cards), `sheet/build.py` (the sheet writer reused), `MeetingSheetView.swift` (the line), i18n en and uk, docs EN and UK, `CHANGELOG.md`. Estimate:
8 team hours for (a), 4 for (b).

### Shared files: do not run these in parallel

| Pair | Shared files | Rule |
|---|---|---|
| S1 and S3 | possibly the Swift sheet source type | S3 after S1 (S3 also needs S1's request parsing) |
| S1 and S4 | `evidence/related.py`, `evidence/__main__.py`, `sheet/build.py` | S4 after S1 |
| S3 and S4 | `docs/en/`, `docs/uk/`, `CHANGELOG.md` | S4 after S3 |
| S1, S4 and S5 | `evidence/related.py`, `sheet/build.py` | S5 after S4 |
| S3 and S5 | `MeetingSheetView.swift`, i18n, docs, `CHANGELOG.md` | S5 after S3 |
| S2 and S5 | `bench/g0/run.py` | S5 after S2 |
| S2 and S3 | none (`docs/*/bench/` vs other docs pages) | may run in parallel |

Total: 56 team hours for S1 to S4 (25 + 12 + 13 + 6), plus 8 for S5 if question 1 is answered (a), plus review rounds (S1 and S2 are
core: full review every round).

## Підсумок українською

1. G3 додає до збірки Meeting Sheet блок повʼязаних доказів зі сховища G2, усе за прапорцем `evidence_store`; без прапорця жоден байт промпту не змінюється.
2. Блок будується один раз у Python до вибору маршруту, тож, коли він є в промпті, він однаковий байт у байт на всіх шести маршрутах AI; це перевіряє тест на api, code, codex, openai, gemini і local.
3. Відбір без жодного виклику моделі: зерна (відкриті пункти серії, рішення останніх дзвінків, люди із запрошення), до двох кроків графом, текстовий пошук, підсумки тем з позначкою "не доказ".
4. Бюджет фіксований і рахується за кількістю, не за часом: до 12 000 символів тексту і 20 елементів (10 фактів, 4 уривки, 6 речень підсумку), разом із заголовками джерел до 15 000 символів; це 1.9-4.7% проходу на хмарних маршрутах і 9-14% на локальній моделі; джерела і план A блок не змінює, а якщо не вміщується, на цьому маршруті його пропущено цілком.
5. Кожен елемент має цитату, локатор, дату і рядок причини, побудований зі шляху в графі, без моделі; у Sources один батьківський рядок і рядок на кожен елемент, PM може зняти елемент або файл; те, що вже є в A (файли і пункти Tracker за id задач), у блок не повторюється.
6. Знімок для живого дзвінка лежить в Application Support (ніколи в папці проєкту), читається без мережі; ідентифікатори елементів стабільні (хеш ключа); прихований дзвінок, відкликаний документ, видалений файл чи відхилення PM прибирають зі знімка і елементи, і імена, що трималися лише на цьому джерелі; джерело знаходимо за `source_id`, тож це працює і після перейменування файлу.
7. Порожнє, часткове, застаріле чи недоступне сховище: збірка йде як сьогодні (промпт байт у байт, якщо елементів немає), а Sources каже чому.
8. G0: проба показала, що простий текстовий пошук уже знаходить 3 з 4 пропусків A на старому еталоні, тому потрібен новий `gold_prep.yaml` з фактами в кілька кроків без спільних слів із назвою дзвінка; гейт C щонайменше 6 з 8 і B + 4, витоків 0.
9. Числа C спираються на авторську фікстуру екстракції; справжню користь міряємо на 10 дзвінках власних проєктів Bohdan (лише лічильники локально, оцінка корисно/ні/хибно).
10. Розбиття: S1 відбір, блок і знімок (25 год), S2 еталон і гейти G0 (12 год), S3 Sources у застосунку і документація (13 год), S4 інструменти вимірювання (6 год), S5 факти аркуша після відкликання лише після відповіді на питання 1 (8 год); S2 і S3 можна паралельно, решта по черзі.
