# PB-37 (G6 Research this website): design review after code, pass 3

Role: Design. Branch `team/PB-37-g6-integration`. Gate run `gate-wave19-2026-11-22-PB-37-dev` (newest for this task). Product code not changed.
Supersedes pass 2. Owner UI rules checked: square buttons, no NSAlert, no red for actions, US example names only.

## Verdict

**PASS.** B1, B2 and B3 from pass 2 are fixed in pixels. What is left is polish, none of it blocking.

Opened (pixels, not code): `web-finished-light`, `web-finished-light-uk`, `web-cancelled-kept-light`, `web-js-only-dark`,
`web-plan-failed-light-uk`, `web-quick-plan-light`, `web-quick-finished-dark`, `web-quick-finished-light-uk`.
Not reopened this pass (unchanged in pass 2, no known change): blocked, unreachable, long-name, narrow, budget-reached,
cancelling. Tips-window shots ignored as instructed.

## Pass 2 blockers

- **B1 finished/cancelled states: fixed.** In `web-finished-*` the stepper, the cost line and "N will be read" are gone.
  The panel shows the result line ("7 pages read, about 6.0K tokens."), the counts line ("2 empty · 1 blocked by
  robots.txt · 1 could not be read · 1 duplicates"), READ AGAIN (square outlined) and Close. Failed pages have a small
  grey "x" instead of a yellow tick, with the reason kept. Skipped pages (login, privacy, over the page limit) stay as
  empty boxes. The numbers now agree (7 read = 7 ticked rows = 7 added "Lumenfield - /..." sources). `web-js-only-dark`:
  "0 pages read", all rows "x / empty page", one amber explanation, no contradictory counter. Good.
  `web-cancelled-kept-light`: "3 pages read, about 2.6K tokens. Stopped: the pages already read are kept." Consistent
  with the 3 added sources, plan controls hidden. See P1 for one leftover.
- **B2 UK failure text: fixed.** `web-plan-failed-light-uk`: "Не вдалося спланувати цей сайт: він не відповів вчасно.
  Спробуйте ще раз або додайте текст вручну." No English left. Amber text, no red; button "СПРОБУВАТИ ЩЕ РАЗ" is square and
  outlined. UK finished is fully localized too ("прочитано", "порожня сторінка", "заблоковано robots.txt", "дублікат
  (той самий текст, що на /about)", "не вдалося прочитати (HTTP 503)", "понад ліміт сторінок").
- **B3 Quick panel in shots: fixed.** `web-quick-plan`, `web-quick-finished` (light, dark, UK) now show the Sources row
  open ("Done" on the right), the panel indented under the site row at the same left edge as the other source rows,
  and the paste field with the placeholder "Paste a link" / "Вставте посилання" (old P5 closed). Finished state in
  Quick matches the Expert one (result line, counts, READ AGAIN, Close; "7 pages read" under the site row, old P4
  closed). Dark theme contrast is fine; the primary button stays yellow, "Cancel" is a text link, not red.

## Polish (non-blocking)

**P1. Cancelled state: unread pages keep a dimmed tick (`web-cancelled-kept-light`).** Only 3 pages were read, yet rows
4 to 12 (/case-studies ... /careers) still show a dimmed ticked box with a token estimate and no status line, while the
first three say "read". It is clear from the result line, but a skimmer reads 12 ticks as 12 read. Fix: show unread
planned rows in the cancelled state with an empty box and the line "not read (stopped)". Same family as old B1, hence
worth doing in the next pass.

**P2. Panel width still jumps between states.** `web-plan-failed-light-uk` frame ends at about 580 of 680 px, finished/
plan fill the width. Give the panel a `maxWidth: .infinity` frame in every phase (carried over from pass 2 P2; applies to
blocked and unreachable too, not re-checked).

**P3. Route wording (carried over).** Quick plan: "Claude account, one pass: 9% of its window; this route has no price
per token". Better: "Your Claude account, one pass: about 9% of its window; no price per token on this route." Footer:
"goes to Claude account" -> "goes to your Claude account". Check the UK counterpart ("Акаунт Claude" capitalised in
mid-sentence in pass 2).

**P4. Redundant row subtitles in the plan.** Quick plan rows repeat the path word ("/team" / "team", "/customers" /
"customers"). Show a reason only when it adds something (sign-in page, policy page, over the page limit).

**P5. Narrow width (carried over).** Pass 2 found that `web-narrow-*` are 600 pt, not the window minimum. Still worth one
shot at the true `minWidth`, EN and UK. Truncated step tabs and cut footer text there predate PB-37.

**P6. UK Quick shot leaks sample text** ("Intro with Emily Parker", "Goal: understand Lumenfield's...") which is fixture
data, not UI copy. Ignore. The label "НАЛАШТУВА/ННЯ" wraps in the Setup column in UK: existing, not PB-37.

**P7. Accessibility, copy (carried over, not re-checked in pixels).** Tick boxes labelled by path only; spinner and
stepper buttons need labels ("Fewer pages" / "More pages"); "Stopping..." could say "after this page".

## Baselines

I propose approving the new `expert-3-sources-*` baselines (the RESEARCH THIS WEBSITE button on the site row, row about
8 px taller; light, dark, UK) and adding the `web-*` shots, including the three Quick states, as new baselines. Both are
Bohdan's decision, for Questions for client. Tips-window baselines are stale and out of scope.

## Handoff

Task PB-37, role Design. Pass 3: B1, B2, B3 closed; polish P1-P7 may go to a follow-up task. Next: Questions for client
(baseline approval), then QA.

Verdict: PASS
