# PB-37 (G6 Research this website): design review after code, pass 2

Role: Design. Branch `team/PB-37-g6-integration`. Gate run `gate-wave16-2026-11-22-PB-37-dev`. Product code not changed.
Pass 1 (B1 panel not in shots, B2 narrow UK, B3 Stepper) is superseded by this file.

## Verdict

**Expert Sources panel: approvable after two small fixes (B1, B2 below). Quick panel: not approvable, still in no screenshot (B3).**

The harness now stages the Expert panel in 19 states x light/dark x EN/UK (`app-sheet/web-*`). I opened these (pixels, not code):
`web-plan-usd` (light, dark), `web-reading` (light), `web-finished` (light), `web-budget-reached` (light),
`web-blocked` (light), `web-unreachable` (dark), `web-cancelled-kept` (dark), `web-js-only` (dark),
`web-long-name` (dark), `web-narrow-dark`, `web-narrow-long-name` (dark, light-uk), `web-plan-failed` (light-uk),
`web-quick-plan` (light-uk), `web-quick-finished` (dark). Not opened (same layout as the siblings I did open):
`web-plan-free/window`, `web-cancelling`, the remaining dark/UK variants. Tips-window shots ignored as instructed.

Pass 1 items now closed by pixels:

- **Stepper (old B3): fixed.** It is two square outlined "-" and "+" buttons around "12 of 15 pages", the same style as
  CHOOSE FILES. Disabled state (reading, budget) is greyed, readable in both themes.
- **No red anywhere.** Failure text is amber/brown on light and orange on dark (blocked, unreachable, JS-only, plan
  failed). Cancel is an outlined square button; READ N PAGES is the yellow primary. Contrast looks fine in both themes
  (not measured with a tool).
- **P3 hint** "Budget reached. Untick a page or raise the number to add another." is present (`web-budget-reached`).
- **P2 nested scroll:** the plan list is not a scroll box any more; all 15 to 21 rows show and the page scrolls. Good.
- **Long site name and long path** (`web-long-name`): title and progress line fit at default width, no clipping. OK.
- **No NSAlert** (0 matches in the diff), 48 `sheet.ui.web.*` keys in en and uk (checked in pass 1).

## Blocking

**B1. Finished state keeps the plan controls live and leaves failed pages ticked (`web-finished-light`, `-dark`,
`web-cancelled-kept-dark`, `web-js-only-dark`).** After reading, the panel still shows the stepper (active), the
"12 of 15 pages" counter, the ~19K cost line and the "12 will be read" line, then the result line "7 pages read, about
6.0K tokens" below them. The numbers contradict each other (12 of 15 / ~19K vs 7 pages / 6.0K; in `web-js-only` "7 of
15 pages" and "~0 tokens" next to "0 pages read"). Pages that failed (blocked, empty, 503, duplicate) keep a yellow
ticked box, which reads as success.
Fix: in the finished/cancelled phases hide the stepper, the cost line and the "N will be read" line (the plan is
spent); show only the result line, the counts line and READ AGAIN / Close. READ AGAIN brings the plan controls back.
Replace the ticked box on a failed row by an empty box or a small "x" mark in `text3`, keep the reason line.

**B2. UK failure text leaks English (`web-plan-failed-light-uk`).** "Не вдалося спланувати цей сайт: the site did not
answer in 30 s". The detail comes from the engine untranslated. Fix: map the engine error to a localized key
(`sheet.ui.web.err.timeout` and the like, EN and UK) and never print the raw engine string. Same check for the HTTP
reason in unreachable and for per-page reasons in UK finished ("blocked by robots.txt", "duplicate (same text as
/about)", "could not be read (HTTP 503)" were fine in EN; I did not open UK finished, so check it).

**B3. Quick panel is still in no screenshot.** `web-quick-plan-*`, `web-quick-finished-*` and `web-quick-collapsed-*` all
show the same collapsed Quick screen (Sources row "cases/..., lumenfield.example/about, Emily Parker (fi..." with
Change). The plan and finished panels inside Change are never rendered, so the link field placeholder (old P5), the
indent of the panel under the row and the "N pages" summary (old P4) are unverified. Fix: the harness must open the
Sources row (set the expanded state) before capture for the three `web-quick-*` shots, light/dark, EN/UK.
Until these exist Design cannot approve Quick.

