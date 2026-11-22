"""PB-37 (G6, subtask B): the stored pages of a researched site feed the build the same way on every route.

Local invented site only (the Lumenfield fixture of test_sheet_web_0100.py), nothing real. Criterion 7: no page of a
researched site is fetched a second time at build time; criterion 8: the Claude routes get Read lines, no WebFetch.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import pytest

from livetips_m0.sheet import build as B
from livetips_m0.sheet import prompt as PR
from livetips_m0.sheet import schema as SCH
from livetips_m0.sheet import sources as SRC
from livetips_m0.sheet import web as W

from test_sheet_web_0100 import SITE, _H, site  # noqa: F401  (the local server fixture)
from test_sheet_pipeline_0100 import _project, _req, quiet

ANSWER = {"goal": "Learn about the company", "blocks": {"overview": [
    {"label": "Team", "text": "Emily Parker is VP Engineering.", "source_ids": ["s3"],
     "locator": {"quote": "VP Engineering"}, "status": "found"}]}}


def _researched(base, tmp_path, max_pages=4):
    """A site read (plan + fetch) as the app does it: pages stored as .txt, sources with path and parent_uri."""
    p = W.plan(base + "/", max_pages=max_pages)
    r = W.fetch(p, tmp_path / "pages", delay=0, sleep=lambda s: None)
    srcs = [s for s in r["sources"] if s["access"] == "ok" and s.get("path")]
    assert len(srcs) >= 3
    return r, srcs


def _hits_of(h, paths):
    return {p: h.hits.count(p) for p in paths}


def test_stored_page_is_read_from_the_file_and_never_fetched(site, tmp_path):
    base, h = site
    _r, srcs = _researched(base, tmp_path)
    h.hits.clear()
    s = dict(srcs[1])
    texts = {}
    SRC.check(s, {}, texts, "local", {})
    assert s["access"] == "ok" and not s.get("fetch") and s["id"] in texts
    assert "Lumenfield" in texts[s["id"]] and s["size_tokens"] > 0
    assert s["parent_uri"] == base + "/" and "page of" in s["why"][0]
    assert h.hits == []                                    # not one request


def test_missing_stored_copy_is_denied_not_refetched(site, tmp_path):
    base, h = site
    _r, srcs = _researched(base, tmp_path)
    h.hits.clear()
    s = dict(srcs[1])
    Path(s["path"]).unlink()
    SRC.check(s, {}, {}, "local", {})
    assert s["access"] == "denied" and s["fix_action"] == "retry" and not s.get("fetch")
    assert h.hits == []


def test_old_web_source_without_a_copy_is_still_fetched_at_build(site):
    base, _h = site
    s = {"id": "s1", "type": "web", "uri": base + "/about", "title": "About"}
    texts = {}
    SRC.check(s, {}, texts, "local", {})
    assert s["access"] == "ok" and s["fetch"] is True and s["access_reason"] == "read when the sheet is built"
    assert s["size_tokens"] == 1500 and not texts


@pytest.mark.parametrize("route", ["fake", "local"])
def test_build_reads_each_page_zero_times_on_packed_routes(site, tmp_path, route):
    base, h = site
    r, srcs = _researched(base, tmp_path)
    root, a, b = _project(tmp_path)
    web = [dict(s, id=f"s{i + 3}") for i, s in enumerate(srcs)]
    h.hits.clear()
    asked = []
    req = _req(root, a, b, route=route, sources=[{"id": "s1", "path": str(a)}, {"id": "s2", "path": str(b)}] + web)

    def responder(system, user):
        asked.append(user)
        return json.dumps(ANSWER)

    res = B.build(req, emit=quiet, responder=responder)
    assert res.get("done"), res
    assert h.hits == [], h.hits                            # criterion 7: the server saw nothing at build time
    user = "\n".join(asked)
    assert "VP Engineering" in user                        # the stored text reached the model


def test_old_web_source_is_fetched_once_on_a_packed_route(site, tmp_path):
    base, h = site
    root, a, b = _project(tmp_path)
    req = _req(root, a, b, route="local",
               sources=[{"id": "s1", "path": str(a)}, {"id": "s2", "path": str(b)},
                        {"id": "s3", "type": "web", "uri": base + "/team"}])
    res = B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER))
    assert res.get("done"), res
    assert h.hits.count("/team") == 1


def test_claude_routes_get_read_lines_and_no_webfetch(site, tmp_path, monkeypatch):
    base, h = site
    _r, srcs = _researched(base, tmp_path)
    web = [dict(s, id=f"s{i + 3}") for i, s in enumerate(srcs)]
    old = {"id": "s9", "type": "web", "uri": base + "/blog", "title": "Blog"}
    texts = {}
    for s in web + [old]:
        SRC.check(s, {}, texts, "code", {})
    usable = [s for s in web + [old] if s["id"] in texts or s.get("fetch")]
    files = B.claude_files(usable, texts, str(tmp_path / "cache"))
    assert set(files) == {s["id"] for s in web}
    prompt = PR.agent_prompt({}, "- overview", usable, files, texts, "en", ["en"])
    for s in web:
        assert f"- {s['id']} (web_page" in prompt and f"Read {s['path']}" in prompt and s["uri"] in prompt
        assert f"WebFetch {s['uri']} " not in prompt
    assert f"WebFetch {old['uri']} (this page only)" in prompt          # an old source keeps its line
    # criterion 8: the host allowlist is made from fetch sources only: researched pages add nothing
    hosts = {s["uri"] for s in usable if s.get("fetch") and s.get("uri") and not s.get("auto")}
    assert hosts == {old["uri"]}


def test_build_on_a_claude_route_has_no_web_host_for_stored_pages(site, tmp_path, monkeypatch):
    base, h = site
    _r, srcs = _researched(base, tmp_path)
    root, a, b = _project(tmp_path)
    web = [dict(s, id=f"s{i + 3}") for i, s in enumerate(srcs)]
    seen = {}

    def fake_run(req, prompt, files, hosts, emit, sources):
        seen.update(prompt=prompt, files=files, hosts=hosts)
        return json.dumps(ANSWER), {"read": list(files.values()), "fetched": [], "turns": 1}

    monkeypatch.setattr(B, "run_claude", fake_run)
    monkeypatch.setattr(B, "find_claude", lambda *a, **k: "/usr/bin/claude")
    h.hits.clear()
    req = _req(root, a, b, route="code", sources=[{"id": "s1", "path": str(a)}, {"id": "s2", "path": str(b)}] + web)
    res = B.build(req, emit=quiet)
    assert res.get("done"), res
    assert seen["hosts"] == [] and "WebFetch" not in seen["prompt"]
    assert all(f"Read {s['path']}" in seen["prompt"] for s in web)
    assert h.hits == []


def test_schema_accepts_parent_uri_and_old_sources_without_it():
    base = {"id": "s1", "n": 1, "type": "web_page", "title": "T", "access": "ok", "used": "full"}
    for extra in ({}, {"parent_uri": "https://example-company.com/", "uri": "https://example-company.com/team"}):
        v = SCH._V()
        SCH._source(v, dict(base, **extra), "sources[0]")
        assert v.errors == []
    v = SCH._V()
    SCH._source(v, dict(base, parent_uri=5), "sources[0]")
    assert v.errors


def _stored(tmp_path, name, title, url, body):
    p = tmp_path / name
    p.write_text(f"{title}\n{url}\n\n{body}\n", encoding="utf-8")
    return p


def test_empty_page_is_kept_in_the_report_but_not_given_to_the_model(tmp_path):
    url = "https://lumenfield.example/soon"
    p = _stored(tmp_path, "soon.txt", "Soon", url, "Coming soon")
    for flagged in (True, False):                          # from fetch (explicit status) and from the copy alone
        s = {"id": "s3", "type": "web_page", "uri": url, "title": "Soon", "path": str(p)}
        if flagged:
            s["empty"] = True
        texts = {}
        SRC.check(s, {}, texts, "local", {})
        assert s["id"] not in texts and s["size_tokens"] == 0 and s["empty"] is True
        assert s["access"] == "ok" and "empty" in s["access_reason"]
        assert B.claude_files([s], texts, str(tmp_path / "cache")) == {}      # no Read line either


def test_build_skips_an_empty_stored_page_and_lists_it(tmp_path):
    root, a, b = _project(tmp_path)
    url = "https://lumenfield.example/soon"
    p = _stored(tmp_path, "soon.txt", "Soon", url, "")
    asked = []

    def responder(system, user):
        asked.append(user)
        return json.dumps(ANSWER)

    req = _req(root, a, b, route="local", sources=[
        {"id": "s1", "path": str(a)}, {"id": "s2", "path": str(b)},
        {"id": "s3", "type": "web_page", "uri": url, "title": "Soon", "path": str(p), "empty": True}])
    res = B.build(req, emit=quiet, responder=responder)
    assert res.get("done"), res
    assert url not in "\n".join(asked)
    sheet = json.loads(Path(res["sheet"]).read_text(encoding="utf-8"))
    row = [s for s in sheet["sources"] if s["id"] == "s3"][0]
    assert row["used"] == "skipped" and "empty" in row["reason"]


def _web_req(tmp_path, with_page=True):
    root, a, b = _project(tmp_path)
    url = "https://lumenfield.example/team"
    p = _stored(tmp_path, "team.txt", "Team", url, "Emily Parker is VP Engineering at Lumenfield. " * 5)
    srcs = [{"id": "s1", "path": str(a)}, {"id": "s2", "path": str(b)}]
    if with_page:
        srcs.append({"id": "s3", "type": "web_page", "uri": url, "title": "Team", "path": str(p)})
    return _req(root, a, b, route="local", sources=srcs)


def _facts(sheet):
    return [f for b in sheet["blocks"] for f in b.get("facts") or []]


def test_facts_of_a_call_given_researched_pages_are_marked_whatever_they_cite(tmp_path):
    # round 8: the model may cite a file (or nothing) for a fact it read on the page; the mark does not rely on citations
    ans = {"blocks": {"overview": [
        {"label": "Team", "text": "Emily Parker is VP Engineering.", "source_ids": ["s1"], "status": "found"},
        {"label": "Size", "text": "About forty people.", "source_ids": [], "status": "found"}]}}
    res = B.build(_web_req(tmp_path), emit=quiet, responder=lambda s, u: json.dumps(ans))
    assert res.get("done"), res
    sheet = json.loads(Path(res["sheet"]).read_text(encoding="utf-8"))
    model = {f["text"]: f for f in _facts(sheet) if f.get("origin") == "model" and not f["id"].startswith("f-header")}
    assert model["Emily Parker is VP Engineering."].get("from_web") is True
    assert model["About forty people."].get("from_web") is True
    assert SCH.validate(sheet) == []


def test_facts_of_a_call_without_researched_pages_are_not_marked(tmp_path):
    ans = {"blocks": {"overview": [{"label": "Team", "text": "Emily Parker is VP Engineering.", "source_ids": ["s1"]}]}}
    res = B.build(_web_req(tmp_path, with_page=False), emit=quiet, responder=lambda s, u: json.dumps(ans))
    sheet = json.loads(Path(res["sheet"]).read_text(encoding="utf-8"))
    assert not any(f.get("from_web") for f in _facts(sheet))


def test_merge_of_passes_keeps_the_researched_mark():
    a = {"overview": [{"label": "T", "text": "Same fact", "source_ids": ["s1"], "status": "found"}]}
    b = {"overview": [{"label": "T", "text": "Same fact", "source_ids": ["s3"], "status": "found", "from_web": True}]}
    merged = B.merge_passes([a, b])["overview"]
    assert len(merged) == 1 and merged[0]["from_web"] is True


def test_edit_remove_the_page_and_refresh_keeps_the_page_record_and_the_mark(tmp_path):
    # round 8: an edited researched fact comes back with its citation; its web_page record must come back with it
    req = _web_req(tmp_path)
    assert B.build(req, emit=quiet, responder=lambda s, u: json.dumps(ANSWER)).get("done")
    path = Path(req["base"] + ".sheet.json")
    sheet = json.loads(path.read_text(encoding="utf-8"))
    f = next(x for x in _facts(sheet) if x["text"] == "Emily Parker is VP Engineering.")
    assert f["source_ids"] == ["s3"] and f["from_web"] is True
    f.update(text="Emily Parker leads engineering.", status="edited")
    path.write_text(json.dumps(sheet), encoding="utf-8")
    req2 = dict(req, sources=[s for s in req["sources"] if s["id"] != "s3"])
    second = {"blocks": {"overview": [{"label": "Sales", "text": "Mike Stone runs sales.", "source_ids": ["s1"]}]}}
    res = B.build(req2, refresh=True, emit=quiet, responder=lambda s, u: json.dumps(second))
    assert res.get("done"), res
    new = json.loads(path.read_text(encoding="utf-8"))
    kept = next(x for x in _facts(new) if x["text"] == "Emily Parker leads engineering.")
    assert kept["source_ids"] == ["s3"] and kept["from_web"] is True
    page = [s for s in new["sources"] if s["id"] == "s3"]
    assert page and page[0]["type"] == "web_page"
    fresh = next(x for x in _facts(new) if x["text"] == "Mike Stone runs sales.")
    assert not fresh.get("from_web")                    # this call had no researched page
    assert SCH.validate(new) == []


def test_two_researches_in_one_folder_keep_their_own_copies(tmp_path):
    out = tmp_path / "pages"
    bodies = {"/a/b": "First page about Emily Parker, VP Engineering at Lumenfield. " * 5,
              "/a-b": "Second page about Mike Stone, head of sales at Lumenfield. " * 5}

    def getter(url, **kw):
        path = url.split("lumenfield.example", 1)[1]
        return 200, url, "text/html", "<html><title>T</title><body><main><p>" + bodies[path] + "</p></main></body></html>"

    def one(path, n, prefix):
        url = "https://lumenfield.example" + path
        plan = {"url": url, "pages": [{"url": url, "path": path, "depth": 0, "planned": True}],
                "limits": {"depth": 0, "max_pages": 1, "respect_robots": False}}
        return W.fetch(plan, out, delay=0, sleep=lambda s: None, getter=getter, start_n=n, id_prefix=prefix)["sources"][0]

    s1, s2 = one("/a/b", 1, "w"), one("/a-b", 1, "v")
    assert s1["path"] != s2["path"]
    t1, t2 = {}, {}
    SRC.check(dict(s1), {}, t1, "local", {})
    SRC.check(dict(s2), {}, t2, "local", {})
    assert "Emily Parker" in "".join(t1.values()) and "Mike Stone" not in "".join(t1.values())
    assert "Mike Stone" in "".join(t2.values()) and "Emily Parker" not in "".join(t2.values())


def test_pb37_routes_files_have_no_long_dash():
    root = Path(__file__).resolve().parents[1]
    for f in (Path(__file__), root / "livetips_m0/sheet/sources.py", root / "livetips_m0/sheet/prompt.py"):
        assert chr(0x2014) not in f.read_text(encoding="utf-8"), f
