"""PB-52 G2 (S1): deterministic ingest of a project into its evidence store (spec sections 5, 6 and 7.1).

    ingest(store, root, hidden_calls=None, revoked=None, web_dirs=(), valid_from=None, embedder=None, ...) -> report
    apply_state(store, hidden_calls=None, revoked=None)    the state-only pass (no model, network or embed request)
    apply_pending(store)                                    the unapplied lines of the state-request log, in file order
    relabel(store, added)                                   the snippet split on an added label (spec section 5.2)

Free and exact: no model request. Vectors only through the `embedder` the caller passes (the loopback bge-m3 server,
or a stub in tests); without one, vec stays NULL and a later pass fills it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import struct
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .. import call_layout as CL
from .. import client_scope as CS
from .. import commitments as CM
from .. import identity as ID
from .. import notion_links as NL
from ..sheet import lookup as LK
from . import store as S

CALL_KINDS = ("transcript", "notes", "brief", "sheet", "link_sidecar")
NO_EXTRACT = ("brief", "sheet", "link_sidecar", "register", "tracker")
FILE_KINDS = ("file", "transcript", "notes", "brief", "sheet", "link_sidecar", "project_state", "web_page")
START_RX = re.compile(r"^-\s*(?:Start|Початок):\s*(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d(?::\d\d)?)", re.M)
STAMP_RX = re.compile(r"^\s*(?:-\s*)?\[(\d{1,2}):(\d\d)(?::(\d\d))?\]")
SPEAKER_RX = re.compile(r"^\s*\[[\d:]+\]\s*([^:(]{1,60}?)\s*(?:\([a-z]{2}\))?:")
DATE_RX = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
NOTION_ID_RX = re.compile(r"([0-9a-f]{32})|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
GOOGLE_COPY_RX = re.compile(r"^google-file-([A-Za-z0-9_-]{1,64})\.[^.]+$")
SECTION = {"decisions": "d", "рішення": "d", "risks": "r", "ризики": "r"}


# ---------------------------------------------------------------- text and time

def normalise(text: str) -> str:
    """Line endings normalised and trailing spaces dropped (spec section 5.2 item 2)."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(ln.rstrip() for ln in t.split("\n")).strip("\n")


