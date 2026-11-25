"""PB-101 (Meeting Sheet, owner 24.11): custom blocks. Each has a title, an instruction, its own sources (a file with a
section pick, a folder, a call source, a past call's transcript, a link, free text), an output format and a length;
several per call, answered under their own ids from their own sources, kept through Refresh, format verbatim reuses the
PB-102 Verbatim block, and the recorder sends every custom block to the tips window. Every fixture is invented here
(Orbit, Mike Carter, Emily Parker)."""
import importlib
import json
import sys
from pathlib import Path

from livetips_m0.sheet import build as B
from livetips_m0.sheet import custom as C
from livetips_m0.sheet import merge as M
from livetips_m0.sheet import prompt as PR
from livetips_m0.sheet import schema as S
from livetips_m0.sheet import verbatim as V

REC = Path(__file__).resolve().parents[3] / "recorder" / "src"

PLAYBOOK = (
    "# Orbit playbook\n\n"
    "## 1. Pricing\n\n- Seat price 40 USD\n- Pilot is free for two weeks\n\n"
    "## 2. Objections\n\n- Too expensive: point to the pilot\n- No time: offer a 20 minute setup\n\n"
    "## 3. Internal only\n\nMargin floor is 22 percent.\n"
)
NOTE = "Emily asked twice about SSO. Mike promised a security sheet."


def quiet(**kw):
    pass


def _project(tmp_path):
    root = tmp_path / "Orbit"
    (root / "live").mkdir(parents=True)
    pb = root / "playbook.md"
    pb.write_text(PLAYBOOK, encoding="utf-8")
    past = root / "live" / "2026-11-20-orbit-sync.md"
    past.write_text("[10:02] Emily Parker: we need SSO before the pilot.\n", encoding="utf-8")
    docs = root / "docs"
    docs.mkdir()
    (docs / "security.md").write_text("SSO through Okta is on the plan for Q1.\n", encoding="utf-8")
    return root, pb, past, docs


def _custom(bid, title, fmt, length, sources, instruction="", lib_id="cb-1"):
    return {"type": "custom", "id": bid, "title": title,
            "custom": {"lib_id": lib_id, "instruction": instruction, "format": fmt, "length": length, "sources": sources}}


def _blocks(pb, past, docs):
    return ["header", "my_focus",
            _custom("b-custom-obj", "Objection answers", "qa", "short",
                    [{"kind": "file", "path": str(pb), "sections": "2"}, {"kind": "text", "text": NOTE}],
                    instruction="Answers to the objections Emily may raise"),
            "overview",
            _custom("b-custom-sso", "SSO status", "checklist", "medium",
                    [{"kind": "transcript", "path": str(past)}, {"kind": "folder", "path": str(docs)}],
                    instruction="What to confirm about SSO", lib_id="cb-2"),
            "questions"]


def _req(root, blocks, **kw):
    r = {"preset": "blank", "route": "fake", "language": "en", "auto_sources": False, "blocks": blocks,
         "meeting": {"title": "Orbit demo", "start": "2026-11-26T10:00:00+02:00"},
         "participants": {"ours": [{"name": "Mike Carter"}], "theirs": [{"name": "Emily Parker", "company": "Orbit"}]},
         "project": {"slug": "orbit", "root": str(root)}, "today": "2026-11-25", "sources": [],
         "base": str(root / "live" / "prep-2026-11-26-1000-orbit-demo")}
    r.update(kw)
    return r


def _answer(n_obj=5):
    obj = [{"label": f"Objection {i}", "text": f"Answer {i}", "source_ids": ["s-b-custom-obj-1"]} for i in range(n_obj)]
    return {"goal": "Agree the pilot", "blocks": {
        "overview": [{"text": "Orbit wants a pilot", "source_ids": ["s-meeting"]}],
        "b-custom-obj": obj,
        "b-custom-sso": [{"label": "SSO", "text": "Confirm Okta SSO for Q1", "source_ids": ["s-b-custom-sso-1"]}],
        "custom": [{"text": "a block type key is never a custom block", "source_ids": ["s-meeting"]}]}}


def _sheet(res):
    return json.loads(Path(res["sheet"]).read_text(encoding="utf-8"))


def _by_id(sheet):
    return {b["id"]: b for b in sheet["blocks"]}


