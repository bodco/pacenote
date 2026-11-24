"""PB-52 G2 (S3): the extraction pass (`evidence pass`, spec sections 8.1, 8.5, 8.6, 9.1 and 13.2).

    extract_pass(store, root, settings, transport=None) -> run summary

Order: pending revisions of active sources (transcripts of the last 14 days, notes, project state, files changed in
the last 30 days, site pages, then the rest newest first), segment by segment. A segment whose seg_hash has a
segment_cache row with this extractor is re-validated against the new snippets and written at no model cost
(`cached`). Every other segment is one request on the evidence route, with the output cap, through the ceiling check
(pass, project day, all projects day; USD before the request with the full cap, percent read back after every
request on plan routes, minutes on local). A reply cut at the cap is split at a snippet boundary down to one snippet,
the merged output cached under the parent seg_hash. A parse failure gets one repair retry; a second marks the segment
`invalid`. `call-active.json` stops the pass before any request, and kills a request in flight (`paused_call`).
State requests (hide, revoke, override) are applied before each segment and at exit; a source no longer active is
skipped. Spend goes to spend-ledger.jsonl with job `evidence`; account usage to ai-accounts.json.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

from .. import cost as COST
from .. import routing as R
from . import extract as X
from . import ingest as IG
from . import segments as SG
from . import store as S

USD_ROUTES = ("api", "openai", "gemini")
PCT_ROUTES = ("codex", "code")
AI_YAML = X.RULES_DIR / "ai.yaml"


# ---------------------------------------------------------------- settings and models

def models(route: str, ai_yaml: Optional[Path] = None) -> Dict[str, str]:
    """{"extract", "summary"} of a route from rules/ai.yaml > evidence; without the map routing.PING_MODEL and the
    after entry of the provider's JOB_DEFAULTS."""
    from ..gemini_provider import JOB_DEFAULTS as GJ
    from ..openai_provider import JOB_DEFAULTS as OJ
    after = {"openai": OJ.get("after"), "gemini": GJ.get("after")}
    try:
        m = (yaml.safe_load(Path(ai_yaml or AI_YAML).read_text(encoding="utf-8")) or {}).get("evidence") or {}
    except (OSError, ValueError):
        m = {}
    if route == "local" and os.environ.get("LT_LOCAL_MODEL"):
        local = os.environ["LT_LOCAL_MODEL"]           # the PM's model on this Mac wins over the map
        return {"extract": local, "summary": local}
    local = "gemma4:26b"
    ext = (m.get("extract") or {}).get(route) or (local if route == "local" else R.PING_MODEL.get(route))
    summ = (m.get("summary") or {}).get(route) or (local if route == "local" else after.get(route) or R.PING_MODEL.get(route))
    return {"extract": ext, "summary": summ}


def sent_model(route: str, model: str) -> str:
    """The model the route's provider really sends for `model`: OpenAIProvider and GeminiProvider replace a name
    without their own prefix with their default (the request child builds them without a job model). Asked of the
    provider class itself (describe needs no key), so the worker and the provider cannot drift. PB-103 review r1."""
    if route == "openai":
        from ..openai_provider import OpenAIProvider
        return OpenAIProvider("").describe(model)["model"]
    if route == "gemini":
        from ..gemini_provider import GeminiProvider
        return GeminiProvider("").describe(model)["model"]
    return model


def pick_model(route: str, settings: dict) -> str:
    """The extraction model of a route: settings.model, else the map; then the name the provider really sends."""
    return sent_model(route, settings.get("model") or models(route, settings.get("ai_yaml"))["extract"])


def ceiling_kind(route: str) -> str:
    return "usd" if route in USD_ROUTES else "pct" if route in PCT_ROUTES else "minutes"


# the app's limits (EvidencePrefs.limits, PB-88) in the worker's ceiling names
_LIMIT_UNITS = {"usd": "usd", "pct": "pct", "min": "minutes"}
_LIMIT_SCOPES = {"pass": "pass", "day": "day", "all_day": "all_projects_day"}


