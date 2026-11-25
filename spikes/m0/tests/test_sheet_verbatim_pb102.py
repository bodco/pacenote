"""PB-102 (Meeting Sheet, owner 24.11): the Verbatim block. Copied unchanged from a file or pasted text (optionally
sections by Markdown heading), never through the model, checked against the source after the write (a mismatch fails
the block visibly), several per call, kept in order and through Refresh (a changed file gives the new text), and sent
to the tips window by the recorder. Every fixture is invented here (Orbit, Mike Carter, Emily Parker)."""
import importlib
import json
import sys
from pathlib import Path

from livetips_m0.sheet import build as B
from livetips_m0.sheet import prompt as PR
from livetips_m0.sheet import schema as S
from livetips_m0.sheet import verbatim as V

REC = Path(__file__).resolve().parents[3] / "recorder" / "src"

SCRIPT = (
    "# Orbit demo script\r\n"
    "\r\n"
    "Intro line before the parts.\r\n"
    "\r\n"
    "## 1. Opening\r\n"
    "\r\n"
    "Thanks for joining, **Emily**. Today we show *three* things:\r\n"
    "\r\n"
    "- the export\r\n"
    "  - CSV and PDF\r\n"
    "- the pilot plan\r\n"
    "\r\n"
    "## 2. Export\r\n"
    "\r\n"
    "```\r\n"
    "## not a heading inside code\r\n"
    "```\r\n"
    "Exports run every night.\r\n"
    "\r\n"
    "### 2.1 Details\r\n"
    "\r\n"
    "1. Pick a range\r\n"
    "2. Press Export\r\n"
    "\r\n"
    "## 3. Pilot\r\n"
    "\r\n"
    "Two weeks, five seats.\r\n"
    "\r\n"
    "## 4. Close\r\n"
    "\r\n"
    "> Ask for the sign-off date.\r\n"
)
PASTED = "Opening line, word for word.\n\nDo **not** promise a date."
ANSWER = {"goal": "Agree the pilot", "blocks": {"overview": [{"text": "Orbit wants a pilot", "source_ids": ["s-meeting"]}]}}


def quiet(**kw):
    pass


def _project(tmp_path):
    root = tmp_path / "Orbit"
    (root / "live").mkdir(parents=True)
    script = root / "demo-script.md"
    script.write_bytes(SCRIPT.encode("utf-8"))
    return root, script


def _blocks(script, sections="2-3"):
    return ["header", "my_focus",
            {"type": "verbatim", "id": "b-verbatim-aa11", "title": "Demo script",
             "verbatim": {"source": {"kind": "file", "path": str(script)}, "sections": sections}},
            "overview",
            {"type": "verbatim", "id": "b-verbatim-bb22", "title": "Do not say",
             "verbatim": {"source": {"kind": "text", "text": PASTED}}},
            "questions"]


def _req(root, blocks, **kw):
    r = {"preset": "blank", "route": "fake", "language": "en", "auto_sources": False, "blocks": blocks,
         "meeting": {"title": "Orbit demo", "start": "2026-11-26T10:00:00+02:00"},
         "participants": {"ours": [{"name": "Mike Carter"}], "theirs": [{"name": "Emily Parker", "company": "Orbit"}]},
         "project": {"slug": "orbit", "root": str(root)}, "today": "2026-11-25", "sources": [],
         "base": str(root / "live" / "prep-2026-11-26-1000-orbit-demo")}
    r.update(kw)
    return r


def _sheet(res):
    return json.loads(Path(res["sheet"]).read_text(encoding="utf-8"))


def _vb(sheet):
    return [b for b in sorted(sheet["blocks"], key=lambda b: b["order"]) if b["type"] == "verbatim"]


# ---------------------------------------------------------------- sections and copy (unit)

