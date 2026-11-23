"""PB-51 GraphRAG G1: one task across calls (rules/commitments.yaml > identity). Runs on the G0 fixture
(bench/g0/fixtures/northpoint, PB-50): two clients with an "item 13", a task repeated in other words, "we will try".
Invented data only (Mike, Emily, Sarah, David; Acme, Globex, Northpoint)."""
import json
import shutil
from pathlib import Path

import pytest

from livetips_m0 import commitments as C
from livetips_m0 import identity as ID

G0 = Path(__file__).resolve().parents[1] / "bench" / "g0" / "fixtures"
LIVE = G0 / "northpoint" / "live"
TARGETS = [("acme-weekly-status-2026-11-17", "2026-11-17-1000-acme-weekly-status.md", "2026-11-17T10:00:00+00:00"),
           ("globex-weekly-2026-11-18", "2026-11-18-1100-globex-weekly.md", "2026-11-18T11:00:00+00:00"),
           ("acme-technical-sync-2026-11-19", "2026-11-19-1400-acme-technical-sync.md", "2026-11-19T14:00:00+00:00")]
TRY_IDS = "c-899fd9b3ce"          # "try to confirm the pilot user list" (Mike, 2026-11-03)


@pytest.fixture
def reg(tmp_path):
    shutil.copy(LIVE / C.FILE, tmp_path / C.FILE)
    return tmp_path


@pytest.fixture
def clock(monkeypatch):
    def at(day):
        monkeypatch.setattr(C, "now_iso", lambda: day)
    return at


def notes_of(target, call):
    return (G0 / "targets" / target / call.replace(".md", ".notes.md")).read_text(encoding="utf-8")


def record(reg, clock, n=3):
    for target, call, day in TARGETS[:n]:
        clock(day)
        C.record_notes(reg, call=call, notes=notes_of(target, call), aliases=["David"], people={})


def open_with(items, *phrases):
    return [i for i in items if i["status"] == "open" and all(p in i["text"].lower() for p in phrases)]


# ---------------------------------------------------------------- the keys

def test_ref_of_reads_an_explicit_reference_in_both_languages():
    assert ID.ref_of("confirm the SSO user mapping for item 13") == "13"
    assert ID.ref_of("Що зараз блокує пункт 13?") == "13"
    assert ID.ref_of("close task #13 today") == "13"
    assert ID.ref_of("see #13") == "13"
    assert ID.ref_of("send 13 items to Mike") == ""            # a count, not a reference
    assert ID.ref_of("item 13 and item 14") == ""              # two items: not one key
    assert ID.ref_of("send the revised rollout checklist") == ""


def test_tentative_wording_in_both_languages():
    assert ID.is_tentative("try to confirm the pilot user list")
    assert ID.is_tentative("We will try to send it by Friday")
    assert ID.is_tentative("спробуємо надіслати список")
    assert not ID.is_tentative("confirm the pilot user list")
    assert not ID.is_tentative("send the entry list")


def test_person_id_follows_voice_library_aliases_and_merges(tmp_path):
    lib = tmp_path / "voices.json"       # Settings > Voice library > Merge into wrote "m. t." and "mike t." -> Mike Turner
    lib.write_text(json.dumps({"people": {"Mike Turner": {}}, "aliases": {"m. t.": "Mike Turner", "mike t.": "m. t.",
                                                                          "dave": "David"}}), encoding="utf-8")
    people = ID.voice_aliases(lib)
    assert ID.person_id("Mike T.", people) == ID.person_id("M. T.", people) == ID.person_id("Mike Turner", people) == "mike turner"
    assert ID.person_id("Dave", people, me=["David"]) == "me"        # an alias of the PM is the PM
    assert ID.person_id("Emily", people) == "emily"
    assert ID.same_person("Mike T.", "Mike Turner", people)
    assert not ID.same_person("Mike T.", "Emily", people)
    assert ID.voice_aliases(tmp_path / "missing.json") == {}


