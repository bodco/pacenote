# PB-37 (G6 Research this website): design review after code

Role: Design. Branch `team/PB-37-g6-integration`. Gate run `gate-int-PB-37-g6`. Product code not changed.

## Verdict

**Not approvable yet: the panel itself is in no screenshot.** Only the entry button is visible. Nothing below about the
panel's look is verified by pixels; items marked "(code)" are read from `SheetSiteResearch.swift` and
`MeetingSheetPrep.swift` and need a screenshot to confirm.

What I opened:

- `app-sheet/expert-3-sources-light.png` (against `baselines/app-sheet/expert-3-sources-light.png`), `-dark.png` and
  `-light-uk.png`.
- `app-sheet/quick-ready-dark.png` and `quick-attention-light.png`.
- The gate wrote diffs for the sheet screens in `app-sheet/diff/`: only `expert-3-sources-dark`, `-light` and
  `-light-uk` (plus two unrelated d3 what's-new UK shots). That matches what I saw: no other sheet screen moved. No
  `app-win/diff/` was needed for this branch (no app-win screen in the diff list).
- Not opened: other app-sheet and app-win files and the tips-window shots (stale, ignored). I did not review them.

Owner rules, checked in the diff against `main` (Swift files): **no `NSAlert`** (0 matches); no red used for actions
(failure text uses the amber `PN.C.pending`, Cancel is a secondary button); buttons are the square `pnSecondary`,
`pnPrimary`, `pnTertiary` styles except the `Stepper` (B3). **i18n parity**: 48 `sheet.ui.web.*` keys in `en.json`
and 48 in `uk.json`; no long dash in them (spec criterion 18).

## What the screenshots show

1. **Expert step 3, light, dark and UK (diff against baseline).** The only change is the new secondary button
   **RESEARCH THIS WEBSITE** / **ДОСЛІДИТИ ЦЕЙ САЙТ** on the `lumenfield.example/about` row, between the ACCESS OK
   label and the x. It is square, outlined, condensed caps: the same style as CONNECT GOOGLE and PASTE THE TEXT in
   all three shots (in dark the outline is white, the amber and green statuses keep their colours). No red. The row
   grew by about 8 px in the 956 px rendering and the rows below shifted down. Expected. In UK the longer label fits
   at the default width with room to spare (about 200 px of free space between the title and the status). Narrow
   windows are still unchecked (B2).
2. **Quick, dark and light "attention".** The Sources row is collapsed in every Quick shot. The link field, the source
   rows and the panel live inside **Change** (code: `MeetingSheetPrep.swift:174-186`) and no screenshot opens it,
   so P5 is code-only. The collapsed row looks as before (summary line cut with "...", FIX and Change), so no
   regression there.
3. The summary line "cases/..., lumenfield.example/about, Emily Parker (fi..." hides child pages (code:
   `parentURI == nil` filter, line 168). Good. It also shows no "N pages read", see P4.

## Blocking

**B1. The panel is not in any screenshot (Expert and Quick).** No shot name has a research state, and `SheetShots.swift`
has no staging for it. `SiteResearchSession.read` also returns early under `SheetShots.active`
(`SheetSiteResearch.swift:52`), so the reading and finished states cannot be reached by clicking in shot mode.
Fix: give the shot harness a way to inject a `SiteResearchSession` in a fixed phase with a fixture `SitePlan` and
`SiteReadReport` (no process, no network), and add the shots listed in "Screenshots needed". Until then Design cannot
approve the panel.

**B2. Narrow window with the longer UK button is unchecked.** At the default width EN and UK are fine (see above).
At the narrowest allowed width, with a long domain as the row title, the row can push the title into truncation
or wrap the button (code: `MeetingSheetPrep.swift:1269`). Needs the narrow shot in EN and UK. If it crowds: move
the button under the title line (as the fix text already sits there), or shorten the label to "Read the site" /
"Читати сайт".

**B3. The system `Stepper` in the plan summary (code: `SheetSiteResearch.swift:215`): confirm in the shot, then fix.**
The spec asks for one stepper (question 2, option b), so the fix is styling, not removal. A SwiftUI `Stepper`
normally draws the rounded macOS control, which would not match the owner's square buttons. I cannot confirm how it
renders in Roadbook/Broadsheet without a screenshot, so this is blocking only if the plan shot shows a rounded
control (likely). Fix: two square `pnSecondary` buttons "-" and "+" (min 28 px hit area, accessibility labels
"Fewer pages" / "More pages") around the counter.

## Polish

**P1. Cost line in mono 11 pt, long in UK (code: line 219).** "N% of one pass of {route}; this route has no per-token
price" and its UK form are sentences, not data. In `homeMono(11)` they wrap to three lines in a narrow window.
Fix: keep "~14K tokens" in mono, put the cost sentence on its own line in `homeText(12)`, `text2`.

**P2. Nested scroll.** The plan list is its own `ScrollView` (max 240 pt) inside the Sources page scroll
(line 190-209). The mouse wheel will stick on the inner list. Fix: show up to about 8 rows, then "and N more" inside a
disclosure, or let the list grow and not scroll; check in the 15-row shot.

**P3. Disabled ticks with no reason.** When the budget is reached, unticked rows are disabled (line 195) and nothing
says why. Fix: a one-line hint under the counter, "Budget reached. Untick a page or raise the number to add another."
Needs a UK string.

**P4. "N pages read" is only in Expert.** In Quick the collapsed Sources row does not tell that pages were read.
Fix: append "· 7 pages of lumenfield.example" to the summary line, or show a count in the row's right side.

**P5. Quick link field placeholder is long** ("Paste a link: Notion, Google Docs, a website, LinkedIn",
line 180) at 14 pt in a 34 pt field; it will be cut in the narrow window and longer still in UK. Fix: "Paste a link" with the
list of kinds in the existing "What can I add?" help; or let the field take the full row width above the ADD button.

**P6. Jargon in the blocked line.** `sheet.ui.web.start.blocked` says "robots.txt does not allow Pacenote". Fix: "The
site asks programs not to read it (robots.txt). Nothing was read. Add the text by hand or skip the site." Same
for `pageState.blocked`.

**P7. Failure colour is correct, keep it.** Errors and blocked text use `PN.C.pending` (the amber/brown seen on NO
ACCESS), Cancel is `pnSecondary`, no red anywhere. Please keep this. Check that the amber text on the panel in
dark theme has 4.5:1 contrast (screenshot needed).

**P8. The panel has no title weight difference from the row above it.** The frame is a 1 px `ruleStrong` outline like
the LinkedIn card, which is good and consistent. Add the same 30 pt indent in Quick as in Expert (it reuses the row, so it
should), and check the left edge lines up with the child rows.

**P9. Copy.** The strings use three ASCII dots ("Reading the home page...", "Stopping..."), which fits the rule of no
long dash; keep ASCII dots and do not switch to the "…" character. "Read 1 page" and "Read N pages" are fine.
Change "Stopping..." to "Stopping after this page..." so the person knows that pages already read are kept;
"Stopped: pages already read are kept." already exists, good.

**P10. Accessibility.** Page toggles are labelled with the path only; add the state ("About, planned, about 900
tokens"). Spinner has no label. The plan list should announce "Plan: 12 pages" when it appears
(`.accessibilityElement` + a live-region post). Not blocking.

## Screenshots needed (light and dark, EN; UK for the starred ones)

Expert step 3, window at default width unless said:

1. **plan**: site with 12 planned, 2 skipped (sign-in, policy), 1 over limit, counter, cost line, "Read 12 pages" (all
   three cost kinds once: dollars, free, window), footer "sent to {route}". Light and dark. *UK*.
2. **reading**: "Reading page 4 of 12: /team", Cancel enabled.
3. **cancelling** ("Stopping...") and **cancelled with pages kept** ("Stopped: pages already read are kept").
4. **finished**: "7 pages read, about 6K tokens", the counts line (empty, blocked, duplicate), child rows under the
   site row in "In this sheet" with "7 pages read". *UK*.
5. **blocked site** (robots) and **site unreachable**, each with its one-line explanation and Close.
6. **JavaScript-only** (0 read, empty pages): the `js` message.
7. **plan failed** with Retry.
8. **long site name**: `a-very-long-company-name-and-division.example.co.uk` in the title, the row and the progress
   line (middle truncation of the path).
9. **narrow window** (the smallest allowed): the source row with the button, the plan, the cost line. *UK*.
10. **budget reached**: 15 of 15, disabled rows, hint (once P3 is done).

Quick, with Change open: the link field, the source rows, the panel in **plan** and **finished**; the collapsed row
after reading. Light and dark, *UK* for plan.

## Approve baseline?

Not now. After B1 shots arrive and B2/B3 are checked, propose approving the new `expert-3-sources-*` baselines (button
added; the +8 px row growth is expected). That decision is Bohdan's: put it in Questions for client.