# ---------------------------------------------------------------- settings and the prompt line

def test_settings_are_cleaned_and_unknown_values_fall_back():
    cs = C.settings_of({"custom": {"format": "poem", "length": "huge", "instruction": "  x  ",
                                   "sources": [{"kind": "file", "path": "/a.md", "sections": " 2 "}, {"kind": "smtp"}, "bad"]}})
    assert cs["format"] == "bullets" and cs["length"] == "medium" and cs["instruction"] == "x"
    assert cs["sources"] == [{"kind": "file", "path": "/a.md", "sections": "2"}]
    assert set(C.FORMATS) == {"summary", "bullets", "table", "qa", "checklist", "verbatim"}


def test_the_prompt_line_names_the_id_instruction_sources_format_and_length():
    b = {"id": "b-custom-obj", "type": "custom", "title": "Objection answers",
         "custom": {"instruction": "Answers to objections", "format": "qa", "length": "short", "sources": [],
                    "source_ids": ["s-b-custom-obj-1", "s2"]}}
    line = PR.block_lines([b, {"type": "overview", "title": "Overview"}], {"overview": {"fields": "who"}}, {}, [], "en")
    assert '- b-custom-obj ("Objection answers")' in line and 'under the key "b-custom-obj"' in line
    assert "Answers to objections" in line and "s-b-custom-obj-1, s2" in line
    assert "label = the question" in line and "At most 3 facts" in line and '- overview ("Overview")' in line


# ---------------------------------------------------------------- build

