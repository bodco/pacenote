# PB-37: Research this website (roadmap G6, with FB7)

Status: spec and plan, awaiting the owner's answers (see "Questions for the owner"). No product code in this step.

Sources read for this spec: the current code on `main` (`spikes/m0/livetips_m0/sheet/web.py`, `sources.py`, `build.py`,
`prompt.py`, `app/Sources/LiveTips/MeetingSheetPrep.swift`, `MeetingSheetDraft.swift`), `docs/en/live-tips.md` (Meeting
Sheet, Sources), `CHANGELOG.md`, and the roadmap items G6 and FB7. The owner's decision D57 puts G6 first in the
evidence graph block.

## 1. Problem

Before a call, the most useful public source about a new company is often its own website: what it does, the team,
products, customers. Today a person who pastes a company link into Sources gets **one page**: the packed routes
fetch that single URL at build time (`build.py` `fetch_page`) and the Claude routes are told to `WebFetch` it
(`prompt.py`, "this page only"). To read the About, Team and Services pages the person must find and paste each link.

What is already built but not reachable: `sheet/web.py` has a full site reader (`plan()` and `fetch()`): same domain
only, depth 2, up to 15 pages (`DEFAULT_MAX_PAGES`), robots.txt respected, a 1 s pause between requests, a Pacenote
user agent, no sign-in, cart, policy, feed, contact or file pages, duplicate pages detected, emails and phone numbers
cut before anything is stored, every read page a `web_page` source with its own URL, links to other sites offered but
never followed. It has CLI commands `webplan` and `webfetch` and tests with a local HTTP server
(`tests/test_sheet_web_0100.py`). **No Swift code calls them** (checked: no `SheetCLI.run("webplan"` or
`"webfetch"`; the only Swift use of the web module is the Pacenote browser capture). So the capability exists in the
sidecar only.

Two gaps keep it from being the feature the roadmap describes:

1. **No UI.** Nothing in Sources starts a site read, shows the plan, the page budget or the cost, or reports what was
   read.
2. **The queue ignores the call goal.** `PRIORITY` in `web.py` is a fixed list (about, team, services, pricing,
   cases, blog ...). A sales intro, an interview of a candidate's employer, a partner call and a vendor review
   need different pages first.

A third, smaller gap: Quick prep takes files and a note only; links go through Expert (FB7).

## 2. Who it is for

- A PM or sales lead preparing a first call with a company they do not know (sales intro, partner or vendor call,
  interview of a team), who wants the brief and Meeting Sheet to rest on the company's own pages, not on one
  homepage.
- The same person on a repeat call in a series, who wants to refresh the site facts without pasting links again.
- People in Quick prep who have only a company link and no time for Expert's five steps (FB7).

## 3. Scenarios

1. **Sales intro, Expert.** Mike has a call with a new prospect. In Expert step 3 he pastes `https://example-company.com`
   (a fictional domain for this spec) and presses **Research this website**. Pacenote reads the home page, shows a
   plan of up to 15 pages ordered for a sales intro (About, Team, Products, Customers first), with a tick per page,
   the page count, the size in tokens and the cost. Mike unticks a page, presses **Read N pages**, sees progress,
   and the pages appear under the site in "In this sheet". The brief and the Meeting Sheet cite those pages.
2. **Different goal, different queue.** For an interview with the goal "understand the engineering team", the same
   site puts Team, Careers and Blog before Pricing.
3. **Quick with a link (FB7).** Emily opens Quick prep, pastes the company link in the Sources row, and the same
   Research this website action is offered there with the same plan, budget and cost.
4. **Blocked or thin site.** robots.txt disallows the site, the site needs JavaScript and the pages are empty, or the
   site is down. The plan says so in one line per cause and the person can add text by hand or skip. Nothing is
   silently dropped.
5. **Budget reached.** A site with 200 pages: Pacenote reads 15, says "15 of about 40 pages found; the rest are
   not read" and lists what was left out and why (limit, policy page, robots).
6. **Repeat call.** The series remembers the site as a source (per the answer to question 4); a new preparation
   reuses the stored pages or reads again, by the person's choice.
7. **Cancel.** The person presses Cancel while pages are being read; pages already read are kept, the rest are not
   read, nothing is half-written.

## 4. Acceptance criteria

Each one can be checked by a test, by the Lumenfield-style local server fixture, or by looking at the window.

