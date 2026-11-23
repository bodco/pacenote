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


def confirm_refs(reg):
    """The PM says yes to every "is it the same?" of an explicit reference (the fixture knows no client, so a
    reference is only a suggestion)."""
    for s in C.suggestions(reg, {}):
        if ID.ref_of(s["text"]):
            C.confirm(reg, s["id"], s["to"])


def open_with(items, *phrases):
    return [i for i in items if i["status"] == "open" and all(p in i["text"].lower() for p in phrases)]


# ---------------------------------------------------------------- the keys

def test_ref_of_reads_an_explicit_reference_in_both_languages():
    assert ID.ref_of("confirm the SSO user mapping for item 13") == "13"
    assert ID.ref_of("Що зараз блокує пункт 13?") == "13"
    assert ID.ref_of("close task #13 today") == "13"
    assert ID.ref_of("see #13") == ""                         # a bare number sign is not a reference (#1 priority)
    assert ID.ref_of("send 13 items to Mike") == ""            # a count, not a reference
    assert ID.ref_of("item 13 and item 14") == ""              # two items: not one key
    assert ID.ref_of("send the revised rollout checklist") == ""


def test_tentative_wording_in_both_languages():
    assert ID.is_tentative("try to confirm the pilot user list")
    assert ID.is_tentative("We will try to send it by Friday")
    assert ID.is_tentative("спробуємо надіслати список")
    assert not ID.is_tentative("confirm the pilot user list")
    assert not ID.is_tentative("send the entry list")


def test_tentative_wording_does_not_catch_a_real_commitment():
    assert not ID.is_tentative("Розібрати невдалу спробу входу Mike")      # the noun "спроба", not "we will try"
    assert not ID.is_tentative("review the failed login attempt")
    assert not ID.is_tentative("send the maybe-list to Emily")
    assert not ID.is_tentative("надіслати звіт якнайшвидше, наскільки можливо")
    assert ID.is_tentative("можливо надішлемо звіт у п'ятницю")
    assert ID.is_tentative("спробую підтвердити список")
    assert ID.is_tentative("maybe send the report on Friday")
    assert ID.is_tentative("Мабуть надішлю звіт у п'ятницю")
    assert not ID.is_tentative("надіслати звіт, мабуть, у п'ятницю")


def test_tentative_only_when_the_promise_opens_the_item():
    # someone else trying inside a firm task does not make it a "we will try" (PB-51 review round 3)
    assert not ID.is_tentative("Fix the crash when users try to export")
    assert not ID.is_tentative("Check why users try to log in twice")
    assert not ID.is_tentative("Block users who attempt to sign in")
    assert not ID.is_tentative("Send Mike the steps he will try")
    assert not ID.is_tentative("Попросити Олега спробувати новий білд")
    assert not ID.is_tentative("Try the new build on staging")
    assert ID.is_tentative("we'll try to fix the export")
    assert ID.is_tentative("We’ll try to fix the export")
    assert ID.is_tentative("I will try to confirm the list")
    assert ID.is_tentative("will try to send the deck")
    assert ID.is_tentative("Спробуємо надіслати список")
    assert ID.is_tentative("Ми спробуємо надіслати список")
    assert ID.is_tentative("я постараюсь надіслати список")
    assert ID.is_tentative("- try to send the IdP metadata file for item 13")


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
    lib.write_text(json.dumps({"aliases": {"STRASSE": "Jake Strauss"}}), encoding="utf-8")   # case-folded as the app
    assert ID.person_id("Straße", ID.voice_aliases(lib)) == ID.person_id("Jake Strauss") == "jake strauss"


def test_scope_is_client_when_known_else_series():
    a = {"project": "northpoint", "series": "northpoint|acmeweeklystatus"}
    assert ID.scope_of(a) != ID.scope_of(dict(a, series="northpoint|globexweekly"))
    assert ID.scope_of(dict(a, client="Acme")) == ID.scope_of({"project": "northpoint", "series": "x|acmetechnicalsync",
                                                               "client": "acme"})


