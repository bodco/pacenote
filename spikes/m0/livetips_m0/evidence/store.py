"""PB-52 G2 (S1): the store file, its schema, the locks, the state-request log and the write path for extracted items.

    Store.open(path=None, uid=None, root=None, readonly=False) -> Store
    store.project_lock(timeout) / worker_lock(timeout)       flock, released with the process
    store.append_request(kind, payload, request_id)          one line of <uid>.state-requests.jsonl
    store.pending_requests()                                  lines with no state_request row (readers deny hides)
    apply_extraction(store, revision_id, items)               entity, alias, claim, edge and evidence rows (S3 calls it)
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import errno
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional

SCHEMA_VERSION = 1
RULES_VERSION = "g2-s1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, uri TEXT NOT NULL UNIQUE, title TEXT, origin TEXT NOT NULL,
  call_key TEXT, state TEXT NOT NULL DEFAULT 'active', state_reason TEXT, state_at TEXT, first_seen TEXT NOT NULL,
  last_checked TEXT, ext_id TEXT);
CREATE INDEX IF NOT EXISTS source_state ON source(state, kind);
CREATE INDEX IF NOT EXISTS source_call ON source(call_key);
CREATE TABLE IF NOT EXISTS revision (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
  content_hash TEXT NOT NULL, mtime REAL, size INTEGER, chars INTEGER, valid_from TEXT NOT NULL, valid_to TEXT,
  observed_at TEXT NOT NULL, superseded_at TEXT, extract_state TEXT NOT NULL DEFAULT 'pending', extractor TEXT);
CREATE INDEX IF NOT EXISTS revision_hash ON revision(source_id, content_hash);
CREATE INDEX IF NOT EXISTS revision_active ON revision(source_id, superseded_at);
CREATE INDEX IF NOT EXISTS revision_valid ON revision(source_id, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS revision_extract ON revision(extract_state);
CREATE TABLE IF NOT EXISTS snippet (
  id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL REFERENCES revision(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, text TEXT NOT NULL, locator TEXT NOT NULL, heading TEXT, speaker TEXT, said_at TEXT,
  client_set TEXT NOT NULL DEFAULT '', vec BLOB, UNIQUE (revision_id, ord));
CREATE INDEX IF NOT EXISTS snippet_rev ON snippet(revision_id);
CREATE TABLE IF NOT EXISTS snippet_mark (
  snippet_id INTEGER NOT NULL REFERENCES snippet(id) ON DELETE CASCADE, kind TEXT NOT NULL, pos INTEGER NOT NULL,
  name TEXT NOT NULL, PRIMARY KEY (snippet_id, kind, pos, name));
CREATE INDEX IF NOT EXISTS snippet_mark_name ON snippet_mark(name, snippet_id);
CREATE TABLE IF NOT EXISTS entity (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, client TEXT NOT NULL DEFAULT '', status TEXT,
  origin TEXT NOT NULL, merged_into TEXT REFERENCES entity(id), merged_at TEXT, pm_locked INTEGER NOT NULL DEFAULT 0,
  created_run INTEGER REFERENCES run(id), updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS entity_type ON entity(type, client);
CREATE TABLE IF NOT EXISTS entity_mark (
  entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE, kind TEXT NOT NULL, name TEXT NOT NULL,
  PRIMARY KEY (entity_id, kind, name));
CREATE TABLE IF NOT EXISTS entity_source (
  entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE, PRIMARY KEY (entity_id, source_id));
CREATE TABLE IF NOT EXISTS entity_rev (
  entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  revision_id INTEGER NOT NULL REFERENCES revision(id) ON DELETE CASCADE, PRIMARY KEY (entity_id, revision_id));
CREATE INDEX IF NOT EXISTS entity_rev_rev ON entity_rev(revision_id);
CREATE TABLE IF NOT EXISTS alias (
  alias_norm TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE, lang TEXT,
  origin TEXT NOT NULL, status TEXT NOT NULL, client_set TEXT NOT NULL DEFAULT '', vec BLOB,
  PRIMARY KEY (alias_norm, entity_id));
CREATE INDEX IF NOT EXISTS alias_entity ON alias(entity_id);
CREATE TABLE IF NOT EXISTS alias_seen (
  alias_norm TEXT NOT NULL, entity_id TEXT NOT NULL,
  snippet_id INTEGER NOT NULL REFERENCES snippet(id) ON DELETE CASCADE, PRIMARY KEY (alias_norm, entity_id, snippet_id),
  FOREIGN KEY (alias_norm, entity_id) REFERENCES alias(alias_norm, entity_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS alias_seen_snippet ON alias_seen(snippet_id);
CREATE TABLE IF NOT EXISTS claim (
  id INTEGER PRIMARY KEY, subject_id TEXT NOT NULL REFERENCES entity(id), predicate TEXT NOT NULL, value TEXT,
  modality TEXT NOT NULL, said_by TEXT REFERENCES entity(id), said_at TEXT NOT NULL, due TEXT,
  client_set TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, origin TEXT NOT NULL, confidence REAL,
  run_id INTEGER REFERENCES run(id), nkey TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS claim_subject ON claim(subject_id, status, said_at);
CREATE TABLE IF NOT EXISTS edge (
  id INTEGER PRIMARY KEY, src_id TEXT NOT NULL REFERENCES entity(id), rel TEXT NOT NULL,
  dst_id TEXT NOT NULL REFERENCES entity(id), modality TEXT NOT NULL, client_set TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, origin TEXT NOT NULL, confidence REAL, first_said_at TEXT NOT NULL, last_said_at TEXT NOT NULL,
  run_id INTEGER REFERENCES run(id), UNIQUE (src_id, rel, dst_id, modality));
CREATE INDEX IF NOT EXISTS edge_src ON edge(src_id, status);
CREATE INDEX IF NOT EXISTS edge_dst ON edge(dst_id, status);
CREATE TABLE IF NOT EXISTS evidence (
  target TEXT NOT NULL, target_id INTEGER NOT NULL, snippet_id INTEGER NOT NULL REFERENCES snippet(id) ON DELETE CASCADE,
  quote TEXT NOT NULL, q_start INTEGER NOT NULL, q_end INTEGER NOT NULL, said_by TEXT REFERENCES entity(id),
  PRIMARY KEY (target, target_id, snippet_id, q_start));
CREATE INDEX IF NOT EXISTS evidence_snippet ON evidence(snippet_id);
CREATE TABLE IF NOT EXISTS pm_override (
  id INTEGER PRIMARY KEY, target TEXT NOT NULL, target_key TEXT NOT NULL, field TEXT NOT NULL, value TEXT,
  at TEXT NOT NULL, via TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pm_override_target ON pm_override(target, target_key);
CREATE TABLE IF NOT EXISTS state_request (
  request_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, received_at TEXT NOT NULL,
  applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topic (
  id INTEGER PRIMARY KEY, label TEXT NOT NULL, client_set TEXT NOT NULL DEFAULT '', member_hash TEXT NOT NULL,
  summary_at TEXT, summary_run INTEGER REFERENCES run(id), state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topic_member (topic_id INTEGER REFERENCES topic(id) ON DELETE CASCADE, entity_id TEXT,
  weight REAL, PRIMARY KEY (topic_id, entity_id));
CREATE TABLE IF NOT EXISTS topic_sentence (
  id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL REFERENCES topic(id) ON DELETE CASCADE, ord INTEGER NOT NULL,
  text TEXT NOT NULL, UNIQUE (topic_id, ord));
CREATE TABLE IF NOT EXISTS topic_cite (
  sentence_id INTEGER NOT NULL REFERENCES topic_sentence(id) ON DELETE CASCADE, target TEXT NOT NULL,
  target_id INTEGER NOT NULL, PRIMARY KEY (sentence_id, target, target_id));
CREATE INDEX IF NOT EXISTS topic_cite_target ON topic_cite(target, target_id);
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, route TEXT, model TEXT, fallback_from TEXT, extractor TEXT,
  started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL, segments_plan INTEGER, segments_done INTEGER,
  in_tokens INTEGER, cached_tokens INTEGER, out_tokens INTEGER, est_usd REAL, usd REAL, est_pct REAL,
  pct_before REAL, pct_after REAL, seconds REAL, ceiling_kind TEXT, ceiling_value REAL, error TEXT);
CREATE TABLE IF NOT EXISTS run_item (
  run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  revision_id INTEGER NOT NULL REFERENCES revision(id) ON DELETE CASCADE, segment INTEGER NOT NULL, seg_hash TEXT,
  status TEXT NOT NULL, in_tokens INTEGER, out_tokens INTEGER, dropped INTEGER, error TEXT,
  PRIMARY KEY (run_id, revision_id, segment));
CREATE TABLE IF NOT EXISTS segment_cache (
  seg_hash TEXT NOT NULL, extractor TEXT NOT NULL, output TEXT NOT NULL, run_id INTEGER REFERENCES run(id),
  in_tokens INTEGER, out_tokens INTEGER, last_used TEXT NOT NULL, PRIMARY KEY (seg_hash, extractor));
"""
# Two additions to the schema of spec section 6, both deterministic caches: source.ext_id (the Google <id16> or the
# Notion page id a --revoked list names) and entity_source (the sources that back a deterministic entity, so its
# visibility follows its sources' access class, spec section 11.2 step 1).

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS snippet_fts USING fts5(text, heading, content='snippet', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE IF NOT EXISTS topic_fts USING fts5(text, content='topic_sentence', content_rowid='id');
"""

UID_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


class Refused(Exception):
    """The CLI refuses (no uid and no store, flag off, a store newer than the code)."""


def now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc(value) -> str:
    """A full UTC time string (2026-11-19T14:05:31Z) from an epoch, an aware datetime, a naive local datetime (the Mac's
    time zone) or an ISO string with or without zone."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        d = _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
    elif isinstance(value, _dt.datetime):
        d = value if value.tzinfo else value.astimezone()
    else:
        s = str(value).strip().replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        d = d if d.tzinfo else d.astimezone()
    return d.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def support_dir() -> Path:
    """$LT_SUPPORT_DIR, else the app root of $LT_AI_STATE (<root>/state/ai-routes.json), else Application Support."""
    if os.environ.get("LT_SUPPORT_DIR"):
        return Path(os.environ["LT_SUPPORT_DIR"]).expanduser()
    state = os.environ.get("LT_AI_STATE")
    if state:
        return Path(state).expanduser().parent.parent
    return Path("~/Library/Application Support/LiveTips").expanduser()


def store_path(uid: str) -> Path:
    if not uid or not UID_RX.match(uid):
        raise Refused("a project uid is required (--project-uid); the store never derives one from a path")
    return support_dir() / "evidence" / (uid + ".sqlite")


def _flock(path: Path, timeout: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    end = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                f.close()
                raise
            if time.monotonic() >= end:
                f.close()
                return None
            time.sleep(0.02)


class Store:
    def __init__(self, path: Path, conn: sqlite3.Connection, readonly: bool):
        self.path = Path(path)
        self.conn = conn
        self.readonly = readonly
        self.fts = True

    # ------------------------------------------------------------ open
    @classmethod
    def open(cls, path=None, uid: str = "", root: str = "", slug: str = "", readonly: bool = False) -> "Store":
        if not path:
            path = store_path(uid)
        path = Path(path).expanduser()
        if root:
            rp = os.path.realpath(os.path.expanduser(root))
            sp = os.path.realpath(str(path))
            if sp == rp or sp.startswith(rp.rstrip(os.sep) + os.sep):
                raise Refused("the store never lives inside the project folder")
        if readonly:
            if not path.exists():
                raise Refused("no store yet")
            conn = sqlite3.connect("file:{}?mode=ro".format(path), uri=True, timeout=5, check_same_thread=False)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        st = cls(path, conn, readonly)
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            if ver > SCHEMA_VERSION:
                conn.close()
                raise Refused("the store is newer than this code (schema {})".format(ver))
            with conn:
                conn.executescript(SCHEMA)
                try:
                    conn.executescript(FTS)
                except sqlite3.OperationalError:
                    st.fts = False
                conn.execute("PRAGMA user_version={}".format(SCHEMA_VERSION))
                st._meta_defaults(uid or path.stem, root, slug)
        else:
            st.fts = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='snippet_fts'").fetchone())
        return st

    def _meta_defaults(self, uid: str, root: str, slug: str) -> None:
        put = lambda k, v: self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (k, v))
        put("schema_version", str(SCHEMA_VERSION))
        put("rules_version", RULES_VERSION)
        put("evidence_uid", uid)
        put("created_at", now_utc())
        put("flag_state", "on")
        put("label_universe", "[]")
        put("slug_history", "[]")
        if root:
            self.set_meta("root_hint", os.path.realpath(os.path.expanduser(root)))
        if slug:
            hist = self.meta_json("slug_history", [])
            if slug not in hist:
                self.set_meta("slug_history", json.dumps(hist + [slug]))
            self.set_meta("slug_hint", slug)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ meta
    def meta(self, key: str, default: str = "") -> str:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def meta_json(self, key: str, default=None):
        try:
            return json.loads(self.meta(key, ""))
        except ValueError:
            return default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))

    # ------------------------------------------------------------ locks and the state-request log
    @property
    def base(self) -> Path:
        return self.path.with_suffix("")

    def project_lock(self, timeout: float = 2.0):
        return _flock(Path(str(self.base) + ".lock"), timeout)

    def worker_lock(self, timeout: float = 0.0):
        return _flock(self.path.parent / "worker.lock", timeout)

    @property
    def log_path(self) -> Path:
        return Path(str(self.base) + ".state-requests.jsonl")

    def append_request(self, kind: str, payload: dict, request_id: str = "") -> str:
        """One JSON line with one write() under the short log lock, never across a model request."""
        rid = request_id or str(uuid.uuid4())
        line = json.dumps({"request_id": rid, "kind": kind, "payload": payload, "at": now_utc()}, ensure_ascii=False) + "\n"
        lk = _flock(Path(str(self.base) + ".state-requests.lock"), 5.0)
        try:
            fd = os.open(str(self.log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        finally:
            if lk:
                lk.close()
        return rid

    def read_log(self) -> List[dict]:
        """Complete lines only: a last line with no trailing newline or not valid JSON is still being appended."""
        try:
            raw = self.log_path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return []
        out = []
        for ln in raw.split("\n")[:-1]:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("request_id") and e.get("kind"):
                out.append(e)
        return out

    def applied_ids(self) -> set:
        return {r[0] for r in self.conn.execute("SELECT request_id FROM state_request")}

    def pending_requests(self) -> List[dict]:
        done = self.applied_ids()
        return [e for e in self.read_log() if e["request_id"] not in done]

    def compact_log(self) -> None:
        """Drop applied lines: a new file renamed over the old one, under the log lock (readers see old or new)."""
        lk = _flock(Path(str(self.base) + ".state-requests.lock"), 5.0)
        try:
            pend = self.pending_requests()
            if not self.log_path.exists():
                return
            tmp = Path(str(self.log_path) + ".new")
            tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in pend), encoding="utf-8")
            os.replace(tmp, self.log_path)
        finally:
            if lk:
                lk.close()

    # ------------------------------------------------------------ small helpers
    def q(self, sql: str, args: Iterable = ()) -> List[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable = ()):
        return self.conn.execute(sql, tuple(args)).fetchone()


