"""PB-30 (D56), review round 7: an engine that joined mid-call never gives a skipped row's seq to a later row, a known
out-of-set language is translated to the language it was decoded for (Belarusian heard on an English-dominant call
goes to English), and an answer that only copies part of the source is not a translation."""
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


# ---- blocker 1: a mid-call join and a rewrite that collides with a later row ---------------------------------------

@pytest.mark.parametrize("read_before_rewrite", [False, True])
def test_a_skipped_row_translated_to_a_later_rows_words_never_takes_its_seq(T, tmp_path, read_before_rewrite):
    """S1's "Bonjour" is in the file before the engine joins; S2 says "Hello" in the same second; S1's row is then
    translated to "Hello". The engine feeds only S2's row, with S2's own seq."""
    md, eng0, run0, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    a = T.Line("them", t0, "en", "Bonjour", sid="S1", translated_from="fr", seq=1)
    w.add(a)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    eng = Eng()
    run = LiveRun({"me_label": T.DEFAULTS["me_label"]}, eng, md, from_start=False)    # joins after S1's row
    w.add(T.Line("them", t0, "en", "Hello", sid="S2", seq=2))
    event(t=hms, ch="them", seg_end=hms, written=hms)
    w.replace(a, "Hello")
    event(event="translated", seq=1, t=hms, ch="them", translated_from="fr", original="Bonjour", text="Hello")
    if read_before_rewrite:
        run.step()
    w.close("\n")
    run.step()
    run.step()
    assert [(h.seq, h.stag, h.text) for h in eng.history] == [(2, "S2", "Hello")]


# ---- blocker 2: Belarusian keeps the destination it was decoded for -----------------------------------------------

@pytest.mark.parametrize("dom", ["uk", "en"])
def test_belarusian_is_translated_to_the_dominant_language(T, dom):
    """Forced through English, Whisper can still write Belarusian in Cyrillic; the script must not turn the line's
    destination into Ukrainian."""
    w = make(T, ["uk", "en"], says={dom: "Дзякуй за дапамогу"}, probs={"be": 0.9, "uk": 0.05, "en": 0.05})
    lang, heard, foreign = w.choose(AUDIO, None, dom)
    assert (lang, heard, foreign) == (dom, None, "be")
    text, tag, src = w.decode(AUDIO, lang, None, dom, foreign)
    assert (tag, src) == (dom, "be")
    assert T.needs_translation(text, tag, src)


def test_ukrainian_heard_as_russian_still_keeps_its_own_tag(T):
    w = make(T, ["uk", "en"], says={"en": "Це дуже добре, дякую"}, probs={"ru": 0.9, "uk": 0.05, "en": 0.05})
    lang, heard, foreign = w.choose(AUDIO, None, "en")
    assert tuple(w.decode(AUDIO, lang, None, "en", foreign))[1] == "uk"


# ---- blocker 3: a copied part of the source is not a translation --------------------------------------------------

def test_a_foreign_excerpt_from_the_local_model_falls_through_to_the_route(monkeypatch):
    import transcriber as TT
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    tr = TR.Translator({"translate_cmd": ["x"]}, TT.translation_ok)
    monkeypatch.setattr(tr, "_ollama_up", lambda: True)
    monkeypatch.setattr(tr, "_ollama", lambda text, src, dst: "Bonne soirée")
    monkeypatch.setattr(tr, "_route", lambda text, src, dst: "Have a good evening")
    assert tr.translate("Passez une bonne soirée", "fr", "en") == "Have a good evening"


def test_a_foreign_excerpt_from_every_route_leaves_the_marked_line(monkeypatch):
    import transcriber as TT
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    tr = TR.Translator({"translate_cmd": ["x"]}, TT.translation_ok)
    monkeypatch.setattr(tr, "_ollama_up", lambda: True)
    monkeypatch.setattr(tr, "_ollama", lambda text, src, dst: "Bonne soirée")
    monkeypatch.setattr(tr, "_route", lambda text, src, dst: "bonne soiree!")
    assert tr.translate("Passez une bonne soirée", "fr", "en") is None
    applied = []
    wk = TR.TranslateWorker(lambda text, src, dst: "Bonne soirée", lambda j, new: applied.append(new),
                            TT.translation_ok)
    wk.start()
    wk.submit({"text": "Passez une bonne soirée", "src": "fr", "dst": "en"})
    wk.stop(5.0)
    assert applied == [] and wk.kept == 1


@pytest.mark.parametrize("src,new", [("Message important", "Important message"), ("Bonjour Ryan", "Hello Ryan"),
                                     ("Merci beaucoup", "Thank you very much")])
def test_real_translations_that_reuse_source_words_pass(src, new):
    import translate as TR
    assert not TR.copied(new, src)
