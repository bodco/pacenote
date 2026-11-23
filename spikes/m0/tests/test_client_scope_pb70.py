"""PB-70 (owner 23.11): the facts of another client never reach the preparation or the search during the call.

One project folder with two clients (docs/acme, docs/globex; the series "Acme weekly" and "Globex weekly"). One test
per leak path the G0 benchmark found: the folder window, the Tracker, the decisions, the Current State page, the
search during the call (files, the register, the lt index hits). Plus: a source the PM added by hand stays, project-wide
lines stay, and with no clear client nothing is hidden. Invented data only (Mike, Sarah, David; Acme, Globex)."""
import json
import threading

import pytest

from livetips_m0 import client_scope as CS
from livetips_m0.sheet import lookup as LK
from livetips_m0.sheet import sources as SRC
from livetips_m0.sheet import tracker as TR

DECISIONS = """# Active decisions

- 2026-11-12 Acme: SSO uses SAML, not OIDC.
- 2026-11-11 Globex: the invoice export is CSV only, no PDF.
- 2026-11-10 Weekly status reports go out on Fridays.
"""
STATE = """# Northpoint Current State

## Acme
- Pilot with 60 users.

## Globex
- Pilot budget 120 hours.
### Globex risks
- Sample invoice file is late.

## Team
- David is on leave December 22.
"""


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _event(cid, text, owner, series, call):
    return json.dumps({"v": 1, "op": "add", "id": cid, "text": text, "owner": owner, "project": "", "series": series,
                       "call": call, "created": "2026-11-11T10:00:00+00:00", "due": "", "by": "app", "source": "notes"})


def _project(tmp_path):
    root = tmp_path / "northpoint"
    _w(root / "docs" / "acme" / "acme-release-plan.md", "# Acme release plan\n\n- Go-live rehearsal: December 3.\n")
    _w(root / "docs" / "globex" / "item-13-invoice-export.md", "# Globex item 13\n\nItem 13 is the invoice export, CSV only.\n")
    _w(root / "docs" / "overview.md", "# Overview\n\nNorthpoint support desk rollout, shared notes.\n")
    _w(root / "pacenote" / "decisions.md", DECISIONS)
    _w(root / "_notion" / "Northpoint Current State.md", STATE)
    _w(root / "live" / "2026-11-10-acme-weekly" / "2026-11-10-1000-acme-weekly.md", "[10:00:01] Mike: item 13 waits for our security team.\n")
    _w(root / "live" / "2026-11-11-globex-weekly" / "2026-11-11-1100-globex-weekly.md", "[11:00:01] Sarah: item 13 needs the sample invoice file.\n")
    _w(root / "live" / "commitments.jsonl", "\n".join([
        _event("c-a", "confirm the SSO user mapping for item 13", "Mike", "|acmeweekly", "2026-11-10-1000-acme-weekly.md"),
        _event("c-g", "send a sample invoice file for item 13", "Sarah", "|globexweekly", "2026-11-11-1100-globex-weekly.md"),
        _event("c-p", "book the team offsite", "me", "", ""),
    ]) + "\n")
    return root


def _req(root, company="Acme", title="Acme weekly", **kw):
    r = {"project": {"slug": "northpoint", "root": str(root)}, "meeting": {"title": title, "start": "2026-11-17T10:00:00"},
         "participants": {"ours": [{"name": "David"}], "theirs": [{"name": "Mike", "company": company}]},
         "today": "2026-11-17", "route": "codex"}
    r.update(kw)
    return r


# ---------------------------------------------------------------- the scope itself

def test_scope_is_the_call_client_and_its_sibling_client_folders(tmp_path):
    root = _project(tmp_path)
    sc = CS.for_request(_req(root))
    assert sc["client"] == "acme" and sc["others"] == ["globex"]
    assert sc["folders"] == {"acme": ["docs/acme"], "globex": ["docs/globex"]}
    # the title alone does not make a folder a client; it picks one a company of an earlier sheet backs
    assert CS.for_request(_req(root, company="", title="Globex weekly")) is None
    _earlier_sheet(root, "Globex")
    assert CS.for_request(_req(root, company="", title="Globex weekly"))["client"] == "globex"   # by the title


