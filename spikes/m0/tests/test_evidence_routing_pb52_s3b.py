"""PB-103 (GraphRAG G2 S3b, docs/specs/PB-52-g2-evidence-store.md sections 8.6, 9.1 and 17 S3): the evidence routing
job, the model map in rules/ai.yaml, the real OpenAI and Gemini classes with one request per cut answer, Ollama's exact
cap, local overflow at a ceiling and the --extract calibration.

No request leaves the Mac: the provider classes talk to a stubbed urlopen, Ollama is a fake server on loopback (its
/api/tags answers, so nothing starts Ollama) and the calibration replays the checked-in authored fixture. No app, GUI
program or real CLI is started (L33).
"""
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "bench" / "g0"))

from livetips_m0 import cost as COST  # noqa: E402
from livetips_m0 import gemini_provider as GP  # noqa: E402
from livetips_m0 import openai_provider as OP  # noqa: E402
from livetips_m0 import routing as R  # noqa: E402
from livetips_m0.evidence import extract as X  # noqa: E402
from livetips_m0.evidence import request as RQ  # noqa: E402
from livetips_m0.evidence import worker as W  # noqa: E402

from livetips_m0.evidence import segments as SG  # noqa: E402
from test_evidence_extract_pb52_s3 import (  # noqa: E402,F401  (env is a fixture)
    FakeTransport, big_file, env, honest, ingest, many_sections, open_store, part_lines, put, run_pass)

AI_YAML = HERE.parent.parent.parent / "rules" / "ai.yaml"


# ---------------------------------------------------------------- the routing job (section 8.6)

def test_evidence_job_steps_only_to_the_model_on_this_mac():
    assert "evidence" in R.JOBS
    every = list(R.CHAIN_DEFAULT)
    for chosen in ("api", "openai", "gemini", "codex", "code"):
        assert R.chain_order("evidence", chosen, every, set(every)) == [chosen, "local"]
    assert R.chain_order("evidence", "local", every, set(every)) == ["local"]
    # the other jobs are unchanged (negative control: the same chain without the evidence rule)
    assert R.chain_order("after", "openai", every, set(every)) == ["openai", "code", "codex", "api", "gemini", "local"]
    assert R.chain_order("prep", "api", every, set(every)) == ["api", "code", "codex", "openai", "gemini", "local"]
    assert R.chain_order("tips", "api", every, set(every)) == ["api", "code", "codex", "openai", "gemini"]


# ---------------------------------------------------------------- the model map (rules/ai.yaml > evidence)

def test_the_map_in_rules_names_every_route_and_usd_models_are_priced(monkeypatch):
    monkeypatch.delenv("LT_LOCAL_MODEL", raising=False)
    m = yaml.safe_load(AI_YAML.read_text(encoding="utf-8"))["evidence"]
    assert set(m["extract"]) == set(m["summary"]) == set(R.ROUTES)
    for route in R.ROUTES:
        got = W.models(route)
        assert got == {"extract": m["extract"][route], "summary": m["summary"][route]}
    for route in W.USD_ROUTES:     # a pass on an unpriced name would end "off" before its first request
        assert COST.price_for(m["extract"][route]) and COST.price_for(m["summary"][route]), route
    # owner decision 24.11 (a): the cheapest model extracts, the after-call model summarises
    assert W.models("openai") == {"extract": "gpt-6-luna", "summary": OP.JOB_DEFAULTS["after"]}
    assert W.models("gemini") == {"extract": "gemini-3.5-flash-lite", "summary": GP.JOB_DEFAULTS["after"]}
    monkeypatch.setenv("LT_LOCAL_MODEL", "qwen9:7b")
    assert W.models("local") == {"extract": "qwen9:7b", "summary": "qwen9:7b"}


def test_a_changed_map_is_used_by_the_next_request(env, tmp_path):
    root = env
    many_sections(root, "docs/plan.md", 2)
    st = open_store(root)
    ingest(st, root)
    y = tmp_path / "ai.yaml"
    d = yaml.safe_load(AI_YAML.read_text(encoding="utf-8"))
    d["evidence"]["extract"]["api"] = "claude-sonnet-5-5"
    y.write_text(yaml.safe_dump(d), encoding="utf-8")
    tr = FakeTransport()
    res = run_pass(st, root, tr, ai_yaml=str(y))
    assert res["status"] == "done" and tr.requests
    assert {r["model"] for r in tr.requests} == {"claude-sonnet-5-5"} and res["model"] == "claude-sonnet-5-5"


