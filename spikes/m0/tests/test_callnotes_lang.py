"""0.9.1 (owner): the call notes are written in the language the call was held in."""
from livetips_m0.callnotes import call_lang


def test_majority_of_line_tags():
    t = "# Live transcript: X\n- Languages: Я = en, allowed = en\n[13:00:58] Я (uk): Привіт\n[13:01:04] Співрозмовник [S1] (uk): Так\n[13:01:09] Я (en): ok\n"
    assert call_lang(t) == "uk"


def test_header_when_no_tags_and_default():
    assert call_lang("- Languages: Я = en, allowed = en\n") == "en"
    assert call_lang("nothing") == "uk"


# PB-32 S2 (FB2, D56): `auto` is the call's output language, not the line-tag majority alone.
import json
import types

from livetips_m0 import callnotes as CN
from livetips_m0 import call_language as CL

MOSTLY_EN_TAGS = ("# Live transcript: X\n- Languages: Me = uk, allowed = uk+en\n"
                  + "".join("[13:00:{:02d}] Me (en): Привіт, давайте почнемо {}\n".format(i, i) for i in range(8))
                  + "[13:01:30] Mike (uk): Добре\n[13:01:40] Mike (uk): Так\n")


class Prov:
    def __init__(self):
        self.systems = []

    def complete(self, model, system, user, max_tokens, cache_ttl=None):
        self.systems.append(system[0]["text"])
        return types.SimpleNamespace(text="## Summary\nok\n## Topics\n- t", error=None, model=model, input_tokens=1,
                                     cache_write_5m=0, cache_write_1h=0, cache_read=0, output_tokens=1)


def _call(tmp_path, text=MOSTLY_EN_TAGS):
    md = tmp_path / "2026-11-21-1300-sync.md"
    md.write_text(text)
    return md


def _run(md, lang="auto", meta=None):
    prov = Prov()
    res = CN.make_notes(md, "m", lang, provider=prov, meta=meta)
    assert res["ok"]
    return prov


def test_regression_dominant_field_beats_wrongly_tagged_lines(tmp_path):
    """A Ukrainian call whose lines are mostly tagged en comes out Ukrainian when the dominant field says uk."""
    prov = _run(_call(tmp_path), meta={"dominant_language": "uk"})
    assert prov.systems and all("Write in Ukrainian" in s for s in prov.systems[:1])
    assert not any("Write in English" in s for s in prov.systems)


def test_without_a_dominant_field_the_tag_majority_is_kept(tmp_path):
    assert notes_text(_run(_call(tmp_path))) == "English"


def notes_text(prov):
    return "English" if "Write in English" in prov.systems[0] else "Ukrainian"


def test_explicit_choice_in_the_sidecar_wins(tmp_path):
    md = _call(tmp_path)
    CL.write_sidecar(md, output_language="en")
    assert notes_text(_run(md, meta={"dominant_language": "uk"})) == "English"


def test_explicit_lang_argument_and_other_languages(tmp_path):
    assert CN.notes_lang(tmp_path / "a.md", "x", "pl") == "pl"
    prov = _run(_call(tmp_path), lang="pl")
    assert "Write in Polish" in prov.systems[0]


def test_notes_record_the_language_written(tmp_path):
    md = _call(tmp_path)
    _run(md, meta={"dominant_language": "uk"})
    assert CL.read_sidecar(md)["artifacts"] == {"notes": "uk", "meeting_notes": "uk"}


def test_interface_language_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_UI_LANG", "uk")
    assert notes_text(_run(_call(tmp_path))) == "English"


def test_named_transcript_uses_the_same_sidecar(tmp_path):
    md = _call(tmp_path)
    CL.write_sidecar(md, output_language="pl")
    named = md.with_name(md.name[:-3] + ".named.md")
    assert CN.notes_lang(named, "x", "auto") == "pl"
