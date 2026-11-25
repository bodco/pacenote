# PB-108: speaker clustering splits a few voices into many tiny clusters (investigation)

Status: investigation done (acceptance line 0). No product code changed. The fix is a separate task (see "Implementation steps").
Source: owner request 25.11. Board: PB-108.

## Summary for the owner (UA, 10 lines)

1. Причина: коміт `3bf2f34` (29.10, Pacenote 0.8.5) зробив пороги "приєднатися до спікера" і "злити двох спікерів" зростаючими з кількістю рядків спікера (з 0.45 до 0.59 і з 0.6 до 0.81).
2. У одного голосу 12-17% рядків не дотягують до нового порогу; кожен такий рядок від 1.5 с відкриває нового спікера.
3. Спікера з одним рядком автозлиття не чіпає (`len(members) < 2`): 72 з 88 дрібних кластерів на 20 збережених дзвінках мають рівно один рядок.
4. Бісект: replay тих самих голосових відбитків кодом до `3bf2f34` дає старі числа, кодом `3bf2f34` і поточним дає нинішні, на всіх 20 дзвінках.
5. Історія: до 29.10 медіана 5 кластерів на дзвінок (2.9 на 10 хв мовлення), після 8 (5.9 на 10 хв), найгірше 50.
6. Просто відкотити не можна: старі пороги змішують різних людей (на дзвінку з 7 учасниками чистота 0.57 замість 1.0).
7. Пропозиція: після End call кластери до 10 с зливати з найближчим голосом лише коли він явно ближчий (косинус від 0.55 і на 0.1 ближче за другий), інакше в один кошик "Інші голоси"; під час дзвінку лише показувати їх як "Інші голоси", без злиття.
8. На 20 дзвінках: медіана кластерів 7 -> 4, найгірший 18 -> 7, перевищення над реальними голосами в медіані 1; хибне злиття з чужою людиною 1 з 36 перевірених рядків.
9. Старі дзвінки і бібліотека голосів не змінюються: злиття пишеться в той самий `merged`, який уже читають усі читачі; "Інші голоси" ніколи не вчать бібліотеку.
10. Не виміряно replay: дзвінки з 8 у списку Meet (три записи 13.11) і з 50 кластерами (немає відбитків); оцінка зі `speakers.json` за 10 с: 09.11 і 13.11 по 10, 12.11 8, тобто "найгірший менше 10" за 10 с не виконано, за 20 с виконано.

## How it was checked

- Data: the 20 calls (20.11-25.11) that have per-line voice prints in `.work/<stem>.voices.json` (written since 0.26.2, 20.11), plus `<stem>.speakers.json` of 118 calls (22.10-25.11) for the history. Everything was read locally on the owner's Mac; nothing was written into a call folder or the voice library.
- Call count: 175 `speakers.json` files under the work root, 136 unique by content (the rest are the same files reached by a second path), 118 of them with at least 60 s of other-side speech: 27 before `3bf2f34`, 91 after (re-counted in fix round 1).
- Replay harness (scratch, not in the repo; implementation step 1 rebuilds it under `private/` so it can be re-run): every call's lines, in time order, go into `SpeakerTracker.observe` of a given `transcriber.py` version with `library=None` (nothing learned), and the result is written only to a temp folder. Versions: current `HEAD`, `3bf2f34`, `3bf2f34^`, `e4da9da^` (before 0.8.5), and current code with variants.
- Data snapshot: 25.11, 15:14 Kyiv. One call of that day got its Google Meet list after the first runs (now 2 participants instead of 3); it has 2 named voices, so its voice count below is the same either way.
- Fidelity: the current-code replay reproduces the recorded cluster count on 18 of 20 calls, and the count of clusters under 10 s too on 17. The 2 calls that differ have merges in their recorded `speakers.json` that the replay does not make (a merge in the tips window during the call or a library effect; the files do not say which). On the other 18 calls running without the library gave exactly the recorded count.
- Real voices per call: the Google Meet participant list minus the PM (`speakers.json > meet.participants`), or the number of distinct names on the call when that is larger (5 calls have more names than Meet participants, for example two people at one device). Names come from the PM, Meet or auto naming (19 of 204 names on these calls are auto). The system channel carries only the other side, so the PM is not a voice here. With Meet participants alone (17 calls have a Meet list) the proposed fix leaves the median clusters minus voices at 2 and 9 calls over voices + 1, against 1 and 8 with the definition above.
- Purity: lines whose speaker has a name on the call are the partial truth; purity = share of those lines whose replayed cluster holds mostly the same name. It shows when the live clustering puts two named people together. It cannot judge where a small cluster goes, because small clusters were never named; that is measured separately (see "Is the nearest voice the right one?").
- Calls are labelled A to F by shape only. Titles, folders and names are not in this document.

