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


def test_actions_from_the_call_page_register_win(call, capsys):
    """Acceptance 2: the app passes the call page's own task lines (its commitments register); the page writes those."""
    lines = ["Emily Clark: send the deck (Nov 28)", "Me: book the room"]
    assert W.main(call.base + ["--actions", json.dumps(lines)]) == 0
    page = next(c for c in call.calls if c[0] == "PATCH" and c[1].endswith("/children"))[2]["children"]
    assert _texts([b for b in page if b["type"] == "to_do"]) == lines


def test_the_notes_step_writes_the_register_fields_in_the_same_request(tmp_path):
    """Acceptance 5: the Meeting notes request gets the areas and the active decisions from the snapshot; the model's
    numbers become the decision's text and the replaced decision's title; the call page's decisions are unchanged."""
    from livetips_m0.decision_notion import snapshot_text
    live = tmp_path / "Orbit" / "live"
    live.mkdir(parents=True)
    (tmp_path / "Orbit" / "pacenote").mkdir()
    (tmp_path / "Orbit" / "pacenote" / "decisions.md").write_text(snapshot_text(
        [{"date": "2026-10-01", "title": "Pilot goes to Acme first", "stated_by": "David Kim", "area": "Scope"}], "now",
        ["Scope", "Delivery"]))
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
    assert "# AREAS\nScope, Delivery" in seen[0][1] and "1. Pilot goes to Acme first (2026-10-01)" in seen[0][1]
    rows = callnotes.decision_register(sec)
    assert rows == [{"decision": "Pilot goes to Northwind first", "area": "Scope", "door": "two-way",
                     "context": "Northwind has the accounts.", "replaces": "Pilot goes to Acme first"}]
    m = callnotes.parse_meeting(sec)
    assert [i["text"] for i in m["decisions"]] == ["**Emily Clark**: Pilot goes to Northwind first"] and m["unclear"][0]["text"] == "one name"
    assert "not json" not in sec


def test_the_snapshot_keeps_the_area_options_and_its_readers_still_read_only_decisions():
    from livetips_m0.decision_notion import parse_snapshot, snapshot_areas, snapshot_text
    t = snapshot_text([{"date": "2026-10-01", "title": "Acme first", "stated_by": "", "area": "Scope"}], "now", ["Scope", "Budget, Q4"])
    assert snapshot_areas(t) == ["Scope", "Budget  Q4"] and [d["title"] for d in parse_snapshot(t)] == ["Acme first"]
    assert snapshot_areas(snapshot_text([], "now")) == []                                 # an older snapshot: no line


def test_old_notes_have_no_register_and_no_snapshot_is_fine(tmp_path):
    assert callnotes.decision_register(NOTES) == []
    md = tmp_path / "live" / "2026-10-12-1000-x.md"
    assert callnotes.active_decisions(md) == [] and callnotes.area_options(md) == []
    assert "(none)" in callnotes.register_input([])
    assert "# AREAS\nScope" in callnotes.register_input([{"title": "a", "area": "Scope"}])     # no options line: the areas in use
    sec = callnotes.with_register("## Meeting notes\n### TL;DR\nx\n### Decision register\n- {\"n\": 1}\n", [])
    assert "Decision register" not in sec                  # no decisions: no empty heading left behind
