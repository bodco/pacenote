#!/usr/bin/env python3
"""PB-50 GraphRAG G0: a synthetic benchmark of the call context, so every later GraphRAG step (G1 identity, G2 evidence
store, G3 preparation, G4 live, G5 after the call) shows with numbers whether the context got better.

Offline and deterministic: no model call, no network but the fixture site the harness serves on 127.0.0.1. It replays
three target calls of fixtures/northpoint (invented data, two clients that both have an "item 13") through the code the
app runs today and measures what the engine assembles BEFORE the model:

  prep     sheet.build (route codex, the real per-pass budget) with a responder that only records the packed prompt;
           gold facts found in its ## Sources = coverage, the other client's facts there = scope leaks
  live     lookup.snippets over the background FolderIndex, as engine._project_snippets runs it without lt and without
           the model's extra queries; the expected source in the top N = answer rate; p50/p95 of the warm lookup
  after    commitments.record_notes with the call's notes into the register; duplicates of an existing item, items of
           the other client's "item 13" in the call's Tracker, "we will try" items that stand as open commitments.
           Since G1 (PB-51) record_notes first migrates the fixture register once (as the app does on its first write),
           and the identity suggestions it leaves to the PM are counted, with the duplicates left if the PM confirms all

  python3 bench/g0/run.py --variant A            # prints the table, writes results/A-<date>.json (never over an existing one; --tag x: A-<date>-x.json)
  python3 bench/g0/run.py --variant B --set small   # gold v2 on the small set only (default: small and scale)
  python3 bench/g0/run.py --compare A C          # the newest result of each, metric by metric
  python3 bench/g0/run.py --regen-register       # rebuild fixtures/.../live/commitments.jsonl from the past notes

Variant A = the current code. PB-52 G2 (S2) adds B (the evidence store's snippets and FTS5 over every call and file,
scope and time on, no entities, edges or topics) and C (B plus entities, claims, edges, hops; its extraction comes
from the checked-in fixture keyed by seg_hash, recorded in S3), and gold v2 (gold_v2.yaml, spec section 14.2) on the
small set and on the seeded scale set. Every variant answers the v1 targets (the existing columns: B and C replace
the live lookup only, preparation and after-call stay A's until G3) and every gold v2 row (the new columns).
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
M0 = HERE.parents[1]
if str(M0) not in sys.path:
    sys.path.insert(0, str(M0))

import yaml  # noqa: E402

from livetips_m0 import call_layout as CL  # noqa: E402
from livetips_m0 import client_scope as CS  # noqa: E402
from livetips_m0 import commitments as CM  # noqa: E402
from livetips_m0.evidence import __main__ as ECLI  # noqa: E402
from livetips_m0.evidence import extract as EX  # noqa: E402
from livetips_m0.evidence import ingest as IG  # noqa: E402
from livetips_m0.evidence import worker as W  # noqa: E402
from livetips_m0.evidence import search as SE  # noqa: E402
from livetips_m0.evidence import segments as SG  # noqa: E402
from livetips_m0.evidence import store as ES  # noqa: E402
from livetips_m0.evidence import topics as TP  # noqa: E402
from livetips_m0.sheet import build as B  # noqa: E402
from livetips_m0.sheet import lookup as LK  # noqa: E402
from livetips_m0.sheet import tracker as TR  # noqa: E402

FIX = HERE / "fixtures"
PROJECT = FIX / "northpoint"
GOLD = HERE / "gold.yaml"
RESULTS = HERE / "results"
GOLD2 = HERE / "gold_v2.yaml"
OVERLAY = FIX / "v2"                    # gold v2 files over the northpoint copy, the register additions, the Tracker
REVISIONS = FIX / "revisions"           # the revision states of section 14.1 and revisions.yaml
EXTRACT = FIX / "extract"               # C's extraction fixture, <seg_hash>.json (recorded in S3)
V2_DIRS = ("v2", "revisions", "extract", "topics")   # outside fixture_sha, so the v1 columns keep their hash
READY = {"S2"}          # the sub-tasks whose code C has: S3 adds "S3" with the extraction, S4 adds "S4" with topics
MANIFEST = EXTRACT / "manifest.json"    # the recorded extraction fixture: gold sha256, route, model, calibration
TOPICS = FIX / "topics"                 # S4: C's topic summaries, <sha256 of the request text>.json (--record-topics)
SUMMARY_AT = "2026-11-22T00:00:00Z"     # S4: the bench writes summaries after the last revision state (Nov 21)


def ready_set(fixture_dir: Optional[Path] = None, topics_dir: Optional[Path] = None) -> set:
    """S3 counts as ready for C only with a recorded extraction fixture (raw model output, recorded by --extract);
    S4 only with S3 on the checked-in extraction fixture and a summary fixture recorded on it (--record-topics): an
    --extract calibration replays its own new extraction, which no summary fixture was recorded against."""
    m = (fixture_dir / "manifest.json") if fixture_dir else MANIFEST
    out = READY | ({"S3"} if m.is_file() else set())
    tm = (topics_dir or TOPICS) / "manifest.json"
    if "S3" in out and Path(fixture_dir or EXTRACT) == EXTRACT and tm.is_file() and \
            json.loads(tm.read_text(encoding="utf-8")).get("extract_sha") == tree_sha(EXTRACT):
        out.add("S4")       # a re-recorded extraction leaves S4 rows not_evaluated (the S4 gate fails) until re-recorded
    return out
SETS = ("small", "scale")
SEED = 52               # the scale set's noise generator
VARIANTS = ("A", "B", "C")
LOOKUP_REPS = 5
DATE_RX = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
EMPTY_ANSWER = json.dumps({"goal": "", "blocks": {}})


# ---------------------------------------------------------------- helpers

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def has_phrase(text: str, phrases: List[str]) -> bool:
    """Any phrase in the text: lower case, whitespace collapsed, on word boundaries."""
    t = norm(text)
    return any(re.search(r"(?<!\w)" + re.escape(norm(p)) + r"(?!\w)", t) for p in phrases)


def load_gold(path: Path = GOLD) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def tree_sha(*roots: Path, skip=()) -> str:
    h = hashlib.sha256()
    for root in roots:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file()
                                                     and p.relative_to(root).parts[0] not in skip)
        for p in files:
            h.update(str(p.relative_to(root.parent)).encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def quantile(xs: List[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[i], 2)


def set_dates(root: Path, file_dates: Dict[str, str]) -> None:
    """Every file gets a fixed mtime (the date in its path, else gold file_dates, else 2026-11-01), so the folder window,
    the index order and the passage dates do not depend on the day of the checkout."""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        day = file_dates.get(rel)
        if not day:
            m = DATE_RX.search(rel)
            day = m.group(0) if m else "2026-11-01"
        ts = dt.datetime.fromisoformat(str(day) + "T12:00:00").timestamp()
        os.utime(p, (ts, ts))


@contextlib.contextmanager
def offline():
    """Any connection to a host other than the loopback fails (and is counted): the run stays offline."""
    real = socket.create_connection
    blocked: List[str] = []

    def guard(address, *a, **kw):
        host = str(address[0])
        if host not in ("127.0.0.1", "localhost", "::1"):
            blocked.append(host)
            raise OSError("G0 bench is offline: connection to " + host + " blocked")
        return real(address, *a, **kw)
    socket.create_connection = guard
    saved = {k: os.environ.pop(k) for k in ("LT_AI_STATE", "ANTHROPIC_API_KEY", "CLAUDE_API", "OPENAI_API_KEY",
                                            "GEMINI_API_KEY") if k in os.environ}
    # PB-37 refuses loopback pages outside tests; the fixture site is served on 127.0.0.1 by this harness
    loop = os.environ.get("PACENOTE_TEST_WEB_LOOPBACK")
    os.environ["PACENOTE_TEST_WEB_LOOPBACK"] = "1"
    try:
        yield blocked
    finally:
        socket.create_connection = real
        os.environ.update(saved)
        if loop is None:
            os.environ.pop("PACENOTE_TEST_WEB_LOOPBACK", None)
        else:
            os.environ["PACENOTE_TEST_WEB_LOOPBACK"] = loop


@contextlib.contextmanager
def site_server(directory: Path):
    """The fixture site on 127.0.0.1 with an ephemeral port, in a daemon thread."""
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(directory)))
    th = threading.Thread(target=srv.serve_forever, name="g0-site", daemon=True)
    th.start()
    try:
        yield "http://127.0.0.1:{}/".format(srv.server_address[1])
    finally:
        srv.shutdown()
        srv.server_close()


@contextlib.contextmanager
def clock(day_iso: str):
    """commitments.now_iso fixed to the call's day, so the register's created dates (and the due dates the Tracker
    reads from them) are the same on every run."""
    real = CM.now_iso
    CM.now_iso = lambda: day_iso
    try:
        yield
    finally:
        CM.now_iso = real


def fresh_copy(tmp: Path, gold: dict) -> Path:
    root = tmp / gold["project"]
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(PROJECT, root)
    set_dates(root, {str(k): str(v) for k, v in (gold.get("file_dates") or {}).items()})
    return root


def rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return Path(path).name


def stamp(start: str) -> str:
    return start[:16].replace("T", "-").replace(":", "")


# ---------------------------------------------------------------- variant A: the current code

class VariantA:
    """The code the app runs today. Later variants override prep_context, lookup and after_call."""
    name = "A"

    def prep_context(self, req: dict) -> dict:
        prompts: List[str] = []
        size: dict = {}

        def responder(system: str, user: str) -> str:
            prompts.append(user)
            return EMPTY_ANSWER

        def emit(**kw):
            if kw.get("stage") == "size" and "per_pass" in kw:
                size.update({k: kw[k] for k in ("state", "total_tokens", "per_pass")})
        res = B.build(req, emit=emit, responder=responder)
        sources = "\n".join(u.split("## Sources\n", 1)[1] if "## Sources\n" in u else "" for u in prompts)
        try:            # the call's sheet, as engine._sheet() finds it next to the brief
            sheet = json.loads(CL.sidecar(B.base_of(req), ".sheet.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sheet = None
        return {"context": sources, "passes": len(prompts), "size": size, "sheet": sheet,
                "error": res.get("error") if isinstance(res, dict) else None}

    def lookup_index(self, root: Path, exclude_prefix: str, sheet: Optional[dict]):
        """engine._folder_index: the project root, the sheet's folder sources in full (deep), the call's client scope
        the sheet keeps (PB-70), built once."""
        return LK.FolderIndex([str(root)], exclude_prefix=exclude_prefix, deep=LK.sheet_folder_roots(sheet),
                              scope=CS.from_sheet(sheet)).build()

    def lookup(self, question: str, root: Path, exclude_prefix: str, sheet: Optional[dict], index) -> List[dict]:
        """engine._project_snippets > own_search with no lt and no model queries (k 6, 14000 chars, 1 s)."""
        return LK.snippets(question, sheet=sheet, roots=[str(root)], k=6, queries=[], budget_chars=14000,
                           max_seconds=1.0, exclude_prefix=exclude_prefix, index=index)

    def after_call(self, live: Path, call: str, notes: str, aliases: List[str], sheet: Optional[dict] = None) -> List[dict]:
        """engine.write_register: the call's client is the scope the sheet keeps (PB-70), "" when none."""
        client = (CS.from_sheet(sheet) or {}).get("client") or ""
        return CM.record_notes(str(live), call=call, notes=notes, aliases=aliases, people={},   # not this Mac's voices
                               client=client)


def store_hit(item: dict, root: Path) -> dict:
    """A search item as run_live reads a passage: the file path under the project (a Tracker or register row keeps its
    uri) and every text it returns (the snippet, or the quotes of a claim or edge)."""
    uri = item.get("source") or ""
    text = "\n".join([item.get("text") or ""] + [q.get("quote") or "" for q in item.get("quotes") or []])
    if item.get("kind") == "claim" and item.get("value"):
        text += "\n" + str(item["value"])
    path = str(root / uri) if uri and not uri.startswith(("/", "notion:", "crm:")) else uri
    return {"path": path, "src": uri, "text": text, "said_at": item.get("said_at"), "kind": item.get("kind")}


class VariantB(VariantA):
    """Wider text search without a graph (spec section 14.1): the evidence store's snippets over every call and file
    of the project, no 7-day window, scope and time filters on, no entities, edges or topics. Preparation and after-call
    stay A's (the store reaches them in G3)."""
    name = "B"
    kinds = ("snippet",)

    def lookup_index(self, root: Path, exclude_prefix: str, sheet: Optional[dict]):
        self.stores = getattr(self, "stores", 0) + 1         # one store per target copy
        st = ES.Store.open(uid="g0-v1-{}".format(self.stores), root=str(root))
        IG.ingest(st, str(root), embedder=None, voices={}, quiet_s=0)
        # the call's own files (its prep sheet and brief), which A leaves out by exclude_prefix: the request's exclude
        own = [r[0] for r in st.q("SELECT uri FROM source WHERE instr(uri, ?) > 0", (exclude_prefix.rstrip("."),))]
        return {"store": st, "scope": CS.from_sheet(sheet), "exclude": own}

    def lookup(self, question: str, root: Path, exclude_prefix: str, sheet: Optional[dict], index) -> List[dict]:
        res = SE.search(index["store"], question, scope=index["scope"], exclude=index["exclude"], kinds=self.kinds,
                        mode="live")
        return [store_hit(i, root) for i in res["items"]]


