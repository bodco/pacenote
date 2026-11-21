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


def test_pb37_routes_files_have_no_long_dash():
    root = Path(__file__).resolve().parents[1]
    for f in (Path(__file__), root / "livetips_m0/sheet/sources.py", root / "livetips_m0/sheet/prompt.py"):
        assert chr(0x2014) not in f.read_text(encoding="utf-8"), f
