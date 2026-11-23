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

  python3 bench/g0/run.py --variant A            # prints the table, writes results/A-<date>.json
  python3 bench/g0/run.py --compare A C          # the newest result of each, metric by metric
  python3 bench/g0/run.py --regen-register       # rebuild fixtures/.../live/commitments.jsonl from the past notes

Variant A = the current code. B (wider text search) and C (graph) are added by the later GraphRAG steps.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
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
from livetips_m0 import commitments as CM  # noqa: E402
from livetips_m0.sheet import build as B  # noqa: E402
from livetips_m0.sheet import lookup as LK  # noqa: E402
from livetips_m0.sheet import tracker as TR  # noqa: E402

FIX = HERE / "fixtures"
PROJECT = FIX / "northpoint"
GOLD = HERE / "gold.yaml"
RESULTS = HERE / "results"
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


def tree_sha(*roots: Path) -> str:
    h = hashlib.sha256()
    for root in roots:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
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
    try:
        yield blocked
    finally:
        socket.create_connection = real
        os.environ.update(saved)


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
        """engine._folder_index: the project root, the sheet's folder sources in full (deep), built once."""
        return LK.FolderIndex([str(root)], exclude_prefix=exclude_prefix, deep=LK.sheet_folder_roots(sheet)).build()

    def lookup(self, question: str, root: Path, exclude_prefix: str, sheet: Optional[dict], index) -> List[dict]:
        """engine._project_snippets > own_search with no lt and no model queries (k 6, 14000 chars, 1 s)."""
        return LK.snippets(question, sheet=sheet, roots=[str(root)], k=6, queries=[], budget_chars=14000,
                           max_seconds=1.0, exclude_prefix=exclude_prefix, index=index)

    def after_call(self, live: Path, call: str, notes: str, aliases: List[str]) -> List[dict]:
        return CM.record_notes(str(live), call=call, notes=notes, aliases=aliases)


def variant(name: str):
    if name == "A":
        return VariantA()
    raise SystemExit("variant {} is not implemented in G0; the GraphRAG step that adds it plugs it in here".format(name))


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


def open_matching(items: List[dict], match: List[str]) -> List[dict]:
    return [i for i in items if i.get("status") == "open" and all(has_phrase(i.get("text", ""), [m]) for m in match)]


def run_after(v, t: dict, gold: dict, root: Path) -> dict:
    live = root / "live"
    notes = (FIX / t["dir"] / t["call"].replace(".md", ".notes.md")).read_text(encoding="utf-8")
    before = {i["id"] for i in CM.load(str(live))}
    with clock(t["start"] + "+00:00"):
        events = v.after_call(live, t["call"], notes, list(gold["aliases"]))
    items = CM.load(str(live))
    by_id = {i["id"]: i for i in items}
    added = {e["id"] for e in events if e.get("op") == "add"}
    touched = {e["id"] for e in events if e.get("op") != "add"}
    rows, dups, wrong, false_agreed = [], 0, 0, 0
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
        wrong += int(not ok)
        rows.append({"id": a["id"], "kind": a["kind"], "ok": ok, "open_items": len(m), "added": len(new),
                     "tentative_open": tentative_open})
    other = [c for c in gold["clients"] if c != t["client"]]
    cross_events = sum(1 for i in touched if client_of_series((by_id.get(i) or {}).get("series", ""), gold) in other)
    tr = TR.collect({"project": {"root": str(root)}, "today": t["start"][:10]})
    cross_tracker = sum(1 for x in tr["on_me"] + tr["waiting"]
                        if has_phrase(x["text"], ["item 13"]) and client_of_series(x.get("series", ""), gold) in other)
    return {"duplicates": dups, "wrong_identity": wrong, "false_agreed": false_agreed, "suggested": len(sugg),
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


# ---------------------------------------------------------------- the whole run

def run(variant_name: str = "A", gold_path: Path = GOLD) -> dict:
    gold = load_gold(gold_path)
    bad = check_invariants(gold)
    if bad:
        raise SystemExit("G0 fixture is broken:\n- " + "\n- ".join(bad))
    v = variant(variant_name)
    t_all = time.time()
    out = {"schema": "pacenote.bench.g0/1", "variant": variant_name, "date": dt.date.today().isoformat(),
           "commit": git_head(), "fixture_sha": tree_sha(FIX), "gold_sha": tree_sha(gold_path),
           "route": gold.get("route", "codex"), "top": gold.get("top", 3), "lookup_reps": LOOKUP_REPS, "targets": []}
    samples: List[float] = []
    with offline() as blocked, site_server(FIX / "site") as site, tempfile.TemporaryDirectory(prefix="g0-") as tmp:
        for t in sorted(gold["targets"], key=lambda x: x["start"]):
            root = fresh_copy(Path(tmp), gold)           # each target call on its own clean copy
            prep = run_prep(v, t, gold, root, site)
            live = run_live(v, t, gold, root, prep.pop("_sheet"))
            samples += live.pop("_samples")
            after = run_after(v, t, gold, root)
            out["targets"].append({"id": t["id"], "client": t["client"], "prep": prep, "live": live, "after": after})
        out["blocked_connections"] = len(blocked)
    out["totals"] = totals(out["targets"], samples)
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
            "false_agreed": sum(x["after"]["false_agreed"] for x in targets)}


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
           ("Item 13 cross-client", "item13_cross_client", ""), ("False agreed", "false_agreed", "")]


def row_of(x: dict) -> dict:
    return {"prep_coverage": x["prep"]["coverage"], "prep_leaks": len(x["prep"]["leaks"]),
            "live_answer_rate": x["live"]["answer_rate"], "live_answer_text_rate": x["live"]["answer_text_rate"],
            "live_leaks": x["live"]["leaks"],
            "lookup_p50_ms": x["live"]["p50_ms"], "lookup_p95_ms": x["live"]["p95_ms"],
            "duplicates": x["after"]["duplicates"], "suggested": x["after"].get("suggested"),
            "duplicates_if_confirmed": x["after"].get("duplicates_if_confirmed"), "wrong_identity": x["after"]["wrong_identity"],
            "item13_cross_client": x["after"]["item13_cross_client"], "false_agreed": x["after"]["false_agreed"]}


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


def save(res: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / "{}-{}{}.json".format(res["variant"], res["date"], "-" + res["label"] if res.get("label") else "")
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
                   "item13_cross_client", "false_agreed"}


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
            CM.record_notes(str(live), call=call, notes=n.read_text(encoding="utf-8"), aliases=list(gold["aliases"]))
    return reg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variant", choices=VARIANTS, default="A")
    ap.add_argument("--compare", nargs=2, metavar=("X", "Y"), help="variants (or result files) to compare")
    ap.add_argument("--no-save", action="store_true", help="print only, write no results file")
    ap.add_argument("--label", default="", help="results/<variant>-<date>-<label>.json (keeps a same-day result)")
    ap.add_argument("--regen-register", action="store_true")
    a = ap.parse_args(argv)
    if a.regen_register:
        print("wrote", regen_register().relative_to(HERE))
        return 0
    if a.compare:
        print(compare(newest(a.compare[0]), newest(a.compare[1])))
        return 0
    res = run(a.variant)
    if a.label:
        res["label"] = re.sub(r"[^\w.-]", "", a.label)
    print(table(res))
    m = misses(res)
    if m:
        print("\n" + m)
    if not a.no_save:
        print("\nwrote", save(res).relative_to(HERE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