def app_settings(env: Optional[dict] = None) -> dict:
    """The PM's settings as the app passes them to `evidence pass` (PB-88, spec section 17 S5): `LT_EVIDENCE_LIMITS`
    = {"pass"|"day"|"all_day": {"usd", "pct", "min"}, "extract", "local_overflow", "topics"} and `LT_LOCAL_FALLBACK`
    ("0" = the model on this Mac is off in the chain). Unset: {} (the rules' defaults). Unreadable: AI reading off,
    so a setting the worker cannot read never sends project text (PB-103 review r1)."""
    env = os.environ if env is None else env
    out: dict = {}
    if env.get("LT_LOCAL_FALLBACK") == "0":
        out["no_local"] = True
    raw = env.get("LT_EVIDENCE_LIMITS")
    if not raw:
        return out
    try:
        lim = json.loads(raw)
        if not isinstance(lim, dict):
            raise ValueError("not an object")
    except ValueError as e:
        out.update(extract=False, limits_error="LT_EVIDENCE_LIMITS unreadable: {}".format(e))
        return out
    ceilings: Dict[str, dict] = {}
    for scope, name in _LIMIT_SCOPES.items():
        for unit, kind in _LIMIT_UNITS.items():
            v = (lim.get(scope) or {}).get(unit) if isinstance(lim.get(scope), dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                ceilings.setdefault(kind, {})[name] = float(v)
    if ceilings:
        out["ceilings"] = ceilings
    if "extract" in lim:
        out["extract"] = lim["extract"] is not False      # only an explicit false turns AI reading off
    if "local_overflow" in lim:
        out["local_overflow"] = lim["local_overflow"] is True
    if lim.get("topics") in ("eager", "lazy"):
        out["topics"] = lim["topics"]
    return out


def state_path() -> Path:
    return Path(R.default_state_path())


def call_active(path: Optional[Path] = None, now: Optional[float] = None) -> bool:
    """`call-active.json` next to ai-routes.json; a file older than 6 hours or with a dead pid is stale."""
    p = Path(path) if path else state_path().with_name("call-active.json")
    try:
        st = p.stat()
    except OSError:
        return False
    if (now or time.time()) - st.st_mtime > 6 * 3600:
        return False
    try:
        pid = int(json.loads(p.read_text(encoding="utf-8") or "{}").get("pid") or 0)
    except (OSError, ValueError, TypeError):
        pid = 0
    if pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    return True


def five_hour_pct(route: str, sp: Optional[Path] = None) -> Optional[float]:
    """The used percent of the route's 5-hour window from ai-accounts.json (codex and code)."""
    try:
        d = json.loads(R.accounts_path(sp or state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for w in ((d.get(route) or {}).get("windows") or []):
        if w.get("name") == "five_hour" and w.get("used_percent") is not None:
            return float(w["used_percent"])
    return None


# ---------------------------------------------------------------- transports

class Handle:
    def done(self) -> bool: ...
    def result(self) -> dict: ...
    def cancel(self) -> None: ...


class ChildHandle(Handle):
    def __init__(self, req: dict, env: Optional[dict] = None):
        self.proc = subprocess.Popen([sys.executable, "-m", "livetips_m0.evidence.request"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     start_new_session=True, cwd=str(Path(__file__).resolve().parents[2]),
                                     env=env or dict(os.environ))
        self.out: Optional[str] = None
        self.cancelled = False
        self.t = threading.Thread(target=self._talk, args=(json.dumps(req, ensure_ascii=False),), daemon=True)
        self.t.start()

    def _talk(self, data: str) -> None:
        try:
            self.out, _ = self.proc.communicate(data)
        except Exception:  # noqa: BLE001
            self.out = ""

    def done(self) -> bool:
        return not self.t.is_alive()

    def cancel(self) -> None:
        self.cancelled = True
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)       # the child and any CLI it started
        except (ProcessLookupError, PermissionError):
            pass

    def result(self) -> dict:
        self.t.join()
        if self.cancelled:
            return {"cancelled": True}
        try:
            return json.loads((self.out or "").strip().split("\n")[-1])
        except ValueError:
            return {"error": "request: no answer"}


class ChildTransport:
    """The real transport: one child process per request (request.py)."""

    def start(self, req: dict) -> Handle:
        return ChildHandle(req)


# ---------------------------------------------------------------- spend

def ledger_path() -> Path:
    return state_path().with_name("spend-ledger.jsonl")


def ledger_row(route: str, model: str, ans: dict, usd: Optional[float], pct: Optional[float], seconds: float,
               project: str) -> None:
    """One line per request with job `evidence` (never the prompt or the reply). The shape of routing.spend_ledger
    plus pct and seconds, which the all-projects day cap sums; ProviderCaps reads route and usd as before."""
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "job": "evidence", "route": route, "model": model,
           "input": int(ans.get("in_tokens") or 0), "output": int(ans.get("out_tokens") or 0),
           "cache_read": int(ans.get("cached") or 0), "cache_write": int(ans.get("cache_write") or 0),
           "usd": round(usd, 6) if usd is not None else None, "pct": pct, "seconds": round(seconds, 3),
           "project": project, "error": bool(ans.get("error"))}
    try:
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def today_local() -> str:
    return time.strftime("%Y-%m-%d")


def all_projects_today(route: str, kind: str) -> float:
    """Today's evidence spend of every project on the route, from the shared ledger (spec section 9.1)."""
    total = 0.0
    try:
        lines = ledger_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0.0
    day = today_local()
    for ln in lines:
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if o.get("job") != "evidence" or o.get("route") != route or not str(o.get("at", "")).startswith(day):
            continue
        total += float(o.get({"usd": "usd", "pct": "pct", "minutes": "seconds"}[kind]) or 0) / (60.0 if kind == "minutes" else 1)
    return total


def project_today(store: S.Store, route: str, kind: str) -> float:
    """Today's spend of this project on the route, summed over today's extract run rows."""
    day = _dt.datetime.now().astimezone().date()
    total = 0.0
    for r in store.q("SELECT started_at, route, usd, pct_before, pct_after, seconds FROM run WHERE kind='extract'"
                     " AND (route=? OR fallback_from=?)", (route, route)):
        try:
            d = _dt.datetime.fromisoformat(str(r["started_at"]).replace("Z", "+00:00")).astimezone().date()
        except ValueError:
            continue
        if d != day:
            continue
        if kind == "usd":
            total += float(r["usd"] or 0)
        elif kind == "pct":
            total += max(0.0, float(r["pct_after"] or 0) - float(r["pct_before"] or 0))
        elif r["route"] == route:       # minutes are local's own (a run that ended on local counts all its seconds)
            total += float(r["seconds"] or 0) / 60.0
    return total


# ---------------------------------------------------------------- estimates

def tokens(text: str, route: str) -> int:
    from ..sheet import size as SZ
    return SZ.estimate(text, route)


def out_cap(route: str, model: str, cap: int) -> int:
    """Output tokens one request can bill: the cap, plus Gemini's thinking room (billed as output)."""
    if route == "gemini":
        from ..gemini_provider import THINKING_ROOM, thinking_level
        return cap + THINKING_ROOM.get(thinking_level(model), 0)
    return cap


def price(model: str, fresh: int, prefix_tokens: int, out: int, cached_ok: bool) -> Optional[float]:
    """USD of one request on the model that sends it; the prefix at the cache-read rate only on a route whose
    cached-token count was seen above 0 (spec section 9), else at the higher of the input and the cache-write rate
    (requests mark the prefix for caching, and a write costs 1.25 times input on Claude and OpenAI)."""
    p = COST.price_for(model or "")
    if not p:
        return None
    pre = prefix_tokens * (p[3] if cached_ok else max(p[0], p[1]))   # unseen cache: at most a 5-minute write
    return (fresh * p[0] + pre + out * p[4]) / 1e6


# ---------------------------------------------------------------- the pass

class Stop(Exception):
    def __init__(self, status: str, why: str = ""):
        super().__init__(why or status)
        self.status, self.why = status, why


KIND_ORDER = {"transcript": 0, "notes": 1, "project_state": 2, "file": 3, "web_page": 4}


def pending_revisions(store: S.Store, now: Optional[_dt.datetime] = None, extractor: Optional[str] = None) -> List[dict]:
    """Revisions to extract, of active sources, in the order of spec section 8.1: pending ones, and with `extractor`
    also those done by another extractor ("re-read with the new rules" sends them again; segments whose cache row has
    the current extractor stay free)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    rows = [dict(r) for r in store.q(
        "SELECT rv.id, rv.valid_from, s.kind, s.uri, s.id AS sid FROM revision rv JOIN source s ON s.id=rv.source_id"
        " WHERE (rv.extract_state='pending' OR (? <> '' AND rv.extract_state='done' AND coalesce(rv.extractor, '')<>?))"
        " AND rv.superseded_at IS NULL AND s.state='active' AND s.kind NOT IN ('register','tracker')",
        (extractor or "", extractor or ""))]

    def age_days(r) -> float:
        try:
            d = _dt.datetime.fromisoformat(str(r["valid_from"]).replace("Z", "+00:00"))
        except ValueError:
            return 9999.0
        return (now - d).total_seconds() / 86400.0

    def key(r):
        k, a = r["kind"], age_days(r)
        if k == "transcript" and a <= 14:
            g = 0
        elif k == "notes":
            g = 1
        elif k == "project_state":
            g = 2
        elif k == "file" and a <= 30:
            g = 3
        elif k == "web_page":
            g = 4
        else:
            g = 5
        return (g, a, r["id"])
    return sorted(rows, key=key)


class Pass:
    def __init__(self, store: S.Store, root: str, settings: Optional[dict] = None, transport=None,
                 rules: Optional[dict] = None, sleep: Callable[[float], None] = time.sleep):
        self.st, self.root = store, root
        self.set = dict(app_settings())             # the app's settings (env), then the caller's own win
        self.set.update(settings or {})
        self.rules = rules or X.load_rules()
        self.ex = X.extractor_id(self.rules)
        self.prefix = X.prefix(self.rules)
        self.transport = transport or ChildTransport()
        self.sleep = sleep
        self.route = self.set.get("route") or os.environ.get("LT_AI_ROUTE") or ""   # neither set: no request (off)
        self.fallback_from: Optional[str] = None
        self.kind = ceiling_kind(self.route)
        self.model = pick_model(self.route, self.set)
        self.cap = int(self.rules.get("max_output_tokens", 2000))
        self.poll = float(self.set.get("poll_s", 1.0))
        self.labels = X.project_labels(root)
        self.project = store.meta("evidence_uid") or store.path.stem
        self.errors = 0
        self.t0 = time.monotonic()
        self.t_local: Optional[float] = None      # minutes on local count from the switch to it
        self.totals = {"in": 0, "cached": 0, "out": 0, "usd": 0.0, "est_usd": 0.0, "requests": 0, "catalog_tokens": 0}
        self.run_id = 0
        self.last_est = 0.0                       # the estimate of the request in flight (USD routes)

    # ---- settings
    def ceilings(self) -> dict:
        kind = ceiling_kind(self.route)
        d = dict((self.rules.get("ceilings") or {}).get(kind) or {})
        d.update({k: v for k, v in (self.set.get("ceilings") or {}).get(kind, {}).items() if v is not None})
        return d

    # ---- run rows
    def _run_start(self) -> None:
        self.model = pick_model(self.route, self.set)
        self.kind = ceiling_kind(self.route)
        self.pct_before = five_hour_pct(self.route) if self.kind == "pct" else None
        self._pct_seen = self.pct_before        # the last reading of the account's 5-hour window
        self.pct_spent = 0.0                    # percent of the window this pass spent (sections 9.1)
        with self.st.conn:
            self.st.conn.execute("UPDATE run SET status='failed', error='killed' WHERE status='running' AND kind='extract'")
            self.run_id = self.st.conn.execute(
                "INSERT INTO run(kind, route, model, fallback_from, extractor, started_at, status, ceiling_kind,"
                " ceiling_value, pct_before, segments_done, in_tokens, cached_tokens, out_tokens, est_usd, usd)"
                " VALUES ('extract',?,?,?,?,?,'running',?,?,?,0,0,0,0,0,0)",
                (self.route, self.model, self.fallback_from, self.ex, S.now_utc(), self.kind,
                 self.ceilings().get("pass"), self.pct_before)).lastrowid

    def _run_end(self, status: str, error: str = "") -> dict:
        IG.apply_pending(self.st)                     # at exit, whatever the status (section 13.1)
        secs = time.monotonic() - self.t0
        # the plan's percent and the USD spent before a switch to local stay on the row (the day caps sum them)
        pct_after = (self.pct_before or 0.0) + self.pct_spent \
            if self.kind == "pct" or self.fallback_from in PCT_ROUTES else None
        usd_run = self.kind == "usd" or self.fallback_from in USD_ROUTES
        with self.st.conn:
            self.st.conn.execute(
                "UPDATE run SET status=?, ended_at=?, error=?, in_tokens=?, cached_tokens=?, out_tokens=?, est_usd=?,"
                " usd=?, pct_after=?, seconds=?, route=?, model=?, fallback_from=? WHERE id=?",
                (status, S.now_utc(), error or None, self.totals["in"], self.totals["cached"], self.totals["out"],
                 round(self.totals["est_usd"], 6) if usd_run else None,
                 round(self.totals["usd"], 6) if usd_run else None, pct_after, round(secs, 3),
                 self.route, self.model, self.fallback_from, self.run_id))
        r = dict(self.st.one("SELECT * FROM run WHERE id=?", (self.run_id,)))
        r["requests"] = self.totals["requests"]
        r["catalog_tokens"] = self.totals["catalog_tokens"]
        return r

    def _item(self, rid: int, n: int, seg_hash: str, status: str, in_t=0, out_t=0, dropped=0, error=None) -> None:
        with self.st.conn:
            self.st.conn.execute("INSERT OR REPLACE INTO run_item(run_id, revision_id, segment, seg_hash, status,"
                                 " in_tokens, out_tokens, dropped, error) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (self.run_id, rid, n, seg_hash, status, in_t, out_t, dropped, error))
            if status in ("done", "cached"):
                self.st.conn.execute("UPDATE run SET segments_done=segments_done+1 WHERE id=?", (self.run_id,))

    # ---- spend so far, in the ceiling's unit
    def spent_pass(self) -> float:
        if self.kind == "usd":
            return self.totals["usd"]
        if self.kind == "pct":
            return self.pct_spent           # summed per request in _spend (a reading, or the assumed percent)
        return (time.monotonic() - (self.t_local or self.t0)) / 60.0

    def check_ceiling(self, next_cost: float) -> None:
        """Before every request: stop when this request, answering at the full cap, would cross the pass, the
        project-day or the all-projects-day cap; plan routes also stop above stop_above of the 5-hour window."""
        c = self.ceilings()
        this = self.spent_pass()
        # this run's row is written at its end, so today's rows hold earlier passes only; the shared ledger already
        # holds every request of this pass too
        day = project_today(self.st, self.route, self.kind) + this
        glob = all_projects_today(self.route, self.kind)
        if self.kind == "minutes":
            glob += this
        if self.kind == "pct":
            used = five_hour_pct(self.route)
            if used is not None and used >= float(c.get("stop_above", 80)):
                raise Stop("ceiling", "plan window above {}%".format(c.get("stop_above", 80)))
        # the day caps first: at a day cap the pass ends even when the pass ceiling is crossed too and overflow is on
        for name, spent in (("day", day), ("all_projects_day", glob), ("pass", this)):
            limit = c.get(name)
            if limit is None:
                continue
            if spent + next_cost > float(limit) + 1e-12 or (next_cost == 0 and spent >= float(limit)):
                if name == "pass" and self.route != "local" and self.set.get("local_overflow") \
                        and not self.set.get("no_local"):
                    # the PM's opt-in (off by default, spec sections 8.6 and 9.1): at the pass ceiling go on with the
                    # model on this Mac, under its own minutes ceilings; never another cloud route. A day cap ends
                    # the pass (9.1 "daily").
                    self.switch_local()
                    return self.check_ceiling(0.0)
                raise Stop("ceiling", name)

    # ---- one request
    def send(self, user: str, max_tokens: int, view: Optional[dict] = None) -> dict:
        if call_active():
            raise Stop("paused_call", "a call is in progress")
        req = {"route": self.route, "model": self.model, "system": self.prefix, "user": user,
               "max_tokens": max_tokens, "cache": True}
        if view is not None:            # for the G0 recorder (bench --extract); the request child ignores them
            req.update(seg_hash=view["seg_hash"], base_line=view.get("base_line", 0))
        t = time.monotonic()
        h = self.transport.start(req)
        last = 0.0
        while not h.done():
            if time.monotonic() - last >= self.poll:
                last = time.monotonic()
                if call_active():
                    h.cancel()
                    est_in = tokens(self.prefix + user, self.route)
                    self._spend({"in_tokens": est_in, "out_tokens": 0}, time.monotonic() - t, cancelled=True)
                    raise Stop("paused_call", "a call started during a request")
            time.sleep(min(0.02, self.poll))
        ans = h.result()
        if ans.get("cancelled"):
            raise Stop("paused_call", "a call started during a request")
        self._spend(ans, time.monotonic() - t)
        return ans

    def _spend(self, ans: dict, secs: float, cancelled: bool = False) -> None:
        self.totals["requests"] += 1
        self.totals["in"] += int(ans.get("in_tokens") or 0)
        self.totals["cached"] += int(ans.get("cached") or 0)
        self.totals["out"] += int(ans.get("out_tokens") or 0)
        if int(ans.get("cached") or 0) > 0:
            self.st.set_meta("cache_seen:" + self.route, "1")
            self.st.conn.commit()
        usd = None
        model = ans.get("model") or self.model          # the model that answered (PB-103 review r1)
        if self.kind == "usd":
            toks = (int(ans.get("in_tokens") or 0), int(ans.get("cache_write") or 0), 0, int(ans.get("cached") or 0),
                    int(ans.get("out_tokens") or 0))
            usd = COST.cost_usd(model, *toks)
            if usd is None:                 # never 0: an unpriced answer is charged the estimate it was let through on
                usd = self.last_est
            self.totals["usd"] += usd
        pct = None
        if ans.get("account_usage"):
            R.note_account_usage(self.route, ans["account_usage"], state_path())
        if self.kind == "pct":
            # A fresh reading moves the percent by what the account says; a request whose answer carries no usage
            # (Codex in --ephemeral mode, a cancelled request, a stale reading in ai-accounts.json) counts the assumed
            # percent (rules/evidence.yaml pct_per_request_assumed), so the pass and both day caps never see 0.
            now = five_hour_pct(self.route) if ans.get("account_usage") else None
            if now is not None and self._pct_seen is not None:
                pct = max(0.0, now - self._pct_seen)
            else:
                pct = float(self.rules.get("pct_per_request_assumed", 0.5))
            if now is not None:
                self._pct_seen = now
            self.pct_spent += pct
        ledger_row(self.route, model, ans, usd, pct, secs, self.project)

    def estimate(self, user: str) -> float:
        """The cost of one request answering at the full cap, in the ceiling's unit (0 on plan routes and local,
        which are read back after every request)."""
        if self.kind != "usd":
            return 0.0
        pre = tokens(self.prefix, self.route)
        fresh = tokens(user, self.route)
        cached_ok = self.st.meta("cache_seen:" + self.route) == "1"
        usd = price(self.model, fresh, pre, out_cap(self.route, self.model, self.cap), cached_ok)
        return usd or 0.0

    def gate(self, user: str) -> None:
        """The ceiling check before one request; the estimate is added only while the route is priced in USD (a
        switch to local inside the check leaves it out). A USD route whose model has no price ends the pass before
        the request: every estimate and spend would be 0 and the three USD caps would be off (PB-103 review r1)."""
        if self.kind == "usd" and not COST.price_for(self.model or ""):
            raise Stop("off", "the model {} has no price (livetips_m0/cost.py PRICES)".format(self.model))
        est = self.estimate(user)
        self.last_est = est
        self.check_ceiling(est)
        if self.kind == "usd":
            self.totals["est_usd"] += est

    # ---- one segment (recursive split)
    def extract_view(self, view: dict, cat: List[dict], depth: int = 0) -> tuple:
        """The parsed answer for a view, splitting a cut answer down to one snippet. Returns (obj, tokens, note)."""
        user = X.request_text(view, cat, self.labels)
        self.gate(user)
        self.totals["catalog_tokens"] += tokens(json.dumps(cat, ensure_ascii=False, separators=(",", ":")), self.route)
        ans = self.send(user, self.cap, view)
        if ans.get("error") and not X.cut(ans):
            raise RouteError(str(ans["error"]))
        toks = (int(ans.get("in_tokens") or 0), int(ans.get("out_tokens") or 0))
        if X.cut(ans):
            if len(view["snippets"]) > 1:
                a, b = X.split_view(view)
                oa, ta, na = self.extract_view(a, cat, depth + 1)
                ob, tb, nb = self.extract_view(b, cat, depth + 1)
                return X.merge([oa, ob]), (toks[0] + ta[0] + tb[0], toks[1] + ta[1] + tb[1]), (na or nb)
            # one snippet still cut: the one repair retry asking for the items that fit, marked truncated
            user2 = user + "\n\nYour previous answer was cut at the output limit. Return only the most important items" \
                           " that fit, as complete JSON."
            self.gate(user2)
            ans2 = self.send(user2, self.cap, view)
            obj, err = X.parse(ans2.get("text") or "", self.rules["schema"])
            toks = (toks[0] + int(ans2.get("in_tokens") or 0), toks[1] + int(ans2.get("out_tokens") or 0))
            if obj is None:
                raise Invalid("truncated; " + (err or ""), toks)
            return obj, toks, "truncated"
        obj, err = X.parse(ans.get("text") or "", self.rules["schema"])
        if obj is None:
            user2 = user + "\n\nYour previous answer was not valid: {}. Return the JSON only.".format(err)
            self.gate(user2)
            ans2 = self.send(user2, self.cap, view)
            toks = (toks[0] + int(ans2.get("in_tokens") or 0), toks[1] + int(ans2.get("out_tokens") or 0))
            if ans2.get("error") and not X.cut(ans2):
                raise RouteError(str(ans2["error"]))
            obj, err = X.parse(ans2.get("text") or "", self.rules["schema"])
            if obj is None:
                raise Invalid(err or "invalid", toks)
        return obj, toks, None

    def write(self, view: dict, items: dict) -> None:
        S.apply_extraction(self.st, view["revision_id"], items, run_id=self.run_id,
                           threshold=float((self.rules.get("thresholds") or {}).get("confidence", 0.7)), mark_done=False)

    def segment(self, rid: int, n: int, seg: dict) -> str:
        view = X.segment_view(self.st, rid, seg)
        view["base_line"] = view["snippets"][0]["lines"][0]
        cat = X.catalog(self.st, view, self.root, self.labels, int(self.rules.get("catalog_max", 60)))
        row = self.st.one("SELECT output FROM segment_cache WHERE seg_hash=? AND extractor=?", (seg["seg_hash"], self.ex))
        if row:
            # re-validated in full against the new snippets: quotes, offsets, the client check, known ids
            items, counts = X.validate(self.st, view, rebase(json.loads(row["output"]), view, +1), self.rules, cat,
                                       self.root, self.labels)
            self.write(view, items)
            dropped = counts["dropped"]
            with self.st.conn:
                self.st.conn.execute("UPDATE segment_cache SET last_used=? WHERE seg_hash=? AND extractor=?",
                                     (S.now_utc(), seg["seg_hash"], self.ex))
            self._item(rid, n, seg["seg_hash"], "cached", dropped=dropped)
            return "cached"
        try:
            obj, toks, note = self.extract_view(view, cat)
        except Invalid as e:
            self._item(rid, n, seg["seg_hash"], "invalid", e.toks[0], e.toks[1], error=str(e)[:200])
            return "invalid"
        self.errors = 0
        items, counts = X.validate(self.st, view, obj, self.rules, cat, self.root, self.labels)
        with self.st.conn:
            self.st.conn.execute("INSERT OR REPLACE INTO segment_cache(seg_hash, extractor, output, run_id, in_tokens,"
                                 " out_tokens, last_used) VALUES (?,?,?,?,?,?,?)",
                                 (seg["seg_hash"], self.ex, json.dumps(rebase(items, view, -1), ensure_ascii=False),
                                  self.run_id, toks[0], toks[1], S.now_utc()))
        self.write(view, items)
        self._item(rid, n, seg["seg_hash"], "done", toks[0], toks[1], counts["dropped"], note)
        return "done"

    # ---- the whole pass
    def run(self) -> dict:
        self._run_start()
        try:
            if self.set.get("limits_error"):         # the app's settings could not be read: no request at all
                raise Stop("failed", self.set["limits_error"])
            if self.set.get("extract") is False:     # "Use AI to read sources" off (the CLI skips the pass before)
                raise Stop("off", "AI reading of sources is off in Settings")
            if self.route in USD_ROUTES:
                caps = COST.ProviderCaps.from_cfg(self.set.get("cfg") or {}, str(state_path()))
                if caps is not None and caps.capped_route(self.route):
                    if self.set.get("no_local"):     # the monthly limit is reached and local is off in the chain
                        raise Stop("ceiling", "monthly limit of the provider")
                    self.switch_local()              # the monthly limit of this provider is reached
            if self.route not in R.ROUTES:
                raise Stop("off", "no route chosen for the evidence job")
            if call_active():
                raise Stop("paused_call", "a call is in progress")
            revs = pending_revisions(self.st, extractor=self.ex)
            plan = [(r, SG.of_revision(self.st, r["id"])) for r in revs]
            with self.st.conn:
                self.st.conn.execute("UPDATE run SET segments_plan=? WHERE id=?", (sum(len(s) for _r, s in plan),
                                                                                   self.run_id))
            for r, segs in plan:
                settled = 0
                for n, seg in enumerate(segs):
                    IG.apply_pending(self.st)            # between segments (section 13.1)
                    if self.st.one("SELECT state FROM source WHERE id=?", (r["sid"],))[0] != "active":
                        break                            # a hide or revoke applied mid-pass: nothing more of it is sent
                    try:
                        st = self.segment(r["id"], n, seg)
                    except RouteError as e:
                        if not self.to_local(str(e)):
                            self._item(r["id"], n, seg["seg_hash"], "failed", error=str(e)[:200])
                            self.on_route_error(str(e))
                            continue
                        try:
                            st = self.segment(r["id"], n, seg)
                        except RouteError as e2:
                            self._item(r["id"], n, seg["seg_hash"], "failed", error=str(e2)[:200])
                            self.on_route_error(str(e2))
                            continue
                    if st in ("done", "cached", "invalid"):
                        settled += 1
                if settled == len(segs):
                    with self.st.conn:
                        self.st.conn.execute("UPDATE revision SET extract_state='done', extractor=? WHERE id=?",
                                             (self.ex, r["id"]))
            return self._run_end("done")
        except Stop as s:
            return self._run_end(s.status, s.why)

    def to_local(self, err: str) -> bool:
        """The chosen route is not connected or out (no key, not installed, out of credit, signed out, a plan limit):
        extraction steps only to the model on this Mac, never to another cloud route (section 8.6)."""
        if self.route == "local" or not R.out_reason(self.route, err) or self.set.get("no_local"):
            return False
        self.switch_local()
        return True

    def switch_local(self) -> None:
        self.fallback_from, self.route = self.route, "local"
        self.t_local = time.monotonic()
        self.model = models("local", self.set.get("ai_yaml"))["extract"]
        self.kind = "minutes"
        self.errors = 0
        with self.st.conn:
            self.st.conn.execute("UPDATE run SET route='local', model=?, fallback_from=?, ceiling_kind='minutes',"
                                 " ceiling_value=? WHERE id=?", (self.model, self.fallback_from,
                                                                  self.ceilings().get("pass"), self.run_id))

    def on_route_error(self, err: str) -> None:
        if self.route == "local" and any(w in err for w in ("URLError", "Connection refused", "ConnectionRefused",
                                                            "HTTP Error 404", "not found")):
            raise Stop("off", "the model on this Mac is not installed or not running")
        self.errors += 1
        if self.errors >= int(self.rules.get("errors_to_fail", 3)):
            raise Stop("failed", "3 errors in a row: " + err[:120])
        b0, b1 = (self.rules.get("backoff_s") or [60, 3600])[:2]
        self.sleep(min(float(b1), float(self.set.get("backoff0_s", b0)) * (2 ** (self.errors - 1))))


class RouteError(Exception):
    pass


class Invalid(Exception):
    def __init__(self, why: str, toks):
        super().__init__(why)
        self.toks = toks


def rebase(items: dict, view: dict, sign: int) -> dict:
    """Cached items keep their lines relative to the segment's first file line (the texts are byte-equal, the line
    numbers may move when a section above was edited); sign -1 stores, +1 reads back for this view."""
    base = view["snippets"][0]["lines"][0] if view["snippets"] else 0
    out = {}
    for k in ("entities", "claims", "relations"):
        out[k] = []
        for it in items.get(k) or []:
            it = dict(it)
            if isinstance(it.get("lines"), list) and len(it["lines"]) == 2:
                it["lines"] = [int(it["lines"][0]) + sign * base, int(it["lines"][1]) + sign * base]
            out[k].append(it)
    return out


def extract_pass(store: S.Store, root: str, settings: Optional[dict] = None, transport=None, **kw) -> dict:
    return Pass(store, root, settings, transport, **kw).run()


# ---------------------------------------------------------------- the estimate shown before a pass (sections 9 and 10)

TYPICAL_OUT = 500           # output tokens per segment until the --extract calibration replaces it (section 9)
CATALOG_TOKENS = 2000       # the catalog per segment assumed by section 9 until --extract measures it


def estimate(store: S.Store, root: str, route: str, rules: Optional[dict] = None, settings: Optional[dict] = None) -> dict:
    """The pending segments and what extracting them costs on `route`, typical to cap, priced per request on the
    model that sends it: the real segment count of the pending revisions, cached segments free (section 8.2)."""
    rules = rules or X.load_rules()
    settings = settings or {}
    ex, pre = X.extractor_id(rules), X.prefix(rules)
    model = pick_model(route, settings)
    cap = out_cap(route, model, int(rules.get("max_output_tokens", 2000)))
    typical = int(settings.get("typical_out") or store.meta("typical_out:" + route) or TYPICAL_OUT)
    catalog_t = int(settings.get("catalog_tokens") or store.meta("catalog_tokens:" + route) or CATALOG_TOKENS)
    pre_t = tokens(pre, route)
    cached_ok = store.meta("cache_seen:" + route) == "1"
    segs = chars = fresh = 0
    docs = set()
    for r in pending_revisions(store, extractor=ex):
        for seg in SG.of_revision(store, r["id"]):
            if store.one("SELECT 1 FROM segment_cache WHERE seg_hash=? AND extractor=?", (seg["seg_hash"], ex)):
                continue
            segs += 1
            docs.add(r["id"])
            chars += seg["chars"]
            rows = store.q("SELECT text FROM snippet WHERE id IN ({})".format(",".join("?" * len(seg["snippet_ids"]))),
                           seg["snippet_ids"])
            fresh += tokens("\n".join(t["text"] for t in rows), route) + catalog_t
    out = {"route": route, "model": model, "segments": segs, "documents": len(docs), "chars": chars,
           "tokens_in": fresh + segs * pre_t, "tokens_out_typical": segs * typical, "tokens_out_cap": segs * cap,
           "cache_assumed": cached_ok, "typical_out": typical, "catalog_tokens": catalog_t}
    if route in USD_ROUTES:
        lo = price(model, fresh, segs * pre_t, segs * typical, cached_ok)
        hi = price(model, fresh, segs * pre_t, segs * cap, cached_ok)
        # an unpriced model has no estimate (None, never $0) and a pass on it ends before the first request
        out.update(unpriced=lo is None, usd_typical=round(lo, 2) if lo is not None else None,
                   usd_cap=round(hi, 2) if hi is not None else None)
    return out
