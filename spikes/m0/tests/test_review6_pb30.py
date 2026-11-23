"""PB-30 (D56), review round 6 (fix round 6): the end-of-call rewrite is matched to the rows already read by write
position, Belarusian on a Ukrainian call is marked and translated, and a translation that is still in another language
of the same script is not used."""
import sys
from pathlib import Path

import numpy as np
import pytest

from livetips_m0.live import LiveRun
from test_call_language_pb30 import T, make  # noqa: F401  (T is a fixture)
from test_round11_pb30 import Eng, call

RECORDER = Path(__file__).absolute().parents[3] / "recorder" / "src"
sys.path.insert(0, str(RECORDER))

AUDIO = np.ones(16000, dtype=np.float32) * 0.1


# ---- blocker 1: a partial read before the rewrite ------------------------------------------------------------------

@pytest.mark.parametrize("read_s2", [False, True])
def test_a_rewritten_row_never_takes_the_seq_of_another_speakers_unread_row(T, tmp_path, read_s2):
    """S1's "Bonjour" is read, then translated to "Hello"; S2 says "Hello" in the same second. Whether the engine read
    S2's row before the rewrite or not, each row keeps its own seq and speaker."""
    md, eng, run, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    fr = T.Line("them", t0, "en", "Bonjour", sid="S1", translated_from="fr", seq=1)
    w.add(fr)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    run.step()                                              # S1's row is read before the translation
    w.add(T.Line("them", t0, "en", "Hello", sid="S2", seq=2))
    event(t=hms, ch="them", seg_end=hms, written=hms)
    if read_s2:
        run.step()
    w.replace(fr, "Hello")
    event(event="translated", seq=1, t=hms, ch="them", translated_from="fr", original="Bonjour", text="Hello")
    w.close("\n")
    run.step()
    run.step()
    assert sorted((h.seq, h.stag, h.text) for h in eng.history) == [(1, "S1", "Hello"), (2, "S2", "Hello")]


def test_an_unread_row_translated_after_a_partial_read_is_fed_once_with_its_text(T, tmp_path):
    md, eng, run, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    a = T.Line("them", t0, "en", "Bonjour", sid="S1", translated_from="fr", seq=1)
    w.add(a)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    run.step()
    b = T.Line("them", t0, "en", "Merci", sid="S2", translated_from="fr", seq=2)
    w.add(b)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    for ln, new in ((a, "Hello"), (b, "Thank you")):
        orig = ln.text
        w.replace(ln, new)
        event(event="translated", seq=ln.seq, t=hms, ch="them", translated_from="fr", original=orig, text=new)
    w.close("\n")
    run.step()
    run.step()
    assert [(h.seq, h.stag, h.text) for h in eng.history] == [(1, "S1", "Hello"), (2, "S2", "Thank you")]


def test_an_engine_that_joined_mid_call_does_not_replay_the_earlier_rows_on_the_rewrite(T, tmp_path):
    md, eng0, run0, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    a = T.Line("them", t0, "en", "Bonjour", sid="S1", translated_from="fr", seq=1)
    w.add(a)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    eng = Eng()
    run = LiveRun({"me_label": T.DEFAULTS["me_label"]}, eng, md, from_start=False)    # joins after the first row
    later = T.hhmmss(t0 + 5)
    w.add(T.Line("them", t0 + 5, "en", "Okay", sid="S2", seq=2))
    event(t=later, ch="them", seg_end=later, written=later)
    run.step()
    w.replace(a, "Hello")
    event(event="translated", seq=1, t=hms, ch="them", translated_from="fr", original="Bonjour", text="Hello")
    w.close("\n")
    run.step()
    run.step()
    assert [(h.seq, h.text) for h in eng.history] == [(2, "Okay")]


# ---- blocker 2: Belarusian on a Ukrainian-dominant call ----------------------------------------------------------

def test_belarusian_on_a_ukrainian_call_is_marked_and_sent_to_ukrainian(T):
    w = make(T, ["uk", "en"], says={"uk": "Дзякуй за дапамогу"}, probs={"be": 0.9, "uk": 0.05, "en": 0.05})
    lang, heard, foreign = w.choose(AUDIO, None, "uk")
    assert (lang, foreign) == ("uk", "be")
    text, tag, src = w.decode(AUDIO, lang, None, "uk", foreign)
    assert (text, tag, src) == ("Дзякуй за дапамогу", "uk", "be")
    assert T.needs_translation(text, tag, src)
    assert not T.translation_ok("Дзякуй за дапамогу", "uk")       # an answer that kept the Belarusian is not used
    assert T.translation_ok("Дякую за допомогу", "uk")


def test_ukrainian_heard_as_russian_on_a_ukrainian_call_is_still_not_marked(T):
    w = make(T, ["uk", "en"], says={"uk": "Це дуже добре, дякую"}, probs={"ru": 0.9, "uk": 0.05, "en": 0.05})
    lang, heard, foreign = w.choose(AUDIO, None, "uk")
    assert (lang, foreign) == ("uk", None)
    assert tuple(w.decode(AUDIO, lang, None, "uk", foreign)) == ("Це дуже добре, дякую", "uk", None)


# ---- blocker 3: a same-script foreign answer is not a translation ------------------------------------------------

@pytest.mark.parametrize("bad", ["Je voudrais un café", "Je voudrais un cafe", "Ich möchte einen Kaffee, bitte",
                                 "Quiero un café con leche"])
def test_a_same_script_answer_in_another_language_is_not_a_translation(T, bad):
    assert not T.translation_ok(bad, "en")


@pytest.mark.parametrize("good", ["Hello", "Yes", "Thank you for the help", "I would like a coffee",
                                  "Okay, sounds good", "Let's meet in Paris on Monday",
                                  "Let's meet in Los Angeles on Monday", "I will call Las Vegas"])
def test_real_english_answers_pass(T, good):
    assert T.translation_ok(good, "en")


def test_a_french_local_answer_falls_through_to_the_route(monkeypatch):
    import transcriber as TT
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    tr = TR.Translator({"translate_cmd": ["x"]}, TT.translation_ok)
    monkeypatch.setattr(tr, "_ollama_up", lambda: True)
    monkeypatch.setattr(tr, "_ollama", lambda text, src, dst: "Je voudrais un café")      # a paraphrase, not English
    monkeypatch.setattr(tr, "_route", lambda text, src, dst: "I would like a coffee")
    assert tr.translate("Je veux un cafe", "fr", "en") == "I would like a coffee"


def test_french_from_every_route_leaves_the_marked_line(monkeypatch):
    import transcriber as TT
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    tr = TR.Translator({"translate_cmd": ["x"]}, TT.translation_ok)
    monkeypatch.setattr(tr, "_ollama_up", lambda: True)
    monkeypatch.setattr(tr, "_ollama", lambda text, src, dst: "Je voudrais un café")
    monkeypatch.setattr(tr, "_route", lambda text, src, dst: "Je voudrais un café, s'il vous plaît")
    assert tr.translate("Je veux un cafe", "fr", "en") is None
    applied = []
    wk = TR.TranslateWorker(lambda text, src, dst: "Je voudrais un café", lambda j, new: applied.append(new),
                            TT.translation_ok)
    wk.start()
    wk.submit({"text": "Je veux un cafe", "src": "fr", "dst": "en"})
    wk.stop(5.0)
    assert applied == [] and wk.kept == 1
