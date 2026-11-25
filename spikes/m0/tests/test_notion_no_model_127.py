"""PB-127 (owner 25.11): the Notion step after a call makes no model request. The page is the call's ready notes and
lists; notes not ready yet show a line that --fill replaces; the Decisions register's Area, Door, Context and the
replaced decision come from the notes step. Fakes only; nothing reaches Notion or a model."""
import json

from livetips_m0 import call_lists, callnotes
from livetips_m0 import wrapup_notion as W
from test_wrapup_main import NOTES, _stateful_notion, call, out  # noqa: F401  (the fixture and helpers of the end-to-end tests)


def _notes_file(call):
    return call.tmp / "2026-10-01-1000-weekly.notes.md"


def _texts(blocks):
    return ["".join(r["text"]["content"] for r in b[b["type"]].get("rich_text", [])) for b in blocks if "type" in b]


def _section(pages, pid):
    kids = pages[pid]
    i = next(k for k, b in enumerate(kids) if b["type"] == "heading_2")
    j = next(k for k in range(i + 1, len(kids)) if kids[k]["type"] == "heading_2" and _texts([kids[k]])[0] in ("Transcript", "Транскрипт"))
    return kids[i + 1:j]


def test_notes_not_ready_page_at_once_then_fill_without_a_model(call, capsys, monkeypatch):
    """Acceptance 4: the page is created at once with the header, the transcript and "being written"; --fill puts the
    notes in place of that line and the topic into the title."""
    _notes_file(call).unlink()
    pages, log = _stateful_notion(call, monkeypatch)
    assert W.main(call.base + ["--notes", "pending"]) == 0
    res = out(capsys)
    assert res["ok"] and res["created"] and res["notes"] == "pending" and res["cost_usd"] == 0
    pid = next(iter(pages))
    body = _texts(_section(pages, pid))
    assert body[0] == W.TXT["en"]["pending"] and "We agreed." not in body
    first = [c for c in call.calls if c[0] == "POST" and c[1] == "/pages"][0][2]
    assert first["properties"]["Name"]["title"][0]["text"]["content"] == "Weekly sync "      # no topic before the notes
    assert len(pages) == 2                                                                   # the transcript sub-page is there
    assert "notion" not in W.call_language.read_sidecar(call.md).get("artifacts", {})       # not "written" until the notes are

    _notes_file(call).write_text(NOTES, encoding="utf-8")
    title = [{"type": "text", "text": {"content": "Weekly sync "}, "plain_text": "Weekly sync "},
             {"type": "mention", "mention": {"type": "date", "date": {"start": "2026-10-01T10:00"}}, "plain_text": "x"}]
    real = W.notion

    def with_title(method, path, body=None):
        r = real(method, path, body)
        if method == "GET" and path == "/pages/" + pid:
            r["properties"] = {"Name": {"type": "title", "title": title}}
        return r
    monkeypatch.setattr(W, "notion", with_title)
    log.clear()
    assert W.main(call.base + ["--page", pid, "--fill", "--notes", "failed"]) == 0
    res = out(capsys)
    assert res["ok"] and res["notes"] == "ready" and not res["created"]
    body = _texts(_section(pages, pid))
    assert W.TXT["en"]["pending"] not in body and body[0] == "We agreed."
    assert not [p for m, p in log if m == "POST"]                                            # no second page, no second transcript
    rename = [c for c in call.calls if c[0] == "PATCH" and c[1] == "/pages/" + pid and "Name" in c[2]["properties"]]
    t = rename[-1][2]["properties"]["Name"]["title"]
    assert t[0]["text"]["content"] == "Weekly sync: Release plan " and t[1]["type"] == "mention"
    assert W.call_language.read_sidecar(call.md)["artifacts"]["notion"] == "en"


def test_notes_failed_says_so(call, capsys):
    """Acceptance 4: no notes and none coming: the page says the notes were not written and how to make them."""
    _notes_file(call).unlink()
    assert W.main(call.base) == 0
    assert out(capsys)["notes"] == "failed"
    blocks = next(c for c in call.calls if c[0] == "PATCH" and c[1].endswith("/children"))[2]["children"]
    assert W.TXT["en"]["failed"] in _texts(blocks) and "Make meeting notes" in W.TXT["en"]["failed"]
    assert "Зробити нотатки зустрічі" in W.TXT["uk"]["failed"]