## Cause

### The change

`3bf2f34` (29.10 15:07, "Voices: thresholds grow with the lines behind a speaker", part of 0.8.5) replaced the fixed thresholds in `SpeakerTracker` (`recorder/src/transcriber.py`):

- join: a line joins the closest speaker from `max(speaker_join 0.45, speaker_join_k 0.85 x expected_same(1, n))`;
- automerge: two speakers merge from `max(speaker_automerge 0.6, speaker_merge_k 0.9 x expected_same(n, m))`;
- `expected_same(n, m) = rho / sqrt((rho + (1 - rho) / n) x (rho + (1 - rho) / m))`, `speaker_same_rho` 0.5.

Thresholds this gives (computed from the current code):

| Lines behind the speaker | 1 | 2 | 3 | 5 | 10 | 30 |
|---|---|---|---|---|---|---|
| join threshold (was 0.45) | 0.45 | 0.491 | 0.521 | 0.549 | 0.573 | 0.591 |

| Automerge threshold (was 0.6) | 1 line vs 5 | 1 vs 30 | 2 vs 5 | 2 vs 30 | 5 vs 5 | 5 vs 30 |
|---|---|---|---|---|---|---|
| | 0.6 | 0.626 | 0.671 | 0.723 | 0.75 | 0.808 |

The commit and `rules/voices.yaml > thresholds` already name the price: "more small speakers are the price".

### Why it splits one voice

- One voice is noisy line to line. On the three 1:1 calls (one remote voice by Meet), a line's cosine to the rest of that voice is 0.77-0.86 in the median, but 12-17% of lines fall under 0.60, all of them 1.5 s or longer (median 2.5-2.9 s). During the call the centroid is built from fewer lines, so the live cosine is lower still.
- Such a line misses the grown join threshold (about 0.57-0.59 once the speaker has 10-30 lines) and, being at least `speaker_new_min_s` 1.5 s long, opens a new speaker.
- `_automerge` skips any speaker with fewer than 2 members (`len(members) < 2`), so a one-line speaker never merges back. Of the 88 clusters under 10 s that the current code makes on the 20 calls, 72 have exactly one line. Dropping that guard alone changes nothing (replayed: the same count on every one of the 20 calls), because the automerge threshold for one line against a grown speaker is 0.6-0.63 and these lines are under 0.60.
- The "Who is speaking?" ask waits for 15 s of speech, so these speakers are never asked about; they stay as "Speaker 14" in the transcript, the speakers list, the notes and the tips prompt (engine `SpeakerNames.who` gives "Them [S14]").

### The bisect

Replay of the same voice prints through each version (clusters per call, all 20 calls):

- `e4da9da^` (before 0.8.5) and `3bf2f34^`: identical counts on all 20 calls.
- `3bf2f34` and `HEAD`: identical counts on all 20 calls, equal to what the calls recorded on 18 of 20 (see Fidelity).
- `HEAD` with `speaker_join_k 0` and `speaker_merge_k 0` (fixed 0.45 / 0.6 again): identical to `3bf2f34^` on all 20 calls.

