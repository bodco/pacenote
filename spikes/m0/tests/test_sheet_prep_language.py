"""PB-32 S4: the Meeting Sheet and the brief are written in the language the app resolved (the call language)."""
from livetips_m0 import localprep
from livetips_m0.sheet import prompt as P


def test_sheet_system_prompt_names_the_language():
    s = P.system("uk", ["uk", "en"])
    assert "Write the fact texts in Ukrainian" in s
    assert "Ukrainian, English" in s


def test_sheet_language_beyond_the_old_six():
    assert P.lang_name("it") == "Italian"
    assert P.lang_name("xx") == "xx"
    assert "Write the fact texts in Italian" in P.system("it", ["it"])


def test_brief_language_names():
    assert localprep.lang_name("en") == "English"
    assert localprep.lang_name("ru") == "Russian"
    assert localprep.lang_name("pt-BR") == "Portuguese"
    assert localprep.lang_name("") == "English"


def test_local_brief_requests_use_the_resolved_language_over_project_config(tmp_path, monkeypatch):
    """PB-32 round 2: a project config saying 'brief in Ukrainian' must not override the resolved English."""
    import json
    cfg = tmp_path / "config.md"
    cfg.write_text("Brief in Ukrainian\n", encoding="utf-8")
    (tmp_path / "skill" / "references").mkdir(parents=True)
    (tmp_path / "live").mkdir()
    seen = []

    def fake_chat(url, model, system, user, on_tokens, max_out=6000):
        seen.append(system)
        return {"text": "## Brief\nok\n", "prompt_eval_count": 1, "eval_count": 1}

    monkeypatch.setattr(localprep, "ollama_chat", fake_chat)
    req = {"title": "Sync", "when": "today", "project_name": "P", "model": "m", "live": str(tmp_path / "live"),
           "skill": str(tmp_path / "skill"), "base": str(tmp_path / "prep-x"), "config": str(cfg),
           "languages": ["en"], "brief_lang": "en", "use_project_folder": False, "root": str(tmp_path)}
    f = tmp_path / "req.json"
    f.write_text(json.dumps(req), encoding="utf-8")
    localprep.main(["--request", str(f)])
    assert seen, "no model request was made"
    assert not any("Ukrainian" in s for s in seen)
    assert any("English" in s for s in seen)
    brief = [s for s in seen if "Write the brief in English:" in s]
    assert brief and all("overrides any language the" in s for s in brief)      # authoritative over the config text in the pack