def test_page_and_call_page_say_the_same_words(call, capsys):
    """Acceptance 2: Summary, decisions and tasks on the Notion page are the call page's own lists (call_lists, the same
    rule as LiveTipsCore CallLists), word for word, also after the PM edited the decisions on the call page."""
    tips = call.tmp / "2026-10-01-1000-weekly.md.tips.jsonl"
    tips.write_text(json.dumps({"kind": "situation", "source": "call_page", "lists": ["decisions"],
                                "decisions": ["Ship on Monday instead"]}) + "\n")
    assert W.main(call.base) == 0
    page = next(c for c in call.calls if c[0] == "PATCH" and c[1].endswith("/children"))[2]["children"]
    lists = call_lists.read(call.md)                                             # what the call page shows
    mn = callnotes.parse_meeting(_notes_file(call).read_text(encoding="utf-8"))

    def plain_of(kind):
        rows = []
        for b in page:
            if b["type"] != kind:
                continue
            parts = [r for r in b[kind]["rich_text"] if (r.get("annotations") or {}).get("color") != "gray"
                     and not (r.get("annotations") or {}).get("bold") and r["text"]["content"] != ": "]
            rows.append("".join(r["text"]["content"] for r in parts))
        return rows
    assert plain_of("bulleted_list_item")[:1] == lists["decisions"] == ["Ship on Monday instead"]
    assert plain_of("to_do") == [call_lists.task_line(dict(t, owner="")) for t in lists["tasks"]] == ["send the deck (due: Thursday)"]
    assert [b for b in page if b["type"] == "to_do"][0]["to_do"]["rich_text"][1]["text"]["content"] == lists["tasks"][0]["owner"]
    assert _texts([b for b in page if b["type"] == "paragraph"])[0] == mn["tldr"]
    props = [c for c in call.calls if c[0] == "POST" and c[1] == "/pages"][0][2]["properties"]
    assert props["Summary"]["rich_text"][0]["text"]["content"] == mn["tldr"]


def test_title_topic_from_the_notes():
    """Acceptance 3: meeting name + topic from the notes, no model."""
    assert W.page_topic({"recap": ["**Release plan.** We went through it."]}) == "Release plan"
    assert W.page_topic({"recap": ["We went through the release and picked a day."]}) == "We went through the release and"
    assert W.page_topic({"recap": [], "topics": [{"title": "Export screen"}]}) == "Export screen"
    assert W.page_topic({}) == ""
    assert W.page_name("Weekly sync", W.page_topic({"recap": ["**Release plan.** x"]})) == "Weekly sync: Release plan"


def test_add_topic_leaves_a_calendar_or_renamed_title_alone(monkeypatch):
    seen = []
    monkeypatch.setattr(W, "notion", lambda m, p, b=None: seen.append((m, p, b)) or {})
    page = {"id": "p", "properties": {"Name": {"title": [{"type": "text", "text": {"content": "Board review with Sarah"}}]}}}
    W.add_topic(page, {"title": "Name"}, ["Weekly sync"], "Release plan")
    own = dict(page, properties={"Name": {"title": [{"type": "text", "text": {"content": "Weekly sync "}}]}})
    W.add_topic(own, {"title": "Name"}, ["Weekly sync"], "")
    assert seen == []
    W.add_topic(own, {"title": "Name"}, ["Renamed in the app", "Weekly sync"], "Release plan")     # the transcript's name matches
    assert seen[0][2]["properties"]["Name"]["title"][0]["text"]["content"] == "Weekly sync: Release plan "


def test_regenerate_without_meeting_notes_fails_and_keeps_the_page(call, capsys):
    """Review: --page without --fill never puts the "not written" line over a written section."""
    _notes_file(call).unlink()
    assert W.main(call.base + ["--page", "0123456789abcdef0123456789abcdef"]) == 1
    assert "no meeting notes" in out(capsys)["error"] and not [c for c in call.calls if c[0] in ("PATCH", "POST", "DELETE")]