class VariantC(VariantB):
    """B plus entities, claims, edges and hops (spec section 14.1). Its claims and edges of files come from the
    extraction fixture (S3); until then C is B plus the deterministic entities of the register, the Tracker, notes and
    sidecars."""
    name = "C"
    kinds = ("snippet", "claim", "edge")


def variant(name: str):
    if name == "A":
        return VariantA()
    if name == "B":
        return VariantB()
    if name == "C":
        return VariantC()
    raise SystemExit("variant {} is not implemented in G0".format(name))


# ---------------------------------------------------------------- one target call

def client_of_series(series: str, gold: dict) -> str:
    s = (series or "").split("|")[-1]
    for c, info in gold["clients"].items():
        if any(s == x for x in info.get("series") or []):
            return c
    return ""


def other_tags(client: str, gold: dict) -> List[str]:
    return [info["path_tag"] for c, info in gold["clients"].items() if c != client]


def build_req(t: dict, gold: dict, root: Path, site: str) -> dict:
    srcs = [{"id": "s-folder", "type": "folder", "path": str(root), "title": gold["project"]}]
    if t.get("web"):
        srcs.append({"id": "s-site", "type": "web", "uri": site + "index.html", "title": "Acme status page"})
    return {"route": gold.get("route", "codex"), "language": "en", "preset": gold.get("preset", "client_sync"),
            "today": t["start"][:10], "meeting": {"title": t["title"], "start": t["start"]},
            "participants": {"ours": [{"name": gold["aliases"][0]}], "theirs": list(t.get("theirs") or [])},
            "project": {"slug": gold["project"], "root": str(root)}, "sources": srcs,
            "on_too_much": "passes", "control_wait_s": 0}


def run_prep(v, t: dict, gold: dict, root: Path, site: str) -> dict:
    req = build_req(t, gold, root, site)
    got = v.prep_context(req)
    ctx = got["context"]
    facts = [{"id": f["id"], "present": has_phrase(ctx, f["any"]), "from": f.get("from", "")} for f in t["prep_facts"]]
    leaks = [f["id"] for f in gold["forbidden"].get(t["client"], []) if has_phrase(ctx, f["any"])]
    hit = sum(1 for f in facts if f["present"])
    return {"coverage": pct(hit, len(facts)), "found": hit, "total": len(facts), "facts": facts, "leaks": leaks,
            "passes": got["passes"], "size": got["size"], "context_chars": len(ctx), "error": got["error"],
            "_sheet": got["sheet"]}


def run_live(v, t: dict, gold: dict, root: Path, sheet: Optional[dict]) -> dict:
    prefix = t["call"][: -len(".md")] + "."
    t0 = time.perf_counter()
    index = v.lookup_index(root, prefix, sheet)
    build_ms = (time.perf_counter() - t0) * 1000
    top = int(gold.get("top", 3))
    tags = other_tags(t["client"], gold)
    rows, samples = [], []
    for q in t["live_questions"]:
        hits = []
        for _ in range(LOOKUP_REPS):
            s = time.perf_counter()
            hits = v.lookup(q["q"], root, prefix, sheet, index)
            samples.append((time.perf_counter() - s) * 1000)
        paths = [rel(h.get("path") or "", root) if h.get("path") else h.get("src", "") for h in hits[:top]]
        ok = any(any(e in p for e in q["expect"]) for p in paths)
        answer = any(has_phrase(h.get("text", ""), [q["answer"]]) for h in hits[:top])
        leak = [p for p in paths if any(tag in p.lower() for tag in tags)]
        rows.append({"q": q["q"], "ok": ok, "answer_in_top": answer, "top": paths, "leaks": leak})
    s = time.perf_counter()
    LK.snippets(t["live_questions"][0]["q"], sheet=sheet, roots=[str(root)], k=6, max_seconds=1.0, exclude_prefix=prefix)
    cold_ms = (time.perf_counter() - s) * 1000
    ok = sum(1 for r in rows if r["ok"])
    text_ok = sum(1 for r in rows if r["answer_in_top"])
    return {"answer_rate": pct(ok, len(rows)), "answered": ok, "total": len(rows), "questions": rows,
            "answer_text_rate": pct(text_ok, len(rows)), "answer_text": text_ok,
            "leaks": sum(len(r["leaks"]) for r in rows), "p50_ms": quantile(samples, 0.5),
            "p95_ms": quantile(samples, 0.95), "index_build_ms": round(build_ms, 1), "cold_ms": round(cold_ms, 1),
            "_samples": samples}


def matching(items: List[dict], match: List[str], status: str) -> List[dict]:
    return [i for i in items if i.get("status") == status and all(has_phrase(i.get("text", ""), [m]) for m in match)]


def open_matching(items: List[dict], match: List[str]) -> List[dict]:
    return matching(items, match, "open")


def run_after(v, t: dict, gold: dict, root: Path, sheet: Optional[dict] = None) -> dict:
    live = root / "live"
    notes = (FIX / t["dir"] / t["call"].replace(".md", ".notes.md")).read_text(encoding="utf-8")
    before = {i["id"] for i in CM.load(str(live))}
    with clock(t["start"] + "+00:00"):
        events = v.after_call(live, t["call"], notes, list(gold["aliases"]), sheet)
    items = CM.load(str(live))
    by_id = {i["id"]: i for i in items}
    added = {e["id"] for e in events if e.get("op") == "add"}
    touched = {e["id"] for e in events if e.get("op") != "add"}
    rows, dups, wrong, false_agreed, false_proposal = [], 0, 0, 0, 0
    reg = CM.read_events(str(live))
    sugg = CM.suggestions(reg)          # G1: what the PM is asked ("is it the same?"); confirming all of them:
    confirmed = CM.fold(reg + [{"op": "same", "id": x["id"], "to": x["to"], "by": "pm"} for x in sugg])
    dups_confirmed = sum(max(0, len(open_matching(confirmed, a["match"])) - 1) for a in t["after_call"])
    for a in t["after_call"]:
        m = open_matching(items, a["match"])
        old = [i for i in m if i["id"] in before]
        new = [i for i in m if i["id"] in added]
        dups += max(0, len(m) - 1)
        if a["kind"] == "update":
            ok = len(m) == 1 and bool(old) and not new
        elif a["kind"] == "new":
            ok = len(m) == 1 and bool(new)
        else:
            ok = len(m) <= 1
        tentative_open = a.get("modality") == "tentative" and bool(new if a["kind"] != "existing" else m)
        false_agreed += int(tentative_open)
        firm = 0 if a.get("modality") == "tentative" else len(matching(items, a["match"], "proposal"))
        false_proposal += firm      # PB-51: an agreed task read as "we will try" leaves every owed list
        wrong += int(not ok)
        rows.append({"id": a["id"], "kind": a["kind"], "ok": ok, "open_items": len(m), "added": len(new),
                     "tentative_open": tentative_open, "firm_as_proposal": firm})
    other = [c for c in gold["clients"] if c != t["client"]]
    cross_events = sum(1 for i in touched if client_of_series((by_id.get(i) or {}).get("series", ""), gold) in other)
    tr = TR.collect({"project": {"root": str(root)}, "today": t["start"][:10]})
    cross_tracker = sum(1 for x in tr["on_me"] + tr["waiting"]
                        if has_phrase(x["text"], ["item 13"]) and client_of_series(x.get("series", ""), gold) in other)
    return {"duplicates": dups, "wrong_identity": wrong, "false_agreed": false_agreed, "false_proposal": false_proposal,
            "suggested": len(sugg),
            "duplicates_if_confirmed": dups_confirmed,
            "item13_cross_client": cross_events + cross_tracker, "item13_cross_events": cross_events,
            "item13_in_tracker": cross_tracker, "items": rows, "events": len(events)}


# ---------------------------------------------------------------- the fixture's own promises

def corpus() -> str:
    """Every text of the fixture project and the site (what any variant could ever find)."""
    files = [p for p in sorted(PROJECT.rglob("*")) + sorted((FIX / "site").rglob("*")) if p.is_file()]
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def check_invariants(gold: dict) -> List[str]:
    """What the fixture must hold for the scores to mean anything (not the scores). [] = fine."""
    bad = []
    inv = gold["invariants"]

    def holds(spec: dict) -> bool:
        p = PROJECT / spec["file"]
        return p.is_file() and has_phrase(p.read_text(encoding="utf-8"), spec["any"])
    named = [("item13." + k, s) for k, s in inv["item13"].items()] + \
            [("cross_series." + k, s) for k, s in inv["cross_series"].items()] + \
            [("mixed_language", inv["mixed_language"]), ("we_will_try", inv["we_will_try"])] + \
            [("repeated_task.{}".format(i), s) for i, s in enumerate(inv["repeated_task"])]
    bad += ["{} not in {}".format(n, s["file"]) for n, s in named if not holds(s)]
    if inv["item13"]["acme"]["file"] == inv["item13"]["globex"]["file"]:
        bad.append("the two item 13 meanings must live in different calls")
    if has_phrase((PROJECT / inv["item13"]["acme"]["file"]).read_text(encoding="utf-8"), inv["item13"]["globex"]["any"]):
        bad.append("the Acme item 13 call also holds the Globex meaning")
    text = corpus()
    for t in gold["targets"]:
        bad += ["{} › {}: no fixture file holds it".format(t["id"], f["id"]) for f in t["prep_facts"] if not has_phrase(text, f["any"])]
        bad += ["{} › live \"{}\": answer not in the fixture".format(t["id"], q["q"]) for q in t["live_questions"]
                if not has_phrase(text, [q["answer"]])]
        for k in ("call", "call_notes"):
            p = FIX / t["dir"] / (t["call"] if k == "call" else t["call"].replace(".md", ".notes.md"))
            if not p.is_file():
                bad.append("{}: {} missing".format(t["id"], p.name))
        if list((PROJECT / "live").glob("*/" + t["call"])):
            bad.append("{}: the target call must not be in the project before it happens".format(t["id"]))
    return bad


# ---------------------------------------------------------------- gold v2 (PB-52 G2 S2): its promises

CONTENT_STOP = set(LK.STOP) | {"is", "a", "an", "of", "to", "in", "on", "at", "by", "be", "we", "us", "so", "far", "go",
                               "out", "up", "has", "have", "can", "who", "what", "when", "how", "before", "run"}


def words(text: str) -> set:
    return {w.lower() for w in re.findall(r"[^\W_]+", text or "", re.U)}


def v2_files() -> List[Path]:
    return [p for root in (PROJECT, OVERLAY, REVISIONS) for p in sorted(root.rglob("*")) if p.is_file()]


def gold_phrases(gold2: dict) -> List[str]:
    """Every phrase gold v2 looks for (answers, leaks, other clients' and excluded facts)."""
    out = []
    for r in gold2.get("rows") or []:
        out += [p for f in r.get("expect") or [] for p in f["any"]]
        out += [p for v in (r.get("leak") or {}).values() for p in v] + list(r.get("history") or [])
    out += [p for fs in (gold2.get("forbidden") or {}).values() for f in fs for p in f]
    out += [p for x in gold2.get("excluded_facts") or [] for p in x["any"]]
    return sorted(set(out))


def check_v2_invariants(gold2: dict, gold: dict) -> List[str]:
    """What gold v2 must hold for its rates to mean anything (spec section 14.2). [] = fine."""
    bad = []
    rows = gold2.get("rows") or []
    if gold2.get("version") != 2:
        bad.append("gold_v2.yaml must say version: 2")
    ids = [r.get("id") for r in rows]
    if len(ids) != len(set(ids)):
        bad.append("row ids are not unique")
    for r in rows:
        exp = r.get("expected") or {}
        if r.get("needs") not in ("S2", "S3", "S4"):
            bad.append("{}: needs must be S2, S3 or S4".format(r.get("id")))
        if set(exp) != set(VARIANTS) or any(x not in ("pass", "fail") for x in exp.values()) \
                or not str(r.get("why") or "").strip():
            bad.append("{}: expected pass or fail for A, B and C, and why".format(r.get("id")))
        if not (r.get("q") and r.get("kind") and r.get("client") in gold2["forbidden"]):
            bad.append("{}: q, kind and a client of `forbidden` are required".format(r.get("id")))
    hops = [r for r in rows if r.get("kind") == "hop"]
    if len(hops) < 6 or sum(1 for r in hops if r["client"] == "globex") < 2 or sum(1 for r in hops if r.get("hops") == 2) < 2:
        bad.append("item 1 needs at least 6 multi-hop rows, 2 of them for Globex and 2 with 2 hops")
    for r in hops:
        own = (words(r["q"]) - CONTENT_STOP) - words(r.get("start") or "")
        if len(r.get("expect") or []) < 2:
            bad.append("{}: a multi-hop answer needs quotes from at least 2 sources".format(r["id"]))
        for f in r.get("expect") or []:
            for ph in f["any"]:
                if own & words(ph):
                    bad.append("{}: quote \"{}\" shares {} with the question".format(r["id"], ph, sorted(own & words(ph))))
    texts = [(p, p.read_text(encoding="utf-8", errors="replace")) for p in v2_files()]
    corpus_text = "\n".join(t for _p, t in texts)
    for r in rows:
        for f in r.get("expect") or []:
            if not has_phrase(corpus_text, f["any"]):
                bad.append("{}: no fixture file holds {}".format(r["id"], f["any"]))
    for x in gold2.get("excluded_facts") or []:
        holders = [str(p) for p, t in texts if has_phrase(t, x["any"])]
        if not holders or any(x["in"] not in h for h in holders):
            bad.append("excluded fact {} must be only in {} (found in {})".format(x["id"], x["in"], holders))
    reg = (PROJECT / "live" / CM.FILE).read_text(encoding="utf-8") + (OVERLAY / "register-extra.jsonl").read_text(encoding="utf-8")
    for x in gold2.get("excluded_facts") or []:
        if x["how"] == "hidden" and has_phrase(reg, x["any"]):
            bad.append("the hidden call's fact must not be a register item (register events of a hidden call stay searchable)")
    revs = yaml.safe_load((REVISIONS / "revisions.yaml").read_text(encoding="utf-8"))
    for rel_path, states in revs["files"].items():
        bad += ["revision state {} of {} missing".format(s["state"], rel_path) for s in states
                if not (REVISIONS / s["state"]).is_file()]
        if (OVERLAY / "files" / rel_path).exists() or (PROJECT / rel_path).exists():
            bad.append("{} must come only from its revision states".format(rel_path))
    files, _gen = scale_set(SEED)
    noise = "\n".join(files.values())
    bad += ["the scale set's noise holds the gold phrase \"{}\"".format(p) for p in gold_phrases(gold2) if has_phrase(noise, [p])]
    return bad