def read_text(path: str) -> str:
    from ..sheet import readers
    p = str(path)
    if p.endswith((".json", ".jsonl")):
        try:
            return normalise(LK._jsonl_text(Path(p).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            return ""
    r = readers.read(p)
    return normalise(r.get("text") or "")


def _local_utc(day: str, clock: str = "00:00:00") -> str:
    """A local wall time of this Mac (the recorder writes call starts without a zone) as full UTC."""
    if len(clock) == 5:
        clock += ":00"
    return S.utc(_dt.datetime.fromisoformat("{}T{}".format(day, clock)))


def _name_day(rel: str) -> str:
    m = DATE_RX.search(os.path.basename(rel)) or DATE_RX.search(rel)
    if not m:
        return ""
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


def call_start(text: str, rel: str) -> str:
    """The transcript header "- Start: YYYY-MM-DD HH:MM:SS" (local time, spec section 6), else the date in the
    folder or file name at 00:00 local; "" when neither."""
    m = START_RX.search(text[:2000] or "")
    if m:
        try:
            return _local_utc(m.group(1), m.group(2))
        except ValueError:
            pass
    d = _name_day(rel)
    return _local_utc(d) if d else ""


def line_times(text: str, start_utc: str) -> Dict[int, str]:
    """{line number: UTC} of the transcript's [HH:MM:SS] lines: local clock times on the call start's local date,
    the next date once the clock goes past midnight."""
    if not start_utc:
        return {}
    start = _dt.datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone()
    day = start.date()
    out, prev = {}, None
    for n, ln in enumerate(text.split("\n"), 1):
        m = STAMP_RX.match(ln)
        if not m:
            continue
        sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)
        if prev is not None and sec < prev:
            day = day + _dt.timedelta(days=1)
        prev = sec
        local = _dt.datetime.combine(day, _dt.time(sec // 3600 % 24, sec // 60 % 60, sec % 60))
        out[n] = S.utc(local)
    return out


def first_valid_from(kind: str, rel: str, text: str, mtime: float, observed: str) -> str:
    """valid_from of revision 1 (spec section 6): a transcript starts at its call start; every other file-backed kind at
    the later of its mtime and the date in its name at 00:00 local. Never later than observed_at."""
    if kind == "transcript":
        v = call_start(text, rel)
        if v:
            return min(v, observed)
    cands = [S.utc(mtime)] if mtime else []
    d = _name_day(rel)
    if d:
        cands.append(_local_utc(d))
    v = max(cands) if cands else observed
    return min(v, observed)


def next_valid_from(prev_from: str, mtime: float, observed: str) -> str:
    """Revision 2 and later: the mtime when it is later than the previous revision's valid_from (cut to observed_at),
    otherwise observed_at (a restore or a sync with an older mtime never shows the change too early)."""
    m = S.utc(mtime) if mtime else ""
    if m and m > prev_from:
        return min(m, observed)
    return max(observed, prev_from)


# ---------------------------------------------------------------- vectors

def pack(vec) -> Optional[bytes]:
    if vec is None:
        return None
    vals = [float(x) for x in vec]
    return struct.pack("<{}e".format(len(vals)), *vals)


class LoopbackEmbedder:
    """The bundled bge-m3 server on 127.0.0.1 (embed_server.py). Down or slow: None, vectors stay NULL."""

    def __init__(self, port: int = 11435, timeout: float = 20.0):
        self.port, self.timeout, self.requests = port, timeout, 0

    def __call__(self, texts: List[str]):
        import socket
        import urllib.request
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                pass
        except OSError:
            return None
        self.requests += 1
        body = json.dumps({"model": "bge-m3", "input": texts}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:{}/api/embed".format(self.port), data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8")).get("embeddings")
        except (OSError, ValueError):
            return None


# ---------------------------------------------------------------- the walk

def _rel(root: str, p: str) -> str:
    return os.path.relpath(p, root).replace(os.sep, "/")


def classify_live(rel: str) -> Optional[dict]:
    """A file under live/: its kind, origin and call key, or None (logs, service files, the register)."""
    name = rel.rsplit("/", 1)[-1]
    parts = rel.split("/")
    if parts[:2] == ["live", "google"]:
        m = GOOGLE_COPY_RX.match(name)
        return {"kind": "file", "origin": "primary", "ext_id": m.group(1)[:16] if m else None} if m else None
    if ".work" in parts and "web" in parts:
        if name.endswith(".txt"):
            return {"kind": "web_page", "origin": "primary"}
        return None
    p = CL.parse(name)
    if not p:
        return None
    rest = p["rest"]
    key = ID.call_stem(name)
    if rest.endswith(".notion.json"):
        return {"kind": "link_sidecar", "origin": "pm", "call_key": key}
    if rest in (".notes.md", ".app.notes.md"):
        return {"kind": "notes", "origin": "derived", "call_key": key}
    if rest in (".brief.md", ".app.brief.md"):
        return {"kind": "brief", "origin": "derived", "call_key": key}
    if rest in (".sheet.md", ".sheet.json"):
        return {"kind": "sheet", "origin": "derived", "call_key": key}
    if rest in (".md", ".app.md"):
        return {"kind": "transcript", "origin": "primary", "call_key": key}
    return None


def _notion_ext(path: str) -> Optional[str]:
    m = NOTION_ID_RX.search(os.path.basename(path))
    if not m:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                m = NOTION_ID_RX.search(f.read(2000))
        except OSError:
            m = None
    return (m.group(1) or m.group(2)).replace("-", "").lower() if m else None


def walk(root: str, web_dirs: Iterable[str] = ()) -> Dict[str, dict]:
    """{uri: {kind, origin, path, call_key, ext_id, title}} of every file source of the project (spec section 5.1)."""
    from ..sheet import sources as SRC
    out: Dict[str, dict] = {}
    files, _excluded = SRC.folder_files(root)
    for c in files:
        rel = _rel(root, c["path"])
        if rel.startswith("pacenote/") and rel.rsplit("/", 1)[-1] in ("decisions.md", "answer-cards.md"):
            out[rel] = {"kind": "project_state", "origin": "primary", "path": c["path"]}
        else:
            out[rel] = {"kind": "file", "origin": "primary", "path": c["path"]}
    live = os.path.join(root, "live")
    for d, dirs, names in os.walk(live):
        dirs[:] = sorted(x for x in dirs if x in (".work", "web") or not x.startswith(".")
                         or os.path.basename(d) == ".work")
        for n in sorted(names):
            if n.startswith("."):
                continue
            p = os.path.join(d, n)
            rel = _rel(root, p)
            info = classify_live(rel)
            if info:
                out[rel] = dict(info, path=p)
    notion = os.path.join(root, "_notion")
    for d, dirs, names in os.walk(notion):
        dirs[:] = sorted(x for x in dirs if not x.startswith((".", "_archived")))
        for n in sorted(names):
            p = os.path.join(d, n)
            if n.startswith(".") or not n.lower().endswith((".md", ".txt")):
                continue
            rel = _rel(root, p)
            kind = "project_state" if "current state" in n.lower() else "file"
            out[rel] = {"kind": kind, "origin": "derived" if kind == "project_state" else "primary", "path": p,
                        "ext_id": _notion_ext(p)}
    for wd in web_dirs or ():
        wd = os.path.realpath(os.path.expanduser(wd))
        for d, dirs, names in os.walk(wd):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for n in sorted(names):
                if n.endswith(".txt") and not n.startswith("."):
                    p = os.path.join(d, n)
                    out[p] = {"kind": "web_page", "origin": "primary", "path": p}
    return out


# ---------------------------------------------------------------- marks

def path_marks(rel: str) -> set:
    """The word rule of client_scope.side on a path: every folder and file-name word (and two-word name). Not cut to
    the label universe: a folder created later must find the mark without a relabel (spec section 5.3)."""
    parts = rel.split("/")
    parts[-1] = os.path.splitext(parts[-1])[0]
    return set().union(*(CS._names(p) for p in parts))


def _client_set(names: Iterable[str], labels: set) -> str:
    return "|".join(sorted(set(names) & labels))


# ---------------------------------------------------------------- ingest

class Ctx:
    def __init__(self, store: S.Store, root: str, universe: List[str], labels: List[str], embedder, observed: str,
                 valid_from: Optional[str], between: Optional[Callable] = None):
        self.store, self.conn, self.root = store, store.conn, root
        self.universe, self.labels = set(universe), set(labels)
        self.embedder, self.observed, self.valid_from = embedder, observed, valid_from
        self.between = between
        self.stems, self.rids = deny_lists(store)
        self.report = {"new_revisions": 0, "unchanged": 0, "touched": 0, "moved": 0, "deleted": 0, "skipped": 0,
                       "embed_requests": 0, "vectors_copied": 0, "sources": 0}


def deny_lists(store: S.Store) -> tuple:
    """(call stems, document ids) a source is denied for from its first write: the lists the last state-only pass
    applied plus what the store already denies (spec section 5.2 item 8, section 12: there is no window)."""
    stems = set(store.meta_json("hidden_calls", []) or [])
    stems |= {r[0] for r in store.q("SELECT call_key FROM source WHERE state='excluded' AND state_reason='hidden call'")
              if r[0]}
    rids = set(store.meta_json("revoked", []) or [])
    rids |= {r[0] for r in store.q("SELECT ext_id FROM source WHERE state='revoked' AND ext_id IS NOT NULL")}
    return stems, rids


def born_state(ctx: Ctx, kind: str, call_key: Optional[str], ext_id: Optional[str]) -> tuple:
    if kind in CALL_KINDS and call_key and call_key in ctx.stems:
        return "excluded", "hidden call"
    if ext_id and ext_id in ctx.rids:
        return "revoked", "no longer shared"
    return "active", None


def ingest(store: S.Store, root: str, *, hidden_calls=None, revoked=None, web_dirs=(), valid_from=None,
           embedder=None, tracker_path: Optional[str] = None, voices: Optional[Dict[str, str]] = None, slug: str = "",
           quiet_s: float = 600.0, call_active: bool = False, between: Optional[Callable] = None) -> dict:
    """One deterministic pass. The caller holds the project lock. Pending state requests are applied first, between
    sources (the stand-in for segments in S1) and before return."""
    root = os.path.realpath(os.path.expanduser(root))
    observed = S.now_utc()
    if slug:
        with store.conn:
            store._meta_defaults(store.meta("evidence_uid") or store.path.stem, root, slug)
    apply_pending(store)
    # the pass's lists first, so a hidden or revoked source is never active between two sources (section 12)
    apply_state(store, hidden_calls=hidden_calls, revoked=revoked)
    started_lists = (store.meta("hidden_calls"), store.meta("revoked"))
    universe = CS.label_universe(root)
    old_uni = set(store.meta_json("label_universe", []) or [])
    added = sorted(set(universe) - old_uni)
    rep_relabel = relabel(store, set(universe), added) if added and old_uni is not None else {}
    ctx = Ctx(store, root, universe, CS.labels(root), embedder, observed, valid_from, between)
    found = walk(root, web_dirs)
    now = time.time()
    # in-progress transcripts wait for the next pass (spec section 5.2 item 7)
    for uri in [u for u, i in found.items() if i["kind"] == "transcript"]:
        try:
            busy = call_active or (now - os.path.getmtime(found[uri]["path"]) < quiet_s)
        except OSError:
            busy = True
        if busy:
            found[uri]["busy"] = True
    known = {r["uri"]: r for r in store.q("SELECT * FROM source WHERE kind IN ({})".format(",".join("?" * len(FILE_KINDS))),
                                          FILE_KINDS)}
    # 1. the vanished paths and the hashes of the new ones; 2. match moves; 3. ingest; 4. mark the rest deleted
    # only active sources become deleted: a revoked or hidden source stays denied when its file goes away
    vanished = [r for u, r in known.items() if u not in found and r["state"] == "active"]
    new = {u: i for u, i in found.items() if u not in known and not i.get("busy")}
    texts: Dict[str, str] = {}
    for u, i in new.items():
        texts[u] = read_text(i["path"])
        i["hash"] = S.sha(texts[u])
    moves = _match_moves(store, vanished, new)
    with store.conn:
        for old, u in moves:
            i = found[u]
            store.conn.execute("UPDATE source SET uri=?, call_key=?, last_checked=? WHERE id=?",
                               (u, i.get("call_key"), observed, old["id"]))
            state, why = born_state(ctx, i["kind"], i.get("call_key"), old["ext_id"])
            if state != "active":
                store.conn.execute("UPDATE source SET state=?, state_reason=?, state_at=? WHERE id=?",
                                   (state, why, observed, old["id"]))
            remark_path(ctx, old["id"], u)
            try:
                st = os.stat(i["path"])
                store.conn.execute("UPDATE revision SET mtime=?, size=? WHERE source_id=? AND superseded_at IS NULL",
                                   (st.st_mtime, st.st_size, old["id"]))
            except OSError:
                pass
            ctx.report["moved"] += 1
    moved_new = {u for _o, u in moves}
    moved_old = {o["id"] for o, _u in moves}
    ingest_register(ctx)
    order = sorted(found, key=lambda u: (found[u]["kind"] == "link_sidecar", u))
    for u in order:
        i = found[u]
        if i.get("busy") or u in moved_new:
            ctx.report["skipped" if i.get("busy") else "unchanged"] += 1
            continue
        ingest_file(ctx, u, i, texts.get(u))
        if between:
            between()
        if apply_pending(store):
            ctx.stems, ctx.rids = deny_lists(store)
    with store.conn:
        for r in vanished:
            if r["id"] in moved_old:
                continue
            if r["kind"] == "transcript" and found.get(r["uri"], {}).get("busy"):
                continue
            store.conn.execute("UPDATE source SET state='deleted', state_reason='file deleted', state_at=? WHERE id=?",
                               (observed, r["id"]))
            ctx.report["deleted"] += 1
    ingest_tracker(ctx, tracker_path)
    ingest_voices(ctx, voices)
    # a hide, unhide or revoke applied while the pass ran is newer than the lists it started with: that list stays
    # (section 13.1; the app may have quit before it could send it again)
    apply_state(store, hidden_calls=hidden_calls if store.meta("hidden_calls") == started_lists[0] else None,
                revoked=revoked if store.meta("revoked") == started_lists[1] else None)
    embed_missing(ctx)
    with store.conn:
        store.set_meta("label_universe", json.dumps(sorted(universe)))
        store.set_meta("label_tree_hash", S.sha("\n".join(sorted(universe))))
        store.set_meta("labels", json.dumps(sorted(ctx.labels)))
        store.set_meta("root_hint", root)
        store.set_meta("last_pass", observed)
    apply_pending(store)
    ctx.report["sources"] = store.one("SELECT count(*) FROM source")[0]
    if rep_relabel:
        ctx.report["relabel"] = rep_relabel
    return ctx.report


def ingest_sidecars(store: S.Store, root: str) -> int:
    """Every changed <stem>.notion.json of the project (deterministic, free; the (mtime, size) pre-check), so an
    override keyed notion:<page id> resolves whichever order the notes, the sidecar and the click arrive in."""
    root = os.path.realpath(os.path.expanduser(root))
    uni = store.meta_json("label_universe", []) or CS.label_universe(root)
    ctx = Ctx(store, root, uni, store.meta_json("labels", []) or [], None, S.now_utc(), None)
    n = 0
    for uri, info in walk(root).items():
        if info["kind"] == "link_sidecar":
            ingest_file(ctx, uri, info)
            n += 1
    return n


def _match_moves(store: S.Store, vanished: List, new: Dict[str, dict]) -> List[tuple]:
    """One to one by content hash: exactly one vanished active source and exactly one new path with that hash."""
    by_hash_old: Dict[str, list] = {}
    for r in vanished:
        if r["state"] != "active":
            continue
        h = store.one("SELECT content_hash FROM revision WHERE source_id=? AND superseded_at IS NULL", (r["id"],))
        if h:
            by_hash_old.setdefault(h[0], []).append(r)
    by_hash_new: Dict[str, list] = {}
    for u, i in new.items():
        by_hash_new.setdefault(i["hash"], []).append(u)
    out = []
    for h, olds in by_hash_old.items():
        news = by_hash_new.get(h, [])
        if len(olds) == 1 and len(news) == 1 and new[news[0]]["kind"] == olds[0]["kind"]:
            out.append((olds[0], news[0]))
    return out


def _source(ctx: Ctx, uri: str, info: dict, title: str = "") -> int:
    r = ctx.store.one("SELECT id FROM source WHERE uri=?", (uri,))
    if r:
        ctx.conn.execute("UPDATE source SET last_checked=?, call_key=coalesce(?, call_key), ext_id=coalesce(?, ext_id)"
                         " WHERE id=?", (ctx.observed, info.get("call_key"), info.get("ext_id"), r[0]))
        return r[0]
    state, why = born_state(ctx, info["kind"], info.get("call_key"), info.get("ext_id"))
    cur = ctx.conn.execute("INSERT INTO source(kind, uri, title, origin, call_key, state, state_reason, state_at, first_seen,"
                           " last_checked, ext_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (info["kind"], uri, title or uri.rsplit("/", 1)[-1], info["origin"], info.get("call_key"),
                            state, why, ctx.observed if why else None, ctx.observed, ctx.observed, info.get("ext_id")))
    return cur.lastrowid


def remark_path(ctx: Ctx, sid: int, uri: str) -> None:
    """A moved source: the path marks of every snippet of every revision from the new path, and client_set again
    from the path, heading and line marks, as write_snippets would write them (spec sections 5.2 item 6 and 5.3)."""
    pmarks = path_marks(uri) if not uri.startswith("/") else set()
    for (snid,) in ctx.store.q("SELECT sn.id FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id WHERE rv.source_id=?",
                               (sid,)):
        ctx.conn.execute("DELETE FROM snippet_mark WHERE snippet_id=? AND kind='path'", (snid,))
        ctx.conn.executemany("INSERT OR IGNORE INTO snippet_mark(snippet_id, kind, pos, name) VALUES (?,?,0,?)",
                             [(snid, "path", n) for n in pmarks])
        names = {r[0] for r in ctx.store.q("SELECT name FROM snippet_mark WHERE snippet_id=? AND kind IN"
                                           " ('path','heading','line')", (snid,))}
        ctx.conn.execute("UPDATE snippet SET client_set=? WHERE id=?", (_client_set(names, ctx.labels), snid))


def ingest_file(ctx: Ctx, uri: str, info: dict, text: Optional[str] = None) -> None:
    path = info["path"]
    try:
        st = os.stat(path)
    except OSError:
        return
    src = ctx.store.one("SELECT * FROM source WHERE uri=?", (uri,))
    act = ctx.store.one("SELECT * FROM revision WHERE source_id=? AND superseded_at IS NULL", (src["id"],)) if src else None
    if act and act["mtime"] == st.st_mtime and act["size"] == st.st_size:
        ctx.report["unchanged"] += 1
        if src["state"] == "deleted":          # came back with the same text
            with ctx.conn:
                ctx.conn.execute("UPDATE source SET state=?, state_reason=?, state_at=? WHERE id=?",
                                 born_state(ctx, src["kind"], src["call_key"], src["ext_id"]) + (ctx.observed, src["id"]))
        return
    text = read_text(path) if text is None else text
    h = S.sha(text)
    with ctx.conn:
        if src and src["state"] == "deleted":
            ctx.conn.execute("UPDATE source SET state=?, state_reason=?, state_at=? WHERE id=?",
                             born_state(ctx, src["kind"], src["call_key"], src["ext_id"]) + (ctx.observed, src["id"]))
        if act and act["content_hash"] == h:
            ctx.conn.execute("UPDATE revision SET mtime=?, size=? WHERE id=?", (st.st_mtime, st.st_size, act["id"]))
            ctx.report["touched"] += 1
            return
        title = text.split("\n", 1)[0][:200] if info["kind"] == "web_page" else ""
        sid = _source(ctx, uri, info, title)
        if act:
            vf = ctx.valid_from or next_valid_from(act["valid_from"], st.st_mtime, ctx.observed)
            ctx.conn.execute("UPDATE revision SET superseded_at=?, valid_to=? WHERE id=?", (ctx.observed, vf, act["id"]))
        else:
            vf = ctx.valid_from or first_valid_from(info["kind"], uri, text, st.st_mtime, ctx.observed)
        cur = ctx.conn.execute(
            "INSERT INTO revision(source_id, content_hash, mtime, size, chars, valid_from, observed_at, extract_state)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (sid, h, st.st_mtime, st.st_size, len(text), vf, ctx.observed,
             "not_needed" if info["kind"] in NO_EXTRACT else "pending"))
        rid = cur.lastrowid
        if info["kind"] != "link_sidecar":
            write_snippets(ctx, sid, rid, uri, info["kind"], text, vf)
        ctx.report["new_revisions"] += 1
        if info["kind"] == "notes":
            notes_items(ctx, sid, rid, uri)
        elif info["kind"] == "link_sidecar":
            sidecar_items(ctx, sid, rid, uri, text)
        elif info["kind"] == "project_state" and uri.endswith("decisions.md"):
            decisions_md_items(ctx, sid, rid)


