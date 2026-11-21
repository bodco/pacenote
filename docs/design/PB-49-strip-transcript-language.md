# PB-49 + PB-33: Transcript button and call language in the tips window strip

Role: Design, step "before code". Status: mockup for the owner's approval (Needs client). No product code in this step.
Date: 2026-11-21. Branch: `team/PB-49-transcript-button-language` (on top of PB-32 S5).

"The strip" below is the header bar of the tips window (`header.bar` in `app/Resources/tips/index.html`).
Today, left to right: record dot, timer, status word, level bars, AI status | spacer | Deal chip, **On me** |
mic, pause, bulb | recording control | End call, Home, ⋯ (More). The S5 tips-language chip (`#langBtn`) is
inserted by JS before ⋯ (`tips.js:1095`).

Sources read: `index.html`, `tips.js` (render at 440–510, `renderPane` 1249, `renderLangSwitch` 1092,
`openTab/closePane` 1556, click handlers 1803, 1890), `tips.css` (strip, `@container` rules at 262–283, styles
at 689–790), `Prompter.swift` (`setTipsLang` push, line 209), `CallEngine.swift` (`tipsLang/tipsLangs`),
`CallSettings.swift` (native language picker that already sends `call_lang`), `recorder/src/transcriber.py`
(`call_lang` handler, line 5350), `rules/meeting.yaml`, `LiveTipsCore/CallLanguage.swift`, and the stale
baselines `live-wide`, `live-narrow`, `compact`.

Also read: `design/pacenote-0.8/REDESIGN-SPEC.md` and the baselines `live-dark`, `presentation`, `onme-wide`.
The baselines show the Roadbook look: outlined square-cornered chips (On me amber, End call red outline),
condensed uppercase display type, unbordered icon tools; dark uses the same tokens. Checks taken from the
spec: every icon-only button has a VoiceOver label (checklist, line 420), and text is at least 12 px (F1).
Because of the 12 px rule the language chip is 12 px, not the 11 px of today's `.langbtn`.

Not checked: the project memory with the owner's UI decisions could not be listed from this session's sandbox
(the folder is outside the allowed directory). I applied the rules named in the Design role brief (square
buttons in Roadbook/Broadsheet, "not ready" amber for attention, no red for actions, no NSAlert). Anything in
that memory overrides this doc; the owner should say so if a choice here conflicts. The pixel widths below are
estimates from the CSS, not measured; the appshots gate should confirm them.

Window size: the tips window opens at **520 × 420 px** (`Prompter.swift:1214`), so the default is the narrow
layout (below 760 px, no side panel). The 520 px case is the one to get right; the wide layout is what a user
sees only after resizing.

---

## 1. Why "On me" is missing for the beta tester

1. **It is hidden by design when its count is 0.** `tips.js:474`: `$('onmeBtn').hidden = openOnMe === 0`.
   `openOnMeCount()` (`tips.js:1348`) counts open lines of this call, pending "mine" lines, open register items
   of earlier calls (not "theirs") and open Notion tasks. A new project, a first call, or a call before the first
   commitment is spoken has 0 items, so the chip does not exist. It appears only when the first item arrives.
2. **There is no other visible way to the transcript.**
   - Wide window (inner width of 760 px or more): the side panel opens on Transcript by default
     (`paneTab: 'transcript'`). After the panel is closed with ✕ (`paneOff`), only On me (if shown) or
     ⋯ > Transcript brings it back.
   - Narrow window (below 760 px; the default window is 520 px wide, so this is the normal case): there is **no** panel. The only way
     in is the On me chip or ⋯ > Transcript (`tips.js:1371`). With On me hidden, the transcript sits behind an
     unlabelled three-dot icon.
   - Compact card (`body.card`): no On me, no panel; only Expand.
3. Not a bug in the data: the same hiding applies to the Deal chip (`dealBtn`, hidden without a chip). The
   result is a strip whose buttons come and go with call content.

The Transcript button must therefore not depend on counts or content: **always visible**, in every call state.

---

## 2. What the language control must do (the existing logic, so the UI does not contradict it)

There are two different languages. The UI must not blur them.

| | Call language (new, FB3) | Tips language (S5, exists) |
|---|---|---|
| Meaning | What the recorder expects and transcribes | What the tips and cards are written in |
| Setting | `call_lang` = `uk`, `en`, `uk+en`, or any Whisper set of up to 5, first = main | `tips_lang` = `uk` or `en` |
| Handler | `transcriber.py:5350`. Applies live, no restart: sets `whisper.langs`, `me_lang`, the prompt glossary, and writes a transcript marker line `language: uk+en` | `_tips_lang` in `live.py:122` |
| Echo | status fields `call_lang`, `me_lang`, `langs` | status `tips_lang` |
| Today's UI | only the native Call settings popover (⚙ > Language) | `#langBtn`, shown only for a mixed call (S5, owner answer 3A) |

