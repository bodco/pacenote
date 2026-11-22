# PB-49 + PB-33: design review after code

Role: Design, step "after code". Branch `team/PB-49-transcript-button-language`, HEAD `d106858` (on top of `1dcd99b`).
Compared against `docs/design/PB-49-strip-transcript-language.md` (owner accepted Q1–Q8 as recommended).
Screens read (newest gate for this task): `private/team/shots/gate-dispatch-2026-11-22-PB-49-r1-dev-0711/tips/`
(`live-narrow`, `compact`, `ready`). Also read from the earlier gate `gate-wave18-2026-11-22-PB-49-dev/tips/`:
`live-wide`, `live-narrow`, `compact`, `ready`. Code read: `tips.js` (`renderLangSwitch`, `renderLangMenu`, `pickLang`,
`tipsLangSet`, key handler at 1985), `tips.css`. Baselines were not used (L34): `compare.json` reports every shot as
changed, which is expected and says nothing about quality. Nothing was run or launched (L33).

## Verdict

**CHANGES.** What the gate rendered is good and matches the doc. The earlier blocker B1 (chip in Ready) is fixed in the
new gate. The remaining blocker is evidence: the new language menu, the mixed and pending chip, the compact card and the
sheet fallback have still never been rendered, so Design cannot approve them.

## What matches (rendered, OK)

- Transcript icon: speech bubble with lines, same stroke as mic/pause/bulb, icon only, left of On me, shown even with
  On me count 0 as designed (`live-wide`, `live-narrow`).
- Wide, panel on Transcript (`live-wide`, `ready`): the button is pressed (amber underline, square, no red). Not pressed
  in the narrow shots, where no panel is open. Correct.
- Narrow 600 px (`live-narrow`, `compact`): two deliberate rows. Row 1: status, End call, Home, ⋯. Row 2: Transcript,
  On me left; mic, pause, bulb, `EN ▾`, record right. Nothing hidden or orphaned.
- Chip: ISO `EN`, 12 px, square, ink text, small ▾, not red.
- **B1 fixed:** `ready.png` (new gate) has no chip; the old gate showed `EN ▾` in Ready. The Transcript button stays.
- Owner rules: square buttons yes; no NSAlert (toast only); no red for actions (End call keeps its existing red
  outline, unchanged); US example names (Emily, David) yes.

## Requested states: in the shots or not

| State | In the shots? | Why not |
|---|---|---|
| Wide + Transcript pressed | Yes (`live-wide`, `ready`, `live-dark`, `live-uk`, `presentation`) | – |
| Narrow, two rows | Yes (`live-narrow`, `empty`, `compact`) | – |
| Language menu open | **No** | Gate has no scenario. The page supports `?lang_menu=open`, `?pending=1`, `?call_lang=uk+en`, `?mode=card`, `?pane=off`; the gate shot list never calls them. |
| Compact card (`body.card`) | **No** | `compact.png` is a 600 × 400 window with the two-row strip, not the card. Needs `?mode=card`. |
| Mixed chip `UK+EN`, `UK+2`, Tips-in row | **No** | Needs `?call_lang=uk+en`. |
| Pending chip (amber, dotted code) | **No** | Needs `?pending=1`. |
| Ended (chip absent) | **No** | No Ended scenario. |
| Narrow, transcript open (grown window or sheet fallback) | **No** | Not in the shot list. The sheet fallback is new in `d106858`. |
| Widths 480 and 760 | **No** | Only 600 and the wide width are rendered. |
| Ready narrow, empty-pane copy | **No** | `ready.png` is wide with a fixture transcript. |
| Presentation, narrow split | **No** | Only wide presentation. |

## Blocking

**B2. Evidence for the new UI is missing.** The open menu (focus ring, ◉ row, footnote, clipping at 480 px: `left` is
clamped to the window and `min-width` is 230 px, `tips.js:1169`), the mixed chip with Tips-in row, the pending state, the
card's Transcript icon, Ended with no chip, and the sheet fallback were never rendered. Fix: add gate scenarios for
`?lang_menu=open` at 480 and 600, `?call_lang=uk+en` with the menu open, `?pending=1`, `?mode=card`, Ended, narrow with
the transcript open (sheet), Ready narrow. Re-run; any layout bug goes back to Dev. This is the only blocker.

## Polish (not blocking)

- **P1. Height at 400 px** (`compact.png`, both gates): row 2 costs about 42 px, so the hero answer is cut by the
  "Team retro" banner and the hint toast covers the banner text. Dev/Tech lead: confirm the feed scrolls (no content
  lost) or raise the default window height 420 → 460 (`Prompter.swift:1214`). Recommended: raise it.
- **P2. Pressed state in narrow:** once the shot exists, confirm the amber underline is not clipped by the row border.
- **P3. Icon weight:** the 16 px bubble looks light next to the On me chip; acceptable, 18 px glyph if the owner asks.
- **P4. Tab order vs visual order on narrow:** End call, Home, ⋯ come first visually but last in the DOM (CSS `order`).
  Accept and document: End call first on Tab is not helpful during a call.

## Dev's open questions

**1. Row 2 alignment (`margin-left:auto` vs sketch).** Keep the dev's right alignment and fix the sketch in 3.3. Views
on the left and call controls on the right is the same split as wide; a left-packed row leaves a hole on the right at
520–759 px and shifts when On me appears; mic/pause stay under End call. Approved as built (`live-narrow`).

**2. Cmd+Shift+T while typing in the ask field.** Let it work. It is a modifier chord, not text; the handler
(`tips.js:1985`) sits after the ask-field Enter branch and is not gated on the target, so it fires. Conditions: (a) the
toggle must not move focus out of the ask field, so the PM keeps typing (add one test); (b) ignore it when
`e.isComposing`; (c) it also works from the Transcript pane. Do not add a "disabled while typing" rule: checking what was
just said while composing a question is the reason for the shortcut.

**3. Tips language after a single-language switch.** Yes, follow the main language, as built (`tips.js:1184`), but say
so. Today the toast `cl.ok` names only the call language. When the tips language also changes, the toast should read
"Call and tips language: English. Applies from the next phrase." (UK: "Мова дзвінка і підказок: англійська. Діє з
наступної фрази."). In a mixed call the tips language stays as chosen in the Tips-in row. Non-blocking copy fix.

## Re-run checklist

1. Add the shots listed under B2 (plus 480 and 760 widths).
2. Design re-reads them. If they show only the expected change, propose baseline approval for the `tips/` baselines to
   the owner (every strip shot changes; decision goes to Questions for client).

Verdict: CHANGES
Blocking: B2 (no rendered evidence for the language menu, mixed and pending chip, compact card, Ended, narrow
transcript/sheet, 480 and 760 widths).
