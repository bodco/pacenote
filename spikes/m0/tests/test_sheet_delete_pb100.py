"""PB-100: a fact deleted on the Meeting Sheet page (a card line, a fact, an agenda point, a question) does not come
back on Refresh or with the readiness check, the same as an edit. The app removes the fact, marks its block
edited_by_user and keeps the id in deleted_facts (SheetEdit.deleteFact, CoreChecks). Fixtures are invented."""
import copy
import json
from pathlib import Path

import pytest

from livetips_m0.sheet import build as B
from livetips_m0.sheet import merge
from livetips_m0.sheet import presets as P
from livetips_m0.sheet import readiness as R
from livetips_m0.sheet import schema as S

from test_sheet_pipeline_0100 import ANSWER, _project, _req  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "sheet"
TODAY = "2026-11-12"


def quiet(**_):
    pass


def load(name):
    return json.loads((FIX / f"{name}.sheet.json").read_text(encoding="utf-8"))


def block(sheet, t):
    return next(b for b in sheet["blocks"] if b["type"] == t)


def ids(sheet):
    return [f["id"] for b in sheet["blocks"] for f in b.get("facts") or []]


def delete(sheet, fid):
    """What SheetEdit.deleteFact writes to the file."""
    for b in sheet["blocks"]:
        if any(f["id"] == fid for f in b.get("facts") or []):
            b["facts"] = [f for f in b["facts"] if f["id"] != fid]
            b["edited_by_user"] = True
    sheet["deleted_facts"] = sorted(set(sheet.get("deleted_facts") or []) | {fid})


def test_refresh_does_not_bring_back_a_deleted_agenda_point_or_question(tmp_path):
    root, a, b = _project(tmp_path)
    req = _req(root, a, b)
    assert B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER)).get("done")
    path = Path(req["base"] + ".sheet.json")
    sheet = json.loads(path.read_text(encoding="utf-8"))
    agenda = block(sheet, "agenda")["facts"][0]["id"]
    question = block(sheet, "questions")["facts"][0]["id"]
    delete(sheet, agenda)
    delete(sheet, question)
    path.write_text(json.dumps(sheet), encoding="utf-8")
    res = B.build(req, refresh=True, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))   # the model says the same again
    assert res.get("done"), res
    new = json.loads(path.read_text(encoding="utf-8"))
    texts = [f["text"] for b in new["blocks"] for f in b.get("facts") or []]
    assert agenda not in ids(new) and question not in ids(new)
    assert "Walk through the monthly report" not in texts and "Who signs off on a workshop?" not in texts
    assert set(new["deleted_facts"]) == {agenda, question} and S.validate(new) == []
    # a second Refresh still keeps them out
    assert B.build(req, refresh=True, emit=quiet, responder=lambda s, u: json.dumps(ANSWER)).get("done")
    again = json.loads(path.read_text(encoding="utf-8"))
    assert agenda not in ids(again) and question not in ids(again) and set(again["deleted_facts"]) == {agenda, question}


def test_merge_keeps_a_deleted_fact_out_even_when_its_block_is_not_marked():
    old = load("sales-intro-lumenfield")
    old["deleted_facts"] = ["f-industry"]
    block(old, "company")["facts"] = [f for f in block(old, "company")["facts"] if f["id"] != "f-industry"]
    new = load("sales-intro-lumenfield")                 # the rebuild has f-industry again, block not edited
    out = merge.protect_edits(old, new)
    assert "f-industry" not in ids(out) and out["deleted_facts"] == ["f-industry"]
    assert "f-industry" in ids(new)                      # input untouched


def test_merge_keeps_a_deleted_conflict_side_out_and_the_sheet_valid(rules):
    old = load("sales-intro-lumenfield")
    old["deleted_facts"] = ["f-size-site"]
    block(old, "company")["facts"] = [f for f in block(old, "company")["facts"] if f["id"] != "f-size-site"]
    new = load("sales-intro-lumenfield")                 # the rebuild finds the same conflict again
    out = merge.protect_edits(old, new)
    li = next(f for b in out["blocks"] for f in b["facts"] if f["id"] == "f-size-li")
    assert "f-size-site" not in ids(out) and "conflict_with" not in li and li["status"] == "needs_check"
    assert S.validate(out, P.method_names(rules)) == []


def newer_wins(sheet):
    """Team size: 80 (f-size-li) replaced the older 120 (f-size-site), shown as "was 120" under it."""
    for f in block(sheet, "company")["facts"]:
        if f["id"] == "f-size-site":
            f.update(status="stale", conflict_with=["f-size-li"], superseded_by="f-size-li")
        if f["id"] == "f-size-li":
            f["status"] = "found"
            f.pop("conflict_with", None)
    return sheet


def test_merge_keeps_the_was_value_of_a_deleted_fact_out(rules):
    # the app deletes the newer fact together with its "was" value (SheetEdit.deleteFact)
    old = newer_wins(load("sales-intro-lumenfield"))
    old["deleted_facts"] = ["f-size-li"]
    block(old, "company")["facts"] = [f for f in block(old, "company")["facts"] if f["id"] not in ("f-size-li", "f-size-site")]
    new = newer_wins(load("sales-intro-lumenfield"))     # the rebuild finds 80 replacing 120 again, block not edited
    out = merge.protect_edits(old, new)
    assert "f-size-li" not in ids(out) and "f-size-site" not in ids(out)
    assert not [f for b in out["blocks"] for f in b["facts"] if f.get("label") == "Team size"]
    assert out["deleted_facts"] == ["f-size-li"] and S.validate(out, P.method_names(rules)) == []


def test_merge_keeps_the_was_value_out_when_the_old_file_still_has_it(rules):
    # a file written before this fix: the "was" value stayed, pointing at the deleted fact
    old = newer_wins(load("sales-intro-lumenfield"))
    old["deleted_facts"] = ["f-size-li"]
    block(old, "company")["facts"] = [f for f in block(old, "company")["facts"] if f["id"] != "f-size-li"]
    block(old, "company")["edited_by_user"] = True
    out = merge.protect_edits(old, newer_wins(load("sales-intro-lumenfield")))
    assert "f-size-site" not in ids(out) and S.validate(out, P.method_names(rules)) == []


def test_merge_without_deletes_adds_no_key():
    out = merge.protect_edits(load("sales-intro-lumenfield"), load("sales-intro-lumenfield"))
    assert "deleted_facts" not in out


@pytest.fixture(scope="module")
def rules():
    return P.load_rules()


def test_a_deleted_auto_question_is_not_added_again(rules):
    s = load("sales-intro-lumenfield")
    R.apply(s, rules, TODAY)
    assert "q-readiness-budget" in ids(s)
    delete(s, "q-readiness-budget")
    snapshot = copy.deepcopy(s)
    R.apply(s, rules, TODAY)
    assert "q-readiness-budget" not in ids(s)
    assert [f["id"] for f in block(s, "questions")["facts"]] == [f["id"] for f in block(snapshot, "questions")["facts"]]
    assert S.validate(s, P.method_names(rules)) == []