So the first version that produces today's counts is `3bf2f34`, and its two new factors alone explain the difference. Later speaker commits (`55b64a4` 19.11: the same model file, pinned by SHA-256, plus auto naming; `b5fb48d` 20.11: call page edits) do not change clustering in the replay.

### The history

`speakers.json` of 118 calls with at least 60 s of other-side speech:

| | Calls | Clusters, median | Clusters, worst | Under 10 s, median | Share under 10 s | Clusters per 10 min of speech, median |
|---|---|---|---|---|---|---|
| Before `3bf2f34` (22.10-29.10 15:07) | 27 | 5 | 12 | 2 | 0.38 | 2.89 |
| After (29.10-25.11) | 91 | 8 | 50 | 5 | 0.67 | 5.93 |

The rise per 10 minutes of speech shows this is not just longer calls. The two 50-cluster calls (09.11 and 12.11) are long ones: 55 and 88 minutes of other-side speech. 50 is not a cap: their speaker ids go up to S52 and S51 (with 2 and 1 merges), and the code has no limit on the number of speakers.

## Numbers per call

Six calls by shape. "Voices" as defined above. Cells are clusters / of them under 10 s. "Old" = `3bf2f34^` (fixed thresholds), "k 0.7" = current code with `speaker_join_k 0.7`, `speaker_merge_k 0.8`, "Fix" = current code plus the proposed End call settle (a cluster under 10 s goes to the nearest voice of 10 s or more only at cosine 0.55 or more and 0.1 ahead of the second nearest, else to one Other bucket, which counts as a cluster).

| Call | Shape | Speech | Voices | Recorded | Current replay | Old | k 0.7 | Fix | Purity current / old / fix |
|---|---|---|---|---|---|---|---|---|---|
| A | short, 6 min, 44 lines | 285 s | 3 | 7 / 4 | 7 / 4 | 6 / 3 | 6 / 3 | 4 / 0 | 1.0 / 1.0 / 1.0 |
| B | long, 48 min, 219 lines | 1702 s | 2 | 10 / 7 | 10 / 7 | 6 / 3 | 7 / 4 | 4 / 0 | 1.0 / 1.0 / 1.0 |
| C | 2 persons, 23 min, 87 lines | 601 s | 1 | 7 / 5 | 7 / 5 | 2 / 1 | 5 / 4 | 3 / 0 | 1.0 / 1.0 / 1.0 |
| D | most people, 7 on Meet, 21 min, 83 lines | 573 s | 6 | 12 / 6 | 12 / 6 | 8 / 4 | 11 / 5 | 7 / 0 | 1.0 / 0.57 / 1.0 |
| E | worst today, 28 min, 160 lines | 1011 s | 3 | 18 / 15 | 18 / 15 | 6 / 3 | 8 / 5 | 4 / 0 | 1.0 / 1.0 / 1.0 |
| F | long, 36 min, 175 lines | 1345 s | 3 | 8 / 5 | 8 / 5 | 6 / 2 | 6 / 2 | 4 / 0 | 1.0 / 1.0 / 1.0 |
| Six calls | | | | | median 9, worst 18 | median 6, worst 8 | median 6.5, worst 11 | median 4, worst 7 | |

No call with 8 or more people has voice prints: of the 17 calls with a Meet list the largest has 7 participants (call D), and the 3 calls without one have at most 4 named voices. The 8+ case is not measured by replay. In the 118-call history three recordings (all on 13.11, starting about an hour apart) have 8 on the Meet list, the PM included; none has more. The list does not show who actually spoke. Their recorded counts and bounds are in "Calls without voice prints" below.

All 20 replayable calls:

