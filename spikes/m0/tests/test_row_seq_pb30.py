"""PB-30 (D56), review round 8: the writer publishes a row's seq when it appends the row; a mic line it drops as echo
has a timing entry but no row, and must not shift the seq of the rows that were written."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from livetips_m0.live import LiveRun

SRC = Path(__file__).absolute().parents[3] / "recorder" / "src" / "transcriber.py"
sys.path.insert(0, str(SRC.parent))


@pytest.fixture(scope="module")
def T():
    spec = importlib.util.spec_from_file_location("lt_transcriber_pb30_r8", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lt_transcriber_pb30_r8"] = mod
    spec.loader.exec_module(mod)
    return mod


class Eng:
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.history = []

    def on_line(self, ln):
        with self.lock:
            self.history.append(ln)


def test_an_echo_dropped_mic_line_does_not_shift_the_seq_of_written_rows(T, tmp_path):
    md, tm = tmp_path / "c.md", tmp_path / "c.md.timing.jsonl"
    tm.write_text("", encoding="utf-8")
    w = T.Writer(md, dict(T.DEFAULTS), "", them_speech=lambda: None)   # nobody talks: a mic line is written at once

    def publish(line):                                                 # the shape transcriber.publish_row writes
        with tm.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "row", "seq": line.seq, "t": T.hhmmss(line.t), "ch": line.channel,
                                "text": line.text}, ensure_ascii=False) + "\n")

    w.on_row = publish
    t0 = 1_700_000_000.0
    lines = [T.Line("them", t0, "uk", "Це дуже добре, дякую вам за час", seq=1),
             T.Line("me", t0, "uk", "Це дуже добре, дякую вам за час", seq=2),     # echo of the system line: dropped
             T.Line("me", t0, "uk", "Это очень хорошо", seq=3, translated_from="ru"),
             T.Line("me", t0, "uk", "Хорошо", seq=4, translated_from="ru")]
    for ln in lines:                                                   # process(): add(), then a timing entry per segment
        w.add(ln)
        with tm.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": T.hhmmss(ln.t), "ch": ln.channel, "seg_end": "10:00:02.000",
                                "written": "10:00:03.000", "seq": ln.seq}) + "\n")
    rows = [json.loads(l) for l in tm.read_text(encoding="utf-8").splitlines() if '"event"' in l]
    assert [r["seq"] for r in rows] == [1, 3, 4]                      # no row event for the dropped line (seq 2)

    eng = Eng()
    run = LiveRun({"me_label": T.DEFAULTS["me_label"]}, eng, md, from_start=True)
    run.step()
    assert [h.seq for h in eng.history] == [1, 3, 4]
    with tm.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "translated", "seq": 3, "t": T.hhmmss(t0), "ch": "me", "translated_from": "ru",
                            "original": "Это очень хорошо", "text": "Це дуже добре"}, ensure_ascii=False) + "\n")
    run.step()
    assert [h.text for h in eng.history][1:] == ["Це дуже добре", "Хорошо"]                      # the next row is untouched