Consequences for the design:
- FB3 cannot reuse the S5 rule "show only when the call is mixed". Switching a one-language call to another
  language is the point of FB3. The control is shown in every live call.
- The tips language today comes from `callRequest` (`CallEngine.tipsLang/tipsLangs`) and is pushed once, when the
  transcript file appears. After a mid-call switch that source is stale. Engineering must derive "main language"
  and the "mixed" flag from the recorder's status echo (`langs`, `call_lang`). The rule stays: tips follow the main
  language (`me_lang`), and a mixed call may override the tips language.
- After the call, FB1/FB2 resolve the output language as: explicit PM choice > dominant spoken language
  (from the per-line language tags) > Start call. Transcript lines carry the language tag, so a mid-call switch
  feeds the dominant-language calculation correctly with no extra work. The mid-call switch is **not** an explicit
  output-language choice (see Q5).

---

## 3. Transcript button

### 3.1 Icon

Inline SVG, 16×16, stroke 1.4, `currentColor`: the same construction as `MIC_ON`, `BULB`, `PAUSE`. The strip is
HTML, so SF Symbols are not rendered. The nearest SF Symbol is named only as the drawing reference for the
native side.

| Option | Drawing | SF reference | Risk |
|---|---|---|---|
| **A (recommended)** | Speech bubble with three text lines, the third shorter | `text.bubble` | None found. It reads as "what was said", distinct from the bulb (tips) and mic. |
| B | Four text lines, first and third shorter, no bubble | `text.alignleft` | Reads as "a document"; may be mistaken for Brief or the Meeting Sheet. |

Recommendation: A. Icon only, as the owner asked. No text label at any width.

Button: class `icon-btn tool`, 28 × 28 px like mic, pause and bulb (same hover style). It sits outside `.tools`,
in its own slot (see 3.2), next to the 32 px On me chip, vertically centred. 28 px is the size throughout. In Roadbook/Broadsheet the square
`[data-style]` corners apply. Pressed state (panel open on Transcript): same look as `.tool.on` (bulb on),
no red.

### 3.2 Placement

Order, left to right (changes marked **new**):

```
[Deal] [ Transcript ] [On me ·2]  |  [mic][pause][bulb] [ UK ▾ ]  [rec]  |  [End call][Home][⋯]
        new                          lang chip: new position, new role
```

- Transcript sits **immediately left of On me**. When On me is hidden (0 items) Transcript is in the same slot, so
  the strip does not jump when On me appears. Views (Deal, Transcript, On me) are grouped on the left, call
  controls (mic, pause, bulb, language) on the right, then the recording control and the exit group.
- The language chip leaves its place before ⋯ and joins the call controls, right after the bulb, because it
  changes how the recorder listens (like the mic), not the interface.

