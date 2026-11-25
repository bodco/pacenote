# PB-32 (FB2): one output language, the call language

Status: spec + plan, waiting for the owner's answers (see "Questions for the owner").
Source: owner decision D56 (roadmap FB2). Depends on PB-30 (FB1, the recorder computes the dominant language of a call;
in progress on `team/PB-30-fb1-transcript-language`, not merged, not read by this spec).

## Problem

Today the language of what Pacenote writes comes from three unrelated places:

| Output | Where the language comes from today |
|---|---|
| Meeting Sheet, Quick prep | its own "Sheet language" field. `MeetingSheetDraft.swift:96`: the remembered value of the series, else `uk` when the start-call language is `uk*` and the audience is not `client`, else `en`. Sent as `language` in the sheet request (`MeetingSheetDraft.swift:276`). The Sheet run for a call (`SheetRunner.swift:286`) sends `language: L10n.lang`, the interface language, and `languages` = the call's languages. |
| Brief, Expert prep | `brief_lang: L10n.lang` (`PrepRunner.swift:612`), the interface language; `localprep.py:362` maps it to a name. |
| Call notes, Summary, Recap, meeting notes | `callnotes.call_lang()` (`callnotes.py:33`): the majority of the transcript line tags `(uk)`, `(en)`; `rules/notes.yaml:5`. The app always passes `--lang auto` (`CallEngine.swift:485`, `:526`). A wrongly tagged transcript gives notes in the wrong language. |
| Follow-up email | `FollowUpWindow.language(of:)` (`CallPage.swift:1741`): the project's `client_language` text (`"English"`, `"Ukrainian"`...), English when empty (`rules/followup.yaml:4`). Ignores what was actually spoken. |
| Notion meeting page | `wrapup_notion.py:91-93`: the first language of the transcript header, `en` or else `uk` (only these two have static labels). |
| Tips | `tips_lang` = `me_lang` of the call (`CallEngine.swift:356`), with a switch in the tips window to the interface language (`rules/meeting.yaml > tips_language`, 0.8.5). |

Result: the same call can produce a Ukrainian brief, an English Sheet, Ukrainian notes and an English follow-up. The PM
has to fix it by hand in each place, and some places (notes) cannot be fixed at all.

## Who it is for

The project manager who runs calls in one language (most calls) or mixes two or three (for example a Ukrainian team
call with an English-speaking client, Mike). The one-language PM should never see a language choice. The mixed-language
PM gets one control, not five.

## The rule (owner decision D56)

One source of truth: **the call language.**

- **One language call:** brief, Quick and Expert prep, Meeting Sheet, tips, notes, Summary, Recap, follow-up email, Notion
  page and the email with materials are all in that language. No language field is shown anywhere.
- **Two or three languages:** an **Output language** toggle on the Meeting Sheet and on the call page. Default = the
  dominant language of the call (computed by the recorder, PB-30). Changing it regenerates what depends on it, on a button.
- The separate Sheet language field is removed or derived from the call language.
- `client_language` of the project is only a hint for the default call language (before anyone has spoken).

### Definitions used below

- **Call languages:** the languages chosen in Start call (`me_langs` / `langs` of the call request, up to 3) before the
  call; after the call, the languages the recorder found actually spoken (PB-30).
- **Dominant language:** the single language with the largest share of the call (PB-30 computes and stores it; this task only reads it).
- **Output language:** the one language all generated text of the call is written in. Resolved by one function, in this order:
  1. the PM's explicit choice for this call (the toggle), if any;
  2. the dominant language of the call from the recorder (after the call);
  3. before the call: the first language chosen in Start call; else the language implied by the project's `client_language`
     (hint only, `MenuBar.swift:868` already does this for the start-call default); else the language of Settings >
     Transcription; else English.
- **Multi-language call:** a call with 2 or 3 spoken languages above the recorder's "spoken" threshold (open question 1).

## Scenarios

1. **Mike, one language.** Emily calls Mike in English only. Prep, Sheet, tips, notes, follow-up and the Notion page are
   English, whatever the app's interface language or the project's `client_language` says. No language control appears.
2. **Ukrainian team call, interface in English.** Everything the call produces is Ukrainian. Today brief and Sheet follow
   the interface language; they must not.
3. **Mixed call.** Emily speaks Ukrainian with her team and English with Mike (about 70 / 30). Notes default to
   Ukrainian. In the call page header she switches Output language to English and presses Regenerate: Summary, Recap, notes
   and the follow-up draft come out in English. The transcript is not changed.