def _earlier_sheet(root, company, call="2026-11-11-globex-weekly"):
    _w(root / "live" / call / ".work" / "prep-{}-1100-x.sheet.json".format(call[:10]),
       json.dumps({"schema": "pacenote.sheet/1", "participants": {"theirs": [{"name": "Sarah", "company": company}]}}))


def test_topic_folders_named_like_series_are_not_clients(tmp_path):
    """Review r0: one client, folders by topic (docs/design, docs/research) and series named after them. No company
    backs either folder, so there is no scope and the research files and lines stay."""
    root = tmp_path / "initech"
    _w(root / "docs" / "design" / "flows.md", "# Onboarding flows\n")
    _w(root / "docs" / "research" / "interviews.md", "# Research interviews\n\n- Users skip the tour.\n")
    _w(root / "live" / "2026-11-10-design-review" / "2026-11-10-1000-design-review.md", "x\n")
    _w(root / "live" / "2026-11-11-research-sync" / "2026-11-11-1000-research-sync.md", "x\n")
    for company in ("", "Initech"):
        assert CS.decide(str(root), "Design review", [company] if company else []) is None
        assert CS.for_request(_req(root, company=company, title="Design review")) is None
    _earlier_sheet(root, "Initech", "2026-11-10-design-review")
    assert CS.decide(str(root), "Design review", [], str(root / "live" / "2026-11-17-1000-design-review.md")) is None


def test_no_clear_client_hides_nothing(tmp_path):
    root = _project(tmp_path)
    assert CS.for_request(_req(root, company="", title="Northpoint planning")) is None          # no client named
    assert CS.for_request(_req(root, company="Acme", title="Globex weekly")) is None           # two clients named
    assert CS.for_request(_req(root, client_scope=False)) is None
    # no scope: the Tracker and the folder are what they were before PB-70
    r = CS.with_scope(_req(root, company="", title="Northpoint planning"))
    texts = [x["text"] for x in TR.collect(r)["waiting"]]
    assert "send a sample invoice file for item 13" in texts
    one = tmp_path / "solo"
    _w(one / "docs" / "acme" / "a.md", "Acme notes\n")
    _w(one / "live" / "2026-11-10-acme-weekly" / "2026-11-10-1000-acme-weekly.md", "x\n")
    assert CS.for_request(_req(one)) is None                                                    # one client only


def test_a_lone_folder_named_like_a_series_is_not_a_client(tmp_path):
    root = _project(tmp_path)
    _w(root / "design" / "mockups.md", "# Design mockups\n")
    _w(root / "live" / "2026-11-12-design-review" / "2026-11-12-1500-design-review.md", "x\n")
    sc = CS.for_request(_req(root))
    assert sc["others"] == ["globex"]                       # design/ is not a sibling of docs/acme
    assert CS.side(sc, str(root / "design" / "mockups.md")) is None


# ---------------------------------------------------------------- leak path 1: the folder window

def test_folder_window_leaves_out_the_other_clients_folder(tmp_path):
    root = _project(tmp_path)
    req = _req(root, sources=[{"id": "s-folder", "type": "folder", "path": str(root), "title": "northpoint"}])
    srcs, texts, _ = SRC.collect(req)
    titles = [s["title"] for s in srcs if s.get("type") == "folder_file"]
    assert "docs/acme/acme-release-plan.md" in titles and "docs/overview.md" in titles
    assert not any("globex" in t for t in titles)
    assert next(s for s in srcs if s["id"] == "s-folder")["folder"]["other_client"] == 1


def test_a_folder_of_the_other_client_added_by_hand_is_read(tmp_path):
    root = _project(tmp_path)
    req = _req(root, sources=[{"id": "s-gx", "type": "folder", "path": str(root / "docs" / "globex"), "title": "globex"}])
    srcs, texts, _ = SRC.collect(req)
    assert [s["title"] for s in srcs if s.get("type") == "folder_file"] == ["item-13-invoice-export.md"]


