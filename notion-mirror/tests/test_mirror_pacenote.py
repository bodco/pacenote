"""INT5: the mirror without Control Tower (Pacenote mode). Synthetic data only."""
import json
from pathlib import Path

import mirror
from fake_notion import dashed, date, page, title, uid

ROOT, M1, P1, P2, ROW = uid(40), uid(41), uid(42), uid(43), uid(44)


class PacenoteWorld:
    def __init__(self):
        self.ds = {"pmeet": [page(M1, {"Name": title("Kickoff with Mike"), "Date": date("2026-11-18"),
                                        "Project": {"type": "select", "select": {"name": "Orbit"}}})]}
        self.search = [
            page(P1, {"title": title("Team handbook")}, edited="2026-11-18T10:00:00.000Z", parent={"type": "workspace", "workspace": True}),
            page(ROOT, {"title": title("Pacenote")}, edited="2026-11-17T10:00:00.000Z", parent={"type": "workspace", "workspace": True}),
            page(ROW, {"Name": title("A row")}, edited="2026-11-16T10:00:00.000Z", parent={"type": "data_source_id", "data_source_id": "pmeet"}),
            page(P2, {"title": title("Old notes")}, edited="2026-10-01T10:00:00.000Z", parent={"type": "page_id", "page_id": dashed(ROOT)}),
        ]
        self.fail_search = False
        self.calls = []

    def answer(self, method, path, body=None, version=None):
        self.calls.append((method, path, json.dumps(body or {}, sort_keys=True)))
        if path == "/search":
            if (body or {}).get("filter", {}).get("value") == "data_source":
                return {"results": [], "has_more": False}       # no Control Tower here
            if self.fail_search:
                raise mirror.NotionError(500, "down")
            return {"results": self.search, "has_more": False}
        if path.startswith("/data_sources/") and path.endswith("/query"):
            if path.split("/")[2] not in self.ds:
                raise mirror.NotionError(404, "no such data source")
            return {"results": self.ds[path.split("/")[2]], "has_more": False}
        if path.startswith("/data_sources/"):
            if path.split("/")[2] not in self.ds:
                raise mirror.NotionError(404, "no such data source")
            return {"properties": {}}
        if path.startswith("/users"):
            return {"results": [], "has_more": False}
        if "/markdown" in path:
            return {"markdown": "Body.", "truncated": False}
        raise AssertionError("unexpected call %s %s" % (method, path))


def setup(tmp_path, monkeypatch, world, pacenote=True):
    work = tmp_path / "work"
    work.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()
    cfg = json.loads((Path(__file__).parents[1] / "mirror.config.example.json").read_text())
    cfg.update(work_root=str(work), secrets_file="", configs_dir="")
    if pacenote:
        cfg["pacenote"] = {"root_page_id": dashed(ROOT), "databases": {"meetings": "pmeet", "tasks": "gone", "risks": ""}}
    cfgp = conf / "mirror.config.json"
    cfgp.write_text(json.dumps(cfg))
    for name, val in (("CONFIG_PATH", str(cfgp)), ("STATE_DIR", str(conf / "state")), ("STATE_PATH", str(conf / "state" / "state.json")),
                      ("LOCK_PATH", str(conf / "state" / "mirror.lock"))):
        monkeypatch.setattr(mirror, name, val)
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setattr(mirror.Notion, "req", lambda self, m, p, body=None, version=None: (
        setattr(self, "calls", self.calls + 1), world.answer(m, p, body, version))[1])
    return work, cfgp


def run(monkeypatch, *args):
    monkeypatch.setattr(mirror.sys, "argv", ["mirror.py"] + list(args))
    return mirror.main()


def test_discover_without_control_tower_switches_to_pacenote_mode(tmp_path, monkeypatch, capsys):
    w = PacenoteWorld()
    _, cfgp = setup(tmp_path, monkeypatch, w)
    assert run(monkeypatch, "--discover") == 0
    cfg = json.loads(cfgp.read_text())
    assert cfg["mode"] == "pacenote" and cfg["projects_ds"] == "" and cfg["databases"] == {}
    out = capsys.readouterr().out
    assert "pacenote mode: meetings + shared pages" in out and "tasks (HTTP 404)" in out


def test_discover_without_anything_still_asks_for_control_tower(tmp_path, monkeypatch):
    w = PacenoteWorld()
    setup(tmp_path, monkeypatch, w, pacenote=False)
    try:
        run(monkeypatch, "--discover")
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "Projects database" in str(e)


def test_full_run_writes_rows_and_shared_pages_under_work_notion(tmp_path, monkeypatch):
    w = PacenoteWorld()
    work, _ = setup(tmp_path, monkeypatch, w)
    run(monkeypatch, "--discover")
    assert run(monkeypatch, "--full") == 2          # the "gone" tasks database is an error, the rest is written
    files = sorted(str(p.relative_to(work)) for p in work.rglob("*.md"))
    assert any(f.startswith("_notion/meetings/2026-11-18 Kickoff with Mike [") for f in files)
    pages = [f for f in files if f.startswith("_notion/pages/")]
    assert len(pages) == 3 and any("Team handbook" in f for f in pages) and any("Pacenote [" in f for f in pages)
    assert not any("A row" in f for f in files)      # database rows come only from their database
    assert "_notion/INDEX.md" in files and "_notion/MIRROR_STATUS.md" in files
    assert not any(c[1] == "/data_sources//query" for c in w.calls)


