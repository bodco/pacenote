# GraphRAG G0: call context benchmark, baseline

PB-50. A synthetic benchmark of the context Pacenote assembles for a call, so every later GraphRAG step (G1 identity,
G2 evidence store, G3 preparation, G4 live, G5 after the call) shows with numbers that the context got better, not
just different. Ukrainian version: [docs/uk/bench/g0-baseline.md](../../uk/bench/g0-baseline.md).

## Baseline (variant A, the current code, 2026-11-23)

| Target | Prep coverage | Prep leaks | Live answer rate | Answer text in top | Live leaks | Lookup p50 ms | Lookup p95 ms | Duplicates | Wrong identity | Item 13 cross-client | False agreed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| acme-weekly-status-2026-11-17 | 75.0% | 4 | 100.0% | 50.0% | 2 | 0.31 | 0.41 | 3 | 2 | 1 | 1 |
| globex-weekly-2026-11-18 | 80.0% | 3 | 100.0% | 100.0% | 1 | 0.27 | 0.35 | 2 | 2 | 2 | 0 |
| acme-technical-sync-2026-11-19 | 85.7% | 4 | 100.0% | 100.0% | 1 | 0.23 | 0.87 | 1 | 1 | 1 | 1 |
| **All (A)** | **80.0%** | **11** | **100.0%** | **80.0%** | **4** | **0.26** | **1.14** | **6** | **5** | **4** | **2** |

Raw numbers: `spikes/m0/bench/g0/results/A-2026-11-23.json`. Latency is the only column that changes between runs;
every other number is the same on every run of the same code.

What the baseline says:

- **Preparation misses what sits outside the series.** 4 of 20 gold facts never reach the context: item 13 "blocks the
  SSO integration" (technical series) is missing when preparing the weekly call, and item 13 "waiting for client
  confirmation" (weekly series) is missing when preparing the technical call; the change freeze from the 4th call back
  of the series (only the last 3 calls are read); the Globex payment terms in a folder file older than 7 days.
- **Scope leaks everywhere.** 11 of 12 facts of the other client reach the context: the Tracker reads the whole project
  register, the decisions and the Current State page hold both clients, and a fresh Globex file falls into the 7-day
  folder window of an Acme call.
- **Live lookup finds the file but mixes the clients.** Every expected source is in the top 3, but "Where are we with
  item 13?" on the Acme call ranks the Globex item 13 first, and 4 of the top results belong to the other client.
  The Ukrainian question about the English notes has the right call only in third place, and the passage it returns
  does not hold the answer.
- **After the call the register does not know an item it already holds.** The same task in other words in another call
  becomes a new item (6 duplicates; the rollout checklist is 3 open items after the weekly call), "we will try" stands
  as an open commitment (2), and the Tracker of each call shows the other client's item 13 (4).

## After PB-70 (client scope, 2026-11-23)

The call's client is decided before ranking; the other client's files, Tracker items and lines of shared files reach
neither the preparation nor the search during the call. Prep leaks 11 -> 0, live leaks 4 -> 0; prep coverage 80% and
live answer rate 100% unchanged (the same 4 misses). Lookup p50/p95 0.26/1.14 -> 1.69/2.55 ms (the scope check per question; latency varies between runs). Item 13 cross-client
stays 4: it counts the register after the call and a Tracker collected without the call (no client scope).
Raw numbers: `spikes/m0/bench/g0/results/A-2026-11-23-pb70.json`. The harness now allows the fixture's loopback page
(`PACENOTE_TEST_WEB_LOOPBACK`): since PB-37 the Acme status page on 127.0.0.1 was refused and its fact dropped out.

## After G1 identity (PB-51, variant A, 2026-11-23)

The same variant A after the G1 change (`commitments` identity across calls; measured at commit `080c330`, the totals are unchanged through fix round 7). G1 was built before PB-70, so the 11 prep and 4 live leaks below are the
pre-PB-70 numbers; the merged code is in the next section. Raw numbers:
`spikes/m0/bench/g0/results/A-2026-11-23-g1.json` (same fixture and gold hashes as the baseline).

