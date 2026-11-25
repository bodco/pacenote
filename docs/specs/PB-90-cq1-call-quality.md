# PB-90 CQ1: call and tip quality, spec (rubric v2, selection, events, privacy, console)

Status: CQ1 spec for review. SPEC ONLY: no product code changes. Task PB-90 (stream B, owner request 25.11, owner decisions 24.11, the
task page in the Product Backlog). Priority: after GraphRAG G3 (owner 24.11).

Where this file lives: the task asks for the spec "in research/". `research/` is the Pacenote root folder, outside the
app repository and outside the team worktree, so this spec follows the four earlier team specs and lives in
`docs/specs/`. The defaults it proposes are in [`rules/quality.yaml`](../../rules/quality.yaml) (a draft that nothing
reads yet).

Sources read for this spec: `spikes/m0/livetips_m0/judge.py` (the blind judge, rubric v1), `CallEngine.runJudge` and
`AppConfig.judgeAfterCall` (the developer switch, Settings > AI > After the call), `rules/analytics.yaml` (catalog v2),
`rules/usage.yaml`, `rules/ai.yaml` (routes, provider limits, economy mode), `rules/call_layout.yaml`, the server's
`analytics.py`, `analytics_events.yaml`, `funnels.yaml`, `console/aplatform.py` and `console/aview.py`, the analytics
plan and spec of 17.11 (`research/pacenote-analytics-plan-2026-11-17.md`, `research/pacenote-analytics-spec-2026-11-17.md`),
`PRIVACY.md` and `PRIVACY.uk.md`, and the Privacy section of Settings > General.

## 1. Owner decisions 24.11 (recorded as given)

| # | Decision |
|---|---|
| D1 | Statistics on call and tip quality for all outside users: whether tips came on time and whether they were useful by content, compared against the transcript, sent to telemetry. |
| D2 | There is no Pacenote AI key yet and cost matters: the judge does not score every call, only selected calls worth scoring, chosen by preparation, brief and call duration. |
| D3 | Scoring is on by default, with a switch in Settings > Privacy like the rest of analytics. No per-call send button. |
| D4 | The judge runs on the user's own AI route. |
| D5 | Priority: after GraphRAG G3. |

Everything else in this spec is a proposal. Proposals that change what a user pays or sees are listed in section 13
with a recommendation, and the defaults follow the recommendation.

## 2. What exists today