def test_without_the_map_the_worker_falls_back_to_routing(tmp_path, monkeypatch):
    monkeypatch.delenv("LT_LOCAL_MODEL", raising=False)
    y = tmp_path / "ai.yaml"
    d = yaml.safe_load(AI_YAML.read_text(encoding="utf-8"))
    d.pop("evidence")
    y.write_text(yaml.safe_dump(d), encoding="utf-8")
    for route in ("api", "codex", "code"):
        assert W.models(route, y)["extract"] == R.PING_MODEL[route]
    assert W.models("openai", y) == {"extract": R.PING_MODEL["openai"], "summary": OP.JOB_DEFAULTS["after"]}
    assert W.models("gemini", y) == {"extract": R.PING_MODEL["gemini"], "summary": GP.JOB_DEFAULTS["after"]}


# ---------------------------------------------------------------- the real provider classes (PB-52 review note)

CUT = '{"entities": [{"ref": "n1", "type": "topic", "name": "Mile'       # no whole JSON object at the start


class Stream:
    def __init__(self, lines):
        self.lines = [(ln + "\n").encode() for ln in lines]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.lines)


def stub_urlopen(monkeypatch, module, answer):
    """module.urllib.request.urlopen -> answer(body) as SSE lines; records every request body."""
    bodies = []

    def fake(req, timeout=None):
        body = json.loads(req.data)
        bodies.append(body)
        return Stream(answer(body))
    monkeypatch.setattr(module.urllib.request, "urlopen", fake)
    return bodies


def openai_answer(full):
    def answer(body):
        text = full(body)
        if text is None:          # cut at max_output_tokens
            return ["data: " + json.dumps({"type": "response.output_text.delta", "delta": CUT}),
                    "data: " + json.dumps({"type": "response.incomplete", "response": {
                        "usage": {"input_tokens": 900, "output_tokens": body["max_output_tokens"]},
                        "incomplete_details": {"reason": "max_output_tokens"}}})]
        return ["data: " + json.dumps({"type": "response.output_text.delta", "delta": text}),
                "data: " + json.dumps({"type": "response.completed", "response": {
                    "usage": {"input_tokens": 900, "output_tokens": 120}}})]
    return answer


def gemini_answer(full):
    def answer(body):
        text = full(body)
        cut = text is None
        chunk = {"candidates": [{"content": {"parts": [{"text": CUT if cut else text}]},
                                 "finishReason": "MAX_TOKENS" if cut else "STOP"}],
                 "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 2000 if cut else 120,
                                   "thoughtsTokenCount": 128}}
        return ["data: " + json.dumps(chunk)]
    return answer


def req_for(route, model):
    return {"route": route, "model": model, "system": X.prefix(X.load_rules()), "user": "x", "max_tokens": 2000,
            "cache": True}


@pytest.mark.parametrize("route,module,answer,model", [
    ("openai", OP, openai_answer, "gpt-6-luna"), ("gemini", GP, gemini_answer, "gemini-3.5-flash-lite")])
def test_a_cut_answer_is_one_request_with_the_evidence_options_and_two_without(monkeypatch, route, module, answer,
                                                                                 model):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-gemini")
    bodies = stub_urlopen(monkeypatch, module, answer(lambda body: None))
    out = RQ.run(req_for(route, model))                  # the request child's own code, in process
    assert len(bodies) == 1, "the evidence job must not pay a second request for a cut answer"
    assert X.cut(out) and out["incomplete"] == "max_output_tokens" and out["retried"] is None
    # negative control: the same class without the evidence job's options asks once more with a larger limit
    bodies.clear()
    p = RQ.leg(route)
    c = p.complete(model=model, system=[{"text": "s"}], user="x", max_tokens=2000, cache_ttl=None, extra=None)
    assert len(bodies) == 2 and c.retried == "longer"
    if route == "openai":
        assert [b["max_output_tokens"] for b in bodies] == [2000, OP.LONGER_MAX]
    else:
        room = GP.THINKING_ROOM[GP.thinking_level(model)]
        assert bodies[0]["generationConfig"]["maxOutputTokens"] == 2000 + room


