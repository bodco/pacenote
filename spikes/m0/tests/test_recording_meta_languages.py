"""PB-32 round 6: the language fields the recorder persists (call_layout.save_spoken, called when the recording ends
and after the end-of-call correction) are what both output-language readers use. The file the recorder's own copy of
call_layout writes is Fixtures/recording_meta_languages.json; CoreChecks reads that same file with the Swift reader and
saves it through RecordingMeta."""
import importlib.util
import json
from pathlib import Path

from livetips_m0 import call_language as CL

APP = Path(__file__).absolute().parents[3]
FIXTURE = APP / "Fixtures/recording_meta_languages.json"
WRONG_TAGS = "".join(f"[10:00:0{i}] Me (en): line\n" for i in range(6))     # every line tag says English
SECS = {"uk": 1260.54, "en": 310.2, "de": 0.0}                             # LangShare.secs at the end of a call


def _recorder_layout():
    spec = importlib.util.spec_from_file_location("recorder_call_layout", APP / "recorder/src/call_layout.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call(tmp_path):
    d = tmp_path / "live" / "2026-11-22-mixed"
    d.mkdir(parents=True)
    t = d / "2026-11-22-1000-mixed.app.md"
    t.write_text(WRONG_TAGS, "utf-8")
    return t


def _app_parts(t):
    """What the app's RecordingMeta.save left there when the screen recording stopped."""
    p = CL.meta_path(t)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "title": "Mixed", "project": "Orbit", "parts": [
        {"file": "2026-11-22-1000-mixed.recording.mp4", "kind": "video", "started": "2026-11-22T10:00:00Z", "seconds": 1800}]}), "utf-8")


def test_the_recorder_writes_the_fields_both_readers_use_and_keeps_the_app_parts(tmp_path):
    rec = _recorder_layout()
    t = _call(tmp_path)
    _app_parts(t)
    assert rec.save_spoken(t, SECS, "uk") == CL.meta_path(t)                 # the recorder and the reader agree on the file
    got = json.loads(CL.meta_path(t).read_text("utf-8"))
    assert got == json.loads(FIXTURE.read_text("utf-8"))                       # the file CoreChecks reads in Swift
    assert got["parts"][0]["seconds"] == 1800                                  # the app's recording parts stay
    sp = CL.spoken(WRONG_TAGS, CL.load_meta(t))
    assert sp.dominant == "uk" and sp.languages() == ["uk", "en"]              # not the wrong tags
    assert CL.output_language(t, WRONG_TAGS) == "uk"
    assert CL.output_language(t.with_name("2026-11-22-1000-mixed.app.named.md"), WRONG_TAGS) == "uk"


def test_after_the_correction_the_second_write_replaces_the_first(tmp_path):
    rec = _recorder_layout()
    t = _call(tmp_path)
    rec.save_spoken(t, {"en": 900.0, "uk": 100.0}, "en")                     # what the live recognition counted
    assert CL.output_language(t, WRONG_TAGS) == "en"
    rec.save_spoken(t, {"uk": 700.0, "en": 300.0}, None)                     # the corrected transcript, no dominant field
    d = json.loads(CL.meta_path(t).read_text("utf-8"))
    assert "dominant_language" not in d and d["parts"] == [] and d["schema"] == 1     # a new file still has the app's fields
    assert CL.output_language(t, WRONG_TAGS) == "uk"


def test_the_recorder_and_the_app_copy_of_the_writer_are_one(tmp_path):
    assert (APP / "recorder/src/call_layout.py").read_text("utf-8") == (APP / "spikes/m0/livetips_m0/call_layout.py").read_text("utf-8")