# ---------------------------------------------------------------- the G0 cases

def test_item_13_of_acme_and_of_globex_stay_two_items(reg, clock):
    record(reg, clock)
    assert not [e for e in C.read_events(reg) if e["op"] == "same" and e.get("by") != "pm"]   # no client: no auto link
    confirm_refs(reg)
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


def test_voice_alias_joins_an_item_13_said_under_another_spelling(tmp_path, clock):
    C.append(tmp_path, [{"op": "add", "id": "c-m13", "text": "confirm the SSO user mapping for item 13", "owner": "Mike",
                         "project": "", "series": "|acmeweeklystatus", "call": "2026-11-10-1000-acme-weekly-status.md",
                         "created": "2026-11-10T10:00:00+00:00", "due": "", "by": "app", "client": "Acme"}])
    clock("2026-11-17T10:00:00+00:00")
    notes = "## Meeting notes\n### Action items\n- [10:01:44] **M. Turner**: confirm the SSO user mapping for item 13\n"
    ev = C.record_notes(tmp_path, call="2026-11-17-1000-acme-weekly-status.md", notes=notes, aliases=["David"],
                        client="Acme", people={"m. turner": "Mike"})
    assert [e for e in ev if e["op"] == "same"]
    assert len(open_with(C.load(tmp_path), "sso user mapping")) == 1


def test_a_mention_ticked_done_in_the_window_closes_the_older_item(reg, clock):
    C.append(reg, [{"op": "add", "id": "c-plan13", "text": "send the SSO test plan for item 13", "owner": "me", "project": "",
                    "series": "|acmeweeklystatus", "call": "2026-11-10-1000-acme-weekly-status.md",
                    "created": "2026-11-10T10:00:00+00:00", "due": "", "by": "app", "client": "Acme"}])
    clock("2026-11-17T10:30:00+00:00")
    C.ensure_migrated(reg, {})
    ev = C.record_call(reg, call="2026-11-17-1000-acme-weekly-status.app.md", project="", title="Acme weekly status",
                       actions=[{"id": "x", "what": "Send the SSO test plan to Mike for item 13"}], theirs=[],
                       ticked={"x": True}, client="Acme", people={})
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
    confirm_refs(reg)
    mapping = open_with(C.load(reg), "sso user mapping")[0]
    newer = [x for x in mapping["ids"] if x != mapping["id"]][0]
    C.split(reg, newer)
    assert len(open_with(C.load(reg), "sso user mapping")) == 2
    events = C.read_events(reg)
    assert C.link_events(events, [newer], {}) == []          # the split pair is never linked again


def _pair(old_text="fix item 13", new_text="fix item 13", old_mod="", new_due=""):
    a = {"op": "add", "id": "c-old", "text": old_text, "owner": "me", "project": "p", "client": "acme",
         "call": "2026-11-03-1000-acme-weekly.md", "created": "2026-11-03T10:00:00"}
    if old_mod:
        a["modality"] = old_mod
    b = {"op": "add", "id": "c-new", "text": new_text, "owner": "me", "project": "p", "client": "acme", "due": new_due,
         "call": "2026-11-10-1000-acme-weekly.md", "created": "2026-11-10T10:00:00"}
    return [a, b]


def _by_id(events):
    return {i["id"]: i for i in C.fold(events)}


def test_split_after_a_done_mention_gives_the_older_item_back_open():
    ev = _pair() + [{"op": "done", "id": "c-new", "at": "2026-11-10T10:30:00", "source": "manual"},
                    {"op": "same", "id": "c-new", "to": "c-old", "by": "app"}]
    assert _by_id(ev)["c-old"]["status"] == "done"
    got = _by_id(ev + [{"op": "split", "id": "c-new", "to": "c-old", "by": "pm"}])
    assert got["c-old"]["status"] == "open" and got["c-old"]["done_at"] == ""
    assert got["c-new"]["status"] == "done"


