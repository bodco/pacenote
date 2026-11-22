"""PB-30 (D56), review round 13: a row the end-of-call rewrite translated before the engine first read it is fed once
(matched to its seq by the "translated" event), and a line from outside the set is tagged and translated to the
language it was decoded for, not to the language of the script Whisper happened to write."""
import numpy as np
import pytest

from test_call_language_pb30 import T, make  # noqa: F401  (T is a fixture)
from test_round11_pb30 import call

AUDIO = np.ones(16000, dtype=np.float32) * 0.1


# ---- blocker 1: the first poll after the rewrite ------------------------------------------------------------------

@pytest.mark.parametrize("new", ["Hi", "Hello, I would like a cup of coffee with milk and no sugar, please"])
def test_a_row_rewritten_before_the_engine_read_it_is_fed_once_with_its_seq(T, tmp_path, new):
    md, eng, run, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    ln = T.Line("them", t0, "en", "Je voudrais un cafe", sid="S1", translated_from="fr", seq=1)
    w.add(ln)
    w.add(T.Line("them", t0 + 5, "en", "Okay, sounds good", sid="S1", seq=2))
    event(t=hms, ch="them", seg_end=hms, written=hms)
    event(t=T.hhmmss(t0 + 5), ch="them", seg_end=hms, written=hms)
    original = ln.text
    w.replace(ln, new)
    event(event="translated", seq=1, t=hms, ch="them", translated_from="fr", original=original, text=new)
    w.close("\n")                                           # the engine has not polled yet
    run.step()
    run.step()
    assert [(h.seq, h.text) for h in eng.history] == [(1, new), (2, "Okay, sounds good")]


def test_identical_rows_one_translated_before_the_first_read_are_both_fed_once(T, tmp_path):
    md, eng, run, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    a = T.Line("them", t0, "en", "Bonjour", sid="S1", translated_from="fr", seq=1)
    b = T.Line("them", t0, "en", "Bonjour", sid="S1", translated_from="fr", seq=2)
    w.add(a)
    w.add(b)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    w.replace(b, "Hello")                                   # only the second one is translated
    event(event="translated", seq=2, t=hms, ch="them", translated_from="fr", original="Bonjour", text="Hello")
    w.close("\n")
    run.step()
    run.step()
    assert [(h.seq, h.stag, h.text) for h in eng.history] == [(1, "S1", "Bonjour"), (2, "S1", "Hello")]


@pytest.mark.parametrize("first", ["S1", "S2"])
def test_two_speakers_one_translated_to_the_other_ones_words_keep_their_own_seq(T, tmp_path, first):
    """Review round 6 (PB-30): S1 says "Bonjour", S2 says "Hello" in the same second; S1's line is translated to "Hello"
    before the engine's first poll. Each row keeps its own seq and speaker, whichever of the two was written first."""
    md, eng, run, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    fr = T.Line("them", t0, "en", "Bonjour", sid="S1", translated_from="fr")
    en = T.Line("them", t0, "en", "Hello", sid="S2")
    order = [fr, en] if first == "S1" else [en, fr]
    for seq, ln in enumerate(order, 1):
        ln.seq = seq
        w.add(ln)
        event(t=hms, ch="them", seg_end=hms, written=hms)
    w.replace(fr, "Hello")
    event(event="translated", seq=fr.seq, t=hms, ch="them", translated_from="fr", original="Bonjour", text="Hello")
    w.close("\n")
    run.step()
    run.step()
    want = sorted([(fr.seq, "S1", "Hello"), (en.seq, "S2", "Hello")])
    assert [(h.seq, h.stag, h.text) for h in eng.history] == want


def test_a_row_rewritten_to_the_text_of_a_real_later_row_is_not_dropped(T, tmp_path):
    md, eng, run, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    a = T.Line("them", t0, "en", "Bonjour", sid="S1", translated_from="fr", seq=1)
    w.add(a)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    w.replace(a, "Hello")
    event(event="translated", seq=1, t=hms, ch="them", translated_from="fr", original="Bonjour", text="Hello")
    w.add(T.Line("them", t0, "en", "Hello", sid="S1", seq=2))   # the writer appended a real "Hello" row too
    event(t=hms, ch="them", seg_end=hms, written=hms)
    w.close("\n")                                           # the file now has "Hello" twice
    run.step()
    run.step()
    assert [(h.seq, h.stag, h.text) for h in eng.history] == [(1, "S1", "Hello"), (2, "S1", "Hello")]


# ---- blocker 2: the target language does not follow the script Whisper wrote ---------------------------------

def test_french_with_english_dominant_written_in_ukrainian_letters_is_tagged_and_sent_to_english(T):
    w = make(T, ["uk", "en"], says={"en": "Мені потрібна кава"}, probs={"fr": 0.8, "en": 0.1, "uk": 0.1})
    lang, heard, foreign = w.choose(AUDIO, None, "en")
    assert (lang, foreign) == ("en", "fr")
    assert tuple(w.decode(AUDIO, lang, None, "en", foreign)) == ("Мені потрібна кава", "en", "fr")
    assert T.needs_translation("Мені потрібна кава", "en", "fr")
    assert not T.translation_ok("Мені потрібна кава", "en")   # a worker answer that kept the Ukrainian is not used


def test_french_with_ukrainian_dominant_written_in_english_is_sent_to_ukrainian(T):
    w = make(T, ["uk", "en"], says={"uk": "I would like a coffee"}, probs={"fr": 0.8, "en": 0.1, "uk": 0.1})
    lang, heard, foreign = w.choose(AUDIO, None, "uk")
    assert tuple(w.decode(AUDIO, lang, None, "uk", foreign)) == ("I would like a coffee", "uk", "fr")
    assert T.needs_translation("I would like a coffee", "uk", "fr")


def test_ukrainian_heard_as_russian_with_english_dominant_stays_ukrainian(T):
    w = make(T, ["uk", "en"], says={"en": "Це дуже добре, дякую"}, probs={"ru": 0.8, "en": 0.1, "uk": 0.1})
    lang, heard, foreign = w.choose(AUDIO, None, "en")
    text, tag, src = w.decode(AUDIO, lang, None, "en", foreign)
    assert (tag, src) == ("uk", "ru")                       # a language of the set, kept as spoken
    assert not T.needs_translation(text, tag, src)


def test_russian_with_english_dominant_is_tagged_and_sent_to_english(T):
    w = make(T, ["uk", "en"], says={"en": "Это очень хорошо, спасибо"}, probs={"ru": 0.8, "en": 0.1, "uk": 0.1})
    lang, heard, foreign = w.choose(AUDIO, None, "en")
    text, tag, src = w.decode(AUDIO, lang, None, "en", foreign)
    assert (tag, src) == ("en", "ru")
    assert T.needs_translation(text, tag, src)