def test_gemini_request_carries_cap_plus_thinking_room_and_the_estimate_counts_it(env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-gemini")
    bodies = stub_urlopen(monkeypatch, GP, gemini_answer(lambda body: '{"entities": [], "claims": [], "relations": []}'))
    for model in ("gemini-3.5-flash-lite", "gemini-3.8-flash"):
        bodies.clear()
        RQ.run(req_for("gemini", model))
        sent = bodies[0]["generationConfig"]["maxOutputTokens"]
        assert sent == W.out_cap("gemini", model, 2000) == 2000 + GP.THINKING_ROOM[GP.thinking_level(model)]
    root = env
    st = open_store(root)
    p = W.Pass(st, str(root), {"route": "gemini", "model": "gemini-3.5-flash-lite"}, FakeTransport())
    p._run_start()
    fresh, pre = W.tokens("hello", "gemini"), W.tokens(p.prefix, "gemini")
    m = "gemini-3.5-flash-lite"
    assert p.estimate("hello") == pytest.approx(W.price(m, fresh, pre, 2000 + 128, False))   # room priced as output
    assert p.estimate("hello") > W.price(m, fresh, pre, 2000, False)


def user_of(body) -> str:
    """The user text of an OpenAI (plain or with cache items) or Gemini request body."""
    if "contents" in body:
        return body["contents"][0]["parts"][0]["text"]
    if isinstance(body["input"], str):
        return body["input"]
    c = body["input"][-1]["content"]
    return c if isinstance(c, str) else "".join(b["text"] for b in c)


class InProc:
    """A worker transport running request.run in a thread (the real provider classes, the stubbed connection)."""

    def __init__(self):
        self.requests = []

    def start(self, req):
        self.requests.append(req)
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("a", RQ.run(req)), daemon=True)
        t.start()

        class H:
            def done(self):
                return not t.is_alive()

            def cancel(self):
                pass

            def result(self):
                t.join()
                return box["a"]
        return H()


@pytest.mark.parametrize("route,module,answer,model", [
    ("openai", OP, openai_answer, "gpt-6-luna"), ("gemini", GP, gemini_answer, "gemini-3.5-flash-lite")])
def test_the_worker_splits_a_cut_answer_of_the_real_class(env, monkeypatch, route, module, answer, model):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-gemini")

    sizes = []

    def full(body):
        user = user_of(body)
        sizes.append(len(part_lines(user)))
        return None if sizes[-1] > 20 else json.dumps(honest(user))      # a long part is cut at the cap
    bodies = stub_urlopen(monkeypatch, module, answer(full))
    root = env
    big_file(root, lines=60)
    st = open_store(root)
    ingest(st, root)
    segs = SG.of_revision(st, st.one("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id"
                                     " WHERE s.uri='docs/big.md'")[0])
    assert len(segs) == 1 and len(segs[0]["snippet_ids"]) > 1, "the fixture must be one multi-snippet segment"
    tr = InProc()
    res = run_pass(st, root, tr, route=route, model=model)
    assert res["status"] == "done", res
    assert sizes[0] > 20 and len(tr.requests) > 2              # the first answer was cut, the worker split the part
    assert len(bodies) == len(tr.requests)                     # one HTTP request per worker request: never a second
    assert all(s <= sizes[0] for s in sizes[1:]) and st.one("SELECT count(*) FROM claim")[0] > 20
    assert st.one("SELECT count(*) FROM segment_cache WHERE seg_hash=?", (segs[0]["seg_hash"],))[0] == 1


# ---------------------------------------------------------------- Ollama: exact cap, cut answers

class OllamaStub(http.server.BaseHTTPRequestHandler):
    bodies = []
    reason = "length"

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models": []}')

    def do_POST(self):
        OllamaStub.bodies.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0))))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"message": {"content": CUT}, "done": False}).encode() + b"\n")
        self.wfile.write(json.dumps({"done": True, "done_reason": OllamaStub.reason, "prompt_eval_count": 900,
                                     "eval_count": 2000}).encode() + b"\n")


def test_ollama_gets_exactly_the_cap_and_reports_a_cut_answer(monkeypatch):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), OllamaStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("LT_OLLAMA_URL", "http://127.0.0.1:{}".format(srv.server_address[1]))
        OllamaStub.bodies.clear()
        out = RQ.run(req_for("local", "gemma4:26b"))
        assert OllamaStub.bodies[-1]["options"]["num_predict"] == 2000
        assert X.cut(out) and out["stop_reason"] == "length"
        # other jobs keep twice the cap and no stop reason (unchanged)
        c = RQ.leg("local").complete(model="gemma4:26b", system=[{"text": "s"}], user="x", max_tokens=2000,
                                     cache_ttl=None)
        assert OllamaStub.bodies[-1]["options"]["num_predict"] == 4000 and c.stop_reason is None
    finally:
        srv.shutdown()
        srv.server_close()


def test_local_model_not_installed_ends_the_pass_off(env):
    root = env
    many_sections(root, "docs/plan.md", 4)
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport(lambda req: {"error": "HTTPError: HTTP Error 404: Not Found", "text": ""})
    res = run_pass(st, root, tr, route="local")
    assert res["status"] == "off" and len(tr.requests) == 1


# ---------------------------------------------------------------- local overflow at a ceiling (sections 8.6, 9.1)