def test_build_answers_each_custom_block_from_its_own_sources_in_its_format_and_length(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    seen = []
    res = B.build(_req(root, _blocks(pb, past, docs)), emit=quiet,
                  responder=lambda s, u: (seen.append(u), json.dumps(_answer()))[1])
    assert res.get("done"), res
    sheet = _sheet(res)
    assert S.validate(sheet) == []
    order = [b["id"] for b in sorted(sheet["blocks"], key=lambda b: b["order"])]
    assert order[2] == "b-custom-obj" and order[4] == "b-custom-sso"            # where the PM put them
    bl = _by_id(sheet)
    obj, sso = bl["b-custom-obj"], bl["b-custom-sso"]
    assert obj["type"] == sso["type"] == "custom" and obj["title"] == "Objection answers"
    assert [f["text"] for f in obj["facts"]] == ["Answer 0", "Answer 1", "Answer 2"]   # short = at most 3
    assert [f["text"] for f in sso["facts"]] == ["Confirm Okta SSO for Q1"]
    assert obj["custom"]["format"] == "qa" and obj["custom"]["lib_id"] == "cb-1" and sso["custom"]["format"] == "checklist"
    # the block's own sources joined the build: the file's section 2 only (Markdown as it is), the text, the past call,
    # the folder and its file
    src = {s["id"]: s for s in sheet["sources"]}
    assert src["s-b-custom-obj-1"]["sections"] == "2" and src["s-b-custom-obj-2"]["type"] == "text"
    assert src["s-b-custom-sso-1"]["type"] == "transcript" and src["s-b-custom-sso-2"]["type"] == "folder"
    kids = [s["id"] for s in sheet["sources"] if s.get("parent") == "s-b-custom-sso-2"]
    assert kids and set(kids) <= set(sso["custom"]["source_ids"])
    assert obj["custom"]["source_ids"] == ["s-b-custom-obj-1", "s-b-custom-obj-2"]
    u = seen[0]
    assert "Too expensive: point to the pilot" in u and "Margin floor" not in u and "Seat price" not in u
    assert "## 2. Objections" in u and NOTE in u and "SSO through Okta" in u and "we need SSO" in u
    assert "Answers to the objections Emily may raise" in u and 'under the key "b-custom-sso"' in u
    # a custom block's facts never land in another block, and the type key alone fills nothing
    assert not any("never a custom block" in f["text"] for b in sheet["blocks"] for f in b.get("facts") or [])


def test_a_section_pick_that_does_not_fit_marks_the_source_with_the_reason(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    bl = ["header", _custom("b-custom-x", "X", "bullets", "short", [{"kind": "file", "path": str(pb), "sections": "9"}])]
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: json.dumps({"blocks": {}}))
    sheet = _sheet(res)
    s = next(x for x in sheet["sources"] if x["id"] == "s-b-custom-x-1")
    assert s["used"] == "skipped" and "only 3 section" in s["reason"]


def test_a_call_source_is_shared_not_read_twice(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    bl = ["header", _custom("b-custom-x", "X", "summary", "long", [{"kind": "call_source", "path": str(pb), "source_id": "s1"}])]
    res = B.build(_req(root, bl, sources=[{"id": "s1", "type": "file", "path": str(pb)}]), emit=quiet,
                  responder=lambda s, u: json.dumps({"blocks": {}}))
    sheet = _sheet(res)
    assert [s["id"] for s in sheet["sources"] if s.get("path") == str(pb)] == ["s1"]
    assert _by_id(sheet)["b-custom-x"]["custom"]["source_ids"] == ["s1"]


def test_format_verbatim_is_the_built_in_verbatim_block(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    bl = ["header", _custom("b-custom-v", "Pricing script", "verbatim", "medium", [{"kind": "file", "path": str(pb), "sections": "1"}]),
          _custom("b-custom-l", "From a link", "verbatim", "medium", [{"kind": "url", "uri": "https://orbit.example/pricing"}])]
    seen = []
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: (seen.append(u), json.dumps({"blocks": {}}))[1])
    sheet = _sheet(res)
    assert S.validate(sheet) == []
    v, link = _by_id(sheet)["b-custom-v"], _by_id(sheet)["b-custom-l"]
    assert v["type"] == "verbatim" and V.is_verbatim(v)
    assert v["verbatim"]["status"] == "ok" and v["verbatim"]["text"] == V.pick(PLAYBOOK, "1")[0]
    assert v["custom"]["format"] == "verbatim" and v["custom"]["lib_id"] == "cb-1"      # editable again in the app
    assert link["verbatim"]["status"] == "failed" and link["verbatim"]["error"] == C.VERBATIM_NO_SOURCE
    assert not any("Seat price 40 USD" in u for u in seen)                               # never through the model
    # the copy is cut from a re-read sheet like any Verbatim block (PB-102)
    assert "Seat price 40 USD" not in (V.sheet_file_text(res["sheet"]) or "")


# ---------------------------------------------------------------- Refresh and edits

def test_refresh_without_a_block_list_keeps_custom_blocks_with_their_settings_and_place(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    req = _req(root, _blocks(pb, past, docs))
    assert B.build(req, emit=quiet, responder=lambda s, u: json.dumps(_answer())).get("done")
    again = {k: v for k, v in req.items() if k != "blocks"}
    seen = []
    res = B.build(again, refresh=True, emit=quiet, responder=lambda s, u: (seen.append(u), json.dumps(_answer(1)))[1])
    assert res.get("done"), res
    sheet = _sheet(res)
    assert S.validate(sheet) == []
    order = [b["id"] for b in sorted(sheet["blocks"], key=lambda b: b["order"])]
    assert order.index("b-custom-obj") == order.index("b-my_focus") + 1
    bl = _by_id(sheet)
    assert bl["b-custom-obj"]["custom"]["instruction"] == "Answers to the objections Emily may raise"
    assert [f["text"] for f in bl["b-custom-obj"]["facts"]] == ["Answer 0"]
    assert "Too expensive" in seen[0]                                         # its sources are read again


def test_refresh_with_the_wizard_list_takes_its_order_and_removals(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    req = _req(root, _blocks(pb, past, docs))
    assert B.build(req, emit=quiet, responder=lambda s, u: json.dumps(_answer())).get("done")
    bl = _blocks(pb, past, docs)
    bl = [bl[0], bl[4], bl[1], bl[3]]                                       # SSO first, objections removed
    res = B.build(dict(req, blocks=bl), refresh=True, emit=quiet, responder=lambda s, u: json.dumps(_answer()))
    sheet = _sheet(res)
    ids = [b["id"] for b in sorted(sheet["blocks"], key=lambda b: b["order"])]
    assert "b-custom-obj" not in ids and ids.index("b-custom-sso") == 1


def test_protect_edits_never_puts_one_custom_block_into_another():
    old = {"blocks": [{"id": "b-custom-a", "type": "custom", "title": "A", "edited_by_user": True, "facts": [
        {"id": "f-1", "text": "mine", "origin": "user", "status": "edited"}]}]}
    new = {"blocks": [{"id": "b-custom-b", "type": "custom", "title": "B", "facts": []}]}
    out = M.protect_edits(old, new)
    by = {b["id"]: b for b in out["blocks"]}
    assert by["b-custom-b"]["facts"] == [] and by["b-custom-a"]["facts"][0]["text"] == "mine"


def test_carry_over_keeps_a_verbatim_format_custom_block_editable():
    old = {"blocks": [{"id": "b-header", "type": "header", "order": 0},
                      {"id": "b-custom-v", "type": "verbatim", "title": "Script", "order": 1,
                       "verbatim": {"source": {"kind": "text", "text": "Hi"}, "sections": ""},
                       "custom": {"format": "verbatim", "lib_id": "cb-9", "sources": [{"kind": "text", "text": "Hi"}]}}]}
    out = V.carry_over(old, [{"id": "b-header", "type": "header"}])
    assert out[1]["id"] == "b-custom-v" and out[1]["custom"]["lib_id"] == "cb-9" and out[1]["verbatim"]["source"]["text"] == "Hi"


# ---------------------------------------------------------------- the tips window

def _rec():
    sys.path.insert(0, str(REC))
    try:
        return importlib.import_module("transcriber")
    finally:
        sys.path.remove(str(REC))


def test_the_recorder_sends_every_custom_block_in_its_format_with_verbatim_ones():
    T = _rec()
    f = lambda lab, txt, st="found": {"label": lab, "text": txt, "status": st}   # noqa: E731
    sheet = {"blocks": [
        {"id": "b-custom-c", "type": "custom", "title": "Checks", "order": 3, "custom": {"format": "checklist"},
         "facts": [f("SSO", "Confirm Okta"), f("Seats", "Five seats")]},
        {"id": "b-verbatim-1", "type": "verbatim", "title": "Script", "order": 1,
         "verbatim": {"status": "ok", "text": "Hello  there\n"}},
        {"id": "b-custom-q", "type": "custom", "title": "Q&A", "order": 2, "custom": {"format": "qa"},
         "facts": [f("Price?", "40 USD a seat"), f("Pilot?", "Two weeks")]},
        {"id": "b-custom-s", "type": "custom", "title": "Summary", "order": 4, "custom": {"format": "summary"},
         "facts": [f("", "One."), f("", "Two.")]},
        {"id": "b-custom-n", "type": "custom", "title": "Nothing", "order": 5, "custom": {"format": "bullets"},
         "facts": [f("", "No source says the budget", "not_found")]},
        {"id": "b-custom-h", "type": "custom", "title": "Hidden", "order": 6, "visible": False, "facts": [f("", "x")]}]}
    out = T.sheet_verbatim(sheet)
    assert [x["id"] for x in out] == ["b-verbatim-1", "b-custom-q", "b-custom-c", "b-custom-s", "b-custom-n"]
    by = {x["id"]: x for x in out}
    assert by["b-verbatim-1"]["text"] == "Hello  there\n"
    assert by["b-custom-c"]["text"] == "- [ ] SSO: Confirm Okta\n- [ ] Seats: Five seats" and by["b-custom-c"]["ok"]
    assert by["b-custom-q"]["text"] == "**Price?**\n40 USD a seat\n\n**Pilot?**\nTwo weeks"
    assert by["b-custom-s"]["text"] == "One.\n\nTwo."
    assert by["b-custom-n"]["ok"] is False and by["b-custom-n"]["error"] == "No source says the budget"
    assert by["b-custom-n"]["custom"] is True and "custom" not in by["b-verbatim-1"]       # the window words it apart


# ---------------------------------------------------------------- review: leaks, dedup, own sources, hidden, auto

def test_a_verbatim_format_custom_blocks_pasted_text_never_leaks_through_the_saved_request(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    bl = ["header", _custom("b-custom-v", "Do not say", "verbatim", "medium", [{"kind": "text", "text": "PASTED-LINE-5521"}]),
          _custom("b-custom-q", "Answers", "qa", "short", [{"kind": "text", "text": "AI-NOTE-7781"}])]
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: json.dumps({"blocks": {}}))
    assert res.get("done"), res
    base = str(Path(res["sheet"]))[:-len(".sheet.json")]
    req_file = base + ".sheet.request.json"
    assert "PASTED-LINE-5521" in Path(req_file).read_text(encoding="utf-8")       # the app's own copy keeps it
    clean = V.sheet_file_text(req_file)
    assert clean is not None and "PASTED-LINE-5521" not in clean and "AI-NOTE-7781" in clean
    assert "PASTED-LINE-5521" not in (V.sheet_file_text(res["sheet"]) or "")


def test_custom_facts_are_neither_deduped_nor_take_other_blocks_facts_and_cite_only_own_sources(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    bl = ["header", _custom("b-custom-obj", "Pilot", "bullets", "medium", [{"kind": "text", "text": NOTE}]), "overview"]
    same = {"label": "Pilot", "text": "Orbit wants a two week pilot", "source_ids": ["s-meeting"]}
    ans = {"blocks": {"b-custom-obj": [dict(same), {"label": "SSO", "text": "SSO asked twice", "source_ids": ["s9", "s-b-custom-obj-1"]},
                                       {"label": "Other", "text": "From elsewhere", "source_ids": ["s9"]}],
                      "overview": [dict(same)]}}
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: json.dumps(ans))
    sheet = _sheet(res)
    by = _by_id(sheet)
    assert [f["text"] for f in by["b-custom-obj"]["facts"]][0] == "Orbit wants a two week pilot"
    assert any(f["text"] == "Orbit wants a two week pilot" for f in by["b-overview"]["facts"])   # overview kept it too
    sso = next(f for f in by["b-custom-obj"]["facts"] if f["text"] == "SSO asked twice")
    other = next(f for f in by["b-custom-obj"]["facts"] if f["text"] == "From elsewhere")
    assert sso["source_ids"] == ["s-b-custom-obj-1"] and other["source_ids"] == [] and other["status"] == "needs_check"


def test_a_hidden_custom_block_reads_no_sources(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    b = _custom("b-custom-h", "Hidden", "bullets", "short", [{"kind": "text", "text": "HIDDEN-NOTE-3301"}])
    b["visible"] = False
    seen = []
    res = B.build(_req(root, ["header", b, "overview"]), emit=quiet, responder=lambda s, u: (seen.append(u), json.dumps({"blocks": {}}))[1])
    assert res.get("done") and not any("HIDDEN-NOTE-3301" in u for u in seen)
    assert not any(s["id"].startswith("s-b-custom-h") for s in _sheet(res)["sources"])


def test_a_sectioned_custom_file_does_not_replace_the_whole_file_as_an_auto_source(tmp_path, monkeypatch):
    root, pb, past, docs = _project(tmp_path)
    monkeypatch.setattr(B.SRC, "auto_sources", lambda req: [{"type": "file", "path": str(pb), "title": "playbook.md", "auto": True}])
    bl = ["header", _custom("b-custom-x", "X", "bullets", "short", [{"kind": "file", "path": str(pb), "sections": "2"}])]
    seen = []
    res = B.build(_req(root, bl, auto_sources=True), emit=quiet, responder=lambda s, u: (seen.append(u), json.dumps({"blocks": {}}))[1])
    assert res.get("done"), res
    paths = [s for s in _sheet(res)["sources"] if s.get("path") == str(pb)]
    assert len(paths) == 2 and "Margin floor is 22 percent" in seen[0]                   # the whole file for the rest


# ---------------------------------------------------------------- review round 1

def test_refresh_with_a_new_hidden_custom_block_takes_no_other_blocks_facts(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    req = _req(root, _blocks(pb, past, docs))
    assert B.build(req, emit=quiet, responder=lambda s, u: json.dumps(_answer())).get("done")
    new = _custom("b-custom-new", "New", "bullets", "short", [{"kind": "text", "text": "NEW-NOTE-7702"}], lib_id="cb-3")
    new["visible"] = False
    res = B.build(dict(req, blocks=_blocks(pb, past, docs) + [new]), refresh=True, emit=quiet,
                  responder=lambda s, u: json.dumps(_answer()))
    assert res.get("done"), res
    sheet = _sheet(res)
    assert S.validate(sheet) == []
    by = _by_id(sheet)
    assert by["b-custom-new"]["facts"] == [] and by["b-custom-new"]["visible"] is False
    assert len(by["b-custom-obj"]["facts"]) == 3


def test_a_custom_blocks_sources_are_its_own_marked_and_never_cited_by_built_in_blocks(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    bl = ["header", _custom("b-custom-obj", "Pilot", "bullets", "medium",
                            [{"kind": "text", "text": NOTE}, {"kind": "folder", "path": str(docs)},
                             {"kind": "url", "uri": "https://orbit.example/pricing?token=abc"}]), "overview"]
    ans = {"blocks": {"b-custom-obj": [{"label": "SSO", "text": "SSO asked twice", "source_ids": ["s-b-custom-obj-1"]}],
                      "overview": [{"text": "SSO came up twice", "source_ids": ["s-b-custom-obj-1"]},
                                   {"text": "Orbit wants a pilot", "source_ids": ["s-meeting", "s-b-custom-obj-1"]}]}}
    seen = []
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: (seen.append(u), json.dumps(ans))[1])
    assert res.get("done"), res
    sheet = _sheet(res)
    assert S.validate(sheet) == []
    own = {s["id"] for s in sheet["sources"] if s.get("block") == "b-custom-obj"}
    assert {"s-b-custom-obj-1", "s-b-custom-obj-2", "s-b-custom-obj-3"} <= own
    files = [s for s in sheet["sources"] if s.get("parent") == "s-b-custom-obj-2"]
    assert files and all(s.get("block") == "b-custom-obj" for s in files)     # a folder's files follow the folder
    by = _by_id(sheet)
    ov = {f["text"]: f for f in by["b-overview"]["facts"]}
    assert ov["SSO came up twice"]["source_ids"] == [] and ov["SSO came up twice"]["status"] == "needs_check"
    assert ov["Orbit wants a pilot"]["source_ids"] == ["s-meeting"]
    assert by["b-custom-obj"]["facts"][0]["source_ids"] == ["s-b-custom-obj-1"]
    assert "ONLY for the user's own blocks" in seen[0]
    # the paths and the link live only in the sources and the block's own settings, nowhere else on the sheet
    rest = {k: v for k, v in sheet.items() if k != "sources"}
    rest["blocks"] = [{k: v for k, v in b.items() if k != "custom"} for b in sheet["blocks"]]
    dump = json.dumps(rest)
    assert str(docs) not in dump and "orbit.example" not in dump and "token=abc" not in dump


def test_a_custom_block_without_own_sources_never_cites_another_blocks_own_source(tmp_path):
    root, pb, past, docs = _project(tmp_path)
    bl = ["header", _custom("b-custom-a", "A", "bullets", "short", [{"kind": "text", "text": NOTE}]),
          _custom("b-custom-b", "B", "bullets", "short", [])]
    ans = {"blocks": {"b-custom-b": [{"label": "SSO", "text": "SSO asked", "source_ids": ["s-b-custom-a-1", "s-meeting"]}]}}
    res = B.build(_req(root, bl), emit=quiet, responder=lambda s, u: json.dumps(ans))
    assert res.get("done"), res
    assert _by_id(_sheet(res))["b-custom-b"]["facts"][0]["source_ids"] == ["s-meeting"]


def test_a_custom_file_that_is_also_an_auto_source_serves_the_whole_sheet(tmp_path, monkeypatch):
    root, pb, past, docs = _project(tmp_path)
    monkeypatch.setattr(B.SRC, "auto_sources", lambda req: [{"type": "file", "path": str(pb), "title": "playbook.md", "auto": True}])
    bl = ["header", _custom("b-custom-x", "X", "bullets", "short", [{"kind": "file", "path": str(pb)}]), "overview"]
    ans = {"blocks": {"overview": [{"text": "The pilot is free", "source_ids": ["s-b-custom-x-1"]}]}}
    res = B.build(_req(root, bl, auto_sources=True), emit=quiet, responder=lambda s, u: json.dumps(ans))
    assert res.get("done"), res
    sheet = _sheet(res)
    src = [s for s in sheet["sources"] if s.get("path") == str(pb)]
    assert len(src) == 1 and not src[0].get("block")
    assert _by_id(sheet)["b-overview"]["facts"][0]["source_ids"] == ["s-b-custom-x-1"]