4. **Mixed call, before it starts.** In the Meeting Sheet (Quick or Expert) Emily sees Output language because Start call
   has 2 languages. Default = the first language of Start call. She changes it; the Sheet is built in it.
5. **The transcript was tagged wrongly.** Most lines are tagged `en` although the call was Ukrainian. After PB-30 fixes the
   dominant language, notes follow it (they no longer count line tags themselves).
6. **A call page for an old call** (recorded before this change, no dominant language stored). The notes keep the language
   they were written in; Output language shows the language computed from the transcript tags (the old rule), nothing is
   regenerated by itself.
7. **Project config says client_language: English, the call turned out Ukrainian.** Prep (before the call) hints English;
   after the call everything follows the call (Ukrainian).

## Acceptance criteria

Numbered so QA can tick them.

**Resolution**
1. A single resolver returns the output language for a call from the order above, in Python and in Swift, with the same
   results on a shared fixture table (explicit choice > dominant > first start-call language > client_language hint >
   Settings > `en`).
2. The resolver never reads the interface language (`L10n.lang`) to pick the language of generated content.
3. With a recorder that has no dominant-language field yet (PB-30 not merged, or an old call), the resolver falls back to
   the current line-tag majority (`call_lang`) and returns the same value as today for such calls.

**One-language call**
4. In a call whose only language is X: brief, Quick prep, Expert prep, Sheet, notes, Summary, Recap, follow-up draft, Notion
   page and the materials email are written in X (checked by the request each one sends: language name in the prompt).
5. No Output language control is visible on the Meeting Sheet, the call page or in settings for such a call, and "Sheet
   language" is gone from the Quick setup.

**Mixed-language call**
6. For a call with 2 or 3 languages the Meeting Sheet and the call page each show an Output language control listing only
   the call's languages; its default is the dominant language (after the call) or the first start-call language (before).
7. Choosing another language and pressing **Regenerate** rewrites Summary, Recap and notes, the follow-up draft and the
   Notion page text in that language; a visible state says which language each artifact was written in and marks an
   artifact "out of date" when it differs from the chosen output language, except for a follow-up intentionally written
   in the client’s language (option C in question 5).
8. Without pressing Regenerate nothing is rewritten and nothing is sent to a model; the choice is remembered for this call.
9. Regenerate asks nothing when the cost is below the existing spend-watch threshold and otherwise follows the existing
   cost confirmation (same behaviour as the current notes rewrite).

**Removal of the old sources**
10. `MeetingSheetDraft.language` is no longer a user setting. `SheetSeriesMemory` entries written by older versions (with
    `language`) still load; the stored value is ignored for choosing the language.
11. The resolver supplies the default follow-up language. The Follow-up tab and inspector read `client_language`
    to make the offer in option C before the first draft.
12. `rules/notes.yaml`, `rules/followup.yaml`, `rules/meeting.yaml` (tips) and the generated skill section state the new
    rule; `tools/gen_skill_section.py --check` passes.

**Compatibility and safety**
13. Old Sheets, notes, call files and series memory open without error and are not rewritten on load.
14. Languages without static labels (anything beyond `en`/`uk`) still work: text in that language, fixed English headings
    where the app reads headings (as notes do today), no crash, no mixed half-translated labels.
15. No new network destination and no new telemetry data. Language codes and the client-language choice are stored locally.

**Docs**
16. `CHANGELOG.md` (Unreleased), `docs/en/live-tips.md` and `docs/uk/live-tips.md` (the sections on the Meeting Sheet,
    notes and follow-up language), and `docs/*/roadmap.md` (FB2 line) are updated in step; `i18n` `en.json` and `uk.json`
    carry the same new keys (checked by the existing i18n parity test).

## Out of scope

- Computing the dominant language, fixing line tags, transcript language correction: PB-30 (FB1).
- Translating the transcript itself (it stays as recorded).
- Changing the languages the recorder transcribes, or Whisper language choice (Start call > Languages).
- Interface language and its setting (`uiLanguage`): unchanged, still decides menus, buttons and date formats only.
- Adding static UI labels for languages beyond `en`/`uk`.
- Tip phrases ("what to say now"): they follow the language the PM is expected to speak right now
  (`rules/meeting.yaml > answer_lang`), unchanged.