| Target | Duplicates | Suggested | Duplicates if PM confirms | Wrong identity | Item 13 cross-client | False agreed |
|---|---:|---:|---:|---:|---:|---:|
| acme-weekly-status-2026-11-17 | 3 | 3 | 0 | 2 | 1 | 0 |
| globex-weekly-2026-11-18 | 2 | 3 | 0 | 2 | 2 | 0 |
| acme-technical-sync-2026-11-19 | 0 | 2 | 0 | 0 | 1 | 0 |
| **All, before (baseline)** | **6** | n/a | n/a | **5** | **4** | **2** |
| **All, after G1** | **5** | **8** | **0** | **4** | **4** | **0** |

Preparation and live columns did not change (80.0% coverage, 11 prep leaks, 100.0% answer rate, 80.0% answer text,
4 live leaks); only latency moves between runs.

- **Item 13 is a suggestion, per client.** The engine knew no client of a call before PB-70 (the fixture has none either), and
  two clients can both have a "Weekly sync", so an explicit reference joins items by itself only when both name the
  same client. Here the mentions of item 13 wait for the PM as suggestions inside one meeting series; Acme's and
  Globex's item 13 are never suggested to each other. A first run of this change joined them by the series alone
  (duplicates 3, wrong identity 2); the review found that two clients with the same meeting title would merge.
- **"We will try" is a proposal.** The migration marks "try to confirm the pilot user list" as a proposal, and a
  later "try to send" never weakens an agreed item: false agreed 2 to 0.
- **The rest waits for the PM too.** The rollout checklist (3 calls, 3 wordings) and the invoice export mockup have
  no ID and no reference, so they stay separate items with a suggestion each; once the PM confirms the suggestions,
  duplicates are 0.
  Suggested counts every open suggestion in the register after the call, so a pair from older calls is counted on
  each target.
- **Item 13 cross-client is unchanged (4).** It counts the other client's item 13 in the call's Tracker, which still
  reads the whole project register; scoping the Tracker belongs to the preparation steps (G3, G5).

## After PB-70 and G1 together (variant A, 2026-11-23)

Both changes on one branch (main `db3c2be` with PB-51 merged). The register now takes a call's client from the client
scope its preparation decided (PB-70), so two calls both called "Weekly sync" but scoped to two clients never join or
suggest each other's item 13. Raw numbers: `spikes/m0/bench/g0/results/A-2026-11-23-g1-pb70.json`.

| Run | Prep coverage | Prep leaks | Live answer rate | Answer text in top | Live leaks | Duplicates | Suggested | Duplicates if PM confirms | Wrong identity | Item 13 cross-client | False agreed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (A) | 80.0% | 11 | 100.0% | 80.0% | 4 | 6 | n/a | n/a | 5 | 4 | 2 |
| After PB-70 | 80.0% | 0 | 100.0% | 80.0% | 0 | 6 | n/a | n/a | 5 | 4 | 2 |
| After G1 (before PB-70) | 80.0% | 11 | 100.0% | 80.0% | 4 | 5 | 8 | 0 | 4 | 4 | 0 |
| **After PB-70 and G1** | **80.0%** | **0** | **100.0%** | **80.0%** | **0** | **5** | **8** | **0** | **4** | **4** | **0** |

The two changes add up without moving each other's numbers: the fixture's older register items have no client, so
the after-call columns are G1's, and the preparation and live columns are PB-70's. Item 13 cross-client stays 4 for
the reason given above.

## G2 S1: the evidence store behind the flag (2026-11-24)

The store of PB-52 (sub-task S1, `livetips_m0/evidence/`, flag `evidence_store`, off by default) is not on the
variant A path, so A's columns are unchanged (`results/A-2026-11-24-3.json` equals `A-2026-11-23-g1-pb70.json` apart
from the date, commit, label and run time; lookup p50 1.66 ms, p95 2.64 ms on this run). Variants B and C come with S2.
Measured on the same 10 KB fixture by the S1 tests and one ingest:

| What | Value |
|---|---:|
| Deterministic ingest of the fixture (22 sources) | 0.06 s |
| Snippets: `lookup.chunks` (A) / `lookup.chunks_at` (store) | 30 / 84 |
| Non-blank lines in runs that name a folder of the label universe | 13.7% |
| `search(mode="live")` p95, Acme scope / Globex scope (10 questions, 5 times) | 1.7 ms / 1.9 ms |
| Leaks in the store tests (other client, excluded, hidden call, revoked, deleted-only, future) | 0 |

`chunks_at` cuts more snippets than `chunks` because a line that names a client or topic folder gets its own snippet
(the chunk invariant of the spec, section 5.2); this is the price of line-exact client access.

