"""PB-30 (D56), review round 7: the translation uses only the AIs allowed for live tips, runs only with the per-launch
permission of the app (never from a stale config or an inherited key), and one line stays one transcript row."""
import re
import sys
from pathlib import Path

import pytest

from livetips_m0 import routing, translate_text
from livetips_m0.providers import Completion, FakeProvider, Provider

RECORDER = Path(__file__).absolute().parents[3] / "recorder" / "src"
sys.path.insert(0, str(RECORDER))
CHAIN = '["code","codex","api","openai","gemini","agy","local"]'


class Failing(Provider):
    def complete(self, *, model, system, user, max_tokens, cache_ttl, on_text=None, extra=None):
        return Completion(model=model, t_start=0.0, t_done=0.0, error="credit balance is too low")


class Tripwire(Provider):
    def complete(self, **kw):
        pytest.fail("the live tips route does not allow this AI for text of a call")


# ---- finding 1: the live tips chain and exclusions ------------------------------------------------------------

def real_router(monkeypatch, tmp_path, job):
    for k, v in {"LT_AI_ROUTE": "code", "LT_AI_AUTO": "1", "LT_AI_CHAIN": CHAIN, "LT_GEMINI_FALLBACK": "1",
                 "LT_LOCAL_FALLBACK": "1", "CLAUDE_API": "dummy", "LT_AI_STATE": str(tmp_path / "state.json"),
                 "LT_TRANSLATE_ROUTE": "1"}.items():
        monkeypatch.setenv(k, v)
    return routing.provider_from_env(job, timeout=5.0, background=(job == "translate"))


def test_translation_has_the_live_tips_chain_with_the_real_router(monkeypatch, tmp_path):
    tips = real_router(monkeypatch, tmp_path, "tips")
    tips.background = True                                    # what the engine's tips provider is during a call
    tr = real_router(monkeypatch, tmp_path, "translate")
    assert tr.order() == tips.order()
    assert not {"agy", "local"} & set(tr.order()) and tr._plan_b() == tips._plan_b() and not tr.local_fallback()


def test_with_the_chosen_ai_out_translation_goes_to_the_next_tips_ai_never_agy_or_local(monkeypatch, tmp_path):
    tr = real_router(monkeypatch, tmp_path, "translate")
    seen = []
    tr.legs["code"] = Failing()
    tr.legs["api"] = FakeProvider(lambda m, u: seen.append(u) or "Це добре")
    for r in ("agy", "local", "gemini", "openai", "codex"):
        if r in tr.legs:
            tr.legs[r] = Tripwire() if r in ("agy", "local") else None
    c = tr.complete(model="m", system=[{"text": "s"}], user="Это хорошо", max_tokens=10, cache_ttl=None)
    assert c.text == "Це добре" and seen == ["Это хорошо"], (c.error, tr.order(), tr.state.read())


def test_a_chosen_route_that_live_tips_never_use_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    monkeypatch.setenv("LT_AI_ROUTE", "local")
    monkeypatch.setenv("LT_AI_STATE", str(tmp_path / "state.json"))
    with pytest.raises(RuntimeError, match="not usable"):
        translate_text.translate("Это", "ru", "uk")


def test_the_command_does_nothing_without_the_permission(monkeypatch):
    monkeypatch.delenv("LT_TRANSLATE_ROUTE", raising=False)
    monkeypatch.setenv("CLAUDE_API", "dummy")
    with pytest.raises(RuntimeError, match="not allowed"):
        translate_text.translate("Это", "ru", "uk")


# ---- finding 2: the permission is per launch --------------------------------------------------------------------

CFG = {"ollama_url": "http://127.0.0.1:9", "translate_timeout_s": 1.0,
       "translate_cmd": [sys.executable, "-m", "livetips_m0.translate_text"], "translate_dir": "/stale"}


def test_a_stale_command_in_config_is_ignored_without_the_flag(monkeypatch):
    import subprocess
    import translate as TR
    monkeypatch.delenv("LT_TRANSLATE_ROUTE", raising=False)
    monkeypatch.setenv("CLAUDE_API", "dummy")                  # inherited key, no permission
    monkeypatch.setenv("LT_AI_ROUTE", "api")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("the command must not run"))
    assert TR.Translator(CFG).translate("Это", "ru", "uk") is None
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "0")
    assert TR.Translator(CFG).translate("Это", "ru", "uk") is None


def test_the_command_runs_with_the_flag(monkeypatch):
    import subprocess
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    calls = []

    class P:
        returncode, stdout = 0, '{"text": "Це"}\n'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or P())
    assert TR.Translator(CFG).translate("Это", "ru", "uk") == "Це" and len(calls) == 1


# ---- finding 4: one line stays one row -------------------------------------------------------------------------

ROW = re.compile(r"^\[(\d\d:\d\d:\d\d)\] (.+?)(?: \[(S\d+)\])? \(([\w+-]+)\): (.*)$")


def test_a_multiline_translation_is_saved_as_one_complete_row(tmp_path):
    import transcriber as T
    w = T.Writer.__new__(T.Writer)
    import threading
    w.lock, w.closed, w.replaced, w.path = threading.RLock(), False, [], tmp_path / "c.md"
    w.cfg = {"me_label": "Me", "them_label": "Other"}
    line = T.Line("them", 36000.0, "uk", "Это хорошо. Спасибо.")
    line.row = w._row(line)
    w.path.write_text(line.row + "\n", encoding="utf-8")
    assert w.replace(line, "Це добре.\nДякую.\r\n") == line.row
    w._rewrite()
    rows = [l for l in w.path.read_text(encoding="utf-8").split("\n") if l]
    assert len(rows) == 1 and ROW.match(rows[0]).group(5) == "Це добре. Дякую."