def test_split_after_an_agreed_mention_gives_the_proposal_back():
    ev = _pair("try to fix item 13", "fix item 13 by friday", "tentative", "friday") + [
        {"op": "same", "id": "c-new", "to": "c-old", "by": "app"}]
    assert _by_id(ev)["c-old"]["status"] == "open"
    got = _by_id(ev + [{"op": "split", "id": "c-new", "to": "c-old", "by": "pm"}])
    assert (got["c-old"]["status"], got["c-old"]["modality"], got["c-old"]["due"]) == ("proposal", "tentative", "")
    assert (got["c-new"]["status"], got["c-new"]["due"]) == ("open", "friday")


def test_split_after_the_due_date_was_copied_clears_it():
    ev = _pair(new_due="Nov 30") + [{"op": "same", "id": "c-new", "to": "c-old", "by": "app"}]
    assert _by_id(ev)["c-old"]["due"] == "Nov 30"
    got = _by_id(ev + [{"op": "split", "id": "c-new", "to": "c-old", "by": "pm"}])
    assert got["c-old"]["due"] == "" and got["c-new"]["due"] == "Nov 30"
    assert [h["id"] for h in got["c-old"]["history"]] == ["c-old"] and got["c-old"]["ids"] == ["c-old"]


def test_events_while_joined_stay_with_the_item_they_named_after_a_split():
    ev = _pair() + [{"op": "same", "id": "c-new", "to": "c-old", "by": "app"},
                    {"op": "edit", "id": "c-old", "at": "2026-11-12T09:00:00", "due": "Dec 1"},
                    {"op": "done", "id": "c-new", "at": "2026-11-12T10:00:00", "source": "manual"},
                    {"op": "split", "id": "c-new", "to": "c-old", "by": "pm"}]
    got = _by_id(ev)
    assert (got["c-old"]["status"], got["c-old"]["due"]) == ("open", "Dec 1")
    assert got["c-new"]["status"] == "done"


def test_moving_a_joined_done_mention_gives_the_older_item_back_open():
    ev = _pair(new_due="Nov 30") + [{"op": "done", "id": "c-new", "at": "2026-11-10T10:30:00", "source": "manual"},
                                    {"op": "same", "id": "c-new", "to": "c-old", "by": "app"},
                                    {"op": "move", "id": "c-new", "to": "other"}]
    got = _by_id(ev)
    assert list(got) == ["c-old"]
    assert (got["c-old"]["status"], got["c-old"]["done_at"], got["c-old"]["due"]) == ("open", "", "")
    assert got["c-old"]["ids"] == ["c-old"]


def test_a_pair_split_and_confirmed_again_is_one_item():
    ev = _pair() + [{"op": "same", "id": "c-new", "to": "c-old", "by": "app"},
                    {"op": "split", "id": "c-new", "to": "c-old", "by": "pm"},
                    {"op": "same", "id": "c-new", "to": "c-old", "by": "pm"}]
    got = C.fold(ev)
    assert len(got) == 1 and got[0]["ids"] == ["c-old", "c-new"]


def test_pm_edit_on_the_older_item_survives_a_link(reg, clock):
    C.append(reg, [{"op": "edit", "id": "c-deb94c8d56", "at": "2026-11-11T09:00:00+00:00",
                    "text": "Mike confirms the SSO group mapping for item 13", "due": "Nov 20"}])
    record(reg, clock, 1)
    confirm_refs(reg)
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


def _one_page(a, b, link_a, link_b):
    """Two items of two calls on one tracker entry (the PM's link events), by the migration and by link_events."""
    return [add("c-a", "send the security memo", "me", "2026-11-03-1000-weekly-sync.md", "2026-11-03T10:00:00", **a),
            add("c-b", "draft the memo for security", "me", "2026-11-05-1000-weekly-sync.md", "2026-11-05T10:00:00", **b),
            dict(link_a, id="c-a"), dict(link_b, id="c-b")]


PAGE = {"op": "link", "notion_page": "1111-2222"}


@pytest.mark.parametrize("a,b", [(dict(client="Acme"), dict(client="Globex")),            # same page, two clients
                                 (dict(project="p", client="Acme"), dict(project="q", client="Acme")),   # two projects
                                 (dict(project="p"), dict(project="q"))])                 # two projects, no client
