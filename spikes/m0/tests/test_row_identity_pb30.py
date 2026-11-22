"""PB-30 (D56), review round 9: a translation targets the exact row by its seq, live (a row event read after the line)
and in the saved transcript (one of two identical rows)."""
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

from livetips_m0.live import LiveRun

SRC = Path(__file__).absolute().parents[3] / "recorder" / "src" / "transcriber.py"
sys.path.insert(0, str(SRC.parent))


@pytest.fixture(scope="module")
def T():
    spec = importlib.util.spec_from_file_location("lt_transcriber_pb30_r9", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lt_transcriber_pb30_r9"] = mod
    spec.loader.exec_module(mod)
    return mod


class Eng:
    def __init__(self):
        self.lock = threading.Lock()
        self.history = []

    def on_line(self, ln):
        with self.lock:
            self.history.append(ln)


def test_a_row_event_read_after_the_line_still_reaches_it_and_a_translation_never_hits_the_other_speaker(T, tmp_path):
    md, tm = tmp_path / "c.md", tmp_path / "c.md.timing.jsonl"
    tm.write_text("", encoding="utf-8")
    eng = Eng()
    run = LiveRun({"me_label": T.DEFAULTS["me_label"]}, eng, md, from_start=True)
    t0 = 1_700_000_000.0
    hms = T.hhmmss(t0)
    w = T.Writer(md, dict(T.DEFAULTS), "", them_speech=lambda: None)
    with tm.open("a", encoding="utf-8") as f:       # the timing entries are read first, the rows land after that read
        for ch in ("them", "them"):
            f.write(json.dumps(dict(t=hms, ch=ch, seg_end="10:00:02.000", written="10:00:03.000")) + "\n")
    run.read_timing()

    def publish(line):
        with tm.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event="row", seq=line.seq, t=hms, ch=line.channel, text=line.text)) + "\n")

    w.on_row = publish
    w.add(T.Line("them", t0, "en", "Okay, sounds good", sid="S1", seq=1))
    w.add(T.Line("them", t0, "en", "Okay, sounds good", sid="S2", seq=2))
    run.read_lines()
    run.feed_ready()
    assert [h.seq for h in eng.history] == [None, None]               # the row events are not read yet
    run.step()
    assert [h.seq for h in eng.history] == [1, 2]
    with tm.open("a", encoding="utf-8") as f:
        f.write(json.dumps(dict(event="translated", seq=2, t=hms, ch="them", translated_from="ru",
                                original="Okay, sounds good", text="Добре"), ensure_ascii=False) + "\n")
    run.step()
    assert [h.text for h in eng.history] == ["Okay, sounds good", "Добре"]


def test_a_saved_translation_replaces_the_exact_identical_row(T, tmp_path):
    md = tmp_path / "c.md"
    w = T.Writer(md, dict(T.DEFAULTS), "# header\n", them_speech=lambda: None)
    t0 = 1_700_000_000.0
    a = T.Line("me", t0, "uk", "Хорошо", seq=1)
    b = T.Line("me", t0, "uk", "Хорошо", seq=2)
    w.add(a)
    w.marker("muted")
    w.add(b)
    assert w.replace(b, "Добре")
    w.close("")
    rows = [ln for ln in md.read_text(encoding="utf-8").splitlines() if ln.startswith("[")]
    assert rows[0].endswith(": Хорошо") and rows[1].endswith(": Добре")