# ---------------------------------------------------------------- the write path for validated items (S3 calls it)

WS = re.compile(r"\s+")


def _fold(s: str) -> str:
    return WS.sub(" ", (s or "")).strip().casefold()


def _find_quote(text: str, quote: str):
    """Offsets of `quote` inside `text` after whitespace collapse and case fold (spec section 8.4 item 3)."""
    q = _fold(quote)
    if not q:
        return None
    # map folded positions back to the original text
    folded, back = [], []
    prev_space = True
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space:
                folded.append(" ")
                back.append(i)
            prev_space = True
            continue
        for c in ch.casefold():
            folded.append(c)
            back.append(i)
        prev_space = False
    f = "".join(folded)
    k = f.find(q)
    if k < 0:
        return None
    start = back[k]
    end = back[k + len(q) - 1] + 1
    return start, end


def known(conn, eid) -> Optional[str]:
    """`eid` when that entity exists, else None (said_by references entity)."""
    if not eid:
        return None
    return eid if conn.execute("SELECT 1 FROM entity WHERE id=?", (eid,)).fetchone() else None


def norm_alias(s: str) -> str:
    return _fold(s)


def entity_llm_id(etype: str, name: str) -> str:
    return "e-" + hashlib.sha1("{}|{}".format(etype, _fold(name)).encode("utf-8")).hexdigest()[:10]