def test_local_overflow_goes_on_with_the_model_on_this_mac_only_when_turned_on(env):
    root = env
    many_sections(root, "docs/plan.md", 6)
    st = open_store(root)
    ingest(st, root)
    one = W.Pass(st, str(root), {"route": "api"}, FakeTransport()).estimate("x")
    lim = {"usd": {"pass": one * 2.5, "day": 100, "all_projects_day": 100}}
    off = run_pass(st, root, FakeTransport(out_tokens=2000), ceilings=lim)
    assert off["status"] == "ceiling" and off["route"] == "api"
    tr = FakeTransport(out_tokens=2000)
    many_sections(root, "docs/plan2.md", 6)
    ingest(st, root)
    on = run_pass(st, root, tr, ceilings=lim, local_overflow=True)
    routes = [r["route"] for r in tr.requests]
    assert on["status"] == "done" and on["route"] == "local" and on["fallback_from"] == "api"
    assert set(routes) == {"api", "local"} and routes.index("local") == routes.count("api")   # never back to the cloud
    assert on["usd"] is not None and 0 < on["usd"] <= lim["usd"]["pass"] + 1e-9           # the cloud part stays counted
    ledger = [json.loads(x) for x in W.ledger_path().read_text().splitlines()]
    assert {x["route"] for x in ledger} == {"api", "local"} and {x["job"] for x in ledger} == {"evidence"}
    # the project day still sees the cloud spend of the run that ended on local
    assert W.project_today(st, "api", "usd") >= off["usd"] + on["usd"] - 1e-9


def test_a_call_cancels_a_request_within_2_seconds_at_the_default_poll(env):
    """At the worker's default 1-second poll, from the moment call-active.json is created to the end of the pass."""
    import os
    import time as T
    root = env
    put_path = Path(root) / "docs" / "a.md"
    put_path.parent.mkdir(parents=True)
    put_path.write_text("# A\nAlpha line one is here.\n", encoding="utf-8")
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport(delay=10.0)
    made = {}

    def call():
        p = W.state_path().with_name("call-active.json")
        p.write_text(json.dumps({"pid": os.getpid()}))
        made["t"] = T.monotonic()
    threading.Timer(0.5, call).start()
    res = W.extract_pass(st, str(root), {"route": "api"}, tr, sleep=lambda s: None)   # poll_s 1.0 (the default)
    took = T.monotonic() - made["t"]
    assert res["status"] == "paused_call" and tr.cancelled == 1 and len(tr.requests) == 1
    assert took < 2.0, took


def seed_codex(pct):
    R.note_account_usage("codex", {"at": 0, "limited": False, "windows": [{"name": "five_hour", "used_percent": pct}]},
                         W.state_path())


def test_a_stale_plan_reading_still_counts_every_request(env):
    """ai-accounts.json holds an old codex reading and the answers carry no usage: each request counts the assumed
    percent (fails on a pass that reads 0 from the unchanged reading and runs every segment)."""
    root = env
    many_sections(root, "docs/plan.md", 10)
    st = open_store(root)
    ingest(st, root)
    seed_codex(20.0)
    assumed = float(X.load_rules()["pct_per_request_assumed"])
    tr = FakeTransport()
    res = run_pass(st, root, tr, route="codex", ceilings={"pct": {"pass": 4 * assumed, "day": 100,
                                                                  "all_projects_day": 100}})
    assert res["status"] == "ceiling" and len(tr.requests) == 4
    assert res["pct_after"] - res["pct_before"] == pytest.approx(4 * assumed)
    ledger = [json.loads(x) for x in W.ledger_path().read_text().splitlines()]
    assert [x["pct"] for x in ledger] == [assumed] * 4                 # the all-projects day cap sums these
    assert W.project_today(st, "codex", "pct") == pytest.approx(4 * assumed)


def test_usage_without_a_five_hour_window_counts_the_assumed_percent(env):
    """Review r1 (non-blocking): an answer whose usage has only a seven-day window replaces the route's reading, so
    the five-hour percent reads as unknown and the request counts the assumed percent, never 0."""
    root = env
    many_sections(root, "docs/plan.md", 3)
    st = open_store(root)
    ingest(st, root)
    seed_codex(20.0)
    assumed = float(X.load_rules()["pct_per_request_assumed"])
    tr = FakeTransport(lambda req: {"text": json.dumps(honest(req["user"])), "account_usage": {
        "at": 0, "limited": False, "windows": [{"name": "seven_day", "used_percent": 40.0}]}})
    res = run_pass(st, root, tr, route="codex")
    assert res["status"] == "done" and tr.requests
    assert [x["pct"] for x in ledger_rows()] == [assumed] * len(tr.requests)