def test_incremental_stops_at_window_and_failed_search_archives_nothing(tmp_path, monkeypatch):
    w = PacenoteWorld()
    work, _ = setup(tmp_path, monkeypatch, w)
    run(monkeypatch, "--discover")
    run(monkeypatch, "--full")
    st = json.loads((tmp_path / "conf" / "state" / "state.json").read_text())
    st["last_run"] = "2026-11-17T12:00:00.000Z"
    (tmp_path / "conf" / "state" / "state.json").write_text(json.dumps(st))
    w.calls.clear()
    run(monkeypatch)
    assert sum(1 for c in w.calls if c[1] == "/search") == 1
    w.fail_search = True
    assert run(monkeypatch, "--full") == 2
    assert len([p for p in (work / "_notion" / "pages").glob("*.md")]) == 3
    assert not (work / "_notion" / "_archived").exists()


def test_page_is_standalone():
    assert mirror.page_is_standalone({"parent": {"type": "workspace"}})
    assert mirror.page_is_standalone({"parent": {"type": "page_id"}})
    assert not mirror.page_is_standalone({"parent": {"type": "data_source_id"}})
    assert not mirror.page_is_standalone({"parent": {"type": "database_id"}})
    assert not mirror.page_is_standalone({"parent": {"type": "workspace"}, "in_trash": True})


def test_lost_pages_listed_for_project_memory(tmp_path, monkeypatch):
    """PB-88: pages the mirror can no longer read go to state "lost" (the app passes them to project memory as
    revoked); a page read again leaves it. A database that answers 403 keeps its files, a 401 or 5xx loses nothing."""
    w = PacenoteWorld()
    work, _ = setup(tmp_path, monkeypatch, w)
    run(monkeypatch, "--discover")
    run(monkeypatch, "--full")
    sp = tmp_path / "conf" / "state" / "state.json"

    def lost():
        return json.loads(sp.read_text())["lost"]
    assert lost() == {}
    meeting = next((work / "_notion" / "meetings").glob("*.md"))
    real = w.answer

    def denied(code):
        def answer(method, path, body=None, version=None):
            if path == "/data_sources/pmeet/query":
                raise mirror.NotionError(code, "denied")
            return real(method, path, body, version)
        return answer
    for code in (401, 500):
        w.answer = denied(code)
        run(monkeypatch)
        assert lost() == {}, code                                          # not definitive: nothing lost
    w.answer = denied(403)
    run(monkeypatch)
    assert lost() == {dashed(M1): {"db": "meetings", "why": "no_access"}} and meeting.exists()
    w.answer = real
    run(monkeypatch)
    assert lost() == {}                                                    # shared again
    # a plain page unshared: gone from search on a full run, archived and lost; back in search, it leaves
    handbook = w.search.pop(0)
    run(monkeypatch, "--full")
    assert lost() == {dashed(P1): {"db": "pages", "why": "gone"}}
    assert any(P1 in p.name for p in (work / "_notion" / "_archived").rglob("*.md"))
    w.search.insert(0, handbook)
    run(monkeypatch, "--full")
    assert lost() == {}


def test_lost_page_level_and_child_pages(tmp_path, monkeypatch):
    """PB-88: a page whose own read answers 403 is lost; a linked child page that answers 403 does not make its
    readable parent lost; a database that answers 404 loses its pages like a 403."""
    w = PacenoteWorld()
    work, cfgp = setup(tmp_path, monkeypatch, w)
    run(monkeypatch, "--discover")
    cfg = json.loads(cfgp.read_text())
    cfg["child_pages_for"] = ["pages"]
    cfgp.write_text(json.dumps(cfg))
    run(monkeypatch, "--full")
    sp = tmp_path / "conf" / "state" / "state.json"

    def lost():
        return json.loads(sp.read_text())["lost"]
    child = uid(45)
    real = w.answer
    asked = []

    def answer(method, path, body=None, version=None):
        if path.startswith("/pages/%s/markdown" % dashed(M1)):
            raise mirror.NotionError(403, "restricted")
        if path.startswith("/pages/%s/markdown" % dashed(P1)):
            return {"markdown": '<page url="https://www.notion.so/%s">Child</page>' % child, "truncated": False}
        if path.startswith("/pages/%s/markdown" % dashed(child)):
            asked.append(child)
            raise mirror.NotionError(403, "restricted")
        return real(method, path, body, version)
    w.answer = answer
    w.ds["pmeet"][0]["last_edited_time"] = "2099-01-01T00:00:00.000Z"
    w.search[0]["last_edited_time"] = "2099-01-01T00:00:00.000Z"
    run(monkeypatch, "--full")
    assert lost() == {dashed(M1): {"db": "meetings", "why": "no_access_page"}} and asked == [child]
    w.answer = real
    run(monkeypatch, "--full")
    assert lost() == {}

    def gone(method, path, body=None, version=None):
        if path == "/data_sources/pmeet/query":
            raise mirror.NotionError(404, "object_not_found")
        return real(method, path, body, version)
    w.answer = gone
    run(monkeypatch)
    assert lost() == {dashed(M1): {"db": "meetings", "why": "no_access"}}