@pytest.mark.parametrize("auto", [False, True])      # True: a wizard candidate the PM ticked (the app keeps auto)
def test_a_file_of_the_other_client_added_by_hand_is_read_in_full(tmp_path, auto):
    root = _project(tmp_path)
    p = root / "docs" / "globex" / "item-13-invoice-export.md"
    srcs, texts, _ = SRC.collect(_req(root, sources=[{"id": "s-gx", "type": "folder_file", "path": str(p), "auto": auto}]))
    assert "CSV only" in texts["s-gx"]


# ---------------------------------------------------------------- leak path 2: the Tracker

def test_tracker_leaves_out_the_other_clients_items_and_keeps_project_wide_ones(tmp_path):
    root = _project(tmp_path)
    tr = TR.collect(CS.with_scope(_req(root)))
    assert [x["text"] for x in tr["waiting"]] == ["confirm the SSO user mapping for item 13"]
    assert [x["text"] for x in tr["on_me"]] == ["book the team offsite"]
    assert tr["other_client"] == 1
    auto = {c["type"]: c for c in SRC.auto_sources(_req(root))}
    assert "sample invoice" not in auto["tracker"]["text"] and "SSO user mapping" in auto["tracker"]["text"]


# ---------------------------------------------------------------- leak paths 3 and 4: decisions and Current State

def test_decisions_lose_the_other_clients_lines_only(tmp_path):
    root = _project(tmp_path)
    req = CS.with_scope(_req(root))
    texts = {}
    s = {"id": "d", "type": "project_state", "path": str(root / "pacenote" / "decisions.md"), "auto": True}
    SRC.check(s, req, texts, "codex", {})
    assert "SAML" in texts["d"] and "Fridays" in texts["d"] and "CSV only" not in texts["d"]
    assert s["other_client_lines"] == 1


def test_current_state_loses_the_other_clients_section(tmp_path):
    root = _project(tmp_path)
    req = CS.with_scope(_req(root))
    texts = {}
    s = {"id": "cs", "type": "project_state", "path": str(root / "_notion" / "Northpoint Current State.md"), "auto": True}
    SRC.check(s, req, texts, "codex", {})
    t = texts["cs"]
    assert "60 users" in t and "December 22" in t and "## Team" in t
    assert "120 hours" not in t and "Sample invoice" not in t and "Globex" not in t


def test_a_shared_file_the_pm_added_by_hand_is_not_cut(tmp_path):
    root = _project(tmp_path)
    texts = {}
    s = {"id": "d", "type": "file", "path": str(root / "pacenote" / "decisions.md")}
    SRC.normalize([s])
    SRC.check(dict(s, auto=False), CS.with_scope(_req(root)), texts, "codex", {})
    assert "CSV only" in texts["d"]


def test_filter_text_keeps_lines_that_name_both_clients():
    sc = {"client": "acme", "others": ["globex"], "folders": {}, "root": "/nowhere"}
    text, n = CS.filter_text(sc, "- Acme and Globex share the SSO vendor.\n- Globex only: CSV.\n- No client here.")
    assert text == "- Acme and Globex share the SSO vendor.\n- No client here." and n == 1


# ---------------------------------------------------------------- leak path 5: the search during the call

def _sheet(root, extra_sources=()):
    sc = CS.public(CS.for_request(_req(root)))
    return {"schema": "pacenote.sheet/1", "client_scope": sc, "blocks": [], "sources": list(extra_sources)}


def test_live_search_leaves_out_the_other_clients_files_and_register_items(tmp_path):
    root = _project(tmp_path)
    sheet = _sheet(root)
    idx = LK.FolderIndex([str(root)], scope=CS.from_sheet(sheet)).build()
    for index in (idx, None):
        hits = LK.snippets("Where are we with item 13?", sheet=sheet, roots=[str(root)], k=8, index=index)
        assert hits, "the own client's passages are still found"
        assert not any("globex" in (h.get("path") or "").lower() for h in hits)
        body = " ".join(h["text"] for h in hits)
        assert "sample invoice" not in body and "CSV only" not in body
        assert "security team" in body or "SSO user mapping" in body


