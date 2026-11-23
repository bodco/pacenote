"""PB-30 (D56, roadmap FB1 + FB13): the call language. One language is forced; several are written as spoken with the
main one being the most spoken; a language outside the set and Russian are redone in the main one and marked
(translated_from); Whisper stop-phrases on near-silence are dropped. Whisper itself is replaced by a scripted fake."""
import importlib.util
import sys
import threading
import time
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

def test_french_on_a_several_language_call_is_decoded_in_the_main_language_and_marked(T):
    w = make(T, ["uk", "en"], says={"en": "I would like a coffee please", "uk": "Я хотів би каву"},
             probs={"uk": 0.05, "en": 0.15, "fr": 0.7})
    assert w.choose(AUDIO, None, "en") == ("en", None, "fr")       # outside the set: the main language, French reported
    assert w.choose(AUDIO, None, "uk") == ("uk", None, "fr")
    lang, heard, foreign = w.choose(AUDIO, None, "en")             # the real choose() -> decode() sequence
    assert w.decode(AUDIO, lang, None, "en", foreign) == ("I would like a coffee please", "en", "fr")
    assert w.mlx_whisper.calls == ["en"]                           # forced, never autodetected


def test_french_on_a_one_language_call_is_forced_and_not_marked(T):
    # D56: a one-language call is forced, no autodetect, so there is no source-language evidence and no marker
    w = make(T, ["en"], says={"en": "Je voudrais un cafe"}, probs={"fr": 0.9})
    lang, heard, foreign = w.choose(AUDIO)
    assert (lang, heard, foreign) == ("en", "en", None)
    assert w.decode(AUDIO, lang, None, "en", foreign) == ("Je voudrais un cafe", "en", None)
    assert w.mlx_whisper.calls == ["en"]


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


def test_russian_on_the_first_main_language_decode_is_retried_once_then_marked(T):
    w = make(T, ["uk"], says={"uk": "Это очень хорошо"})
    assert w.decode(AUDIO, "uk", None, "uk")[2] == "ru"
    assert w.mlx_whisper.calls == ["uk", "uk"]                     # one retry in the main language, then the marker
    w = make(T, ["uk"], says={})
    seq = iter(["Это очень хорошо", "Це дуже добре"])
    w.mlx_whisper.transcribe = lambda *a, **k: {"language": "uk", "segments": [{
        "text": next(seq), "no_speech_prob": 0.01, "avg_logprob": -0.2, "compression_ratio": 1.0}]}
    assert w.decode(AUDIO, "uk", None, "uk") == ("Це дуже добре", "uk", "ru")   # the retry fixed the text; the source stays marked


def test_russian_as_detected_language(T):
    w = make(T, ["uk", "en"], probs={"uk": 0.2, "en": 0.05, "ru": 0.7})
    assert w.choose(AUDIO, None, "uk") == ("uk", None, None)       # Ukrainian main, heard as ru: not a translation
    assert w.choose(AUDIO, None, "en") == ("en", None, "ru")       # English main: the retry is in English, source kept


def test_short_russian_replies_are_russian(T):
    for t in ("Пока.", "Хорошо.", "Ладно"):
        assert T.is_russian(t)
    assert not T.is_russian("Добре.") and not T.is_russian("Поки.") and not T.is_russian("Уже.")


# ---- silence stop-phrases ---------------------------------------------------------------------------------------

def test_stop_phrase_on_near_silence_is_dropped_but_not_in_a_normal_voice(T):
    cfg = {"silence_max_s": 2.5, "silence_rms": 0.0035}
    assert T.silence_stop("Дякую за перегляд!", 1.2, 0.002, cfg)
    assert T.silence_stop("Thank you for watching.", 0.9, 0.001, cfg)
    assert not T.silence_stop("Дякую за перегляд!", 1.2, 0.05, cfg)         # normal voice
    assert not T.silence_stop("Дякую за перегляд!", 6.0, 0.002, cfg)        # long: not a hallucination of this kind
    assert not T.silence_stop("Дякую за вашу відповідь", 1.2, 0.002, cfg)   # not a stop-phrase
    for reply in ("Так.", "Okay", "Добре", "Yes, sure", "Дякую, до зустрічі"):   # ordinary replies are never stop-phrases
        assert not T.silence_stop(reply, 0.6, 0.001, cfg)
    for hallucination in ("Дякую.", "Bye.", "Thank you."):                  # only on near-silence
        assert T.silence_stop(hallucination, 0.6, 0.001, cfg)
        assert not T.silence_stop(hallucination, 0.6, 0.05, cfg)


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
    voice = np.full(30 * T.FRAME, 0.2, dtype=np.float32)               # whole frames: no half-voiced frame at the edges
    sg.feed(np.concatenate([np.zeros(16 * T.FRAME, dtype=np.float32), voice, np.zeros(2 * T.SR, dtype=np.float32)]))
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