def test_plan_percent_stays_counted_after_a_switch_to_local(env):
    root = env
    many_sections(root, "docs/plan.md", 6)
    st = open_store(root)
    ingest(st, root)
    seed_codex(20.0)
    used = [20.0]

    def plan(req):
        if req["route"] == "local":
            return {"text": json.dumps(honest(req["user"]))}
        used[0] += 3.0
        return {"text": json.dumps(honest(req["user"])), "account_usage": {
            "at": 0, "limited": False, "windows": [{"name": "five_hour", "used_percent": used[0]}]}}
    tr = FakeTransport(plan)
    res = run_pass(st, root, tr, route="codex", local_overflow=True,
                   ceilings={"pct": {"pass": 7, "day": 100, "all_projects_day": 100}})
    routes = [r["route"] for r in tr.requests]
    assert res["status"] == "done" and res["route"] == "local" and res["fallback_from"] == "codex"
    assert routes.count("codex") == 3 and set(routes) == {"codex", "local"}
    assert res["pct_after"] - res["pct_before"] == pytest.approx(9.0)
    assert W.project_today(st, "codex", "pct") == pytest.approx(9.0)


# ---------------------------------------------------------------- --extract calibration (recorded, not gated)

def test_extract_calibration_reports_quality_size_and_cost(tmp_path, monkeypatch):
    """Replays the checked-in authored answers through the worker as if a route sent them (no provider is called):
    the calibration fills precision, recall, hop and paraphrase rates, output tokens per segment, catalog tokens,
    segments per document and cost, and the recorded fixture gives C the same hop rate as the checked-in one."""
    import run as G0
    monkeypatch.setenv("LT_AI_STATE", str(tmp_path / "state" / "ai-routes.json"))
    (tmp_path / "state").mkdir()
    authored = G0.EXTRACT

    class Replay(FakeTransport):
        def answer(self, req):
            data = json.loads((authored / (req["seg_hash"] + ".json")).read_text(encoding="utf-8"))
            return {"text": data["answers"][0], "in_tokens": 1500, "out_tokens": 400, "cached": 0, "model": req["model"]}
    out = tmp_path / "extract"
    m = G0.record_extract("api", out_dir=out, transport=Replay())
    cal = m["calibration"]
    for k in ("precision", "recall", "hop_answer_rate", "paraphrase_answer_rate", "out_tokens_per_segment",
              "catalog_tokens_per_request", "segments_per_document", "usd", "seconds"):
        assert cal.get(k) is not None, k
    assert cal["measured"] is True and m["route"] == "api" and m["model"] == W.models("api")["extract"]
    assert cal["out_tokens_per_segment"] == 400.0 and cal["usd"] > 0
    assert 0 < cal["precision"] <= 100 and 0 < cal["recall"] <= 100
    assert cal["hop_answer_rate"]["n"] == 5 and cal["hop_answer_rate"]["of"] == 6    # as the authored fixture
    assert json.loads((out / "manifest.json").read_text())["calibration"]["precision"] == cal["precision"]


# ---------------------------------------------------------------- unpriced and swapped models (PB-103 review round 1)

def ledger_rows():
    return [json.loads(x) for x in W.ledger_path().read_text().splitlines()]


@pytest.mark.parametrize("via", ["settings", "map"])
def test_an_unpriced_model_on_a_usd_route_sends_nothing(env, tmp_path, via):
    """An unpriced name would price every request at 0 and turn all three USD caps off: the pass ends `off` first."""
    root = env
    many_sections(root, "docs/plan.md", 6)
    st = open_store(root)
    ingest(st, root)
    extra = {"model": "claude-unknown-9"}
    if via == "map":
        y = tmp_path / "ai.yaml"
        d = yaml.safe_load(AI_YAML.read_text(encoding="utf-8"))
        d["evidence"]["extract"]["api"] = "claude-unknown-9"
        y.write_text(yaml.safe_dump(d), encoding="utf-8")
        extra = {"ai_yaml": str(y)}
    lim = {"usd": {"pass": 0.0001, "day": 100, "all_projects_day": 100}}
    tr = FakeTransport(out_tokens=2000)
    res = run_pass(st, root, tr, ceilings=lim, **extra)
    assert res["status"] == "off" and "no price" in (res["error"] or "")
    assert tr.requests == [] and res["model"] == "claude-unknown-9"
    # local overflow does not turn an unpriced cloud model into a local pass: the PM must pick a priced model
    tr2 = FakeTransport(out_tokens=2000)
    res2 = run_pass(st, root, tr2, ceilings=lim, local_overflow=True, **extra)
    assert res2["status"] == "off" and tr2.requests == []


def test_the_estimate_of_an_unpriced_model_is_unknown_not_zero(env):
    root = env
    many_sections(root, "docs/plan.md", 3)
    st = open_store(root)
    ingest(st, root)
    out = W.estimate(st, str(root), "api", settings={"model": "claude-unknown-9"})
    assert out["segments"] > 0 and out["unpriced"] is True
    assert out["usd_typical"] is None and out["usd_cap"] is None
    ok = W.estimate(st, str(root), "api")                 # negative control: the map's priced model
    assert ok["unpriced"] is False and isinstance(ok["usd_cap"], float) and isinstance(ok["usd_typical"], float)