def write_snippets(ctx: Ctx, sid: int, rid: int, uri: str, kind: str, text: str, vf: str) -> List[int]:
    """Snippets of one revision by lookup.chunks_at on the label universe, with marks, FTS rows, said_at and vectors
    (a text-equal snippet of an earlier revision of the same source keeps its said_at and copies its vector)."""
    chunks = LK.chunks_at(text, ctx.universe)
    earlier = {}
    for r in ctx.store.q("SELECT sn.text, sn.said_at, sn.vec FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id"
                         " WHERE rv.source_id=? AND rv.id<>? ORDER BY rv.id", (sid, rid)):
        earlier.setdefault(r["text"], (r["said_at"], r["vec"]))
    times = line_times(text, call_start(text, uri) or vf) if kind == "transcript" else {}
    pmarks = path_marks(uri) if not uri.startswith("/") else set()
    ids, need = [], []
    for n, c in enumerate(chunks):
        loc = {"lines": c["lines"], "chars": c["chars"]}
        if c["heading"]:
            loc["heading"] = c["heading"]
            loc["path"] = c["path"]
        said = vf
        speaker = None
        if kind == "transcript":
            ts = [times[k] for k in range(c["lines"][0], c["lines"][1] + 1) if k in times]
            if ts:
                said = max(ts)                  # the latest line of the snippet: never visible before it was said
                m = STAMP_RX.match(c["text"])
                if m:
                    loc["t"] = m.group(0).strip().lstrip("-").strip().strip("[]")
            sp = {SPEAKER_RX.match(ln).group(1).strip() for ln in c["text"].split("\n") if SPEAKER_RX.match(ln)}
            if len(sp) == 1:
                pid = ID.person_id(sp.pop())
                speaker = ID.ME if pid == ID.ME else "person:" + pid
        elif c["text"] in earlier:
            said = earlier[c["text"]][0] or vf
        vec = earlier.get(c["text"], (None, None))[1]
        names = set(pmarks)
        for _lv, title in c["path"]:
            names |= CS._names(title) & ctx.universe
        for ms in c["marks"].values():
            names |= set(ms)
        cur = ctx.conn.execute("INSERT INTO snippet(revision_id, ord, text, locator, heading, speaker, said_at, client_set, vec)"
                               " VALUES (?,?,?,?,?,?,?,?,?)",
                               (rid, n, c["text"], json.dumps(loc, ensure_ascii=False), c["heading"] or None,
                                speaker, said,
                                _client_set(names, ctx.labels), vec))
        snid = cur.lastrowid
        ids.append(snid)
        write_marks(ctx.conn, snid, pmarks, c, ctx.universe)
        if ctx.store.fts:
            ctx.conn.execute("INSERT INTO snippet_fts(rowid, text, heading) VALUES (?,?,?)", (snid, c["text"], c["heading"] or ""))
        if vec is not None:
            ctx.report["vectors_copied"] += 1
        else:
            need.append((snid, c["text"]))
    # a hidden or revoked source is never sent to the embed server; embed_missing catches up once it is active
    if need and ctx.embedder is not None and ctx.store.one("SELECT state FROM source WHERE id=?", (sid,))[0] == "active":
        vecs = ctx.embedder([t for _i, t in need])
        if vecs:
            ctx.report["embed_requests"] += 1
            for (snid, _t), v in zip(need, vecs):
                ctx.conn.execute("UPDATE snippet SET vec=? WHERE id=?", (pack(v), snid))
    return ids