def test_line_record_has_the_translated_from_field_and_the_row_ends_with_the_marker(T, tmp_path):
    ln = T.Line("them", 1000.0, "uk", "Привіт", translated_from="en")
    assert ln.translated_from == "en" and T.Line("me", 1.0, "uk", "x").translated_from is None
    w = T.Writer(tmp_path / "c.md", dict(T.DEFAULTS), "")
    w._write(ln)
    row = (tmp_path / "c.md").read_text(encoding="utf-8").strip()
    # review round 6: the source language is saved at the end of the row; the prefix (time, label, tag) is unchanged
    assert row.endswith("Співрозмовник (uk): Привіт (← en)")


# ---- the recorder path: asr_segment() = choose -> decode -> clean_text -> silence filter -> language share ----------

class Scripted:
    """The real Whisper.choose()/decode() on a fake mlx_whisper, wrapped so the path is the production one."""

    def __init__(self, T, langs, says, probs):
        self.w = make(T, langs, says, probs)

    def __getattr__(self, name):
        return getattr(self.w, name)


def seg_of(T, rms, seconds=1.0, channel="them"):
    audio = np.full(int(seconds * T.SR), rms, dtype=np.float32)
    return T.Segment(channel, 1000.0, 1000.0 + seconds, audio, seconds, rms)


def run(T, w, seg, langs=("uk", "en"), last="uk", dominant=None, me_langs=()):
    cfg = dict(T.DEFAULTS, langs=list(langs), me_langs=list(me_langs))
    share = T.LangShare(list(langs), warmup_s=0.0)
    if dominant:
        share.add(dominant, 100.0)
        share.t0 = 0.0
    res = T.asr_segment(w, seg, cfg, share, {"me": last, "them": last}, None, set())
    return res, share


def test_voiced_short_replies_survive_the_whole_cleanup_and_near_silent_ones_do_not(T):
    for reply in ("Дякую.", "Bye.", "Thank you for watching.", "Thank you."):
        w = make(T, ["uk"], says={"uk": reply})
        res, _ = run(T, w, seg_of(T, 0.05), langs=["uk"])           # a normal voice: kept
        assert res is not None and res[0] == reply
        res, _ = run(T, make(T, ["uk"], says={"uk": reply}), seg_of(T, 0.002), langs=["uk"])
        assert res is None                                           # near-silence: the Whisper stop-phrase


def test_short_english_reply_after_a_ukrainian_line_stays_english_and_counts_as_english(T):
    w = make(T, ["uk", "en"], says={"en": "Sure, go ahead", "uk": "Звісно, продовжуйте"},
             probs={"uk": 0.05, "en": 0.9})
    res, share = run(T, w, seg_of(T, 0.05, 1.0), last="uk")
    assert res == ("Sure, go ahead", "en", None)
    assert share.secs == {"en": 1.0}


def test_short_unclear_segment_keeps_the_previous_language_and_does_not_count_as_evidence(T):
    w = make(T, ["uk", "en"], says={"uk": "Так, добре", "en": "Yes"}, probs={"uk": 0.4, "en": 0.45})
    res, share = run(T, w, seg_of(T, 0.05, 1.0), last="uk")
    assert res == ("Так, добре", "uk", None)
    assert share.secs == {}                                         # the decode was forced; nothing was clearly heard
    w = make(T, ["uk", "en"], says={"en": "Yes"}, probs={"uk": 0.4, "en": 0.45})
    res, share = run(T, w, seg_of(T, 0.05, 1.0), last="en")
    assert res == ("Yes", "en", None)
    assert share.secs == {"en": 1.0}                                # the detector's own pick agrees with the keep