def test_scope_is_client_when_known_else_series():
    a = {"project": "northpoint", "series": "northpoint|acmeweeklystatus"}
    assert ID.scope_of(a) != ID.scope_of(dict(a, series="northpoint|globexweekly"))
    assert ID.scope_of(dict(a, client="Acme")) == ID.scope_of({"project": "northpoint", "series": "x|acmetechnicalsync",
                                                               "client": "acme"})


# ---------------------------------------------------------------- the G0 cases

def test_item_13_of_acme_and_of_globex_stay_two_items(reg, clock):
    record(reg, clock)
    items = C.load(reg)
    acme = open_with(items, "sso user mapping", "item 13")
    globex = open_with(items, "sample invoice file", "item 13")
    assert len(acme) == 1 and len(globex) == 1
    assert acme[0]["id"] != globex[0]["id"]
    assert {C.call_key(h["call"]) for h in acme[0]["history"]} == {"2026-11-10-1000-acme-weekly-status",
                                                                   "2026-11-17-1000-acme-weekly-status"}
    assert {C.call_key(h["call"]) for h in globex[0]["history"]} == {"2026-11-04-1100-globex-weekly",
                                                                     "2026-11-18-1100-globex-weekly"}


def test_the_same_person_and_item_13_at_the_other_client_is_not_linked(reg, clock):
    clock("2026-11-18T11:00:00+00:00")
    notes = "## Meeting notes\n### Action items\n- [11:00:40] **Mike**: confirm the SSO user mapping for item 13\n"
    ev = C.record_notes(reg, call="2026-11-18-1100-globex-weekly.md", notes=notes, aliases=["David"], people={})
    assert [e for e in ev if e["op"] == "add"] and not [e for e in ev if e["op"] == "same"]
    assert len(open_with(C.load(reg), "sso user mapping", "item 13")) == 2


def test_item_13_reference_links_only_the_same_persons_task(reg, clock):
    record(reg, clock)
    items = C.load(reg)
    assert len(open_with(items, "idp metadata")) == 1       # Emily's item 13 (technical) is not Mike's item 13 (weekly)
    assert open_with(items, "idp metadata")[0]["owner"] == "Emily"


def test_repeated_task_is_a_suggestion_then_one_item_after_the_pm_confirms(reg, clock):
    record(reg, clock, 1)
    items = C.load(reg)
    assert len(open_with(items, "rollout checklist")) == 3    # no ID, no reference: nothing joins by itself
    sugg = C.suggestions(reg, {})
    final = next(s for s in sugg if "final rollout checklist" in s["text"])
    assert "rollout checklist" in final["to_text"] and final["question"] == "Looks like the task from 03.11, is it the same?"
    assert not any("pilot timeline" in s["to_text"] for s in sugg)      # "send ... to Mike" alone is not the same task
    for s in sugg:
        if "rollout checklist" in s["text"]:
            C.confirm(reg, s["id"], s["to"])
    one = open_with(C.load(reg), "rollout checklist")
    assert len(one) == 1
    assert one[0]["id"] == "c-cd6930cc17" and one[0]["text"] == "send Mike the updated rollout checklist"   # oldest kept
    assert len(one[0]["history"]) == 3 and len(one[0]["ids"]) == 3
    assert not [s for s in C.suggestions(reg, {}) if "rollout checklist" in s["text"]]


def test_we_will_try_stays_a_proposal(reg, clock):
    record(reg, clock)
    by = {x: i for i in C.load(reg) for x in i["ids"]}
    assert by[TRY_IDS]["status"] == "proposal" and by[TRY_IDS]["modality"] == "tentative"
    idp = next(i for i in C.load(reg) if "idp metadata" in i["text"].lower())
    assert idp["status"] == "open"       # "try to send" on a later call does not weaken the agreed item
    clock("2026-11-20T10:00:00+00:00")
    notes = "## Meeting notes\n### Action items\n- [10:00:10] **Sarah**: we will try to send the tax form\n"
    C.record_notes(reg, call="2026-11-20-1000-globex-weekly.md", notes=notes, aliases=["David"], people={})
    tax = next(i for i in C.load(reg) if "tax form" in i["text"])
    assert tax["status"] == "proposal"
    C.mark(reg, tax["id"], "agree")
    assert next(i for i in C.load(reg) if "tax form" in i["text"])["status"] == "open"