def test_regenerate_and_a_moved_page_say_what_the_after_call_page_says(call, capsys, monkeypatch, tmp_path):
    """Acceptance 2 and 7 (review 3): the app gives every page run the call page's task lines (PyTool, CoreChecks
    notionPageRunPB127); with them Regenerate (--page) and a new page after a move write the same Summary, decisions
    and tasks as the after-call page, word for word, and Regenerate keeps the PM's register edits."""
    lines = ["Emily Clark: send the deck (Nov 28)", "Me: book the room"]
    acts = ["--actions", json.dumps(lines)]
    pages, _ = _stateful_notion(call, monkeypatch)
    assert W.main(call.base + acts) == 0
    pid = out(capsys)["page_id"]
    after = _texts(_section(pages, pid))
    assert lines[0] in after and "We agreed." in after
    assert W.main(call.base + acts + ["--page", pid]) == 0                       # Regenerate on the same page
    assert out(capsys)["ok"]
    assert _texts(_section(pages, pid)) == after
    moved = W.main(call.base + acts + ["--meetings-db", "db-of-the-new-project"])  # a new page in another database
    assert moved == 0
    pid2 = out(capsys)["page_id"]
    assert pid2 != pid and _texts(_section(pages, pid2)) == after


def test_regenerate_without_notes_gives_a_code_the_app_translates(call, capsys):
    """Review 3: the app shows its own EN or UK line for this case (call.outlang.notionNoNotes), not the English error."""
    _notes_file(call).unlink()
    assert W.main(call.base + ["--page", "0123456789abcdef0123456789abcdef"]) == 1
    assert out(capsys)["code"] == "no_notes"


def test_actions_from_the_call_page_register_win(call, capsys):
    """Acceptance 2: the app passes the call page's own task lines (its commitments register); the page writes those."""
    lines = ["Emily Clark: send the deck (Nov 28)", "Me: book the room"]
    assert W.main(call.base + ["--actions", json.dumps(lines)]) == 0
    page = next(c for c in call.calls if c[0] == "PATCH" and c[1].endswith("/children"))[2]["children"]
    assert _texts([b for b in page if b["type"] == "to_do"]) == lines


def test_every_task_dropped_on_the_call_page_none_on_the_notion_page(call, capsys):
    """Review 3: the call page shows no task once the PM dropped them all; the app passes [] and the notes' tasks stay out."""
    assert W.main(call.base + ["--actions", "[]"]) == 0
    page = next(c for c in call.calls if c[0] == "PATCH" and c[1].endswith("/children"))[2]["children"]
    assert not [b for b in page if b["type"] == "to_do"] and "We agreed." in _texts(page)