def test_long_unclear_segment_stays_in_the_dominant_language_and_does_not_count_as_evidence(T):
    w = make(T, ["uk", "en"], says={"uk": "Я хотів би", "en": "I would like"}, probs={"uk": 0.3, "en": 0.55})
    res, share = run(T, w, seg_of(T, 0.05, 3.0), dominant="uk")
    assert res == ("Я хотів би", "uk", None)
    assert share.secs == {"uk": 100.0}                              # only the seeded share; this line adds nothing


def test_short_segment_without_a_detector_keeps_the_previous_language_without_crediting_it(T):
    w = make(T, ["uk", "en"], says={"uk": "Так, добре"}, probs=None)
    res, share = run(T, w, seg_of(T, 0.05, 1.0), last="uk")
    assert res == ("Так, добре", "uk", None) and share.secs == {}


def test_french_on_a_several_language_call_through_the_recorder_path(T):
    w = make(T, ["uk", "en"], says={"en": "I would like a coffee please"}, probs={"uk": 0.05, "en": 0.15, "fr": 0.7})
    res, share = run(T, w, seg_of(T, 0.05, 3.0), dominant="en")
    assert res == ("I would like a coffee please", "en", "fr")
    assert share.secs == {"en": 100.0}                              # a French line is not credited to English


def test_russian_with_english_dominant_is_retried_in_english_and_marked(T):
    w = make(T, ["uk", "en"], says={"en": "That is very good, thanks", "uk": "Це дуже добре"},
             probs={"uk": 0.2, "en": 0.05, "ru": 0.7})
    res, _ = run(T, w, seg_of(T, 0.05, 3.0), dominant="en")
    assert res == ("That is very good, thanks", "en", "ru")
    assert w.mlx_whisper.calls == ["en"]                            # the dominant language, not the first Cyrillic one


def test_russian_text_on_the_first_dominant_decode_is_retried_then_marked(T):
    w = make(T, ["uk", "en"], says={"uk": "Это очень хорошо"}, probs={"uk": 0.9, "en": 0.05})
    res, share = run(T, w, seg_of(T, 0.05, 3.0))
    assert res == ("Это очень хорошо", "uk", "ru")
    assert w.mlx_whisper.calls == ["uk", "uk"]
    assert share.secs == {}                                         # marked lines are not language evidence


def test_short_russian_replies_through_the_recorder_path(T):
    for reply in ("Пока.", "Хорошо."):
        w = make(T, ["uk", "en"], says={"uk": reply}, probs={"uk": 0.9, "en": 0.05})
        res, _ = run(T, w, seg_of(T, 0.05, 1.0))
        assert res[2] == "ru" and w.mlx_whisper.calls == ["uk", "uk"]


# ---- review round 5: the language of short out-of-set speech, stop-phrases through the recorder path --------------

def test_short_french_segment_uses_the_dominant_language_not_the_previous_line(T):
    w = make(T, ["uk", "en"], says={"en": "I would like a coffee please", "uk": "Я хотів би каву"},
             probs={"uk": 0.05, "en": 0.15, "fr": 0.7})
    res, share = run(T, w, seg_of(T, 0.05, 1.0), dominant="en", last="uk")
    assert res == ("I would like a coffee please", "en", "fr")      # English is dominant; the previous line was Ukrainian
    assert w.mlx_whisper.calls == ["en"] and share.secs == {"en": 100.0}


def test_unclear_short_segment_still_keeps_the_previous_language_when_it_differs_from_the_dominant(T):
    w = make(T, ["uk", "en"], says={"uk": "Так", "en": "Yes"}, probs={"uk": 0.4, "en": 0.35})
    res, _ = run(T, w, seg_of(T, 0.05, 1.0), dominant="en", last="uk")
    assert res[1] == "uk"                                           # unclear: the previous language, as before