**Plan and queue**
1. Pasting a link that is a company site (not Google, Notion or LinkedIn) in Sources, Expert step 3, shows a
   **Research this website** action on that source row. Pasting a link keeps working as today (one page) when the
   person does not press it.
2. Pressing it runs `webplan` on the start page only and shows the plan before anything else is read: each page
   with path, kind, a tick, and the reason when it is not planned (sign-in, cart, policy, feed, contact, file,
   robots, limit). Only same-site pages are planned. Other sites appear as separate suggested sources, never read.
3. The order of the queue depends on the call goal: the plan request carries the goal line, the call type
   (`sales`, `presentation`, `interview`, `internal`, ...) and the audience. Planned pages are ordered by a score
   made of (a) a goal-term match against the link text and URL path and (b) the existing section priority as the
   tie-break. With an empty goal the order is exactly today's `PRIORITY` order (regression test).
4. The budget is 15 pages by default, counting the start page; the plan never lists more than the budget as
   planned; the rest say "limit". Pages found while reading (depth 2) never push the total above the budget.
5. Contact pages are skipped, and emails and phone numbers are removed from page text before it is stored (existing
   behaviour, kept; test with the fixture's contact page).
6. robots.txt is checked for every page at read time; the user agent names Pacenote; at least 1 s passes between
   requests (existing behaviour, kept; assert on the fixture server's hit log).

**Reading and feeding the brief**
7. **Same text on every route.** Each page read is stored as a copy and enters the sheet as a `web_page` source
   with `uri`, `path` (the stored copy), `parent_uri` (the site) and `why` (for example "page of example-company.com
   (team)"). The packed routes (API, OpenAI, Gemini, local) and the Claude routes read that stored copy. **No page of
   a researched site is fetched a second time at build time** (no `fetch_page`, no `WebFetch` for these sources),
   so the text is identical on every route. Test: build with the fake route and the packed path and assert the fixture
   server saw each page once.
8. The Claude routes get these sources as `Read <stored file>` lines, so the WebFetch host allowlist (SEC-01,
   `PrepSecurity.webHosts`, the sales web search) is not widened.
9. A fact from a site page cites the concrete subpage (the existing `web_page` behaviour). The brief
   (`derive` -> `brief.md`) and the Meeting Sheet blocks use the pages like any other source: counted in Size,
   shown in "In this sheet", listed in the sheet's source list with their `why`.
10. A page that is empty or a placeholder is reported as empty and not used; a page cut at 2 MB says "truncated"
    (existing behaviour, kept).

**Cost and size**
11. Before reading, the plan shows: pages planned, an estimated size in tokens (the existing 1500-token page
    estimate until read), and the **cost line**. After reading, the real tokens replace the estimate and Size
    (step 4) updates.
12. The cost line follows the answer to question 5. The crawl itself makes no model call, so its own cost is zero;
    what is shown is the extra the pages add to the build on the chosen route (tokens, and money where the route has
    a price). Routes without a price (subscription or account routes) show tokens and the share of the context
    window, never an invented dollar figure.

**Progress, cancel, failure**
13. Reading shows progress ("Reading page 4 of 12: /team") and can be cancelled at once; cancel keeps the pages
    already read and discards the unfinished one.
14. The window stays responsive: the crawl runs in the sidecar through `SheetCLI`, off the main thread, and nothing
    in it runs during a live call (the live call makes no new network request for this feature).
15. Every page ends in one state in the report: read, empty, blocked, error, skipped, duplicate. Network errors
    never crash the build; a site that cannot be read leaves the other sources and the build intact.

**Quick (FB7)**
16. Quick prep's Sources row has a link field (paste a link: Notion, Google Docs, a site, LinkedIn). It uses the same
    `add(pasted:)` path as Expert, so the access check, the fixes (Connect Google, Retry, Open in the browser) and
    Research this website behave the same, and a link typed into "Anything special today?" is still only offered
    with Move to Sources.
17. In Quick the plan shows in place (under the row, like People > Change) with the same budget and cost line; the
    person does not have to open Expert.

**Language and parity**
18. All new texts exist in `en.json` and `uk.json` together and use no long dash. Docs EN and UK are updated in step.
19. Meeting Sheet language is untouched: page text keeps its own language and the model writes in the call language
    as for any source.

## 5. Out of scope

- Following links to other domains (offered only), crawling past depth 2 or past the budget, a sitemap-wide index,
  scheduled re-crawls, or crawling during a call.
- Reading sign-in pages, JavaScript-only sites (no headless browser), PDFs and other files on the site.
- LinkedIn (FB12 is a separate spike; the Pacenote browser capture is unchanged). Plain web search is unchanged.
- The evidence graph (G1 to G5), storing page text in a graph, and LLM extraction. G6 only produces ordinary
  sources that G2 can ingest later.
- Org or team sharing of crawled pages (on the server, content is encrypted with the owner's key; nothing is sent).
- A price in dollars for subscription routes (see question 5).

## 6. UI impact

- **Expert step 3, Sources** (`SheetSourcesStep`, `SheetDraftSourceRow` in `MeetingSheetPrep.swift`): on a source of
  type `web`, a button **Research this website**; when pressed, an inline panel with the plan list (tick, path,
  kind, reason if skipped), a counter "N of 15 pages · ~X tokens · cost", **Read N pages**, **Cancel**, and a
  progress line. After reading, the pages appear as child rows under the site row in "In this sheet" (indented, own
  tokens, own tick), the site row shows "N pages read". The suggested external domains show as Suggested sources.
- **Quick prep** (`SheetQuickView`): a link field and the same panel inside the Sources summary row's Change view.
  The "No sources yet" text, which already tells people to paste a link in the note, points to this field instead.
- **Size step**: counts the pages read; the biggest-sources list can show the site as one group.
- New view code goes in a new file (`SheetSiteResearch.swift`) so Quick and Expert share one component.
- A visual review by Design is needed (new panel in two screens); the step's screenshots go through the usual
  `SheetShots` path. No change to the tips window or to the live call.

## 7. Data

- Request to the sidecar `webplan`: `{url, depth, max_pages, respect_robots, goal, call_type, audience, route}`
  (`goal`, `call_type`, `audience` new; `route` only for the cost estimate). Response: the existing plan plus per page
  `score` and `why_ranked` (short, for the plan list) and `est_tokens`.
- Request to `webfetch`: `{plan, out_dir, delay, start_n, cancel_path}` (`cancel_path`: a file the app creates to
  cancel; the sidecar checks it between pages). Response: the existing report, `sources` (`web_page` with `path`,
  `parent_uri`, `why`, `checked_at`) and `suggested`.
- Stored pages: one `.txt` per page (title, URL, text) written by `fetch(out_dir=...)`. Where (question 3). A
  `web_page` source with a stored `path` must be read from the file: today the `web`/`web_page` branch in
  `sources.py` only looks at `text` and otherwise sets `fetch = True`, which would fetch the page again. This is
  the main code risk of the feature (subtask B).
- The series setup remembers the site source (`uri`, depth, budget), not the page text (question 4).
- No migration of existing sheets: old `web` and `web_page` sources behave as before. The sheet schema already has
  `web_page`; if `parent_uri` is not yet in `schema.py`, subtask B adds it as optional (no schema version bump,
  validated by the existing `validate` tests).
- Analytics (opt-in telemetry): at most counters (a site read started, pages read, cancelled), no URLs and no text;
  added to the catalog only if the owner wants them.

## 8. Privacy

- Only public pages of a site the person named, one site at a time, started by a click. robots.txt is honoured; the
  user agent says Pacenote and gives the product page. Pacenote does not send anything from the project, the call
  context or people's names to the site (a plain GET, no query from the files).
- Emails and phone numbers are never stored (cut from the text before the copy is written; Contact pages are not
  read).
- The pages' text goes to the person's chosen AI route as part of the sources, like any other source, so the
  cost and "what is sent" lines must name it. Nothing goes to Pacenote servers or telemetry.
- **Page copies** are public site text, but they sit in the project folder if stored under `live/`, which Drive
  may sync to colleagues. State this in docs and in the plan footer. The folder rules that exclude generated files
  from search and Drive copies should be checked for the new folder (risk in section 10).
- The SEC-01 WebFetch allowlist is unchanged (criterion 8). The crawler's own requests come from the sidecar and
  carry no secrets. SSRF check: the sidecar must refuse to plan a URL that resolves to a private address (loopback,
  link-local, RFC 1918) outside tests, and must not follow a redirect to one; the fixture server on 127.0.0.1 is
  allowed only behind a test flag.
- The privacy page and the guide say that Pacenote reads public pages of a site on request. No change to
  PRIVACY.md data categories is expected (nothing leaves the Mac except the page requests themselves and the
  model call that already exists); confirm during the docs subtask.

## 9. Docs and changelog

- `docs/en/live-tips.md` and `docs/uk/live-tips.md`: in "Sources: what you can add" (Meeting Sheet), replace the web
  line with Research this website: the plan, the order by goal, the budget, the cost line, what is skipped, where
  copies live; in Quick, the link field. Add the new bullet to the "Quick or Expert" table if rows differ.
- `CHANGELOG.md`: an entry under **Unreleased** (the file has no Unreleased heading today; add one) when the
  feature ships, user-visible, with the date, in the existing style. **Not added in this step**: a spec is not a
  user-visible change.
- `docs/en/roadmap.md` and `docs/uk/roadmap.md`, and the site `/roadmap/` page: updated by the release step, not here.
- `rules/`: no change expected (this is app behaviour, not call rules). The skill parity question is question 6.
- i18n: `app/Resources/i18n/en.json` and `uk.json` together (keys `sheet.ui.web.*`).

---

## Plan (tech lead)

### P1. How it fits the current code

```
Sources (Swift)                         sidecar (Python, livetips_m0.sheet)
SheetSourcesStep / Quick row  ---- webplan {url, goal, call_type, audience, max_pages} ----> web.plan()  (+ goal score)
SheetSiteResearch panel       <--- plan, est_tokens ---------------------------------------
tick pages, Read N            ---- webfetch {plan(ticked), out_dir, cancel_path} --------> web.fetch()  (stored .txt per page)
draft.sources += web_page     <--- sources[{type: web_page, path, parent_uri, why}] -------
checkSources / size / build   ---- existing sources, size, build, derive ----------------> sources.py reads path, no fetch
```

The brief is derived from the sheet (`derive` -> `brief.md`), and `SheetRunner` builds the sheet, so one pipeline
feeds both: no separate brief work.

### P2. Modules and files

Python (sidecar), all under `spikes/m0/livetips_m0/sheet/`:

| File | Change |
|---|---|
| `web.py` | `plan()` takes `goal`, `call_type`, `audience`; new `goal_score(url, text, goal, call_type)` (deterministic: terms from the goal line and a per-call-type boost table, matched against the link text and URL path, in English and Ukrainian); sort by score then today's `_rank`; `est_tokens` per page; `fetch()` accepts `cancel_path`, checked between pages; private-address refusal (section 8). Budget logic unchanged (15, hard cap 30 in code). |
| `__main__.py` | pass the new request fields to `plan` and `fetch` (`webplan`, `webfetch`). |
| `sources.py` | a `web_page` source with `path` is read from the file (access ok, no `fetch`); `parent_uri` kept. |
| `prompt.py` | `agent_prompt`: a stored web page becomes a `Read <path>` line, not `WebFetch`. |
| `build.py` | packed routes: a `web_page` with `path` is not passed to `fetch_page`; (lines near 1130, 1196, 1239, 1263, 1277 key on `fetch`, so clearing `fetch` should be enough; verify). |
| `schema.py` | `parent_uri` optional on a source if missing. |

Swift (app):

| File | Change |
|---|---|
| `LiveTips/SheetSiteResearch.swift` (new) | the plan panel view and its model: run `webplan`, tick pages, run `webfetch`, progress, cancel, the cost line. Shared by Quick and Expert. |
| `LiveTips/MeetingSheetDraft.swift` | state for the site plan per source; `researchSite(sourceID:)`, `readPages()`, `cancelResearch()`; adds the child `web_page` sources, links them to the parent; request fields (`goal`, `call_type`, `audience`); the series remembers the site source. |
| `LiveTips/MeetingSheetPrep.swift` | Expert: the button on the `web` row in `SheetDraftSourceRow`, child rows indentation in "In this sheet"; Quick: the link field and the panel in the Sources summary row. Smallest possible edits, the view is in the new file. |
| `LiveTipsCore/MeetingSheet.swift` | only if the child source needs a new optional field (`parentURI`); `SourceType.webPage` already exists. |
| `app/Resources/i18n/en.json`, `uk.json` | new `sheet.ui.web.*` keys together. |
| `LiveTips/AnalyticsCatalog` (`LiveTipsCore/AnalyticsCatalog.swift`, generated by `tools/gen_analytics_catalog.py`) | only if the owner wants counters. |

Docs and changelog: `docs/en/live-tips.md`, `docs/uk/live-tips.md`, `CHANGELOG.md`.

### P3. Risks

| Area | Risk | Mitigation |
|---|---|---|
| Data | `web_page` with `path` falls into the `fetch` branch and is read twice, with different text per route | criterion 7; test that counts server hits; subtask B first |
| Data | A re-run adds duplicate child sources for the same URL | key child sources by normalized URL; replace on re-read |
| Data | Copies under `live/` sync to Drive and show up in call search | store under a folder excluded like `live/google`, or in Application Support (question 3) |
| Migration | None for sheets: old `web` sources unchanged. A series that remembered a site source keeps working | regression test with an old-style sheet fixture |
| Privacy | Crawler reaches an internal address (SSRF) or follows a redirect to one | refuse private ranges and re-check redirects in `http_get`; test with a fixture redirect |
| Privacy | Contact data in page text | existing scrub plus the Contact skip; keep the fixture test |
| Privacy | The cost line hides that page text goes to the model | the panel footer names the chosen route |
| Performance, 16 GB Mac | Memory is small (each page capped at 2 MB, text only, parsed one at a time). Wall time is the issue: 15 pages at a 1 s pause plus network is about 20 to 60 s | progress and cancel are required; run in the sidecar off the main thread; the Size step must not block; nothing runs during a live call |
| Performance | A very large site makes `plan()` slow on the home page parse | only the start page is parsed at plan time (existing); keep it |
| UX | A slow or hung site | existing per-request timeouts (15 s request, 30 s total); a hung page is reported as error, the rest go on |
| Quality | Goal terms mis-rank pages (Ukrainian and English goals) | deterministic terms, tie-break by `PRIORITY`, the person sees and edits the plan before reading |
| Scope | FB7 plus G6 in one block is larger | FB7 is subtask D, separable; it can slip to the next block without blocking G6 (question 7) |

### P4. Tests

Python (`cd spikes/m0 && python3 -m pytest tests/test_sheet_web_0100.py tests/test_sheet_web_pb37.py -q`, plus
`test_sheet_core_0100.py`, `test_sheet_pipeline_0100.py`, `test_sec01_sheet_0170.py` as regression):

- reuse the Lumenfield local server fixture (`SITE`, `_H`), which already has Mike and Emily as team names and
  contact, policy, login, cart, robots, duplicate, empty, PDF and external-link pages. G0 can share the same site.
- goal ranking: sales intro puts About, Team, Services, Cases first; an interview goal puts Team and Careers first
  (add a `/careers` page to a copy of the fixture); empty goal equals today's order.
- budget: a generated site of 40 pages reads exactly 15, the report says "limit" for the rest.
- single read: after a build on the fake and a packed route, each page was requested once; the Claude agent prompt
  has `Read` lines and no `WebFetch` line for researched pages.
- cancel: a `cancel_path` created mid-run stops after the current page and keeps the read ones.
- private address and redirect refusal; contacts removed; robots disallow; user agent; delay (with injected `sleep`).
- old `web` source without a stored copy behaves as before.
- `tools/check_anonymized.py` and the no-long-dash test on the new files.

Swift and UI: `swift build` (slow; once per subtask C and D), the `LiveTipsCore` tests for any pure helper (cost
line, child source grouping), `app/WebTests` untouched. Screenshots through `SheetShots` for Design review of the
panel in Quick and Expert. No test of the real internet.

Not testable here: the real behaviour on real company sites (JavaScript-only sites, bot protection, slow hosts).
This stays "not verified on real sites" until the owner tries it.

### P5. Split into independent subtasks

Contract first (both sides code to it): the request and response shapes in section 7. Subtasks with their own
files, so developers can work in parallel; B and A are Python only, C and D are Swift only.

| # | Subtask | Files | Depends on | Done when |
|---|---|---|---|---|
| A | Goal-ranked plan, estimates, cancel, private-address refusal | `sheet/web.py`, `sheet/__main__.py`, new `tests/test_sheet_web_pb37.py` | contract | criteria 3, 4, 5, 6, 13 (sidecar side), SSRF tests green |
| B | Stored pages feed the build the same way on every route | `sheet/sources.py`, `sheet/prompt.py`, `sheet/build.py`, `sheet/schema.py`, tests in the same new test file section "routes" | contract | criteria 7, 8, 9, 10 green; regression suites green |
| C | Research this website in Expert Sources (panel, progress, cancel, child rows, cost line, i18n) | new `SheetSiteResearch.swift`, `MeetingSheetDraft.swift`, the `web` row in `MeetingSheetPrep.swift`, `en.json`, `uk.json` | contract (works against a fixture JSON until A and B land) | criteria 1, 2, 11, 12, 13, 14, 15; swift build |
| D | FB7: link field and the panel in Quick | the Quick part of `MeetingSheetPrep.swift` only; `en.json`, `uk.json` (distinct keys from C) | C's panel view | criteria 16, 17; swift build |
| E | Docs and changelog | `docs/en/live-tips.md`, `docs/uk/live-tips.md`, `CHANGELOG.md` (Unreleased) | C and D behaviour | docs EN/UK in step; CHANGELOG entry |

A and B are independent (they only meet at the contract) and can run in parallel with C. C and D both touch
`MeetingSheetPrep.swift` and the i18n files: C takes the Expert part, D the Quick part, and D starts after C's view
file exists, to avoid a merge conflict. E last.

**Does FB7 fit?** Yes, naturally: Quick already tells people to paste a link in the note, `add(pasted:)` is shared
with Expert, and the panel is a shared view. It is a small subtask (D) after C. Recommendation: ship it with G6
(question 7).

Order after the owner answers: A and B and C in parallel, then D, then Design review of the screenshots, then E,
then QA and the gate (`steps_for_diff`: swift build, core tests, check_anonymized).

---

## Questions for the owner

1. **How is the page queue chosen by the call goal?**
   - a) Deterministic: terms from the goal line plus a table per call type, matched on link text and URL path, then
     today's section order (free, instant, same on every route, predictable).
   - b) Semantic with the local bge-m3 model (understands a goal in any language, needs the local model running,
     slower, harder to explain).
   - c) Ask the AI route to rank the links (best fit, but a model call and a cost for every plan, and different per
     route).
   - **Recommendation: a.** The person sees and edits the plan anyway; b can be added later if a is too weak.
2. **Page budget.**
   - a) Fixed 15 (the roadmap number; simple cost story).
   - b) Default 15, adjustable in the plan up to 30 (the code's hard cap).
   - c) Default 15 in Settings > Meeting Sheet for everyone.
   - **Recommendation: b.** Same default, one stepper in the plan panel; the cap keeps the cost bounded.