def test_voice_alias_joins_an_item_13_said_under_another_spelling(reg, clock):
    clock("2026-11-17T10:00:00+00:00")
    notes = "## Meeting notes\n### Action items\n- [10:01:44] **M. Turner**: confirm the SSO user mapping for item 13\n"
    ev = C.record_notes(reg, call="2026-11-17-1000-acme-weekly-status.md", notes=notes, aliases=["David"],
                        people={"m. turner": "Mike"})
    assert [e for e in ev if e["op"] == "same"]
    assert len(open_with(C.load(reg), "sso user mapping")) == 1


def test_a_mention_ticked_done_in_the_window_closes_the_older_item(reg, clock):
    C.append(reg, [{"op": "add", "id": "c-plan13", "text": "send the SSO test plan for item 13", "owner": "me", "project": "",
                    "series": "|acmeweeklystatus", "call": "2026-11-10-1000-acme-weekly-status.md",
                    "created": "2026-11-10T10:00:00+00:00", "due": "", "by": "app"}])
    clock("2026-11-17T10:30:00+00:00")
    C.ensure_migrated(reg, {})
    ev = C.record_call(reg, call="2026-11-17-1000-acme-weekly-status.app.md", project="", title="Acme weekly status",
                       actions=[{"id": "x", "what": "Send the SSO test plan to Mike for item 13"}], theirs=[],
                       ticked={"x": True}, people={})
    assert [e for e in ev if e["op"] == "same"]
    plan = next(i for i in C.load(reg) if "c-plan13" in i["ids"])
    assert plan["id"] == "c-plan13" and plan["status"] == "done" and len(plan["ids"]) == 2
    assert not open_with(C.load(reg), "sso test plan")


def test_a_done_item_is_never_reopened_by_a_link(reg, clock):
    C.mark(reg, "c-deb94c8d56", "done")
    record(reg, clock, 1)
    mapping = open_with(C.load(reg), "sso user mapping", "item 13")
    assert len(mapping) == 1 and mapping[0]["id"] != "c-deb94c8d56"     # a new open task, not the closed one reopened


# ---------------------------------------------------------------- the PM wins

def test_pm_reject_and_split_win_over_the_keys(reg, clock):
    record(reg, clock, 1)
    sugg = [s for s in C.suggestions(reg, {}) if "final rollout checklist" in s["text"]][0]
    C.reject(reg, sugg["id"], sugg["to"])
    assert not [s for s in C.suggestions(reg, {}) if s["id"] == sugg["id"] and s["to"] == sugg["to"]]
    mapping = open_with(C.load(reg), "sso user mapping")[0]
    newer = [x for x in mapping["ids"] if x != mapping["id"]][0]
    C.split(reg, newer)
    assert len(open_with(C.load(reg), "sso user mapping")) == 2
    events = C.read_events(reg)
    assert C.link_events(events, [newer], {}) == []          # the split pair is never linked again


def test_pm_edit_on_the_older_item_survives_a_link(reg, clock):
    C.append(reg, [{"op": "edit", "id": "c-deb94c8d56", "at": "2026-11-11T09:00:00+00:00",
                    "text": "Mike confirms the SSO group mapping for item 13", "due": "Nov 20"}])
    record(reg, clock, 1)
    it = open_with(C.load(reg), "sso group mapping")[0]
    assert it["text"] == "Mike confirms the SSO group mapping for item 13" and it["due"] == "Nov 20"
    assert len(it["history"]) == 2


