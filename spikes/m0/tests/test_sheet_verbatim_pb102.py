"""PB-102 (Meeting Sheet, owner 24.11): the Verbatim block. Copied unchanged from a file or pasted text (optionally
sections by Markdown heading), never through the model, checked against the source after the write (a mismatch fails
the block visibly), several per call, kept in order and through Refresh (a changed file gives the new text), and sent
to the tips window by the recorder. Every fixture is invented here (Orbit, Mike Carter, Emily Parker)."""
import importlib
import json
import sys
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------- review round 1: the next call of the series

PRIVATE = "Exports run every night"        # in the private script (not a call source) and in the copy only


def _call_one(tmp_path):
    root, script = _project(tmp_path)
    res = B.build(_req(root, _blocks(script)), emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    assert res.get("done"), res
    base = str(Path(res["sheet"]))[:-len(".sheet.json")]
    assert PRIVATE in Path(base + ".sheet.md").read_text(encoding="utf-8")       # the human copy keeps the text
    return root, base


def _clean(text):
    return PRIVATE not in text and "word for word" not in text and "promise a date" not in text


@pytest.mark.parametrize("drop_md", [False, True])
def test_the_next_call_of_the_series_never_sends_a_verbatim_copy_to_the_model(tmp_path, drop_md):
    root, base = _call_one(tmp_path)
    if drop_md:                                   # the fallback: the previous .sheet.json is the source
        Path(base + ".sheet.md").unlink()
    seen = []
    nxt = str(root / "live" / "prep-2026-12-03-1000-orbit-demo")
    res = B.build(_req(root, ["header", "overview"], auto_sources=True, base=nxt,
                       meeting={"title": "Orbit demo", "start": "2026-12-03T10:00:00+02:00"}),
                  emit=quiet, responder=lambda s, u: (seen.append(s + "\n" + u), json.dumps(ANSWER))[1])
    assert res.get("done"), res
    rep = Path(nxt + ".sheet.sources.json").read_text(encoding="utf-8")
    assert "Previous Meeting Sheet" in rep                                 # the previous sheet was a source
    assert seen and "Orbit wants a pilot" in seen[-1]                     # and its facts still reach the model
    assert all(_clean(u) for u in seen)


def test_a_sheet_file_as_a_source_reads_without_verbatim_copies_on_every_route(tmp_path):
    from livetips_m0.sheet import sources as SRC
    root, base = _call_one(tmp_path)
    for suffix in (".sheet.md", ".sheet.json", ".sheet.request.json"):
        for t in ("prev_call", "file"):
            s = {"id": "s1", "type": t, "path": base + suffix}
            texts = {}
            SRC.check(s, {}, texts, "code", {})
            assert s["access"] == "ok" and texts["s1"].strip() and _clean(texts["s1"]), (suffix, t)
            # the Claude Code route opens a file itself: a cleaned copy, never the original
            files = B.claude_files([s], texts, str(tmp_path / "cache"))
            assert files["s1"] != base + suffix and _clean(Path(files["s1"]).read_text(encoding="utf-8"))
    md = V.sheet_file_text(base + ".sheet.md")
    assert "Orbit wants a pilot" in md and "## Demo script" not in md


def test_the_claude_code_route_gets_a_clean_copy_of_the_previous_sheet(tmp_path, monkeypatch):
    root, base = _call_one(tmp_path)
    got = {}

    def fake_claude(req, prompt, files, hosts, emit, usable):
        got.update(prompt=prompt, files=dict(files))
        return json.dumps(ANSWER), {}
    monkeypatch.setattr(B, "run_claude", fake_claude)
    nxt = str(root / "live" / "prep-2026-12-03-1000-orbit-demo")
    res = B.build(_req(root, ["header", "overview"], auto_sources=True, base=nxt, route="code",
                       meeting={"title": "Orbit demo", "start": "2026-12-03T10:00:00+02:00"}), emit=quiet)
    assert res.get("done"), res
    assert S.validate(_sheet(res)) == []
    assert got["files"] and _clean(got["prompt"])
    prev = [p for p in got["files"].values() if "sheet-cache" in p]
    assert prev and all(not p.startswith(base) for p in got["files"].values())         # never the original sheet file
    for p in got["files"].values():
        assert _clean(Path(p).read_text(encoding="utf-8")), p


def test_after_refresh_every_sheet_file_reads_without_the_old_or_new_copy(tmp_path):
    root, script = _project(tmp_path)
    req = _req(root, _blocks(script))
    assert B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER)).get("done")
    script.write_bytes(SCRIPT.replace("Two weeks, five seats.", "Three weeks, eight seats.").encode("utf-8"))
    res = B.build(req, refresh=True, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    assert res.get("done"), res
    base = str(Path(res["sheet"]))[:-len(".sheet.json")]
    files = [base + s for s in (".sheet.json", ".sheet.md", ".sheet.request.json")] + \
        [str(p) for p in Path(base).parent.glob(Path(base).name + ".sheet.v*.json")]
    assert len(files) >= 5                                    # versions 1 and 2 as well
    for f in files:
        text = V.sheet_file_text(f)
        assert text is not None and _clean(text) and "Two weeks" not in text and "Three weeks" not in text, f


def test_a_sheet_in_a_call_folder_finds_its_json_in_work(tmp_path):
    root, script = _project(tmp_path)
    req = _req(root, _blocks(script))
    req["base"] = B.base_of({"project": {"root": str(root)}, "meeting": req["meeting"]})
    res = B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    assert res.get("done"), res
    assert "/.work/" in res["sheet"]
    md = str(B.CL.sidecar(req["base"], ".sheet.md"))
    assert "/.work/" not in md
    assert PRIVATE in Path(md).read_text(encoding="utf-8")
    text = V.sheet_file_text(md)
    assert _clean(text) and "Orbit wants a pilot" in text and V.MD_OPEN not in text     # rendered from the JSON


def test_a_sheet_md_without_its_json_still_drops_the_copies(tmp_path):
    root, base = _call_one(tmp_path)
    Path(base + ".sheet.json").unlink()
    text = V.sheet_file_text(base + ".sheet.md")
    assert text is not None and _clean(text) and "Orbit wants a pilot" in text


def test_a_sheet_without_verbatim_and_other_files_read_as_before(tmp_path):
    root, script = _project(tmp_path)
    res = B.build(_req(root, ["header", "overview"]), emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    base = str(Path(res["sheet"]))[:-len(".sheet.json")]
    assert V.sheet_file_text(base + ".sheet.md") is None and V.sheet_file_text(base + ".sheet.json") is None
    assert V.sheet_file_text(str(script)) is None


def test_evidence_ingest_reads_sheet_files_without_verbatim_copies(tmp_path):
    from livetips_m0.evidence import ingest as IN
    root, base = _call_one(tmp_path)
    for suffix in (".sheet.md", ".sheet.json"):
        text = IN.read_text(base + suffix)
        assert text.strip() and _clean(text) and "Orbit wants a pilot" in text, suffix