## Polish

**P1. The "narrow" shots are not narrow.** `web-narrow-*` are 1440 px wide (600 pt) against 1720 px (717 pt) for the
default, about 17% less. Pass 1 asked for the smallest allowed window. At 600 pt: the panel itself holds up (rows,
cost line, READ 12 PAGES, stepper all fit; the long UK label "ПРОЧИТАТИ СТОРІНОК: 12" fits). Things that
break are older and outside the panel: step tabs truncate ("З...", "Щ...", "Д...", "Пе..."), the footer "ДАЛІ:
ПЕРЕВІР..." is cut, the Paste field placeholder is cut in UK ("Посилання, листування, нот"). Not caused by PB-37, so not
blocking, but add one shot at the real minimum width (the window's `minWidth`) to confirm the panel there.

**P2. Error-state frames shrink-wrap, the plan frame is full width.** Blocked light ends at about 573 px, unreachable
at 426, plan failed UK at 374, plan/finished at 696. The panel's width jumps between states. Fix: give the panel a
fixed `maxWidth: .infinity` frame in every phase.

**P3. Blocked copy still has jargon (old P6).** `web-blocked-light`: "robots.txt does not allow Pacenote to read this
site, so nothing was read. Add the text by hand or skip the site." Suggested: "The site asks programs not to read it
(robots.txt), so nothing was read. Add the text by hand or skip the site." Same shorter form for the per-page
"blocked by robots.txt (robots.txt)" in `web-finished-light`, which repeats the word ("blocked by robots.txt
(robots.txt)"): drop the parenthesis.

**P4. Route name grammar.** "9% of one pass of Claude account; this route has no price per token" (EN) reads like a
placeholder was filled with the account name; UK: "9% одного проходу Акаунт Claude" is wrong case (capital A,
nominative after "одного проходу"). Fix: "of one pass on your Claude account" / "одного проходу через ваш акаунт Claude".
Same for "Page text goes to Claude account..." -> "goes to your Claude account".

**P5. UK token format inconsistent.** In the row subtitle UK shows "~токенів: 900" while the plan rows show "~1.0K
токенів". Both formats in one panel. Pick one ("~900 токенів"). Likely an existing string, check whether PB-37 added it.

**P6. Cost line typography (old P1).** The cost sentence is already in the proportional font (not mono) and wraps to
two lines at 600 pt, fine. Nothing to do.

**P7. Spinner is a still glyph in the shots** (`web-reading-*`: a small icon left of "Reading page 4 of 12"). This is the
agreed static staging, but it looks like a person icon. Fine for the harness; in the live app confirm it is the standard
spinner.

**P8. Accessibility (old P10), not blocking:** tick boxes are labelled by path only; add the state ("planned, about
900 tokens"); give the spinner and the stepper buttons labels ("Fewer pages" / "More pages").

**P9. Copy "Stopping..." (old P9)**: still no "after this page"; I did not open `web-cancelling`. Check and change if
unchanged.

## Remaining screenshots

Add or fix: (1) the three Quick shots with Sources open (B3); (2) a real-minimum-width Expert shot, EN and UK (P1);
(3) re-shoot finished, cancelled, JS-only and plan-failed UK after B1 and B2.

## Approve baseline?

After B1, B2, B3 are fixed and re-shot, I propose approving the new `expert-3-sources-*` baselines (the RESEARCH THIS
WEBSITE button on the site row, row grows by about 8 px; light, dark, UK) and adding the `web-*` shots as new baselines.
Both are Bohdan's decision, to be put in Questions for client. The tips-window baselines are stale and out of scope.

## Handoff

Task PB-37, role Design. Verdict: Expert panel approvable after B1 and B2; Quick not approvable until B3. If B1/B2 are
fixed and the Quick shots exist, a short third look (finished, plan-failed UK, three Quick shots) is enough.
