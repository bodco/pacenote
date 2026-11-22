"""PB-30 (D56), review round 11: the local client never uses a proxy, the end-of-call rewrite does not feed a
translated row twice, French decoded in English is translated (an answer that repeats it is not used), and a failed
translation is logged by route and category, never with the text."""
import http.client
import importlib.util
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from livetips_m0.live import LiveRun

SRC = Path(__file__).absolute().parents[3] / "recorder" / "src" / "transcriber.py"
sys.path.insert(0, str(SRC.parent))


@pytest.fixture(scope="module")
def T():
    spec = importlib.util.spec_from_file_location("lt_transcriber_pb30_r11", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lt_transcriber_pb30_r11"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- finding 1: no proxy for the local model -------------------------------------------------------------------

def connects(monkeypatch):
    hosts = []

    def connect(self):
        hosts.append(self.host)
        raise OSError("no network in tests")

    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)
    return hosts


def test_the_local_model_is_asked_directly_even_with_a_proxy_in_the_environment(monkeypatch):
    import translate as TR
    for k in ("no_proxy", "NO_PROXY", "LT_TRANSLATE_ROUTE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("http_proxy", "http://proxy.example.invalid:3128")
    hosts = connects(monkeypatch)
    with pytest.raises(OSError):                       # the probe sees a proxy when the default opener is used
        urllib.request.build_opener(TR._LoopbackOnly).open("http://127.0.0.1:11434/api/tags", timeout=1)
    assert hosts == ["proxy.example.invalid"]
    hosts.clear()
    tr = TR.Translator({"ollama_url": "http://127.0.0.1:11434", "translate_model": "m"})
    assert tr.translate("Bonjour", "fr", "en") is None
    assert hosts and set(hosts) == {"127.0.0.1"}


# ---- finding 2: the end-of-call rewrite --------------------------------------------------------------------------

class Eng:
    def __init__(self):
        self.lock = threading.RLock()
        self.history = []

    def on_line(self, ln):
        with self.lock:
            self.history.append(ln)


def call(T, tmp_path):
    md, tm = tmp_path / "c.md", tmp_path / "c.md.timing.jsonl"
    tm.write_text("", encoding="utf-8")
    eng = Eng()
    run = LiveRun({"me_label": T.DEFAULTS["me_label"]}, eng, md, from_start=True)
    w = T.Writer(md, dict(T.DEFAULTS), "", them_speech=lambda: None)

    def event(**d):
        with tm.open("a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    w.on_row = lambda ln: event(event="row", seq=ln.seq, t=T.hhmmss(ln.t), ch=ln.channel, text=ln.text)
    return md, eng, run, w, event


@pytest.mark.parametrize("new", ["Hi", "Hello, I would like a cup of coffee with milk and no sugar, please"])
def test_a_row_translated_by_the_end_of_call_rewrite_stays_one_engine_line(T, tmp_path, new):
    md, eng, run, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    ln = T.Line("them", t0, "en", "Je voudrais un cafe", sid="S1", translated_from="fr", seq=1)
    w.add(ln)
    w.add(T.Line("them", t0 + 5, "en", "Okay, sounds good", sid="S1", seq=2))
    event(t=hms, ch="them", seg_end=hms, written=hms)
    event(t=T.hhmmss(t0 + 5), ch="them", seg_end=hms, written=hms)
    run.step()
    assert [h.seq for h in eng.history] == [1, 2]
    original = ln.text
    assert w.replace(ln, new) == ln.row and ln.row          # the row is already in the file: a live event follows
    event(event="translated", seq=1, t=hms, ch="them", translated_from="fr", original=original, text=new)
    run.step()
    w.close("\n")                                           # the real rewrite: the file is replaced (shorter or longer)
    assert new in md.read_text(encoding="utf-8") and original not in md.read_text(encoding="utf-8")
    run.step()
    run.step()
    assert [(h.seq, h.text) for h in eng.history] == [(1, new), (2, "Okay, sounds good")]


def test_a_real_row_with_the_translated_text_is_still_a_new_line(T, tmp_path):
    md, eng, run, w, event = call(T, tmp_path)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    ln = T.Line("them", t0, "en", "Je voudrais un cafe", sid="S1", translated_from="fr", seq=1)
    w.add(ln)
    event(t=hms, ch="them", seg_end=hms, written=hms)
    run.step()
    w.replace(ln, "I would like a coffee")
    event(event="translated", seq=1, t=hms, ch="them", translated_from="fr", original="Je voudrais un cafe",
          text="I would like a coffee")
    w.add(T.Line("them", t0, "en", "I would like a coffee", sid="S1", seq=2))   # the writer appended it: row event
    event(t=hms, ch="them", seg_end=hms, written=hms)
    run.step()
    run.step()
    assert [h.seq for h in eng.history] == [1, 2]


# ---- finding 3: French decoded in English; an answer that repeats it ---------------------------------------------

def translator(monkeypatch, T, local, route):
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    logs = []
    tr = TR.Translator({"translate_cmd": ["x"]}, T.translation_ok, log=logs.append)
    calls = []
    monkeypatch.setattr(tr, "_ollama_up", lambda: True)
    monkeypatch.setattr(tr, "_ollama", lambda text, src, dst: local)
    monkeypatch.setattr(tr, "_route", lambda text, src, dst: calls.append(text) or route)
    return tr, calls, logs


def test_an_answer_that_repeats_the_french_text_is_not_used_and_the_route_is_asked(monkeypatch, T):
    tr, calls, logs = translator(monkeypatch, T, "Je voudrais un cafe.", "I would like a coffee")
    assert tr.translate("Je voudrais un cafe", "fr", "en") == "I would like a coffee" and calls
    tr, calls, logs = translator(monkeypatch, T, "Je voudrais un cafe", "je voudrais un café")
    assert tr.translate("Je voudrais un cafe", "fr", "en") is None
    assert logs == ["translate: local: answer repeats the text", "translate: route: answer repeats the text"]


def test_same_means_already_in_the_target_language_and_no_route_is_asked(monkeypatch, T):
    tr, calls, logs = translator(monkeypatch, T, "SAME", "I would like a coffee")
    assert tr.translate("I would like a coffee", "fr", "en") is None and calls == []
    assert logs == ["translate: local: already en, kept"]
    import translate as TR
    assert "answer exactly SAME" in TR.prompt_for("fr", "en")


def test_a_latin_out_of_set_line_is_always_sent_a_clean_ukrainian_redo_of_russian_is_only_marked(T):
    assert T.needs_translation("Je voudrais un cafe", "en", "fr")
    assert T.needs_translation("I would like a coffee", "uk", "fr")        # wrong script: sent anyway
    assert not T.needs_translation("Це дуже добре", "uk", "ru")             # the script tells: no model call
    assert T.needs_translation("Это очень хорошо", "uk", "ru")


def test_same_is_read_only_in_capitals_also_with_a_comment_after_it():
    import translate as TR
    assert TR.is_same("SAME") and TR.is_same(" SAME. ") and TR.is_same("SAME (the text is already English)")
    assert not TR.is_same("Same") and not TR.is_same("same") and not TR.is_same("The same, please")


def test_the_live_event_is_written_under_the_writer_lock_before_the_rewrite(T, tmp_path):
    w = T.Writer(tmp_path / "c.md", dict(T.DEFAULTS), "", them_speech=lambda: None)
    ln = T.Line("them", 1_700_000_000.0, "en", "Je voudrais un cafe", translated_from="fr", seq=1)
    w.add(ln)
    held = []
    assert w.replace(ln, "I would like a coffee", lambda row: held.append((row, w.lock.locked()))) == ln.row
    assert held == [("[" + T.hhmmss(ln.t) + "] " + T.DEFAULTS["them_label"] + " (en): Je voudrais un cafe", True)]
    w.close("")
    assert w.replace(ln, "x", lambda row: pytest.fail("closed: no event")) is None


def test_the_worker_keeps_the_marked_line_when_the_answer_repeats_it(T):
    import translate as TR
    applied = []
    wk = TR.TranslateWorker(lambda text, src, dst: text, lambda j, new: applied.append(new), T.translation_ok)
    wk.start()
    wk.submit({"text": "Je voudrais un cafe", "src": "fr", "dst": "en"})
    wk.stop(5)
    assert applied == [] and wk.kept == 1 and wk.done == 0


# ---- non-blocking: failures are logged by route and category, without the text ---------------------------------

def test_failures_are_logged_by_route_and_category_without_the_text(monkeypatch, T):
    import subprocess
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    logs = []
    tr = TR.Translator({"translate_cmd": ["x"]}, T.translation_ok, log=logs.append)
    monkeypatch.setattr(tr, "_ollama_up", lambda: True)

    def boom(text, src, dst):
        raise TimeoutError(text)

    monkeypatch.setattr(tr, "_ollama", boom)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 3, "", "secret Bonjour"))
    assert tr.translate("Bonjour", "fr", "en") is None
    assert logs == ["translate: local: TimeoutError", "translate: route: exit 3"]
    monkeypatch.delattr(tr, "_ollama_up")
    tr.ollama_allowed, tr._up_until = True, 0.0           # the probe runs again (once a minute)
    monkeypatch.setattr(tr._opener, "open", lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("x")))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "not json", ""))
    logs.clear()
    assert tr.translate("Bonjour", "fr", "en") is None
    assert logs == ["translate: local: not reachable (ConnectionRefusedError)", "translate: route: JSONDecodeError"]
    assert not any("Bonjour" in m or "secret" in m for m in logs)