@pytest.mark.parametrize("route,module,answer,default", [
    ("openai", OP, openai_answer, OP.MODEL), ("gemini", GP, gemini_answer, GP.MODEL)])
def test_the_worker_prices_the_model_the_provider_sends(env, monkeypatch, route, module, answer, default):
    """OpenAIProvider and GeminiProvider send their default model for a name without their prefix; the worker prices,
    logs and records the model really sent, not the name it was given."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-gemini")
    urls = []
    if route == "gemini":
        real = GP.urllib.request.Request

        def spy(url, *a, **k):
            urls.append(url)
            return real(url, *a, **k)
        monkeypatch.setattr(GP.urllib.request, "Request", spy)
    bodies = stub_urlopen(monkeypatch, module, answer(lambda body: json.dumps(honest(user_of(body)))))
    root = env
    many_sections(root, "docs/plan.md", 2)
    st = open_store(root)
    ingest(st, root)
    asked = "claude-sonnet-5-5"                                     # priced, but not a name this provider sends
    assert COST.price_for(asked) != COST.price_for(default)
    tr = InProc()
    res = run_pass(st, root, tr, route=route, model=asked)
    assert res["status"] == "done" and bodies
    if route == "openai":
        assert {b["model"] for b in bodies} == {default}
    else:
        assert urls and all(default in u for u in urls)
    assert {r["model"] for r in tr.requests} == {default} and res["model"] == default
    rows = ledger_rows()
    assert {x["model"] for x in rows} == {default}
    want = sum(COST.cost_usd(default, x["input"], x["cache_write"], 0, x["cache_read"], x["output"]) for x in rows)
    assert want > 0 and res["usd"] == pytest.approx(want)


def test_spend_is_priced_on_the_model_the_answer_reports(env):
    root = env
    many_sections(root, "docs/plan.md", 2)
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport(lambda req: {"text": json.dumps(honest(req["user"])), "model": "claude-sonnet-5-5",
                                    "in_tokens": 1000, "out_tokens": 300})
    res = run_pass(st, root, tr, model="claude-haiku-5-5")
    n = len(tr.requests)
    assert res["status"] == "done" and n
    assert res["usd"] == pytest.approx(n * COST.cost_usd("claude-sonnet-5-5", 1000, 0, 0, 0, 300))
    assert {x["model"] for x in ledger_rows()} == {"claude-sonnet-5-5"}


def test_an_answer_from_an_unpriced_model_is_charged_its_estimate(env):
    root = env
    many_sections(root, "docs/plan.md", 2)
    st = open_store(root)
    ingest(st, root)
    tr = FakeTransport(lambda req: {"text": json.dumps(honest(req["user"])), "model": "", "in_tokens": 1000,
                                    "out_tokens": 300})
    res = run_pass(st, root, tr, model="claude-haiku-5-5")
    assert tr.requests and res["status"] == "done"
    # the empty name falls back to the model it asked for
    assert res["usd"] == pytest.approx(len(tr.requests) * COST.cost_usd("claude-haiku-5-5", 1000, 0, 0, 0, 300))
    tr2 = FakeTransport(lambda req: {"text": json.dumps(honest(req["user"])), "model": "claude-renamed-1",
                                     "in_tokens": 1000, "out_tokens": 300})
    put(root, "docs/other.md", "## Budget\nThe budget review moves to Friday.\n## Venue\nThe venue is in Denver.\n")
    ingest(st, root)
    res2 = run_pass(st, root, tr2, model="claude-haiku-5-5")
    assert tr2.requests and res2["status"] == "done"
    assert res2["usd"] > 0 and res2["usd"] == pytest.approx(res2["est_usd"])   # never priced at 0


# ---------------------------------------------------------------- the app's settings (PB-103 review round 1, PB-88)

# the exact shape of EvidencePrefs.limits (app/Sources/LiveTipsCore/Evidence.swift on main), as JSON
def app_limits(extract=True, local_overflow=False, usd=(1.0, 3.0, 3.0), pct=(10.0, 30.0, 30.0), mins=(120.0, 360.0, 360.0)):
    return json.dumps({"pass": {"usd": usd[0], "pct": pct[0], "min": mins[0]},
                       "day": {"usd": usd[1], "pct": pct[1], "min": mins[1]},
                       "all_day": {"usd": usd[2], "pct": pct[2], "min": mins[2]},
                       "topics": "lazy", "extract": extract, "local_overflow": local_overflow})


def cli_pass(monkeypatch, root, tr, route="api"):
    """`evidence pass` as the app starts it (no --settings-json, no --no-extract), the request child replaced by tr."""
    import io
    from livetips_m0.evidence import __main__ as CLI
    monkeypatch.setattr(W, "ChildTransport", lambda: tr)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    rc = CLI.main(["pass", "--project-uid", "p-app", "--root", str(root), "--route", route, "--no-embed"])
    return rc, json.loads(out.getvalue().strip().splitlines()[-1])


def test_ai_reading_off_in_the_app_sends_nothing(env, monkeypatch):
    many_sections(env, "docs/plan.md", 3)
    monkeypatch.setenv("LT_EVIDENCE_LIMITS", app_limits(extract=False))
    tr = FakeTransport()
    rc, out = cli_pass(monkeypatch, env, tr)
    assert rc == 0 and out["status"] == "done" and "extract" not in out
    assert tr.requests == []                         # no project text left for any AI
    monkeypatch.setenv("LT_EVIDENCE_LIMITS", app_limits(extract=True))
    rc, out = cli_pass(monkeypatch, env, tr)         # control: the same pass with AI reading on sends
    assert rc == 0 and out["extract"]["status"] == "done" and tr.requests


def test_the_apps_ceilings_reach_the_worker(env, monkeypatch):
    many_sections(env, "docs/plan.md", 8)
    st = open_store(env)
    one = W.Pass(st, str(env), {"route": "api"}, FakeTransport()).estimate("x")
    monkeypatch.setenv("LT_EVIDENCE_LIMITS", app_limits(usd=(one * 2.5, 7.0, 9.0), pct=(4.0, 20.0, 25.0)))
    c = W.Pass(st, str(env), {"route": "codex"}, FakeTransport()).ceilings()
    assert c == {"pass": 4.0, "day": 20.0, "all_projects_day": 25.0, "stop_above": 80}   # stop_above from the rules
    assert W.Pass(st, str(env), {"route": "local"}, FakeTransport()).ceilings()["day"] == 360.0
    st.close()
    tr = FakeTransport(out_tokens=2000)
    rc, out = cli_pass(monkeypatch, env, tr)
    ex = out["extract"]
    assert ex["status"] == "ceiling" and ex["ceiling_value"] == pytest.approx(one * 2.5)
    assert 1 <= len(tr.requests) <= 3                # the default $1 pass ceiling lets all 8 parts through


def test_unreadable_app_settings_send_nothing(env, monkeypatch):
    many_sections(env, "docs/plan.md", 2)
    monkeypatch.setenv("LT_EVIDENCE_LIMITS", "{not json")
    tr = FakeTransport()
    rc, out = cli_pass(monkeypatch, env, tr)
    assert out["extract"]["status"] == "failed" and "LT_EVIDENCE_LIMITS" in out["extract"]["error"]
    assert tr.requests == []


def test_local_off_in_the_chain_never_falls_back_to_local(env, monkeypatch):
    root = env
    many_sections(root, "docs/plan.md", 2)
    st = open_store(root)
    ingest(st, root)
    monkeypatch.setenv("LT_LOCAL_FALLBACK", "0")
    tr = FakeTransport(lambda req: {"error": "NoRoute: no API key", "text": ""})
    res = run_pass(st, root, tr, route="openai")
    assert res["route"] == "openai" and not res["fallback_from"]
    assert {r["route"] for r in tr.requests} == {"openai"}


def test_a_capped_provider_with_local_off_sends_nothing(env, monkeypatch):
    root = env
    many_sections(root, "docs/plan.md", 2)
    st = open_store(root)
    ingest(st, root)

    class Capped:
        def capped_route(self, route):
            return True
    monkeypatch.setattr(COST.ProviderCaps, "from_cfg", classmethod(lambda cls, cfg, sp=None: Capped()))
    monkeypatch.setenv("LT_LOCAL_FALLBACK", "0")
    tr = FakeTransport()
    res = run_pass(st, root, tr, route="api")
    assert res["status"] == "ceiling" and tr.requests == []
    monkeypatch.delenv("LT_LOCAL_FALLBACK")          # control: with local allowed the pass goes on there
    tr = FakeTransport()
    res = run_pass(st, root, tr, route="api")
    assert res["route"] == "local" and tr.requests and {r["route"] for r in tr.requests} == {"local"}


@pytest.mark.parametrize("cap", ["day", "all_projects_day", "pass_and_day"])
def test_local_overflow_only_at_the_pass_ceiling(env, cap):
    """Spec 8.6 and 9.1: overflow goes on with local at the pass ceiling; at a day cap the pass ends there, also
    when the pass ceiling is crossed at the same request."""
    root = env
    many_sections(root, "docs/plan.md", 6)
    st = open_store(root)
    ingest(st, root)
    one = W.Pass(st, str(root), {"route": "api"}, FakeTransport()).estimate("x")
    lim = {"pass": 100.0, "day": 100.0, "all_projects_day": 100.0}
    name = "day" if cap == "pass_and_day" else cap
    lim[name] = one * 2.5
    if cap == "pass_and_day":
        lim["pass"] = one * 2.5
    tr = FakeTransport(out_tokens=2000)
    res = run_pass(st, root, tr, ceilings={"usd": lim}, local_overflow=True)
    assert res["status"] == "ceiling" and res["error"] == name and res["route"] == "api"
    assert tr.requests and {r["route"] for r in tr.requests} == {"api"}


# ---------------------------------------------------------------- --extract all or nothing (PB-103 review round 1)

def snapshot(d: Path) -> dict:
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


@pytest.mark.parametrize("how", ["ceiling", "error", "not_connected"])
def test_a_partial_extract_run_leaves_the_fixture_as_it_was(tmp_path, monkeypatch, how):
    import shutil
    import run as G0
    monkeypatch.setenv("LT_AI_STATE", str(tmp_path / "state" / "ai-routes.json"))
    monkeypatch.delenv("LT_EVIDENCE_LIMITS", raising=False)
    monkeypatch.delenv("LT_LOCAL_FALLBACK", raising=False)
    (tmp_path / "state").mkdir()
    out = tmp_path / "extract"
    shutil.copytree(G0.EXTRACT, out)
    before = snapshot(out)
    authored = G0.EXTRACT
    n = {"sent": 0}

    class Partial(FakeTransport):
        def answer(self, req):
            n["sent"] += 1
            if req["route"] == "local":
                return {"text": "{}", "model": req["model"]}       # must never be recorded under the route
            if how == "not_connected":
                return {"error": "NoRoute: no API key", "text": ""}
            if how == "error" and n["sent"] == 7:
                return {"error": "HTTPError: HTTP Error 500: server error", "text": ""}
            data = json.loads((authored / (req["seg_hash"] + ".json")).read_text(encoding="utf-8"))
            return {"text": data["answers"][0], "in_tokens": 1500, "cached": 0, "model": req["model"],
                    "out_tokens": 2000 if how == "ceiling" else 400}
    if how == "ceiling":                             # a pass ceiling of about 2 answers at the cap ends passes early
        rules = X.load_rules()
        rules["ceilings"] = dict(rules["ceilings"], usd={"pass": 0.03, "day": 3.0, "all_projects_day": 3.0})
        monkeypatch.setattr(X, "load_rules", lambda *a, **k: rules)
    with pytest.raises(SystemExit) as e:
        G0.record_extract("api", out_dir=out, transport=Partial(), settings={"backoff0_s": 0})
    assert "nothing recorded" in str(e.value)
    assert {"ceiling": "ended ceiling", "error": "failed", "not_connected": "failed"}[how] in str(e.value)
    assert n["sent"] > 0
    assert snapshot(out) == before                   # byte for byte, manifest included
    assert sorted(p.name for p in tmp_path.iterdir()) == ["extract", "state"]   # no temporary folder left


def test_a_complete_extract_run_with_a_repair_and_a_cut_answer_replaces_the_fixture(tmp_path, monkeypatch):
    """The answers the worker did not keep (an unparsable first answer, a cut one) are not recorded, so a complete run
    is swapped in: old files gone, the new manifest measured."""
    import shutil
    import run as G0
    monkeypatch.setenv("LT_AI_STATE", str(tmp_path / "state" / "ai-routes.json"))
    monkeypatch.delenv("LT_EVIDENCE_LIMITS", raising=False)
    (tmp_path / "state").mkdir()
    out = tmp_path / "extract"
    shutil.copytree(G0.EXTRACT, out)
    (out / "stale-authored.json").write_text("{}")   # a file no new run writes: must be gone after the swap
    authored = G0.EXTRACT
    n = {"sent": 0}

    class Replay(FakeTransport):
        def answer(self, req):
            n["sent"] += 1
            data = json.loads((authored / (req["seg_hash"] + ".json")).read_text(encoding="utf-8"))
            if n["sent"] == 1:
                return {"text": "not json at all", "in_tokens": 1500, "out_tokens": 5, "model": req["model"]}
            if n["sent"] == 3:
                return {"text": data["answers"][0][:40], "stop_reason": "max_tokens", "in_tokens": 1500,
                        "out_tokens": 2000, "model": req["model"]}
            return {"text": data["answers"][0], "in_tokens": 1500, "out_tokens": 400, "model": req["model"]}
    m = G0.record_extract("api", out_dir=out, transport=Replay(), settings={"backoff0_s": 0})
    assert m["calibration"]["measured"] is True and m["calibration"]["fixture_misses"] == 0
    assert not (out / "stale-authored.json").exists() and json.loads((out / "manifest.json").read_text())["route"] == "api"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["extract", "state"]
