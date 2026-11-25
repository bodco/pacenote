"""PB-104 (rules/recording.yaml > muted_mic): the PM muted the mic in Pacenote 7 s after the start on 24.11 and the call
ran 39 min muted with no warning; the tips ran blind to the PM's speech. The recorder warns (60 s of the other side's
speech since the mute, at most every 5 min), the transcript and the notes mark the muted span, and the engine calls a
question to the PM from that span "not heard", never "unanswered"."""
import importlib.util
import threading
import sys
from pathlib import Path

import pytest

from livetips_m0 import callnotes
from livetips_m0.engine import Engine, picture_items
from livetips_m0.transcript import mute_marker, muted_spans

SRC = Path(__file__).resolve().parents[3] / "recorder/src/transcriber.py"

MUTE_AT, MUTED_FOR = 7, 39 * 60          # 24.11: muted 7 s after the start, 39 min muted


@pytest.fixture(scope="module")
def T():
    spec = importlib.util.spec_from_file_location("lt_transcriber_pb104", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lt_transcriber_pb104"] = mod
    spec.loader.exec_module(mod)
    return mod


def replay(T, them_talks, start_muted=MUTE_AT, muted_for=MUTED_FOR, end=MUTE_AT + MUTED_FOR + 300):
    """The recorder's loop every 2 s: the other side's talk seconds since the mute, the warnings it raises."""
    warned, last, them, them0, was = [], None, 0.0, 0.0, False
    for t in range(0, end + 1, 2):
        muted = start_muted <= t < start_muted + muted_for
        if muted and not was:
            them0 = them                               # the recorder's mute_track at the mute
        was = muted
        if them_talks(t):
            them += 2
        if T.muted_warning(muted=muted, them_talk_s=them - them0 if muted else 0.0, now_t=t, last_warn=last):
            warned.append(t)
            last = t
    return warned


def test_24_11_run_warns_a_minute_after_the_mute_and_every_5_min_after(T):
    warned = replay(T, lambda t: True)
    assert 58 <= warned[0] - MUTE_AT <= 62            # 60 s of the other side talking since the mute (2 s ticks)
    assert len(warned) == 8                            # 39 min muted: once, then every 5 min
    assert all(b - a >= 300 for a, b in zip(warned, warned[1:]))
    assert all(w < MUTE_AT + MUTED_FOR for w in warned)


def test_the_warning_counts_only_the_other_sides_speech(T):
    # they talk 1 s in 3: 60 s of their speech take about 3 min
    warned = replay(T, lambda t: t % 6 == 0)
    assert warned and warned[0] - MUTE_AT >= 170
    assert replay(T, lambda t: False) == []          # muted, nobody talks: nothing to miss
    assert replay(T, lambda t: True, muted_for=0) == []   # never muted


def test_recorder_publishes_the_warning_in_status(T):
    """The recorder's loop writes the three status fields the app reads."""
    assert hasattr(T, "muted_warning")
    src = SRC.read_text(encoding="utf-8")
    assert "mic_muted_warn=" in src and "muted_them_s=" in src and "mic_muted_s=" in src


# ---- transcript markers: the recorder writes them, the engine and the notes read them

def test_recorder_markers_parse_both_languages():
    src = SRC.read_text(encoding="utf-8")
    for uk, en in (("мікрофон вимкнено", "microphone muted"), ("мікрофон увімкнено", "microphone on"),
                   ("мікрофон вимкнено до кінця дзвінка", "microphone muted until the end of the call")):
        assert f'"{uk}"' in src and f'"{en}"' in src
    assert mute_marker("--- microphone muted 10:00:07 ---") == (True, "10:00:07")
    assert mute_marker("--- мікрофон вимкнено 10:00:07 ---") == (True, "10:00:07")
    assert mute_marker("--- microphone on 10:39:07 ---") == (False, "10:39:07")
    assert mute_marker("--- мікрофон увімкнено 10:39:07 ---") == (False, "10:39:07")
    assert mute_marker("--- microphone muted until the end of the call 10:39:07 ---") == (False, "10:39:07")
    assert mute_marker("--- мікрофон вимкнено до кінця дзвінка 10:39:07 ---") == (False, "10:39:07")
    assert mute_marker("--- microphone froze, restarting it 10:01:00 ---") is None
    assert mute_marker("[10:00:09] Mike (en): microphone muted") is None


def call_24_11() -> str:
    """A synthetic 39-min muted call shaped like the 24.11 run (American names only)."""
    rows = ["# Weekly sync", "[10:00:00] Me (en): Hi everyone, let's start.", "--- microphone muted 10:00:07 ---"]
    for m in range(1, 39):
        who = ("Mike", "Emily", "Sarah", "David")[m % 4]
        rows.append(f"[10:{m:02d}:10] {who} (en): Can you confirm the release date for item {m}?" if m % 5 == 0
                    else f"[10:{m:02d}:10] {who} (en): An update on item {m}.")
    rows += ["--- microphone muted until the end of the call 10:39:07 ---", "", "--- end of recording 10:39:08 ---"]
    return "\n".join(rows) + "\n"


def test_transcript_muted_span_of_the_24_11_run():
    assert muted_spans(call_24_11()) == [["10:00:07", "10:39:07"]]
    two = "--- microphone muted 10:00:07 ---\n--- microphone on 10:05:00 ---\n--- мікрофон вимкнено 10:20:00 ---\n[10:21:00] Mike (en): ok\n"
    assert muted_spans(two) == [["10:00:07", "10:05:00"], ["10:20:00", None]]


def test_notes_mark_the_muted_span():
    sec = "## Meeting notes\n### TL;DR\nThe weekly sync.\n### Open questions\n- [10:25:10] Release date for item 25\n"
    out = callnotes.with_muted(sec, callnotes.muted_items(call_24_11(), "en"))
    unclear = callnotes.parse_meeting(out)["unclear"]
    assert len(unclear) == 1 and unclear[0]["t"] == "10:00:07"
    assert "muted in Pacenote until 10:39:07 (39 min)" in unclear[0]["text"]
    uk = callnotes.muted_items(call_24_11(), "uk")[0]
    assert "вимкнений у Pacenote до 10:39:07 (39 хв)" in uk
    # a span still open (no closing marker): it runs to the last line
    open_span = "--- microphone muted 10:00:07 ---\n[10:12:00] Mike (en): bye\n"
    assert "until 10:12:00 (12 min)" in callnotes.muted_items(open_span, "en")[0]
    assert callnotes.with_muted(sec, callnotes.muted_items("[10:00:00] Me (en): hi\n", "en")) == sec
    # the models are told too: a question to the PM in the span is not "not answered"
    assert "not heard (mic muted)" in callnotes.PART_SYS and "microphone muted" in callnotes.MEETING_SYS


def test_sheet_check_knows_the_pms_own_questions_were_not_heard():
    """The owner's planned questions (Meeting Sheet) asked while muted are not "not" by default."""
    from livetips_m0.sheet import aftercheck
    qs = [{"id": "q1", "text": "When is the release?"}]
    user = aftercheck.build_user(qs, [], call_24_11(), set())
    assert "THE PM'S MICROPHONE WAS MUTED IN PACENOTE: 10:00:07 to 10:39:07" in user
    assert "MUTED" not in aftercheck.build_user(qs, [], "[10:00:00] Me (en): When is the release?\n", set())


# ---- engine

def bare_engine() -> Engine:
    e = Engine.__new__(Engine)
    e.lock = threading.RLock()
    e.mute_spans = []
    e.agenda_items, e.open_commitments, e.commit_proposed, e.onme_fixed, e.onme_mine = [], [], set(), {}, []
    return e


def test_engine_reads_the_muted_span_and_asks_from_it_are_not_heard():
    e = bare_engine()
    for raw in call_24_11().splitlines():
        mk = mute_marker(raw)
        if mk:
            e.mic_mute(*mk)
    e.mic_mute(True, "10:00:07")                       # the file read again after the end-of-call rewrite
    assert e.mute_spans == [["10:00:07", "10:39:07"]]
    assert e.muted_at("10:25:10") and not e.muted_at("10:00:00") and not e.muted_at("10:45:00")
    sit = {"asks": [{"t": "10:25:10", "who": "Mike", "what": "confirm the release date", "type": "question"},
                    {"t": "10:00:00", "who": "Emily", "what": "share the agenda", "type": "request"}]}
    marked = e.mark_unheard(sit)
    assert marked["asks"][0].get("unheard") is True and "unheard" not in marked["asks"][1]
    items = picture_items(marked, False)
    assert items == ["Not heard (mic muted in Pacenote): confirm the release date · Mike",
                     "Unanswered: share the agenda · Emily"]
    assert picture_items(marked, True)[0].startswith("Не почуто (мікрофон вимкнено в Pacenote): ")
    assert "THE PM'S MICROPHONE WAS MUTED IN PACENOTE" in e._situation_extra_input()
    assert "10:00:07 to 10:39:07" in e._situation_extra_input()


def test_engine_without_a_mute_is_unchanged():
    e = bare_engine()
    sit = {"asks": [{"t": "10:25:10", "who": "Mike", "what": "confirm the release date", "type": "question"}]}
    assert e.mark_unheard(sit) is sit
    assert picture_items(sit, False) == ["Unanswered: confirm the release date · Mike"]
    assert "MUTED" not in e._situation_extra_input()
    e.mic_mute(True, "10:00:07")                       # muted now, span still open
    assert e.muted_at("10:30:00")
    assert "10:00:07 to now" in e._situation_extra_input()


# ---- review r0: every reader of the open asks honors "unheard" (acceptance line 4)

MUTED_SIT = {"kind": "situation", "decisions": [], "actions": [],
             "asks": [{"t": "10:25:10", "who": "Mike", "what": "confirm the release date", "type": "question", "unheard": True},
                      {"t": "10:00:00", "who": "Emily", "what": "share the agenda", "type": "request"}]}


def test_followup_draft_does_not_call_an_unheard_ask_open():
    from livetips_m0.followup import picture_lines
    lines = picture_lines(MUTED_SIT)
    assert "Open: share the agenda" in lines
    assert "Open: confirm the release date" not in lines
    assert any(l.startswith("Not heard (the PM's mic was muted in Pacenote") and l.endswith("confirm the release date")
               for l in lines)


def test_pn_tools_call_facts_skip_unheard_asks(tmp_path):
    import json
    from livetips_m0 import pn_tools
    from livetips_m0.call_lists import tips_paths
    md = tmp_path / "2026-11-24_1000_weekly.md"
    md.write_text(call_24_11(), encoding="utf-8")
    tp = tips_paths(md)[0]
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(json.dumps(MUTED_SIT) + "\n", encoding="utf-8")
    _, rows = pn_tools.call_facts(md)
    assert [r[2] for r in rows if r[0] == "open"] == ["share the agenda"]


def test_live_tips_model_does_not_get_unheard_asks_as_open():
    e = bare_engine()
    e.tips, e.tip_ratings = [], {}
    e.situation = MUTED_SIT
    e.clock = lambda: 36000.0
    assert e._state()["situation"]["open_asks_to_pm"] == ["share the agenda"]


def test_situation_model_is_told_to_keep_muted_span_asks_only_when_muted():
    e = bare_engine()
    assert "Keep an ask" not in e._situation_extra_input()
    e.mic_mute(True, "10:00:07")
    assert "Keep an ask to the PM from these times in asks" in e._situation_extra_input()