def test_one_tracker_id_never_joins_across_clients_or_projects(tmp_path, clock, a, b):
    ev = _one_page(a, b, PAGE, PAGE)
    assert C.link_events(ev, ["c-b"], {}) == []
    C.append(tmp_path, ev)
    clock("2026-11-16T08:00:00")
    assert C.migrate(tmp_path, {})["links"] == 0
    assert sorted(i["id"] for i in C.load(tmp_path)) == ["c-a", "c-b"]


def test_one_tracker_id_joins_within_a_client_or_with_an_unknown_one(tmp_path, clock):
    ev = _one_page(dict(client="Acme"), dict(client="acme "), PAGE, PAGE)
    assert [e["to"] for e in C.link_events(ev, ["c-b"], {})] == ["c-a"]
    ev = _one_page(dict(client="Acme"), {}, PAGE, PAGE)
    assert [(e["to"], e["source"]) for e in C.link_events(ev, ["c-b"], {})] == [("c-a", "tracker")]


def test_migration_joins_one_page_per_client_not_across(tmp_path, clock):
    """An unknown-client item on the page joins Acme's; Globex's item on the same page stays its own."""
    ev = _one_page({}, dict(client="Acme"), PAGE, PAGE)
    ev.append(add("c-g", "memo for security review", "me", "2026-11-07-1000-weekly-sync.md", "2026-11-07T10:00:00",
                  client="Globex"))
    ev.append(dict(PAGE, id="c-g"))
    C.append(tmp_path, ev)
    clock("2026-11-16T08:00:00")
    assert C.migrate(tmp_path, {})["links"] == 1
    assert sorted(tuple(i["ids"]) for i in C.load(tmp_path)) == [("c-a", "c-b"), ("c-g",)]
    ev = C.read_events(tmp_path)                                 # the app path: Acme's mention bars Globex too
    assert C.link_events(ev, ["c-g"], {}) == []


def _joined_before(tmp_path, page=True, text="item 13: send the security memo"):
    """c-a (no client) and c-b (Acme) joined by the PM before the migration, then Globex's c-g of the same page or
    the same "item 13". The joined item's own client is unknown; only its Acme mention tells the scope."""
    ev = [add("c-a", text, "me", "2026-11-03-1000-weekly-sync.md", "2026-11-03T10:00:00"),
          add("c-b", text, "me", "2026-11-05-1000-weekly-sync.md", "2026-11-05T10:00:00", client="Acme"),
          {"op": "same", "id": "c-b", "to": "c-a", "by": "pm", "source": "pm", "at": "2026-11-05T11:00:00"},
          add("c-g", text, "me", "2026-11-07-1000-weekly-sync.md", "2026-11-07T10:00:00", client="Globex")]
    if page:
        ev += [dict(PAGE, id="c-a"), dict(PAGE, id="c-b"), dict(PAGE, id="c-g")]
    C.append(tmp_path, ev)
    return ev


@pytest.mark.parametrize("page", [True, False])           # the same Tracker ID, or only the same "item 13"
def test_an_item_joined_before_the_migration_keeps_its_clients(tmp_path, clock, page):
    ev = _joined_before(tmp_path, page)
    assert C.link_events(ev, ["c-g"], {}) == []
    assert not [s for s in C.suggestions(ev, {}) if {s["id"], s["to"]} == {"c-g", "c-a"}]
    clock("2026-11-16T08:00:00")
    assert C.migrate(tmp_path, {})["links"] == 0
    assert sorted(tuple(i["ids"]) for i in C.load(tmp_path)) == [("c-a", "c-b"), ("c-g",)]


