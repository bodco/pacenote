"""PB-73 (owner 25.11): the after-call notes register and the call's Tracker keep to the call's client (PB-70 scope).

The notes path (callnotes._register_tasks, as the app runs it after a call) takes the client from the scope the call's
Meeting Sheet keeps, as engine.write_register does during the call. The G0 bench reads the call's Tracker within that
scope, as the preparation does, and loses none of the call's own or project-wide items.
Invented data only (Mike, Sarah, David; Acme, Globex)."""
import importlib.util
import json
from pathlib import Path

import pytest

from livetips_m0 import client_scope as CS
from livetips_m0 import commitments as C
from livetips_m0.callnotes import _register_tasks

G0 = Path(__file__).resolve().parents[1] / "bench" / "g0"
CALL = "2026-11-17-1000-weekly-sync.md"          # no client word in the name: only the sheet can tell the client
NOTES = """## Meeting notes
### TL;DR
SSO pilot.
### Action items
- [10:05:00] **Mike**: confirm the SSO user mapping for item 13 (due: Friday)
- [10:06:00] **David**: send the pilot user list
### Topics
"""


def _call(tmp_path, scope):
    live = tmp_path / "live"
    live.mkdir()
    md = live / CALL
    md.write_text("[10:00:01] Mike: item 13 waits for our security team.\n", encoding="utf-8")
    if scope is not None:
        sheet = {"schema": "pacenote.sheet/1", "client_scope": scope}
        (live / CALL.replace(".md", ".sheet.json")).write_text(json.dumps(sheet), encoding="utf-8")
    return live, md


def test_notes_take_the_client_of_the_call_sheet(tmp_path):
    live, md = _call(tmp_path, {"client": "acme", "others": ["globex"], "folders": {}, "root": str(tmp_path),
                                "why": "the other side's company"})
    _register_tasks(md, NOTES, ["David"])
    adds = [e for e in C.read_events(live) if e.get("op") == "add"]
    assert len(adds) == 2 and all(e.get("client") == "acme" for e in adds)
    assert all(i.get("client") == "acme" for i in C.load(live))


@pytest.mark.parametrize("scope", [None, {"client": None, "why": "not sure"}, {"client": None, "why": "off"}])
def test_no_sheet_or_no_scope_keeps_no_client(tmp_path, scope):
    live, md = _call(tmp_path, scope)
    _register_tasks(md, NOTES, ["David"])
    adds = [e for e in C.read_events(live) if e.get("op") == "add"]
    assert len(adds) == 2 and all(not e.get("client") for e in adds)


def test_unreadable_sheet_still_registers(tmp_path):
    live, md = _call(tmp_path, None)
    (live / CALL.replace(".md", ".sheet.json")).write_text("{not json", encoding="utf-8")
    _register_tasks(md, NOTES, ["David"])
    assert len(C.load(live)) == 2


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("g0_run_pb73", G0 / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("own,title,other", [("acme", "Acme weekly status", "globex"),
                                             ("globex", "Globex weekly", "acme")])
def test_bench_tracker_keeps_to_the_call_client_without_loss(bench, own, title, other):
    """The call's Tracker in the bench is the preparation's: the other client's items are out, every item of the call's
    client and every project-wide item stays (scoped + hidden = the whole register)."""
    scope = CS.decide(str(bench.PROJECT), title, [own.capitalize()])
    assert scope and scope["client"] == own
    gold = bench.load_gold()
    v = bench.VariantA()
    full = v.tracker(bench.PROJECT, "2026-11-17", None)
    scoped = v.tracker(bench.PROJECT, "2026-11-17", {"client_scope": CS.public(scope)})
    rows = lambda tr: tr["on_me"] + tr["waiting"]  # noqa: E731
    side = lambda x: bench.client_of_series(x.get("series", ""), gold)  # noqa: E731
    assert not [x for x in rows(scoped) if side(x) == other]
    assert [x for x in rows(full) if side(x) == other and "item 13" in x["text"].lower()]   # it was there before
    assert [x for x in rows(scoped) if side(x) == own and "item 13" in x["text"].lower()]   # the call's own item 13
    keep = [x["text"] for x in rows(full) if side(x) != other]
    assert sorted(x["text"] for x in rows(scoped)) == sorted(keep)
    assert len(rows(scoped)) + scoped["other_client"] == len(rows(full))