def test_listed_subtitle_stop_phrases_survive_a_normal_voice_and_die_on_near_silence(T):
    for phrase in ("Субтитри зроблені спільнотою", "Subtitles by"):
        res, _ = run(T, make(T, ["uk"], says={"uk": phrase}), seg_of(T, 0.05, 1.0), langs=["uk"])
        assert res is not None and res[0] == phrase                 # said at a normal volume: a real line
        res, _ = run(T, make(T, ["uk"], says={"uk": phrase}), seg_of(T, 0.002, 1.0), langs=["uk"])
        assert res is None                                          # near-silence: the Whisper credit line
    res, _ = run(T, make(T, ["uk"], says={"uk": "Субтитри зроблені спільнотою Amara.org"}), seg_of(T, 0.05), langs=["uk"])
    assert res is None                                              # a credit with a site is never speech


# ---- review round 5: the translation fallback (background, never on the live path) -------------------------------

def writer_with(T, tmp_path, lines):
    w = T.Writer(tmp_path / "c.md", dict(T.DEFAULTS), "# call\n", them_speech=lambda: None)
    for ln in lines:
        w.add(ln)
    return w


def worker_for(T, translate, w, done=None, maxlen=16):
    import translate as TR
    applied = done if done is not None else []

    def apply(j, new):
        if w.replace(j["line"], new) is not None:
            applied.append((j["text"], new))
    return TR.TranslateWorker(translate, apply, T.translation_ok, maxlen=maxlen)


def job_of(ln, src="ru"):
    return {"text": ln.text, "src": src, "dst": "uk", "line": ln}


def test_russian_line_goes_through_the_recorder_path_then_the_translation_replaces_it_in_the_final_transcript(T, tmp_path):
    wh = make(T, ["uk", "en"], says={"uk": "Это очень хорошо"}, probs={"uk": 0.9, "en": 0.05})
    res, _ = run(T, wh, seg_of(T, 0.05, 3.0))
    text, lang, tf = res
    assert (text, lang, tf) == ("Это очень хорошо", "uk", "ru") and T.needs_translation(text, lang, tf)
    ln = T.Line("them", 1000.0, lang, text, translated_from=tf)
    w = writer_with(T, tmp_path, [ln])
    assert "(uk): Это очень хорошо" in (tmp_path / "c.md").read_text(encoding="utf-8")   # the live line, marked
    wk = worker_for(T, lambda t, s, d: "Це дуже добре", w)
    wk.start()
    wk.submit(job_of(ln))
    wk.stop(5.0)
    w.close("--- end ---\n")
    body = (tmp_path / "c.md").read_text(encoding="utf-8")
    assert "Співрозмовник (uk): Це дуже добре" in body and "Это очень" not in body
    assert body.rstrip().endswith("--- end ---")                    # the footer stays last
    assert ln.translated_from == "ru" and ln.text == "Це дуже добре"


def test_a_translation_that_is_still_russian_or_empty_is_not_used(T, tmp_path):
    ln = T.Line("them", 1000.0, "uk", "Это очень хорошо", translated_from="ru")
    w = writer_with(T, tmp_path, [ln])
    for bad in ("Это очень хорошо", "", "   ", "That is very good"):
        done = []
        wk = worker_for(T, lambda t, s, d, bad=bad: bad, w, done)
        wk.start()
        wk.submit(job_of(ln))
        wk.stop(5.0)
        assert done == []
    w.close("")
    assert "Это очень хорошо" in (tmp_path / "c.md").read_text(encoding="utf-8")


def test_no_translation_route_keeps_the_marked_line(T, tmp_path):
    import translate as TR
    tr = TR.Translator({"ollama_url": "http://127.0.0.1:9", "translate_timeout_s": 1.0})   # no Ollama, no command
    assert tr.translate("Это очень хорошо", "ru", "uk") is None
    ln = T.Line("them", 1000.0, "uk", "Это очень хорошо", translated_from="ru")
    w = writer_with(T, tmp_path, [ln])
    wk = worker_for(T, tr.translate, w)
    wk.start()
    wk.submit(job_of(ln))
    wk.stop(5.0)
    w.close("")
    assert "(uk): Это очень хорошо" in (tmp_path / "c.md").read_text(encoding="utf-8") and ln.translated_from == "ru"


