"""PB-37 plan contract tests using the Lumenfield fixture."""
import io
import json
import socket
from pathlib import Path

import pytest

from livetips_m0.sheet import __main__ as cli
from livetips_m0.sheet import web
from test_sheet_web_0100 import LOREM, SITE, page

BASE = "https://lumenfield.example"


def site_getter(pages, hits):
    def get(url):
        path = url.removeprefix(BASE)
        hits.append(path)
        return (200, url, "text/html", pages[path]) if path in pages else (404, url, "", "")
    return get


def planned_paths(plan):
    return [row["path"] for row in plan["pages"] if row["planned"]]


def test_plan_goal_order_and_estimates():
    links = [("/pricing", "Pricing"), ("/about", "About"), ("/team", "Engineering team"),
             ("/careers", "Вакансії"), ("/services", "Services"), ("/cases", "Cases"), ("/blog", "Blog")]
    pages = dict(SITE, **{"/": page("Lumenfield", LOREM, links)})
    hits = []
    get = site_getter(pages, hits)
    neutral = web.plan(BASE, getter=get, call_type="interview")
    assert planned_paths(neutral) == ["/", "/about", "/team", "/services", "/pricing", "/cases", "/careers", "/blog"]
    sales = web.plan(BASE, getter=get, goal="Sales intro", call_type="sales")
    assert planned_paths(sales)[:5] == ["/", "/about", "/team", "/services", "/cases"]
    interview = web.plan(BASE, getter=get, goal="Engineering team", call_type="interview", audience="external")
    assert planned_paths(interview)[:3] == ["/", "/team", "/careers"]
    assert interview["pages"][1]["score"] > interview["pages"][2]["score"] > 0
    assert "engineering" in interview["pages"][1]["why_ranked"]
    ukrainian = web.plan(BASE, getter=get, goal="Вакансії", call_type="interview", route="local")
    assert planned_paths(ukrainian)[1] == "/careers"
    assert all(row["est_tokens"] == 1500 for row in interview["pages"])
    assert set(hits) == {"/", "/robots.txt"}


@pytest.mark.parametrize(("requested", "expected"), [(None, 15), (22, 22), (40, 30)])
def test_plan_and_fetch_budget(requested, expected):
    paths = [f"/page-{n}" for n in range(40)]
    pages = {path: page(path, f"{path}: {LOREM}", [(path + "/child", "Child")]) for path in paths}
    pages["/"] = page("Lumenfield", LOREM, [(path, path) for path in paths])
    hits = []
    get = site_getter(pages, hits)
    kwargs = {} if requested is None else {"max_pages": requested}
    plan = web.plan(BASE, getter=get, **kwargs)
    assert len(planned_paths(plan)) == expected
    assert sum(row["note"] == "limit" for row in plan["pages"]) == 41 - expected
    hits.clear()
    report = web.fetch(plan, getter=get, sleep=lambda _: None)
    assert report["summary"]["read"] == expected
    assert len([path for path in hits if path != "/robots.txt"]) == expected
    assert all(row["note"] == "limit" for row in report["pages"] if row["depth"] == 2)


def test_fetch_waits_between_pages_and_checks_robots_again():
    hits = []
    pages = dict(SITE)
    get = site_getter(pages, hits)
    plan = web.plan(BASE, getter=get, max_pages=3)
    pages["/robots.txt"] += "Disallow: /about-us\n"
    hits.clear()
    web.fetch(plan, getter=get, sleep=lambda seconds: hits.append(seconds))
    assert hits == ["/robots.txt", "/", 1.0, "/about"]


def test_cancel_keeps_completed_pages(tmp_path):
    cancel = tmp_path / "cancel"
    hits = []
    get = site_getter(SITE, hits)
    plan = web.plan(BASE, getter=get)
    hits.clear()

    def getter(url):
        result = get(url)
        if url == BASE + "/about":
            cancel.touch()
        return result

    report = web.fetch(plan, tmp_path / "pages", getter=getter, sleep=lambda _: None, cancel_path=cancel)
    assert hits == ["/robots.txt", "/", "/about"]
    assert {row["status"] for row in report["pages"]} == {"read", "skipped"}
    assert any(row.get("note") == "cancelled" for row in report["pages"])
    assert len(report["sources"]) == 2
    assert all(Path(source["path"]).is_file() for source in report["sources"])