def test_the_notes_step_writes_the_register_fields_in_the_same_request(tmp_path):
    """Acceptance 5: the Meeting notes request gets the areas and the active decisions from the snapshot; the model's
    numbers become the decision's text and the replaced decision's title; the call page's decisions are unchanged."""
    from livetips_m0.decision_notion import areas_path, snapshot_text
    live = tmp_path / "Orbit" / "live"
    live.mkdir(parents=True)
    (tmp_path / "Orbit" / "pacenote").mkdir()
    snap = tmp_path / "Orbit" / "pacenote" / "decisions.md"
    snap.write_text(snapshot_text(
        [{"date": "2026-10-01", "title": "Pilot goes to Acme first", "stated_by": "David Kim", "area": "Scope"}], "now"))
    areas_path(snap).write_text(json.dumps(["Scope", "Budget, Q4"]))
    md = live / "2026-10-12-1000-orbit.md"
    md.write_text("[10:00:01] Me (en): hi\n")
    seen = []

    def ask(system, user, max_tokens):
        seen.append((system, user))
        return ("## Meeting notes\n### TL;DR\nWe moved the pilot.\n### Decisions\n- [10:00:02] **Emily Clark**: Pilot goes to Northwind first\n"
                "### Unclear\n- one name\n### Decision register\n"
                '- {"n": 1, "area": "Scope", "door": "two-way", "context": "Northwind has the accounts.", "replaces": 1}\n'
                '- {"n": 7, "area": "x", "door": "", "context": "", "replaces": 0}\n- not json\n')
    sec = callnotes.make_meeting(md, "notes", "[10:00:01] Me (en): hi", "en", ask)
    assert len(seen) == 1 and "### Decision register" in seen[0][0]
    assert "# AREAS\n- Scope\n- Budget, Q4\n" in seen[0][1] and "1. Pilot goes to Acme first (2026-10-01)" in seen[0][1]
    rows = callnotes.register_in(sec)
    want = [{"decision": "Pilot goes to Northwind first", "area": "Scope", "door": "two-way",
             "context": "Northwind has the accounts.", "replaces": "Pilot goes to Acme first"}]
    assert rows == want
    m = callnotes.parse_meeting(sec)
    assert [i["text"] for i in m["decisions"]] == ["**Emily Clark**: Pilot goes to Northwind first"] and m["unclear"][0]["text"] == "one name"
    assert "not json" not in sec
    # the register leaves the notes for its own file: the notes every reader sees have no JSON lines
    notes = callnotes.take_register(md, callnotes.put_meeting("# Notes\n\n## Summary\nx\n", sec))
    assert "Decision register" not in notes and '"decision"' not in notes and "one name" in notes and "## Summary" in notes
    assert callnotes.decision_register(md) == want and callnotes.register_path(md).name.endswith(".decision-register.json")
    # the PM edits the Meeting notes on the call page (the app rewrites the section): the register stays
    callnotes.put_meeting(notes, "## Meeting notes\n### TL;DR\nEdited.\n")
    assert callnotes.decision_register(md) == want
    # notes written again with no decisions: the register is emptied, not left stale
    callnotes.take_register(md, "## Meeting notes\n### TL;DR\nNothing decided.\n")
    assert callnotes.decision_register(md) == []


def test_register_file_in_a_call_folder_is_one_for_the_transcript_and_the_named_copy(tmp_path):
    """PB-127 review: the call-folder layout; the notes step may get X.app.named.md (.work) or X.app.md, the decisions
    write gets X.app.md: all three name one register file in .work, and the muted-mic lines of ### Unclear stay."""
    folder = tmp_path / "Orbit" / "live" / "2026-11-18-orbit-sync"
    (folder / ".work").mkdir(parents=True)
    md = folder / "2026-11-18-1512-orbit-sync.app.md"
    named = folder / ".work" / "2026-11-18-1512-orbit-sync.app.named.md"
    assert callnotes.register_path(md) == callnotes.register_path(named)
    assert callnotes.register_path(md).parent.name == ".work"
    sec = callnotes.with_muted("## Meeting notes\n### Decisions\n- [10:00:02] Ship on Friday\n### Unclear\n- one name\n"
                               '### Decision register\n- {"decision": "Ship on Friday", "area": "Delivery"}', ["[10:01:00] muted 2 min"])
    notes = callnotes.take_register(named, callnotes.put_meeting("", sec))
    assert "muted 2 min" in notes and "one name" in notes and "Decision register" not in notes
    assert callnotes.decision_register(md) == [{"decision": "Ship on Friday", "area": "Delivery"}]
    from livetips_m0.decision_notion import notes_details
    assert notes_details(md, "Ship on Friday")["area"] == "Delivery"


def test_the_area_options_live_beside_the_snapshot_not_in_it(tmp_path):
    """PB-127 review: the preparation and the evidence read decisions.md as text; the options are kept apart, whole."""
    from livetips_m0.decision_notion import areas_path, parse_snapshot, read_areas, snapshot_text
    snap = tmp_path / "decisions.md"
    t = snapshot_text([{"date": "2026-10-01", "title": "Acme first", "stated_by": "", "area": "Scope"}], "now")
    assert "Area options" not in t and [d["title"] for d in parse_snapshot(t)] == ["Acme first"]
    assert read_areas(snap) == []                                                          # an older snapshot: no file
    areas_path(snap).write_text(json.dumps(["Scope", "Budget, Q4"]))
    assert read_areas(snap) == ["Scope", "Budget, Q4"] and areas_path(snap).name == ".decision-areas.json"


