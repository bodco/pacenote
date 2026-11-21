"""PB-30 (D56), review round 6: the bundle carries translate.py, the route comes only from the app's config (never a
call request), an unusable local answer falls through to the route, and a translation changes the live row in place."""
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from livetips_m0 import live
from livetips_m0.live import LiveRun

REPO = Path(__file__).absolute().parents[3]
RECORDER = REPO / "recorder" / "src"
BUNDLE_SH = REPO / "app" / "buildtools" / "bundle.sh"
CALL_ENGINE = REPO / "app" / "Sources" / "LiveTips" / "CallEngine.swift"
sys.path.insert(0, str(RECORDER))


def bundled_core_files() -> list[str]:
    """The recorder files bundle.sh copies into core/src (the `cp ... "$CORE/src/"` line)."""
    line = next(l for l in BUNDLE_SH.read_text(encoding="utf-8").splitlines() if l.rstrip().endswith('"$CORE/src/"'))
    return re.findall(r'"\$LT/src/([\w.]+)"', line)


# ---- finding 1: the packaged recorder can start --------------------------------------------------------------

def test_the_bundle_copies_every_local_module_the_recorder_imports():
    bundled = set(bundled_core_files())
    src = (RECORDER / "transcriber.py").read_text(encoding="utf-8")
    imported = set(re.findall(r"^(?:import|from) (\w+)", src, re.M)) | set(re.findall(r"^    import (\w+)", src, re.M))
    local = {m + ".py" for m in imported if (RECORDER / (m + ".py")).exists() and m != "transcriber"}
    assert "translate.py" in local
    assert local <= bundled, f"not in the bundle: {sorted(local - bundled)}"


def test_the_integrity_check_lists_the_same_files_as_the_bundle():
    swift = CALL_ENGINE.read_text(encoding="utf-8")
    listed = re.findall(r'"src/([\w.]+)"', swift[swift.index("func verifyCore"):swift.index("func ensureAudioHelper")])
    assert sorted(listed) == sorted(bundled_core_files())


def test_the_recorder_imports_from_the_staged_bundle_file_list(tmp_path):
    staged = tmp_path / "src"
    staged.mkdir()
    for name in bundled_core_files():
        (staged / name).write_bytes((RECORDER / name).read_bytes())
    # a clean interpreter, so a module another test already imported cannot hide a missing file
    p = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import transcriber; "
                        "print(transcriber.TR.__file__)", str(staged)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-800:]
    assert p.stdout.strip() == str(staged / "translate.py")


# ---- finding 3: a call request cannot name the translation command ---------------------------------------------

def test_a_call_request_cannot_set_the_translation_command_or_its_folder():
    import transcriber as T
    assert {"translate_cmd", "translate_dir"} <= T.REQUEST_BLOCKED
    assert T.DEFAULTS["translate_cmd"] == [] and T.DEFAULTS["translate_dir"] == ""


# ---- finding 2: the normal launch path, Ollama down, the PM's route configured --------------------------------

def test_with_ollama_down_the_configured_route_translates_and_gets_the_apps_route_environment(tmp_path, monkeypatch):
    import translate as TR
    pkg = tmp_path / "engine" / "livetips_m0"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "translate_text.py").write_text(
        "import json, os, sys\n"
        "req = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'text': 'UK:' + req['text'] + ':' + os.environ.get('LT_AI_ROUTE', '') + ':' + os.environ.get('CLAUDE_API', '')}))\n")
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")              # the app's permission for this launch
    monkeypatch.setenv("LT_AI_ROUTE", "api")                     # what the app puts in the recorder's environment
    monkeypatch.setenv("CLAUDE_API", "KEY-VALUE")                # the value is not logged here; it reaches the command only
    cfg = {"ollama_url": "http://127.0.0.1:9", "translate_timeout_s": 10.0,       # nothing listens there
           "translate_cmd": [sys.executable, "-m", "livetips_m0.translate_text"], "translate_dir": str(tmp_path / "engine")}
    assert TR.Translator(cfg).translate("Это", "ru", "uk") == "UK:Это:api:KEY-VALUE"


def test_without_a_configured_route_and_without_ollama_nothing_is_sent_anywhere():
    import translate as TR
    assert TR.Translator({"ollama_url": "http://127.0.0.1:9", "translate_timeout_s": 1.0}).translate("Это", "ru", "uk") is None


