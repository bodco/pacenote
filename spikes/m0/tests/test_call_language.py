"""PB-32 S1 (FB2, D56): the output language resolver, the recorder adapter and the per-call sidecar.
The shared fixture table (Fixtures/output_language_cases.json) is also run by CoreChecks on the Swift resolver."""
import json
import re
from pathlib import Path

import pytest

from livetips_m0 import call_language as CL
from livetips_m0.callnotes import call_lang

APP = Path(__file__).absolute().parents[3]
CASES = json.loads((APP / "Fixtures/output_language_cases.json").read_text("utf-8"))


@pytest.mark.parametrize("c", CASES["resolve"], ids=lambda c: c["name"])
def test_resolve_cases(c):
    i = c["input"]
    assert CL.resolve(i.get("explicit"), i.get("dominant"), i.get("start_langs"), i.get("client_language", ""),
                      i.get("settings", "")) == c["expect"]


@pytest.mark.parametrize("c", CASES["hint"], ids=lambda c: c["text"] or "empty")
def test_hint_cases(c):
    assert CL.hint_from_client_language(c["text"]) == c["expect"]


@pytest.mark.parametrize("c", CASES["adapter"], ids=lambda c: c["name"])
def test_adapter_cases(c):
    sp = CL.spoken(c["transcript"], c["meta"])
    assert sp.dominant == c["dominant"]
    assert sp.languages() == c["languages"]


@pytest.mark.parametrize("c", CASES["toggle"], ids=lambda c: c["name"])
def test_toggle_cases(c):
    assert CL.toggle_languages(CL.spoken(c["transcript"], c["meta"]), c["start_langs"], c["after_call"]) == c["expect"]


@pytest.mark.parametrize("c", CASES["stale"], ids=lambda c: str(c["artifacts"]))
def test_stale_cases(c):
    assert CL.stale_artifacts(c["artifacts"], c["output"]) == c["expect"]


def test_without_the_recorder_field_it_is_todays_tag_majority():
    """Criterion 3: no dominant_language yet (PB-30 not merged, old call) gives the same value as callnotes.call_lang."""
    for c in CASES["adapter"]:
        if c["meta"] is None and re.search(r"\(\w\w\):", c["transcript"]):
            assert CL.spoken(c["transcript"]).dominant == call_lang(c["transcript"])


def test_regression_wrong_tags_with_a_dominant_field():
    """The reported bug: a Ukrainian call whose lines are mostly tagged en. With the recorder's dominant field the
    output language is Ukrainian; without it the old tag majority (en) stays, as today."""
    text = next(c["transcript"] for c in CASES["adapter"] if c["name"].startswith("wrong tags"))
    assert CL.resolve(dominant=CL.spoken(text).dominant) == "en"
    assert CL.resolve(dominant=CL.spoken(text, {"dominant_language": "uk"}).dominant) == "uk"


def test_the_interface_language_is_not_an_input():
    import inspect
    names = set(inspect.signature(CL.resolve).parameters) | set(inspect.signature(CL.output_language).parameters)
    assert not {n for n in names if "interface" in n or "ui" == n or "l10n" in n}


def test_language_names():
    assert CL.language_name("uk") == "Ukrainian" and CL.language_name("UK-ua") == "Ukrainian"
    assert CL.language_name("sv") == "sv"          # no name: written as the code, no crash
    assert CL.language_name(None) == "English"


def test_python_tables_match_the_rules_file():
    import yaml
    d = yaml.safe_load((APP / "rules/output_language.yaml").read_text("utf-8"))
    assert d["spoken_min_share"] == CL.SPOKEN_MIN_SHARE and d["max_languages"] == CL.MAX_LANGUAGES
    assert d["default"] == CL.DEFAULT and d["names"] == CL.NAMES
    assert tuple(d["client_language_hints"].items()) == CL.CLIENT_HINTS
    assert tuple(d["sidecar"]["fields"]["artifacts"]) == CL.ARTIFACTS
    assert d["sidecar"]["file"].startswith("<stem>" + CL.SIDECAR_SUFFIX)


def test_swift_resolver_carries_the_same_tables():
    s = (APP / "app/Sources/LiveTipsCore/CallLanguage.swift").read_text("utf-8")
    for key, code in CL.CLIENT_HINTS:
        assert f'("{key}", "{code}")' in s
    for code, name in CL.NAMES.items():
        assert f'"{code}": "{name}"' in s
    assert f"spokenMinShare = {CL.SPOKEN_MIN_SHARE}" in s and f"maxLanguages = {CL.MAX_LANGUAGES}" in s
    assert 'suffix = ".outlang.json"' in s


# ---- the per-call sidecar

def _call(tmp_path):
    d = tmp_path / "live" / "2026-11-21-sync"
    d.mkdir(parents=True)
    t = d / "2026-11-21-1000-sync.app.md"
    t.write_text("x", "utf-8")
    return t


def test_sidecar_absent_is_nothing_chosen(tmp_path):
    t = _call(tmp_path)
    assert CL.read_sidecar(t) == {"output_language": None, "set_by": None, "artifacts": {}}
    assert CL.output_language(t, "", None, start_langs=["uk"]) == "uk"


def test_sidecar_roundtrip_lives_in_work_and_keeps_fields(tmp_path):
    t = _call(tmp_path)
    CL.write_sidecar(t, artifact="notes", written_in="uk")
    CL.write_sidecar(t, output_language="EN")
    CL.write_sidecar(t, artifact="followup", written_in="uk")
    p = CL.sidecar_path(t)
    assert p.parent.name == ".work" and p.name == "2026-11-21-1000-sync.app.outlang.json"
    assert CL.read_sidecar(t) == {"output_language": "en", "set_by": "user", "artifacts": {"notes": "uk", "followup": "uk"}}
    assert CL.stale_artifacts(CL.read_sidecar(t)["artifacts"], "en") == ["notes", "followup"]
    assert CL.output_language(t, "", {"dominant_language": "uk"}) == "en"      # the explicit choice wins


def test_sidecar_ignores_bad_input_and_survives_a_half_written_file(tmp_path):
    t = _call(tmp_path)
    CL.write_sidecar(t, output_language="auto", artifact="nope", written_in="uk")
    assert CL.read_sidecar(t)["output_language"] is None and CL.read_sidecar(t)["artifacts"] == {}
    for bad in ("", "{", "[]", '{"output_language": 5, "artifacts": "x"}'):
        CL.sidecar_path(t).write_text(bad, "utf-8")
        assert CL.read_sidecar(t) == {"output_language": None, "set_by": None, "artifacts": {}}


def test_no_long_dash_in_the_new_files():
    for f in ("rules/output_language.yaml", "spikes/m0/livetips_m0/call_language.py",
              "app/Sources/LiveTipsCore/CallLanguage.swift", "Fixtures/output_language_cases.json"):
        assert "—" not in (APP / f).read_text("utf-8"), f