def test_sections_by_heading_ignore_code_fences_and_pick_exact_slices():
    secs = V.sections(SCRIPT)
    # one "# " title and four "## " parts: the parts are the sections; "## " inside the code fence is not one
    assert [t for _, _, t in secs] == ["1. Opening", "2. Export", "3. Pilot", "4. Close"]
    two, err = V.pick(SCRIPT, "2-3")
    assert err is None and two.startswith("## 2. Export") and "### 2.1 Details" in two and "## 3. Pilot" in two
    assert "## 4. Close" not in two and "## 1. Opening" not in two
    assert two in SCRIPT and "\r\n" in two                        # one unchanged slice, line ends kept
    assert V.pick(SCRIPT, "")[0] == SCRIPT                           # no pick: the whole text
    assert V.pick(SCRIPT, " 4 ")[0].startswith("## 4. Close")
    assert V.pick(SCRIPT, "2–3")[0] == two                      # a typed en dash range reads as 2-3
    assert V.pick(SCRIPT, "5") == (None, "the source has only 4 section(s)")
    assert V.pick(SCRIPT, "3-1")[1] and V.pick(SCRIPT, "one")[1] and V.pick(SCRIPT, "1,3")[1]
    assert V.pick("No headings here.", "1")[1] == "the source has no headings to count sections by"
    flat = "# A\ntext a\n# B\ntext b\n"
    assert [t for _, _, t in V.sections(flat)] == ["A", "B"]


def test_the_model_never_sees_a_verbatim_block():
    lines = PR.block_lines([{"type": "verbatim", "title": "Demo script"}, {"type": "overview", "title": "Overview"}],
                           {"verbatim": {"fields": "copied"}, "overview": {"fields": "who they are"}}, {}, [], "en")
    assert "verbatim" in PR.SKIP_BLOCKS
    assert "Demo script" not in lines and "verbatim" not in lines and '- overview ("Overview")' in lines


# ---------------------------------------------------------------- build: copied unchanged, several, in order

def test_build_copies_unchanged_several_blocks_in_order_and_outside_the_model(tmp_path):
    root, script = _project(tmp_path)
    seen = []
    res = B.build(_req(root, _blocks(script)), emit=quiet, responder=lambda s, u: (seen.append(u), json.dumps(ANSWER))[1])
    assert res.get("done"), res
    sheet = _sheet(res)
    assert S.validate(sheet) == []
    order = [b["type"] for b in sorted(sheet["blocks"], key=lambda b: b["order"])]
    assert order == ["header", "my_focus", "verbatim", "overview", "verbatim", "questions"]   # first after My focus
    a, b = _vb(sheet)
    assert a["id"] == "b-verbatim-aa11" and a["title"] == "Demo script" and b["id"] == "b-verbatim-bb22"
    assert a["verbatim"]["status"] == "ok" and a["verbatim"]["text"] == V.pick(SCRIPT, "2-3")[0]
    assert b["verbatim"]["status"] == "ok" and b["verbatim"]["text"] == PASTED
    assert a["facts"] == [] and b["facts"] == []
    # nothing of the copies reached the model (a file that is not a call source is not sent either)
    assert seen and not any("Exports run every night" in u or "word for word" in u for u in seen)
    base = Path(res["sheet"]).with_suffix("").with_suffix("")
    md = Path(str(base) + ".sheet.md").read_text(encoding="utf-8")
    assert "## Demo script" in md and "Exports run every night." in md and "Do **not** promise a date." in md
    brief = Path(str(base) + ".brief.md").read_text(encoding="utf-8")
    assert "Exports run every night" not in brief and "promise a date" not in brief   # the parsed, editable brief stays clean
    for suffix in (".cards.json", ".facts.json"):
        assert "Exports run every night" not in Path(str(base) + suffix).read_text(encoding="utf-8")


