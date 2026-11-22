# PB-49 + PB-33: design review after code

Role: Design, step "after code". Branch `team/PB-49-transcript-button-language`, commit `1dcd99b`.
Compared against `docs/design/PB-49-strip-transcript-language.md` (owner accepted Q1–Q8 as recommended).
Screens read: `private/team/shots/gate-wave18-2026-11-22-PB-49-dev/tips/` (`live-wide`, `live-narrow`, `live-dark`,
`live-uk`, `ready`, `empty`, `presentation`, `compact`, `onme-wide`). Code read: the diff of `1dcd99b` in
`tips.css`, `tips.js`, `index.html`, `Prompter.swift`. Baselines were not used (L34); every screen is judged on its own.
Nothing was run or launched (L33).

## Verdict

**Not approvable yet.** Two blocking items (B1, B2) are about states the gate did not show and one state it did show
(Ready) that breaks the spec. The part that was rendered is good: it matches the doc and the owner rules. Fix B1 and B2,
re-run the gate with the extra shots below, and this should be approvable with the polish items as follow-ups.

## What matches (rendered, OK)

- Transcript icon: speech bubble with three lines, 16 px, same stroke as mic/pause/bulb, icon only, left of On me
  (`live-wide`, `live-narrow`, `live-uk`). It does not depend on the On me count.
- Wide, panel on Transcript (`live-wide`, `live-dark`): the button is pressed. In Roadbook it is an amber underline,
  no red, square. With the panel on On me (`onme-wide`) the button is not pressed and On me is. Correct.
- Narrow 600 px (`live-narrow`, `empty`): two deliberate rows. Row 1 status, End call, Home, ⋯. Row 2 Transcript, On me
  on the left, mic, pause, bulb, chip, record on the right. Nothing hidden, nothing orphaned. Row 2 adds about 42 px,
  close to the 36 px estimate.
- Chip: ISO `EN` (not `EN`/`УК` mix), 12 px bold, square-ish, small ▾, ink text, not red (`live-uk` keeps `EN` in the UK
  interface, as designed).
- UK interface (`live-uk`): `НА МЕНІ`, `ЗАВЕРШИТИ`, `ТРАНСКРИПТ` fit; no overflow in the strip at the wide width.
- Dark theme: same tokens, no contrast problem seen.
- Owner rules: square buttons yes, no NSAlert (toast only, in code), no red for actions (End call keeps its existing
  red outline, unchanged), US example names yes.

## Missing shots (the gate did not render these) and why

| State asked | In the shots? | Why it is missing |
|---|---|---|
| Wide + Transcript pressed | **Yes** (`live-wide`, `live-dark`, `live-uk`, `ready`, `presentation`) | – |
| Narrow, two rows | **Yes** (`live-narrow`, `empty`) | – |
| Language menu open | **No** | No scenario for it. The page already supports `?lang_menu=open`, `?pending=1`, `?call_lang=uk+en`, `?mode=card`, `?pane=off` (added in `tips.js`), but the gate's shot list was not extended to use them. |
| Compact card | **No** | `compact.png` is a 600 × 400 window with the two-row strip, not `body.card` (`.tc-bar`). The card's Transcript icon was never rendered. Needs `?mode=card`. |
| Mixed chip `UK+EN`, `UK+2` | **No** | Same: needs `?call_lang=uk+en`. |
| Pending chip (amber + dotted code) | **No** | Needs `?pending=1`. |
| Narrow with transcript open (sheet or grown window) | **No** | Not in the list. |
| Widths 480 and 760 (and 459 / 400 breakpoints) | **No** | Only 600 and the wide width were rendered. The spec asked for 480 and 760. |
| Ready with empty pane text | **No** | `ready.png` shows a full transcript (fixture), not the empty-pane copy. |
| Presentation, narrow split | **No** | Only the wide presentation was rendered. |

Also note that `speakers-open` and `diff/*` were not needed for this review.

## Blocking

**B1. The language chip shows in Ready (and will in Ended).** Screen: `ready.png` (chip `EN ▾` next to a "Ready" strip
with no recorder running). Spec section 4.1/5: hidden in Ready and Ended. Code: `renderLangSwitch` hides only when there
is no main language (`b.hidden = !l.main`), with no call-state check. Failure: a PM opens the menu before listening, picks
Ukrainian, the setting goes nowhere, the chip shows the amber pending state for 4 s, then the "Could not change the
language" toast. Fix: also hide when `S.call.state` is `ready` or `ended`, and close the menu in those states.
Add a gate shot for Ready and one for Ended with the chip absent.