- Sending anything automatically after Regenerate (nothing is sent; the follow-up is still a draft).
- The "email with materials": not found as a feature in this tree (see Plan, risks, and question 6).

## UI impact

- **Meeting Sheet, step 1 (`MeetingSheetPrep.swift` ~724):** the "Sheet language" field is removed. For a multi-language
  call it is replaced by Output language (same place and same menu style, items = the call languages). Remind me stays.
- **Call page (`CallPage.swift`, header or the Summary tab toolbar, to be placed by Design):** Output language menu +
  **Regenerate** button, visible only for multi-language calls; a one-line state under it ("Notes: Ukrainian, follow-up:
  English, out of date"). Needs a design pass (plan_steps: design). Empty, loading, error and "regenerating" states.
- **Tips window:** see question 3 (the interface-language switch for tips).
- No change for one-language calls except that two existing fields disappear.

## Data

- New per-call value: the PM's choice of output language and, per artifact, the language it was written in. Stored in a
  small sidecar next to the call files (name and exact path follow `CallLayout`, to be fixed by the dev in S1). Fields:
  `output_language` (ISO 639-1 code, absent = not chosen), `set_by` (`user`), `artifacts` (map: `notes`, `meeting_notes`,
  `followup`, `notion`, `sheet` to the code each was written in). Optional `followup_uses_client_language` records
  that the PM explicitly chose the client’s language for the follow-up.
- Read from PB-30 (assumed, to be confirmed with that task): see "What is needed from PB-30".
- Removed as a user setting: `SheetSeriesMemory.Entry.language` (kept decodable, no longer written as a choice).
- Project `client_language`: field stays (free text, hint only).
- `sheet.schema` keeps `language` (required by `sheet/schema.py:92`); it is filled from the resolver, so old and new
  Sheets validate.

## Privacy

- The language codes and the client-language choice are stored locally. Nothing new leaves the Mac.
- Regenerate sends the same transcript/notes to the same model route the user already chose (Claude API, or the model on
  this Mac); no new provider, no new telemetry event carrying text. If the owner wants a usage counter for the toggle, it
  is a count with no language values or content (question 7).
- Meeting Sheet and the call page never share a language choice between projects (the choice is per call, the series
  memory no longer stores it).

## What is needed from PB-30 (assumed, to confirm)

The resolver reads exactly one adapter function, so PB-30's final shape only changes that function:

1. A per-call field with the dominant language, an ISO 639-1 code (`uk`, `en`...), or empty/null when it cannot be told
   (very short call, no speech). Proposed name `dominant_language`. Stored where both Python (`callnotes`, `followup`,
   `wrapup_notion`) and Swift (`CallPage`, `AfterCall`) can read it: the call transcript header and/or the call's sidecar.
2. The list of languages actually spoken with their share (lines or seconds), e.g. `{"uk": 0.71, "en": 0.29}`, to decide
   "one language vs 2-3" and to fill the toggle. Languages under the "spoken" threshold are not listed.
3. When it is computed: at the end of the call, and again after any transcript correction or reassign
   (`CallReassignService`), so it is never stale.
4. Whether the PM can already override it in PB-30 (if so, the toggle here must write the same field, not a second one).
5. A stable value for old calls (none): the resolver falls back to the tag majority (acceptance criterion 3).

Agreed persisted format (round 4): the recorder's `LangShare` already counts seconds of speech per language in memory
(`LangShare.secs`) and PB-30 does not store it. PB-30 must write, into the call's `<project>/video/<stem>.recording.json`,
`"languages": {"uk": 1260.5, "en": 310.2}` (that map, seconds of speech) and `"dominant_language": "uk"` (or leave it
out, then the largest value wins), at the end of the call and after a transcript correction. Both readers
(`call_language.py`, `CallLanguage.swift`) accept seconds or shares: a map whose values add up to more than 1 is divided
by its total. The shared fixture table holds a seconds case. Until PB-30 writes the fields, calls fall back to the line tags.

Writer (round 6, on this branch): `call_layout.save_spoken(transcript, secs, dominant=None)` in `recorder/src/call_layout.py`
(the app copy `spikes/m0/livetips_m0/call_layout.py` is byte-identical, a test keeps them so). It writes `languages` and
`dominant_language` into the file above under the same flock as the app's `RecordingMeta.update`, keeps the app's
recording parts, and is atomic. The format is locked by `Fixtures/recording_meta_languages.json`: pytest
(`tests/test_recording_meta_languages.py`) produces it with the recorder's copy and compares; CoreChecks reads that same
file with the Swift reader and saves it through `RecordingMeta` without dropping the fields.

What was still needed from PB-30 when round 6 closed (done in PB-72, below), two call sites in `transcriber.py`:
1. When the recording ends: `call_layout.save_spoken(md, lang_share.secs, dominant)`.
2. After the end-of-call transcript correction (and after a reassign that changes the transcript): the same call with
   the corrected counts.

`dominant` is `None` when no speech was counted or the call is still inside the warm-up: `LangShare.dominant()` returns
the first language of the set then, which would mislabel a short call; with `None` the readers take the largest value of
`languages`. Before PB-72, a call with wrong line tags still resolved from the tags (the open part of review round 6,
finding 1).

Done in PB-72: `transcriber.save_call_languages` calls `save_spoken` after `writer.close` and again after
`apply_line_edits` when any edit was applied. The counts are `LangShare.total` (seconds of the whole call; `reset()` on a
language switch clears only the live share `secs`), the dominant is `LangShare.spoken(t)`: `None` with no speech or
under `warmup_s` from the call's first segment. Each written line keeps the seconds it credited, and a line deleted in
the transcript pane is taken out of the totals (`uncount_dropped`). No file is made for a call with no speech counted
and no recording. A reassign by `CallReassignService` in the app does
not re-save the counts (out of PB-72).

## Plan

### Modules and files

| Area | Files | Change |
|---|---|---|
| Resolver (new) | `spikes/m0/livetips_m0/call_language.py`, `app/Sources/LiveTipsCore/CallLanguage.swift`, `rules/output_language.yaml` (the order, the hint mapping, the supported list) | one function in each language, shared fixture table `Fixtures/output_language_cases.json` |
| Client hint mapping | `app/Sources/LiveTips/CallPage.swift:1741` (`FollowUpWindow.language`) | body replaced by the resolver, keep the function name to avoid churn |
| Notes, Summary, Recap | `callnotes.py` (`call_lang`, `--lang auto`), `CallEngine.swift:484-526` (args), `rules/notes.yaml` | `auto` calls the resolver; `--lang` accepts any supported code |
| Follow-up | `followup.py` (`LANGS`), `AfterCall.swift:32,43,69`, `rules/followup.yaml` | language from the resolver; keep `--lang` |
| Notion page | `wrapup_notion.py:91-93` and `TXT` | language from the resolver; unsupported static labels fall back to English |
| Sheet, Quick prep | `MeetingSheetDraft.swift:65,96,276,442`, `MeetingSheetPrep.swift:724`, `MeetingSheetService.swift:549` (Entry), `SheetRunner.swift:286`, `sheet/build.py:965`, `sheet/prompt.py` | remove the field, derive `language`; Output language only for multi-language calls |
| Brief, Expert prep | `PrepRunner.swift:612,641`, `localprep.py:362`, `prepcontext.py:441`, `rules/prep.yaml` | `brief_lang` from the resolver, not `L10n.lang` |
| Tips | `CallEngine.swift:356`, `Prompter.swift:211`, `app/Resources/tips/tips.js:1092`, `rules/meeting.yaml > tips_language` | depends on question 3 |
| Call page UI | `CallPage.swift`, `CallPageLayout.swift`, new `OutputLanguageView.swift` | menu, Regenerate, staleness state |
| Strings, docs | `app/Resources/i18n/{en,uk}.json` (also `rules/i18n/` if the keys live there), `CHANGELOG.md`, `docs/{en,uk}/live-tips.md`, `docs/{en,uk}/roadmap.md` | in step |
| Skill | `rules/generated/skill_*.md`, `skill/SKILL.md`, `skill/settings.yaml:47`, `skill/references/start.md:46,70` | regenerated by `tools/gen_skill_section.py`; the skill follows the same rule |

### Risks

- **Data / migrations:** `SheetSeriesMemory.Entry` is `Codable` with a non-optional `language: String`; removing the field
  or making it optional must decode old files (test with a real old JSON). `sheet/schema.py` requires `language`; keep
  it. The new per-call sidecar must tolerate being absent (all old calls) and being half-written.
- **Staleness without PB-30:** until PB-30 lands, "dominant language" equals the tag majority. If PB-30's field changes
  meaning (share by seconds vs lines) the default can differ for edge calls; isolated in the one adapter.
- **Cost:** Regenerate re-runs model calls. Full notes about $0.19 per 15 minutes with Sonnet; the meeting-notes section only
  about $0.05 to $0.10 (`rules/notes.yaml`). Question 2 picks the scope.
- **Privacy:** none new (see Privacy). Do not log transcript text when the language is logged.
- **Performance on a 16 GB Mac:** resolving is counting tags and reading one small file, no model load. Regenerate must run
  one request at a time, never in parallel with a recording that uses the local model (On this Mac route), and must not
  start while the call is still recording; it reuses the existing queue/spend-watch (`SpendWatch.swift`).
- **UI churn for a language list longer than en/uk:** the Sheet field offers only `en`/`uk` today; the new control lists
  call languages, which can be any Whisper language. Static labels exist only for `en`/`uk` (criterion 14).
- **Conflicts between parallel devs:** `CallEngine.swift`, `i18n/*.json` and `CHANGELOG.md` are touched by several
  subtasks; ownership below avoids two devs on one file, the JSON/CHANGELOG are appended by each subtask in its own block.

### Tests

- Python (`cd spikes/m0 && python3 -m pytest <files> -q`): `tests/test_call_language.py` (order of resolution, one vs several
  languages, null dominant, unknown code, client_language mapping, no interface language), extend
  `tests/test_callnotes_lang.py` (auto uses the resolver; wrong tags + dominant field gives the dominant), follow-up and
  wrapup language tests with a fake provider (language name in the system prompt), sheet request language.
- Swift (`app/Sources/CoreChecks/main.swift`, the CoreChecks target): resolver cases from the shared fixture table;
  `SheetSeriesMemory` decodes an old entry with `language`; sidecar read/write and "out of date" computation.
- Web (`node app/WebTests/feed.test.js`) only if the tips switch changes (S5).
- Regression for the reported bug (rule: a test first, red on old code): notes of a Ukrainian call whose lines are mostly
  tagged `en` come out Ukrainian when the dominant field says `uk`.
- Manual (QA, because UI): Sheet one-language and mixed, call page toggle and Regenerate, an old call, a call page for a
  project with `client_language: English` and a Ukrainian call.
- `tools/gen_skill_section.py --check`, `tools/check_anonymized.py`, the i18n parity test, `swift build` for the Swift subtasks.

### Subtasks (independent files, can run in parallel after S1's interface is fixed)

| # | Subtask | Owns (only these files) | Depends on |
|---|---|---|---|
| S1 | Resolver and per-call sidecar: Python + Swift + shared fixtures + `rules/output_language.yaml` | `livetips_m0/call_language.py`, `LiveTipsCore/CallLanguage.swift`, `Fixtures/output_language_cases.json`, `rules/output_language.yaml`, `tests/test_call_language.py`, `CoreChecks/main.swift` (resolver block) | interface only: PB-30 field (adapter) |
| S2 | After-call text: notes, Summary, Recap, Notion page | `callnotes.py`, `wrapup_notion.py`, `CallEngine.swift` (lines for callnotes/wrapup args and, if question 3 says so, `tipsLang`), `rules/notes.yaml`, `tests/test_callnotes_lang.py`, `tests/test_wrapup_*` | S1 |
| S3 | Follow-up and materials email | `followup.py`, `AfterCall.swift`, `CallPage.swift` (only `FollowUpWindow.language`), `rules/followup.yaml`, `tests/test_followup_*` | S1 |
| S4 | Meeting Sheet, Quick prep, brief, Expert prep | `MeetingSheetDraft.swift`, `MeetingSheetPrep.swift`, `MeetingSheetService.swift`, `SheetRunner.swift`, `sheet/build.py`, `sheet/prompt.py`, `PrepRunner.swift`, `localprep.py`, `prepcontext.py`, `rules/prep.yaml`, `rules/meeting-sheet.yaml`, Sheet tests | S1 |
| S5 | Tips language (only if question 3 = no switch for one-language calls) | `Prompter.swift`, `app/Resources/tips/tips.js`, `rules/meeting.yaml`, `WebTests/feed.test.js` | S1, answer to question 3 |
| S6 | Call page: Output language menu, Regenerate, staleness | `OutputLanguageView.swift` (new), `CallPage.swift` (header block), `CallPageLayout.swift`, sidecar writer in `CallLanguage.swift` calls only | S1, S2, S3 (what Regenerate calls) |
| S7 | Docs, CHANGELOG, roadmap, skill regeneration, i18n merge | `CHANGELOG.md`, `docs/{en,uk}/live-tips.md`, `docs/{en,uk}/roadmap.md`, `skill/*`, `rules/generated/*`, `i18n/{en,uk}.json` final check | S2 to S6 merged |

Order: S1 first (small, unblocks everything); S2, S3, S4 (and S5) in parallel; S6 after S2/S3; S7 last. Design review
(S4 field removal, S6 layout) and Copy review (new strings en + uk) are plan_steps. A dev adding i18n keys appends them
under its own prefix (`sheet.ui.*` for S4, `call.outlang.*` for S6) so the JSON merges cleanly.

## Questions for the owner

1. **When is a call "mixed" (shows the toggle)?** The recorder lists the languages spoken; a few stray words in a second
   language should not turn on a toggle.
   - A. A language counts when it has at least 10% of the call. 
   - B. At least 20% of the call.
   - C. Any language the PM chose in Start call counts, whatever was actually spoken.
   Recommendation: A, and the toggle lists only languages that pass it; C is how it works before the call.
2. **What does Regenerate rewrite?**
   - A. Everything text: full notes, Summary, Recap, follow-up draft, Notion text (about $0.19 per 15 minutes).
   - B. Only the meeting notes section (TL;DR, Recap, Decisions, Actions...) and the follow-up draft; the full detailed
     notes stay in the language they were first written in (about $0.05 to $0.10).
   - C. A, but ask before spending when the call is longer than one hour.
   Recommendation: C. The full notes are what later preps read, so a language change should not leave half of them stale.
3. **Tips window for a one-language call.** Today (0.8.5) a switch can show tips in the interface language when it differs
   from the call language. D56 says one language, everything in it.
   - A. Remove the switch for one-language calls; keep it for mixed calls only.
   - B. Keep the switch everywhere as a deliberate exception (tips are live, the PM reads them while speaking).
   - C. Remove it everywhere; mixed calls use the Output language toggle for tips too.
   Recommendation: A. It follows D56 and keeps the 0.8.5 behaviour where the language genuinely varies.
4. **Where does the PM's explicit choice live?**
   - A. Per call only (the toggle changes this call's output; the next call starts from its own dominant language).
   - B. Per call, and also remembered per series (a weekly call with the same title starts with the last choice).
   - C. Per project default for mixed calls.
   Recommendation: A. D56 says default = dominant language of the call; a remembered choice would bring back the second
   source of truth this task removes.
5. **The follow-up email to a client whose language differs from the call language** (a Ukrainian call, an English-speaking
   client). `client_language` was the only way to get this today.
   - A. Follow the call language like everything else; the PM switches the toggle to English before drafting.
   - B. Follow-up alone keeps the project's `client_language` when it is set, as a documented exception.
   - C. Follow the call output language by default. The Follow-up tab and inspector ask "Write in English instead?"
     before the first draft when `client_language` differs. If the PM accepts, the follow-up uses the client’s language
     and stays current when the call output language changes. Write again requires confirmation and logs hand edits
     before replacing an existing draft.
   Recommendation: C. It respects D56 and keeps the real use of `client_language` without a silent override.
6. **"The email with materials".** I did not find a feature with this name or an email send of Sheet materials in this tree
   (only the follow-up mailto in `AfterCall.swift` and the Gmail/Calendar integrations). Which is it?
   - A. It is the Meeting Sheet "send to participants" email planned in the roadmap: include it when that feature
     exists, with the same resolver (no extra work now).
   - B. It is the follow-up email: nothing separate.
   - C. It is something else: tell me where, and I will add it to S3 or S4.
   Recommendation: A or B, whichever it is, covered by the resolver; confirm so that S3 does not miss a file.
7. **Usage counting for the toggle.**
   - A. None.
   - B. An anonymous count of toggle uses in the optional Help improve Pacenote telemetry, no language values.
   Recommendation: A for now; the owner can add B later without changing the design.
8. **Languages beyond English and Ukrainian.** The Notion page and the UI labels have static text only for `en` and `uk`.
   - A. Text in the output language, fixed English headings and labels (as notes do today).
   - B. Limit Output language to `en`/`uk` and others fall back to the dominant of those two.
   - C. A now, and add labels for Spanish, German, French, Polish as a later task (`followup.py` already knows them).
   Recommendation: A (matches criterion 14; no loss for calls in other languages).