def test_old_notes_have_no_register_and_no_snapshot_is_fine(tmp_path):
    md = tmp_path / "live" / "2026-10-12-1000-x.md"
    assert callnotes.register_in(NOTES) == [] and callnotes.decision_register(md) == []
    assert callnotes.active_decisions(md) == [] and callnotes.area_options(md) == []
    assert "(none)" in callnotes.register_input([])
    assert "# AREAS\n- Scope" in callnotes.register_input([{"title": "a", "area": "Scope"}])     # no options file: the areas in use
    sec = callnotes.with_register("## Meeting notes\n### TL;DR\nx\n### Decision register\n- {\"n\": 1}\n", [])
    assert "Decision register" not in sec                  # no decisions: no empty heading left behind


def test_model_calls_per_call_from_the_ledger(call, capsys, monkeypatch, tmp_path):
    """Acceptance 6: every after-call model request lands in the spend ledger with the call's tag (LT_CALL, set by the app);
    the page step reports the call's count once the notes are ready. A call with notes, a Notion page and a decision
    auto-add has its notes request only: no after_notion and no after_decision_notion row (before PB-127 each wrote one)."""
    from test_decisions_088 import call_file, fake, notes_with_register
    from livetips_m0 import decision_notion as D
    from livetips_m0 import routing
    from livetips_m0.providers import Completion

    state = tmp_path / "state" / "ai-routes.json"
    state.parent.mkdir()
    monkeypatch.setenv("LT_AI_STATE", str(state))

    class Leg:
        n = 0

        def complete(self, **kw):
            Leg.n += 1
            return Completion(text="## Summary\nok\n## Topics\n- t", model=kw["model"], input_tokens=5, output_tokens=2)

    def router(stage, tag):         # the app's routed provider: every request goes through spend_ledger
        monkeypatch.setenv("LT_AI_STAGE", stage)
        monkeypatch.setenv("LT_CALL", tag)
        return routing.RoutedProvider("after", "code", {"code": Leg()}, state=routing.RouteState(str(state)))

    # this call's notes, the real notes step (callnotes.make_notes) on a copy of the call: all its requests are counted
    notes_md = tmp_path / "notes" / call.md.name
    notes_md.parent.mkdir()
    notes_md.write_text(call.md.read_text(encoding="utf-8"), encoding="utf-8")
    assert callnotes.make_notes(notes_md, "sonnet", "en", provider=router("after_notes", "c0ffee000001"))["ok"]
    made = Leg.n
    assert made >= 2                                # the notes and their Meeting notes at least
    router("after_notes", "c0ffee000002").complete(model="sonnet", system="s", user="u", max_tokens=10, cache_ttl=None)
    router("live", "").complete(model="sonnet", system="s", user="u", max_tokens=10, cache_ttl=None)   # live: no tag
    monkeypatch.setenv("LT_CALL", "c0ffee000001")
    monkeypatch.setenv("LT_AI_STAGE", "after_notion")
    assert W.main(call.base) == 0
    res = out(capsys)
    assert res["ok"] and res["notes"] == "ready" and res["cost_usd"] == 0
    assert res["ai_calls"] == {"calls": made, "by_stage": {"after_notes": made}}

    fake(monkeypatch)
    md = call_file(tmp_path / "dec")
    notes_with_register(md, [{"decision": "Orbit pilot goes to Northwind first", "area": "", "door": "", "context": ""}])
    monkeypatch.setenv("LT_AI_STAGE", "after_decision_notion")
    assert D.main(["--db", "dec1", "--title", "Orbit pilot goes to Northwind first", "--call-file", str(md)]) == 0
    capsys.readouterr()
    rows = [json.loads(x) for x in (state.parent / "spend-ledger.jsonl").read_text().splitlines()]
    assert [r["job"] for r in rows] == ["after_notes"] * (made + 1) + ["live"]
    assert "call" not in rows[-1] and routing.call_model_calls(state, "c0ffee000001")["calls"] == made
    assert routing.call_model_calls(state, "") == {"calls": 0, "by_stage": {}}


def test_no_tag_no_count(call, capsys, monkeypatch):
    """The app always tags; a run without LT_CALL (the skill, an older app) reports no count rather than a wrong one."""
    monkeypatch.delenv("LT_CALL", raising=False)
    assert W.main(call.base) == 0
    assert "ai_calls" not in out(capsys)