def embed_missing(ctx: Ctx) -> None:
    """One embed request for the current snippets of active file sources with no vector: a source restored from
    hidden or revoked, or a pass when the embed server was down."""
    if ctx.embedder is None:
        return
    rows = ctx.store.q("SELECT sn.id, sn.text FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id JOIN source s"
                       " ON s.id=rv.source_id WHERE s.state='active' AND rv.superseded_at IS NULL AND sn.vec IS NULL"
                       " AND s.kind IN ({}) ORDER BY sn.id".format(",".join("?" * len(FILE_KINDS))), FILE_KINDS)
    if not rows:
        return
    vecs = ctx.embedder([r["text"] for r in rows])
    if vecs:
        ctx.report["embed_requests"] += 1
        with ctx.conn:
            ctx.conn.executemany("UPDATE snippet SET vec=? WHERE id=?", [(pack(v), r["id"]) for r, v in zip(rows, vecs)])


def write_marks(conn, snid: int, pmarks: set, chunk: dict, universe: set) -> None:
    rows = [(snid, "path", 0, n) for n in pmarks]
    for lv, title in chunk["path"]:
        rows += [(snid, "heading", int(lv), n) for n in CS._names(title) & universe]
    for ln, ms in chunk["marks"].items():
        rows += [(snid, "line", int(ln), n) for n in ms]
    conn.executemany("INSERT OR IGNORE INTO snippet_mark(snippet_id, kind, pos, name) VALUES (?,?,?,?)", rows)


# ---------------------------------------------------------------- deterministic entities

def _entity(ctx: Ctx, eid: str, etype: str, name: str, origin: str, sid: Optional[int], marks: Dict[str, Iterable[str]],
            status: Optional[str] = None, client: str = "", rid: Optional[int] = None) -> None:
    """rid: the revision that carries the entity (a decision or risk is visible at as_of only through one, 11.2)."""
    ctx.conn.execute("INSERT INTO entity(id, type, name, client, status, origin, updated_at) VALUES (?,?,?,?,?,?,?)"
                     " ON CONFLICT(id) DO UPDATE SET name=CASE WHEN entity.pm_locked=1 THEN entity.name ELSE excluded.name END,"
                     " status=coalesce(excluded.status, entity.status), updated_at=excluded.updated_at",
                     (eid, etype, name, client, status, origin, ctx.observed))
    for kind, names in marks.items():
        ctx.conn.executemany("INSERT OR IGNORE INTO entity_mark(entity_id, kind, name) VALUES (?,?,?)",
                             [(eid, kind, n) for n in names if n])
    if sid:
        ctx.conn.execute("INSERT OR IGNORE INTO entity_source(entity_id, source_id) VALUES (?,?)", (eid, sid))
    if rid:
        ctx.conn.execute("INSERT OR IGNORE INTO entity_rev(entity_id, revision_id) VALUES (?,?)", (eid, rid))


def _alias(conn, an: str, eid: str, origin: str, status: str = "confirmed") -> None:
    an = S.norm_alias(an)
    if an:
        conn.execute("INSERT OR IGNORE INTO alias(alias_norm, entity_id, lang, origin, status) VALUES (?,?,?,?,?)",
                     (an, eid, "", origin, status))