def test_the_command_route_translates_and_a_failing_or_slow_one_gives_nothing(T, monkeypatch):
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")      # the app's permission for this launch
    ok = "import sys,json; r=json.loads(sys.stdin.read()); print(json.dumps({'text': 'UK:' + r['text'] + r['src'] + r['dst']}))"
    base = {"ollama_url": "http://127.0.0.1:9", "translate_timeout_s": 10.0}
    assert TR.Translator(dict(base, translate_cmd=[sys.executable, "-c", ok])).translate("Hi", "ru", "uk") == "UK:Hiruuk"
    assert TR.Translator(dict(base, translate_cmd=[sys.executable, "-c", "raise SystemExit(1)"])).translate("x", "ru", "uk") is None
    slow = TR.Translator(dict(base, translate_timeout_s=0.5, translate_cmd=[sys.executable, "-c", "import time; time.sleep(5)"]))
    assert slow.translate("x", "ru", "uk") is None                  # the request has its own deadline


def test_a_slow_translator_blocks_neither_submit_nor_the_writer(T, tmp_path):
    gate, started = threading.Event(), threading.Event()

    def slow(text, src, dst):
        started.set()
        gate.wait(10)
        return "Це дуже добре"
    ln = T.Line("them", 1000.0, "uk", "Это очень хорошо", translated_from="ru")
    w = writer_with(T, tmp_path, [ln])
    wk = worker_for(T, slow, w)
    wk.start()
    wk.submit(job_of(ln))
    assert started.wait(5)
    nxt = T.Line("them", 1005.0, "uk", "Наступна фраза")
    w.add(nxt)                                                      # the live path goes on while the translator is stuck
    for i in range(40):
        wk.submit({"text": f"x{i}", "src": "ru", "dst": "uk", "line": nxt})
    assert len(wk.jobs) <= 16                                       # bounded: the oldest waiting lines are dropped
    body = (tmp_path / "c.md").read_text(encoding="utf-8")
    assert "Это очень хорошо" in body and "Наступна фраза" in body
    gate.set()
    wk.stop(5.0)
    w.close("")
    assert "(uk): Це дуже добре" in (tmp_path / "c.md").read_text(encoding="utf-8")


def test_stop_does_not_wait_longer_than_the_drain_time_and_a_late_translation_changes_nothing(T, tmp_path):
    gate = threading.Event()
    ln = T.Line("them", 1000.0, "uk", "Это очень хорошо", translated_from="ru")
    w = writer_with(T, tmp_path, [ln])
    wk = worker_for(T, lambda t, s, d: gate.wait(10) and "Це добре", w)
    wk.start()
    wk.submit(job_of(ln))
    t0 = time.monotonic()
    wk.stop(0.3)
    assert time.monotonic() - t0 < 3.0
    w.close("")
    gate.set()
    assert w.replace(ln, "late") is None
    assert "Это очень хорошо" in (tmp_path / "c.md").read_text(encoding="utf-8")


def test_a_held_mic_line_is_translated_in_memory_before_it_is_written(T, tmp_path):
    ln = T.Line("me", 1000.0, "uk", "Это очень хорошо", translated_from="ru")
    w = T.Writer(tmp_path / "c.md", dict(T.DEFAULTS), "", them_speech=lambda: T.now())   # the other side talks: the line waits
    w.add(ln)
    assert w.held_me and not ln.row
    assert w.replace(ln, "Це дуже добре") == ""                      # held: no row shown yet, so no live event
    w.close("")
    assert "(uk): Це дуже добре" in (tmp_path / "c.md").read_text(encoding="utf-8")


def test_only_russian_or_wrong_script_lines_are_sent_to_translation(T):
    assert T.needs_translation("Это очень хорошо", "uk", "ru")
    assert T.needs_translation("I would like a coffee", "uk", "und")
    assert T.needs_translation("I would like a coffee please", "en", "fr")   # the model answers SAME when it is English
    assert not T.needs_translation("Це дуже добре", "uk", None)
    assert T.translation_ok("Це дуже добре", "uk") and not T.translation_ok("Это очень хорошо", "uk")