def test_the_same_tracker_id_is_one_item(tmp_path):
    ev = [{"op": "add", "id": "c-a", "text": "send the security memo", "owner": "me", "series": "p|acmeweekly",
           "call": "2026-11-03-1000-acme-weekly.md", "created": "2026-11-03T10:00:00"},
          {"op": "add", "id": "c-b", "text": "draft the memo for security", "owner": "me", "series": "p|acmetechnical",
           "call": "2026-11-05-1400-acme-technical.md", "created": "2026-11-05T14:00:00"},
          {"op": "link", "id": "c-a", "notion_page": "1111-2222"}, {"op": "link", "id": "c-b", "notion_page": "11112222"}]
    C.append(tmp_path, ev)
    res = C.migrate(tmp_path, {})
    assert res["links"] == 1
    items = C.load(tmp_path)
    assert [i["id"] for i in items] == ["c-a"] and items[0]["ids"] == ["c-a", "c-b"]
    C.mark(tmp_path, "c-b", "done")                          # an event on the old id applies to the item
    assert C.load(tmp_path)[0]["status"] == "done"


# ---------------------------------------------------------------- migration and rollback

def historical(tmp_path):
    """The fixture register plus an item 13 said again on a later weekly (before G1, so it is its own item)."""
    shutil.copy(LIVE / C.FILE, tmp_path / C.FILE)
    C.append(tmp_path, [{"op": "add", "id": "c-old13", "text": "confirm the SSO user mapping for item 13", "owner": "Mike",
                         "project": "", "series": "|acmeweeklystatus", "call": "2026-11-14-1000-acme-weekly-status.md",
                         "created": "2026-11-14T10:00:00+00:00", "due": "", "by": "app", "source": "notes"},
                        {"op": "done", "id": "c-old13", "at": "2026-11-15T09:00:00+00:00", "source": "manual"},
                        {"op": "reopen", "id": "c-old13", "at": "2026-11-15T09:05:00+00:00"}])
    return tmp_path


def test_migration_keeps_every_item_and_history_and_rolls_back(tmp_path, clock):
    reg = historical(tmp_path)
    lines_before = (reg / C.FILE).read_text(encoding="utf-8")
    before = C.load(reg)
    clock("2026-11-16T08:00:00+00:00")
    res = C.migrate(reg, {})
    assert res["tentative"] == 1 and res["links"] == 1
    assert (reg / C.FILE).read_text(encoding="utf-8").startswith(lines_before)      # append only
    after = C.load(reg)
    ids = {x for i in after for x in i["ids"]}
    assert {i["id"] for i in before} <= ids                                         # every old id still resolves
    mapping = next(i for i in after if "c-old13" in i["ids"])
    assert mapping["id"] == "c-deb94c8d56" and len(mapping["history"]) == 2
    assert next(i for i in after if TRY_IDS in i["ids"])["status"] == "proposal"
    assert C.migrate(reg, {})["events"] == 0                                        # once
    C.rollback(reg)
    assert C.load(reg) == before                                                    # the fold is exactly as before
    C.ensure_migrated(reg, {})
    assert C.load(reg) == before                                                    # a rolled back register stays so


def test_events_on_an_old_id_still_apply_after_migration(tmp_path, clock):
    reg = historical(tmp_path)
    clock("2026-11-16T08:00:00+00:00")
    C.migrate(reg, {})
    C.mark(reg, "c-old13", "done")
    assert next(i for i in C.load(reg) if "c-old13" in i["ids"])["status"] == "done"


def test_cli_suggest_and_same(reg, clock, capsys):
    record(reg, clock, 1)
    assert C.main(["suggest", str(reg)]) == 0
    sugg = json.loads(capsys.readouterr().out)
    s = next(x for x in sugg if "final rollout checklist" in x["text"])
    assert C.main(["same", str(reg), s["id"], "--to", s["to"]]) == 0
    assert json.loads(capsys.readouterr().out)["by"] == "pm"