# ---------------------------------------------------------------- gold v2: the scale set

SCALE_PARTS = ["atlas cache", "cobalt queue", "harbor relay", "juniper scheduler", "maple ledger", "onyx mirror",
               "quartz indexer", "raven notifier", "sierra uploader", "tundra router", "willow parser", "amber poller",
               "cedar vault", "delta tagger", "ember collector", "fjord watcher"]
SCALE_VERBS = ["feeds", "replaces", "monitors", "batches requests for", "retries calls to", "caches results of",
               "wraps", "throttles", "audits", "warms up"]
SCALE_PEOPLE = ["Ashley", "Tyler", "Megan", "Justin", "Lauren", "Ryan", "Nicole", "Austin"]
SCALE_UNITS = ["tickets", "batches", "nodes", "requests", "retries", "jobs"]
SCALE_TAILS = ["before the next release", "during the overnight window", "for the support desk", "when the queue backs up",
               "after the config review", "for the pilot group", "under the current budget", "behind the load balancer"]


def scale_set(seed: int = SEED):
    """The scale set (spec section 14.2 item 7): about 2 MB of plausible project text, 40 documents (16 Acme, 16 Globex, 8
    shared) and 10 calls, from one seed; the same seed gives the same bytes. Also each document's extraction output,
    built from the template it filled: perfect by construction and marked generated, so it measures ranking, latency
    and leaks under distractors, never extraction quality (no hop or paraphrase gate reads it).
    -> ({rel path: text}, {rel path: extraction})"""
    rng = random.Random(seed)
    files: Dict[str, str] = {}
    gen: Dict[str, dict] = {}

    def sentence(client: str):
        a, b = rng.sample(SCALE_PARTS, 2)
        s = "The {} {} the {} {} ({} {}, {}).".format(a, rng.choice(SCALE_VERBS), b, rng.choice(SCALE_TAILS),
                                                      rng.randint(3, 97), rng.choice(SCALE_UNITS), rng.choice(SCALE_PEOPLE))
        if client and rng.random() < 0.2:
            s = s[:-1] + " for {}.".format(client)
        return a, b, s

    plan = [("acme", "Acme", "docs/acme/scale/acme-scale-{:02d}.md", 16), ("globex", "Globex", "docs/globex/scale/globex-scale-{:02d}.md", 16),
            ("", "", "docs/scale/shared-scale-{:02d}.md", 8)]
    for _label, client, pattern, n in plan:
        for i in range(1, n + 1):
            rel_path = pattern.format(i)
            lines = ["# {} operations notes {:02d}".format(client or "Shared", i), ""]
            ents, claims = {}, []
            for sec in range(1, 13):
                part = rng.choice(SCALE_PARTS)
                lines += ["## {} review {}".format(part.title(), sec), ""]
                for _p in range(6):
                    for _s in range(7):
                        a, b, s = sentence(client)
                        lines.append("- " + s)
                        for x in (a, b):
                            ents.setdefault(x, {"ref": x, "type": "system", "name": x, "quote": x})
                        if len(claims) < 40:
                            claims.append({"subject": a, "predicate": "works_with", "value": b, "modality": "statement",
                                           "quote": s[:-1], "confidence": 1.0})
                    lines.append("")
            files[rel_path] = "\n".join(lines) + "\n"
            gen[rel_path] = {"generated": True, "seed": seed, "doc": rel_path,
                             "items": {"entities": [ents[k] for k in sorted(ents)], "claims": claims}}
    for i in range(1, 11):
        client, series, clock = ("Acme", "acme-weekly-status", "1000") if i <= 5 else ("Globex", "globex-weekly", "1100")
        day = "2026-09-{:02d}".format(i * 2)
        stem = "{}-{}-{}".format(day, clock, series)
        lines = ["# Live transcript: " + stem, "", "- Project: northpoint",
                 "- Start: {} {}:{}:00".format(day, clock[:2], clock[2:]), ""]
        for n in range(1, 211):
            _a, _b, s = sentence(client)
            lines.append("[{}:{:02d}:{:02d}] {} (en): {}".format(clock[:2], n // 60 % 60, n % 60, rng.choice(SCALE_PEOPLE), s))
        files["live/{}-{}/{}.md".format(day, series, stem)] = "\n".join(lines) + "\n"
    return files, gen


# ---------------------------------------------------------------- gold v2: the project, built in passes

def model_dir() -> Optional[Path]:
    """The bge-m3 folder of the app (models/bge-m3-int8 under the app's support folder), or G0_BGE_M3_DIR."""
    d = Path(os.environ.get("G0_BGE_M3_DIR") or (ES.support_dir() / "models" / "bge-m3-int8")).expanduser()
    return d if (d / "model_quantized.onnx").is_file() and (d / "tokenizer.json").is_file() else None


@contextlib.contextmanager
def embed_server(mdir: Optional[Path]):
    """The bundled embed_server.py on an ephemeral loopback port (11435 may be the app's), started for the run with
    vectors and stopped at the end; None without the model folder or when the server never came up (the run with
    vectors is then recorded as skipped, never run without vectors under the "vectors" label)."""
    if not mdir:
        yield None
        return
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([sys.executable, "-m", "livetips_m0.embed_server", "--model-dir", str(mdir), "--port", str(port)],
                            cwd=str(M0), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline, up = time.time() + 180, False
        while time.time() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen("http://127.0.0.1:{}/api/tags".format(port), timeout=2):
                    up = True
                    break
            except OSError:
                time.sleep(0.5)
        yield IG.LoopbackEmbedder(port=port, timeout=300.0) if up else None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def cli(args: List[str]) -> dict:
    """The evidence CLI in process, its JSON answer parsed (the bench writes overrides and exclusions as the app does)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = ECLI.main(args)
    out = json.loads(buf.getvalue() or "{}")
    if code != 0 or not out.get("ok"):
        raise SystemExit("evidence CLI failed: {} -> {}".format(args[0], out))
    return out


def record_texts(ctx: dict) -> None:
    """The text of every file and of the register as the store hashes it, so quote validity can be checked against
    the revision a snippet sits on (the store keeps hashes, not texts)."""
    root = ctx["root"]
    for _uri, info in IG.walk(str(root)).items():
        t = IG.read_text(info["path"])
        ctx["texts"].setdefault(ES.sha(t), t)
    reg = root / "live" / CM.FILE
    if reg.is_file():
        raw = reg.read_text(encoding="utf-8", errors="replace")
        ctx["texts"].setdefault(ES.sha(raw), raw)


def new_segments(ctx: dict) -> int:
    """Segments of the revisions built since the last call whose seg_hash no earlier revision had: the segments a
    pass would send to the model (a segment cache row exists for every other one, section 8.2)."""
    st, n = ctx["store"], 0
    for r in st.q("SELECT id FROM revision WHERE extract_state<>'not_needed' AND id>? ORDER BY id", (ctx["last_rev"],)):
        for s in SG.of_revision(st, r["id"]):
            if s["seg_hash"] not in ctx["seen"]:
                ctx["seen"].add(s["seg_hash"])
                n += 1
        ctx["last_rev"] = r["id"]
    return n


def build_v2(base: Path, gold: dict, gold2: dict, scale: bool, embedder=None, after_pass=None) -> dict:
    """The gold v2 project and its store, pass by pass (gold_v2.yaml header): the base pass, the decision sidecars
    (*.notion.json) in a pass of their own on notes already ingested (spec 14.2 item 4), the revision states in
    time order, the move, the deletions, the supersede override and the state-only pass through the CLI."""
    base.mkdir(parents=True, exist_ok=True)
    root = base / gold2["project"]
    shutil.copytree(PROJECT, root)
    sidecars = []                     # decision sidecars land in a later pass, on notes already ingested (14.2 item 4)
    for p in sorted((OVERLAY / "files").rglob("*")):
        if p.is_file() and p.name.endswith(".notion.json"):
            sidecars.append(p)
        elif p.is_file():
            dst = root / p.relative_to(OVERLAY / "files")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(p.read_bytes())
    with (root / "live" / CM.FILE).open("a", encoding="utf-8") as f:
        f.write((OVERLAY / "register-extra.jsonl").read_text(encoding="utf-8"))
    gen: Dict[str, dict] = {}
    if scale:
        files, gen = scale_set(SEED)
        for rel_path, text in files.items():
            p = root / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    dates = {str(k): str(v) for k, v in (gold.get("file_dates") or {}).items()}
    dates.update({str(k): str(v) for k, v in (gold2.get("file_dates") or {}).items()})
    set_dates(root, dates)
    tracker = base / "tracker.json"
    tracker.write_bytes((OVERLAY / "tracker.json").read_bytes())
    st = ES.Store.open(uid="g0-v2-" + base.name, root=str(root))
    state = gold2["state"]
    ctx = {"root": root, "store": st, "texts": {}, "seen": set(), "last_rev": 0, "passes": [], "cost": {},
           "generated": gen, "pm_excluded": list(state["pm_excluded"]), "scopes": {}, "a_index": {}, "scale": scale}

    def one_pass(valid_from=None) -> dict:
        record_texts(ctx)
        rep = IG.ingest(st, str(root), valid_from=valid_from, embedder=embedder, tracker_path=str(tracker), voices={},
                        quiet_s=0)
        ctx["passes"].append(rep)
        if after_pass is not None:
            after_pass(ctx)                 # --extract: the recorder extracts each revision state as it is built
        return rep

    one_pass()
    ctx["cost"]["base"] = new_segments(ctx)
    for p in sidecars:
        rel_path = str(p.relative_to(OVERLAY / "files"))
        dst = root / rel_path
        dst.write_bytes(p.read_bytes())
        m = DATE_RX.search(rel_path)
        ts = dt.datetime.fromisoformat((m.group(0) if m else "2026-11-01") + "T12:00:00").timestamp()
        os.utime(dst, (ts, ts))
    if sidecars:
        one_pass()
        ctx["cost"]["sidecars"] = new_segments(ctx)
    revs =yaml.safe_load((REVISIONS / "revisions.yaml").read_text(encoding="utf-8"))
    states = sorted(((ES.utc(s["at"]), rel_path, i, s) for rel_path, ss in revs["files"].items() for i, s in enumerate(ss)),
                    key=lambda x: (x[0], x[1]))
    for at, rel_path, i, s in states:
        p = root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((REVISIONS / s["state"]).read_bytes())
        ts = dt.datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
        os.utime(p, (ts, ts))
        one_pass(valid_from=at if s["mode"] == "valid_from" else None)
        ctx["cost"].setdefault(rel_path, []).append(new_segments(ctx))
    moved = CL.migrate(str(root / "live"))
    rep = one_pass()
    ctx["moved"] = {"files": len(moved["moved"]), "store_moves": rep["moved"], "new_revisions": rep["new_revisions"],
                    "new_segments": new_segments(ctx)}
    for rel_path in state["deleted"]:
        (root / rel_path).unlink()
    one_pass()
    ctx["cost"]["after_delete"] = new_segments(ctx)
    base_args = ["--store", str(st.path), "--root", str(root)]
    sup = state["supersede"]
    cli(["override"] + base_args + ["--target", "entity", "--key", sup["key"], "--field", "supersede", "--value",
                                    sup["value"], "--at", sup["at"], "--via", "app"])
    cli(["exclude"] + base_args + ["--hidden-calls", json.dumps(state["hidden_calls"]), "--revoked", json.dumps(state["revoked"])])
    return ctx


def replay_fixture(ctx: dict, fixture_dir: Path, small: bool) -> dict:
    """C's extraction (spec section 14.1): every segment of every revision to extract, keyed by its content-only
    seg_hash, replayed from fixture_dir/<seg_hash>.json through store.apply_extraction. Hits and misses are counted;
    a recorded fixture with a miss on the small set fails the run as "fixture stale" (a chunker, segmenter or client
    label change moved seg_hash). No file at all = not recorded yet (S3 records it): C runs on its deterministic part."""
    st = ctx["store"]
    recorded = fixture_dir.is_dir() and any(p.name != "manifest.json" for p in fixture_dir.glob("*.json"))
    manifest = fixture_dir / "manifest.json"
    gold_checked = False
    if recorded and manifest.is_file() and ctx.get("gold_sha256"):
        gold_checked = True
        rec_gold = json.loads(manifest.read_text(encoding="utf-8")).get("gold_sha256")
        if rec_gold != ctx["gold_sha256"]:
            raise SystemExit("G0 C: gold changed: the extraction fixture was recorded against gold v2 {}..., the current "
                             "gold is {}...; re-record it (run.py --extract <route>)".format(str(rec_gold)[:12],
                                                                                            ctx["gold_sha256"][:12]))
    rules = EX.load_rules()
    labels = EX.project_labels(str(ctx["root"]))
    hits = misses = tokens = 0
    kept = dropped = 0
    extractors = set()
    for r in st.q("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id WHERE rv.extract_state='pending'"
                  " ORDER BY rv.id"):
        uri = st.one("SELECT s.uri FROM revision rv JOIN source s ON s.id=rv.source_id WHERE rv.id=?", (r["id"],))[0]
        if uri in ctx["generated"]:
            continue                                  # the scale set's generated extraction, below
        for seg in SG.of_revision(st, r["id"]):
            p = fixture_dir / (seg["seg_hash"] + ".json")
            if not p.is_file():
                misses += 1
                continue
            hits += 1
            data = json.loads(p.read_text(encoding="utf-8"))
            extractors.add(str(data.get("extractor") or ""))
            tokens += int(data.get("out_tokens") or 0)
            if "answers" in data:
                # S3: raw model output (reviewed by deletion only) through the same validator as the worker
                if not gold_checked:
                    raise SystemExit("G0 C: gold unverifiable: the extraction fixture has recorded answers but no "
                                     "manifest.json with the gold v2 sha256; re-record it (run.py --extract <route>)")
                view = EX.segment_view(st, r["id"], seg)
                shift = view["snippets"][0]["lines"][0] - int(data.get("base_line") or 0)
                parts = []
                for t in data["answers"]:
                    obj1, err = EX.parse(t, rules["schema"])
                    if obj1 is None:
                        raise SystemExit("G0 C: fixture answer invalid: {} does not parse ({}); re-record it or fix the "
                                         "answer by deletion".format(p.name, err))
                    parts.append(obj1)
                obj = W.rebase(EX.merge(parts) if len(parts) > 1 else parts[0], {"snippets": [{"lines": (shift, 0)}]}, +1)
                cat = EX.catalog(st, view, str(ctx["root"]), labels, int(rules.get("catalog_max", 60)))
                items, c = EX.validate(st, view, obj, rules, cat, str(ctx["root"]), labels)
                kept, dropped = kept + int(c["kept"]), dropped + int(c["dropped"])
                ES.apply_extraction(st, r["id"], items, mark_done=False)
            else:
                ES.apply_extraction(st, r["id"], data.get("items") or {})
    out = {"recorded": recorded, "hits": hits, "misses": misses, "tokens": tokens, "extractors": sorted(extractors),
           "items_kept": kept, "items_dropped": dropped}       # PB-103: the validator's share kept (--extract precision)
    out["state"] = "not_recorded" if not recorded else ("stale" if misses else "ok")
    if recorded and misses and small:
        raise SystemExit("G0 C: fixture stale: {} segments of the small set have no fixture file for their seg_hash "
                         "(the chunker, the segmenter or the client labels changed); re-record it (S3)".format(misses))
    gen_docs = 0
    for rel_path, data in sorted(ctx["generated"].items()):
        rv = st.one("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri=? AND rv.superseded_at IS NULL",
                    (rel_path,))
        if rv:
            ES.apply_extraction(st, rv[0], data["items"])
            gen_docs += 1
    out["generated_docs"] = gen_docs
    return out


# ---------------------------------------------------------------- S4: topic summaries before the state changes

def topic_request_key(user: str) -> str:
    return hashlib.sha256(user.encode("utf-8")).hexdigest()


@contextlib.contextmanager
def before_state(st):
    """The store as it was before the bench's deletions, hide and revoke (spec 14.2 item 6: "the topic summary that
    cites the fact is built before the exclusion"): every source that is not active is active inside the block, and
    gets its exact state row back after it. Restoring goes through the store's source_state_topics trigger, the one
    place every state change marks the topics that cite the source stale, so the per-sentence filter of section 8.7
    is what the gold then tests (the bench does not re-summarise after the change)."""
    saved = [dict(r) for r in st.q("SELECT id, state, state_reason, state_at FROM source WHERE state<>'active' ORDER BY id")]
    with st.conn:
        st.conn.execute("UPDATE source SET state='active' WHERE state<>'active'")
    try:
        yield saved
    finally:
        with st.conn:
            st.conn.executemany("UPDATE source SET state=?, state_reason=?, state_at=? WHERE id=?",
                                [(r["state"], r["state_reason"], r["state_at"], r["id"]) for r in saved])


def topic_step(ctx: dict, topics_dir: Path, small: bool) -> dict:
    """C's topics (S4): detect, then summarise every topic from topics_dir/<sha256 of the request text>.json through
    topics.summarise (the worker's validator and writer), with the sources as they were before the state changes;
    then the states come back and the cited topics turn stale. A request with no fixture file is a miss (the topic
    stays unsummarised); any miss on the small set fails the run as "topic fixture stale", like the extraction."""
    st = ctx["store"]
    manifest = json.loads((topics_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("gold_sha256") != ctx.get("gold_sha256"):
        raise SystemExit("G0 C: gold changed: the topic fixture was recorded against another gold v2; re-record it "
                         "(run.py --record-topics)")
    if manifest.get("extract_sha") != tree_sha(EXTRACT):
        raise SystemExit("G0 C: topic fixture stale: it was recorded on another extraction fixture (fixtures/extract "
                         "changed); re-record it (run.py --record-topics)")
    hits, misses = [0], [0]

    def ask(user: str, cap: int) -> dict:
        p = topics_dir / (topic_request_key(user) + ".json")
        if not p.is_file():
            misses[0] += 1
            raise TP.Skip("no fixture for this request")
        hits[0] += 1
        return {"text": json.loads(p.read_text(encoding="utf-8"))["answer"]}

    rules = EX.load_rules()
    with before_state(st):
        det = TP.detect(st)
        rep = TP.summarise(st, ask, rules, at=SUMMARY_AT, tokens=lambda s: W.tokens(s, "api"))
        control = filter_control(ctx)
    out = dict(rep, detect=det, hits=hits[0], misses=misses[0], route=manifest.get("route"), control=control,
               stale_after_state=st.one("SELECT count(*) FROM topic WHERE state='stale'")[0],
               topics=st.one("SELECT count(*) FROM topic")[0])
    if misses[0] and small:
        raise SystemExit("G0 C: topic fixture stale: {} topic requests of the small set have no fixture file (the "
                         "extraction, the topic split or the request text changed); re-record it (--record-topics)"
                         .format(misses[0]))
    return out


def filter_control(ctx: dict) -> Dict[str, List[str]]:
    """The negative control of the per-sentence filter: while every source is still active, each global and S4 row's
    question through ask_global, and the excluded facts (gold v2 excluded_facts and the row's own excluded leak
    phrases) its summary sentences hold then. A row listed with phrases here and 0 excluded leaks in its result was
    filtered at query time; a row with none never reached a sentence of a denied source (it passes without the
    filter)."""
    gold2 = load_gold(GOLD2)
    out: Dict[str, List[str]] = {}
    for row in gold2["rows"]:
        if not (row["needs"] == "S4" or row["kind"] == "global"):
            continue
        g = SE.ask_global(ctx["store"], row["q"], scope=scope_of(ctx, row), exclude=ctx["pm_excluded"])
        text = "\n".join(s["text"] for t in g["topics"] for s in t["sentences"])
        phrases = list((row.get("leak") or {}).get("excluded") or []) + [p for x in gold2["excluded_facts"] for p in x["any"]]
        out[row["id"]] = sorted({p for p in phrases if has_phrase(text, [p])})
    return out


FACT_LINE = re.compile(r"^(f\d+) \| ([^|]*) \| .*? \| [a-z]+ \| \"(.*)\"$")


def authored_summary(user: str, most: int = TP.MAX_SENTENCES) -> str:
    """The authored answer of one summary request (no model): one sentence per fact, its date and its quote, citing
    it; above `most` facts, consecutive facts share a sentence. Mechanical on purpose: the G0 rows of S4 measure what
    ask_global returns and hides (topics, the per-sentence filter, summary_at, the timeline), not summary prose."""
    facts = [m.groups() for m in (FACT_LINE.match(ln) for ln in user.split("\n")) if m]
    per = max(1, -(-len(facts) // most))
    sents = []
    for i in range(0, len(facts), per):
        part = facts[i:i + per]
        sents.append({"text": "; ".join('{}: "{}"'.format(d.strip(), q) for _r, d, q in part),
                      "cites": [r for r, _d, _q in part]})
    return json.dumps({"sentences": sents}, ensure_ascii=False)


def record_topics(gold_path: Path = GOLD, gold2_path: Path = GOLD2, out_dir: Path = TOPICS) -> dict:
    """--record-topics: build C's small-set and scale-set stores as the evaluation does (extraction fixture replayed,
    sources as before the state changes), detect topics and write one fixtures/topics/<sha256 of the request
    text>.json per summary request plus manifest.json (the scale set's noise documents change some topics' text).
    Answers come from authored_summary only: no model, no network. Recording a real route's summaries needs its own
    recorder (a pass with its run row and the sources' states as they are), a GraphRAG follow-up. The folder is
    replaced as a whole."""
    gold, gold2 = load_gold(gold_path), load_gold(gold2_path)
    answers: Dict[str, str] = {}
    for set_name in SETS:
        _record_topics_set(gold, gold2, gold2_path, set_name == "scale", answers)
    if not answers:
        raise SystemExit("G0 --record-topics: no topic to summarise")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for h, text in sorted(answers.items()):
        (out_dir / (h + ".json")).write_text(json.dumps({"request_sha256": h, "route": "authored", "answer": text},
                                                        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    manifest = {"gold_sha256": hashlib.sha256(Path(gold2_path).read_bytes()).hexdigest(), "route": "authored",
                "extract_sha": tree_sha(EXTRACT), "model": "none",
                "summary_version": (EX.load_rules().get("summary") or {}).get("version"),
                "requests": len(answers), "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
                "note": "PB-87 S4: answers written by authored_summary in run.py (one dated quote per fact, citing it); "
                        "no provider was called. Not a measurement of summary quality: the S4 rows measure what "
                        "ask_global returns and hides."}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def _record_topics_set(gold: dict, gold2: dict, gold2_path: Path, scale: bool, answers: Dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="g0-topics-") as tmp, store_env(Path(tmp)):
        ctx = build_v2(Path(tmp) / "rec", gold, gold2, scale, None)
        ctx["gold_sha256"] = hashlib.sha256(Path(gold2_path).read_bytes()).hexdigest()
        replay_fixture(ctx, EXTRACT, not scale)
        st = ctx["store"]

        def ask(user: str, cap: int) -> dict:
            answers[topic_request_key(user)] = authored_summary(user)
            return {"text": answers[topic_request_key(user)]}
        try:
            with before_state(st):
                TP.detect(st)
                TP.summarise(st, ask, EX.load_rules(), at=SUMMARY_AT, tokens=lambda s: W.tokens(s, "api"))
        finally:
            st.close()


# ---------------------------------------------------------------- gold v2: one row through one variant

def scope_of(ctx: dict, row: dict) -> Optional[dict]:
    key = (row.get("title") or "", tuple(row.get("companies") or []))
    if key not in ctx["scopes"]:
        ctx["scopes"][key] = CS.decide(str(ctx["root"]), key[0], list(key[1]))
    return ctx["scopes"][key]


def item_text(i: dict) -> str:
    bits = [i.get("text") or ""] + [q.get("quote") or "" for q in i.get("quotes") or []]
    bits += [str(i.get(k) or "") for k in ("value", "subject_name", "why")]
    return "\n".join(b for b in bits if b)


def own_entities(st, items: List[dict], as_of: Optional[str], fold) -> List[dict]:
    """C's deterministic entities of the returned evidence rows: the task of a register event, the Tracker item of a
    snapshot row, the decisions and risks the snippet's revision carries and names; only those the request may see
    (the _ent table of the search that returned the rows)."""
    ids = []
    for i in items:
        if i.get("kind") != "snippet":
            continue
        loc = i.get("locator") or {}
        if loc.get("event_id"):
            ids.append(loc["event_id"])
        ids += [r[0] for r in st.q("SELECT name FROM snippet_mark WHERE snippet_id=? AND kind='item'", (i["id"],))]
        ids += [r[0] for r in st.q("SELECT er.entity_id FROM entity_rev er JOIN snippet sn ON sn.revision_id=er.revision_id"
                                   " JOIN entity e ON e.id=er.entity_id WHERE sn.id=? AND instr(lower(sn.text), lower(e.name))>0",
                                   (i["id"],))]
    out, seen = [], set()
    for eid in ids:
        if eid in seen or not st.one("SELECT 1 FROM _ent WHERE id=?", (eid,)):
            continue
        seen.add(eid)
        e = SE.entity_state(st, eid, as_of, fold)
        if e:
            out.append(e)
    return out


def hop_items(st, ents: List[dict], scope, as_of, exclude, k: int, hops: int = 2) -> List[dict]:
    """C's graph step: the edges walked and the claims of entities one and two edges away from the entities the search
    matched, walked from each matched entity in the search's own order, the best match's neighborhood first (S3a: a
    set's order changed between runs, the edge's quote is the hop, and weak matches no longer fill the k slots)."""
    seen = {e["id"] for e in ents}
    out, took = [], set()
    for root in [e["id"] for e in ents][:6]:
        frontier = [root]
        for _ in range(hops):
            nxt = []
            for eid in frontier:
                card = SE.entity_card(st, eid, scope=scope, as_of=as_of, exclude=exclude) or {}
                for e in card.get("edges") or []:
                    other = e.get("dst") if e.get("src") == eid else e.get("src")
                    if not other or other in seen or e.get("rel") == "same_as":
                        continue
                    seen.add(other)
                    nxt.append(other)
                    if e.get("id") not in took:
                        took.add(e.get("id"))
                        out.append(dict(e, hop=True))
            for eid in nxt:
                card = SE.entity_card(st, eid, scope=scope, as_of=as_of, exclude=exclude) or {}
                # PB-87: newest first (recency, spec 11.2 step 5), not row order: an older file's claims read first
                # must not push a neighbour's latest state out of the k slots
                claims = sorted(card.get("claims") or [], key=lambda c: (c.get("said_at") or "", c["id"]), reverse=True)
                out += [dict(c, hop=True) for c in claims]
            frontier = nxt[:6]
            if len(out) >= k:
                return out[:k]
    return out[:k]


def ask(v, ctx: dict, row: dict, k: int, qvec=None) -> dict:
    """One gold v2 question through one variant: the top k items (and C's entities)."""
    st, root = ctx["store"], ctx["root"]
    scope = scope_of(ctx, row)
    as_of = ES.utc(row["as_of"]) if row.get("as_of") else None
    if v.name == "A":
        key = json.dumps(scope, sort_keys=True)
        if key not in ctx["a_index"]:
            ctx["a_index"][key] = LK.FolderIndex([str(root)], scope=scope).build()
        hits = LK.snippets(row["q"], sheet=None, roots=[str(root)], k=k, queries=[], budget_chars=14000, max_seconds=1.0,
                           index=ctx["a_index"][key], scope=scope)
        return {"items": [{"kind": "snippet", "text": h.get("text") or "", "said_at": None,
                           "source": rel(h["path"], root) if h.get("path") else h.get("src", "")} for h in hits[:k]],
                "entities": []}
    res = SE.search(st, row["q"], scope=scope, as_of=as_of, exclude=ctx["pm_excluded"], kinds=v.kinds, mode="prep",
                    budget=SE.Budget(k=k), query_vec=qvec)
    items, ents = list(res["items"]), []
    if v.name == "C":
        fold = IG.register_fold_at(st, as_of, passing=True)
        ents = list(res["entities"])
        ents += [e for e in own_entities(st, items, as_of, fold) if e["id"] not in {x["id"] for x in ents}]
        ents = [dict(e, aliases=SE.visible_aliases(st, e["id"], fold)) for e in ents]
        items += hop_items(st, res["entities"], scope, as_of, ctx["pm_excluded"], k)
        if "S4" in ctx.get("ready", ()) and (row["needs"] == "S4" or row["kind"] == "global"):
            # S4: a global question goes to ask_global, as G3 preparation will send it (section 8.7): the passing
            # summary sentences (kind topic, dated by their earliest cited fact) and the members' dated timeline.
            # The timeline recall of section 14.3 counts what ask_global returns, so a global row is judged on its
            # answer alone; the other S4 rows (time and exclusion) keep the search items too: more surface, stricter
            g = SE.ask_global(st, row["q"], scope=scope, as_of=as_of, exclude=ctx["pm_excluded"])
            got = [s for t in g["topics"] for s in t["sentences"]] + list(g["timeline"])
            if row["kind"] == "global":
                return {"items": got, "entities": []}
            items += got
    return {"items": items, "entities": ents}


def denied_sources(ctx: dict) -> set:
    """Sources nothing may come from: excluded (hidden call, PM), revoked or deleted, as uris under the project."""
    out = set(ctx["pm_excluded"])
    out |= {r[0] for r in ctx["store"].q("SELECT uri FROM source WHERE state IN ('excluded', 'revoked', 'deleted')")}
    return out


def quote_valid(ctx: dict, item: dict) -> List[bool]:
    """Each text the item returns is verbatim at its locator: a snippet is its revision's text at `chars` (the register
    event at its line, a Tracker row holds its item text), a quote sits in the snippet at its locator."""
    st = ctx["store"]

    def snippet_ok(sid: int) -> bool:
        r = st.one("SELECT sn.text, sn.locator, rv.content_hash, s.kind FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id"
                   " JOIN source s ON s.id=rv.source_id WHERE sn.id=?", (sid,))
        if not r:
            return False
        loc = json.loads(r["locator"])
        if r["kind"] == "tracker":
            return bool(loc.get("text")) and loc["text"] in r["text"]
        full = ctx["texts"].get(r["content_hash"])
        if full is None:
            return False
        if r["kind"] == "register":
            n = int(loc["lines"][0])
            lines = full.split("\n")
            try:
                return n <= len(lines) and json.loads(lines[n - 1]).get("id") == loc.get("event_id")
            except ValueError:
                return False
        a, b = loc.get("chars") or [0, 0]
        return full[a:b] == r["text"]

    if item.get("kind") == "snippet":
        return [snippet_ok(item["id"])]
    out = []
    for q in item.get("quotes") or []:
        # S3a: a file's revisions share locators (an edited section keeps its heading), and a quote of an LLM claim
        # may come from an earlier revision (history, as_of); the quote is valid at the revision it sits on
        sns = st.q("SELECT sn.id, sn.text FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id JOIN source s"
                   " ON s.id=rv.source_id WHERE s.uri=? AND sn.locator=? ORDER BY rv.id DESC",
                   (q.get("uri"), json.dumps(q.get("locator"), ensure_ascii=False)))
        out.append(any((q.get("quote") or "") in sn["text"] and snippet_ok(sn["id"]) for sn in sns))
    return out


def judge(v, ctx: dict, gold2: dict, row: dict, res: dict) -> dict:
    """One row's result: facts found, leaks by kind, state, quote validity; pass when every fact is found, the state
    (C) holds and nothing leaks."""
    items, ents = res["items"], res["entities"]
    as_of = ES.utc(row["as_of"]) if row.get("as_of") else None
    blob = "\n".join([item_text(i) for i in items] + [" ".join([e.get("name") or ""] + list(e.get("aliases") or []))
                                                      for e in ents])
    facts = []
    for f in row.get("expect") or []:
        hit = [i for i in items if has_phrase(item_text(i), f["any"])]
        ent_hit = [e for e in ents if has_phrase(e.get("name") or "", f["any"])]
        if row.get("sources"):
            hit = [i for i in hit if any(s in (i.get("source") or "") for s in row["sources"])]
        if row.get("dated"):
            hit = [i for i in hit if (i.get("said_at") or "").startswith(str(row["dated"]))]
            ent_hit = []
        dated = [i for i in hit if i.get("kind") != "topic"]     # S4: a summary sentence finds, a fact dates
        facts.append({"any": f["any"], "found": bool(hit or ent_hit),
                      "said_at": min((i.get("said_at") or "" for i in dated), default="") or None})
    leak = row.get("leak") or {}
    leaks = {"future": [p for p in leak.get("future") or [] if has_phrase(blob, [p])],
             "stale": [p for p in leak.get("stale") or [] if has_phrase(blob, [p])],
             "excluded": sorted({p for p in list(leak.get("excluded") or []) +
                                 [p for x in gold2["excluded_facts"] for p in x["any"]] if has_phrase(blob, [p])}),
             "client": [f[0] for f in gold2["forbidden"][row["client"]] if has_phrase(blob, f)]}
    denied = denied_sources(ctx)
    for i in items:                                   # structural: rows from denied sources, rows dated after as_of
        srcs = [i.get("source") or ""] + [q.get("uri") or "" for q in i.get("quotes") or []]
        leaks["excluded"] += ["item:" + s for s in srcs if s in denied]
        if as_of:
            dates = [i.get("said_at") or ""] + [q.get("said_at") or "" for q in i.get("quotes") or []]
            leaks["future"] += ["item:{}@{}".format(i.get("source"), d) for d in dates if d and d > as_of]
    state_ok = None
    if row.get("state"):
        want = row["state"]
        e = next((e for e in ents if has_phrase(e.get("name") or "", [want["name"]])), None)
        got = (e or {}).get("status")
        state_ok = bool(e) and (got != "superseded" if want["status"] == "current" else got == want["status"])
        if as_of and e and got in ("done", "superseded") and want["status"] in ("open", "current"):
            leaks["future"].append("status:{}={}".format(e["id"], got))
    checks = [q for i in items for q in quote_valid(ctx, i)] if v.name != "A" else []
    order_ok = None
    if row.get("order"):
        # S4: the order of the facts returned (at least two, each dated); a missing fact is counted by recall, and
        # the row passes only with every fact found (spec 17 S4: recall at least 75% with correct order)
        dates = [f["said_at"] for f in facts if f["found"]]
        order_ok = len(dates) >= 2 and all(dates) and dates == sorted(dates)
    ok = all(f["found"] for f in facts) and state_ok is not False and order_ok is not False \
        and not any(leaks[k] for k in ("future", "stale", "excluded", "client"))
    facts_found = [i for i in items if i.get("kind") in ("claim", "edge") and any(
        has_phrase(item_text(i), f["any"]) for f in row.get("expect") or [])]
    derived = derived_confirmed(ctx, items) if v.name != "A" else None
    if row["kind"] == "modality":
        # S3a: modality is carried by claims and edges, text alone never answers it. "proposal": a matching fact
        # says proposal and none counts as confirmed; "not_confirmed": a matching fact and none counts as confirmed
        # (spec 7.4: modality confirmation with a primary or pm source)
        confirmed = [i for i in facts_found if i.get("modality") == "confirmation" and (i.get("confirmations") or 0) > 0]
        want_mod = row.get("modality")
        ok = ok and bool(facts_found) and not confirmed and (
            want_mod != "proposal" or any(i.get("modality") == "proposal" for i in facts_found))
    if row.get("history") and v.name != "C":
        ok = False                     # "changed later" lives in the entity timeline only
    return {"id": row["id"], "kind": row["kind"], "needs": row["needs"], "status": "pass" if ok else "fail",
            "expected": row["expected"][v.name], "facts": facts, "leaks": leaks, "state_ok": state_ok,
            "order_ok": order_ok, "quotes_valid": sum(checks), "quotes": len(checks),
            "top": [i.get("source") or "" for i in items], "derived_confirmed": derived}


def derived_confirmed(ctx: dict, items: List[dict]) -> List[int]:
    """Spec 7.4, checked against the store and not the item's own count: returned claims and edges shown as confirmed
    (modality confirmation, confirmations > 0) whose evidence holds no primary or pm source. Must be []."""
    st, bad = ctx["store"], []
    for i in items:
        if i.get("kind") not in ("claim", "edge") or i.get("modality") != "confirmation" or not i.get("confirmations"):
            continue
        if i.get("origin") == "pm" or not isinstance(i.get("id"), int):
            continue                    # a PM supersede or override row is its own primary source
        if not st.one("SELECT 1 FROM evidence ev JOIN snippet sn ON sn.id=ev.snippet_id JOIN revision rv ON"
                      " rv.id=sn.revision_id JOIN source s ON s.id=rv.source_id WHERE ev.target=? AND ev.target_id=?"
                      " AND s.origin IN ('primary', 'pm')", (i["kind"], i["id"])):
            bad.append(i["id"])
    return bad


# ---------------------------------------------------------------- gold v2: one set, one vector mode

def evaluate_v2(v, ctx: dict, gold2: dict, set_name: str, embedder=None, fixture_dir: Optional[Path] = None) -> dict:
    fixture = replay_fixture(ctx, fixture_dir or EXTRACT, set_name == "small") if v.name == "C" else None
    ready = ready_set(fixture_dir)
    ctx["ready"] = ready
    if v.name == "C" and "S4" in ready:
        fixture = dict(fixture or {}, topics=topic_step(ctx, TOPICS, set_name == "small"))
    if embedder is not None and not ctx["store"].one("SELECT 1 FROM snippet WHERE vec IS NOT NULL"):
        raise SystemExit("G0 {}: the run with vectors stored no snippet vector (the embed server answered nothing)"
                         .format(v.name))
    rows = []
    top = int(gold2.get("top", 6))
    for row in gold2["rows"]:
        if v.name == "C" and row["needs"] not in ready:
            rows.append({"id": row["id"], "kind": row["kind"], "needs": row["needs"], "status": "not_evaluated",
                         "missing": "{} ({})".format(row["needs"], {"S3": "LLM extraction", "S4": "topic summaries"}[row["needs"]]),
                         "expected": row["expected"]["C"]})
            continue
        qvec = None
        if embedder is not None:
            vecs = embedder([row["q"]])
            qvec = vecs[0] if vecs else None
            if qvec is None:
                raise SystemExit("G0 {}: the run with vectors got no query vector for {}".format(v.name, row["id"]))
        k = int(row.get("top") or top)
        got = judge(v, ctx, gold2, row, ask(v, ctx, row, k, qvec))
        if v.name != "A":
            got["live_leaks"] = live_leaks(v, ctx, gold2, row, k)
        rows.append(got)
    lat = latency(v, ctx, gold2, rows)
    return dict(metrics(rows, ctx, set_name), rows=rows, latency=lat, fixture=fixture,
                index_cost=index_cost(ctx, fixture), passes=len(ctx["passes"]))


def live_leaks(v, ctx: dict, gold2: dict, row: dict, k: int) -> dict:
    """The row's question through search(mode="live") as the app asks it during a call (now, no as_of, the PM's
    exclusions passed): the client and excluded leaks of what it returns. Future and stale do not apply to "now"."""
    res = SE.search(ctx["store"], row["q"], scope=scope_of(ctx, row), exclude=ctx["pm_excluded"], kinds=v.kinds,
                    mode="live", budget=SE.Budget(k=k, ms=150))
    now = {key: val for key, val in row.items() if key not in ("as_of", "state", "order", "history", "dated")}
    got = judge(v, ctx, gold2, now, {"items": list(res["items"]), "entities": list(res.get("entities") or [])})
    return {"client": got["leaks"]["client"], "excluded": got["leaks"]["excluded"]}


def latency(v, ctx: dict, gold2: dict, rows: List[dict]) -> dict:
    """search(mode="live") and mode="prep" over the evaluated rows' questions (A: its folder lookup), LOOKUP_REPS each."""
    live, prep = [], []
    asked = {r["id"] for r in rows if r["status"] != "not_evaluated"}
    for row in gold2["rows"]:
        if row["id"] not in asked:
            continue
        scope = scope_of(ctx, row)
        for _ in range(LOOKUP_REPS):
            t = time.perf_counter()
            if v.name == "A":
                ask(v, ctx, row, 6)
                live.append((time.perf_counter() - t) * 1000)
                continue
            SE.search(ctx["store"], row["q"], scope=scope, exclude=ctx["pm_excluded"], kinds=v.kinds, mode="live")
            live.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            SE.search(ctx["store"], row["q"], scope=scope, as_of=row.get("as_of"), exclude=ctx["pm_excluded"],
                      kinds=v.kinds, mode="prep")
            prep.append((time.perf_counter() - t) * 1000)
    return {"engine": "folder index (A)" if v.name == "A" else "evidence store",
            "live_p50_ms": quantile(live, 0.5), "live_p95_ms": quantile(live, 0.95),
            "prep_p50_ms": quantile(prep, 0.5) if prep else None, "prep_p95_ms": quantile(prep, 0.95) if prep else None}


def index_cost(ctx: dict, fixture: Optional[dict]) -> dict:
    st = ctx["store"]
    segs = docs = 0
    for r in st.q("SELECT id FROM revision WHERE extract_state<>'not_needed' AND superseded_at IS NULL"):
        docs += 1
        segs += len(SG.of_revision(st, r["id"]))
    plan, budget = ctx["cost"].get("docs/acme/phase-two-plan.md", []), ctx["cost"].get("docs/acme/phase-two-budget.md", [])
    return {"segments": segs, "documents": docs, "segments_per_document": round(segs / docs, 2) if docs else None,
            "tokens": (fixture or {}).get("tokens", 0),
            "changed_section_resegments": plan[1] if len(plan) > 1 else None, "changed_section_expected": 1,
            "revert_resegments": budget[2] if len(budget) > 2 else None,
            "move_new_revisions": ctx["moved"]["new_revisions"], "move_resegments": ctx["moved"]["new_segments"],
            "moved_files": ctx["moved"]["files"]}


def rate(sel: List[dict]) -> dict:
    n = sum(1 for r in sel if r["status"] == "pass")
    return {"rate": pct(n, len(sel)), "n": n, "of": len(sel)}


def metrics(rows: List[dict], ctx: dict, set_name: str) -> dict:
    """The new columns of spec section 14.3 over the evaluated rows only (not_evaluated rows are counted apart)."""
    ev = [r for r in rows if r["status"] != "not_evaluated"]
    by = lambda *kinds: [r for r in ev if r["kind"] in kinds]          # noqa: E731
    glob_rows = by("global")
    tl_found = sum(1 for r in glob_rows for f in r["facts"] if f["found"])
    tl_total = sum(len(r["facts"]) for r in glob_rows)
    evid = [r for r in ev if r["id"] in EVIDENCE_ROWS]
    shared = [r for r in ev if r["id"] in SHARED_ROWS]
    q_ok, q_all = sum(r.get("quotes_valid", 0) for r in ev), sum(r.get("quotes", 0) for r in ev)
    store_rows = any(r.get("derived_confirmed") is not None for r in ev)      # B and C (A has no store)
    out = {"rows_evaluated": len(ev), "rows_not_evaluated": len(rows) - len(ev), "rows_passed": rate(ev)["n"],
           "expected_mismatch": [r["id"] for r in ev if r["status"] != r["expected"]],
           "hop_answer_rate": rate(by("hop")), "paraphrase_answer_rate": rate(by("paraphrase", "paraphrase_uk")),
           "paraphrase_uk": rate(by("paraphrase_uk")),
           "timeline_recall": pct(tl_found, tl_total), "timeline_order": all(r["order_ok"] for r in glob_rows) if glob_rows else None,
           "evidence_recall": rate(evid), "quote_validity": pct(q_ok, q_all), "quotes_checked": q_all,
           # S3a: modality rows passed, and confirmed facts with only derived evidence (None for A: no store)
           "modality_accuracy": rate(by("modality"))["rate"] if store_rows else None,
           "derived_confirmed": len({x for r in ev for x in r.get("derived_confirmed") or []}) if store_rows else None,
           "future_leaks": sum(len(r["leaks"]["future"]) for r in ev),
           "stale_returned": sum(len(r["leaks"]["stale"]) for r in ev),
           "excluded_leaks": sum(len(r["leaks"]["excluded"]) for r in ev),
           "client_leaks": sum(len(r["leaks"]["client"]) for r in ev),
           "shared_facts_kept": rate(shared)}
    if any("live_leaks" in r for r in ev):     # B and C: the same rows through mode="live" (now, PM exclusions)
        out["live_client_leaks"] = sum(len(r["live_leaks"]["client"]) for r in ev)
        out["live_excluded_leaks"] = sum(len(r["live_leaks"]["excluded"]) for r in ev)
    if ctx["scale"]:            # the scale set's extraction is generated: no hop or paraphrase gate reads it
        out["generated_extraction"] = {"docs": len(ctx["generated"]), "gated": False}
    return out


EVIDENCE_ROWS = set()           # filled from the gold rows (`evidence`), see run_v2
SHARED_ROWS = set()


def run_v2(v, gold: dict, gold2: dict, gold2_path: Path, tmp: Path, sets, mdir: Optional[Path]) -> dict:
    EVIDENCE_ROWS.clear()
    EVIDENCE_ROWS.update(r["id"] for r in gold2["rows"] if r.get("evidence"))
    SHARED_ROWS.clear()
    SHARED_ROWS.update(r["id"] for r in gold2["rows"] if r.get("shared"))
    out = {"gold_version": 2, "gold_sha256": hashlib.sha256(gold2_path.read_bytes()).hexdigest(),
           "fixture_v2_sha": tree_sha(OVERLAY, REVISIONS), "ready": sorted(ready_set()), "sets": {}}
    modes = ("no_vectors",) if v.name == "A" else ("no_vectors", "vectors")
    with embed_server(mdir if v.name != "A" else None) as emb:
        for s in sets:
            out["sets"][s] = {}
            for m in modes:
                if m == "vectors" and emb is None:
                    why = ("no bge-m3 model folder (models/bge-m3-int8 or G0_BGE_M3_DIR): the run with vectors is "
                           "evaluated on a machine with the model") if not mdir else \
                        "the embed server did not come up on loopback within 180 s"
                    out["sets"][s][m] = {"skipped": why}
                    continue
                ctx = build_v2(tmp / "v2-{}-{}".format(s, m), gold, gold2, s == "scale", emb if m == "vectors" else None)
                ctx["gold_sha256"] = out["gold_sha256"]
                try:
                    out["sets"][s][m] = evaluate_v2(v, ctx, gold2, s, emb if m == "vectors" else None)
                finally:
                    ctx["store"].close()
    return out


def s2_gate(res: dict) -> Optional[dict]:
    """The S2 G0 gate (spec section 17, S2) for B and C: prep and live leaks 0 on the v1 targets; on every evaluated
    run of gold v2 client, future and excluded leaks 0 in prep, client and excluded leaks 0 in mode live (the same
    rows, now), quote validity 100%, evidence recall of register and Tracker facts 100%; mode live p95 on the scale
    set at most 150 ms."""
    if res["variant"] == "A" or "v2" not in res:
        return None
    checks = {"prep_leaks_0": res["totals"]["prep_leaks"] == 0, "live_leaks_0": res["totals"]["live_leaks"] == 0}
    for s, modes in res["v2"]["sets"].items():
        for m, r in modes.items():
            if "skipped" in r:
                checks["{}/{} skipped".format(s, m)] = None
                continue
            k = "{}/{} ".format(s, m)
            checks[k + "client_leaks_0"] = r["client_leaks"] == 0
            checks[k + "future_leaks_0"] = r["future_leaks"] == 0
            checks[k + "excluded_leaks_0"] = r["excluded_leaks"] == 0
            checks[k + "quote_validity_100"] = r["quote_validity"] in (100.0, None)
            checks[k + "evidence_recall_100"] = r["evidence_recall"]["rate"] == 100.0
            checks[k + "live_leaks_0"] = r.get("live_client_leaks") == 0 and r.get("live_excluded_leaks") == 0
            if s == "scale":
                checks[k + "live_p95_le_150ms"] = r["latency"]["live_p95_ms"] <= 150
    return {"pass": all(x for x in checks.values() if x is not None), "checks": checks}


def s3_open(res: dict, b_res: Optional[dict] = None) -> List[str]:
    """Section 17 S3 lines that are not met and not gated (printed next to the gate so a PASS does not hide them):
    the paraphrase answer rate of C above B on the small set without vectors (a GraphRAG follow-up task)."""
    small = res["v2"]["sets"].get("small", {}).get("no_vectors")
    if not small or "skipped" in small:
        return []
    c = small["paraphrase_answer_rate"]
    try:
        b = (b_res or newest("B"))["v2"]["sets"]["small"]["no_vectors"]["paraphrase_answer_rate"]
    except (SystemExit, KeyError, TypeError):
        return ["paraphrase above B: no B result"]
    if c["n"] > b["n"]:
        return []
    return ["paraphrase above B not met ({} of {}, B {} of {}; follow-up)".format(c["n"], c["of"], b["n"], b["of"])]


def s4_gate(res: dict) -> Optional[dict]:
    """The S4 G0 gate (spec section 17, S4) for C once S3 is ready: every gold v2 row evaluated (no not_evaluated
    left, so a missing or stale topic fixture fails here), global timeline recall at least 75% with the returned facts
    in order on the small set without vectors, topic fixture misses 0 there; future and excluded leaks 0 (topic
    sentences included) are the S2 checks on the same runs, and the S3 checks stay (the caller merges all three).
    Row x5 must reach the per-sentence filter on the small set: its negative control (the sentences before the hide)
    holds the hidden fact, so "excluded leaks 0" there is the filter's work, not empty data. On the scale set the
    noise documents' topics outrank it for that question (reported in the control, not gated)."""
    if res["variant"] != "C" or "S3" not in res.get("v2", {}).get("ready", []):
        return None
    checks: Dict[str, Optional[bool]] = {}
    for s, modes in res["v2"]["sets"].items():
        for m, r in modes.items():
            if "skipped" not in r:
                checks["{}/{} rows_not_evaluated_0".format(s, m)] = r["rows_not_evaluated"] == 0
    small = res["v2"]["sets"].get("small", {}).get("no_vectors")
    if small and "skipped" not in small:
        checks["small timeline_recall_ge_75"] = (small["timeline_recall"] or 0) >= 75.0
        checks["small timeline_order"] = small["timeline_order"] is True
        topics = (small.get("fixture") or {}).get("topics") or {}
        checks["small topic_fixture_misses_0"] = topics.get("misses") == 0
        checks["small x5_control_not_empty"] = bool((topics.get("control") or {}).get("x5-hidden-topic"))
    return checks


def s3_gate(res: dict, b_res: Optional[dict] = None) -> Optional[dict]:
    """The S3a G0 gate (spec section 17, S3) for C with a recorded extraction fixture whose gold hash is current (the
    replay stops on "gold changed" otherwise): on the small set hop answer rate at least 4 of 6 and at least 2 more
    than B (B's newest saved result), fixture misses 0, modality accuracy 100%; derived counted as confirmed 0 on every
    evaluated run; the S2 checks still pass (added by the caller). Paraphrase rates are reported, not gated here."""
    if res["variant"] != "C" or "S3" not in res.get("v2", {}).get("ready", []):
        return None
    checks: Dict[str, Optional[bool]] = {}
    small = res["v2"]["sets"].get("small", {}).get("no_vectors")
    if small and "skipped" not in small:
        hop = small["hop_answer_rate"]["n"]
        checks["small hop_ge_4_of_6"] = hop >= 4 and small["hop_answer_rate"]["of"] >= 6
        try:
            b = (b_res or newest("B"))["v2"]["sets"]["small"]["no_vectors"]["hop_answer_rate"]["n"]
            checks["small hop_ge_B_plus_2"] = hop >= b + 2
        except (SystemExit, KeyError, TypeError):
            checks["small hop_ge_B_plus_2"] = False      # no B result saved: the gate cannot pass without it
        checks["small fixture_misses_0"] = (small.get("fixture") or {}).get("misses") == 0
        checks["small modality_accuracy_100"] = small["modality_accuracy"] == 100.0
    for s, modes in res["v2"]["sets"].items():
        for m, r in modes.items():
            if "skipped" not in r:
                checks["{}/{} derived_confirmed_0".format(s, m)] = r["derived_confirmed"] == 0
    return checks


# ---------------------------------------------------------------- the whole run

@contextlib.contextmanager
def store_env(tmp: Path):
    """The evidence stores of a run live in its temp folder (LT_SUPPORT_DIR, never the Mac's Application Support) and
    the flag evidence_store is on for the run (LT_EVIDENCE=1), as in the store's tests."""
    keys = ("LT_SUPPORT_DIR", "LT_EVIDENCE")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["LT_SUPPORT_DIR"] = str(tmp / "support")
    os.environ["LT_EVIDENCE"] = "1"
    try:
        yield
    finally:
        for k, val in saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val


def run(variant_name: str = "A", gold_path: Path = GOLD, sets=SETS, gold2_path: Path = GOLD2) -> dict:
    gold = load_gold(gold_path)
    bad = check_invariants(gold)
    if bad:
        raise SystemExit("G0 fixture is broken:\n- " + "\n- ".join(bad))
    gold2 = load_gold(gold2_path) if sets else None
    bad = check_v2_invariants(gold2, gold) if gold2 else []
    if bad:
        raise SystemExit("G0 gold v2 is broken:\n- " + "\n- ".join(bad))
    v = variant(variant_name)
    t_all = time.time()
    mdir = model_dir()                     # read before the run moves the support folder
    out = {"schema": "pacenote.bench.g0/2", "variant": variant_name, "date": dt.date.today().isoformat(),
           "commit": git_head(), "fixture_sha": tree_sha(FIX, skip=V2_DIRS), "gold_sha": tree_sha(gold_path),
           "route": gold.get("route", "codex"), "top": gold.get("top", 3), "lookup_reps": LOOKUP_REPS, "targets": []}
    samples: List[float] = []
    with offline() as blocked, site_server(FIX / "site") as site, tempfile.TemporaryDirectory(prefix="g0-") as tmp, \
            store_env(Path(tmp)):
        for t in sorted(gold["targets"], key=lambda x: x["start"]):
            root = fresh_copy(Path(tmp), gold)           # each target call on its own clean copy
            prep = run_prep(v, t, gold, root, site)
            sheet = prep.pop("_sheet")
            live = run_live(v, t, gold, root, sheet)
            samples += live.pop("_samples")
            after = run_after(v, t, gold, root, sheet)
            out["targets"].append({"id": t["id"], "client": t["client"], "prep": prep, "live": live, "after": after})
        if gold2:
            out["v2"] = run_v2(v, gold, gold2, gold2_path, Path(tmp), sets, mdir)
        out["blocked_connections"] = len(blocked)
    out["totals"] = totals(out["targets"], samples)
    if "v2" in out:
        out["v2"]["gate"] = s2_gate(out)
        s3 = s3_gate(out)
        if s3 and out["v2"]["gate"]:          # S3a: one gate, the S2 zeros still 0 plus the S3 checks
            out["v2"]["gate"]["checks"].update(s3)
            out["v2"]["gate"]["checks"].update(s4_gate(out) or {})      # S4 (PB-87): topics and global questions
            out["v2"]["gate"]["pass"] = all(x for x in out["v2"]["gate"]["checks"].values() if x is not None)
            out["v2"]["gate"]["s3"] = True
            out["v2"]["gate"]["s4"] = s4_gate(out) is not None
            out["v2"]["gate"]["open"] = s3_open(out)
    out["seconds"] = round(time.time() - t_all, 2)
    return out


def totals(targets: List[dict], samples: List[float]) -> dict:
    f = sum(x["prep"]["found"] for x in targets)
    ft = sum(x["prep"]["total"] for x in targets)
    a = sum(x["live"]["answered"] for x in targets)
    at = sum(x["live"]["total"] for x in targets)
    return {"prep_coverage": pct(f, ft), "prep_found": f, "prep_total": ft,
            "prep_leaks": sum(len(x["prep"]["leaks"]) for x in targets),
            "live_answer_rate": pct(a, at), "live_answered": a, "live_total": at,
            "live_answer_text_rate": pct(sum(x["live"]["answer_text"] for x in targets), at),
            "live_leaks": sum(x["live"]["leaks"] for x in targets),
            "lookup_p50_ms": quantile(samples, 0.5), "lookup_p95_ms": quantile(samples, 0.95),
            "duplicates": sum(x["after"]["duplicates"] for x in targets),
            "wrong_identity": sum(x["after"]["wrong_identity"] for x in targets),
            "suggested": sum(x["after"].get("suggested", 0) for x in targets),
            "duplicates_if_confirmed": sum(x["after"].get("duplicates_if_confirmed", x["after"]["duplicates"]) for x in targets),
            "item13_cross_client": sum(x["after"]["item13_cross_client"] for x in targets),
            "false_agreed": sum(x["after"]["false_agreed"] for x in targets),
            "false_proposal": sum(x["after"].get("false_proposal", 0) for x in targets)}


def git_head() -> str:
    try:
        r = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- output

COLUMNS = [("Prep coverage", "prep_coverage", "%"), ("Prep leaks", "prep_leaks", ""),
           ("Live answer rate", "live_answer_rate", "%"), ("Answer text in top", "live_answer_text_rate", "%"),
           ("Live leaks", "live_leaks", ""),
           ("Lookup p50 ms", "lookup_p50_ms", ""), ("Lookup p95 ms", "lookup_p95_ms", ""),
           ("Duplicates", "duplicates", ""), ("Suggested", "suggested", ""),
           ("Duplicates if PM confirms", "duplicates_if_confirmed", ""), ("Wrong identity", "wrong_identity", ""),
           ("Item 13 cross-client", "item13_cross_client", ""), ("False agreed", "false_agreed", ""),
           ("False proposal", "false_proposal", "")]


def row_of(x: dict) -> dict:
    return {"prep_coverage": x["prep"]["coverage"], "prep_leaks": len(x["prep"]["leaks"]),
            "live_answer_rate": x["live"]["answer_rate"], "live_answer_text_rate": x["live"]["answer_text_rate"],
            "live_leaks": x["live"]["leaks"],
            "lookup_p50_ms": x["live"]["p50_ms"], "lookup_p95_ms": x["live"]["p95_ms"],
            "duplicates": x["after"]["duplicates"], "suggested": x["after"].get("suggested"),
            "duplicates_if_confirmed": x["after"].get("duplicates_if_confirmed"), "wrong_identity": x["after"]["wrong_identity"],
            "item13_cross_client": x["after"]["item13_cross_client"], "false_agreed": x["after"]["false_agreed"],
            "false_proposal": x["after"].get("false_proposal")}


def fmt(v, unit: str) -> str:
    if v is None:
        return "n/a"
    return "{}{}".format(v, unit)


def table(res: dict) -> str:
    head = "| Target | " + " | ".join(c[0] for c in COLUMNS) + " |"
    sep = "|---|" + "---:|" * len(COLUMNS)
    lines = [head, sep]
    for x in res["targets"]:
        r = row_of(x)
        lines.append("| {} | ".format(x["id"]) + " | ".join(fmt(r[k], u) for _, k, u in COLUMNS) + " |")
    t = res["totals"]
    lines.append("| **All ({})** | ".format(res["variant"]) + " | ".join("**" + fmt(t[k], u) + "**" for _, k, u in COLUMNS) + " |")
    return "\n".join(lines)


V2_COLUMNS = [("Rows passed", lambda r: "{}/{}".format(r["rows_passed"], r["rows_evaluated"])),
              ("Not evaluated", lambda r: r["rows_not_evaluated"]),
              ("Hop rate", lambda r: fmt(r["hop_answer_rate"]["rate"], "%")),
              ("Paraphrase rate", lambda r: fmt(r["paraphrase_answer_rate"]["rate"], "%")),
              ("Ukrainian", lambda r: fmt(r["paraphrase_uk"]["rate"], "%")),
              ("Modality", lambda r: fmt(r.get("modality_accuracy"), "%")),
              ("Derived as confirmed", lambda r: fmt(r.get("derived_confirmed"), "")),
              ("Timeline recall", lambda r: fmt(r["timeline_recall"], "%")),
              ("Evidence recall", lambda r: fmt(r["evidence_recall"]["rate"], "%")),
              ("Quote validity", lambda r: fmt(r["quote_validity"], "%")),
              ("Future leaks", lambda r: r["future_leaks"]), ("Stale", lambda r: r["stale_returned"]),
              ("Excluded leaks", lambda r: r["excluded_leaks"]), ("Client leaks", lambda r: r["client_leaks"]),
              ("Shared kept", lambda r: fmt(r["shared_facts_kept"]["rate"], "%")),
              ("Live client/excluded leaks", lambda r: "{}/{}".format(r["live_client_leaks"], r["live_excluded_leaks"])
               if "live_client_leaks" in r else "n/a"),
              ("Fixture hits/misses", lambda r: "{}/{}".format(r["fixture"]["hits"], r["fixture"]["misses"]) if r.get("fixture") else "n/a"),
              ("Live p50/p95 ms", lambda r: "{}/{}".format(r["latency"]["live_p50_ms"], r["latency"]["live_p95_ms"])),
              ("Prep p50/p95 ms", lambda r: "{}/{}".format(r["latency"]["prep_p50_ms"], r["latency"]["prep_p95_ms"])
               if r["latency"]["prep_p50_ms"] is not None else "n/a"),
              ("Segments", lambda r: r["index_cost"]["segments"]),
              ("Re-extract section/revert/move", lambda r: "{}/{}/{}".format(r["index_cost"]["changed_section_resegments"],
                                                                             r["index_cost"]["revert_resegments"],
                                                                             r["index_cost"]["move_resegments"]))]


def table_v2(res: dict) -> str:
    v2 = res.get("v2")
    if not v2:
        return ""
    lines = ["Gold v2 (sha256 {}..., C ready: {})".format(v2["gold_sha256"][:12], ", ".join(v2["ready"])), "",
             "| {} set | ".format(res["variant"]) + " | ".join(c[0] for c in V2_COLUMNS) + " |",
             "|---|" + "---:|" * len(V2_COLUMNS)]
    for s, modes in v2["sets"].items():
        for m, r in modes.items():
            if "skipped" in r:
                lines.append("| {} {} | skipped: {} |".format(s, m, r["skipped"]))
                continue
            lines.append("| {} {} | ".format(s, m) + " | ".join(str(f(r)) for _, f in V2_COLUMNS) + " |")
    gate = v2.get("gate")
    if gate:
        failed = [k for k, x in gate["checks"].items() if x is False]
        skipped = [k.split(" ")[0] for k, x in gate["checks"].items() if x is None]
        note = " ({} not evaluated)".format(", ".join(skipped)) if skipped else ""
        lines += ["", "{} gate: {}".format("S2+S3+S4" if gate.get("s4") else "S2+S3" if gate.get("s3") else "S2","PASS" + note if gate["pass"] else "FAIL: " + ", ".join(failed))]
        if gate.get("open"):
            lines.append("Not gated, open: " + "; ".join(gate["open"]))
    return "\n".join(lines)


def misses_v2(res: dict) -> str:
    out = []
    for s, modes in (res.get("v2") or {}).get("sets", {}).items():
        for m, r in modes.items():
            for row in r.get("rows") or []:
                if row["status"] == "not_evaluated":
                    continue
                leaks = {k: x for k, x in row["leaks"].items() if x}
                if row["status"] != row["expected"] or leaks:
                    out.append("- {} {} › {}: {} (expected {}){}".format(
                        s, m, row["id"], row["status"], row["expected"], " leaks {}".format(leaks) if leaks else ""))
    return "\n".join(out)


def misses(res: dict) -> str:
    out = []
    for x in res["targets"]:
        for f in x["prep"]["facts"]:
            if not f["present"]:
                out.append("- prep miss {} › {} (in {})".format(x["id"], f["id"], f["from"]))
        for q in x["live"]["questions"]:
            if not q["ok"] or not q["answer_in_top"]:
                out.append("- live {} {} › \"{}\" top: {}".format("miss" if not q["ok"] else "source ok, answer text missing",
                                                                   x["id"], q["q"], ", ".join(q["top"]) or "nothing"))
    return "\n".join(out)


def save(res: dict, tag: str = "") -> Path:
    """results/<variant>-<date>[-<tag>].json; a file already there (a committed baseline) is never overwritten: the
    new run gets -2, -3, ..."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    base = "{}-{}".format(res["variant"], res["date"]) + ("-" + re.sub(r"[^\w.-]+", "-", tag) if tag else "")
    p, n = RESULTS / (base + ".json"), 1
    while p.exists():
        n += 1
        p = RESULTS / "{}-{}.json".format(base, n)
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def newest(name: str) -> dict:
    p = Path(name)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    found = sorted(RESULTS.glob("{}-*.json".format(name)), key=lambda f: f.stem)     # a labeled same-day result is newer
    if not found:
        raise SystemExit("no results for variant {} in {}".format(name, RESULTS.name))
    return json.loads(found[-1].read_text(encoding="utf-8"))


LOWER_IS_BETTER = {"prep_leaks", "live_leaks", "lookup_p50_ms", "lookup_p95_ms", "duplicates", "duplicates_if_confirmed",
                   "wrong_identity",
                   "item13_cross_client", "false_agreed", "false_proposal"}


def compare(a: dict, b: dict) -> str:
    lines = []
    if a.get("fixture_sha") != b.get("fixture_sha") or a.get("gold_sha") != b.get("gold_sha"):
        lines.append("Note: the fixture or the gold file differs between the two runs; the numbers are not comparable 1:1.\n")
    lines += ["| Metric | {} | {} | Delta | Better |".format(a["variant"], b["variant"]), "|---|---:|---:|---:|:---:|"]
    for label, k, unit in COLUMNS:
        x, y = a["totals"].get(k), b["totals"].get(k)
        if x is None or y is None:
            d, better = "n/a", ""
        else:
            d = round(y - x, 2)
            better = "" if d == 0 else ("yes" if (d < 0) == (k in LOWER_IS_BETTER) else "no")
            d = ("+" if d > 0 else "") + str(d) + unit
        lines.append("| {} | {} | {} | {} | {} |".format(label, fmt(x, unit), fmt(y, unit), d, better))
    for s in SETS:                                   # gold v2: the run without vectors of each set
        ra = ((a.get("v2") or {}).get("sets") or {}).get(s, {}).get("no_vectors")
        rb = ((b.get("v2") or {}).get("sets") or {}).get(s, {}).get("no_vectors")
        if ra and rb and "skipped" not in ra and "skipped" not in rb:
            lines += ["", "| Gold v2 {} | {} | {} |".format(s, a["variant"], b["variant"]), "|---|---:|---:|"]
            lines += ["| {} | {} | {} |".format(label, f(ra), f(rb)) for label, f in V2_COLUMNS]
    return "\n".join(lines)


# ---------------------------------------------------------------- the register of the fixture

def regen_register(gold_path: Path = GOLD) -> Path:
    """live/commitments.jsonl of the fixture, written by the app's own record_notes from the past calls' notes in date
    order (created = the call's day), so its format is exactly what the engine writes."""
    gold = load_gold(gold_path)
    live = PROJECT / "live"
    reg = live / CM.FILE
    if reg.exists():
        reg.unlink()
    notes = sorted(live.glob("*/*.notes.md"), key=lambda p: p.name)
    for n in notes:
        call = n.name.replace(".notes.md", ".md")
        m = re.match(r"^(\d{4}-\d\d-\d\d)-(\d\d)(\d\d)-", call)
        day = "{}T{}:{}:00+00:00".format(m.group(1), m.group(2), m.group(3))
        with clock(day):
            CM.record_notes(str(live), call=call, notes=n.read_text(encoding="utf-8"), aliases=list(gold["aliases"]),
                            people={})
    return reg


class Recorder:
    """Wraps the worker's transport and keeps every raw answer by the seg_hash of the segment it was asked for."""

    def __init__(self, inner):
        self.inner = inner
        self.answers: Dict[str, List[str]] = {}
        self.base: Dict[str, int] = {}
        self.tokens: Dict[str, List[int]] = {}
        self._schema = None

    def schema(self):
        if self._schema is None:
            self._schema = EX.load_rules()["schema"]
        return self._schema

    def start(self, req: dict):
        h = self.inner.start(req)
        rec = self

        class H:
            def done(self):
                return h.done()

            def cancel(self):
                h.cancel()

            def result(self):
                a = h.result()
                if not a.get("error") and req.get("seg_hash"):
                    # only answers the worker keeps: a cut answer (split next) or one that does not parse (repaired
                    # next) is paid and counted but not replayed (PB-103 review r1)
                    text = a.get("text") or ""
                    if not EX.cut(a) and EX.parse(text, rec.schema())[0] is not None:
                        rec.answers.setdefault(req["seg_hash"], []).append(text)
                    rec.base.setdefault(req["seg_hash"], int(req.get("base_line") or 0))
                    t = rec.tokens.setdefault(req["seg_hash"], [0, 0])
                    t[0] += int(a.get("in_tokens") or 0)
                    t[1] += int(a.get("out_tokens") or 0)
                return a
        return H()


def record_extract(route: str, gold_path: Path = GOLD, gold2_path: Path = GOLD2, out_dir: Path = EXTRACT,
                   transport=None, settings: Optional[dict] = None) -> dict:
    """--extract <route>: extract the gold v2 small set on a real route through the worker (this sends the invented
    fixture text to that route's provider; never run by the gate), write fixtures/extract/<seg_hash>.json (raw model
    output, review by deletion only) and manifest.json (gold sha256, route, model, extractor, calibration).

    All or nothing (PB-103 review r1): the answers go to a new folder next to out_dir, which replaces out_dir only when
    every pass ended `done` on the route asked for (no switch to local, no failed segment) and the new folder replays
    the small set with no miss and no unparsable answer. Otherwise out_dir is left as it was and the run exits
    non-zero, so a pass stopped at a ceiling never mixes real answers with the authored ones."""
    gold, gold2 = load_gold(gold_path), load_gold(gold2_path)
    rec = Recorder(transport or W.ChildTransport())
    runs: List[dict] = []
    bad: List[str] = []
    # never the model on this Mac in place of the route asked for, whatever the environment says
    # topics: lazy, the extraction recording sends no summary request (S4 records summaries with --record-topics)
    settings = dict(settings or {}, route=route, no_local=True, local_overflow=False, extract=True, topics="lazy")
    with tempfile.TemporaryDirectory(prefix="g0-extract-") as tmp, store_env(Path(tmp)):
        def extract(ctx):
            r = W.extract_pass(ctx["store"], str(ctx["root"]), settings, rec)
            runs.append(r)
            items = ctx["store"].q("SELECT status, COUNT(*) n FROM run_item WHERE run_id=? AND status NOT IN"
                                   " ('done','cached') GROUP BY status", (r["id"],))
            bad.extend("{} {}".format(i["n"], i["status"]) for i in items)
        ctx = build_v2(Path(tmp) / "rec", gold, gold2, False, None, after_pass=extract)
        cost = index_cost(ctx, None)
        ctx["store"].close()
    why = ["pass {} ended {}{}".format(i + 1, r["status"], ": " + str(r.get("error")) if r.get("error") else "")
           for i, r in enumerate(runs) if r["status"] != "done"]
    why += ["a pass switched from {} to {}".format(r["fallback_from"], r["route"]) for r in runs
            if r.get("fallback_from") or r.get("route") != route]
    why += ["segments not answered: " + ", ".join(sorted(set(bad)))] if bad else []
    why += ["no answer recorded"] if not rec.answers else []
    if why:
        raise SystemExit("G0 --extract {}: nothing recorded, {} left as it was ({})".format(
            route, out_dir.name, "; ".join(why)))
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    new = Path(tempfile.mkdtemp(prefix="." + out_dir.name + "-new-", dir=out_dir.parent))
    old = None
    try:
        manifest = _write_recording(new, route, rec, runs, cost, gold, gold2, gold2_path)
        if out_dir.exists():
            old = Path(tempfile.mkdtemp(prefix="." + out_dir.name + "-old-", dir=out_dir.parent))
            out_dir.rename(old / out_dir.name)
        new.rename(out_dir)
    finally:
        if old is not None and not out_dir.exists():
            (old / out_dir.name).rename(out_dir)          # the swap failed half way: the old fixture comes back
        for d in (new, old):
            if d is not None and d.exists():
                shutil.rmtree(d)
    return manifest


def _write_recording(out_dir: Path, route: str, rec: "Recorder", runs: List[dict], cost: dict, gold: dict,
                     gold2: dict, gold2_path: Path) -> dict:
    """The answers and manifest of a complete --extract run into the new (empty) folder; replaying it must hit every
    small-set segment and parse every answer (replay_fixture raises SystemExit otherwise)."""
    ex = runs[0]["extractor"] if runs else ""
    for h, answers in rec.answers.items():
        (out_dir / (h + ".json")).write_text(json.dumps(
            {"seg_hash": h, "extractor": ex, "route": route, "base_line": rec.base.get(h, 0), "answers": answers,
             "in_tokens": rec.tokens[h][0], "out_tokens": rec.tokens[h][1]}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    sent = sum(int(r.get("requests") or 0) for r in runs)
    outs = sum(int(r.get("out_tokens") or 0) for r in runs)
    pcts = [max(0.0, float(r["pct_after"]) - float(r["pct_before"])) for r in runs
            if r.get("pct_after") is not None and r.get("pct_before") is not None]
    cal = {"measured": True, "segments_recorded": len(rec.answers), "requests": sent,
           "out_tokens_per_segment": round(outs / len(rec.answers), 1) if rec.answers else None,
           "catalog_tokens_per_request": round(sum(float(r.get("catalog_tokens") or 0) for r in runs) / sent, 1) if sent else None,
           "segments_per_document": cost["segments_per_document"],
           "usd": round(sum(float(r.get("usd") or 0) for r in runs), 4),
           "pct_of_5h_window": round(sum(pcts), 2) if pcts else None,
           "seconds": round(sum(float(r.get("seconds") or 0) for r in runs), 1),
           "statuses": sorted({r["status"] for r in runs})}
    manifest = {"gold_sha256": hashlib.sha256(Path(gold2_path).read_bytes()).hexdigest(), "route": route,
                "model": runs[0]["model"] if runs else "", "extractor": ex, "recorded_at": dt.datetime.now().isoformat(
                    timespec="seconds"), "calibration": cal}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    cal.update(extract_quality(out_dir, gold, gold2, Path(gold2_path)))     # recorded, not gated (spec section 17 S3)
    if cal.get("fixture_misses"):
        raise SystemExit("G0 --extract {}: nothing recorded, {} small-set segments have no answer".format(
            route, cal["fixture_misses"]))
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def extract_quality(fixture_dir: Path, gold: dict, gold2: dict, gold2_path: Path) -> dict:
    """--extract calibration of quality: C on the small set (no vectors) replaying `fixture_dir`. precision = items the
    validator kept / items the model returned (a quote that is not verbatim at its lines, an unknown enum, another
    client's quote or a link without a word of both ends is dropped); recall = gold v2 facts found / gold facts over
    the rows C evaluates; hop and paraphrase answer rates as in the results JSON. Recorded, not gated."""
    EVIDENCE_ROWS.clear()
    EVIDENCE_ROWS.update(r["id"] for r in gold2["rows"] if r.get("evidence"))
    SHARED_ROWS.clear()
    SHARED_ROWS.update(r["id"] for r in gold2["rows"] if r.get("shared"))
    with tempfile.TemporaryDirectory(prefix="g0-extract-q-") as tmp, store_env(Path(tmp)):
        ctx = build_v2(Path(tmp) / "q", gold, gold2, False, None)
        ctx["gold_sha256"] = hashlib.sha256(gold2_path.read_bytes()).hexdigest()
        try:
            r = evaluate_v2(variant("C"), ctx, gold2, "small", None, fixture_dir=fixture_dir)
        finally:
            ctx["store"].close()
    fx = r["fixture"] or {}
    returned = int(fx.get("items_kept") or 0) + int(fx.get("items_dropped") or 0)
    ev = [x for x in r["rows"] if x["status"] != "not_evaluated"]
    found = sum(1 for x in ev for f in x.get("facts") or [] if f["found"])
    facts = sum(len(x.get("facts") or []) for x in ev)
    return {"precision": pct(int(fx.get("items_kept") or 0), returned), "items_returned": returned,
            "recall": pct(found, facts), "facts_asked": facts,
            "hop_answer_rate": r["hop_answer_rate"], "paraphrase_answer_rate": r["paraphrase_answer_rate"],
            "rows_passed": r["rows_passed"], "rows_evaluated": r["rows_evaluated"],
            "fixture_misses": int(fx.get("misses") or 0),
            "definitions": {"precision": "items the validator kept / items the model returned (not against gold)",
                            "recall": "gold v2 facts found / gold facts over the rows C evaluates, small set, no vectors"}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variant", choices=VARIANTS, default="A")
    ap.add_argument("--compare", nargs=2, metavar=("X", "Y"), help="variants (or result files) to compare")
    ap.add_argument("--no-save", action="store_true", help="print only, write no results file")
    ap.add_argument("--tag", "--label", dest="tag", default="",
                    help="suffix of the results file name (A-<date>-<tag>.json, keeps a same-day result)")
    ap.add_argument("--set", choices=("small", "scale", "both", "none"), default="both",
                    help="gold v2 sets to run (none: the v1 targets only)")
    ap.add_argument("--regen-register", action="store_true")
    ap.add_argument("--extract", metavar="ROUTE", choices=("api", "code", "local", "openai", "codex", "gemini"),
                    help="record C's extraction fixture on a real route (sends the invented fixture text to it)")
    ap.add_argument("--record-topics", action="store_true",
                    help="write C's topic summary fixture from authored answers (no model, no network)")
    a = ap.parse_args(argv)
    if a.record_topics:
        print(json.dumps(record_topics(), indent=1))
        return 0
    if a.extract:
        m = record_extract(a.extract)
        print(json.dumps(m, indent=1))
        return 0
    if a.regen_register:
        print("wrote", regen_register().relative_to(HERE))
        return 0
    if a.compare:
        print(compare(newest(a.compare[0]), newest(a.compare[1])))
        return 0
    res = run(a.variant, sets={"both": SETS, "none": ()}.get(a.set, (a.set,)))
    if a.tag:
        res["label"] = re.sub(r"[^\w.-]", "", a.tag)
    print(table(res))
    m = misses(res)
    if m:
        print("\n" + m)
    if res.get("v2"):
        print("\n" + table_v2(res))
        m = misses_v2(res)
        if m:
            print("\n" + m)
    if not a.no_save:
        print("\nwrote", save(res, a.tag).relative_to(HERE))
    gate = (res.get("v2") or {}).get("gate")
    return 1 if gate and not gate["pass"] else 0      # a failing S2 gate fails the run (the result is still saved)


if __name__ == "__main__":
    raise SystemExit(main())
