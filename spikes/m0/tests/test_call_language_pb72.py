"""PB-72: the recorder saves the measured call languages (call_layout.save_spoken) when the recording ends and again
after the end-of-call edits. The dominant language is None in the first minute; the totals survive a mid-call language
switch; a line deleted at the end no longer counts. The readers (call_language.py) see what was saved."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from livetips_m0 import call_language as CL
from test_call_language_pb30 import make, run, seg_of       # the PB-30 fake Whisper and recorder path

REPO = Path(__file__).absolute().parents[3]
SRC = REPO / "recorder" / "src" / "transcriber.py"
T0 = 1_700_000_000.0
WRONG_TAGS = "".join(f"[10:00:0{i}] Me (en): line\n" for i in range(6))     # every line tag says English


@pytest.fixture(scope="module")
def T():
    spec = importlib.util.spec_from_file_location("lt_transcriber_pb72", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lt_transcriber_pb72"] = mod
    spec.loader.exec_module(mod)
    return mod


def _call(tmp_path, text=WRONG_TAGS):
    d = tmp_path / "live" / "2026-11-25-1000-orbit"
    d.mkdir(parents=True)
    t = d / "2026-11-25-1000-orbit.app.md"
    t.write_text(text, "utf-8")
    return t


def _meta(t):
    return json.loads(CL.meta_path(t).read_text("utf-8"))


def test_end_of_call_save_writes_seconds_and_the_dominant_language_both_readers_use(T, tmp_path):
    s = T.LangShare(["en", "uk"], warmup_s=60)
    s.dominant(T0)                                    # the first segment starts the call
    s.add("uk", 1260.5)
    s.add("en", 310.2)
    t = _call(tmp_path)
    assert T.save_call_languages(t, s, T0 + 1800)
    d = _meta(t)
    assert d["languages"] == {"en": 310.2, "uk": 1260.5} and d["dominant_language"] == "uk"
    assert CL.output_language(t, WRONG_TAGS) == "uk"                     # measured speech, not the line tags


def test_dominant_is_none_in_the_first_minute_but_the_seconds_are_kept(T, tmp_path):
    s = T.LangShare(["en", "uk"], warmup_s=60)
    s.dominant(T0)
    s.add("uk", 40.0)
    assert s.spoken(T0 + 59) == ({"uk": 40.0}, None)
    assert s.spoken(T0 + 60) == ({"uk": 40.0}, "uk")
    t = _call(tmp_path)
    T.save_call_languages(t, s, T0 + 45)
    d = _meta(t)
    assert d["languages"] == {"uk": 40.0} and "dominant_language" not in d
    assert T.LangShare(["uk"]).spoken(T0 + 600) == ({}, None)              # no speech: no language either


def test_totals_survive_a_mid_call_language_switch(T, tmp_path):
    s = T.LangShare(["uk", "en"], warmup_s=60)
    s.dominant(T0)
    s.add("uk", 900.0)
    s.add("en", 100.0)
    s.reset(["de", "en"])                             # the PM switches the call to German + English
    assert s.secs == {}                               # the live share starts again (PB-30, unchanged)
    s.dominant(T0 + 1000)
    s.add("de", 300.0)
    s.add("uk", 50.0)                                 # not in the new set: not counted anywhere
    assert s.spoken(T0 + 1300) == ({"uk": 900.0, "en": 100.0, "de": 300.0}, "uk")
    assert s.spoken(T0 + 1010)[1] == "uk"             # the warm-up counts from the call start, not the switch
    t = _call(tmp_path)
    T.save_call_languages(t, s, T0 + 1300)
    assert _meta(t)["languages"] == {"de": 300.0, "en": 100.0, "uk": 900.0}


def test_after_the_end_of_call_edits_a_deleted_line_no_longer_counts(T, tmp_path, monkeypatch):
    monkeypatch.setattr(T, "LINE_EDITS", {"del": {("10:01:00", "Let us talk")}, "who": {("10:00:00", "Привіт"): "Mike"}})
    text = ("[10:00:00] Them (uk): Привіт, почнемо\n"
            "[10:01:00] Me (en): Let us talk about the budget (← uk)\n"
            "[10:02:00] Me (uk): Добре\n")
    t = _call(tmp_path, text)
    s = T.LangShare(["uk", "en"], warmup_s=60)
    s.dominant(T0)
    credits = [("10:00:00", "Привіт, почнемо", "uk", 100.0),
               ("10:01:00", "Let us talk about the budget", "en", 400.0),
               ("10:02:00", "Добре", "uk", 200.0)]
    for _, _, lang, secs in credits:
        s.add(lang, secs)
    T.save_call_languages(t, s, T0 + 900)
    assert _meta(t)["dominant_language"] == "en"
    dropped = []
    assert T.apply_line_edits(t, dropped) == 2
    assert dropped == [("10:01:00", "Let us talk about the budget (← uk)")]
    assert T.uncount_dropped(s, credits, dropped) == 1
    T.save_call_languages(t, s, T0 + 900)
    d = _meta(t)
    assert d["languages"] == {"uk": 300.0} and d["dominant_language"] == "uk"
    assert "Mike (uk)" in t.read_text("utf-8")


def test_a_deleted_row_that_was_translated_matches_the_line_of_its_second(T):
    s = T.LangShare(["uk", "en"])
    s.add("uk", 50.0)
    s.add("en", 30.0)
    credits = [("10:00:05", "Добрий день", "uk", 50.0), ("10:00:09", "Hello", "en", 30.0)]
    assert T.uncount_dropped(s, credits, [("10:00:05", "Good afternoon (← uk)"), ("10:09:00", "gone")]) == 1
    assert s.total == {"uk": 0.0, "en": 30.0} and s.spoken(0)[0] == {"en": 30.0}


def test_the_recorder_path_credits_each_line_with_the_seconds_it_added(T):
    s = T.LangShare(["uk"], warmup_s=0)
    assert s.add("uk", 2.5) == 2.5 and s.add("fr", 9.0) == 0.0 and s.add(None, 1.0) == 0.0
    assert T.Decoded(("a", "uk", None)).credit == (None, 0.0)
    w = make(T, ["uk", "en"], says={"en": "Sure, go ahead"}, probs={"uk": 0.05, "en": 0.9})
    res, share = run(T, w, seg_of(T, 0.05, 1.0), last="uk")              # clearly English: counted
    assert res.credit == ("en", 1.0) and share.total == {"en": 1.0}
    w = make(T, ["uk", "en"], says={"en": "I would like a coffee please"}, probs={"uk": 0.05, "en": 0.15, "fr": 0.7})
    res, share = run(T, w, seg_of(T, 0.05, 3.0), dominant="en")           # French written in English: not counted
    assert res[2] == "fr" and res.credit == (None, 0.0) and share.total == {"en": 100.0}
    w = make(T, ["uk", "en"], says={"uk": "Так, добре"}, probs={"uk": 0.4, "en": 0.45})
    res, share = run(T, w, seg_of(T, 0.05, 1.0), last="uk")              # unclear: no evidence, nothing to undo
    assert res.credit[1] == 0.0 and share.total == {}


def test_no_speech_and_no_recording_writes_no_file(T, tmp_path):
    t = _call(tmp_path)
    assert T.save_call_languages(t, T.LangShare(["uk"]), T0 + 600) is False
    assert not CL.meta_path(t).exists()


def test_a_failed_write_is_logged_and_does_not_raise(T, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(T.call_layout, "save_spoken", boom)
    s = T.LangShare(["uk"])
    s.add("uk", 5.0)
    assert T.save_call_languages(_call(tmp_path), s, T0) is False


def test_a_deleted_translated_row_sharing_its_second_with_another_language_takes_nothing_out(T):
    s = T.LangShare(["uk", "en"])
    s.add("uk", 50.0)
    s.add("en", 30.0)
    credits = [("10:00:05", "Добрий день", "uk", 50.0), ("10:00:05", "Hello there", "en", 30.0)]
    assert T.uncount_dropped(s, credits, [("10:00:05", "Good afternoon (← uk)")]) == 0
    assert s.total == {"uk": 50.0, "en": 30.0} and len(credits) == 2       # a stale count, not the wrong language
    assert T.uncount_dropped(s, credits, [("10:00:05", "Hello there")]) == 1   # a word match still works
    assert s.total == {"uk": 50.0, "en": 0.0}


def test_a_failing_read_of_the_totals_is_logged_and_does_not_stop_the_shutdown(T, tmp_path, monkeypatch):
    s = T.LangShare(["uk"])
    s.add("uk", 5.0)

    def boom(t):
        raise RuntimeError("dictionary changed size during iteration")
    monkeypatch.setattr(s, "spoken", boom)
    assert T.save_call_languages(_call(tmp_path), s, T0) is False


def test_the_recorder_path_starts_the_call_clock_for_the_warm_up(T):
    w = make(T, ["uk", "en"], says={"en": "Sure, go ahead"}, probs={"uk": 0.05, "en": 0.9})
    _, share = run(T, w, seg_of(T, 0.05, 1.0), last="uk")
    assert share.call_t0 is not None                  # set by asr_segment itself, not only by the tests


def test_main_saves_the_languages_after_the_recording_ends_and_again_after_the_edits():
    src = SRC.read_text(encoding="utf-8")
    main = src[src.index("\ndef main() -> int:"):]
    assert main.index("lang_credits.append(") < main.index("writer.add(line_obj)")
    close = main.index("L('кінець запису', 'end of recording')")
    end = main.index("call_end = now()")
    first = main.index("save_call_languages(out, lang_share, call_end)")
    assert close < end < first < main.index("sheet_live.save_state(out)")
    edits = main.index("apply_line_edits(out, dropped)")
    unc = main.index("uncount_dropped(lang_share, lang_credits, dropped)", edits)
    assert first < edits < unc < main.index("save_call_languages(out, lang_share, call_end)", unc)   # same end time