@pytest.fixture
def http_fixture(monkeypatch):
    """Run urllib's HTTP and redirect handlers with fixture responses and no network."""
    pages = dict(SITE)
    hits, agents = [], []
    redirects = {}

    class FixtureSocket:
        def settimeout(self, timeout):
            pass

        def setsockopt(self, *args):
            pass

        def connect(self, address):
            assert address == ("127.0.0.1", 8000)

        def sendall(self, request):
            lines = request.decode().splitlines()
            path = lines[0].split()[1]
            hits.append(path)
            agents.append(next(line for line in lines if line.startswith("User-Agent:")))
            body = pages.get(path, "").encode()
            if path in redirects:
                head = f"HTTP/1.1 302 Found\r\nLocation: {redirects[path]}\r\n"
            else:
                status = "200 OK" if path in pages else "404 Not Found"
                head = f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
            self.response = io.BytesIO(head.encode() + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

        def makefile(self, *args):
            return self.response

        def close(self):
            pass

    monkeypatch.setenv("PACENOTE_TEST_WEB_LOOPBACK", "1")
    monkeypatch.setattr(socket, "socket", lambda *args: FixtureSocket())
    return pages, hits, agents, redirects


def test_cli_contract_and_fixture_contacts(http_fixture, tmp_path, capsys):
    pages, hits, agents, _ = http_fixture
    base = "http://127.0.0.1:8000"
    pages["/team"] = page("Team", LOREM + " mike@lumenfield.example +1 512 555 0142")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"url": base, "goal": "Team", "call_type": "interview",
                                   "audience": "external", "route": "local"}))
    assert cli.main(["webplan", "--request", str(request)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert planned_paths(plan)[1] == "/team"
    assert hits == ["/robots.txt", "/"]
    cancel = tmp_path / "cancel"
    args = {"plan": plan, "delay": 0, "start_n": 4, "out_dir": str(tmp_path / "pages"), "cancel_path": str(cancel)}
    request.write_text(json.dumps(args))
    assert cli.main(["webfetch", "--request", str(request)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sources"][0]["n"] == 4
    assert report["suggested"]
    team = next(row for row in report["sources"] if row["uri"].endswith("/team"))
    assert team["parent_uri"] == plan["url"] and team["checked_at"] and team["why"]
    stored = Path(team["path"]).read_text()
    assert "[email removed]" in stored and "[phone removed]" in stored
    assert "/contact" not in hits and "/private/secret" not in hits
    assert all("Pacenote" in agent for agent in agents)
    cancel.touch()
    hits.clear()
    assert cli.main(["webfetch", "--request", str(request)]) == 0
    assert not json.loads(capsys.readouterr().out)["sources"]
    assert not hits


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:192.168.1.2"])
def test_private_addresses_are_refused(address, monkeypatch):
    monkeypatch.delenv("PACENOTE_TEST_WEB_LOOPBACK", raising=False)
    host = f"[{address}]" if ":" in address else address
    plan = web.plan(f"http://{host}")
    assert plan["start"] == {"status": "blocked", "note": "private or non-public address"}
    assert not plan["pages"]


def test_redirect_revalidates_resolved_address(http_fixture, monkeypatch):
    _, hits, _, redirects = http_fixture
    redirects["/redirect"] = "http://127.0.0.1:80/refused"
    resolve = socket.getaddrinfo

    def getaddrinfo(host, port, **kwargs):
        return resolve("10.0.0.1" if port == 80 else host, port, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    plan = web.plan("http://127.0.0.1:8000/redirect", respect_robots=False)
    assert plan["start"] == {"status": "blocked", "note": "private or non-public address"}
    assert hits == ["/redirect"]


def test_redirect_does_not_follow_another_site(http_fixture):
    _, hits, _, redirects = http_fixture
    redirects["/redirect"] = "http://10.0.0.1/refused"
    plan = web.plan("http://127.0.0.1:8000/redirect", respect_robots=False)
    assert plan["start"] == {"status": "error", "note": "redirect leaves the site"}
    assert hits == ["/redirect"]
