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

A run takes about 2 seconds, makes no model call and opens no connection but the local site (any other host is
blocked and counted). Variants B (wider text search) and C (graph) are added by the GraphRAG steps that build them;
until then `--variant B` and `--variant C` stop with a message. After editing the fixture notes, rebuild the register
with `python3 bench/g0/run.py --regen-register`.