| Variant | Clusters, median | Worst | Clusters minus voices, median | Calls over voices + 1 | Calls with purity under 1 |
|---|---|---|---|---|---|
| Current | 7 | 18 | 4.5 | 19 | 2 |
| Old fixed thresholds (`3bf2f34^`) | 4 | 8 | 2 | 11 | 6 (lowest 0.57) |
| k 0.7 | 5 | 11 | 3 | 14 | 5 |
| Current without the `len(members) < 2` guard | 7 | 18 | 4.5 | 19 | 2 |
| Settle at End call, nearest from 0.45, 10 s | 4 | 7 | 1 | 6 | 2 |
| Settle at End call, 0.55 and 0.1 ahead, 10 s (proposed) | 4 | 7 | 1 | 8 | 2 |
| Settle at End call, nearest from 0.45, 20 s | 3 | 6 | 1 | 3 | 2 |
| Settle at End call, nearest from 0.45, 30 s | 3 | 5 | 1 | 1 | 2 |
| Settle live (60 s after a small speaker's last line) and at End call, nearest from 0.45 | 3 | 7 | 1 | 3 | 3 |
| Settle live and at End call, 0.55 and 0.1 ahead | 3 | 6 | 1 | 3 | 3 |

The 2 calls with purity under 1 on the current code (lowest 0.855) have recorded merges or line moves the replay does not make; every End call settle keeps exactly these 2. The old thresholds and k 0.7 add 4 and 3 calls where differently named people share a cluster, and each live settle adds 1.

The cost of the proposed rule: more speech in Other. Over the 20 calls Other holds a median 1.6% of the other side's speech, at most 4.9%; on the six calls A 3.7%, B 1.5%, C 2.7%, D 3.0%, E 3.8%, F 1.6%. On call C (one remote voice) the result is 3 clusters: the voice, one speaker of 10 s or more that the live clustering split off, and Other.

### Calls without voice prints: the bound

The 50-cluster calls and the 8-person calls have no voice prints, so they cannot be replayed. Their `speakers.json` still gives an upper bound for the End call settle, because the settle does not change the live clustering: it only moves an unnamed speaker under the threshold T into a speaker of T or more, or into Other, and never moves a named speaker.

Bound(T) = (unnamed speakers with `seconds` at or over T) + (all named speakers, any size) + 1 Other (if any unnamed speaker is under T).

Speakers are the `clusters` of `speakers.json` (merged ones are already folded into their target, which carries their `seconds`). The real count after the settle can be lower by one at most: an unnamed speaker of T or more stays as it is, but small speakers may all go to voices and leave no Other. On the six replayed calls the bound at 10 s (recorded clusters minus those under 10 s, plus 1: 4, 4, 3, 7, 4, 4) equals the Fix column exactly, so for the calls below the bound is the expected count, not a loose limit. None of the calls below has a named speaker under 10 s, so the named term changes nothing here.

| Call | Speech | Meet participants | Named speakers | Recorded clusters | Of T or more: 10 / 20 / 30 s | Bound: 10 / 20 / 30 s |
|---|---|---|---|---|---|---|
| 09.11, long | 55 min | 3 | 0 | 50 | 9 / 7 / 4 | 10 / 8 / 5 |
| 12.11, long | 88 min | 4 | 3 | 50 | 7 / 5 / 5 | 8 / 6 / 6 |
| 13.11, 8 on Meet, first | 38 min | 8 | 3 | 37 | 9 / 7 / 4 | 10 / 8 / 5 |
| 13.11, 8 on Meet, second | 9 min | 8 | 0 | 10 | 3 / 3 / 3 | 4 / 4 / 4 |
| 13.11, 8 on Meet, third | 11 min | 8 | 0 | 8 | 6 / 6 / 5 | 7 / 7 / 6 |

The 13.11 call with 37 clusters is a third call, not a slip for 12.11. Over all 91 calls since 29.10 the bound is at most 10 at 10 s (reached only by 09.11 and the 37-cluster 13.11 call), at most 8 at 20 s (the same two) and at most 7 at 30 s (11.11, 03.11, 20.11); the median bound is 4 at every T. So at 10 s the expected count on those two calls is 10 (9 if no Other is left), and "worst long call under 10" is not met on them; at 20 s it is met on every past call.

### Is the nearest voice the right one?

Small clusters were never named, so the replay cannot say where they belong. Proxy: on calls with 2 or more named voices of 10 s or more, the named lines that sit far from their own voice (cosine to the rest of it under 0.60, 1.5 s or longer: the lines that become small clusters) are sent by each rule. 36 such lines:

| Rule | To their own voice | To another named person | To Other |
|---|---|---|---|
| nearest from 0.45 | 23 | 9 | 4 |
| nearest from 0.5 | 17 | 6 | 13 |
| nearest from 0.45, 0.1 ahead | 19 | 6 | 11 |
| nearest from 0.55, 0.1 ahead (proposed) | 10 | 1 | 25 |

A wrong merge puts one person's words under another person's name in the notes and the follow-up, which is worse than "Other voices". On the 20 calls the proposed rule keeps the median and the worst of the plain nearest rule (median 4, worst 7) but leaves 8 calls over voices + 1 instead of 6, and gives a wrong person 1 time in 36 instead of 9.

### 1:1 calls

Share of the other side's speech outside the main voice on the three 1:1 calls (one Meet participant besides the PM and one named voice): current code 0.053, 0.049, 0.121; proposed End call settle 0.053, 0.049, 0.121 (the small pieces move from "Speaker N" to Other, they are not given to the voice because they are not clearly close to it); End call settle with the nearest rule from 0.45: 0.049, 0.016, 0.121; live settle from 0.45: 0.0, 0.033, 0.013.

This is a limit of the proposed fix: it only handles speakers under 10 s. A piece of one voice that the live clustering split off and that grew past 10 s (the 0.121 call above; call C keeps one such speaker too) stays a separate speaker. On the long calls without voice prints this is most of what remains: 09.11 has 3 on the Meet list (2 remote voices) but 9 speakers of 10 s or more and still 4 of 30 s or more. Fixing those needs a looser live join, which mixes similar voices (see below), so it is left out.

### Similar voices (the reason for `3bf2f34`)

The existing scenario `test_similar_voices_stay_apart` (two voices at 0.78 of rho, 8 seeds, the test needs mean purity 0.85):

- current code: purity 0.903, 5.9 clusters per run;
- End call settle: purity 0.878 with every rule tried, 1.75 clusters per run (nearest from 0.45). The drop comes from the 2 seeds where the live clustering had already mixed the voices (purity 0.66 and 0.74); there everything collapses into one speaker (0.60). The other 6 seeds keep two speakers and their purity;
- live settle (60 s) with the nearest rule from 0.45: purity 0.80, under the test's 0.85. Two more seeds that the current code keeps apart (0.94, 0.93) collapse (0.68, 0.59): a new similar voice is swallowed in its first seconds and its centroid never forms;
- live settle with the proposed rule (0.55 and 0.1 ahead): purity 0.938, but one seed the current code keeps apart drops from 0.94 to 0.70, and on the 20 real calls it adds one call where two named people share a cluster.

So a loose live merge undoes what `3bf2f34` gained; the strict live merge is close but still mixes one real call; merging at End call does not touch the live clustering at all.

## Proposed fix

1. Keep the live join and automerge thresholds of `3bf2f34` (they keep similar voices apart; going back mixes people).
2. During the call, never merge small speakers. Show an unnamed speaker with under `speaker_tiny_s` 10 s of speech as "Other voices" / "Інші голоси" in the speakers list, the chip and the transcript (a label only; centroids do not move). A speaker that grows past 10 s shows as itself again. This means every new voice is shown as "Other voices" for its first 10 s of speech (today it is "Speaker N" until 15 s, when "Who is speaking?" asks). Auto naming (Meet, roster) can name a voice from `speaker_auto_s` 4 s, so a voice that gets a name early shows its name, not "Other voices", from then on.
3. At End call, settle: an unnamed speaker under 10 s joins the speaker of 10 s or more whose centroid is at cosine `speaker_settle_min` 0.55 or more and `speaker_settle_margin` 0.1 ahead of the second nearest, with the `_automerge` guards (no conflicting names, `_lib_other` does not place it with another known person). Otherwise it goes to one Other bucket per call. Written to the existing `merged` map of `speakers.json`, so `lt`, `read_delta.py`, the engine's `SpeakerNames`, `CallSpeakers.swift` and `named.md` already resolve it, and recorded as `auto_merged` so the call page can tell it from the PM's merges.
4. The Other bucket: `other: true` in its cluster, never asked "Who is speaking?", never auto-named, never learned by the voice library.
5. Tips never carry `S<n>`: an unnamed voice is "the client" when the other side has one voice so far, else "someone on their side" (side from the cluster, the roster or the calendar); a tip that still contains `S\d+` or `Speaker \d+` is rewritten the same way before it is shown.
6. Split a line with two voices at the voice change before labelling. The saved data has one print per line, so this is not measured here and is a separate step with its own measurement.
7. Imported recordings (`fromfile.py`) cluster with their own offline `cluster()`, not `SpeakerTracker`; the same small-speaker rule is applied there as a separate step.

Expected effect, from the replay: median clusters 7 -> 4, worst replayable call 18 -> 7, median clusters minus voices 1, 8 of 20 calls still over voices + 1, no new mixing of named people, a wrong person for about 1 in 36 far-off lines, Other holding a median 1.6% of the other side's speech.

OWNER DECISION: how hard to hide small speakers (bounds from "Calls without voice prints: the bound"). Options: (a) as asked, 10 s, strict rule (0.55, 0.1 ahead): median meets voices + 1, 8 of 20 calls stay over, a wrong person rarely (1 of 36); the expected count on the worst past calls is 10 (09.11 and the 37-cluster 13.11 call), so "worst under 10" is not met on those two; a new voice shows as "Other voices" for up to 10 s; (b) 20 s, same rule: bound at most 8 over all 91 calls since 29.10, so "worst under 10" holds on every past call, but more quiet people folded into Other and a new voice shows as "Other voices" for up to 20 s; (c) 30 s: bound at most 7, more quiet people hidden, up to 30 s as "Other voices". Recommendation: (a) with `speaker_tiny_s` as a setting, and revisit with two weeks of calls; take (b) if the worst-under-10 line must hold on every past call.

## Risk for saved voice matches and named speakers on old calls

- Old calls are not rewritten (`rules/voices.yaml > people.past`). The settle step runs only inside a live `SpeakerTracker` at End call, never on load, never in `fromfile.py edit`. A call recorded before the change keeps its `S<n>` speakers and names exactly as saved; the readers only gain the Other label, which old files never carry, so an old file renders the same.
- Named speakers: the settle never moves a named speaker (a name is the PM's, Meet's or the roster's decision), never merges into a named speaker when the library places the voice with another person, and never merges two named speakers.
- Voice library: it learns only from named speakers through `voice_core` (lines at mean cosine 0.5 or more to the speaker's other lines; nothing when under half are core). A small speaker merged into a named one adds at most a few lines, and lines outside the core are not learned; the Other bucket is never learned. `lib_added` keeps what was added, so a later edit does not add a print twice.
- Wrong merges: about 1 in 36 far-off lines goes to another named person with the proposed rule (9 in 36 with the plain nearest rule). Such a line is attributed to the wrong person in notes and follow-ups. It must be fixable on the call page (Said by on a line, as today), and `auto_merged` lets a later fix undo it.
- The replay ran without the voice library. On a real call the library can also open a speaker (`_lib_other` in `observe`) or name one; on 18 of 20 calls the no-library replay matched the recorded count, so it did not matter there, but it is not proven for every call.
- Not measured by replay: the three 8-person calls of 13.11 and the 50-cluster calls (only an upper bound from `speakers.json`); the 2 synthetic seeds where the live clustering already mixed two similar voices end as one speaker after the settle (the settle cannot repair a live mix).

## Implementation steps

Each step is its own task, core work (Opus, full review rounds). Tests are pytest under `spikes/m0/tests/` unless said otherwise.

1. End call settle (recorder). `SpeakerTracker` gets the settle step at End call and the Other bucket; settings `speaker_tiny_s` 10, `speaker_settle_min` 0.55, `speaker_settle_margin` 0.1 in `DEFAULTS` and `rules/voices.yaml`.
   Acceptance: a test with one synthetic voice whose lines include 15% far-off lines ends with 1 speaker plus at most Other (fails on today's code with several); a small speaker is never merged into a named one the library places with someone else; two named speakers never merge; the similar-voices test still passes (0.85) and a new test checks that nothing merges before End call; `merged` and `auto_merged` written; a test that in `finalize` the settle runs before `_lib_sync` and before `named.md`, the notes and the follow-up are written (a merged small speaker appears in `named.md` under the voice's name, and the library gets no print from the Other bucket); the replay harness is kept under `private/` (not in the app repo) and a replay of the 20 saved calls with it (local, not in CI) gives median clusters minus voices 1 or less and the worst under 10; the same harness computes the bound of "Calls without voice prints" from `speakers.json` for 09.11, 12.11 and the three 13.11 calls and reports it against "under 10": under option (a) the report shows the bounds (10, 8, 10, 4, 7 at 10 s) and "worst long call under 10" is closed by the live check below; under option (b) the bound is under 10 on all five calls.
   Live check after release: the first call with 8 or more people and the first call with 50 minutes or more of other-side speech recorded with the fix are counted (clusters, voices, share in Other) and added to this report; the owner's "worst long call under 10" is closed on them.
2. Readers show Other, and small live speakers show as Other. `lt`, `read_delta.py`, engine `SpeakerNames`, `named.md`, `CallSpeakers.swift`, the tips window (`feed.js`/`tips.js`) and the call page show "Other voices" / "Інші голоси" (i18n en and uk together); Other is not offered for naming in "Who is speaking?".
   Acceptance: CoreChecks for `CallSpeakers` with an old `speakers.json` (no Other: the same display as today) and a new one (Other shown once); pytest for `read_delta.name_lines` both ways; a feed test for the chip; docs EN/UK (`live-tips.md`), CHANGELOG.
3. No `S<n>` in tips. `SpeakerNames.who` gives "the client" or "someone on their side" for an unnamed voice by side; the tips prompt rule says so; a guard rewrites any `S\d+` / `Speaker \d+` left in a tip.
   Acceptance: a pytest that builds the prompt for a call with an unnamed speaker and finds no `S<n>` in it; a pytest that a model tip "S3 asked about the price" is shown as "the client asked about the price"; the skill path (`read_delta.py`) the same.
4. Imported recordings. `fromfile.py`'s offline `cluster()` applies the same small-speaker rule.
   Acceptance: a pytest on synthetic prints with 15% far-off lines (one voice) gives one speaker plus at most Other.
5. Split mixed lines (recorder, speaker worker). Prints of 1.5 s windows inside a line longer than about 4 s; a voice change where adjacent windows fall under the join threshold; the line is split at the nearest word boundary (Whisper word times) before it gets a speaker.
   Acceptance: first a measurement on recordings the owner allows (share of lines with a change), then a pytest with two synthetic voices in one line that gives two lines with two speakers; no change for one-voice lines; the live tip latency budget unchanged.
6. Old calls stay as saved. Fixture tests: an old `speakers.json` (S ids, names, merged, overrides, no Other) renders the same transcript, names and notes before and after steps 1-3; the voice library file is byte-identical after opening and editing an old call without a voice action.

## Not checked in this investigation

- Calls with 8 or more people by replay (the three 8-person calls of 13.11 have no voice prints; only a bound).
- The 50-cluster calls themselves (no voice prints; only a bound).
- The `55b64a4` model pin (19.11): all 20 replayable calls are after it, so the replay cannot rule out an effect of that change on clustering.
- How often a line holds two voices (one print per line in the saved data).
- The voice library's effect on the replay (run with `library=None`).
- The wrong-merge rate is a proxy on 36 named lines from 20 calls, not on real small clusters.
- Imported recordings (`fromfile.py`, its own offline `cluster()`): whether they also get many small speakers.