- **Judge v1** (`livetips_m0.judge`): scores each shown tip alone, blind (no engine name), shuffled, with the transcript
  from 90 s before the trigger line to 20 s after the tip was shown. Score 1 to 5, `timely`, `concrete`, a free-text
  `reason`. Presentation and interview calls get an extra rubric part (slide plan, PM's role). `--quick`: Sonnet, at
  most 25 tips sampled evenly, split between the app and the skill. Output: `<stem>.judge/judge.jsonl` and
  `judge.summary.json` (mean, p50, share of 4+, timely share, worst 3 per engine).
- **The developer switch**: Settings > AI > After the call > "Rate tips after each call (blind judge, about $0.10)",
  `AppConfig.judgeAfterCall`, off by default since 2026-10-26. `CallEngine.runJudge` starts the judge on the "after"
  route after every call when it is on; the result is one line in Settings and the menu bar. Nothing is sent to
  telemetry. The "$0.10" figure is a comment, not a measurement.
- **Judge v1 on other routes**: the judge passes Claude model ids (`claude-sonnet-5-5`, `claude-opus-5-5`) and
  Anthropic thinking options. `RoutedProvider` maps the model per leg (OpenAI and Gemini take their job model for
  "after", the Claude Code leg maps by `cc_model`), and the OpenAI and Gemini providers ignore the Anthropic extras. So
  v1 runs on those routes, but its model choice there is implicit. On the local route it would run on the model on
  this Mac.
- **Analytics v2**: `rules/analytics.yaml` is the master catalog; `server/.../analytics_events.yaml` and
  `research/pacenote-usage-v2-2026-11-17.yaml` are byte-for-byte copies; the app embeds it
  (`LiveTipsCore/AnalyticsCatalog.swift`, from `tools/gen_analytics_catalog.py`). No free strings: a `code` parameter
  has a closed `values` list. The server drops an unknown event, an unknown or invalid parameter, and any event older
  than 35 days. Today's quality-related events: `call_ended` (minutes, tips, rated, asks, brief), `tip_shown`,
  `tip_action`, `tip_rated` (the PM's own verdict and reason).
- **Switches**: Settings > General > Privacy: "Help improve Pacenote" (`shareUsage`, on by default since 0.19.0) under
  the local usage log. An organization can turn analytics on for work in that organization.
- **Console**: Platform > Analytics (staff only): Overview, Features, Funnels (`funnels.yaml`, data not code), Paths,
  Errors, Speed, Retention, People, Devices, Event explorer. Raw events are read only by errors, speed and funnels
  (capped to `DIAG_DAYS` = 92) and by paths and timelines (`RAW_DAYS` = 31); long periods read rollups. B15 S5 is the
  "check on real data, tune funnels and dashboards" step of the analytics plan; the Quality page reuses the funnel
  machinery S3 built and S5 tunes.

## 3. Two layers

1. **Local metrics on every call (CQ2).** No AI, no cost, computed at the end of every live call from the call's own
   files. They answer "on time" with numbers (lag from the trigger line to the shown tip) and "useful" with the PM's
   own actions (ratings, pins, hides).
2. **Selective judge (CQ3).** On a few calls a week per Mac, the judge scores up to 20 tips against the transcript
   with rubric v2. It answers "useful by content" and "correct against the transcript".

Both write `<stem>.quality.json` (section 5). CQ4 sends one `call_quality` event per call and one `tip_scored` per
judged tip.

## 4. Rubric v2

### 4.1 What is scored

Per tip, only shown tips of the app: kinds `tip`, `fact`, `answer`; no partial items, no question cards, no retracted
uid (the filter `judge.shown_tip` already applies). The skill's tips are not judged in the product (the app-versus-skill
comparison stays a developer run of `livetips_m0.judge`).

The judge stays blind and per tip: one request per tip, the system prompt cached, order shuffled with a fixed seed.

### 4.2 The answer per tip

```json
{"useful": 1, "timely": true, "correct": "yes", "concrete": true, "redundant": false, "reason_code": "good",
 "reason": "<max 15 words, stays on this Mac>"}
```

| Field | Values | Meaning |
|---|---|---|
| `useful` | 1 to 5 | 5 exactly what the PM needed then: concrete (fact, number, owner, date or a ready sentence), correct, before the PM had to answer. 4 useful and timely, minor issues. 3 somewhat useful, or useful but late. 2 generic, mostly redundant, or about another topic than the live one. 1 wrong, misleading or noise. (The v1 scale, renamed.) |
| `timely` | bool | Shown before the PM needed it (before the PM answered, before the topic moved on). |
| `correct` | yes, no, unknown | Against the transcript only. `unknown` when the transcript cannot tell (a cited document fact the transcript neither confirms nor contradicts is `unknown`, never `no`). |
| `concrete` | bool | Carries a fact, number, owner, date or a ready sentence. |
| `redundant` | bool | Repeats what was said in the last 90 s or an earlier tip of the window. |
| `reason_code` | `good`, `generic`, `redundant`, `off_topic`, `late`, `wrong_fact`, `too_long`, `noise`, `unclear` | The main reason; `good` when `useful` is 4 or 5. This is what telemetry gets. |
| `reason` | text | Up to 15 words. Local only: written to `quality.json`, never sent. |

The judge does not get the lag in seconds; the app computes it (section 5) so `timely` (the judge's view) and `lag_s`
(the clock) can be compared.

### 4.3 Call-type parts

Kept from v1: the presentation part (slide plan; pacing hints judged against the plan; silence while the PM presents
is correct) and the interview part (the PM's role). A sales call gets no extra part in v2 (open question Q7).

### 4.4 Request options per route

v2 sends no provider-specific options. The model comes from `rules/quality.yaml > judge.models` per route (proposed:
api `claude-sonnet-5-5`, code `sonnet`, openai `gpt-6.1-sol`, gemini `gemini-3.8-flash`, codex `codex/default`), the
same families the evidence summary job uses in `rules/ai.yaml`. The Anthropic thinking option that v1 sets
(`judge_extra`) moves into the Anthropic leg's defaults or is dropped; a JSON-only answer of 300 tokens at most.

### 4.5 Validity check before CQ4 sends anything

CQ3 runs v1 and v2 on the same 5 recorded calls (replay, no live call) and keeps v2 only if its `useful` correlates with
the PM's own `tip_rated` verdicts at least as well as v1's score (where the PM rated) and the two runs of v2 on the same
call agree on `useful` within 1 point for at least 80% of tips (proposed numbers).

## 5. `quality.json`

Path: `CallLayout.sidecar(<call stem>, ".quality.json")`, in the call folder's `.work/` (a legacy flat call keeps it
next to its other sidecars). One file per call. CQ2 writes `call`, `local`, `eligibility`; CQ3 adds `judge`; CQ4 adds
`sent`. A later writer keeps the sections it does not own. Written atomically (temp file and rename).

```json
{
  "format_version": 1,
  "rules_version": "0.3.0",
  "app_version": "0.28.0",
  "call": {"ended": "2026-11-25T14:32:10Z", "minutes": 42, "type": "sales", "brief": "chosen", "sheet": "quick",
           "lines": 512, "other_lines": 260, "muted_s": 0},
  "local": {"tips_shown": 17, "by_kind": {"tip": 12, "answer": 4, "fact": 1}, "retracted": 1,
            "lag_p50_s": 6, "lag_p90_s": 19, "late": 3,
            "rated": 4, "used": 2, "partly": 1, "not_useful": 1, "pinned": 1, "hidden": 2,
            "asks": 9, "asks_open": 2, "asks_muted": 0},
  "eligibility": {"eligible": true, "why": "judged", "rule": "quality.yaml schema 1", "decided": "2026-11-25T14:32:12Z"},
  "judge": {"state": "done", "why": "judged", "route": "api", "provider": "claude_api", "model": "claude-sonnet-5-5",
            "rubric": "v2", "started": "2026-11-25T14:40:02Z", "seconds": 71, "tips_judged": 17,
            "tokens_in": 15800, "tokens_out": 2100, "cost_usd": 0.07, "estimate_usd": 0.08,
            "tips": [{"uid": "t-0412", "kind": "tip", "at": "00:12:31", "lag_s": 5, "useful": 4, "timely": true,
                      "correct": "yes", "concrete": true, "redundant": false, "reason_code": "good",
                      "reason": "Gave the renewal date before the client asked twice.", "rated": "used"}],
            "summary": {"useful_avg": 3.6, "timely_pct": 76, "correct_pct": 88, "concrete_pct": 65}},
  "sent": {"call_quality": "2026-11-25T14:41:15Z", "tip_scored": 17}
}
```

Where each field comes from (checked on main at 4bf745b; "CQ2 adds" = no per-call store exists today):

| Field | Source |
|---|---|
| `call.minutes`, `brief`, `sheet`, `lines` | the same values `call_ended` gets today (`AnalyticsTracker.callEnded`, `CallBriefSignal`) |
| `call.type` | the value `call_started` gets today (`call_ended` has no `type`) |
| `call.other_lines` | the transcript (`<stem>.app.md`), lines whose speaker is not the PM |
| `call.muted_s` | CQ2 adds: the PB-104 muted-span marks in the transcript, summed |
| `local.tips_shown` | equal to `call_ended.tips` (`AnalyticsTracker.callCounts.tips`: every shown kind, all 16 `tip_shown` kinds) |
| `local.by_kind`, `retracted`, `lag_*`, `late` | the tips file `<stem>.app.tips.jsonl` (`kind`, `uid`, `src`, `at`, retract records) |
| `local.rated`, `used`, `partly`, `not_useful` | EditLog rows `tip_rating` of this call's stem (`key` = the tip's key, the last row per key wins, as for the engine's `tip_feedback`) |
| `local.pinned`, `hidden` | CQ2 adds: `tip_action` is counted only in memory today; CQ2 keeps the per-call counts |
| `local.asks`, `asks_open`, `asks_muted` | `call_ended.asks` and PB-104 `OpenAsks` over the call's files |
| `judge.tips[].rated` | the same EditLog rows, matched by key |

Only the judged kinds (`tip`, `fact`, `answer`, the v1 filter `judge.shown_tip`) are scored. The other shown kinds
(agenda, coach, risk, wrap-up and the rest) count in `tips_shown`, `by_kind` and the lag numbers but are not judged:
their usefulness depends on the call plan more than on the transcript (open question Q11).

Rules:

- `judge.state`: `not_eligible`, `queued`, `running`, `done`, `partial` (a budget cap or a second stop at call start
  ended it with some tips scored, or a restarted run with scores failed rules 12 to 16), `failed`, `expired`. A call
  holds a cap slot (section 6) in `queued`, `running`, `done`, `partial` and `failed`.
- `lag_s` = shown time minus trigger line time (`at` minus `src` in the tips file), capped at `local.lag_cap_s`
  (600). `late` counts tips with `lag_s` above `local.late_lag_s` (20).
- `asks_muted`: questions from a muted span (PB-104 `OpenAsks`) are counted apart and never as open.
- `correct_pct` is over tips whose `correct` is not `unknown`; empty when all are `unknown`.
- The cost numbers are estimates from `livetips_m0.cost` PRICES; plan routes (code, codex) have `cost_usd` null.
- The file holds the judge's `reason` text and tip `uid`s and stays on this Mac: call sync does not send it, the
  data export of the account does not include it (it is not on the server), reassigning or migrating a call moves it
  with the call.
- The old `<stem>.judge/` folder of the developer switch stays as it is (section 9).

## 6. Selection: which calls are judged

Defaults in `rules/quality.yaml > eligibility`. Decided right after the local metrics at the end of the call, and
rules 10 to 16 again when the judge starts (below). The first failing rule is the skip reason (`why`):

| Order | Rule (proposed default) | `why` when it fails |
|---|---|---|
| 1 | Settings > General > Privacy > "Rate tips on some calls" is on (default on) | `off` |
| 2 | "Help improve Pacenote" is on (the scores have nowhere to go otherwise) | `analytics_off` |
| 3 | Call type is not rehearsal or an imported transcript | `call_type` |
| 4 | At least 10 minutes | `short` |
| 5 | A preparation: `call_ended.brief` is chosen, auto or late (D2: preparation, brief) | `no_brief` |
| 6 | At least 3 tips shown | `few_tips` |
| 7 | At least 60 transcript lines | `few_lines` |
| 8 | At least 20 lines of the other side | `one_sided` |
| 9 | Muted spans under 30% of the call (PB-104) | `muted` |
| 10 | At most 1 judged call this local day | `day_cap` |
| 11 | At most 3 judged calls in the last 7 days | `week_cap` |
| 12 | The "after" route is not the model on this Mac | `local_route` |
| 13 | The "after" route can run (`AIRoute.canRun(job: "after")`) | `no_route` |
| 14 | Economy mode is off | `economy` |
| 15 | Metered routes (api, openai, gemini): the provider is under its monthly alert share (`rules/ai.yaml > provider_limits`, providers with a key only). Plan routes (code, codex) have no limit source in the app, so this rule does not apply to them; rule 13 covers a route marked down | `near_limit` |
| 16 | The budget estimate fits (section 7) | `budget` |

An eligible call is `queued`; the judge starts after the call's after-call jobs (notes, follow-up, Notion) are done and
no call is running. A queued call that has not started within `expire_hours` (24) of its end is dropped with `expired`
(the Mac was asleep, calls back to back).

**Cap slots.** The caps (rules 10 and 11) count slots, not finished runs, so calls that wait in the queue together
cannot pass the caps together:

- A call takes a slot when it is queued, dated by its queue time. Rules 10 and 11 count every call that holds a slot:
  `queued`, `running`, `done`, `partial` and `failed`. A second call that ends while the first one still waits is
  skipped with `day_cap` or `week_cap`.
- A slot is given back only when the judge never sent a request: the call is `expired`, or rules 12 to 16 fail when it
  starts (then `why` is that rule's code). A run that sent at least one request keeps its slot whatever its end state.
- Rules 10 to 16 are checked again when the judge starts (the route, the spend or the caps may have changed); for
  rules 10 and 11 the call's own slot is not counted.
- A run stopped at call start (section 7) keeps its slot and goes back to the queue once. The requests it already sent
  stay in the ledger and count toward the month cap; the restarted run scores only the tips not scored yet, within the
  same `per_call_usd` minus what the stopped run spent. A second stop ends it `partial` (some tips scored) or `failed`
  (none).
- A slot is dated by `eligibility.decided` (the queue time). The slots live in one file, `state/quality-slots.jsonl`
  next to `spend-ledger.jsonl` (one row per slot: call stem, decided, state; rows older than 8 days are dropped on
  write), so a restart of the app does not reset them and no scan of call folders is needed. It stays on this Mac and
  is not synced; `quality.json` stays the record of each call.

There is no maximum length: the cost grows with the tips judged (at most 20), not with the call.

## 7. Judge route and budget guard

- **Route**: the route chosen for the "After the call" job in Settings > AI, through `AIRoute.env(job: "after")` with
  `LT_AI_STAGE=judge`, **without the fallback chain**: the chain ends on the model on this Mac (rule 12) and may move
  to another provider, which would break the budget and mix judges. The judge runs with the chain off and
  `LT_LOCAL_FALLBACK=0` (as the evidence worker of PB-103); when that route fails, the run ends `failed` and is not
  retried on another route. `quality.json` and the event record the route and model that actually answered (from the
  completion), not the planned ones. The key goes to the child process only, as today.
- **Never during a call**: `CallEngine` keeps the judge `Process` (as `judgeRunning` today) and terminates it at call
  start; the call is re-queued once and keeps its cap slot (section 6, "Cap slots"). The PB-88 `call-active.json` is not used: it is written only with the
  `evidence_store` flag on.
- **Before the first request**: estimate the tokens (system prompt once, cached; per tip: the 110 s window and the
  tip, plus at most 300 output tokens) and price them with `livetips_m0.cost` PRICES for metered routes (api, openai,
  gemini). If the estimate is above `budget.per_call_usd` (proposed $0.15), sample fewer tips until it fits; fewer
  than 3 left: skip with `budget`.
- **Month cap**: judge spend of the current month from `spend-ledger.jsonl` rows with `job` = `judge` (the ledger already
  takes `LT_AI_STAGE` as the job), against
  `budget.per_month_usd` (proposed $1.00) for metered routes. Reached: skip with `budget`.
- **During the run**: spend is counted per answer; the cap reached stops the run with state `partial` and keeps the
  tips already scored.
- **Plan routes** (Claude account via Claude Code, ChatGPT account via Codex): no USD cost; they use the account's
  plan limits, which the app cannot read ahead. The judge skips only when the route is marked down (`no_route`); the
  caps of section 6 are the guard. The ledger's `equiv_usd` for these routes is recorded, not capped.
- **Ledger and Settings > Usage**: every judge request writes a spend-ledger row with `job` = `judge` (today's behaviour), so Settings >
  Usage shows it with the other after-call work.
- **Unpriced model** on a metered route: the judge does not run (`budget`), the same rule as the evidence worker.
- **Measurement in CQ3**: the real tokens and cost of 5 replayed calls go into the CQ3 notes; the defaults above are
  then corrected. Until then no number in this spec is a measurement.

## 8. Events (CQ4)

Drafted in `rules/quality.yaml > events`; CQ4 copies them into `rules/analytics.yaml` with the new enums and values.
Every name and type below is checked against the current catalog by `spikes/m0/tests/test_quality_rules_pb90.py`.

### 8.1 `call_quality`: one per live call, when its state is final

| Parameter | Type | From |
|---|---|---|
| `judged` | bool | the judge ran (done or partial) |
| `why` | `quality_skip` | `judged`, or the skip reason (section 6), or `failed`, `partial`, `expired` |
| `type`, `brief` | `call_type`, `brief_kind` | the call |
| `minutes` | minutes | the call |
| `tips`, `late`, `retracted` | count | local |
| `lag_p50`, `lag_p90` | seconds | local |
| `rated`, `used`, `not_useful` | count | local (the PM's own ratings) |
| `asks`, `asks_open` | count | local |
| `scored` | count | judged tips |
| `useful_avg` | score (0 to 100) | judge: mean `useful` x 20, rounded |
| `timely_pct`, `correct_pct`, `concrete_pct` | score (0 to 100) | judge |
| `provider` | provider | the judge's route (`Analytics.provider(route:)`) |
| `model` | model | the judge's model, only when it matches the `model` type; codex sends none |
| `judge_s` | seconds | the judge's run time |

Judge fields are left out when `judged` is false. The event's `t` is the time it is sent (the state became final),
never the call time, so a judge that runs hours later is still inside the server's 35-day window. `o` is the call's
organization, as for `call_started`. A call that is skipped sends right after the end of the call; a queued call sends
after the judge or at expiry.

### 8.2 `tip_scored`: one per judged tip (20 at most per call)

| Parameter | Type |
|---|---|
| `kind` | `quality_tip_kind` (tip, fact, answer) |
| `type`, `brief` | `call_type`, `brief_kind` (so breakdowns need no join) |
| `useful` | small (1 to 5) |
| `timely`, `concrete`, `redundant` | bool |
| `correct` | `quality_correct` (yes, no, unknown) |
| `reason` | `quality_reason` |
| `lag` | seconds |
| `rated` | `quality_rated` (none, used, partly, not_useful): the PM's own verdict on the same tip |
| `provider` | provider |

There is no call id: the catalog has no free id type and `o` is the organization. `tip_scored` carries the call
context it needs (type, brief) and `call_quality` carries the call totals, so the console needs no join. Volume: at
most 3 judged calls a week per Mac, so at most 60 `tip_scored` a week per Mac.

### 8.3 Switches and sending

- `call_quality` with `judged` false is a usage event like `call_ended`: it is sent when "Help improve Pacenote" is
  on, or when the organization turned analytics on for that call's work.
- The judge runs, and `tip_scored` is sent, only when both "Help improve Pacenote" and "Rate tips on some calls" are
  on. The organization switch does not start the judge on someone's own AI route (open question Q1).
- "Show what is sent" lists both events like any other. The local usage log (`rules/usage.yaml`, schema 1) gets no new
  event.

### 8.4 Catalog changes in CQ4 (one catalog bump)

- `enums`: `quality_skip`, `quality_correct`, `quality_reason`, `quality_rated`, `quality_tip_kind`. The values
  `off`, `yes` and `no` stay quoted in the catalog: the server and the generator read it with `yaml.safe_load`, which
  turns bare `off`, `yes` and `no` into booleans, and the event would then be dropped as `bad_param`.
- `events`: `call_quality`, `tip_scored`.
- `screens.console_analytics`: add `quality` (the console page of CQ5), so CQ5 does not bump the catalog again.
- Deploy order: the server's copy first, then the app (an older server drops both events as unknown).

## 9. Settings and the developer switch

- **New switch** (CQ3), Settings > General > Privacy, right under "Help improve Pacenote" and disabled when it is off:
  `AppConfig.shareQuality`, default true. Proposed text:
  - EN: "Rate tips on some calls" / "A few times a week, after a prepared call of 10 minutes or more, your After the
    call AI scores up to 20 tips against the transcript. Pacenote gets only the scores, never the words. It uses that
    AI's credit or plan, like a summary does."
  - UK: "Оцінювати підказки на деяких дзвінках" / "Кілька разів на тиждень, після підготовленого дзвінка від 10 хвилин,
    ваш AI для роботи після дзвінка оцінює до 20 підказок за транскриптом. Pacenote отримує лише оцінки, ніколи слова.
    Це витрачає кредит чи план цього AI, як і саммарі."
- **No per-call button** (D3).
- **The developer switch** `judgeAfterCall` (Settings > AI > After the call): recommended to become "Score every call
  (developer)": it bypasses the caps and the brief rule but not the budget guard and not rule 12, writes v2 into
  `quality.json` and does not send events beyond what the switches above allow. Its `<stem>.judge/` output and the
  menu bar line are kept for the app-versus-skill comparison. Open question Q2.

## 10. PRIVACY text (CQ4 adds it to `PRIVACY.md` and `PRIVACY.uk.md`)

A new bullet after "Help improve Pacenote":

EN:

> - **Tip quality scores (on by default).** A few times a week, after a prepared call of 10 minutes or more, Pacenote
>   asks the AI you chose for after-call work in Settings > AI to score up to 20 of the tips it showed you, each
>   against the part of the transcript around it. Those transcript excerpts and tips go only to that AI provider,
>   under your own key or account, the same way your call summary does, and it uses that provider's credit or plan.
>   Pacenote itself receives only numbers and fixed codes, never words: for each scored tip its kind, whether it was
>   on time, useful (1 to 5), correct by the transcript, concrete or repeated, a reason from a fixed list, its delay
>   in seconds and your own rating of it; for the call its type, its length, whether it was prepared, if it was not scored the reason as a code, the averages of
>   the scores, how long the scoring took, and which AI provider and model gave the scores. The provider's written
>   reason stays on your Mac. On every call Pacenote also counts, on your Mac, how many tips were shown, withdrawn and
>   late, how late they were, how you rated them, and how many questions were asked and left open, and sends these
>   numbers with product analytics. Only Pacenote staff see the scores; the owner and admins of your organization do
>   not. Turning off Settings > General > Privacy > Rate tips on some calls stops the scoring by the AI provider only.
>   The counts are product analytics: they follow Help improve Pacenote, or your organization's analytics setting for
>   calls in that organization. Turning off Help improve Pacenote also stops the scoring. The same retention and
>   account deletion rules as product analytics apply.

UK:

> - **Оцінки якості підказок (увімкнено за замовчуванням).** Кілька разів на тиждень, після підготовленого дзвінка від
>   10 хвилин, Pacenote просить AI, який ви обрали для роботи після дзвінка в Налаштування > AI, оцінити до 20
>   показаних вам підказок, кожну за частиною транскрипту навколо неї. Ці уривки транскрипту й підказки йдуть лише до
>   цього AI-провайдера, з вашим ключем чи акаунтом, так само як саммарі дзвінка, і витрачають кредит чи план цього
>   провайдера. Сам Pacenote отримує лише числа й фіксовані коди, ніколи слова: для кожної оціненої підказки її вид, чи
>   була вона вчасно, корисна (від 1 до 5), правильна за транскриптом, конкретна чи повторна, причину з фіксованого
>   списку, затримку в секундах і вашу власну оцінку; для дзвінка його тип, тривалість, чи він був підготовлений,
>   якщо його не оцінювали, причину у вигляді коду, середні оцінки, скільки тривало оцінювання і які AI-провайдер і модель дали оцінки. Пояснення провайдера
>   лишається на вашому Маку. На кожному дзвінку Pacenote також рахує на вашому Маку, скільки підказок показано,
>   відкликано і показано із запізненням, наскільки пізно, як ви їх оцінили, скільки питань поставили і скільки
>   лишилося без відповіді, і надсилає ці числа разом із продуктовою аналітикою. Оцінки бачить лише персонал Pacenote;
>   власник і адміністратори вашої організації їх не бачать. Вимкнення «Оцінювати підказки на деяких дзвінках» у
>   Налаштування > Загальні > Приватність зупиняє лише оцінювання AI-провайдером. Лічильники належать до продуктової
>   аналітики: вони діють за перемикачем «Допомогти покращити Pacenote» або, для дзвінків в організації, за
>   налаштуванням аналітики вашої організації. Вимкнення «Допомогти покращити Pacenote» зупиняє й оцінювання. Діють
>   ті самі правила зберігання і видалення акаунта, що й для продуктової аналітики.

The bullet "Otherwise nothing is sent to the author" then names "the tip quality scores above" too.

## 11. Console: Platform > Analytics > Quality (CQ5)

Staff only, like the other Analytics pages (platform session, RLS). Never shown in the organization console.

- Route `/platform/analytics/quality`, entry `("/quality", "Quality", "quality")` in `PAGES`, template
  `platform/analytics/quality.html`, logged as `screen_view {scr: console_analytics, sub: quality}`.
- Filters: the shared ones (period, app version, plan, organization); default 30 days; reads raw `call_quality` and
  `tip_scored` through the (e, t) index capped to `DIAG_DAYS`, like errors and funnels. A rollup is not needed at this
  volume (open question Q6).
- **Tiles**: calls with a `call_quality`; share judged; tips shown per call (p50); lag p50 and p90; late share; useful
  average; timely, correct and concrete shares; the PM's own "used" share.
- **Trend**: weekly useful average, timely share and lag p50 by app version (the question "did this release make tips
  better").
- **Breakdowns** (tables with CSV, like the other pages): by tip kind; by call type; by `brief` (none, chosen, auto,
  late: the question "do prepared calls get better tips"); by judge provider and model (judges differ, so a mix shift
  must be visible); `reason` counts; skip reasons (`why`) of calls not judged.
- **Agreement**: `tip_scored.rated` against `useful` (the PM's verdict versus the judge), a 4 x 5 table.
- **Funnel** in `funnels.yaml` (data, no migration), "Prepared call to a scored call", window 2 days:
  `prepare_run {ok: true}` > `call_started {has_brief: true}` > `call_ended` > `call_quality` >
  `call_quality {judged: true}`. It shows the funnels page's usual by-version and by-plan splits.
- Minimum group size (proposal, borrowed from the organization Usage rule of the analytics spec; Platform pages have
  no such rule today): a breakdown row with fewer than 3 people is shown as "fewer than 3".
- Staff can also see single `tip_scored` and `call_quality` rows in a person's or a device's timeline and in the Event
  explorer, like every other event; the PRIVACY text says "staff see the scores" for that reason.
- `labels.py`: `call_quality` and `tip_scored` go into `NOT_FEATURES` so the Features page does not count them as
  feature use; their labels and the `why` and `reason` labels go into the labels module.

## 12. Steps CQ2 to CQ5 and shared files

Each step is mergeable on its own and changes nothing the next step needs to undo. Repositories: **app** (this repo),
**server** (`Pacenote/server`), **research** (the Pacenote root `research/`, the third catalog copy).

| Step | What | Files (repo) | Tests | Visible to users |
|---|---|---|---|---|
| CQ2 | Local metrics on every call and `quality.json` (`call`, `local`, `eligibility` with the decision only; nothing judged, nothing sent) | new `LiveTipsCore/CallQuality.swift` (pure: tips file, transcript, call sidecars to metrics; the eligibility decision from `rules/quality.yaml`), `CallEngine.swift` (end-of-call hook), `state/quality-slots.jsonl` (the cap slots of section 6), `rules/quality.yaml` (status to active for `sidecar`, `local`, `eligibility`), `rules/call_layout.yaml` and the three call-layout implementations if the sidecar list is named there, `CallReassign`/`CallMigration` (move the new sidecar), call sync (confirm it is not sent) (app) | CoreChecks: metrics from fixture calls (lag, late, retracted, muted asks), every eligibility rule and its `why`, the day and week caps counted as slots (two calls queued together: the second gets `day_cap`; an expired call gives its slot back), atomic write keeping other sections; a reassign test that moves `.quality.json` | no (a file on disk) |
| CQ3 | Selective judge v2, budget guard, Privacy switch | `livetips_m0/judge.py` (`--rubric v2`, `--quality-json` merge, per-route models from `rules/quality.yaml`, no Anthropic-only extras), `livetips_m0/cost.py` if a judge stage is missing, `CallEngine.runJudge` (queue, after-call order, call-active stop), `LiveTipsCore/Settings.swift` (`shareQuality`), `SettingsView.swift` (Privacy row; the developer row of section 9), i18n `en.json` and `uk.json`, `rules/quality.yaml` (`judge`, `rubric` to active), docs EN/UK (app) | pytest: rubric v2 parse and validation with FakeProvider, per-route model choice, budget estimate and caps (per call, month, partial), unpriced model, merge into an existing `quality.json`; CoreChecks: switch defaults and the disabled state, queue order, expiry, rules 10 to 16 again at start, a run stopped at call start keeps its slot and scores only the rest within the remaining per-call budget; the replay of section 4.5 on 5 calls with the measured cost in the notes | yes (switch; spend in Settings > Usage) |
| CQ4 | Events, catalog, PRIVACY | `rules/analytics.yaml` (section 8.4), `AnalyticsCatalog.swift` regenerated by `tools/gen_analytics_catalog.py`, the sender in `CallEngine`/`AnalyticsTracker` (one `call_quality` per call, `tip_scored` per tip, `sent` section), `PRIVACY.md`, `PRIVACY.uk.md`, `telemetry.toggle.sub` if it names what is sent, docs EN/UK, CHANGELOG (app); `analytics_events.yaml` byte copy (server); `pacenote-usage-v2-2026-11-17.yaml` byte copy (research) | `test_quality_rules_pb90.py` stays green (the catalog must equal the draft in `rules/quality.yaml`, or both change); CoreChecks: catalog parity and every `code` with values; the events built from a fixture `quality.json` pass the catalog; nothing sent with the switches off; org switch sends `call_quality` but no judge; pytest (server): the two events accepted, a bad `reason` dropped | yes (PRIVACY, Show what is sent) |
| CQ5 | Console Quality page and funnel | `console/aplatform.py` (`PAGES`, `quality()`, route), `templates/platform/analytics/quality.html`, `funnels.yaml`, `console/labels.py` (server) | pytest (server): page renders with fixture events, filters, CSV per table, fewer-than-3 rule, the funnel counts a fixture path, staff only (403 for an org admin) | staff only |

Order: CQ2 > CQ3 > CQ4 > CQ5. CQ4 can be built in parallel with CQ3 on fixture `quality.json` files but merges after
it. CQ5 needs the server catalog of CQ4 deployed.

**Shared files** (touched by more than one step, so steps that run in parallel must not both hold them):

| File | Steps |
|---|---|
| `rules/quality.yaml` | CQ2, CQ3 (and its `events` section is read by CQ4) |
| `app/Sources/LiveTips/CallEngine.swift` | CQ2, CQ3, CQ4 |
| `rules/analytics.yaml` and its two byte copies | CQ4 only (the console sub-screen of CQ5 goes in with it) |
| `LiveTipsCore/Settings.swift`, `SettingsView.swift`, i18n en/uk | CQ3 (CQ4 only if the Privacy texts change) |
| `PRIVACY.md`, `PRIVACY.uk.md` | CQ4 |
| `docs/en/live-tips.md`, `docs/uk/live-tips.md`, `CHANGELOG.md` | CQ3, CQ4 |
| `server/.../funnels.yaml`, `console/labels.py` | CQ5 |

Outside the team worktree: the server and the research copy of the catalog live in other repositories; CQ4 and CQ5
need the owner's go for those commits and for the server deploy (rule 5 of the team process).

## 13. Open questions with recommendations

| # | Question | Options | Recommendation (the default in `rules/quality.yaml`) |
|---|---|---|---|
| Q1 | Does an organization's "analytics on" also start the judge on a member's own AI route? | a) yes; b) no: it sends `call_quality` (local metrics) but the judge needs the member's own switches | b. The judge spends the member's credit or plan; the organization switch was agreed for usage events, not for spending. |
| Q2 | What happens to the developer switch "Rate tips after each call"? | a) remove it; b) keep it as "Score every call (developer)" on v2, bypassing caps but not the budget guard; c) keep v1 as is | b. It keeps the team's app-versus-skill runs and replays on one rubric. |
| Q3 | Caps: how many judged calls per Mac? | 1 a day and 3 a week; 1 a day and 5 a week; 2 a week | 1 a day, 3 a week until CQ3 measures the cost; then revisit with the numbers. |
| Q4 | Calls without a preparation: never judged, or a small sample for a baseline? | a) never (D2 as written); b) 1 in 10 eligible unprepared calls, within the same caps | a for now. Without b the console cannot compare prepared and unprepared calls by the judge (only by local metrics); b is a one-line change in `brief_kinds` and a sample rule if the owner wants that comparison. |
| Q5 | A "missed moments" pass (one request per call: where a tip was needed and none came)? | a) not in CQ3; b) in CQ3 behind its own cap | a. It measures coverage, not tip quality, roughly doubles the cost of a judged call and needs its own rubric; a follow-up task once CQ3's cost is known. |
| Q6 | Rollup table for quality on the server? | a) raw events with `DIAG_DAYS`; b) a daily rollup | a. At most 60 `tip_scored` a week per Mac; add a rollup only if the page is slow. |
| Q7 | A sales part in the rubric (qualification, next step)? | a) not in v2; b) add it | a. Sales tips are judged by the general rubric first; a sales part after the first month of data. |
| Q8 | Do users see their own scores (Stats or the call page)? | a) not in CQ2 to CQ5; b) a line on the call page | a. D1 asks for statistics for the team; showing a user their own scores is a product choice for later. |
| Q9 | Judge route when the "after" route is the model on this Mac | a) skip (`local_route`); b) judge on it and mark the provider `local` | a. Its scores are not comparable with the cloud judges (about 60% of Sonnet in docs 14.1g) and would mix into the averages. |
| Q10 | When a Pacenote AI key exists | a) keep the user's route (D4); b) judge on Pacenote's key | a until the owner decides; the route is one value in `rules/quality.yaml > judge.job` and a provider `pacenote` already exists in the catalog. |
| Q11 | Judge the other shown kinds (agenda, coach, risk, wrap-up, presentation Q&A)? | a) only tip, fact, answer (v1 filter); b) all kinds | a. The other kinds are counted locally; judging them needs the call plan and the register in the prompt, a later rubric part. |

None of these blocks CQ2. Q1 and Q2 should be answered before CQ3 merges; the defaults follow the recommendations.

## 14. Acceptance for this spec (CQ1)

- [x] Rubric v2 (section 4).
- [x] Eligibility rules with defaults in a rules yaml (section 6, `rules/quality.yaml`).
- [x] `quality.json` format (section 5).
- [x] `call_quality` and `tip_scored` events (section 8), checked against the catalog by a test.
- [x] Judge route and budget guard (section 7).
- [x] PRIVACY text EN and UK (section 10).
- [x] Console page (section 11).
- [x] Owner decisions 24.11 recorded (section 1).
- [x] Open questions with recommendations (section 13).
- [x] CQ2 to CQ5 split into mergeable steps with shared files named (section 12).