def test_live_search_without_scope_is_as_before(tmp_path):
    root = _project(tmp_path)
    hits = LK.snippets("Where are we with item 13?", sheet=None, roots=[str(root)], k=8)
    assert any("globex" in (h.get("path") or "").lower() for h in hits)


@pytest.mark.parametrize("auto", [False, True])      # True: a wizard candidate the PM ticked (the app keeps auto)
def test_live_search_keeps_a_file_the_pm_added_to_the_sheet_by_hand(tmp_path, auto):
    root = _project(tmp_path)
    p = root / "docs" / "globex" / "item-13-invoice-export.md"
    sheet = _sheet(root, [{"id": "s1", "n": 1, "type": "folder_file", "path": str(p), "title": p.name, "auto": auto}])
    hits = LK.snippets("item 13 invoice export", sheet=sheet, roots=[str(root)], k=8)
    assert any(h.get("path") == str(p) for h in hits)


def test_engine_scope_from_the_call_and_lt_hits_of_the_other_client_dropped(tmp_path, monkeypatch):
    from livetips_m0 import context as CTX
    from livetips_m0.engine import Engine
    root = _project(tmp_path)
    e = Engine.__new__(Engine)
    e._fidx_lock = threading.Lock()
    call = root / "live" / "2026-11-17-acme-weekly" / "2026-11-17-1000-acme-weekly.md"
    e.cfg = {"project_root": str(root), "title": "Acme weekly", "call_file": str(call), "lt_cmd": "lt",
             "lookup_index": False}
    assert e._client_scope() is None                    # no sheet, no company backs a folder: nothing is hidden
    _w(root / "live" / "prep-2026-11-10-1000-acme-weekly.sheet.json",
       json.dumps({"participants": {"theirs": [{"name": "Mike", "company": "Acme"}]}}))
    e._cscope_key = None
    assert e._client_scope()["client"] == "acme"        # an earlier sheet backs docs/acme, the title picks it
    gx = str(root / "docs" / "globex" / "item-13-invoice-export.md")
    ac = str(root / "docs" / "acme" / "acme-release-plan.md")
    monkeypatch.setattr(CTX, "index_snippets", lambda *a, **k: [
        {"path": gx, "rel": "docs/globex/item-13-invoice-export.md", "preview": "Globex item 13", "score": 0.9},
        {"path": ac, "rel": "docs/acme/acme-release-plan.md", "preview": "Acme release plan", "score": 0.8}])
    srcs = [x["src"] for x in e._project_snippets("item 13 release")]
    assert "docs/acme/acme-release-plan.md" in srcs
    assert not any("globex" in s for s in srcs)


@pytest.mark.parametrize("kw", [{"company": "Acme", "title": "Globex weekly"}, {"client_scope": False}])
def test_live_search_follows_the_preparations_hide_nothing(tmp_path, kw):
    """Review r0: the preparation decided to hide nothing (company and title name different clients, or the scope is
    switched off). The sheet says so and the engine does not decide again from the title."""
    from livetips_m0.engine import Engine
    root = _project(tmp_path)
    _earlier_sheet(root, "Globex")
    req = CS.with_scope(_req(root, **kw))
    sheet = {"schema": "pacenote.sheet/1", "client_scope": CS.for_sheet(req), "blocks": [], "sources": []}
    assert sheet["client_scope"]["client"] is None
    assert sheet["client_scope"]["why"] == ("off" if "client_scope" in kw else "not sure")
    e = Engine.__new__(Engine)
    e.cfg = {"project_root": str(root), "title": "Globex weekly", "call_file": ""}
    assert CS.decide(str(root), "Globex weekly")["client"] == "globex"      # what the title alone would pick
    assert e._client_scope(sheet) is None
    hits = LK.snippets("Where are we with item 13?", sheet=sheet, roots=[str(root)], k=8)
    assert any("globex" in (h.get("path") or "").lower() for h in hits)