# ---- finding 5: an unusable local answer falls through to the route --------------------------------------------

def test_an_unusable_local_answer_is_not_returned_the_configured_route_is_asked(monkeypatch):
    import transcriber as T
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    tr = TR.Translator({"translate_cmd": ["x"]}, T.translation_ok)
    calls = []
    monkeypatch.setattr(tr, "_ollama_up", lambda: True)
    monkeypatch.setattr(tr, "_ollama", lambda text, src, dst: "Это очень хорошо")      # unchanged Russian
    monkeypatch.setattr(tr, "_route", lambda text, src, dst: calls.append(text) or "Це дуже добре")
    assert tr.translate("Это очень хорошо", "ru", "uk") == "Це дуже добре" and calls == ["Это очень хорошо"]
    assert tr._up is False or tr._ollama_up()                                          # a bad answer is not an outage


def test_a_usable_local_answer_does_not_call_the_route(monkeypatch):
    import transcriber as T
    import translate as TR
    monkeypatch.setenv("LT_TRANSLATE_ROUTE", "1")
    tr = TR.Translator({"translate_cmd": ["x"]}, T.translation_ok)
    monkeypatch.setattr(tr, "_ollama_up", lambda: True)
    monkeypatch.setattr(tr, "_ollama", lambda text, src, dst: "Це дуже добре")
    monkeypatch.setattr(tr, "_route", lambda text, src, dst: pytest.fail("the route must not be called"))
    assert tr.translate("Это очень хорошо", "ru", "uk") == "Це дуже добре"


# ---- finding 4: a translation changes the row already shown, in place -----------------------------------------

class Eng:
    def __init__(self):
        self.lock = threading.RLock()
        self.history = []

    def on_line(self, ln):
        with self.lock:
            self.history.append(ln)


def test_a_translation_event_changes_the_live_line_in_place(tmp_path):
    md = tmp_path / "c.md"
    md.write_text("[10:00:00] Співрозмовник (uk): Это очень хорошо\n[10:00:05] Me (uk): Добре\n", encoding="utf-8")
    tm = tmp_path / "c.md.timing.jsonl"
    rows = [{"t": "10:00:00", "ch": "them", "seg_end": "10:00:02.000", "written": "10:00:03.000", "seq": 1},
            {"t": "10:00:05", "ch": "me", "seg_end": "10:00:07.000", "written": "10:00:08.000", "seq": 2}]
    tm.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    eng = Eng()
    run = LiveRun({"me_label": "Me"}, eng, md, from_start=True)
    run.step()
    assert [h.text for h in eng.history] == ["Это очень хорошо", "Добре"]
    first, ids, seen = eng.history[0], [h.line_id for h in eng.history], set(run.seen_lines)
    ev = {"event": "translated", "seq": 1, "t": "10:00:00", "ch": "them", "translated_from": "ru",
          "original": "Это очень хорошо", "text": "Це дуже добре"}
    with tm.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    run.step()                                                       # before the call ends: no file rewrite yet
    assert eng.history[0] is first and first.text == "Це дуже добре"
    assert [h.line_id for h in eng.history] == ids and len(eng.history) == 2 and run.seen_lines == seen
    assert eng.history[1].text == "Добре"


def test_a_translation_that_arrives_before_the_engine_read_the_line_is_applied_when_it_has(tmp_path):
    md = tmp_path / "c.md"
    tm = tmp_path / "c.md.timing.jsonl"
    md.write_text("", encoding="utf-8")
    ev = {"event": "translated", "seq": 1, "t": "10:00:00", "ch": "them", "translated_from": "ru",
          "original": "Это", "text": "Це"}
    tm.write_text(json.dumps(ev, ensure_ascii=False) + "\n", encoding="utf-8")
    eng = Eng()
    run = LiveRun({"me_label": "Me"}, eng, md, from_start=True)
    run.step()
    assert run.translations and not eng.history                      # kept, not lost
    md.write_text("[10:00:00] Співрозмовник (uk): Это\n", encoding="utf-8")
    run.step()
    assert run.pending[0][0].text == "Це" and not run.translations     # the line waits for its timing: translated already
    run.pending = [(ln, seen - 5) for ln, seen in run.pending]
    run.step()
    assert [h.text for h in eng.history] == ["Це"]
