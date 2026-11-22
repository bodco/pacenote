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
    # the panel words the ranking itself: a code and the person's own terms, never English text
    assert (interview["pages"][1]["why_code"], interview["pages"][1]["why_terms"][0]) == ("goal", "engineering")
    assert interview["pages"][0]["why_code"] == "start" and neutral["pages"][1]["why_code"] == "section"
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

    waits = []

    def sleep(_):
        waits.append(1)
        if len(waits) == 2:         # Cancel arrives once /about is stored, while the next page is waiting
            cancel.touch()

    report = web.fetch(plan, tmp_path / "pages", getter=get, sleep=sleep, cancel_path=cancel)
    assert hits == ["/robots.txt", "/", "/about"]
    assert {row["status"] for row in report["pages"]} == {"read", "skipped"}
    assert any(row.get("note") == "cancelled" for row in report["pages"])
    assert len(report["sources"]) == 2
    assert all(Path(source["path"]).is_file() for source in report["sources"])


def test_cancel_during_the_crawl_wait_starts_no_request(tmp_path):
    cancel = tmp_path / "cancel"
    hits = []
    get = site_getter(SITE, hits)
    plan = web.plan(BASE, getter=get)
    hits.clear()
    report = web.fetch(plan, tmp_path / "pages", getter=get, sleep=lambda _: cancel.touch(), cancel_path=cancel)
    assert hits == ["/robots.txt", "/"]
    assert len(report["sources"]) == 1 and report["cancelled"] is True
    assert any(row.get("note") == "cancelled" for row in report["pages"])


def test_a_response_finished_after_cancel_is_not_kept(tmp_path):
    flag = []
    hits = []
    get = site_getter(SITE, hits)
    plan = web.plan(BASE, getter=get)

    def getter(url):
        result = get(url)
        if url == BASE + "/about":
            flag.append(1)
        return result

    report = web.fetch(plan, tmp_path / "pages", getter=getter, sleep=lambda _: None, cancel=lambda: bool(flag))
    assert [s["uri"] for s in report["sources"]] == [BASE + "/"]
    assert len(list((tmp_path / "pages").glob("*.txt"))) == 1


def test_a_second_run_never_overwrites_the_first_runs_stored_page(tmp_path):
    pages = {"/p?id=1": page("One", LOREM * 2 + "alpha"), "/p?id=2": page("Two", LOREM * 2 + "bravo")}
    get = site_getter(pages, [])

    def run(path):
        the_plan = {"url": BASE + "/", "limits": {"respect_robots": False, "depth": 1},
                    "pages": [{"url": BASE + path, "planned": True, "depth": 1}]}
        return web.fetch(the_plan, tmp_path, delay=0, getter=get, sleep=lambda _: None)

    first, second = run("/p?id=1")["sources"][0]["path"], run("/p?id=2")["sources"][0]["path"]
    assert first != second
    assert "alpha" in Path(first).read_text() and "bravo" in Path(second).read_text()


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
    assert plan["start"] == {"status": "blocked", "note": "private or non-public address", "why": "private"}
    assert not plan["pages"]


def test_redirect_revalidates_resolved_address(http_fixture, monkeypatch):
    _, hits, _, redirects = http_fixture
    redirects["/redirect"] = "http://127.0.0.1:80/refused"
    resolve = socket.getaddrinfo

    def getaddrinfo(host, port, **kwargs):
        return resolve("10.0.0.1" if port == 80 else host, port, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    plan = web.plan("http://127.0.0.1:8000/redirect", respect_robots=False)
    assert plan["start"] == {"status": "blocked", "note": "private or non-public address", "why": "private"}
    assert hits == ["/redirect"]


def test_redirect_does_not_follow_another_site(http_fixture):
    _, hits, _, redirects = http_fixture
    redirects["/redirect"] = "http://10.0.0.1/refused"
    plan = web.plan("http://127.0.0.1:8000/redirect", respect_robots=False)
    assert plan["start"] == {"status": "error", "note": "redirect leaves the site"}
    assert hits == ["/redirect"]


def test_planning_follows_the_robots_rule_on_a_redirect(http_fixture):
    pages, hits, _, redirects = http_fixture
    pages["/robots.txt"] = "User-agent: *\nDisallow: /hidden\n"
    pages["/hidden"] = page("Hidden", LOREM)
    redirects["/start"] = "http://127.0.0.1:8000/hidden"
    plan = web.plan("http://127.0.0.1:8000/start")
    assert plan["start"] == {"status": "error", "note": "redirect points to a page robots.txt does not allow"}
    assert "/hidden" not in hits


def test_cancel_during_a_redirect_makes_no_further_request(http_fixture):
    pages, hits, _, redirects = http_fixture
    pages["/other"] = page("Other", LOREM)
    redirects["/start"] = "http://127.0.0.1:8000/other"
    with pytest.raises(web.Cancelled):
        web.http_get("http://127.0.0.1:8000/start", stop=lambda: "/start" in hits)
    assert hits == ["/start"]


def test_planning_stops_before_its_next_request(http_fixture):
    _, hits, _, _ = http_fixture
    with pytest.raises(web.Cancelled):
        web.plan("http://127.0.0.1:8000/", stop=lambda: True)
    assert hits == []
    with pytest.raises(web.Cancelled):                  # a stopped robots.txt lookup is not "no robots.txt"
        web.Robots(stop=lambda: True).allowed("http://127.0.0.1:8000/")


def test_call_start_stops_webplan_through_the_cancel_file(http_fixture, tmp_path, capsys):
    _, hits, _, _ = http_fixture
    cancel = tmp_path / "plan.cancel"
    cancel.touch()
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"url": "http://127.0.0.1:8000", "cancel_path": str(cancel)}))
    assert cli.main(["webplan", "--request", str(request)]) == 4
    assert json.loads(capsys.readouterr().out) == {"error": "cancelled"}
    assert hits == []


def test_a_cancelled_request_in_fetch_is_not_an_error_row(tmp_path):
    hits = []
    get = site_getter(SITE, hits)
    plan = web.plan(BASE, getter=get)

    def getter(url, stop=None):
        if url == BASE + "/about":
            raise web.Cancelled()
        return get(url)

    report = web.fetch(plan, tmp_path / "pages", getter=getter, sleep=lambda _: None)
    assert report["cancelled"] is True and not any(row["status"] == "error" for row in report["pages"])
    assert [s["uri"] for s in report["sources"]] == [BASE + "/"]