3. **Where are the page copies stored?**
   - a) `<project>/live/web/` (visible with the call files, readable by the skill, but Drive may sync them).
   - b) Application Support, keyed by project (private to the Mac, not visible in the project folder, the skill
     cannot read them).
   - c) `<project>/live/` but under a hidden `.work` style folder that Drive and search already exclude.
   - **Recommendation: c if that folder rule already exists for call folders; otherwise a**, with a note in the
     docs. Public site text is low sensitivity, but it should not clutter search during calls.
4. **A series: read the site again at each preparation?**
   - a) Remember the site source and reuse the stored pages for 14 days, with a "Read again" button.
   - b) Remember the site, always ask before reading again (the plan panel opens, pages unticked if unchanged).
   - c) Do not remember; the person pastes the link each time.
   - **Recommendation: a.** It matches how Meeting Sheet already remembers series sources and avoids needless crawls.
5. **How is the cost shown?** (The crawl itself is free; the cost is the extra tokens the pages add.)
   - a) Tokens only ("~14K tokens, 11% of the window").
   - b) Dollars where the route has a price (API keys, local is $0), tokens and window share for subscription and
     account routes.
   - c) Percent of the monthly or 5-hour account limit.
   - **Recommendation: b.** It is honest where a price exists and never invents one. Note: Meeting Sheet shows no
     money today (only tokens in Size), so this is new in both Quick and Expert, not "as in Expert".
6. **Cowork skill parity (`live-tips` skill).**
   - a) Now: the skill reads the same stored pages from the sheet, no new skill logic.
   - b) Later, with the evidence graph (G2), when the skill reads the shared evidence snapshot.
   - c) Not needed: the skill has its own web tools.
   - **Recommendation: b.** G6 stores ordinary sources; the skill picks them up when it reads sheets, and nothing
     is lost by waiting.
7. **FB7 (links in Quick) in this block?**
   - a) Yes, as subtask D, shipped together.
   - b) No, G6 in Expert first, FB7 in the next block.
   - c) Quick gets only a read-only hint ("add this link in Expert") for now.
   - **Recommendation: a.** It reuses C's view, it is the smaller part of the work, and it removes the reason people
     skip links.
8. **Sites that need JavaScript (empty pages).**
   - a) Say so plainly and offer the Pacenote browser capture for the pages that matter (existing).
   - b) Also try a headless render (new dependency, heavier on a 16 GB Mac).
   - **Recommendation: a.** It is already built and keeps the app light.
