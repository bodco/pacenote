"""PB-30 (D56), review round 6: the saved row carries the language the speech was in (" (← fr)" at its end), from the
first write, through the end-of-call rewrite, whether the translation succeeded, the model said SAME or no translator
answered. Every reader of the row format still parses the row (the prefix is unchanged)."""
import importlib.util
from pathlib import Path

from livetips_m0 import callnotes, wrapup_notion
from livetips_m0.transcript import parse_line
from test_call_language_pb30 import T  # noqa: F401  (T is a fixture)

REC = Path(__file__).resolve().parents[3] / "recorder"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(path):
    return [r for r in path.read_text(encoding="utf-8").split("\n") if r.startswith("[")]


def _call(T, tmp_path):
    w = T.Writer(tmp_path / "c.md", dict(T.DEFAULTS), "")
    ln = T.Line("them", 1_700_000_000.0, "en", "Je voudrais un cafe", sid="S1", translated_from="fr", seq=1)
    w.add(ln)
    w.add(T.Line("them", 1_700_000_005.0, "en", "Okay", sid="S1", seq=2))
    return w, ln


def test_translated_row_keeps_the_marker_through_the_rewrite(T, tmp_path):
    w, ln = _call(T, tmp_path)
    assert _rows(tmp_path / "c.md")[0].endswith(" (en): Je voudrais un cafe (← fr)")   # live, before any translation
    w.replace(ln, "I would like a coffee")
    w.close("\n")
    rows = _rows(tmp_path / "c.md")
    assert rows[0].endswith(" [S1] (en): I would like a coffee (← fr)")
    assert rows[1].endswith(" [S1] (en): Okay")                          # a line in the call's language has none


def test_same_or_no_translator_leaves_the_row_marked(T, tmp_path):
    w, _ln = _call(T, tmp_path)           # SAME and an unavailable translator both mean: no replace()
    w.close("\n")
    assert _rows(tmp_path / "c.md")[0].endswith(" (en): Je voudrais un cafe (← fr)")


def test_unknown_language_is_a_question_mark(T):
    assert T.tr_mark("und") == " (← ?)" and T.tr_mark(None) == "" and T.tr_mark("ru") == " (← ru)"


def test_every_row_reader_still_parses_a_marked_row(T, tmp_path):
    row = "[10:31:02] Emily Carter [S1] (en): I would like a coffee (← fr)"
    ln = parse_line(row, 0, "Me")                                        # the tips engine: words only, marker kept apart
    assert (ln.stag, ln.lang, ln.text, ln.translated_from) == ("S1", "en", "I would like a coffee", "fr")
    assert parse_line("[10:31:02] Me (uk): Добре (← ?)", 0, "Me").translated_from == "und"
    assert parse_line("[10:31:02] Me (uk): Добре", 0, "Me").translated_from is None
    m = T.TAGGED_LINE.match(row)                                         # post-call name and line edits
    assert m and (m.group(3), m.group(4)) == ("S1", "en")
    named = T.apply_speaker_names(row + "\n", T.DEFAULTS["them_label"], lambda t, sid: "Mike Brown")
    assert named == "[10:31:02] Mike Brown (en): I would like a coffee (← fr)\n"
    assert callnotes.LINE.match(row).group(3).startswith("I would like a coffee")
    assert wrapup_notion.LINE_RX.match(row).group(3) == "en"
    rd = _load("lt_read_delta_mark", REC / "bin" / "read_delta.py")
    assert rd.TAGGED_RX.match(row).group(4) == "en"
