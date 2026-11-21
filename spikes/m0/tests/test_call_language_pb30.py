"""PB-30 (D56, roadmap FB1 + FB13): the call language. One language is forced; several are written as spoken with the
main one being the most spoken; a language outside the set and Russian are redone in the main one and marked
(translated_from); Whisper stop-phrases on near-silence are dropped. Whisper itself is replaced by a scripted fake."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

REPO = Path(__file__).absolute().parents[3]
SRC = REPO / "recorder" / "src" / "transcriber.py"
CFG = {"model": "fake", "langs": ["uk", "en"], "lang_bias": 2.0, "max_no_speech": 0.85}
AUDIO = np.ones(16000, dtype=np.float32) * 0.1


@pytest.fixture(scope="module")
def T():
    spec = importlib.util.spec_from_file_location("lt_transcriber_pb30", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lt_transcriber_pb30"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeMlx:
    """mlx_whisper.transcribe stand-in: `says` maps the forced language to the text Whisper writes for it."""

    def __init__(self, says):
        self.says = says
        self.calls = []

    def transcribe(self, audio, path_or_hf_repo=None, language=None, **kw):
        self.calls.append(language)
        lang = language or "en"
        text = self.says.get(language, self.says.get(None, ""))
        return {"language": lang, "segments": [{"text": text, "no_speech_prob": 0.01, "avg_logprob": -0.2,
                                                   "compression_ratio": 1.0}]}


def make(T, langs, says=None, probs=None):
    w = object.__new__(T.Whisper)
    w.mlx_whisper = FakeMlx(says or {})
    w.repo, w.langs = "fake", list(langs)
    w.bias, w.foreign_min, w.max_no_speech = 2.0, 0.5, 0.85
    w.detector = (lambda a: probs) if probs is not None else None
    return w


# ---- one language: forced, no autodetect -------------------------------------------------------------------------

def test_one_language_is_forced_without_detection(T):
    w = make(T, ["en"], probs={"uk": 0.9, "en": 0.05})       # the detector would say Ukrainian; it is not asked
    assert w.choose(AUDIO) == ("en", "en", None)
    assert make(T, ["uk"]).choose(AUDIO) == ("uk", "uk", None)


# ---- uk+en with mostly English ----------------------------------------------------------------------------------

def test_english_stays_english_once_english_is_the_main_language(T):
    w = make(T, ["uk", "en"], probs={"uk": 0.40, "en": 0.45})      # unclear: the main language wins
    assert w.choose(AUDIO, None, "uk")[0] == "uk"                  # warm-up: the first of the set
    assert w.choose(AUDIO, None, "en") == ("en", "en", None)       # after it: the most spoken one


def test_clear_other_language_is_written_as_spoken(T):
    w = make(T, ["uk", "en"], probs={"uk": 0.10, "en": 0.80})
    assert w.choose(AUDIO, None, "uk") == ("en", "en", None)
    w = make(T, ["uk", "en"], probs={"uk": 0.85, "en": 0.05})
    assert w.choose(AUDIO, None, "en") == ("uk", "uk", None)


def test_dominant_language_from_seconds_of_speech_after_warmup(T):
    s = T.LangShare(["uk", "en"], warmup_s=60)
    assert s.dominant(1000.0) == "uk"                  # the call starts
    s.add("en", 20.0)
    s.add("uk", 5.0)
    assert s.dominant(1030.0) == "uk"                  # still warming up: the first language
    assert s.dominant(1061.0) == "en"                  # 60 s in: English is the most spoken
    s.add("uk", 30.0)
    assert s.dominant(1100.0) == "uk"
    s.add("fr", 99.0)                                  # not in the set: not counted
    assert s.dominant(1100.0) == "uk"
    s.reset(["en"])
    assert s.dominant(2000.0) == "en"


# ---- en with a bit of French ------------------------------------------------------------------------------------

def test_french_on_an_english_call_is_decoded_in_english_and_marked(T):
    w = make(T, ["en"], says={"en": "I would like a coffee please"}, probs={})
    text, lang, tf = w.decode(AUDIO, "en", None, "en", "fr")
    assert (text, lang, tf) == ("I would like a coffee please", "en", "fr")
    assert w.mlx_whisper.calls == ["en"]                           # forced, never autodetected
    w = make(T, ["uk", "en"], probs={"uk": 0.05, "en": 0.15, "fr": 0.7})
    assert w.choose(AUDIO, None, "en") == ("en", None, "fr")       # outside the set: the main language, French reported
    assert w.choose(AUDIO, None, "uk") == ("uk", None, "fr")


def test_unclear_detection_is_not_a_foreign_language(T):
    w = make(T, ["uk", "en"], probs={"uk": 0.2, "en": 0.25, "fr": 0.3})
    assert w.choose(AUDIO, None, "en") == ("en", "en", None)


def test_foreign_language_that_still_comes_out_foreign_is_marked_not_dropped(T):
    # TODO(PB-30): no translation model; the line is kept as Whisper wrote it and marked
    w = make(T, ["en"], says={"en": "Je voudrais un cafe"})
    assert w.decode(AUDIO, "en", None, "en", None)[2] is None      # Latin on Latin: nothing to mark without a hint
    w = make(T, ["uk"], says={"uk": "I would like a coffee"})
    assert w.decode(AUDIO, "uk", None, "uk")[2] == "und"           # Latin text on a Ukrainian line


# ---- uk with Russian words --------------------------------------------------------------------------------------

def test_russian_text_is_redone_in_the_main_language(T):
    w = make(T, ["uk", "en"], says={"ru": "Это очень хорошо, спасибо", None: "Это очень хорошо, спасибо",
                                    "uk": "Це дуже добре, дякую"})
    text, lang, tf = w.decode(AUDIO, "en", None, "uk")             # decoded as "en" (Russian audio), text is Russian
    assert w.mlx_whisper.calls == ["en", "uk"]
    assert (text, tf) == ("Це дуже добре, дякую", "ru")           # redone in the main language: marked


def test_russian_check(T):
    assert T.is_russian("Это очень хорошо, спасибо")
    assert T.is_russian("Ну вот, ещё раз")
    assert not T.is_russian("Це дуже добре, дякую")
    assert not T.is_russian("Я думаю, що це гарна ідея")
    assert not T.is_russian("Let's start the call")
    assert not T.is_russian("Так, давайте почнемо")


def test_russian_that_survives_the_main_language_is_marked(T):
    w = make(T, ["uk"], says={"uk": "Это очень хорошо"})
    assert w.decode(AUDIO, "uk", None, "uk")[2] == "ru"


def test_russian_as_detected_language_uses_the_ukrainian_of_the_set(T):
    w = make(T, ["uk", "en"], probs={"uk": 0.2, "en": 0.05, "ru": 0.7})
    assert w.choose(AUDIO, None, "en") == ("uk", None, None)       # Ukrainian heard as ru: not a translation


# ---- silence stop-phrases ---------------------------------------------------------------------------------------

def test_stop_phrase_on_near_silence_is_dropped_but_not_in_a_normal_voice(T):
    cfg = {"silence_max_s": 2.5, "silence_rms": 0.0035}
    assert T.silence_stop("Дякую за перегляд!", 1.2, 0.002, cfg)
    assert T.silence_stop("Thank you for watching.", 0.9, 0.001, cfg)
    assert not T.silence_stop("Дякую за перегляд!", 1.2, 0.05, cfg)         # normal voice
    assert not T.silence_stop("Дякую за перегляд!", 6.0, 0.002, cfg)        # long: not a hallucination of this kind
    assert not T.silence_stop("Дякую за вашу відповідь", 1.2, 0.002, cfg)   # not a stop-phrase
    for reply in ("Так.", "Thanks.", "Okay", "Добре", "Дякую", "Bye"):      # ordinary replies are never stop-phrases
        assert not T.silence_stop(reply, 0.6, 0.001, cfg)


def padded(T, voice_rms, voiced_s=0.6, pad_s=1.5):
    """A segment as the Segmenter makes it: quiet lead-in and the end_silence padding around a short quiet voice."""
    n_voice, n_pad = int(voiced_s * T.SR), int(pad_s * T.SR)
    audio = np.concatenate([np.zeros(n_pad // 2), np.full(n_voice, voice_rms), np.zeros(n_pad // 2)]).astype(np.float32)
    return T.Segment("them", 1000.0, 1000.0 + len(audio) / T.SR, audio, voiced_s, voice_rms)


def test_quiet_short_reply_inside_a_padded_segment_survives(T):
    cfg = dict(T.DEFAULTS)
    seg = padded(T, 0.006)                  # above the VAD speech floor; whole-audio RMS would be ~0.003
    assert np.sqrt(np.mean(seg.audio ** 2)) < 0.0035
    for reply in ("Thanks.", "Так.", "Okay."):
        assert not T.drop_silence_stop(seg, reply, cfg)


def test_hallucinated_stop_phrase_on_near_silence_is_dropped_by_the_wiring_helper(T):
    cfg = dict(T.DEFAULTS)
    assert T.drop_silence_stop(padded(T, 0.002), "Дякую за перегляд!", cfg)
    assert not T.drop_silence_stop(padded(T, 0.05), "Дякую за перегляд!", cfg)
    unknown = T.Segment("them", 1.0, 2.0, np.full(T.SR, 0.002, dtype=np.float32))   # no voiced_rms: whole audio
    assert T.drop_silence_stop(unknown, "Thank you for watching", cfg)


def test_segmenter_records_the_rms_of_the_voiced_frames_only(T):
    import queue
    q = queue.Queue()
    sg = T.Segmenter("them", dict(T.DEFAULTS), q)
    sg.vad.is_speech = lambda f: float(np.max(np.abs(f))) > 0.004     # deterministic VAD
    voice = np.full(int(0.9 * T.SR), 0.2, dtype=np.float32)
    sg.feed(np.concatenate([np.zeros(T.SR // 2, dtype=np.float32), voice, np.zeros(2 * T.SR, dtype=np.float32)]))
    seg = q.get_nowait()
    assert seg.voiced_rms == pytest.approx(0.2, rel=0.01)
    assert np.sqrt(np.mean(seg.audio ** 2)) < 0.15


def test_ukrainian_uzhe_is_not_russian_but_two_russian_words_are(T):
    assert not T.is_russian("Вже уже")
    assert not T.is_russian("Ми уже все зробили")                   # one hit in 4 words, no ы э ъ ё
    assert T.is_russian("Это уже было")


def test_latin_product_names_do_not_mark_a_ukrainian_line(T):
    assert not T.script_mismatch("Давайте відкриємо Slack і Jira", "uk")
    assert not T.script_mismatch("Завдання в jira та slack", "uk")
    assert not T.script_mismatch("Запишемо це в Confluence", "uk")
    assert T.script_mismatch("I would like a coffee please", "uk")
    assert not T.script_mismatch("Mike and Emily", "en")


def test_stop_phrases_in_code_match_the_rules_file(T):
    rules = yaml.safe_load((REPO / "rules" / "asr_filters.yaml").read_text(encoding="utf-8"))
    assert set(rules["silence_stop_phrases"]) == T.SILENCE_STOP_PHRASES
    assert rules["silence_max_s"] == T.DEFAULTS["silence_max_s"]
    assert rules["silence_rms"] == T.DEFAULTS["silence_rms"]
    assert rules["lang_warmup_s"] == T.DEFAULTS["lang_warmup_s"]
    assert rules["lang_foreign_min"] == T.DEFAULTS["lang_foreign_min"]
    assert "ru" in rules["never_languages"]


def test_line_record_has_the_translated_from_field_and_the_transcript_format_is_unchanged(T, tmp_path):
    ln = T.Line("them", 1000.0, "uk", "Привіт", translated_from="en")
    assert ln.translated_from == "en" and T.Line("me", 1.0, "uk", "x").translated_from is None
    w = T.Writer(tmp_path / "c.md", dict(T.DEFAULTS), "")
    w._write(ln)
    row = (tmp_path / "c.md").read_text(encoding="utf-8").strip()
    assert row.endswith("Співрозмовник (uk): Привіт")              # old readers (LINE_RE: two-letter tag) still match