def test_an_item_with_no_client_of_its_own_is_matched_by_its_joined_client(tmp_path, clock):
    """The mention joined into it names Acme, so Acme's later "item 13" joins it by reference."""
    _joined_before(tmp_path, page=False)
    C.append(tmp_path, [add("c-c", "item 13: send the security memo", "me", "2026-11-09-1000-weekly-sync.md",
                            "2026-11-09T10:00:00", client="acme")])
    assert [e["to"] for e in C.link_events(C.read_events(tmp_path), ["c-c"], {})] == ["c-a"]   # the app path too
    clock("2026-11-16T08:00:00")
    assert C.migrate(tmp_path, {})["links"] == 1
    assert sorted(tuple(i["ids"]) for i in C.load(tmp_path)) == [("c-a", "c-b", "c-c"), ("c-g",)]


def test_a_hubspot_task_without_its_portal_is_no_tracker_id(tmp_path, clock):
    assert ID.tracker_id({"crm_task": "42"}) == ""
    assert ID.tracker_id({"crm_task": "42", "crm_portal": "111"}) == "crm:111:42"
    bare = {"op": "crm_link", "crm_task": "42", "crm_portal": ""}
    ev = _one_page({}, {}, bare, bare)
    assert C.link_events(ev, ["c-b"], {}) == []
    C.append(tmp_path, ev)
    clock("2026-11-16T08:00:00")
    assert C.migrate(tmp_path, {})["links"] == 0
    task = {"op": "crm_link", "crm_task": "42", "crm_portal": "111"}
    assert [e["to"] for e in C.link_events(_one_page({}, {}, task, task), ["c-b"], {})] == ["c-a"]
    other = dict(task, crm_portal="222")                        # the same task id in another portal
    assert C.link_events(_one_page({}, {}, task, other), ["c-b"], {}) == []


def test_split_by_the_id_the_joined_item_kept(tmp_path, clock):
    C.append(tmp_path, _one_page(dict(client="Acme"), dict(client="Acme"), PAGE, PAGE))
    clock("2026-11-16T08:00:00")
    assert C.migrate(tmp_path, {})["links"] == 1
    e = C.split(tmp_path, "c-a")                               # the PM sees c-a in the list
    assert e and (e["id"], e["to"]) == ("c-b", "c-a")
    assert sorted(i["id"] for i in C.load(tmp_path)) == ["c-a", "c-b"]
    assert C.split(tmp_path, "c-a") is None                    # nothing joined any more


# ---------------------------------------------------------------- migration and rollback