**B2. Four spec states have no evidence, and one is a real risk: the compact card.** The card's Transcript icon, the
open menu (focus ring, selected row ◉, footnote, clipping at 480 px: the menu is positioned from the chip with
`left` clamped to the window, `min-width:230px`), the mixed chip and the pending state were never rendered, so Design
cannot approve them. Fix: add gate scenarios for `?lang_menu=open` at 480 and 600, `?call_lang=uk+en` (menu with the Tips-in
row), `?pending=1`, `?mode=card`, and re-run. Any layout bug found in those goes back to Dev.

## Polish (not blocking)

**P1. Height at the default window.** Screen `compact.png` (600 × 400): with the second row, the hero card is cut by the
"Team retro" banner and the hint toast covers the banner text. The default window is 520 × 420, so the feed has about 42 px
less than before. The overlap of the toast is not new, but the clipped answer is more likely now. Fix: Dev/Tech lead check
that the feed area scrolls under the banner at 420 px (no content lost), or grow the default window height by the 42 px of
row 2 in `Prompter.swift:1214`. Recommend the window height change (420 → 460).

**P2. Pressed state in narrow.** In narrow with the grown panel or sheet, `transcriptShown()` marks the button pressed;
confirm in a shot (`?mode=…` with the transcript open) that the amber underline is visible on row 2 and not clipped by the
row border.

**P3. Icon size next to On me.** The 16 px bubble looks light beside the 32 px On me chip (`live-narrow`). Acceptable; if the
owner finds it hard to hit, raise the glyph to 18 px, keep the 28 px target. No change needed now.

**P4. Tab order and focus.** Code adds `:focus-visible` rings and arrow-key movement in the menu. After the
row move to two rows, check with VoiceOver that tab order still follows the visual order: DOM order is status, Transcript,
On me, tools, chip, record, End call, Home, ⋯, while visually End call, Home, ⋯ come first on narrow (CSS `order`). That is
a visual/DOM mismatch (WCAG 2.4.3). Fix: either accept it (document it) or move End call/Home/⋯ earlier in the DOM; a
low-risk fix is `order` only on narrow plus a note. Recommend accepting, because End call first on Tab is also not
helpful during a call. Mark as known.

## Dev's open questions

**1. Row 2 alignment: `margin-left:auto` on mic/pause/bulb/chip/record vs the sketch grouping them left.**
Keep the dev's right alignment and change the doc sketch. Reasons (from `live-narrow`): (a) views on the left, call controls
on the right is the same split the wide layout has, so the two layouts read alike; (b) a left-packed row 2 leaves a large
empty area on the right at 520–759 px and the controls move when On me appears or disappears; (c) the right edge keeps
mic/pause under End call, which is the PM's call-control corner. Approved as built. Update the sketch in section 3.3.

**2. Cmd+Shift+T while typing in the ask field.** Let it work. It is a modifier chord, not text input, so it never
conflicts with typing; the app's global key handler (`tips.js:1973`) fires, calls `preventDefault`, and toggles. The ask
input is a static element (not re-rendered), so the draft text and caret survive. Requirements: (a) do not move focus away
from the ask field, so after the toggle the PM keeps typing (check that `openTab` does not call `focus()` elsewhere);
(b) ignore the chord during IME composition (`e.isComposing`); (c) also work from the Transcript pane. Add one test for
(a). Do not add a "disabled while typing" rule: a PM who is typing a question and wants to check what was just said is
exactly the case for the shortcut.

**3. Should tips language follow the main language after a single-language switch?** Yes, as built (`pickLang`
posts `tips_lang` for a one-language pick), with one change: tell the user. Today the toast says only
"Call language: English. Applies from the next phrase.", but the tips language also changed, which is a second effect. Fix:
when the tips language changes with the pick, the toast says "Call and tips language: English. Applies from the next
phrase." (UK: "Мова дзвінка і підказок: англійська. Діє з наступної фрази."). In a mixed call the tips language stays as
chosen in the Tips-in row (as built). This matches section 2 of the design doc ("tips follow the main language").

## Checklist for the re-run

1. Add shots: Ready (no chip), Ended (no chip), menu open at 480 and 600, mixed chip with Tips-in row, pending chip,
   `body.card` with the Transcript icon, narrow with the transcript open, widths 480 and 760, Ready empty-pane copy.
2. Confirm B1 fixed in the Ready/Ended shots.
3. Design re-reads the new shots; if they show only the expected change, propose the baseline approval to the owner
   (the old `tips/` baselines change in every shot with a strip; owner's decision under Questions for client).