def _claim(ctx: Ctx, subj: str, pred: str, value: str, modality: str, rid: int, quote: str, origin: str = "deterministic",
           status: str = "accepted") -> Optional[int]:
    """A deterministic claim with one evidence row on the revision's snippet that holds the quote."""
    for s in ctx.store.q("SELECT id, text, said_at, speaker FROM snippet WHERE revision_id=? ORDER BY ord", (rid,)):
        hit = S._find_quote(s["text"], quote)
        if not hit:
            continue
        nkey = S.claim_nkey(subj, pred, modality, value)
        ctx.conn.execute("INSERT OR IGNORE INTO claim(subject_id, predicate, value, modality, said_at, status, origin, nkey)"
                         " VALUES (?,?,?,?,?,?,?,?)", (subj, pred, value, modality, s["said_at"], status, origin, nkey))
        cid = ctx.store.one("SELECT id FROM claim WHERE nkey=?", (nkey,))[0]
        ctx.conn.execute("INSERT OR IGNORE INTO evidence(target, target_id, snippet_id, quote, q_start, q_end, said_by)"
                         " VALUES ('claim',?,?,?,?,?,?)", (cid, s["id"], s["text"][hit[0]:hit[1]][:300], hit[0], hit[1],
                                                         S.known(ctx.conn, s["speaker"])))
        ctx.conn.execute("UPDATE claim SET said_at=min(said_at, ?) WHERE id=?", (s["said_at"], cid))
        return cid
    return None


ITEM_RX = re.compile(r"^\s*[-*]\s+(?:\[[\d:]+\]\s*)?(?:\*\*[^*]{1,60}\*\*\s*:?\s*)?(.+?)\s*$")


def section_items(text: str) -> List[tuple]:
    """("d" or "r", item text) of the Decisions and Risks sections of call notes (en and uk headings)."""
    out, cur = [], None
    for ln in text.split("\n"):
        h = CS.HEADING.match(ln.strip())
        if h:
            cur = SECTION.get(h.group(2).strip().strip(":").casefold())
            continue
        if cur:
            m = ITEM_RX.match(ln)
            if m and not CM.NOTHING.match(m.group(1).strip()):
                out.append((cur, m.group(1).strip()))
    return out


def _call_marks(stem: str, text: str) -> Dict[str, set]:
    p = CL.parse(stem + ".md")
    series = CS.slug(p["slug"]) if p else ""
    m = CS.CALL_NAME.match(stem.lower())
    return {"series": {series} if series else set(), "call": {m.group(1)} if m else set(),
            "text": CS._names(text)}


def notes_items(ctx: Ctx, sid: int, rid: int, uri: str) -> None:
    """The decisions and risks of call notes as d- and r- entities with one claim each (spec section 7.1)."""
    stem = ID.call_stem(uri)
    text = "\n".join(r["text"] for r in ctx.store.q("SELECT text FROM snippet WHERE revision_id=? ORDER BY ord", (rid,)))
    for kind, item in section_items(text):
        eid = ID.item_id(kind, stem, item)
        _entity(ctx, eid, "decision" if kind == "d" else "risk", item, "g1", sid, _call_marks(stem, item), rid=rid)
        _alias(ctx.conn, item, eid, "g1")
        _claim(ctx, eid, "decided" if kind == "d" else "risk_level", item, "statement", rid, item)