def historical(tmp_path):
    """The fixture register plus an item 13 said again on a later weekly (before G1, so it is its own item), both
    linked to the same Notion page by the PM (the Tracker ID key; the fixture knows no client)."""
    shutil.copy(LIVE / C.FILE, tmp_path / C.FILE)
    C.append(tmp_path, [{"op": "add", "id": "c-old13", "text": "confirm the SSO user mapping for item 13", "owner": "Mike",
                         "project": "", "series": "|acmeweeklystatus", "call": "2026-11-14-1000-acme-weekly-status.md",
                         "created": "2026-11-14T10:00:00+00:00", "due": "", "by": "app", "source": "notes"},
                        {"op": "link", "id": "c-deb94c8d56", "notion_page": "acme-13"},     # both on one Notion page
                        {"op": "link", "id": "c-old13", "notion_page": "acme-13"},
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


# ---------------------------------------------------------------- fix round 1 (review of PB-51)

def add(cid, text, owner, call, created, series="|weeklysync", client="", project="p"):
    e = {"op": "add", "id": cid, "text": text, "owner": owner, "project": project, "series": series, "call": call,
         "created": created, "due": "", "by": "app"}
    if client:
        e["client"] = client
    return e


def test_ref_without_a_known_client_is_only_a_suggestion(tmp_path, clock):
    """Two clients, both meetings titled "Weekly sync", the engine knows no client: never an automatic join."""
    C.append(tmp_path, [add("c-a13", "Send the SSO mapping for item 13", "Mike", "2026-11-03-1000-weekly-sync.md",
                            "2026-11-03T10:00:00", project="")])
    clock("2026-11-10T10:00:00")
    ev = C.record_notes(tmp_path, call="2026-11-10-1000-weekly-sync.md", aliases=["David"], people={},
                        notes="## Meeting notes\n### Action items\n- [10:00:40] **Mike**: Export the invoice CSV for item 13\n")
    assert [e for e in ev if e["op"] == "add"] and not [e for e in ev if e["op"] == "same"]
    assert len(open_with(C.load(tmp_path), "item 13")) == 2
    assert [s["to"] for s in C.suggestions(tmp_path, {})] == ["c-a13"]        # the PM is asked, nothing is joined


def test_ref_with_two_known_clients_is_neither_a_link_nor_a_suggestion(tmp_path):
    C.append(tmp_path, [add("c-a13", "Send the SSO mapping for item 13", "Mike", "2026-11-03-1000-weekly-sync.md",
                            "2026-11-03T10:00:00", client="Acme"),
                        add("c-g13", "Send the SSO mapping for item 13", "Mike", "2026-11-04-1000-weekly-sync.md",
                            "2026-11-04T10:00:00", client="Globex")])
    assert C.link_events(C.read_events(tmp_path), ["c-g13"], {}) == []
    assert C.suggestions(tmp_path, {}) == []


def test_ref_with_the_same_known_client_links(tmp_path):
    C.append(tmp_path, [add("c-a13", "Send the SSO mapping for item 13", "Mike", "2026-11-03-1000-acme-weekly.md",
                            "2026-11-03T10:00:00", "|acmeweekly", "Acme"),
                        add("c-b13", "Confirm the SSO mapping for item 13", "Mike", "2026-11-05-1400-acme-tech.md",
                            "2026-11-05T14:00:00", "|acmetech", "acme")])
    assert [e["to"] for e in C.link_events(C.read_events(tmp_path), ["c-b13"], {})] == ["c-a13"]


def test_a_bare_number_sign_is_not_a_reference(tmp_path):
    assert ID.ref_of("Make the SSO rollout our #1 priority") == ""
    assert ID.ref_of("Keep the billing fix as the #1 priority") == ""
    C.append(tmp_path, [add("c-p1", "Make the SSO rollout our #1 priority", "me", "2026-11-03-1000-weekly-sync.md",
                            "2026-11-03T10:00:00", client="Acme"),
                        add("c-p2", "Keep the billing fix as the #1 priority", "me", "2026-11-10-1000-weekly-sync.md",
                            "2026-11-10T10:00:00", client="Acme")])
    assert C.link_events(C.read_events(tmp_path), ["c-p2"], {}) == []


def test_same_first_name_is_not_the_same_person_for_a_link(tmp_path):
    C.append(tmp_path, [add("c-t13", "Send the SSO mapping for item 13", "Mike Turner", "2026-11-03-1000-weekly-sync.md",
                            "2026-11-03T10:00:00", client="Acme"),
                        add("c-b13", "Send the SSO mapping for item 13", "Mike Brown", "2026-11-10-1000-weekly-sync.md",
                            "2026-11-10T10:00:00", client="Acme")])
    assert C.link_events(C.read_events(tmp_path), ["c-b13"], {}) == []
    assert [s["to"] for s in C.suggestions(tmp_path, {})] == ["c-t13"]      # only a question to the PM
    lib = {"mike b.": "Mike Turner"}                                        # the voice library says they are one
    assert ID.person_id("Mike B.", lib) == ID.person_id("Mike Turner", lib)


def test_moving_the_older_call_keeps_the_joined_item_of_another_call(tmp_path):
    C.append(tmp_path, [add("c-a13", "Send the SSO mapping for item 13", "Mike", "2026-11-03-1000-acme-weekly.md",
                            "2026-11-03T10:00:00", client="Acme"),
                        add("c-b13", "Confirm the SSO mapping for item 13", "Mike", "2026-11-10-1000-acme-weekly.md",
                            "2026-11-10T10:00:00", client="Acme"),
                        {"op": "same", "id": "c-b13", "to": "c-a13", "by": "app"}])
    assert [i["id"] for i in C.load(tmp_path)] == ["c-a13"]
    C.append(tmp_path, [{"op": "move", "id": "c-a13", "to": "other"}])
    items = C.load(tmp_path)
    assert [i["id"] for i in items] == ["c-b13"] and items[0]["status"] == "open" and items[0]["ids"] == ["c-b13"]


def test_moving_the_newer_call_takes_only_its_mention(tmp_path):
    C.append(tmp_path, [add("c-a13", "Send the SSO mapping for item 13", "Mike", "2026-11-03-1000-acme-weekly.md",
                            "2026-11-03T10:00:00", client="Acme"),
                        add("c-b13", "Confirm the SSO mapping for item 13", "Mike", "2026-11-10-1000-acme-weekly.md",
                            "2026-11-10T10:00:00", client="Acme"),
                        {"op": "same", "id": "c-b13", "to": "c-a13", "by": "app"},
                        {"op": "move", "id": "c-b13", "to": "other"}])
    items = C.load(tmp_path)
    assert [i["id"] for i in items] == ["c-a13"] and items[0]["ids"] == ["c-a13"] and len(items[0]["history"]) == 1


def test_migration_keeps_a_pm_edit_of_the_newer_item(tmp_path, clock):
    C.append(tmp_path, [add("c-a13", "Send the mapping for item 13", "Mike", "2026-11-03-1000-acme-weekly.md",
                            "2026-11-03T10:00:00", client="Acme"),
                        add("c-b13", "Send the mapping for item 13 again", "Mike", "2026-11-10-1000-acme-weekly.md",
                            "2026-11-10T10:00:00", client="Acme"),
                        {"op": "edit", "id": "c-b13", "at": "2026-11-11T09:00:00",
                         "text": "PM wording: confirm mapping for item 13 with Okta", "due": "Nov 30"}])
    clock("2026-11-16T08:00:00")
    assert C.migrate(tmp_path, {})["links"] == 1
    items = C.load(tmp_path)
    assert len(items) == 1 and items[0]["text"] == "PM wording: confirm mapping for item 13 with Okta" and items[0]["due"] == "Nov 30"
    C.split(tmp_path, "c-b13")                                   # undo: each item has its own text again
    assert {i["id"]: i["text"] for i in C.load(tmp_path)} == {
        "c-a13": "Send the mapping for item 13", "c-b13": "PM wording: confirm mapping for item 13 with Okta"}


def test_migration_never_closes_an_open_item(tmp_path, clock):
    C.append(tmp_path, [add("c-old", "Send the deck to legal", "me", "2026-11-03-1000-acme-weekly.md", "2026-11-03T10:00:00"),
                        add("c-new", "Send the deck to finance", "me", "2026-11-10-1000-acme-weekly.md", "2026-11-10T10:00:00"),
                        {"op": "link", "id": "c-old", "notion_page": "aaaa"}, {"op": "link", "id": "c-new", "notion_page": "aaaa"},
                        {"op": "done", "id": "c-old", "at": "2026-11-05T10:00:00"}])
    clock("2026-11-16T08:00:00")
    C.migrate(tmp_path, {})
    assert [(i["id"], i["status"]) for i in C.load(tmp_path)] == [("c-old", "done"), ("c-new", "open")]


def test_migration_of_a_large_register_is_one_pass(tmp_path, clock):
    import time
    ev = []
    for n in range(1000):
        day = "2026-{:02d}-{:02d}".format(1 + n // 28 % 12, 1 + n % 28)
        ev.append(add("c-{}".format(n), "send part {} on subject {} for item {}".format(n, n % 37, n % 50), "Mike",
                      "{}-{}-acme-weekly.md".format(day, n), "{}T10:{:02d}:00".format(day, n % 60), client="Acme"))
    C.append(tmp_path, ev)
    t0 = time.monotonic()
    C.fold(C.read_events(tmp_path))
    floor = time.monotonic() - t0
    clock("2026-12-31T08:00:00")
    t0 = time.monotonic()
    res = C.migrate(tmp_path, {})
    spent = time.monotonic() - t0
    assert res["links"] > 0
    assert spent < max(2.0, 6 * floor), (spent, floor)


def test_migrate_after_rollback_in_the_same_second_gets_a_new_tag(tmp_path, clock):
    reg = historical(tmp_path)
    clock("2026-11-16T08:00:00+00:00")
    first = C.migrate(reg, {})["tag"]
    C.rollback(reg)
    again = C.migrate(reg, {})
    assert again["tag"] and again["tag"] != first and again["events"] > 0
    assert C.migration_tag(C.read_events(reg)) == again["tag"]


def test_rollback_without_a_tag_undoes_every_migration(tmp_path, clock):
    reg = historical(tmp_path)
    before = C.load(reg)
    clock("2026-11-16T08:00:00+00:00")
    C.migrate(reg, {})
    twin = [dict(e, mig=e["mig"] + "x", tag=e["mig"] + "x" if e.get("tag") else None)   # a second process migrated too
            for e in C.read_events(reg) if e.get("mig")]
    C.append(reg, [{k: v for k, v in e.items() if v is not None} for e in twin])
    C.rollback(reg)
    assert C.load(reg) == before and not C.migration_tag(C.read_events(reg))


def test_voice_library_default_path_is_the_apps(tmp_path, monkeypatch):
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "voices.json").write_text(json.dumps({"aliases": {"m. t.": "Mike Turner"}}), encoding="utf-8")
    monkeypatch.delenv("LT_VOICES", raising=False)
    monkeypatch.setenv("LT_AI_STATE", str(tmp_path / "state" / "ai-routes.json"))
    assert ID.voice_aliases() == {"m. t.": "Mike Turner"}
    monkeypatch.setenv("LT_VOICES", "")                         # set but empty: no library
    assert ID.voice_aliases() == {}


# ---------------------------------------------------------------- fix round 3 (review of PB-51)

def test_a_trailing_hedge_or_a_quoted_try_keeps_a_firm_task():
    assert not ID.is_tentative("Send Dana the SOW by Friday, maybe earlier")
    assert not ID.is_tentative("Send the deck by Friday if possible")
    assert not ID.is_tentative("Fix the 'try to login' banner")
    assert not ID.is_tentative('Rename the "we will try" label')
    assert not ID.is_tentative("надіслати звіт у п'ятницю, можливо раніше")
    assert not ID.is_tentative("надіслати презентацію до п'ятниці, по можливості")
    assert not ID.is_tentative("виправити банер «спробуємо ще раз»")
    assert ID.is_tentative("If possible, send the deck by Friday")
    assert ID.is_tentative("We will try to send Dana's list by Friday")      # an apostrophe is not a quote
    assert ID.is_tentative("Maybe: send the deck")
    assert ID.is_tentative("якщо вийде, надішлемо звіт у п'ятницю")


def test_migration_keeps_a_firm_task_with_a_trailing_hedge_open(tmp_path):
    C.append(tmp_path, [add("c-firm", "Send the deck by Friday if possible", "me", "2026-11-14-1000-weekly-sync.md",
                            "2026-11-14T10:00:00+00:00")])
    C.ensure_migrated(tmp_path, {})
    item = next(i for i in C.load(tmp_path) if i["id"] == "c-firm")
    assert item["status"] == "open" and not item.get("modality")


def test_cli_agree_makes_a_proposal_open(tmp_path, capsys):
    C.append(tmp_path, [add("c-try", "try to confirm the pilot user list", "Mike", "2026-11-14-1000-weekly-sync.md",
                            "2026-11-14T10:00:00+00:00")])
    C.ensure_migrated(tmp_path, {})
    assert next(i for i in C.load(tmp_path) if i["id"] == "c-try")["status"] == "proposal"
    assert C.main(["agree", str(tmp_path), "c-try"]) == 0
    assert json.loads(capsys.readouterr().out)["op"] == "agree"
    item = next(i for i in C.load(tmp_path) if i["id"] == "c-try")
    assert (item["status"], item["modality"]) == ("open", "agreed")
    assert C.main(["list", str(tmp_path)]) == 0
    assert "c-try" in capsys.readouterr().out                               # back in the owed list
