import json

import pytest

from livetips_m0 import call_language, followup


@pytest.mark.parametrize(
    ("choice", "explicit", "expected", "name"),
    [(None, None, "uk", "Ukrainian"), ("it", None, "it", "Italian"), ("it", "en", "en", "English")],
)
def test_draft_and_revision_use_output_language(tmp_path, monkeypatch, capsys, choice, explicit, expected, name):
    md = tmp_path / "call.md"
    md.write_text("[13:01:04] Emily (uk): Підготуємо план.\n", encoding="utf-8")
    if choice:
        call_language.write_sidecar(md, output_language=choice)
    prompts = []

    def fake_ask(system, user, model, max_tokens, provider):
        prompts.append(system)
        return {"subject": "Plan", "body": "Draft"}, None

    monkeypatch.setattr(followup, "ask_json", fake_ask)
    args = ["--md", str(md)]
    if explicit:
        args += ["--lang", explicit]
    assert followup.main(args) == 0
    current = tmp_path / "draft.json"
    current.write_text(json.dumps({"subject": "Plan", "body": "Draft"}), encoding="utf-8")
    assert followup.main(args + ["--draft", str(current), "--instruction", "Shorter"]) == 0
    assert len(prompts) == 2
    assert all(name in prompt for prompt in prompts)
    assert [json.loads(line)["lang"] for line in capsys.readouterr().out.splitlines()] == [expected, expected]


def test_draft_reports_the_resolved_language(tmp_path, monkeypatch):
    md = tmp_path / "call.md"
    md.write_text("[13:01:04] Mike (uk): Підготуємо план.\n", encoding="utf-8")
    monkeypatch.setattr(followup, "ask_json", lambda *args: ({"body": "Draft"}, None))
    assert followup.draft(md)["lang"] == "uk"