def test_two_verbatim_blocks_with_the_same_id_stay_two_blocks(tmp_path):
    root, script = _project(tmp_path)
    bl = ["header", {"type": "verbatim", "id": "x", "verbatim": {"source": {"kind": "text", "text": "one"}}},
          {"type": "verbatim", "id": "x", "verbatim": {"source": {"kind": "text", "text": "two"}}}]
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    sheet = _sheet(res)
    assert S.validate(sheet) == [] and [v["verbatim"]["text"] for v in _vb(sheet)] == ["one", "two"]
    assert len({v["id"] for v in _vb(sheet)}) == 2 and all(v["title"] == "Verbatim" for v in _vb(sheet))


# ---------------------------------------------------------------- Refresh

def test_refresh_without_a_block_list_keeps_the_blocks_and_takes_the_new_file_text(tmp_path):
    root, script = _project(tmp_path)
    req = _req(root, _blocks(script))
    assert B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER)).get("done")
    script.write_bytes(SCRIPT.replace("Two weeks, five seats.", "Three weeks, eight seats.").encode("utf-8"))
    again = {k: v for k, v in req.items() if k != "blocks"}            # Refresh on the page: the preset's blocks
    res = B.build(again, refresh=True, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    assert res.get("done"), res
    sheet = _sheet(res)
    assert S.validate(sheet) == []
    a, b = _vb(sheet)
    assert (a["id"], b["id"]) == ("b-verbatim-aa11", "b-verbatim-bb22") and a["title"] == "Demo script"
    assert "Three weeks, eight seats." in a["verbatim"]["text"] and "Two weeks" not in a["verbatim"]["text"]
    assert b["verbatim"]["text"] == PASTED
    order = [x["type"] for x in sorted(sheet["blocks"], key=lambda x: x["order"])]
    assert order[:3] == ["header", "my_focus", "verbatim"]             # still first after My focus


def test_refresh_with_the_wizard_list_takes_its_order_and_settings(tmp_path):
    root, script = _project(tmp_path)
    req = _req(root, _blocks(script))
    assert B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER)).get("done")
    bl = _blocks(script, sections="4")
    bl = [bl[0], bl[1], bl[3], bl[2]]                                  # moved below Overview, the pasted one removed
    res = B.build(dict(req, blocks=bl), refresh=True, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    sheet = _sheet(res)
    vb = _vb(sheet)
    assert [v["id"] for v in vb] == ["b-verbatim-aa11"] and vb[0]["verbatim"]["text"].startswith("## 4. Close")
    assert [x["type"] for x in sorted(sheet["blocks"], key=lambda x: x["order"])] == ["header", "my_focus", "overview", "verbatim"]


def test_refresh_after_a_move_on_the_sheet_page_takes_the_new_text_and_keeps_the_move(tmp_path):
    root, script = _project(tmp_path)
    req = _req(root, _blocks(script))
    res = B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    p = Path(res["sheet"])
    sheet = json.loads(p.read_text(encoding="utf-8"))
    a = next(b for b in sheet["blocks"] if b["id"] == "b-verbatim-aa11")
    a["edited_by_user"] = True                      # the app's Move down / hide on the sheet page sets this flag
    a["visible"] = False
    p.write_text(json.dumps(sheet), encoding="utf-8")
    script.write_bytes(SCRIPT.replace("Exports run every night.", "Exports run every hour.").encode("utf-8"))
    res = B.build({k: v for k, v in req.items() if k != "blocks"}, refresh=True, emit=quiet,
                  responder=lambda s, u: json.dumps(ANSWER))
    assert res.get("done"), res
    a = next(b for b in _sheet(res)["blocks"] if b["id"] == "b-verbatim-aa11")
    assert a["verbatim"]["status"] == "ok" and "Exports run every hour." in a["verbatim"]["text"]   # never the old copy
    assert a["visible"] is False and a["edited_by_user"] is True


def test_page_refresh_with_the_saved_request_takes_the_new_text(tmp_path):
    root, script = _project(tmp_path)
    req = _req(root, _blocks(script))
    res = B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    saved = json.loads(Path(req["base"] + ".sheet.request.json").read_text(encoding="utf-8"))   # what the page's Refresh reads
    assert [b.get("id") for b in saved["blocks"] if isinstance(b, dict)] == ["b-verbatim-aa11", "b-verbatim-bb22"]
    script.write_bytes(SCRIPT.replace("Two weeks, five seats.", "Four weeks.").encode("utf-8"))
    res = B.build(saved, refresh=True, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    a, b = _vb(_sheet(res))
    assert "Four weeks." in a["verbatim"]["text"] and b["verbatim"]["text"] == PASTED


# ---------------------------------------------------------------- the check: a mismatch fails visibly

def test_a_copy_that_differs_from_the_source_when_checked_fails_the_block(tmp_path, monkeypatch):
    root, script = _project(tmp_path)
    real = V.read_source
    calls = {"n": 0}

    def changing(src):
        calls["n"] += 1
        t, why = real(src)
        # the first read fills the block; the check after the write reads a source that is now different
        return (t if calls["n"] == 1 else t.replace("Exports run every night.", "Exports run hourly.")), why
    monkeypatch.setattr(V, "read_source", changing)
    bl = ["header", {"type": "verbatim", "id": "b-verbatim-aa11", "title": "Demo script",
                     "verbatim": {"source": {"kind": "file", "path": str(script)}, "sections": "2"}}]
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    assert res.get("done"), res
    sheet = _sheet(res)
    (v,) = _vb(sheet)
    assert v["verbatim"]["status"] == "failed" and v["verbatim"]["text"] == ""
    assert v["verbatim"]["error"] == "the copy does not match the source" and "sha256" not in v["verbatim"]
    assert any("Demo script" in n and "does not match" in n for n in res["notes"])
    assert S.validate(sheet) == []
    md = Path(str(Path(res["sheet"]).with_suffix("").with_suffix("")) + ".sheet.md").read_text(encoding="utf-8")
    assert "Not copied: the copy does not match the source" in md and "Exports run" not in md


def test_check_catches_a_changed_copy_and_passes_an_exact_one(tmp_path):
    root, script = _project(tmp_path)
    b = {"type": "verbatim", "title": "T", "verbatim": {"source": {"kind": "file", "path": str(script)}, "sections": "1"}}
    V.fill(b, "2026-11-25T10:00:00Z")
    assert b["verbatim"]["status"] == "ok" and V.check(b) is None
    b["verbatim"]["text"] = b["verbatim"]["text"].replace("**Emily**", "Emily")      # bold dropped = not verbatim
    assert V.check(b) == "the copy does not match the source"
    assert V.check_written({"blocks": [b]}, "2026-11-25T10:00:01Z") == ["T"] and b["verbatim"]["status"] == "failed"


def test_missing_file_bad_range_and_empty_paste_fail_with_a_reason(tmp_path):
    root, script = _project(tmp_path)
    bl = ["header",
          {"type": "verbatim", "id": "v1", "title": "Gone", "verbatim": {"source": {"kind": "file", "path": str(root / "nope.md")}}},
          {"type": "verbatim", "id": "v2", "title": "Range", "verbatim": {"source": {"kind": "file", "path": str(script)}, "sections": "9"}},
          {"type": "verbatim", "id": "v3", "title": "Empty", "verbatim": {"source": {"kind": "text", "text": "  "}}},
          {"type": "verbatim", "id": "v4", "title": "Unset"}]
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    assert res.get("done"), res
    sheet = _sheet(res)
    errs = {v["title"]: (v["verbatim"]["status"], v["verbatim"]["error"], v["verbatim"]["text"]) for v in _vb(sheet)}
    assert errs == {"Gone": ("failed", "file not found: nope.md", ""), "Range": ("failed", "the source has only 4 section(s)", ""),
                    "Empty": ("failed", "no text pasted", ""), "Unset": ("failed", "no source chosen", "")}
    assert S.validate(sheet) == []


def test_non_utf8_markdown_fails_instead_of_guessing(tmp_path):
    p = tmp_path / "latin.md"
    p.write_bytes("café".encode("latin-1"))
    assert V.read_source({"kind": "file", "path": str(p)}) == (None, "the file is not UTF-8 text")


def test_schema_rejects_an_unknown_verbatim_status():
    sheet = {"schema": "pacenote.sheet/1", "id": "s", "title": "t", "meeting_ref": {"start": "2026-11-26"}, "mode": "quick",
             "preset": "blank", "language": "en", "sources": [],
             "blocks": [{"id": "v", "type": "verbatim", "title": "V", "visible": True, "only_if_data": False, "order": 0,
                         "facts": [], "edited_by_user": False, "verbatim": {"status": "maybe", "text": ""}}]}
    assert any("verbatim.status" in e for e in S.validate(sheet))


# ---------------------------------------------------------------- the live window (recorder)

def test_recorder_sends_the_verbatim_blocks_to_the_tips_window(tmp_path):
    root, script = _project(tmp_path)
    bl = _blocks(script) + [{"type": "verbatim", "id": "v-bad", "title": "Broken",
                             "verbatim": {"source": {"kind": "file", "path": str(root / "gone.md")}}},
                            {"type": "verbatim", "id": "v-hidden", "title": "Hidden", "visible": False,
                             "verbatim": {"source": {"kind": "text", "text": "secret"}}}]
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    sheet = _sheet(res)
    sys.path.insert(0, str(REC))
    tr = importlib.import_module("transcriber")
    items = tr.sheet_verbatim(sheet)
    assert [x["id"] for x in items] == ["b-verbatim-aa11", "b-verbatim-bb22", "v-bad"]      # in order, hidden left out
    assert items[0]["ok"] and items[0]["text"] == V.pick(SCRIPT, "2-3")[0]                   # exact, CRLF and all
    assert items[2] == {"id": "v-bad", "title": "Broken", "ok": False, "text": "", "error": "file not found: gone.md"}
    stem = root / "live" / "call"
    Path(str(stem) + ".sheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    tips = root / "live" / "call.tips.jsonl"
    live = tr.SheetLive(Path(str(stem) + ".md"), tips)
    assert live.refresh()
    rows = [json.loads(x) for x in tips.read_text(encoding="utf-8").splitlines()]
    vb = [r for r in rows if r.get("kind") == "verbatim"]
    assert len(vb) == 1 and vb[0]["items"] == items and "uid" not in vb[0]
    # a rewrite of the sheet with the same Verbatim blocks writes no second copy; a change does
    import os
    os.utime(str(stem) + ".sheet.json", (1, 1))
    assert live.refresh()
    assert sum(1 for x in tips.read_text(encoding="utf-8").splitlines() if '"kind": "verbatim"' in x) == 1
    sheet["blocks"] = [x for x in sheet["blocks"] if x["type"] != "verbatim"]
    Path(str(stem) + ".sheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    os.utime(str(stem) + ".sheet.json", (2, 2))
    assert live.refresh()
    last = [json.loads(x) for x in tips.read_text(encoding="utf-8").splitlines() if '"kind": "verbatim"' in x]
    assert len(last) == 2 and last[-1]["items"] == []                # Refresh removed them: the window clears them


def test_recorder_sends_nothing_for_a_sheet_without_verbatim(tmp_path):
    sys.path.insert(0, str(REC))
    tr = importlib.import_module("transcriber")
    sheet = {"blocks": [{"id": "b-overview", "type": "overview", "facts": []}]}
    assert tr.sheet_verbatim(sheet) == []
    stem = tmp_path / "call"
    Path(str(stem) + ".sheet.json").write_text(json.dumps(dict(sheet, schema="pacenote.sheet/1")), encoding="utf-8")
    tips = tmp_path / "call.tips.jsonl"
    tr.SheetLive(Path(str(stem) + ".md"), tips).refresh()
    text = tips.read_text(encoding="utf-8") if tips.exists() else ""
    assert '"verbatim"' not in text