## G2 S2: gold v2, the scale set, variants B and C (2026-11-24)

PB-85 adds gold version 2 (`bench/g0/gold_v2.yaml`, 39 rows, spec section 14.2), the seeded scale set (2.19 MB: 40
documents and 10 calls of distracting text, the same bytes from the same seed) and two variants over the evidence
store: **B** (snippets and FTS5 over every call and file, scope and time on, no entities) and **C** (B plus the
store's entities; its LLM extraction comes from a fixture recorded in S3). Raw numbers:
`results/{A,B,C}-2026-11-24-s2.json`.

**The existing columns.** A's are unchanged: every column but latency equals `A-2026-11-24-3.json` (and the fixture
and gold hashes too). B and C replace only the live lookup (preparation and after-call stay A's until G3), so their
preparation and after-call columns equal A's.

| Variant | Live answer rate | Answer text in top | Live leaks | Lookup p50/p95 ms |
|---|---:|---:|---:|---:|
| A (folder index) | 100.0% | 80.0% | 0 | 1.88 / 6.01 |
| B (store, snippets) | 90.0% | 80.0% | 0 | 3.38 / 19.23 |
| C (store, snippets, claims, edges) | 100.0% | 80.0% | 0 | 3.38 / 19.15 |

Latency note: the three result files were saved with the three runs in parallel, so their latencies are high. Run one
after another on the same Mac (not saved): v1 lookup p50/p95 A 1.70/2.82, B 2.91/3.98, C 2.94/4.44 ms; gold v2 live
p95 on the scale set A 41.38, B 25.17, C 26.28 ms. Every other number is the same in both runs.

B misses the Ukrainian question of the weekly call ("Чого ми чекаємо від служби безпеки Acme?"): it does not rank the
expected notes in its top 3, while A does (A also reads the call's Meeting Sheet, which B leaves out as the call's own
file); the cause is not traced further. C has the expected notes in its top 3, most likely
through the decision and risk claims those notes carry (the items C adds to B); the run does not trace it further.
A and B also differ in the chunker (B and C cut with `chunks_at`, spec section 5.2), so A versus B includes that
change; B versus C isolates the entities.

**Gold v2, without vectors** (the run with vectors is `skipped` here: the bge-m3 model folder is not on this Mac):

| Variant, set | Rows passed | Not evaluated | Future leaks | Stale | Excluded leaks | Client leaks | Evidence recall | Quote validity | Shared kept | Live p50/p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A small | 10/39 | 0 | 6 | 1 | 13 | 0 | 75.0% | n/a | 100.0% | 1.88 / 12.33 |
| A scale | 10/39 | 0 | 6 | 1 | 13 | 0 | 75.0% | n/a | 100.0% | 6.07 / 80.64 |
| B small | 22/39 | 0 | 0 | 0 | 0 | 0 | 100.0% | 100.0% | 100.0% | 3.28 / 6.46 |
| B scale | 22/39 | 0 | 0 | 0 | 0 | 0 | 100.0% | 100.0% | 100.0% | 21.32 / 66.29 |
| C small | 23/23 | 16 | 0 | 0 | 0 | 0 | 100.0% | 100.0% | 100.0% | 3.28 / 5.88 |
| C scale | 23/23 | 16 | 0 | 0 | 0 | 0 | 100.0% | 100.0% | 100.0% | 21.48 / 64.00 |

Hop, paraphrase and Ukrainian answer rates: 0% for A and B (6, 3 and 1 rows), not evaluated for C until S3. Timeline
recall: 75% for A, 25% for B (one question, four dated facts), not evaluated for C until S4. Index cost (all
variants, the same store): 56 segments on the small set and 626 on the scale set; editing one section of a three
section file re-extracts 1 segment, reverting a file re-extracts 0, moving a call builds no revision. C's extraction
fixture is not recorded yet (0 hits, 64 misses on the small set: every segment, reported, not a failure); the S3
recording fills it.

What it says:

- **A cannot replay time or honour exclusions.** It reads the folder as it is now: 6 future leaks (the go-live date,
  the hourly decision, the edited notes and the same-day edit, each as of a past call), 1 stale value (the budget on
  Nov 20) and 13 excluded leaks (the PM-excluded file, the revoked page and the hidden call, by text and by source).
- **B and C hold every S2 zero** on both sets: client, future and excluded leaks 0, quote validity 100%, register and
  Tracker facts 100%, the deleted and moved facts kept. This is the S2 gate (spec section 17), and it passes for both;
  live p95 on the scale set is 25 to 27 ms run alone (64 to 66 ms in the parallel saved run) against the 150 ms bar.
- **C adds the state columns.** The task closed on Nov 19 is open as of Nov 17 and done now; the decision superseded
  on Nov 19 is current as of Nov 17 and superseded now. B returns the same text with no status, so it fails those 4
  rows by design.
- **Multi-hop and paraphrase are still 0** for A and B, as each row expects: no quote shares a word with its question
  except the start entity. These rows measure S3.

Each row states which variant should pass it and why (`expected`, `why`); A, B and C match every expectation on both
sets.

## What is measured

Each target call is replayed on a clean copy of the fixture project, with every file dated by the date in its name
(the rest by `gold.yaml > file_dates`), so the folder window and the index order are the same on every run.

| Metric | How |
|---|---|
| Prep coverage | `sheet.build` on route `codex` (its real per-pass budget) with a responder that only records the packed prompt; a gold fact is present when one of its phrases is in the prompt's `## Sources` |
| Prep leaks | facts of the other client (`gold.yaml > forbidden`) present in the same `## Sources` |
| Live answer rate | `lookup.snippets` over the background `FolderIndex`, as `engine._project_snippets` runs it without `lt` and without the model's extra queries (k 6, 14000 characters, 1 s); the expected file must be in the top 3 |
| Answer text in top | a top 3 passage holds the expected answer phrase |
| Live leaks | top 3 results from the other client's files |
| Lookup p50/p95 ms | the warm lookup, every question 5 times |
| Duplicates | open register items that match one expected item, minus one, after `commitments.record_notes` with the call's notes |
| Wrong identity | expected items where the register did not do the expected thing (update the existing item, or add one new item) |
| Item 13 cross-client | events of the call on the other client's items, plus the other client's item 13 in the call's Tracker |
| False agreed | a "we will try" item that stands in the register as an open commitment |
| False proposal | a firm expected item that stands in the register as a "we will try" proposal (added in PB-51 fix round 3; 0 on every target, and the fixture has no firm task with a trailing "maybe" yet, so unit tests cover that wording) |

Matching rule: both sides lower case, whitespace collapsed, a phrase matches on word boundaries.

Not measured in G0: what the model writes (the preparation answer, the live answer, the notes). The after-call part uses
fixed notes as the notes step writes them today and measures only what the register does with them.

## The fixture

`spikes/m0/bench/g0/fixtures/`: one project folder (`northpoint`) with two clients, Acme and Globex, that both have an
"item 13" with different meanings. Three call series: Acme weekly status with Mike (4 past calls), Acme technical sync
with Emily (2, the first without notes), Globex weekly with Sarah (2). Project state (`pacenote/decisions.md`,
`_notion/... Current State.md`), folder files, the commitments register written by the app's own `record_notes`, and a
small Acme status site served by the harness on 127.0.0.1 with an ephemeral port. The target calls live apart in
`fixtures/targets/` (transcript and notes), so a preparation never sees its own call. Expected facts: `gold.yaml`.
All data is invented.

## How to rerun

```sh
cd spikes/m0
python3 bench/g0/run.py --variant A          # the table, and results/A-<date>.json (-2, -3... if it exists)
python3 bench/g0/run.py --variant A --tag x  # the same, saved as results/A-<date>-x.json (--label x too); never overwritten
python3 bench/g0/run.py --compare A C        # the newest result of each variant, metric by metric
python3 -m pytest -q tests/test_g0_bench.py  # runs the harness, checks the fixture (not the scores)
```

A run takes 10 to 25 seconds (the v1 targets in about 2, then gold v2 on both sets; `--set small`, `--set scale` or
`--set none` picks the sets), makes no model call and opens no connection but the local site and, for the run with
vectors, the bundled embed server on a free loopback port (any other host is blocked and counted). The stores of a run
live in its temp folder, never in Application Support. `python3 -m pytest -q tests/test_g0_gold_v2_pb52_s2.py` checks
gold v2, the scale set and the S2 gate. After editing the fixture notes, rebuild the register with
`python3 bench/g0/run.py --regen-register`.