def claim_nkey(subject: str, predicate: str, modality: str, value) -> str:
    v = hashlib.sha1(_fold(str(value if value is not None else "")).encode("utf-8")).hexdigest()[:10]
    return "{}|{}|{}|{}".format(subject, predicate, modality, v)


def edge_key(src: str, rel: str, dst: str, modality: str) -> str:
    return "{}|{}|{}|{}".format(src, rel, dst, modality)


FIXED_MODALITY = {"supersedes": "confirmation", "same_as": "statement"}


def apply_extraction(store: Store, revision_id: int, items: dict, run_id: Optional[int] = None,
                     threshold: float = 0.7) -> dict:
    """Write already validated items onto one revision (spec section 8.2): entity, alias (+ alias_seen), claim, edge and
    evidence rows. Every quote is located again in the revision's own snippets (the model's offsets are never
    trusted); an item whose quote is not found is dropped. The same statement found again adds evidence rows."""
    conn = store.conn
    snips = conn.execute("SELECT id, text, locator, said_at, speaker FROM snippet WHERE revision_id=? ORDER BY ord",
                         (revision_id,)).fetchall()
    now = now_utc()
    counts = {"entities": 0, "claims": 0, "edges": 0, "evidence": 0, "dropped": 0}

    def locate(quote: str, lines=None):
        for s in snips:
            if lines:
                a, b = json.loads(s["locator"]).get("lines", [0, 0])
                if lines[1] < a or lines[0] > b:
                    continue
            hit = _find_quote(s["text"], quote)
            if hit:
                return s, hit
        return None, None

    refs: Dict[str, str] = {}
    with conn:
        for e in items.get("entities") or []:
            name = str(e.get("name") or "").strip()
            etype = str(e.get("type") or "topic")
            if not name:
                counts["dropped"] += 1
                continue
            eid = e.get("known_id") or entity_llm_id(etype, name)
            s, hit = locate(e.get("quote") or name, e.get("lines"))
            if not s and not conn.execute("SELECT 1 FROM entity WHERE id=?", (eid,)).fetchone():
                counts["dropped"] += 1
                continue
            conn.execute("INSERT OR IGNORE INTO entity(id, type, name, origin, created_run, updated_at) VALUES (?,?,?,?,?,?)",
                         (eid, etype, name, "llm", run_id, now))
            refs[str(e.get("ref") or eid)] = eid
            counts["entities"] += 1
            if s:
                for al in [name] + list(e.get("aliases") or []):
                    an = norm_alias(al)
                    if not an:
                        continue
                    conn.execute("INSERT OR IGNORE INTO alias(alias_norm, entity_id, lang, origin, status) VALUES (?,?,?,?,?)",
                                 (an, eid, "", "llm", "confirmed"))
                    if conn.execute("SELECT origin FROM alias WHERE alias_norm=? AND entity_id=?", (an, eid)).fetchone()[0] == "llm":
                        conn.execute("INSERT OR IGNORE INTO alias_seen(alias_norm, entity_id, snippet_id) VALUES (?,?,?)",
                                     (an, eid, s["id"]))

        def ref(x):
            x = str(x or "")
            if x in refs:
                return refs[x]
            return x if conn.execute("SELECT 1 FROM entity WHERE id=?", (x,)).fetchone() else ""

        for c in items.get("claims") or []:
            subj = ref(c.get("subject"))
            s, hit = locate(c.get("quote") or "", c.get("lines"))
            if not subj or not s:
                counts["dropped"] += 1
                continue
            mod = str(c.get("modality") or "statement")
            nkey = claim_nkey(subj, str(c.get("predicate") or "said"), mod, c.get("value"))
            conf = c.get("confidence")
            status = "suggestion" if (conf is not None and float(conf) < threshold) else "accepted"
            conn.execute("INSERT OR IGNORE INTO claim(subject_id, predicate, value, modality, said_by, said_at, due, status,"
                         " origin, confidence, run_id, nkey) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (subj, str(c.get("predicate") or "said"), None if c.get("value") is None else str(c.get("value")),
                          mod, ref(c.get("said_by")) or None, s["said_at"] or now, c.get("due"), status, "llm", conf,
                          run_id, nkey))
            cid = conn.execute("SELECT id FROM claim WHERE nkey=?", (nkey,)).fetchone()[0]
            by = ref(c.get("said_by")) or known(conn, s["speaker"])
            conn.execute("INSERT OR IGNORE INTO evidence(target, target_id, snippet_id, quote, q_start, q_end, said_by)"
                         " VALUES ('claim',?,?,?,?,?,?)", (cid, s["id"], s["text"][hit[0]:hit[1]][:300], hit[0], hit[1], by))
            counts["claims"] += 1
            counts["evidence"] += 1

        for r in items.get("relations") or []:
            a, b = ref(r.get("from")), ref(r.get("to"))
            s, hit = locate(r.get("quote") or "", r.get("lines"))
            if not a or not b or not s:
                counts["dropped"] += 1
                continue
            rel = str(r.get("rel") or "mentions")
            mod = FIXED_MODALITY.get(rel, str(r.get("modality") or "statement"))
            conf = r.get("confidence")
            status = "suggestion" if (rel == "same_as" or a == b or (conf is not None and float(conf) < threshold)) else "accepted"
            said = s["said_at"] or now
            conn.execute("INSERT OR IGNORE INTO edge(src_id, rel, dst_id, modality, status, origin, confidence, first_said_at,"
                         " last_said_at, run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (a, rel, b, mod, status, "llm", conf, said, said, run_id))
            eid = conn.execute("SELECT id FROM edge WHERE src_id=? AND rel=? AND dst_id=? AND modality=?", (a, rel, b, mod)).fetchone()[0]
            conn.execute("UPDATE edge SET first_said_at=min(first_said_at, ?), last_said_at=max(last_said_at, ?) WHERE id=?",
                         (said, said, eid))
            conn.execute("INSERT OR IGNORE INTO evidence(target, target_id, snippet_id, quote, q_start, q_end, said_by)"
                         " VALUES ('edge',?,?,?,?,?,?)", (eid, s["id"], s["text"][hit[0]:hit[1]][:300], hit[0], hit[1],
                                                         known(conn, s["speaker"])))
            counts["edges"] += 1
            counts["evidence"] += 1
        conn.execute("UPDATE claim SET said_at=(SELECT min(sn.said_at) FROM evidence ev JOIN snippet sn ON sn.id=ev.snippet_id"
                     " WHERE ev.target='claim' AND ev.target_id=claim.id) WHERE id IN (SELECT target_id FROM evidence"
                     " ev JOIN snippet sn ON sn.id=ev.snippet_id WHERE ev.target='claim' AND sn.revision_id=?)", (revision_id,))
        conn.execute("UPDATE revision SET extract_state='done' WHERE id=?", (revision_id,))
    return counts


@contextlib.contextmanager
def held(lock_file):
    try:
        yield lock_file
    finally:
        if lock_file:
            lock_file.close()