Wide (the window's inner width is 760 px or more; the side panel is on):

```
 ■ 32:14 Listening ılıl (AI)                [💬] [ON ME 2]  🎤 ⏸ 💡 [UK ▾] ◉  [END CALL] ⌂ ⋯   | SPEAKERS ...
                                              ^new                    ^new place               | TRANSCRIPT | ON ME | BRIEF
```
The Transcript button is pressed while the panel shows Transcript. Click when pressed: closes the panel
(same as the second click on On me today: `closePane`). Click when the panel shows another tab: switches to
Transcript, does not close.

Narrow (below 760 px, including the 520 px default; no panel): the button shows, in row 2 of the strip (3.3). Click calls `openTab('transcript')`, which grows the window
for the panel (0.26.2, `post('transcript',{on:true})`) and gives the width back on close. This is the beta
tester's path, and it is now one click. Engineering must check what happens when the screen has no room to
grow. The fallback is the existing sheet overlay (`S.ui.sheet`).

Compact card (`body.card`): add the same icon to `.tc-bar`, immediately before Expand. A click expands to the
full window with the Transcript tab selected. The language chip is not added to the card (the bar is full;
Expand or Call settings still reach it). See Q3.

### 3.3 Width budget: the default 520 px window

Estimated from `tips.css` (not measured): the strip needs about 620 px today and about 720 px with the two new
controls (Transcript +38, language chip +~54, On me reduced to the count). The default window is 520 px, so
**one row cannot hold the strip at the default size**, with or without the new controls: the baseline
`live-narrow` already drops ⋯ alone onto a second row by accident. Compressing (hiding the level bars, the status
word, the AI icon) saves about 130 px and still does not reach 520. So the second row has to be designed.

**Option 1 (recommended): two deliberate rows below 760 px** (`flex-wrap` with ordered groups, container query
on `#app`):

```
 520 px  row 1:  ■ 32:14 Listening ılıl (AI)                         [END CALL] ⌂ ⋯
         row 2:  [💬] [ON ME 2]  🎤 ⏸ 💡 [UK ▾]  ◉
```
- Row 1 is status and exit: dot, timer, status word, level bars, AI icon | End call, Home, ⋯.
- Row 2 is views and call controls: Transcript, On me | mic, pause, bulb, language chip, recording control.
- Cost: about 36 px of height for the tips in a 420 px window. In return nothing is hidden, nothing orphans,
  and no control changes position as On me appears or disappears.
- Below 400 px the status word hides (as today); the rows stay.
- From 760 px (the side panel appears) the strip is one row. A narrow band can be tight (760–860 px); there the
  On me label goes to the count only (as today below 460), then the AI icon shows only on warn/out.

**Option 2: one row, compressed by priority** (On me count only, then AI icon only on warn/out, then level bars,
then the status word, then Home into ⋯). It keeps the tips height, but it cannot reach 520 px, so it needs a second
row anyway below ~640 px, and a state that hides information (the AI limit warning) in the normal-size window.
Recommendation: Option 1.

Things that never hide: Transcript, End call, mic, pause, the language chip (below 400 px it loses ▾, never goes).

---

## 4. Language control

### 4.1 Chip

- Text chip, not an icon: the current language must be readable without opening anything.
  Style: the existing `.langbtn` (24 px high, 12 px instead of today's 11 px, bold, bordered), square in `[data-style]`, with a small ▾.
- Content: main language code. Two languages: `UK+EN` (main first). Three or more: `UK+2`.
  Code is ISO `UK` / `EN` in both interface languages. The existing chip prints Cyrillic `УК` in the EN
  interface, which mixes scripts; I recommend ISO codes (see Q4). The tooltip carries the full name.
- Shown while the call is live or paused (any state where the recorder runs). Hidden in Ready (no recorder
  yet; Start call has the choice) and Ended (after the call the Output-language control of FB2 owns this).
- Colour: ink text, not red. A pending switch uses the "not ready" amber outline (`--pn-notready`).

### 4.2 Menu or popover?

| | Option 1: anchored menu (recommended) | Option 2: cycle on click (as S5 today) |
|---|---|---|
| Shape | Click opens a small menu under the chip (the existing `#menu`, `role="menu"`, Esc closes). Pick = apply + close. | Each click cycles to the next language. |
| Fits 3+ states | Yes: UK, EN, UK+EN, more. | Breaks: with UK, EN and mixed, the PM cycles through a state they do not want. |
| Mistake cost | Low: the menu shows the result before commit. | A double click silently lands on a wrong language; a change that alters transcription is hard to notice. |
| Clicks | 2 | 1 |
| Native pattern | Same as ⋯ menu | Same as S5 chip |

Recommendation: Option 1. A native popover (the Call settings `LanguagePicker`) is heavier and takes the
window's focus; the tips window is a floating panel during a live call, so the in-page menu is right.

### 4.3 What the menu lists

```
 Call language                         (heading, not an item)
 ◉ Ukrainian
 ○ English
 ○ Ukrainian + English  (mixed)         shown if two or more languages are in the candidate set
 ─────────────────────
 Tips in   [ UK | EN ]                  shown only in a mixed call (S5 rule kept)
 ─────────────────────
 More languages…                        opens the native Call settings language picker
 Applies from the next phrase. This call only.
```
- Candidate set: the languages of Start call, plus those of Settings > Transcription (`callLanguage`), plus
  the current ones, capped at 3 rows, no duplicates. "More languages…" reaches the rest (the recorder accepts any
  Whisper language, up to 5).
- Mixed row keeps the current main language first (`uk+en` if UK is main). To change which language is main,
  pick the single language, then mixed. A separate "main" selector is not needed (Q2).
- The selected row is marked with ◉ and `aria-checked="true"`. Arrows move, Enter/Space selects, Esc closes
  and returns focus to the chip.
- The tips-language segment is the S5 control (`#langBtn`, owner answer 3A) moved into this menu, still
  mixed-only. This changes something the owner decided last week; see Q4. In a one-language call there is no
  tips choice: tips follow the call language.

### 4.4 What happens mid-sentence

The recorder takes the new set at its next recognition pass (`apply_setting`, no restart). The phrase being
recognised right now finishes under the old set and can be mis-labelled. The design says this honestly:
- Chip changes to the new code at once (optimistic), with the amber "pending" outline until the status echo
  (`call_lang`) arrives. Usually under 2 s.
- A toast confirms, with no modal: `Call language: English. Applies from the next phrase.`
- The transcript gets the recorder's own marker line (`language: en`); in the pane it renders as a thin
  centred divider with the text `Language: English` (dev check: markers must pass the pane's filter).
- Tips already on screen are not rewritten. New tips and cards come in the new main language. A phrase to say
  already follows the language the PM is expected to speak (`answer_lang`), so it is unchanged.
- Echo does not arrive in 4 s: chip reverts, inline toast `Could not change the language. Try again.` (no alert).

---

## 5. States

| State | Transcript button | Language chip |
|---|---|---|
| Ready (before recording) | Visible, enabled. Pane body: "The transcript appears when the call starts listening." | Hidden |
| Listening, no speech yet | Visible. Pane: "Nothing said yet." | Visible, current language |
| Listening, one language | Normal | Chip `UK`; menu: single languages (and mixed if the set allows) |
| Mixed call | Normal | Chip `UK+EN`; menu adds Tips-in segment |
| Paused / mic off | Normal; transcript stays readable | Visible; the switch still applies when the PM resumes. Tooltip adds "Applies when you resume." |
| Switch pending | Normal | Amber outline, `aria-busy="true"`, up to 4 s |
| Recorder not running / restarting (`S.sys` notRecording, no status) | Normal (the transcript file is still readable) | Disabled, tooltip "Recorder is not running." Does not queue changes. |
| Mic watchdog restart (audio device only) | Normal | Normal. `call_lang` is handled by the recorder loop, not the audio device. |
| Transcript very long | Button unchanged; pane scrolls and follows live as today | n/a |
| Ended | Visible (the transcript stays) | Hidden |
| Presentation (deck on, the split view `.pm-split`) | Visible. In the baseline `presentation` at wide width the panel shows beside the deck as usual. In a narrow split `isWide()` is false and `openTab` opens the **sheet overlay** (`S.ui.sheet`), not a grown window; the button does the same | Visible. A switch during a talk applies as in 4.4 (a presenter may switch language between slides); the deck's own text does not change |
| Compact card | Added before Expand | Not added |
| Dark theme, Roadbook / Broadsheet | Same tokens as the other tools; square / 2 px corners | Same as `.langbtn` per style |

---

## 6. Copy (EN / UK)

| Item | EN | UK |
|---|---|---|
| Transcript tooltip (closed) | Show transcript (⌘⇧T) | Показати транскрипт (⌘⇧T) |
| Transcript tooltip (open) | Hide transcript (⌘⇧T) | Сховати транскрипт (⌘⇧T) |
| Transcript aria-label | Transcript | Транскрипт |
| Chip tooltip | Call language: Ukrainian. Click to change | Мова дзвінка: українська. Клацніть, щоб змінити |
| Chip tooltip, mixed | Call language: Ukrainian + English | Мова дзвінка: українська + англійська |
| Chip aria-label | Call language: Ukrainian. Change | Мова дзвінка: українська. Змінити |
| Menu heading | Call language | Мова дзвінка |
| Menu rows | Ukrainian · English · Ukrainian + English (mixed) | Українська · Англійська · Українська + англійська (змішана) |
| Tips-in row | Tips in | Підказки |
| More | More languages… | Інші мови… |
| Footnote | Applies from the next phrase. This call only. | Діє з наступної фрази. Лише для цього дзвінка. |
| Toast, success | Call language: English. Applies from the next phrase. | Мова дзвінка: англійська. Діє з наступної фрази. |
| Toast, failure | Could not change the language. Try again. | Не вдалося змінити мову. Спробуйте ще раз. |
| Disabled tip | Recorder is not running. | Запис не йде. |
| Pane empty (ready) | The transcript appears when the call starts listening. | Транскрипт з’явиться, коли дзвінок почне слухати. |
| Pane empty (live) | Nothing said yet. | Поки ніхто нічого не сказав. |
| Pane marker | Language: English | Мова: англійська |

Language names follow the interface language, never the call language (the same rule as the output-language
control: the interface language is not an input to the resolver, but it is the language of the labels).

## 7. Keyboard and VoiceOver

- Tab order equals the visual order: Deal, Transcript, On me, mic, pause, bulb, language chip, record, End call,
  Home, ⋯. Each has a visible `:focus-visible` ring (the `.endcall` ring is the pattern: 2 px accent, offset 2).
- Transcript: `aria-label` is stable ("Transcript"); the state is `aria-expanded` with `aria-controls="pane"`;
  the tooltip carries the show/hide verb. Shortcut: ⌘⇧T is proposed; dev must check it against the app's key map
  (Q7). Space/Enter toggles.
- Language chip: `aria-haspopup="menu"`, `aria-expanded`, `aria-label` as above. Menu rows are
  `role="menuitemradio"` with `aria-checked`. Tips-in segment is a radio group with its own label.
- After a switch VoiceOver hears the toast through the existing `role="status"` live region, so no extra
  announcement code.
- The pending state is announced by `aria-busy`; it must not be colour only (the amber outline is paired with
  the dotted underline on the code).
- Target sizes: Transcript 28×28 (as the other tools); chip 24 px high, 36 px minimum wide, 12 px text.
  The 24 px chip is the existing size.

## 8. Checks for the Design gate after code

- Appshots to add: `live-wide`, `live-narrow` (600), a 480 and a 760 case, `compact`, one dark, one UK, one
  mixed call, the open language menu, the pending state, and the Ready state with the empty pane.
- Every changed baseline in `tips/` is expected to change; the diff must match section 3.2 and 3.3.
- Check that the strip does not wrap at 760, 640, 520 and 460 px, and that it wraps cleanly (as designed) below
  460.

## 9. Engineering notes for the next steps (not decisions)

1. Hide rule for On me stays (count 0 hides it); only the Transcript button is unconditional.
2. Add a `post('setting', {key:'call_lang', value})` from the chip. The key already exists in the recorder and in
   `CallSettingsModel.send`; wait for the status echo.
3. Main language and the mixed flag must come from the status echo, not from `callRequest`. Otherwise S5's
   "mixed only" tips switch is wrong after a mid-call change.
4. Marker lines in the transcript pane: confirm the pane does not drop them.
5. Tests: a unit test for the menu's candidate set and the chip text (`UK`, `UK+EN`, `UK+2`); a replay of
   the status echo; a case for a recorder that never echoes.

---

## Questions for the owner

1. **Transcript icon.** A: speech bubble with lines (recommended). B: plain text lines. Placement just left of
   On me, always visible; a second click on the pressed button closes the panel. OK?
2. **Language control form.** 1: a menu with one row per language plus "mixed" (recommended). 2: one-click
   cycling as today. Also: when the PM picks "mixed", the main language stays the one currently active. OK, or do
   you want a separate "main language" choice?
3. **Where the language chip lives.** Recommended: in every live call (not only mixed), next to mic/pause/bulb,
   not on the compact card. Alternative: also on the card. Do you want it on the card?
4. **Changes to the S5 tips-language chip (your answer 3A).** I propose three changes to what you just approved:
   (a) the tips-language choice moves from its own chip into the new language menu, still only for a mixed call;
   (b) the chip's place moves next to mic/pause/bulb, and it now appears in every live call because it shows the
   call language; (c) the code prints `UK`/`EN` in both interface languages instead of the Cyrillic `УК`, at 12 px
   (the 12 px minimum rule). Recommended: all three. Alternative: keep the S5 chip exactly as it is and add a
   separate call-language chip beside it (two chips, more strip width). Which?
5. **Output language after the call.** A mid-call switch changes only what is transcribed, and the call's
   dominant language (FB1/FB2) decides the output, as before. Alternative: a mid-call switch also sets the
   explicit output language of the call (`set_by: user`). Recommendation: do not. That would override the
   dominant language even when the PM switched only for one passage.
6. **Narrow windows.** The default window is 520 px and the strip cannot fit one row there. Recommended:
   two deliberate rows below 760 px (status and exit on top; views and call controls below), about 36 px more
   height, nothing hidden. Alternative: one compressed row that hides the level bars, the status word and the AI
   icon (and still wraps below ~640 px). Which?
7. **Shortcut.** Recommended ⌘⇧T for the transcript, no shortcut for the language. Dev will check conflicts.
   Another key, or none?
8. **Scope of a switch.** Recommended: this call only; the Settings default stays. Alternative: an extra
   "Make default" row in the menu. Do you want that row?
