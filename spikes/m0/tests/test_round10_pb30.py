"""PB-30 (D56), review round 10: same-speaker identical rows are two live lines, an unsure forced decode is translated
even in the target script, and a model server off this Mac gets no transcript text without the call's permission."""
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from livetips_m0.live import LiveRun

SRC = Path(__file__).absolute().parents[3] / "recorder" / "src" / "transcriber.py"
sys.path.insert(0, str(SRC.parent))
AUDIO = np.ones(16000, dtype=np.float32) * 0.1


@pytest.fixture(scope="module")
def T():
    spec = importlib.util.spec_from_file_location("lt_transcriber_pb30_r10", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lt_transcriber_pb30_r10"] = mod
    spec.loader.exec_module(mod)
    return mod


class Eng:
    def __init__(self):
        self.lock = threading.Lock()
        self.history = []

    def on_line(self, ln):
        with self.lock:
            self.history.append(ln)


def timing(tm, hms, n):
    with tm.open("a", encoding="utf-8") as f:
        for _ in range(n):
            f.write(json.dumps(dict(t=hms, ch="them", seg_end="10:00:02.000", written="10:00:03.000")) + "\n")


# ---- finding 1: the writer's seq tells two identical rows of one speaker apart ---------------------------------

def test_two_identical_rows_from_the_same_speaker_are_two_lines_and_a_reread_adds_none(T, tmp_path):
    md, tm = tmp_path / "c.md", tmp_path / "c.md.timing.jsonl"
    tm.write_text("", encoding="utf-8")
    eng = Eng()
    run = LiveRun({"me_label": T.DEFAULTS["me_label"]}, eng, md, from_start=True)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    w = T.Writer(md, dict(T.DEFAULTS), "", them_speech=lambda: None)

    def publish(line):
        with tm.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event="row", seq=line.seq, t=hms, ch=line.channel, text=line.text)) + "\n")

    w.on_row = publish
    w.add(T.Line("them", t0, "en", "Okay, sounds good", sid="S1", seq=1))
    w.add(T.Line("them", t0, "en", "Okay, sounds good", sid="S1", seq=2))
    timing(tm, hms, 2)
    run.step()
    run.step()
    assert [h.seq for h in eng.history] == [1, 2]
    run.t_md.pos = 0                                   # the recorder rewrote the file: every row is read again
    run.step()
    assert [h.seq for h in eng.history] == [1, 2]


def test_legacy_input_without_row_events_keeps_the_deduplication(T, tmp_path):
    md, tm = tmp_path / "c.md", tmp_path / "c.md.timing.jsonl"
    tm.write_text("", encoding="utf-8")
    eng = Eng()
    run = LiveRun({"me_label": T.DEFAULTS["me_label"]}, eng, md, from_start=True)
    w = T.Writer(md, dict(T.DEFAULTS), "", them_speech=lambda: None)
    w.add(T.Line("them", 1_700_000_000.0, "en", "Okay", sid="S1", seq=1))
    timing(tm, T.hhmmss(1_700_000_000.0), 1)
    run.step()
    run.t_md.pos = 0
    run.step()
    assert len(eng.history) == 1


# ---- finding 2: an unsure forced decode is translated even when it shares the target script -------------------

class FakeMlx:
    def __init__(self, logprob):
        self.logprob = logprob

    def transcribe(self, audio, path_or_hf_repo=None, language=None, **kw):
        return {"language": language or "en", "segments": [{"text": "Je voudrais un cafe", "no_speech_prob": 0.01,
                                                              "avg_logprob": self.logprob, "compression_ratio": 1.0}]}


def french(T, logprob):
    w = object.__new__(T.Whisper)
    w.mlx_whisper = FakeMlx(logprob)
    w.repo, w.langs = "fake", ["uk", "en"]
    w.bias, w.foreign_min, w.max_no_speech = 2.0, 0.5, 0.85
    w.detector = lambda a: {"uk": 0.05, "en": 0.15, "fr": 0.7}
    return w


def asr(T, logprob):
    audio = np.full(3 * T.SR, 0.05, dtype=np.float32)
    seg = T.Segment("them", 1000.0, 1003.0, audio, 3.0, 0.05)
    cfg = dict(T.DEFAULTS, langs=["uk", "en"], me_langs=[])
    share = T.LangShare(["uk", "en"], warmup_s=0.0)
    share.add("en", 100.0)
    share.t0 = 0.0
    return T.asr_segment(french(T, logprob), seg, cfg, share, {"me": "uk", "them": "uk"}, None, set())


def test_foreign_text_in_the_target_script_is_translated_unsure_or_confident(T):
    res = asr(T, -1.1)
    assert res == ("Je voudrais un cafe", "en", "fr") and res.low_conf
    assert T.needs_translation(*res, res.low_conf)
    res = asr(T, -0.3)                                 # round 11: a confident French decode is translated too
    assert res == ("Je voudrais un cafe", "en", "fr") and not res.low_conf
    assert T.needs_translation(*res, res.low_conf)


# ---- finding 3: a model server off this Mac -------------------------------------------------------------------

class Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return b'{"models": [{"name": "m"}], "message": {"content": "Hello"}}'


def test_a_remote_ollama_url_gets_no_transcript_text_without_the_per_call_permission(monkeypatch):
    import translate as TR
    sent = []

    def fake_open(self, req, data=None, timeout=None):
        sent.append(getattr(req, "full_url", req))
        return Resp()

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    monkeypatch.delenv("LT_TRANSLATE_ROUTE", raising=False)
    cfg = {"ollama_url": "http://models.example.com:11434", "translate_model": "m"}
    assert TR.Translator(cfg).translate("Bonjour", "fr", "en") is None
    assert sent == []                                              # not even the model list was asked
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    assert TR.Translator(cfg).translate("Bonjour", "fr", "en") == "Hello"
    monkeypatch.delenv("LT_TRANSLATE_ROUTE")
    local = {"ollama_url": "http://127.0.0.1:11434", "translate_model": "m"}
    assert TR.Translator(local).translate("Bonjour", "fr", "en") == "Hello"


def test_a_redirect_off_this_mac_is_refused():
    import translate as TR
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=b"x")
    with pytest.raises(urllib.error.URLError):
        TR._LoopbackOnly().redirect_request(req, None, 307, "", {}, "http://evil.example.com/api/chat")
