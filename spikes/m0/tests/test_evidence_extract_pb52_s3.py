"""PB-52 G2 S3: LLM extraction, validation, routing, ceiling (docs/specs/PB-52-g2-evidence-store.md section 17, S3).

Every test uses invented data and a fake provider: no model, network or embed request leaves the Mac. The two
abort tests start real child processes against a fake Ollama server on loopback and a fake `codex` script; no app,
no GUI program and no real CLI is started (L33).
"""
import http.server
import io
import json
import os
import re
import socketserver
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from livetips_m0 import call_layout as CL  # noqa: E402
from livetips_m0 import routing as R  # noqa: E402
from livetips_m0.evidence import extract as X  # noqa: E402
from livetips_m0.evidence import ingest as IG  # noqa: E402
from livetips_m0.evidence import search as SE  # noqa: E402
from livetips_m0.evidence import segments as SG  # noqa: E402
from livetips_m0.evidence import store as S  # noqa: E402
from livetips_m0.evidence import worker as W  # noqa: E402

OLD = time.time() - 30 * 86400
RULES = X.load_rules()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_SUPPORT_DIR", str(tmp_path / "support"))
    monkeypatch.setenv("LT_EVIDENCE", "1")
    state = tmp_path / "state" / "ai-routes.json"
    state.parent.mkdir()
    monkeypatch.setenv("LT_AI_STATE", str(state))
    monkeypatch.delenv("LT_AI_ROUTE", raising=False)
    monkeypatch.delenv("LT_PROVIDER_CAPS", raising=False)
    monkeypatch.delenv("LT_EVIDENCE_LIMITS", raising=False)     # the app's settings (PB-88), read by the worker
    monkeypatch.delenv("LT_LOCAL_FALLBACK", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    return root


def put(root, rel, text, mtime=None):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    t = OLD if mtime is None else mtime
    os.utime(p, (t, t))
    return p


def open_store(root, name="p-1"):
    return S.Store.open(uid=name, root=str(root))


def ingest(st, root, **kw):
    kw.setdefault("quiet_s", 0)
    kw.setdefault("voices", {})
    return IG.ingest(st, str(root), **kw)


# ---------------------------------------------------------------- the fake provider

PART = re.compile(r"^(\d+) (.*)$")


def part_lines(user: str):
    """The numbered lines of the part (after the marker), as (line number, text)."""
    body = user.split("The part, with line numbers:\n", 1)[1]
    out = []
    for ln in body.split("\n"):
        m = PART.match(ln)
        if m and m.group(2).strip():
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def honest(user: str) -> dict:
    """One topic entity and one statement claim per line of three words or more, quoting the line."""
    ents, claims = [], []
    for n, text in part_lines(user):
        if len(text.split()) < 3 or text.startswith("#"):
            continue
        ref = "n{}".format(n)
        ents.append({"ref": ref, "type": "topic", "name": " ".join(text.split()[:3]), "aliases": [], "quote": text[:300],
                     "lines": [n, n]})
        claims.append({"subject": ref, "predicate": "said", "value": text[:60], "modality": "statement", "said_by": None,
                       "due": None, "quote": text[:300], "lines": [n, n], "confidence": 0.9})
    return {"entities": ents, "claims": claims, "relations": []}


class FakeHandle:
    def __init__(self, tr, req):
        self.tr, self.req = tr, req
        self.cancelled = False
        self.ans = None
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        end = time.monotonic() + self.tr.delay
        while time.monotonic() < end and not self.cancelled:
            time.sleep(0.01)
        if not self.cancelled:
            self.ans = self.tr.answer(self.req)

    def done(self):
        return not self.t.is_alive()

    def cancel(self):
        self.cancelled = True
        self.tr.cancelled += 1

    def result(self):
        self.t.join()
        return {"cancelled": True} if self.cancelled else self.ans


SUMMARY_MARK = "write a short dated summary"     # the S4 topic summary prefix (rules/evidence.yaml > summary)


class FakeTransport:
    """Counts requests; `responder(req) -> answer dict` (text, tokens, stop reason, error). S4: a topic summary request
    is kept apart in `summaries` and answered by `summarizer` (default: no sentence), so the extraction counts here
    stay the extraction's."""

    def __init__(self, responder=None, delay=0.0, out_tokens=None, summarizer=None):
        self.responder = responder or (lambda req: {"text": json.dumps(honest(req["user"]))})
        self.summarizer = summarizer or (lambda req: {"text": json.dumps({"sentences": []})})
        self.delay = delay
        self.out_tokens = out_tokens
        self.requests = []
        self.summaries = []
        self.cancelled = 0

    def answer(self, req):
        a = dict((self.summarizer if SUMMARY_MARK in req["system"] else self.responder)(req))
        a.setdefault("in_tokens", 1000)
        a.setdefault("out_tokens", self.out_tokens if self.out_tokens is not None else 300)
        a.setdefault("model", req["model"])
        return a

    def start(self, req):
        (self.summaries if SUMMARY_MARK in req["system"] else self.requests).append(req)
        return FakeHandle(self, req)


def run_pass(st, root, tr, route="api", **settings):
    settings.setdefault("topics", "lazy")       # S3 tests measure extraction; S4 tests ask for eager summaries
    settings.setdefault("poll_s", 0.05)
    settings.setdefault("backoff0_s", 0)
    return W.extract_pass(st, str(root), dict(settings, route=route), tr, sleep=lambda s: None)


# ---------------------------------------------------------------- validator (section 8.4)

def two_clients(root):
    put(root, "docs/acme/plan.md", "# Plan\nAcme rollout starts in March.\nThe pilot covers 40 seats.\n")
    put(root, "docs/globex/notes.md", "# Notes\nGlobex wants the Halberd module.\nHalberd ships in May.\n")
    put(root, "live/2026-11-04-1000-acme-weekly.md", "Speaker 1: hello\n")
    put(root, "live/2026-11-05-1000-globex-weekly.md", "Speaker 1: hello\n")
    put(root, "pacenote/shared.md", "# Shared\nThe team meets on Mondays.\nWe will try to confirm the venue.\n")


def view_of(st, uri, holding=""):
    """The view of the current revision's segment holding `holding` (the first segment without it)."""
    rv = st.one("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri=? AND rv.superseded_at IS"
                " NULL", (uri,))[0]
    for seg in SG.of_revision(st, rv):
        v = X.segment_view(st, rv, seg)
        if not holding or any(holding in sn["text"] for sn in v["snippets"]):
            return v
    raise AssertionError(holding)


def line_in(view, text):
    """The file line of the first snippet line of the view holding `text`."""
    for sn in view["snippets"]:
        for i, t in enumerate(sn["text"].split("\n")):
            if text in t:
                return sn["lines"][0] + i
    raise AssertionError(text)


def test_validator_drops_and_downgrades(env):
    root = env
    two_clients(root)
    put(root, "live/2026-11-04-1000-acme-weekly.notes.md", "## Decisions\nWe agreed to ship the pilot, maybe in March.\n")
    st = open_store(root)
    ingest(st, root)
    labels = X.project_labels(str(root))
    assert set(labels) >= {"acme", "globex"}
    v = view_of(st, "docs/acme/plan.md", "Acme rollout")
    a = line_in(v, "Acme rollout")
    good = {"ref": "n1", "type": "topic", "name": "Acme rollout", "quote": "Acme rollout starts in March", "lines": [a, a]}
    obj = {"entities": [good,
                        dict(good, ref="n2", quote="Acme rollout starts in April"),                  # not verbatim
                        dict(good, ref="n3", lines=[a + 1, a + 1]),                                  # wrong line range
                        dict(good, ref="n4", type="spaceship")],                                     # unknown enum
           "claims": [{"subject": "n1", "predicate": "status", "value": "x", "modality": "certain", "quote":
                       "Acme rollout starts in March", "lines": [a, a]},                             # unknown modality
                      {"subject": "n1", "predicate": "status", "value": "Globex", "modality": "statement",
                       "quote": "Acme rollout starts in March", "lines": [a, a]}],
           "relations": [{"from": "n1", "rel": "supersedes", "to": "n1", "modality": "statement",
                          "quote": "Acme rollout starts in March", "lines": [a, a]}]}            # PM-only relation
    items, c = X.validate(st, v, obj, RULES, [], str(root), labels)
    assert [e["ref"] for e in items["entities"]] == ["n1"]
    assert c["reasons"]["quote"] == 2 and c["reasons"]["enum"] == 3
    assert len(items["claims"]) == 1 and items["relations"] == []

    # a cross-client quote: an Acme segment quoting a line that names Globex (the shared file's line, inserted)
    put(root, "docs/acme/plan.md", "# Plan\nAcme rollout starts in March.\nGlobex asked about it too.\n", OLD + 100)
    ingest(st, root)
    v = view_of(st, "docs/acme/plan.md", "Globex asked")
    b = line_in(v, "Globex asked")
    items, c = X.validate(st, v, {"entities": [dict(good, quote="Globex asked about it too", lines=[b, b])],
                                  "claims": [], "relations": []}, RULES, [], str(root), labels)
    assert items["entities"] == [] and c["reasons"] == dict(client=1)

    # hedge in the middle of a quote under confirmation -> proposal; a derived confirmation -> statement
    v = view_of(st, "pacenote/shared.md", "try")
    n = line_in(v, "try")
    obj = {"entities": [{"ref": "n1", "type": "topic", "name": "venue", "quote": "confirm the venue", "lines": [n, n]}],
           "claims": [{"subject": "n1", "predicate": "status", "value": "confirmed", "modality": "confirmation",
                       "quote": "We will try to confirm the venue", "lines": [n, n]}], "relations": []}
    items, c = X.validate(st, v, obj, RULES, [], str(root), labels)
    assert items["claims"][0]["modality"] == "proposal" and c["downgraded"] == 1
    nv = view_of(st, "live/2026-11-04-1000-acme-weekly.notes.md", "agreed")
    assert nv["origin"] == "derived"
    m = line_in(nv, "agreed")
    obj = {"entities": [{"ref": "n1", "type": "decision", "name": "ship the pilot", "quote": "ship the pilot",
                         "lines": [m, m]}],
           "claims": [{"subject": "n1", "predicate": "decided", "value": "ship", "modality": "confirmation",
                       "quote": "We agreed to ship the pilot", "lines": [m, m]}], "relations": []}
    items, _c = X.validate(st, nv, obj, RULES, [], str(root), labels)
    assert items["claims"][0]["modality"] == "statement"         # derived never confirms
    # "maybe" inside the quote of a primary confirmation
    obj["claims"][0]["quote"] = "We agreed to ship the pilot, maybe in March"
    items, _c = X.validate(st, dict(nv, origin="primary"), obj, RULES, [], str(root), labels)
    assert items["claims"][0]["modality"] == "proposal"


def test_validator_override_field_and_rejected_pair(env):
    root = env
    put(root, "docs/plan.md", "# Plan\nItem 13 blocks the SSO integration.\n")
    st = open_store(root)
    ingest(st, root)
    v = view_of(st, "docs/plan.md", "Item 13")
    n = line_in(v, "Item 13")
    a, b = S.entity_llm_id("task", "Item 13"), S.entity_llm_id("system", "SSO integration")
    with st.conn:
        st.conn.execute("INSERT INTO pm_override(target, target_key, field, value, at, via) VALUES"
                        " ('entity', ?, 'status', 'open', '2026-11-01T00:00:00Z', 'app')", (a,))
        st.conn.execute("INSERT INTO pm_override(target, target_key, field, value, at, via) VALUES"
                        " ('edge', ?, 'reject', NULL, '2026-11-01T00:00:00Z', 'app')", ("{}|same_as|{}|statement".format(a, b),))
    q = "Item 13 blocks the SSO integration"
    obj = {"entities": [{"ref": "n1", "type": "task", "name": "Item 13", "quote": q, "lines": [n, n]},
                        {"ref": "n2", "type": "system", "name": "SSO integration", "quote": q, "lines": [n, n]}],
           "claims": [{"subject": "n1", "predicate": "status", "value": "blocked", "modality": "statement", "quote": q,
                       "lines": [n, n]},
                      {"subject": "n1", "predicate": "due", "value": "Friday", "modality": "statement", "quote": q,
                       "lines": [n, n]}],
           "relations": [{"from": "n1", "rel": "same_as", "to": "n2", "modality": "statement", "quote": q, "lines": [n, n]},
                         {"from": "n1", "rel": "blocks", "to": "n2", "modality": "statement", "quote": q, "lines": [n, n]}]}
    items, c = X.validate(st, v, obj, RULES, [], str(root), [])
    assert [x["predicate"] for x in items["claims"]] == ["due"]          # the status field is the PM's
    assert [x["rel"] for x in items["relations"]] == ["blocks"]          # the rejected pair is not re-suggested
    assert c["reasons"]["override"] == 2


# ---------------------------------------------------------------- the catalog (section 8.3)

def _seed_entity(st, root, uri, name, etype="system"):
    v = view_of(st, uri, name)
    n = line_in(v, name)
    line = next(ln for sn in v["snippets"] for ln in sn["text"].split("\n") if name in ln)
    items, _ = X.validate(st, v, {"entities": [{"ref": "n1", "type": etype, "name": name, "aliases": [name.lower() + " x"],
                                                "quote": line.strip(), "lines": [n, n]}],
                                  "claims": [{"subject": "n1", "predicate": "said", "value": "v", "modality": "statement",
                                              "quote": line.strip(), "lines": [n, n]}], "relations": []},
                          RULES, [], str(root), X.project_labels(str(root)))
    S.apply_extraction(st, v["revision_id"], items, mark_done=False)
    return S.entity_llm_id(etype, name)


def test_catalog_holds_no_entity_or_alias_of_another_client(env):
    root = env
    two_clients(root)
    put(root, "docs/acme/more.md", "# More\nAcme asks about Halberd and the Mondays meeting.\n")
    put(root, "pacenote/topics.md", "# Topics\nHalberd and the Mondays meeting.\n")
    st = open_store(root)
    ingest(st, root)
    halberd = _seed_entity(st, root, "docs/globex/notes.md", "Halberd")          # only in Globex's file
    mondays = _seed_entity(st, root, "pacenote/shared.md", "Mondays")            # shared
    rollout = _seed_entity(st, root, "docs/acme/plan.md", "Acme rollout")        # only in Acme's file
    labels = X.project_labels(str(root))
    acme = X.catalog(st, view_of(st, "docs/acme/more.md", "Halberd"), str(root), labels)
    ids = {c["id"] for c in acme}
    assert mondays in ids and halberd not in ids
    assert not any("halberd" in a for c in acme for a in c["aliases"] + [c["name"].lower()])
    shared = X.catalog(st, view_of(st, "pacenote/topics.md", "Mondays"), str(root), labels)
    sids = {c["id"] for c in shared}
    assert mondays in sids and halberd not in sids and rollout not in sids


# ---------------------------------------------------------------- caps, repair, failure

def big_file(root, rel="docs/big.md", lines=30):
    put(root, rel, "# Big\n" + "\n\n".join("Line {} of the big plan names the milestone M{}.".format(i, i)
                                            for i in range(lines)) + "\n")


def test_oversized_invalid_and_failing_answers(env):
    root = env
    big_file(root)
    st = open_store(root)
    ingest(st, root)
    # oversized: 100 entities and 100 claims in one answer -> caps 40 and 60 hold
    def oversized(req):
        ls = part_lines(req["user"])
        n, t = ls[0]
        ents = [{"ref": "n{}".format(i), "type": "topic", "name": "M{}".format(i), "quote": t, "lines": [n, n]}
                for i in range(100)]
        cl = [{"subject": "n0", "predicate": "said", "value": str(i), "modality": "statement", "quote": t, "lines": [n, n]}
              for i in range(100)]
        return {"text": json.dumps({"entities": ents, "claims": cl, "relations": []})}
    tr = FakeTransport(oversized)
    res = run_pass(st, root, tr)
    assert res["status"] == "done"
    assert all(r["max_tokens"] == RULES["max_output_tokens"] for r in tr.requests)
    items, c = X.validate(st, view_of(st, "docs/big.md", "Line 0"), json.loads(oversized(tr.requests[0])["text"]), RULES, [],
                          str(root), [])
    assert len(items["entities"]) == 40 and len(items["claims"]) == 60 and c["reasons"]["cap"] == 100

    # invalid twice: one repair retry, then the segment is invalid (retried only on a rules or route change)
    put(root, "docs/big.md", "# Big\nA new plan line names the milestone Z.\n")
    ingest(st, root)
    tr = FakeTransport(lambda req: {"text": "not json at all"})
    res = run_pass(st, root, tr)
    assert len(tr.requests) == 2 and "not valid" in tr.requests[1]["user"]
    item = st.one("SELECT status FROM run_item WHERE run_id=?", (res["id"],))
    assert item["status"] == "invalid"

    # three route errors in a row -> failed
    put(root, "docs/a.md", "# A\nAlpha line one is here.\n## B\nBeta line two is here.\n## C\nGamma line three is here.\n"
                           "## D\nDelta line four is here.\n")
    ingest(st, root)
    tr = FakeTransport(lambda req: {"text": "", "error": "OpenAI: HTTP 500"})
    res = run_pass(st, root, tr, route="openai")
    assert res["status"] == "failed" and len(tr.requests) == 3


def test_cut_answer_is_split_and_cached_under_the_parent(env):
    root = env
    big_file(root, lines=60)
    st = open_store(root)
    ingest(st, root)

    def cut_when_long(req):
        ls = part_lines(req["user"])
        if len(ls) > 20:
            return {"text": '{"entities": [{"ref": "n1", "type": "topic"', "incomplete": "max_output_tokens",
                    "error": "OpenAI: response.incomplete", "out_tokens": 2000}
        return {"text": json.dumps(honest(req["user"]))}
    rv = st.one("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri='docs/big.md'")[0]
    segs = SG.of_revision(st, rv)
    assert len(segs) == 1 and len(segs[0]["snippet_ids"]) > 1, "the fixture must be one multi-snippet segment"
    tr = FakeTransport(cut_when_long)
    res = run_pass(st, root, tr, route="openai")
    assert res["status"] == "done" and len(tr.requests) > 1
    assert st.one("SELECT count(*) FROM segment_cache WHERE seg_hash=?", (segs[0]["seg_hash"],))[0] == 1
    assert st.one("SELECT extract_state FROM revision WHERE id=?", (rv,))[0] == "done"
    # the next pass's segmenter yields the unsplit segment again: a cache hit, no request
    with st.conn:
        st.conn.execute("UPDATE revision SET extract_state='pending' WHERE id=?", (rv,))
    tr2 = FakeTransport(cut_when_long)
    run_pass(st, root, tr2, route="openai")
    assert tr2.requests == []


# ---------------------------------------------------------------- call-active (section 8.5)

def call_file():
    p = Path(os.environ["LT_AI_STATE"]).with_name("call-active.json")
    p.write_text(json.dumps({"started_at": "2026-11-24T10:00:00Z", "pid": os.getpid()}))
    return p


def test_call_active_present_sends_nothing(env):
    root = env
    big_file(root, lines=3)
    st = open_store(root)
    ingest(st, root)
    call_file()
    tr = FakeTransport()
    res = run_pass(st, root, tr)
    assert tr.requests == [] and res["status"] == "paused_call"
    # stale: a dead pid
    Path(os.environ["LT_AI_STATE"]).with_name("call-active.json").write_text(json.dumps({"pid": 999999}))
    assert W.call_active() is False


def test_call_starting_during_a_slow_request_cancels_it(env):
    root = env
    put(root, "docs/a.md", "# A\nAlpha line one is here.\n## B\nBeta line two is here.\n")
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport(delay=5.0)
    threading.Timer(0.3, call_file).start()
    t0 = time.monotonic()
    res = run_pass(st, root, tr)
    assert time.monotonic() - t0 < 2.5 and tr.cancelled == 1
    assert len(tr.requests) == 1 and res["status"] == "paused_call"
    assert st.one("SELECT count(*) FROM revision WHERE extract_state='pending'")[0] >= 1
    assert res["in_tokens"] > 0                 # the aborted request's input is counted as spent
    ledger = [json.loads(x) for x in W.ledger_path().read_text().splitlines()]
    assert ledger and all(x["job"] == "evidence" for x in ledger)


def call_file_when(ready, timeout=30.0):
    """Starts the call once ready() holds (the child request is really running), not after a fixed delay that a loaded
    machine can outrun. Returns a list that gets the monotonic time the call file was written; it stays empty when
    ready() never held within the timeout (then no call file is written)."""
    fired = []
    target = Path(os.environ["LT_AI_STATE"]).with_name("call-active.json")   # this test's state, never a later one's

    def go():
        end = time.monotonic() + timeout
        while not ready():
            if time.monotonic() > end:
                return
            time.sleep(0.02)
        fired.append(time.monotonic())
        target.write_text(json.dumps({"started_at": "2026-11-24T10:00:00Z", "pid": os.getpid()}))
    threading.Thread(target=go, daemon=True).start()
    return fired


class SlowOllama(http.server.BaseHTTPRequestHandler):
    """A fake Ollama on loopback: /api/tags answers (so nothing starts Ollama), /api/chat streams one token a second
    and records when its client goes away."""
    gone = threading.Event()
    posted = threading.Event()

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models": []}')

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SlowOllama.posted.set()
        self.send_response(200)
        self.end_headers()
        try:
            for _ in range(30):
                self.wfile.write(b'{"message": {"content": "{"}, "done": false}\n')
                self.wfile.flush()
                time.sleep(0.3)
        except (BrokenPipeError, ConnectionResetError):
            SlowOllama.gone.set()


def test_abort_closes_the_connection_of_a_real_child_request(env, monkeypatch):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SlowOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("LT_OLLAMA_URL", "http://127.0.0.1:{}".format(srv.server_address[1]))
    root = env
    put(root, "docs/a.md", "# A\nAlpha line one is here.\n")
    st = open_store(root)
    ingest(st, root)
    SlowOllama.gone.clear()
    SlowOllama.posted.clear()
    fired = call_file_when(SlowOllama.posted.is_set)
    res = run_pass(st, root, None, route="local")
    assert SlowOllama.posted.is_set() and fired, "the request never reached the fake provider"
    took = time.monotonic() - fired[0]
    assert res["status"] == "paused_call" and took < 2.5, (res, took)
    assert SlowOllama.gone.wait(3.0), "the fake provider never saw the disconnect"
    srv.shutdown()


def test_abort_kills_the_cli_process_group(env, tmp_path, monkeypatch):
    pidfile = tmp_path / "codex.pid"
    cli = tmp_path / "codex"
    cli.write_text("#!/bin/sh\ncase \"$*\" in *\"login status\"*) echo 'Logged in using ChatGPT'; exit 0;; esac\n"
                   "echo $$ > {}\nexec sleep 30\n".format(pidfile))
    cli.chmod(0o755)
    monkeypatch.setenv("LT_CODEX_CLI", str(cli))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    root = env
    put(root, "docs/a.md", "# A\nAlpha line one is here.\n")
    st = open_store(root)
    ingest(st, root)
    fired = call_file_when(lambda: pidfile.exists() and pidfile.read_text().strip().isdigit())
    res = run_pass(st, root, None, route="codex")
    errs = [dict(r) for r in st.q("SELECT status, error FROM run_item")]
    assert pidfile.exists() and fired, ("the fake codex never started", res, errs)
    assert res["status"] == "paused_call" and time.monotonic() - fired[0] < 2.5, (res, errs)
    pid = int(pidfile.read_text().strip())
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the fake codex process survived the abort")


# ---------------------------------------------------------------- ceilings (section 9.1)

def many_sections(root, rel, n=8):
    put(root, rel, "".join("## S{}\nSection {} of the plan has a milestone line.\n".format(i, i) for i in range(n)))


def at_cap(req):
    return {"text": json.dumps(honest(req["user"])), "out_tokens": 2000}


def test_ceiling_never_exceeded_with_answers_at_the_cap(env):
    root = env
    many_sections(root, "docs/plan.md", 12)
    st = open_store(root)
    ingest(st, root)
    one = W.Pass(st, str(root), {"route": "api"}, FakeTransport()).estimate("x")
    assert one > 0
    ceiling = one * 3.5                         # room for three requests at the full cap, not four
    tr = FakeTransport(at_cap, out_tokens=2000)
    res = run_pass(st, root, tr, ceilings={"usd": {"pass": ceiling, "day": 100, "all_projects_day": 100}})
    assert res["status"] == "ceiling" and res["usd"] <= ceiling + 1e-9
    assert res["est_usd"] is not None and res["usd"] is not None and res["ceiling_kind"] == "usd"
    assert all(r["max_tokens"] == 2000 for r in tr.requests)
    ledger = [json.loads(x) for x in W.ledger_path().read_text().splitlines()]
    assert len(ledger) == len(tr.requests) and {x["job"] for x in ledger} == {"evidence"}


def test_project_day_and_all_projects_day_caps(env, tmp_path):
    root = env
    many_sections(root, "docs/plan.md", 12)
    root2 = tmp_path / "proj2"
    many_sections(root2, "docs/plan.md", 12)
    st, st2 = open_store(root), open_store(root2, "p-2")
    ingest(st, root)
    ingest(st2, root2)
    one = W.Pass(st, str(root), {"route": "api"}, FakeTransport()).estimate("x")
    cap = one * 4.5
    lim = {"usd": {"pass": 100, "day": 100, "all_projects_day": cap}}
    r1 = run_pass(st, root, FakeTransport(at_cap, out_tokens=2000), ceilings=lim)
    r2 = run_pass(st2, root2, FakeTransport(at_cap, out_tokens=2000), ceilings=lim)
    assert r1["usd"] + r2["usd"] <= cap + 1e-9 and r2["status"] == "ceiling"
    # project day: a second pass of the same project the same day stops at the day cap
    many_sections(root, "docs/plan2.md", 12)
    ingest(st, root)
    day = {"usd": {"pass": 100, "day": r1["usd"] + one * 1.5, "all_projects_day": 100}}
    r3 = run_pass(st, root, FakeTransport(at_cap, out_tokens=2000), ceilings=day)
    assert r3["status"] == "ceiling" and r1["usd"] + r3["usd"] <= day["usd"]["day"] + 1e-9


def test_plan_route_percent_and_local_seconds(env):
    root = env
    many_sections(root, "docs/plan.md", 6)
    st = open_store(root)
    ingest(st, root)
    used = [20.0]

    def plan(req):
        used[0] += 4.0
        return {"text": json.dumps(honest(req["user"])), "account_usage": {
            "at": time.time(), "limited": False, "windows": [{"name": "five_hour", "used_percent": used[0]}]}}
    R.note_account_usage("codex", {"at": time.time(), "limited": False,
                                   "windows": [{"name": "five_hour", "used_percent": 20.0}]}, W.state_path())
    res = run_pass(st, root, FakeTransport(plan), route="codex")
    assert res["status"] == "ceiling" and res["ceiling_kind"] == "pct"
    assert res["pct_before"] == 20.0 and res["pct_after"] is not None and res["pct_after"] - res["pct_before"] <= 10 + 4
    assert res["usd"] is None and res["est_usd"] is None
    many_sections(root, "docs/local.md", 2)
    ingest(st, root)
    res = run_pass(st, root, FakeTransport(), route="local")
    assert res["ceiling_kind"] == "minutes" and res["seconds"] is not None and res["usd"] is None


def test_unconnected_route_falls_back_to_local_only(env):
    root = env
    many_sections(root, "docs/plan.md", 2)
    st = open_store(root)
    ingest(st, root)
    seen = []

    def resp(req):
        seen.append(req["route"])
        if req["route"] == "openai":
            return {"error": "NoRoute: no API key", "text": ""}
        return {"text": json.dumps(honest(req["user"]))}
    res = run_pass(st, root, FakeTransport(resp), route="openai")
    assert res["status"] == "done" and res["route"] == "local" and res["fallback_from"] == "openai"
    assert set(seen) == {"openai", "local"}


# ---------------------------------------------------------------- the segment cache (section 8.2)

def plan_text(mid="The middle section is short."):
    return ("# Plan\n\n## Scope\n" + "\n".join("Scope line {} covers the rollout.".format(i) for i in range(5)) +
            "\n\n## Middle\n" + mid + "\n\n## Budget\n" +
            "\n".join("Budget line {} is about the cost.".format(i) for i in range(5)) + "\n")


def test_segment_cache_resends_only_what_changed(env):
    root = env
    put(root, "docs/plan.md", plan_text())
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport()
    run_pass(st, root, tr)
    first = len(tr.requests)
    assert first == 4                                  # title, Scope, Middle, Budget
    tr = FakeTransport()
    run_pass(st, root, tr)
    assert tr.requests == []                           # no changes: 0 requests
    # a short section between two others changes: only that section is sent, the rest are cached
    put(root, "docs/plan.md", plan_text("The middle section now names a new date, March 3."), mtime=OLD + 100)
    ingest(st, root)
    tr = FakeTransport()
    res = run_pass(st, root, tr)
    assert len(tr.requests) == 1 and "March 3" in tr.requests[0]["user"]
    statuses = [r["status"] for r in st.q("SELECT status FROM run_item WHERE run_id=?", (res["id"],))]
    assert statuses.count("cached") == 3
    # the section right above another grows by lines: Budget's lines move, it is still a cache hit
    put(root, "docs/plan.md", plan_text("The middle section now names a new date, March 3.\nAnd a second line."),
        mtime=OLD + 200)
    ingest(st, root)
    tr = FakeTransport()
    run_pass(st, root, tr)
    assert len(tr.requests) == 1
    # a new extractor sends every segment again
    rules = X.load_rules()
    rules["rules_version"] = "g2-s3-test"
    tr = FakeTransport()
    W.Pass(st, str(root), {"route": "api", "poll_s": 0.05}, tr, rules=rules).run()
    assert len(tr.requests) == 4                       # every segment of the done revision, at once
    tr = FakeTransport()
    W.Pass(st, str(root), {"route": "api", "poll_s": 0.05}, tr, rules=rules).run()
    assert tr.requests == []                           # and only once


def test_revert_and_move_send_nothing(env):
    root = env
    a = "# Budget\nThe budget is 40 thousand.\n"
    b = "# Budget\nThe budget is 55 thousand.\n"
    put(root, "docs/budget.md", a)
    put(root, "live/2026-11-04-1000-weekly.md", "Speaker 1: we start the pilot on Monday.\n")
    put(root, "live/2026-11-04-1000-weekly.notes.md", "## Summary\nPilot starts Monday.\n## Decisions\nShip it.\n"
                                                 "## Risks\nNone named.\n")
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport()
    run_pass(st, root, tr)
    notes_reqs = [[t for _n, t in part_lines(r["user"])] for r in tr.requests]
    notes_reqs = [p for p in notes_reqs if "Pilot starts Monday." in p]
    assert len(notes_reqs) == 1 and "Ship it." in notes_reqs[0]                # notes cut like a transcript
    put(root, "docs/budget.md", b, mtime=OLD + 100)
    ingest(st, root)
    run_pass(st, root, FakeTransport())
    put(root, "docs/budget.md", a, mtime=OLD + 200)
    ingest(st, root)
    tr = FakeTransport()
    run_pass(st, root, tr)
    assert tr.requests == []                                                   # revert: the cache answers
    res = SE.search(st, "budget 40 thousand", mode="prep")
    quotes = [q["quote"] for f in res["items"] if f.get("kind") == "claim" for q in f.get("quotes") or []]
    assert any("40 thousand" in q for q in quotes) and not any("55 thousand" in q for q in quotes)
    third = st.one("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri='docs/budget.md'"
                   " AND rv.superseded_at IS NULL")[0]
    assert st.one("SELECT count(*) FROM evidence ev JOIN snippet sn ON sn.id=ev.snippet_id WHERE sn.revision_id=?",
                  (third,))[0] > 0                                             # written onto the third revision
    CL.migrate(str(root / "live"))
    ingest(st, root)
    tr = FakeTransport()
    run_pass(st, root, tr)
    assert tr.requests == []                                                   # a move costs nothing


# ---------------------------------------------------------------- estimate, cache, models

def test_route_with_no_cached_tokens_is_estimated_without_cache(env):
    root = env
    many_sections(root, "docs/plan.md", 3)
    st = open_store(root)
    ingest(st, root)
    no_cache = W.estimate(st, str(root), "api")
    assert no_cache["cache_assumed"] is False
    run_pass(st, root, FakeTransport(lambda req: {"text": json.dumps(honest(req["user"])), "cached": 0}))
    assert st.meta("cache_seen:api") == ""
    p = W.Pass(st, str(root), {"route": "api"}, FakeTransport())
    p._run_start()
    full = p.estimate("hello")
    st.set_meta("cache_seen:api", "1")
    assert p.estimate("hello") < full


def test_models_without_the_map_fall_back_to_routing(env):
    """The extract models of rules/ai.yaml > evidence (PB-103) equal routing.PING_MODEL on these routes; the fallback
    without the map is tested in test_evidence_routing_pb52_s3b."""
    assert W.models("openai")["extract"] == R.PING_MODEL["openai"]
    assert W.models("api")["extract"] == R.PING_MODEL["api"]


def test_flag_off_cli_refuses_pass(env, monkeypatch):
    from livetips_m0.evidence import __main__ as CLI
    monkeypatch.delenv("LT_EVIDENCE", raising=False)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert CLI.main(["pass", "--project-uid", "p-1", "--root", str(env), "--route", "api"]) == 2
    assert not (Path(os.environ["LT_SUPPORT_DIR"]) / "evidence").exists()


def test_prefix_is_identical_on_every_request(env):
    root = env
    many_sections(root, "docs/plan.md", 3)
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport()
    run_pass(st, root, tr)
    assert len({r["system"] for r in tr.requests}) == 1 and all(r["cache"] for r in tr.requests)
    assert X.extractor_id(RULES).startswith(RULES["rules_version"] + ":")


# ---------------------------------------------------------------- the G0 extraction fixture

def test_fixture_records_the_gold_hash_and_a_changed_gold_fails(tmp_path, monkeypatch):
    import importlib.util
    g0 = HERE.parent / "bench" / "g0"
    spec = importlib.util.spec_from_file_location("g0_run_s3", g0 / "run.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    monkeypatch.setenv("LT_AI_STATE", str(tmp_path / "state" / "ai-routes.json"))
    (tmp_path / "state").mkdir()
    out = tmp_path / "extract"
    m = bench.record_extract("api", out_dir=out, transport=FakeTransport())
    gold_sha = __import__("hashlib").sha256(bench.GOLD2.read_bytes()).hexdigest()
    assert m["gold_sha256"] == gold_sha and m["calibration"]["segments_recorded"] > 0
    files = [p for p in out.glob("*.json") if p.name != "manifest.json"]
    assert files and all("answers" in json.loads(p.read_text()) for p in files)
    gold, gold2 = bench.load_gold(), bench.load_gold(bench.GOLD2)
    with bench.store_env(tmp_path / "replay"):
        ctx = bench.build_v2(tmp_path / "replay" / "v2", gold, gold2, False)
        ctx["gold_sha256"] = gold_sha
        rep = bench.replay_fixture(ctx, out, True)
        assert rep["state"] == "ok" and rep["misses"] == 0 and rep["hits"] > 0
        ctx["gold_sha256"] = "0" * 64
        with pytest.raises(SystemExit, match="gold changed"):
            bench.replay_fixture(ctx, out, True)
        ctx["store"].close()
    # review round 2: a recorded answer that does not parse fails the run, it never becomes an empty answer
    bad = files[0]
    data = json.loads(bad.read_text())
    bad.write_text(json.dumps(dict(data, answers=["not json at all"])))
    with bench.store_env(tmp_path / "replay2"):
        ctx = bench.build_v2(tmp_path / "replay2" / "v2", gold, gold2, False)
        ctx["gold_sha256"] = gold_sha
        with pytest.raises(SystemExit, match="fixture answer invalid"):
            bench.replay_fixture(ctx, out, True)
        # answers with no manifest.json: the gold hash cannot be checked, the run fails
        bad.write_text(json.dumps(data))
        (out / "manifest.json").unlink()
        with pytest.raises(SystemExit, match="gold unverifiable"):
            bench.replay_fixture(ctx, out, True)
        ctx["store"].close()


def g0_bench():
    import importlib.util
    spec = importlib.util.spec_from_file_location("g0_run_s3a", HERE.parent / "bench" / "g0" / "run.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    return bench


def test_checked_in_fixture_matches_the_current_gold():
    """S3a: the extraction fixture in bench/g0/fixtures/extract was recorded against today's gold v2 and says honestly
    that no provider wrote it (authored; PB-103 S3b re-records it on a real route)."""
    bench = g0_bench()
    m = json.loads(bench.MANIFEST.read_text(encoding="utf-8"))
    assert m["gold_sha256"] == __import__("hashlib").sha256(bench.GOLD2.read_bytes()).hexdigest()
    assert m["route"] == "authored" and m["calibration"]["measured"] is False
    files = [p for p in bench.EXTRACT.glob("*.json") if p.name != "manifest.json"]
    assert len(files) == m["calibration"]["segments_recorded"] and "S3" in bench.ready_set()


def gate_res(hop=5, misses=0, modality=100.0, derived=0, b_hop=0):
    run = {"hop_answer_rate": {"n": hop, "of": 6}, "fixture": {"misses": misses}, "modality_accuracy": modality,
           "derived_confirmed": derived}
    res = {"variant": "C", "v2": {"ready": ["S2", "S3"], "sets": {"small": {"no_vectors": run,
                                                                            "vectors": {"skipped": "no model"}}}}}
    b = {"v2": {"sets": {"small": {"no_vectors": {"hop_answer_rate": {"n": b_hop}}}}}}
    return res, b


def test_s3_gate_checks(monkeypatch):
    bench = g0_bench()
    res, _b = gate_res()

    def no_b(_v):
        raise SystemExit("no B result saved")
    monkeypatch.setattr(bench, "newest", no_b)
    assert bench.s3_gate(res, None)["small hop_ge_B_plus_2"] is False      # no B result: the gate does not pass
    res, b = gate_res()
    assert all(bench.s3_gate(res, b).values())
    for kw, key in ((dict(hop=3), "small hop_ge_4_of_6"), (dict(b_hop=4), "small hop_ge_B_plus_2"),
                    (dict(misses=1), "small fixture_misses_0"), (dict(modality=50.0), "small modality_accuracy_100"),
                    (dict(derived=1), "small/no_vectors derived_confirmed_0")):
        res, b = gate_res(**kw)
        assert bench.s3_gate(res, b)[key] is False, kw
    res, b = gate_res()
    res["v2"]["ready"] = ["S2"]
    assert bench.s3_gate(res, b) is None             # no recorded fixture: no S3 gate
    res["variant"] = "B"
    assert bench.s3_gate(res, b) is None


def test_modality_rows_and_derived_confirmation(env):
    """A modality row passes only on a matching claim with the expected modality and none confirmed by a primary
    source; a confirmation whose evidence is only a derived source (notes) is counted by derived_confirmed."""
    bench = g0_bench()
    root = env
    put(root, "live/2026-11-04-1000-globex-weekly.notes.md", "## Summary\nNorthpoint proposed 39 extra hours.\n")
    st = open_store(root)
    ingest(st, root)
    rev = st.one("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri LIKE '%notes.md'")[0]
    S.apply_extraction(st, rev, {"entities": [{"ref": "n1", "type": "topic", "name": "Globex extra hours",
                                               "quote": "proposed 39 extra hours", "lines": [2, 2]}],
                                 "claims": [{"subject": "n1", "predicate": "value", "value": "39", "modality": "confirmation",
                                             "quote": "Northpoint proposed 39 extra hours.", "lines": [2, 2]}]})
    cid = st.one("SELECT id FROM claim")[0]
    ctx = {"store": st, "pm_excluded": []}

    class V:
        name = "C"
    row = {"id": "m", "kind": "modality", "needs": "S3", "client": "globex", "expect": [{"any": ["proposed 39 extra hours"]}],
           "modality": "proposal", "expected": {"C": "pass"}}
    gold2 = {"excluded_facts": [], "forbidden": {"globex": []}}
    item = {"kind": "claim", "id": cid, "modality": "proposal", "confirmations": 0, "origin": "llm",
            "text": "Northpoint proposed 39 extra hours.", "quotes": []}
    ok = bench.judge(V, ctx, gold2, row, {"items": [item], "entities": []})
    assert ok["status"] == "pass" and ok["derived_confirmed"] == []
    wrong = dict(item, modality="statement")
    assert bench.judge(V, ctx, gold2, row, {"items": [wrong], "entities": []})["status"] == "fail"
    shown = dict(item, modality="confirmation", confirmations=1)      # counted as confirmed: only notes hold it
    r = bench.judge(V, ctx, gold2, dict(row, modality="not_confirmed"), {"items": [shown], "entities": []})
    assert r["status"] == "fail" and r["derived_confirmed"] == [cid]


def test_hop_walk_follows_the_search_order(env, monkeypatch):
    """The hop step starts from the best match (not a set's order) and returns the edge it walked."""
    bench = g0_bench()
    cards = {"a": {"edges": [{"kind": "edge", "id": 1, "src": "a", "rel": "depends_on", "dst": "b", "text": "a needs b"}]},
             "b": {"edges": [{"kind": "edge", "id": 1, "src": "a", "rel": "depends_on", "dst": "b", "text": "a needs b"},
                             {"kind": "edge", "id": 2, "src": "c", "rel": "blocks", "dst": "b", "text": "c blocks b"}],
                   "claims": [{"kind": "claim", "id": 9, "text": "b is late"}]},
             "c": {"claims": [{"kind": "claim", "id": 10, "text": "c waits for the client"}]}}
    for i in range(20):
        cards["w{}".format(i)] = {"edges": [{"kind": "edge", "id": 100 + i, "src": "w{}".format(i), "rel": "owns",
                                             "dst": "x{}".format(i), "text": "weak"}]}
    monkeypatch.setattr(bench.SE, "entity_card", lambda st, eid, **kw: cards.get(eid))
    ents = [{"id": "a"}] + [{"id": "w{}".format(i)} for i in range(20)]
    out = bench.hop_items(None, ents, None, None, (), 6)
    texts = [o["text"] for o in out]
    assert texts[:4] == ["a needs b", "b is late", "c blocks b", "c waits for the client"]
    assert all(o["hop"] for o in out)


def test_line_range_over_two_snippets_is_kept(env):
    root = env
    put(root, "docs/acme/plan.md", "# Plan\nAcme rollout starts in March.\nGlobex wants the same.\nThe pilot has 40 seats.\n")
    put(root, "docs/globex/x.md", "# X\nGlobex note.\n")
    put(root, "live/2026-11-04-1000-acme-weekly.md", "Speaker 1: hello\n")
    put(root, "live/2026-11-05-1000-globex-weekly.md", "Speaker 1: hello\n")
    st = open_store(root)
    ingest(st, root)
    v = view_of(st, "docs/acme/plan.md", "Acme rollout")
    a = line_in(v, "Acme rollout")
    obj = {"entities": [{"ref": "n1", "type": "topic", "name": "Acme rollout", "quote": "Acme rollout starts in March",
                         "lines": [a, a + 1]}], "claims": [], "relations": []}
    items, c = X.validate(st, v, obj, RULES, [], str(root), X.project_labels(str(root)))
    assert len(items["entities"]) == 1 and c["dropped"] == 0


def test_plan_route_with_unknown_usage_is_bounded_by_requests(env):
    root = env
    many_sections(root, "docs/plan.md", 40)
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport()                              # no account_usage at all, as Codex in --ephemeral mode
    res = run_pass(st, root, tr, route="codex")
    per = float(RULES["pct_per_request_assumed"])
    assert res["status"] == "ceiling" and len(tr.requests) == int(RULES["ceilings"]["pct"]["pass"] / per)


def test_cache_write_is_inside_the_estimate(env):
    root = env
    many_sections(root, "docs/plan.md", 12)
    st = open_store(root)
    ingest(st, root)
    one = W.Pass(st, str(root), {"route": "api"}, FakeTransport()).estimate("x")
    ceiling = one * 3.5
    tr = FakeTransport(lambda req: {"text": json.dumps(honest(req["user"])), "out_tokens": 2000,
                                    "in_tokens": W.tokens(req["user"], "api"),
                                    "cache_write": W.tokens(req["system"], "api")})
    res = run_pass(st, root, tr, ceilings={"usd": {"pass": ceiling, "day": 100, "all_projects_day": 100}})
    assert res["status"] == "ceiling" and res["usd"] <= ceiling + 1e-9


def test_hedge_start_matches_identity():
    from livetips_m0 import identity as ID
    assert RULES["hedge_start"] == [ID.TENTATIVE_RX.pattern, ID.HEDGE_RX.pattern]


# ---------------------------------------------------------------- review round 2 (PB-86)

def test_items_that_break_the_item_schema_are_dropped_not_crashing(env):
    """Section 8.4 item 1 per item: wrong types, a missing key, lines that are not two integers. Each such item is
    dropped as "shape" (never the segment, never an exception), and what is kept writes cleanly."""
    root = env
    two_clients(root)
    st = open_store(root)
    ingest(st, root)
    labels = X.project_labels(str(root))
    v = view_of(st, "docs/acme/plan.md", "Acme rollout")
    a = line_in(v, "Acme rollout")
    q = "Acme rollout starts in March"
    good = {"ref": "n1", "type": "topic", "name": "Acme rollout", "quote": q, "lines": [a, a]}
    claim = {"subject": "n1", "predicate": "said", "value": "March", "modality": "statement", "quote": q, "lines": [a, a]}
    rel = {"from": "n1", "rel": "mentions", "to": "n1", "modality": "statement", "quote": q, "lines": [a, a]}
    obj = {"entities": [good,
                        dict(good, ref="n2", type=["topic"]),                 # a list as the type
                        dict(good, ref="n3", aliases="Acme"),                 # a string as the aliases
                        dict(good, ref="n4", lines=[a, a, a]),                # three lines
                        dict(good, ref="n5", lines=[True, True]),             # booleans, not integers
                        dict(good, ref="n6", known_id={"id": "x"}),           # a dict as the known id
                        {k: x for k, x in good.items() if k != "ref"},        # a required key missing
                        "n7"],                                                # not an object
           "claims": [claim,
                      dict(claim, confidence="high"),                         # a string as the confidence
                      dict(claim, predicate=["said"]),
                      dict(claim, value={"a": 1})],
           "relations": [dict(rel, rel={"x": 1}), dict(rel, lines=["1", "2"])]}
    items, c = X.validate(st, v, obj, RULES, [], str(root), labels)
    assert [e["ref"] for e in items["entities"]] == ["n1"] and len(items["claims"]) == 1 and items["relations"] == []
    assert c["reasons"] == {"shape": 12}
    S.apply_extraction(st, v["revision_id"], items, mark_done=False)


def test_an_answer_missing_a_top_level_list_gets_the_repair_retry(env):
    """{} or an answer without "claims" does not match the schema: it is not cached as empty, it gets the one repair
    retry, and a second such answer makes the segment invalid."""
    assert X.parse("{}", RULES["schema"])[0] is None
    assert "claims" in X.parse('{"entities": [], "relations": []}', RULES["schema"])[1]
    assert X.parse('{"entities": [], "claims": [], "relations": []}', RULES["schema"])[1] is None
    root = env
    put(root, "docs/one.md", "# One\nThe pilot starts in March for the team.\n")
    st = open_store(root)
    ingest(st, root)
    answers = iter([{"text": "{}"}, None])
    tr = FakeTransport(lambda req: next(answers) or {"text": json.dumps(honest(req["user"]))})
    res = run_pass(st, root, tr)
    assert len(tr.requests) == 2 and "missing" in tr.requests[1]["user"]
    assert st.one("SELECT status FROM run_item WHERE run_id=?", (res["id"],))["status"] == "done"
    put(root, "docs/one.md", "# One\nThe pilot starts in April for the team.\n", mtime=OLD + 100)
    ingest(st, root)
    tr = FakeTransport(lambda req: {"text": '{"entities": []}'})
    res = run_pass(st, root, tr)
    assert len(tr.requests) == 2
    assert st.one("SELECT status FROM run_item WHERE run_id=?", (res["id"],))["status"] == "invalid"


def test_cache_hit_on_a_snippet_longer_than_60_lines_keeps_every_item(env):
    """The validated item keeps the lines its quote sits on, not the whole snippet's range: a cached segment whose
    snippet spans more than 60 lines re-validates in full when a section above moves its lines."""
    root = env
    rows = "".join("row {} ok\n".format(i) for i in range(75))
    put(root, "docs/long.md", "# Top\nThe intro line is here.\n## Long\n" + rows)
    st = open_store(root)
    ingest(st, root)
    v = view_of(st, "docs/long.md", "row 0 ok")
    assert any(sn["lines"][1] - sn["lines"][0] > 60 for sn in v["snippets"])       # the case under test
    run_pass(st, root, FakeTransport())

    def written():
        rv = st.one("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri='docs/long.md' AND"
                    " rv.superseded_at IS NULL")[0]
        return st.one("SELECT count(*) FROM evidence ev JOIN snippet sn ON sn.id=ev.snippet_id WHERE sn.revision_id=?"
                      " AND sn.text LIKE '%row %'", (rv,))[0]
    first = written()
    assert first == 40                         # one claim per entity kept, the cap of 40 entities per segment
    put(root, "docs/long.md", "# Top\nThe intro line is here.\nA second intro line.\nA third.\n## Long\n" + rows,
        mtime=OLD + 100)
    ingest(st, root)
    tr = FakeTransport()
    res = run_pass(st, root, tr)
    assert all("row 0 ok" not in r["user"] for r in tr.requests)                    # Long came from the cache
    cached = st.q("SELECT dropped FROM run_item WHERE run_id=? AND status='cached'", (res["id"],))
    assert cached and all(r["dropped"] == 0 for r in cached)
    assert written() == first


def test_catalog_names_a_joined_task_only_as_the_segment_may_see_it(env):
    """A Globex mention joined to an Acme register task and edited: the Acme segment's catalog never carries the
    Globex text or id (the name search shows the Acme scope), and the Globex segment never gets the Acme task's id."""
    root = env
    two_clients(root)
    reg = [{"v": 1, "op": "add", "id": "c-aaaaaaaaaa", "text": "book the heron demo", "owner": "me",
            "series": "|acmeweeklystatus", "created": "2026-11-03T10:00:00+00:00"},
           {"v": 1, "op": "add", "id": "c-hhhhhhhhhh", "text": "Globex heron demo slides", "owner": "me",
            "series": "|globexweekly", "created": "2026-11-04T11:05:00+00:00"},
           {"v": 1, "op": "same", "id": "c-hhhhhhhhhh", "to": "c-aaaaaaaaaa", "at": "2026-11-05T11:00:00+00:00"},
           {"v": 1, "op": "edit", "id": "c-hhhhhhhhhh", "text": "pelican secret price heron demo",
            "at": "2026-11-06T10:00:00+00:00"}]
    put(root, IG.REGISTER, "\n".join(json.dumps(e) for e in reg) + "\n")
    put(root, "docs/acme/demo.md", "# Demo\nAcme asks us to book the heron demo soon.\n")
    put(root, "docs/globex/demo.md", "# Demo\nGlobex asks about the heron demo slides and pelican.\n")
    st = open_store(root)
    ingest(st, root)
    labels = X.project_labels(str(root))
    acme = X.catalog(st, view_of(st, "docs/acme/demo.md", "heron"), str(root), labels)
    assert any(c["id"] == "c-aaaaaaaaaa" for c in acme)
    text = json.dumps(acme).lower()
    assert "pelican" not in text and "hhhhhhhhhh" not in text and "globex" not in text, text
    glob = X.catalog(st, view_of(st, "docs/globex/demo.md", "heron"), str(root), labels)
    assert "aaaaaaaaaa" not in json.dumps(glob).lower()


def test_catalog_drops_an_alias_only_another_client_saw(env):
    root = env
    two_clients(root)
    put(root, "pacenote/topics.md", "# Topics\nThe Mondays meeting is short.\n")
    put(root, "docs/globex/alias.md", "# Alias\nGlobex calls the Mondays meeting the kestrel sync.\n")
    put(root, "docs/acme/more.md", "# More\nAcme joins the Mondays meeting and the kestrel sync.\n")
    st = open_store(root)
    ingest(st, root)
    labels = X.project_labels(str(root))
    mondays = _seed_entity(st, root, "pacenote/topics.md", "Mondays")
    v = view_of(st, "docs/globex/alias.md", "kestrel")
    n = line_in(v, "kestrel")
    line = "Globex calls the Mondays meeting the kestrel sync."
    items, _ = X.validate(st, v, {"entities": [{"ref": "n1", "known_id": mondays, "type": "system", "name": "Mondays",
                                                "aliases": ["kestrel sync"], "quote": line, "lines": [n, n]}],
                                  "claims": [], "relations": []},
                          RULES, [{"id": mondays, "type": "system", "name": "Mondays", "aliases": []}], str(root), labels)
    S.apply_extraction(st, v["revision_id"], items, mark_done=False)
    assert st.one("SELECT 1 FROM alias WHERE alias_norm='kestrel sync' AND entity_id=?", (mondays,))
    acme = X.catalog(st, view_of(st, "docs/acme/more.md", "kestrel"), str(root), labels)
    hit = [c for c in acme if c["id"] == mondays]
    assert hit and "kestrel sync" not in hit[0]["aliases"]