def sidecar_items(ctx: Ctx, sid: int, rid: int, uri: str, text: str) -> None:
    """<stem>.notion.json: notion:<page id> aliases on the call's d-, r- and task entities (spec section 7.1). A key
    with no matching item creates the d- entity from the key alone, so the alias always has an entity."""
    try:
        d = json.loads(Path(os.path.join(ctx.root, uri)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    stem = ID.call_stem(uri)
    for kind in ("decisions", "risks"):
        for key, v in (d.get(kind) or {}).items():
            pid = (v or {}).get("id") if isinstance(v, dict) else ""
            eid = ID.item_id(kind[0], stem, key)
            _entity(ctx, eid, "decision" if kind == "decisions" else "risk", key, "g1", sid,
                    _call_marks(stem, key), rid=rid)
            if pid:
                _alias(ctx.conn, ID.notion_alias(pid), eid, "notion")
    tasks = d.get("tasks") or {}
    if tasks:
        for r in ctx.store.q("SELECT e.id, e.name FROM entity e JOIN entity_mark m ON m.entity_id=e.id"
                             " WHERE e.type='task' AND m.kind='callstem' AND m.name=?", (stem,)):
            v = tasks.get(NL.norm(r["name"]))
            if isinstance(v, dict) and v.get("id"):
                _alias(ctx.conn, ID.notion_alias(v["id"]), r["id"], "notion")


def decisions_md_items(ctx: Ctx, sid: int, rid: int) -> None:
    """pacenote/decisions.md lines (decision_notion.parse_snapshot): d- ids keyed by date and title; a call's decision
    with the same title is only a same_as suggestion (spec section 7.1)."""
    from ..decision_notion import parse_snapshot
    text = "\n".join(r["text"] for r in ctx.store.q("SELECT text FROM snippet WHERE revision_id=? ORDER BY ord", (rid,)))
    for it in parse_snapshot(text):
        eid = ID.register_decision_id(it["date"], it["title"])
        _entity(ctx, eid, "decision", it["title"], "g1", sid, {"text": CS._names(it["title"])}, rid=rid)
        _alias(ctx.conn, it["title"], eid, "g1")
        _claim(ctx, eid, "decided", it["title"], "statement", rid, it["title"])
        for r in ctx.store.q("SELECT id FROM entity WHERE type='decision' AND id<>? AND id LIKE 'd-%'", (eid,)):
            other = ctx.store.one("SELECT name FROM entity WHERE id=?", (r["id"],))[0]
            if NL.norm(other) == NL.norm(it["title"]):
                _same_as(ctx, eid, r["id"], rid, it["title"])


def _same_as(ctx: Ctx, a: str, b: str, rid: int, quote: str) -> None:
    for s in ctx.store.q("SELECT id, text, said_at FROM snippet WHERE revision_id=? ORDER BY ord", (rid,)):
        hit = S._find_quote(s["text"], quote)
        if not hit:
            continue
        ctx.conn.execute("INSERT OR IGNORE INTO edge(src_id, rel, dst_id, modality, status, origin, first_said_at, last_said_at)"
                         " VALUES (?,?,?,?,?,?,?,?)", (a, "same_as", b, "statement", "suggestion", "deterministic",
                                                       s["said_at"], s["said_at"]))
        e = ctx.store.one("SELECT id FROM edge WHERE src_id=? AND rel='same_as' AND dst_id=? AND modality='statement'", (a, b))
        ctx.conn.execute("INSERT OR IGNORE INTO evidence(target, target_id, snippet_id, quote, q_start, q_end) VALUES"
                         " ('edge',?,?,?,?,?)", (e[0], s["id"], s["text"][hit[0]:hit[1]], hit[0], hit[1]))
        return


# ---------------------------------------------------------------- the register

REGISTER = "live/commitments.jsonl"
PM_OPS = ("edit", "same", "split", "reject", "drop", "done", "agree", "reopen", "tentative")


def event_time(e: dict) -> str:
    for k in ("at", "created"):
        v = e.get(k)
        if v:
            try:
                return S.utc(str(v))
            except ValueError:
                continue
    return ""


def register_events(store: S.Store, raw: str) -> List[dict]:
    """The register's events with every project slug the project ever had normalised to the uid, before fold and
    before any G1 key function (spec section 13.4)."""
    hist = set(store.meta_json("slug_history", []) or [])
    uid = store.meta("evidence_uid")
    out = []
    for n, ln in enumerate(raw.split("\n"), 1):
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("op") and e.get("id"):
            e = dict(e, _line=n)
            if hist and str(e.get("project") or "") in hist:
                e["project"] = uid
            out.append(e)
    return out


def _event_text(e: dict) -> str:
    op = e.get("op")
    if op == "add":
        bits = [str(e.get("text") or "")]
        for k in ("owner", "due"):
            if e.get(k):
                bits.append("{} {}".format(k, e[k]))
        return "task: " + ", ".join(bits)
    if op == "edit":
        return "edit {}: {}".format(e.get("id"), ", ".join("{} {}".format(k, e[k]) for k in ("text", "owner", "due") if e.get(k)))
    return "{} {}".format(op, e.get("id"))


def ingest_register(ctx: Ctx) -> None:
    """live/commitments.jsonl: one snippet per event (said_at = the event's time), tasks as entities from
    commitments.fold() with their G1 and Tracker aliases, PM events as pm_override rows with via = register. The
    register is small, so every change refolds the whole file (which also covers op migrate and op rollback)."""
    path = os.path.join(ctx.root, REGISTER)
    try:
        st = os.stat(path)
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    info = {"kind": "register", "origin": "pm"}
    src = ctx.store.one("SELECT * FROM source WHERE uri=?", (REGISTER,))
    act = ctx.store.one("SELECT * FROM revision WHERE source_id=? AND superseded_at IS NULL", (src["id"],)) if src else None
    h = S.sha(raw)
    if act and act["content_hash"] == h:
        return
    events = register_events(ctx.store, raw)
    times = [t for t in (event_time(e) for e in events) if t]
    with ctx.conn:
        sid = _source(ctx, REGISTER, info, "commitments register")
        vf = min(times) if times else ctx.observed
        if act:
            ctx.conn.execute("UPDATE revision SET superseded_at=?, valid_to=? WHERE id=?", (ctx.observed, ctx.observed, act["id"]))
        rid = ctx.conn.execute("INSERT INTO revision(source_id, content_hash, mtime, size, chars, valid_from, observed_at,"
                               " extract_state) VALUES (?,?,?,?,?,?,?, 'not_needed')",
                               (sid, h, st.st_mtime, st.st_size, len(raw), vf, ctx.observed)).lastrowid
        items = CM.fold([{k: v for k, v in e.items() if k != "_line"} for e in events])
        # the side of an event is the item_side of the add events with its own id, as filter_register decides it:
        # pos k = the k-th such add (an id with any add on another client's side is other), names stored uncut
        adds: Dict[str, List[list]] = {}
        for e in events:
            if e.get("op") == "add":
                series = CS.slug(str(e.get("series") or "").split("|")[-1])
                m = CS.CALL_NAME.match(os.path.basename(str(e.get("call") or "")).lower())
                marks = [("rtext", x) for x in CS._names(str(e.get("text") or ""))]
                marks += [("rseries", series)] if series else []
                marks += [("rcall", m.group(1))] if m else []
                adds.setdefault(str(e["id"]), []).append(marks)
        for n, e in enumerate(events):
            txt = _event_text(e)
            raw_e = {k: v for k, v in e.items() if k != "_line"}
            loc = {"lines": [e["_line"], e["_line"]], "event_id": e.get("id"), "op": e.get("op"), "event": raw_e}
            snid = ctx.conn.execute("INSERT INTO snippet(revision_id, ord, text, locator, said_at) VALUES (?,?,?,?,?)",
                                    (rid, n, txt, json.dumps(loc, ensure_ascii=False), event_time(e) or vf)).lastrowid
            ctx.conn.executemany("INSERT OR IGNORE INTO snippet_mark(snippet_id, kind, pos, name) VALUES (?,?,?,?)",
                                 [(snid, kind, k, name) for k, marks in enumerate(adds.get(str(e["id"]), []))
                                  for kind, name in marks])
            if ctx.store.fts:
                ctx.conn.execute("INSERT INTO snippet_fts(rowid, text, heading) VALUES (?,?,?)", (snid, txt, ""))
        by_tracker: Dict[str, dict] = {}
        for it in items:
            series = CS.slug(str(it.get("series") or "").split("|")[-1])
            m = CS.CALL_NAME.match(os.path.basename(str(it.get("call") or "")).lower())
            marks = {"series": {series} if series else set(), "call": {m.group(1)} if m else set(),
                     "text": CS._names(it.get("text") or ""),
                     "callstem": {ID.call_stem(str(h.get("call") or "")) for h in CM.history_of(it) if h.get("call")}}
            _entity(ctx, it["id"], "task", it.get("text") or it["id"], "g1", sid, marks, status=it.get("status"))
            for x in it.get("ids") or []:
                if x != it["id"]:
                    _alias(ctx.conn, x, it["id"], "g1")
            tid = ID.tracker_id(it)
            if tid:
                _alias(ctx.conn, tid, it["id"], "tracker")
                first = by_tracker.get(tid)
                if first and ID.tracker_scope(first, it):
                    ctx.conn.execute("UPDATE entity SET merged_into=?, merged_at=? WHERE id=?",
                                     (first["id"], it.get("created") and S.utc(it["created"]) or vf, it["id"]))
                else:
                    by_tracker.setdefault(tid, it)
        ctx.conn.execute("DELETE FROM pm_override WHERE via='register'")
        for e in events:
            if e.get("op") in PM_OPS and e.get("by") not in ("app-auto",):
                val = {k: e[k] for k in ("to", "text", "owner", "due") if e.get(k) is not None}
                ctx.conn.execute("INSERT INTO pm_override(target, target_key, field, value, at, via) VALUES"
                                 " ('entity',?,?,?,?, 'register')",
                                 (e["id"], e["op"], json.dumps(val, ensure_ascii=False) if val else None,
                                  event_time(e) or vf))
        ctx.report["new_revisions"] += 1


def register_events_at(store: S.Store, as_of: Optional[str], passing: bool = False) -> List[dict]:
    """The active register revision's events up to `as_of`. passing: only the events whose snippet the request may
    see (`_snip` cls pass of search.prepare), the events filter_register keeps for the scope (section 5.3)."""
    rows = store.q("SELECT sn.locator, sn.said_at FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id JOIN source s"
                   " ON s.id=rv.source_id{} WHERE s.kind='register' AND rv.superseded_at IS NULL ORDER BY sn.ord".format(
                       " JOIN _snip x ON x.sid=sn.id AND x.cls='pass'" if passing else ""))
    events = []
    for r in rows:
        if as_of and (r["said_at"] or "") > as_of:
            continue
        e = json.loads(r["locator"]).get("event")
        if isinstance(e, dict):
            events.append(e)
    return events


def register_fold_at(store: S.Store, as_of: Optional[str], passing: bool = False) -> Dict[str, dict]:
    """{task id: item} from the active register revision's events folded up to `as_of` (spec section 11.2 step 2):
    a task closed after `as_of` is open in that replay. passing: fold only the events the request may see, so a name,
    status or join that comes from another client's events never reaches the request (PB-84 review round 4)."""
    out = {}
    for it in CM.fold(register_events_at(store, as_of, passing)):
        for x in it.get("ids") or [it["id"]]:
            out[x] = it
    return out


# ---------------------------------------------------------------- Tracker snapshot and voices

def tracker_snapshot_path(uid: str) -> Optional[str]:
    state = os.environ.get("LT_AI_STATE")
    if not state or not uid:
        return None
    return str(Path(state).expanduser().parent / "tracker-snapshot" / (uid + ".json"))


def ingest_tracker(ctx: Ctx, path: Optional[str]) -> None:
    """The Tracker snapshot the app writes (spec section 5.2 item 5): one source per item, diffed item by item. No
    file keeps the last ingested state; an item missing from a present file is deleted."""
    path = path or tracker_snapshot_path(ctx.store.meta("evidence_uid"))
    if not path or not os.path.exists(path):
        return
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return
    seen = set()
    with ctx.conn:
        for it in items:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            raw_id = str(it["id"])
            uri = raw_id if raw_id.startswith(("notion:", "crm:")) else ID.notion_alias(raw_id)
            seen.add(uri)
            txt = "{} ({})".format(it.get("text") or "", ", ".join(
                "{} {}".format(k, it[k]) for k in ("status", "owner", "due") if it.get(k)))
            h = S.sha(json.dumps(it, sort_keys=True, ensure_ascii=False))
            sid = _source(ctx, uri, {"kind": "tracker", "origin": "primary"}, str(it.get("text") or uri)[:200])
            ctx.conn.execute("UPDATE source SET state='active', state_reason=NULL WHERE id=? AND state='deleted'", (sid,))
            act = ctx.store.one("SELECT * FROM revision WHERE source_id=? AND superseded_at IS NULL", (sid,))
            if act and act["content_hash"] == h:
                continue
            vf = S.utc(it["last_edited"]) if it.get("last_edited") else ctx.observed
            if act:
                ctx.conn.execute("UPDATE revision SET superseded_at=?, valid_to=? WHERE id=?", (ctx.observed, max(vf, act["valid_from"]), act["id"]))
            rid = ctx.conn.execute("INSERT INTO revision(source_id, content_hash, chars, valid_from, observed_at, extract_state)"
                                   " VALUES (?,?,?,?,?, 'not_needed')", (sid, h, len(txt), max(vf, act["valid_from"]) if act else vf,
                                                                        ctx.observed)).lastrowid
            snid = ctx.conn.execute("INSERT INTO snippet(revision_id, ord, text, locator, said_at) VALUES (?,?,?,?,?)",
                                    (rid, 0, txt, json.dumps({"field": "item", "uri": uri, "text": it.get("text"),
                                                              "status": it.get("status")}, ensure_ascii=False),
                                     vf)).lastrowid
            if ctx.store.fts:
                ctx.conn.execute("INSERT INTO snippet_fts(rowid, text, heading) VALUES (?,?,?)", (snid, txt, ""))
            linked = ctx.store.one("SELECT a.entity_id, en.origin FROM alias a JOIN entity en ON en.id=a.entity_id"
                                   " WHERE a.alias_norm=? AND a.origin='tracker'", (S.norm_alias(uri),))
            eid = linked[0] if linked else "e-" + ID._sha10("task|" + uri)
            ctx.conn.execute("INSERT OR IGNORE INTO snippet_mark(snippet_id, kind, pos, name) VALUES (?, 'item', 0, ?)",
                             (snid, eid))
            # the item's own entity follows every snapshot; a register task linked by its Tracker id keeps the register's
            if not linked or linked[1] == "tracker":
                _entity(ctx, eid, "task", str(it.get("text") or uri), "tracker", sid,
                        {"text": CS._names(str(it.get("text") or ""))}, status=it.get("status"))
                _alias(ctx.conn, uri, eid, "tracker")
            else:
                ctx.conn.execute("INSERT OR IGNORE INTO entity_source(entity_id, source_id) VALUES (?,?)", (eid, sid))
            ctx.report["new_revisions"] += 1
        for r in ctx.store.q("SELECT id, uri FROM source WHERE kind='tracker' AND state='active'"):
            if r["uri"] not in seen:
                ctx.conn.execute("UPDATE source SET state='deleted', state_reason='not in the Tracker', state_at=? WHERE id=?",
                                 (ctx.observed, r["id"]))


def ingest_voices(ctx: Ctx, aliases: Optional[Dict[str, str]]) -> None:
    """People of the voice library: person:<casefold> entities with their aliases (origin voices)."""
    al = ID.voice_aliases() if aliases is None else aliases
    if not al:
        return
    with ctx.conn:
        for spelling, kept in al.items():
            pid = ID.person_id(kept, al)
            eid = "me" if pid == ID.ME else "person:" + pid
            _entity(ctx, eid, "person", kept, "voices", None, {})
            _alias(ctx.conn, spelling, eid, "voices")
            _alias(ctx.conn, kept, eid, "voices")


# ---------------------------------------------------------------- the state-only pass and the request log

def _stems(calls: Iterable[str]) -> List[str]:
    return sorted({ID.call_stem(str(c)) for c in calls or () if str(c).strip()})


def _revoked_ids(ids: Iterable[str]) -> List[str]:
    out = set()
    for x in ids or ():
        x = str(x).strip()
        if not x:
            continue
        x = x.split(":", 1)[1] if x.startswith(("notion:", "google:")) else x
        out.add(x.replace("-", "").lower() if NOTION_ID_RX.fullmatch(x) else x)
        out.add(x[:16])
    return sorted(out)


def apply_state(store: S.Store, hidden_calls=None, revoked=None) -> dict:
    """The state-only pass (spec sections 5.1 and 5.2 item 6): sources of hidden calls -> excluded, revoked documents
    -> revoked, and back to active when they leave the list. Each list is the full current list; None keeps the
    current state. The applied lists are kept in meta so a source written later is born denied (deny_lists).
    A local SQLite write only: no model, network or embed request."""
    now = S.now_utc()
    rep = {"excluded": 0, "revoked": 0, "restored": 0}
    with store.conn:
        if hidden_calls is not None:
            stems = _stems(hidden_calls)
            store.set_meta("hidden_calls", json.dumps(stems))
            ph = ",".join("?" * len(CALL_KINDS))
            for r in store.q("SELECT id, call_key, state, state_reason FROM source WHERE kind IN ({})".format(ph), CALL_KINDS):
                hide = r["call_key"] in stems
                if hide and r["state"] in ("active", "deleted"):
                    store.conn.execute("UPDATE source SET state='excluded', state_reason='hidden call', state_at=? WHERE id=?",
                                       (now, r["id"]))
                    rep["excluded"] += 1
                elif not hide and r["state"] == "excluded" and r["state_reason"] == "hidden call":
                    store.conn.execute("UPDATE source SET state='active', state_reason=NULL, state_at=? WHERE id=?", (now, r["id"]))
                    rep["restored"] += 1
        if revoked is not None:
            ids = set(_revoked_ids(revoked))
            store.set_meta("revoked", json.dumps(sorted(ids)))
            for r in store.q("SELECT id, ext_id, state, state_reason FROM source WHERE ext_id IS NOT NULL"):
                rv = r["ext_id"] in ids
                if rv and r["state"] in ("active", "deleted"):
                    store.conn.execute("UPDATE source SET state='revoked', state_reason='no longer shared', state_at=?"
                                       " WHERE id=?", (now, r["id"]))
                    rep["revoked"] += 1
                elif not rv and r["state"] == "revoked":
                    store.conn.execute("UPDATE source SET state='active', state_reason=NULL, state_at=? WHERE id=?", (now, r["id"]))
                    rep["restored"] += 1
    return rep


def apply_request(store: S.Store, req: dict) -> bool:
    """One request of the log in one transaction with its state_request row; a re-sent request id is a no-op."""
    rid = req["request_id"]
    if store.one("SELECT 1 FROM state_request WHERE request_id=?", (rid,)):
        return False
    kind, p = req["kind"], req.get("payload") or {}
    if kind == "hidden_calls":
        apply_state(store, hidden_calls=p.get("hidden_calls") or [])
    elif kind == "revoked":
        apply_state(store, revoked=p.get("revoked") or [])
    elif kind == "override":
        write_override(store, p)
    with store.conn:
        store.conn.execute("INSERT OR IGNORE INTO state_request(request_id, kind, payload, received_at, applied_at)"
                           " VALUES (?,?,?,?,?)", (rid, kind, json.dumps(p, ensure_ascii=False), req.get("at") or S.now_utc(),
                                                   S.now_utc()))
    return True


def apply_pending(store: S.Store) -> int:
    """Every log line whose request id has no state_request row, in file order (the caller holds the project lock)."""
    n = 0
    for req in store.pending_requests():
        n += 1 if apply_request(store, req) else 0
    return n


def write_override(store: S.Store, p: dict) -> None:
    """One pm_override row (spec section 11.6). Keys stay natural (alias keys such as notion:<page id> are resolved
    whenever overrides are read, never at write time)."""
    with store.conn:
        store.conn.execute("INSERT INTO pm_override(target, target_key, field, value, at, via) VALUES (?,?,?,?,?,?)",
                           (p.get("target") or "entity", str(p["key"]), str(p["field"]), p.get("value"),
                            S.utc(p["at"]) if p.get("at") else S.now_utc(), p.get("via") or "app"))


# ---------------------------------------------------------------- relabel on an added label (spec section 5.2)

def relabel(store: S.Store, universe: set, added: List[str]) -> dict:
    """Split stored snippets where the invariant now asks for a cut, from snippet.text alone (superseded revisions
    included): new rows with ord renumbered, their marks, and every evidence row re-anchored by its offset inside the
    old snippet to the new piece that holds the whole quote. A quote that crosses a new cut is dropped. Claims, edges
    and pm_override rows keep their natural keys. In the same transaction, every topic that read a relabelled
    revision's source loses its summary and goes `pending` (S4, section 8.7). No model, network or embed request."""
    rep = {"added": list(added), "revisions": 0, "split": 0, "dropped_quotes": 0, "topics_invalidated": 0}
    add = set(added)
    revs = [r[0] for r in store.q(
        "SELECT DISTINCT sn.revision_id FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id JOIN source s"
        " ON s.id=rv.source_id WHERE s.kind NOT IN ('register','tracker')")]
    labels = set(store.meta_json("labels", []) or [])
    for rid in revs:
        rows = store.q("SELECT * FROM snippet WHERE revision_id=? ORDER BY ord", (rid,))
        if not any(add & CS._names(r["text"]) for r in rows):
            continue
        src = store.one("SELECT s.uri FROM source s JOIN revision rv ON rv.source_id=s.id WHERE rv.id=?", (rid,))[0]
        pmarks = path_marks(src) if not src.startswith("/") else set()
        with store.conn:
            sid = store.one("SELECT source_id FROM revision WHERE id=?", (rid,))[0]
            rep["topics_invalidated"] += S.invalidate_topics(store.conn, [sid])
            plan = []
            for r in rows:
                loc = json.loads(r["locator"])
                l0, c0 = loc.get("lines", [1, 1])[0], loc.get("chars", [0, 0])[0]
                pieces = LK.chunks_at(r["text"], universe, width=10 ** 9)
                path = [list(p) for p in loc.get("path") or []]
                plan.append((r, loc, l0, c0, pieces, path))
            for r, *_rest in plan:
                store.conn.execute("UPDATE snippet SET ord=-ord-1 WHERE id=?", (r["id"],))
            ordn = 0
            for r, loc, l0, c0, pieces, path in plan:
                if len(pieces) <= 1:
                    store.conn.execute("UPDATE snippet SET ord=? WHERE id=?", (ordn, r["id"]))
                    ordn += 1
                    _remark(store, r["id"], pmarks, path, r["text"], l0, universe)
                    continue
                ev = store.q("SELECT * FROM evidence WHERE snippet_id=?", (r["id"],))
                seen = store.q("SELECT * FROM alias_seen WHERE snippet_id=?", (r["id"],))
                for pc in pieces:
                    a, b = pc["chars"]
                    nloc = dict(loc, lines=[l0 + pc["lines"][0] - 1, l0 + pc["lines"][1] - 1], chars=[c0 + a, c0 + b])
                    nid = store.conn.execute(
                        "INSERT INTO snippet(revision_id, ord, text, locator, heading, speaker, said_at, client_set, vec)"
                        " VALUES (?,?,?,?,?,?,?,?,NULL)",
                        (rid, ordn, pc["text"], json.dumps(nloc, ensure_ascii=False), r["heading"], r["speaker"], r["said_at"],
                         _client_set(set(pmarks) | {n for ms in pc["marks"].values() for n in ms}, labels))).lastrowid
                    ordn += 1
                    same = store.one("SELECT vec FROM snippet WHERE text=? AND vec IS NOT NULL LIMIT 1", (pc["text"],))
                    if same:
                        store.conn.execute("UPDATE snippet SET vec=? WHERE id=?", (same[0], nid))
                    _remark(store, nid, pmarks, path, pc["text"], l0 + pc["lines"][0] - 1, universe)
                    if store.fts:
                        store.conn.execute("INSERT INTO snippet_fts(rowid, text, heading) VALUES (?,?,?)",
                                           (nid, pc["text"], r["heading"] or ""))
                    for e in ev:
                        if e["q_start"] >= a and e["q_end"] <= b:
                            store.conn.execute("INSERT OR IGNORE INTO evidence(target, target_id, snippet_id, quote, q_start,"
                                               " q_end, said_by) VALUES (?,?,?,?,?,?,?)",
                                               (e["target"], e["target_id"], nid, e["quote"], e["q_start"] - a, e["q_end"] - a,
                                                e["said_by"]))
                    for s_ in seen:
                        if S._find_quote(pc["text"], s_["alias_norm"]):
                            store.conn.execute("INSERT OR IGNORE INTO alias_seen VALUES (?,?,?)",
                                               (s_["alias_norm"], s_["entity_id"], nid))
                for e in ev:
                    if not any(e["q_start"] >= pc["chars"][0] and e["q_end"] <= pc["chars"][1] for pc in pieces):
                        rep["dropped_quotes"] += 1
                if store.fts:
                    store.conn.execute("INSERT INTO snippet_fts(snippet_fts, rowid, text, heading) VALUES ('delete',?,?,?)",
                                       (r["id"], r["text"], r["heading"] or ""))
                store.conn.execute("DELETE FROM snippet WHERE id=?", (r["id"],))
                rep["split"] += 1
            rep["revisions"] += 1
    with store.conn:
        store.set_meta("label_universe", json.dumps(sorted(universe)))
    return rep


def _remark(store: S.Store, snid: int, pmarks: set, path: List, text: str, first_line: int, universe: set) -> None:
    store.conn.execute("DELETE FROM snippet_mark WHERE snippet_id=?", (snid,))
    rows = [(snid, "path", 0, n) for n in pmarks]
    for lv, title in path:
        rows += [(snid, "heading", int(lv), n) for n in CS._names(title) & universe]
    for i, ln in enumerate(text.split("\n")):
        if ln.strip() and not CS.HEADING.match(ln.strip()):
            rows += [(snid, "line", first_line + i, n) for n in CS._names(ln) & universe]
    store.conn.executemany("INSERT OR IGNORE INTO snippet_mark(snippet_id, kind, pos, name) VALUES (?,?,?,?)", rows)
