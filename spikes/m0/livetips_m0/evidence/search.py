"""PB-52 G2 (S1): search and entity cards over the evidence store (spec section 11).

    search(store, query, scope=None, as_of=None, window=None, exclude=(), kinds=None, mode="prep", budget=None,
           include_suggestions=False) -> dict
    entity_card(store, entity_id, scope=None, as_of=None, window=None) -> dict or None

Access (section 11.2 step 1) and time (step 2) are computed in SQL into one temporary table of snippet classes before
any candidate is selected, so ranking never sees a row the request may not see. Each evidence row is in one class:
`denied` (source excluded or revoked, a pending hide or revoke in the state-request log, or the side `other` for the
scope), `ignored` (source deleted, or in the request's `exclude`) or `pass`. A claim or edge passes when a passing row
exists and no denied row does; every returned field comes from the passing rows only. No access query reads
`client_set` or `entity.client`: the side is decided from `source.uri`, `snippet_mark` and `entity_mark` against the
request's scope. The one exception is S4's topics (prepare_topics): a topic summary was written from all of its
topic's facts, so a scoped request sees only shared topics and its own client's, by `topic.client_set`, and then no
sentence of a topic whose summary input has a row on another client's side for the scope (topic_input).
"""
from __future__ import annotations

import json
import re
import struct
import time
from typing import Dict, Iterable, List, Optional

from .. import client_scope as CS
from .. import identity as ID
from . import ingest as IG
from . import store as S

WORD = re.compile(r"[^\W_]+", re.U)
STOP = {"the", "and", "for", "with", "what", "where", "when", "who", "how", "are", "was", "were", "is", "of", "to",
        "in", "on", "a", "an", "we", "it", "do", "did", "does", "about", "that", "this", "still"}
DET_ORIGINS = ("g1", "tracker", "voices", "pm")
CALL_KINDS = IG.CALL_KINDS
REV_AT = "rv.valid_from<=:as_of AND (rv.valid_to IS NULL OR rv.valid_to>:as_of)"     # a revision valid at as_of
G1_ITEM = ("decision", "risk")            # g1 entities carried by a revision (entity_rev), not by the register


class Budget(dict):
    def __init__(self, k: int = 6, chars: int = 14000, ms: int = 1000, hops: int = 1):
        super().__init__(k=k, chars=chars, ms=ms, hops=hops)


# ---------------------------------------------------------------- the access and time table

def _scope_parts(scope: Optional[dict]):
    if not scope or not scope.get("client") or not scope.get("others"):
        return None
    folders = scope.get("folders") or {}
    own = [r.replace("\\", "/") for r in folders.get(scope["client"], [])]
    oth = [r.replace("\\", "/") for o in scope["others"] for r in folders.get(o, [])]
    return scope["client"], list(scope["others"]), own, oth


def stale_labels(store: S.Store, scope: Optional[dict]) -> List[str]:
    sp = _scope_parts(scope)
    if not sp:
        return []
    uni = set(store.meta_json("label_universe", []) or [])
    return sorted({sp[0], *sp[1]} - uni)


def prepare(store: S.Store, scope: Optional[dict], as_of: Optional[str], exclude: Iterable = (), stale: bool = False,
            window=None) -> None:
    """TEMP tables of this request: _snip(sid, src, cls, tok) with cls in denied/ignored/pass and tok = valid at as_of,
    _src(id, cls) for sources, _ent(id) for visible entities."""
    c = store.conn
    for t in ("_others", "_ownf", "_othf", "_deny", "_excl", "_eside", "_snip", "_src", "_rev", "_ent", "_claim", "_edge",
              "_tsent", "_ttop", "_toth"):
        c.execute("DROP TABLE IF EXISTS temp.{}".format(t))
    c.execute("CREATE TEMP TABLE _others(name TEXT PRIMARY KEY)")
    c.execute("CREATE TEMP TABLE _ownf(rel TEXT)")
    c.execute("CREATE TEMP TABLE _othf(rel TEXT)")
    c.execute("CREATE TEMP TABLE _deny(id INTEGER PRIMARY KEY)")
    c.execute("CREATE TEMP TABLE _excl(id INTEGER PRIMARY KEY)")
    sp = _scope_parts(scope)
    client = sp[0] if sp else ""
    if sp:
        c.executemany("INSERT OR IGNORE INTO _others VALUES (?)", [(o,) for o in sp[1]])
        c.executemany("INSERT INTO _ownf VALUES (?)", [(r,) for r in sp[2]])
        c.executemany("INSERT INTO _othf VALUES (?)", [(r,) for r in sp[3]])
    # pending hides and revokes of the state-request log are denied exactly as after they apply (section 13.1)
    stems, rev = set(), set()
    for req in store.pending_requests():
        p = req.get("payload") or {}
        if req["kind"] == "hidden_calls":
            stems |= set(IG._stems(p.get("hidden_calls") or []))
        elif req["kind"] == "revoked":
            rev |= set(IG._revoked_ids(p.get("revoked") or []))
    if stems:
        c.executemany("INSERT OR IGNORE INTO _deny SELECT id FROM source WHERE call_key=? AND kind IN ({})".format(
            ",".join("'{}'".format(k) for k in CALL_KINDS)), [(s,) for s in stems])
    if rev:
        c.executemany("INSERT OR IGNORE INTO _deny SELECT id FROM source WHERE ext_id=?", [(x,) for x in rev])
    for x in exclude or ():
        if isinstance(x, int) or str(x).isdigit():
            c.execute("INSERT OR IGNORE INTO _excl VALUES (?)", (int(x),))
        else:
            c.execute("INSERT OR IGNORE INTO _excl SELECT id FROM source WHERE uri=?", (str(x),))
    under = lambda tbl: ("EXISTS (SELECT 1 FROM {} f WHERE s.uri=f.rel OR substr(s.uri, 1, length(f.rel)+1)=f.rel||'/')"
                         .format(tbl))
    item_other = ("EXISTS (SELECT 1 FROM snippet_mark m JOIN _eside e ON e.id=m.name WHERE m.snippet_id=sn.id"
                  " AND m.kind='item' AND e.side='other')")
    # a register event: item_side of an add with its own id (its written client, PB-73, then series by prefix, then
    # call, then text), as filter_register; an add event carries at most one client
    has = lambda kind, cond: ("EXISTS (SELECT 1 FROM snippet_mark r WHERE r.snippet_id=sn.id AND r.pos=a.pos"
                              " AND r.kind='{}' AND {})".format(kind, cond))
    pre_own, pre_oth = "substr(r.name, 1, length(:client))=:client", ("EXISTS (SELECT 1 FROM _others o"
                                                                    " WHERE substr(r.name, 1, length(o.name))=o.name)")
    own, oth = "r.name=:client", "r.name IN (SELECT name FROM _others)"
    reg_other = ("EXISTS (SELECT 1 FROM snippet_mark a WHERE a.snippet_id=sn.id"
                 " AND a.kind IN ('rclient','rseries','rcall','rtext')"
                 " AND NOT {lo} AND ({lx} OR (NOT {so} AND ({sx} OR (NOT {co} AND ({cx} OR (NOT {to} AND {tx})))))))"
                 ).format(lo=has("rclient", own), lx=has("rclient", oth),
                          so=has("rseries", pre_own), sx=has("rseries", pre_oth), co=has("rcall", own),
                          cx=has("rcall", oth), to=has("rtext", own), tx=has("rtext", oth))
    if not sp:
        side = "'shared'"
    elif stale:
        # the live reader cannot relabel: only rows under the own client's folders pass (section 5.3)
        side = "CASE WHEN {} THEN 'own' ELSE 'other' END".format(under("_ownf"))
    else:
        side = ("CASE WHEN {own} THEN 'own' WHEN {oth} THEN 'other'"
                " WHEN EXISTS (SELECT 1 FROM snippet_mark m WHERE m.snippet_id=sn.id AND m.kind='path' AND m.name=:client)"
                " THEN 'own'"
                " WHEN EXISTS (SELECT 1 FROM snippet_mark m WHERE m.snippet_id=sn.id AND m.kind='path'"
                " AND m.name IN (SELECT name FROM _others)) THEN 'other'"
                " WHEN EXISTS (SELECT 1 FROM snippet_mark m WHERE m.snippet_id=sn.id AND m.kind IN ('heading','line')"
                " AND m.name IN (SELECT name FROM _others) AND NOT EXISTS (SELECT 1 FROM snippet_mark m2"
                " WHERE m2.snippet_id=sn.id AND m2.kind=m.kind AND m2.pos=m.pos AND m2.name=:client)) THEN 'other'"
                " WHEN {item} OR {reg} THEN 'other' ELSE 'shared' END").format(own=under("_ownf"), oth=under("_othf"),
                                                                                item=item_other, reg=reg_other)
    # the side of a deterministic entity: its one written client (client_side, PB-73), then series_side, then
    # side_of_name, then text_side on its entity_mark rows
    if sp:
        eside = ("CASE WHEN EXISTS (SELECT 1 FROM entity_mark m WHERE m.entity_id=en.id AND m.kind='client'"
                 " AND m.name=:client) THEN 'own'"
                 " WHEN EXISTS (SELECT 1 FROM entity_mark m WHERE m.entity_id=en.id AND m.kind='client'"
                 " AND m.name IN (SELECT name FROM _others)) THEN 'other'"
                 " WHEN EXISTS (SELECT 1 FROM entity_mark m WHERE m.entity_id=en.id AND m.kind='series'"
                 " AND substr(m.name, 1, length(:client))=:client) THEN 'own'"
                 " WHEN EXISTS (SELECT 1 FROM entity_mark m JOIN _others o WHERE m.entity_id=en.id AND m.kind='series'"
                 " AND substr(m.name, 1, length(o.name))=o.name) THEN 'other'"
                 " WHEN EXISTS (SELECT 1 FROM entity_mark m WHERE m.entity_id=en.id AND m.kind='call' AND m.name=:client)"
                 " THEN 'own' WHEN EXISTS (SELECT 1 FROM entity_mark m WHERE m.entity_id=en.id AND m.kind='call'"
                 " AND m.name IN (SELECT name FROM _others)) THEN 'other'"
                 " WHEN EXISTS (SELECT 1 FROM entity_mark m WHERE m.entity_id=en.id AND m.kind='text' AND m.name=:client)"
                 " THEN 'own' WHEN EXISTS (SELECT 1 FROM entity_mark m WHERE m.entity_id=en.id AND m.kind='text'"
                 " AND m.name IN (SELECT name FROM _others)) THEN 'other' ELSE 'shared' END")
        if stale:
            eside = "'other'"
    else:
        eside = "'shared'"
    c.execute("CREATE TEMP TABLE _eside AS SELECT en.id AS id, {} AS side FROM entity en".format(eside), {"client": client})
    c.execute("CREATE INDEX temp._eside_i ON _eside(id)")
    if as_of:
        tok = ("((s.kind='register' AND rv.superseded_at IS NULL) OR (s.kind<>'register' AND rv.valid_from<=:as_of"
               " AND (rv.valid_to IS NULL OR rv.valid_to>:as_of))) AND coalesce(sn.said_at, rv.valid_from)<=:as_of")
    else:
        tok = "rv.superseded_at IS NULL"
    if window:
        tok += " AND coalesce(sn.said_at, rv.valid_from)>=:w0 AND coalesce(sn.said_at, rv.valid_from)<=:w1"
    params = {"client": client, "as_of": as_of or "", "w0": (window or ("", ""))[0] or "",
              "w1": (window or ("", "9999"))[1] or "9999"}
    store._side = (side, {"client": client})     # prepare_topics reuses the side rule on summary inputs only (S4)
    c.execute("CREATE TEMP TABLE _snip AS SELECT sn.id AS sid, s.id AS src, s.origin AS origin,"
              " CASE WHEN s.state IN ('excluded','revoked') OR s.id IN (SELECT id FROM _deny) OR ({side})='other'"
              " THEN 'denied' WHEN s.state='deleted' OR s.id IN (SELECT id FROM _excl) THEN 'ignored' ELSE 'pass' END AS cls,"
              " CASE WHEN {tok} THEN 1 ELSE 0 END AS tok"
              " FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id JOIN source s ON s.id=rv.source_id".format(
                  side=side, tok=tok), params)
    c.execute("CREATE INDEX temp._snip_i ON _snip(sid)")
    # source classes for the rows with no evidence of their own (deterministic entities)
    c.execute("CREATE TEMP TABLE _src AS SELECT s.id AS id, CASE WHEN s.state IN ('excluded','revoked')"
              " OR s.id IN (SELECT id FROM _deny) THEN 'denied' WHEN s.state='deleted' OR s.id IN (SELECT id FROM _excl)"
              " THEN 'ignored' ELSE 'pass' END AS cls, EXISTS (SELECT 1 FROM revision rv WHERE rv.source_id=s.id AND ({})) AS tok"
              " FROM source s".format(
                  "(s.kind='register' AND rv.superseded_at IS NULL) OR (rv.valid_from<=:as_of AND (rv.valid_to IS NULL"
                  " OR rv.valid_to>:as_of))" if as_of else "rv.superseded_at IS NULL"), params)
    # the register task ids the request may see: ids of passing register events up to as_of (filter_register)
    c.execute("CREATE TEMP TABLE _rev(id TEXT PRIMARY KEY, norm TEXT)")
    c.executemany("INSERT OR IGNORE INTO _rev VALUES (?,?)", [(str(e["id"]), S.norm_alias(str(e["id"])))
                                                              for e in IG.register_events_at(store, as_of, passing=True)
                                                              if e.get("id")])
    c.execute("CREATE INDEX temp._rev_n ON _rev(norm)")
    # claims and edges: a passing row exists and no denied row does (strict denial); rejected by the PM: never
    c.execute("CREATE TEMP TABLE _claim AS SELECT cl.id AS id FROM claim cl WHERE"
              " EXISTS (SELECT 1 FROM evidence ev JOIN _snip x ON x.sid=ev.snippet_id WHERE ev.target='claim'"
              " AND ev.target_id=cl.id AND x.cls='pass' AND x.tok=1)"
              " AND NOT EXISTS (SELECT 1 FROM evidence ev JOIN _snip x ON x.sid=ev.snippet_id WHERE ev.target='claim'"
              " AND ev.target_id=cl.id AND x.cls='denied')"
              " AND NOT EXISTS (SELECT 1 FROM pm_override o WHERE o.target='claim' AND o.target_key=cl.nkey AND o.field='reject')")
    c.execute("CREATE TEMP TABLE _edge AS SELECT e.id AS id FROM edge e WHERE"
              " EXISTS (SELECT 1 FROM evidence ev JOIN _snip x ON x.sid=ev.snippet_id WHERE ev.target='edge'"
              " AND ev.target_id=e.id AND x.cls='pass' AND x.tok=1)"
              " AND NOT EXISTS (SELECT 1 FROM evidence ev JOIN _snip x ON x.sid=ev.snippet_id WHERE ev.target='edge'"
              " AND ev.target_id=e.id AND x.cls='denied')"
              " AND NOT EXISTS (SELECT 1 FROM pm_override o WHERE o.target='edge' AND o.field='reject'"
              " AND o.target_key=e.src_id||'|'||e.rel||'|'||e.dst_id||'|'||e.modality)")
    es_ok = ("EXISTS (SELECT 1 FROM entity_source es JOIN _src x ON x.id=es.source_id WHERE es.entity_id=en.id"
             " AND x.cls='pass' AND x.tok=1)")
    # a decision or risk of notes, a sidecar or decisions.md: a passing revision that carries it, valid at as_of
    # (any revision of its file is not enough: the item may come in a later one, section 11.2)
    er_ok = ("EXISTS (SELECT 1 FROM entity_rev er JOIN revision rv ON rv.id=er.revision_id JOIN _src x ON x.id=rv.source_id"
             " WHERE er.entity_id=en.id AND x.cls='pass' AND {})".format(REV_AT if as_of else "rv.superseded_at IS NULL"))
    c.execute("CREATE TEMP TABLE _ent AS SELECT en.id AS id FROM entity en WHERE"
              " EXISTS (SELECT 1 FROM claim cl JOIN _claim x ON x.id=cl.id WHERE cl.subject_id=en.id)"
              " OR EXISTS (SELECT 1 FROM edge e JOIN _edge x ON x.id=e.id WHERE e.src_id=en.id OR e.dst_id=en.id)"
              " OR (en.origin IN ('g1','tracker','pm') AND CASE WHEN en.origin='g1' AND en.type='task'"
              # a register task: one of its ids has a passing event, never the folded entity's side (section 5.3)
              " THEN (en.id IN (SELECT id FROM _rev) OR EXISTS (SELECT 1 FROM alias a JOIN _rev r ON r.norm=a.alias_norm"
              " WHERE a.entity_id=en.id AND a.origin='g1')) AND {es}"
              " WHEN en.origin='g1' THEN (SELECT side FROM _eside WHERE id=en.id)<>'other' AND {er}"
              " ELSE (SELECT side FROM _eside WHERE id=en.id)<>'other' AND {es} END)".format(es=es_ok, er=er_ok) +
              " OR (en.origin='voices' AND NOT EXISTS (SELECT 1 FROM entity_source es WHERE es.entity_id=en.id))", params)
    c.execute("CREATE INDEX temp._ent_i ON _ent(id)")
    if c.in_transaction:
        c.commit()          # only TEMP tables were written: end the implicit transaction so no read lock stays held


# ---------------------------------------------------------------- overrides and state under as_of

def resolve_key(store: S.Store, key: str) -> List[str]:
    """An entity key: its id, or an alias key such as notion:<page id> resolved through `alias` (never at write time)."""
    key = str(key or "")
    if store.one("SELECT 1 FROM entity WHERE id=?", (key,)):
        return [key]
    return [r[0] for r in store.q("SELECT entity_id FROM alias WHERE alias_norm=?", (S.norm_alias(key),))]


def supersedes(store: S.Store, as_of: Optional[str]) -> List[dict]:
    """The PM's supersede overrides that apply at `as_of`: [{old, new, at}] (new may be "" when not resolved yet)."""
    out = []
    for o in store.q("SELECT * FROM pm_override WHERE field='supersede' ORDER BY at, id"):
        if as_of and o["at"] > as_of:
            continue
        olds = resolve_key(store, o["target_key"])
        news = resolve_key(store, o["value"] or "")
        for old in olds:
            out.append({"old": old, "new": news[0] if news else "", "at": o["at"]})
    return out


def _task_item(store: S.Store, eid: str, fold: Dict[str, dict]) -> Optional[dict]:
    """The item of a register task in a request's fold: by its id, else by one of its g1 ids (a mention joined to
    the task, seen on its own when the request may see only that mention's events)."""
    it = fold.get(eid)
    if it is None:
        ids = {r[0] for r in store.q("SELECT alias_norm FROM alias WHERE entity_id=? AND origin='g1'", (eid,))}
        it = next((v for k, v in fold.items() if S.norm_alias(k) in ids), None)
    return it


def entity_state(store: S.Store, eid: str, as_of: Optional[str], fold: Optional[Dict[str, dict]] = None) -> dict:
    """Name and status of an entity at `as_of`, derived from dated rows (never the "now" caches under as_of). fold:
    the request's fold of the register events it may see (search and entity_card pass it); None folds every event
    (no request scope). A register task is named only from that fold, never from the stored name of the full fold."""
    e = store.one("SELECT * FROM entity WHERE id=?", (eid,))
    if not e:
        return {}
    name, status, merged, shown = e["name"], e["status"], e["merged_into"], eid
    if as_of and e["origin"] == "g1" and e["type"] in G1_ITEM and not store.one(
            "SELECT 1 FROM entity_rev er JOIN revision rv ON rv.id=er.revision_id WHERE er.entity_id=?1 AND "
            + REV_AT.replace(":as_of", "?2"), (eid, as_of)):
        return {}                               # no revision of its file carried it yet at as_of (section 11.2)
    if e["type"] == "task" and e["origin"] == "g1":
        scoped = fold is not None
        it = _task_item(store, eid, fold) if scoped else IG.register_fold_at(store, as_of).get(eid)
        if it is None and (as_of or scoped):
            return {}
        if it:
            name, status = it.get("text") or it["id"], it.get("status") or status
            if eid not in (it.get("ids") or [it["id"]]):
                shown = it["id"]                # only the joined mention is visible: it is named by its own id
    elif e["type"] == "task" and e["origin"] == "tracker":
        # a Tracker item: name and status of the Tracker revision valid at as_of (the current one without as_of)
        r = store.one("SELECT sn.locator FROM entity_source es JOIN source s ON s.id=es.source_id AND s.kind='tracker'"
                      " JOIN revision rv ON rv.source_id=s.id JOIN snippet sn ON sn.revision_id=rv.id WHERE es.entity_id=?"
                      " AND {} ORDER BY rv.valid_from DESC, rv.id DESC LIMIT 1".format(
                          "rv.valid_from<=? AND (rv.valid_to IS NULL OR rv.valid_to>?)" if as_of else "rv.superseded_at IS NULL"),
                      (eid, as_of, as_of) if as_of else (eid,))
        if r is None and as_of:
            return {}
        loc = json.loads(r["locator"]) if r else {}
        name = name if e["pm_locked"] else (loc.get("text") or name)
        status = loc.get("status") or (None if as_of else status)
    if as_of and merged and (e["merged_at"] or "") > as_of:
        merged = None
    if any(s["old"] == eid for s in supersedes(store, as_of)):
        status = "superseded"
    return {"id": shown, "type": e["type"], "name": name, "status": status, "merged_into": merged, "origin": e["origin"]}


# ---------------------------------------------------------------- rows from passing evidence only

def _rows(store: S.Store, target: str, tid: int) -> List[dict]:
    return [dict(r) for r in store.q(
        "SELECT ev.quote, ev.q_start, ev.q_end, ev.said_by, sn.id AS snippet_id, sn.said_at, sn.locator, s.uri, s.title,"
        " s.origin, s.id AS source_id FROM evidence ev JOIN _snip x ON x.sid=ev.snippet_id AND x.cls='pass' AND x.tok=1"
        " JOIN snippet sn ON sn.id=ev.snippet_id JOIN revision rv ON rv.id=sn.revision_id JOIN source s ON s.id=rv.source_id"
        " WHERE ev.target=? AND ev.target_id=? ORDER BY sn.said_at, sn.id", (target, tid))]


def _fact(store: S.Store, kind: str, row, ents: Dict[str, dict]) -> dict:
    rows = _rows(store, kind, row["id"])
    first = rows[0] if rows else {}
    out = {"kind": kind, "id": row["id"], "modality": row["modality"], "status": row["status"], "origin": row["origin"],
           "quotes": [{"quote": r["quote"], "uri": r["uri"], "locator": json.loads(r["locator"]), "said_at": r["said_at"]}
                      for r in rows],
           "said_at": first.get("said_at"), "said_by": first.get("said_by"),
           "confirmations": len({r["source_id"] for r in rows if r["origin"] in ("primary", "pm")}),
           "first_said_at": first.get("said_at"), "last_said_at": rows[-1]["said_at"] if rows else None,
           "source": first.get("uri"), "text": first.get("quote")}
    if kind == "claim":
        out.update(subject=row["subject_id"], subject_name=(ents.get(row["subject_id"]) or {}).get("name"),
                   predicate=row["predicate"], value=row["value"], nkey=row["nkey"])
    else:
        out.update(src=row["src_id"], rel=row["rel"], dst=row["dst_id"])
    return out


# ---------------------------------------------------------------- search

def fts_query(query: str) -> str:
    words = [w.lower() for w in WORD.findall(query or "") if w.lower() not in STOP and (len(w) > 1 or w.isdigit())]
    return " OR ".join('"{}"*'.format(w.replace('"', "")) for w in dict.fromkeys(words))


def _relabel_if_stale(store: S.Store, scope: Optional[dict], mode: str) -> tuple:
    stale = stale_labels(store, scope)
    if not stale:
        return False, {}
    if mode == "live" or store.readonly:
        return True, {}
    root = store.meta("root_hint")
    uni = set(CS.label_universe(root)) if root else set()
    added = sorted(uni - set(store.meta_json("label_universe", []) or []))
    if not added:
        return True, {}
    lock = store.project_lock(2.0)          # the relabel writes: one writer per project
    if not lock:
        return True, {}                     # a pass holds the store: answer like the live reader (coverage, no leak)
    with S.held(lock):
        rep = IG.relabel(store, uni, added)
    return bool(stale_labels(store, scope)), rep


def search(store: S.Store, query: str, *, scope: Optional[dict] = None, as_of=None, window=None, exclude=(),
           kinds=None, mode: str = "prep", budget=None, include_suggestions: bool = False, query_vec=None) -> dict:
    t0 = time.monotonic()
    budget = budget or Budget(ms=150 if mode == "live" else 1000)
    if mode == "live" and query_vec is not None:
        raise ValueError("mode=live refuses query_vec (a query embedding is a model call)")
    as_of = S.utc(as_of) if as_of else None
    stale, relabel_rep = _relabel_if_stale(store, scope, mode)
    prepare(store, scope, as_of, exclude, stale=stale, window=window)
    kinds = set(kinds or ("snippet", "claim", "edge"))
    fold = IG.register_fold_at(store, as_of, passing=True)
    sup = {s["old"] for s in supersedes(store, as_of)}
    q = fts_query(query)
    items: List[dict] = []
    candidates: List[int] = []
    hits: List[tuple] = []
    if q and store.fts:
        hits = [(r[0], r[1]) for r in store.q(
            "SELECT f.rowid, bm25(snippet_fts) FROM snippet_fts f JOIN _snip x ON x.sid=f.rowid"
            " WHERE snippet_fts MATCH ? AND x.cls='pass' AND x.tok=1 ORDER BY bm25(snippet_fts) LIMIT 200", (q,))]
    elif q:
        words = [w.strip('"*') for w in q.split(" OR ")]
        for r in store.q("SELECT sn.id, sn.text FROM snippet sn JOIN _snip x ON x.sid=sn.id WHERE x.cls='pass' AND x.tok=1"):
            score = -sum(r["text"].lower().count(w) for w in words)
            if score < 0:
                hits.append((r["id"], score))
        hits.sort(key=lambda h: h[1])
    if query_vec is not None:
        hits = _with_vectors(store, hits, query_vec)
    candidates = [h[0] for h in hits]
    # entities matched by alias or name (visible only); claims and edges of matched snippets and entities
    qn = " " + S.norm_alias(" ".join(WORD.findall(query or ""))) + " "
    ents: Dict[str, dict] = {}
    for r in store.q("SELECT DISTINCT a.entity_id, a.alias_norm, a.origin FROM alias a JOIN _ent v ON v.id=a.entity_id"):
        an = " " + " ".join(WORD.findall(r["alias_norm"])) + " "
        if len(an.strip()) < 3 or an not in qn:
            continue
        if not _alias_ok(store, r["alias_norm"], r["origin"], r["entity_id"], fold):
            continue
        st = entity_state(store, r["entity_id"], as_of, fold)
        if st:
            ents[r["entity_id"]] = st
    snip_ids = set(candidates)
    claim_rows = store.q("SELECT cl.* FROM claim cl JOIN _claim x ON x.id=cl.id WHERE cl.subject_id IN (SELECT value FROM"
                         " json_each(?)) OR EXISTS (SELECT 1 FROM evidence ev WHERE ev.target='claim' AND ev.target_id=cl.id"
                         " AND ev.snippet_id IN (SELECT value FROM json_each(?)))",
                         (json.dumps(list(ents)), json.dumps(list(snip_ids))))
    edge_rows = store.q("SELECT e.* FROM edge e JOIN _edge x ON x.id=e.id WHERE e.src_id IN (SELECT value FROM json_each(?))"
                        " OR e.dst_id IN (SELECT value FROM json_each(?)) OR EXISTS (SELECT 1 FROM evidence ev"
                        " WHERE ev.target='edge' AND ev.target_id=e.id AND ev.snippet_id IN (SELECT value FROM json_each(?)))",
                        (json.dumps(list(ents)), json.dumps(list(ents)), json.dumps(list(snip_ids))))
    visible = {r[0] for r in store.q("SELECT id FROM _ent")}
    for e in list(claim_rows):
        if e["subject_id"] not in ents and e["subject_id"] in visible:
            st = entity_state(store, e["subject_id"], as_of, fold)
            if st:
                ents[e["subject_id"]] = st
    if "claim" in kinds:
        for r in claim_rows:
            if r["subject_id"] not in visible or r["subject_id"] in sup:
                continue
            if r["status"] == "suggestion" and not include_suggestions:
                continue
            items.append(dict(_fact(store, "claim", r, ents), score=_score(hits, r, store, "claim")))
    if "edge" in kinds:
        for r in edge_rows:
            if r["src_id"] not in visible or r["dst_id"] not in visible:
                continue
            if r["status"] == "suggestion" and not include_suggestions:
                continue
            f = _fact(store, "edge", r, ents)
            f["why"] = "{} -> {} -> {}".format(_name(store, r["src_id"], ents, as_of, fold), r["rel"],
                                               _name(store, r["dst_id"], ents, as_of, fold))
            items.append(dict(f, score=_score(hits, r, store, "edge")))
    if "snippet" in kinds:
        per_src: Dict[int, int] = {}
        for sid, score in hits:
            r = store.one("SELECT sn.*, s.uri, s.title, s.id AS source_id, s.kind AS skind, s.origin FROM snippet sn"
                          " JOIN revision rv ON rv.id=sn.revision_id JOIN source s ON s.id=rv.source_id WHERE sn.id=?", (sid,))
            if per_src.get(r["source_id"], 0) >= 2:
                continue
            per_src[r["source_id"]] = per_src.get(r["source_id"], 0) + 1
            items.append({"kind": "snippet", "id": sid, "text": r["text"], "locator": json.loads(r["locator"]),
                          "source": r["uri"], "title": r["title"], "said_at": r["said_at"], "origin": r["origin"],
                          "score": -float(score)})
    items.sort(key=lambda i: -(i.get("score") or 0))
    out, chars, truncated = [], 0, False
    for it in items:
        size = len(it.get("text") or "")
        if len(out) >= budget["k"] or chars + size > budget["chars"] or (time.monotonic() - t0) * 1000 > budget["ms"]:
            truncated = True
            break
        out.append(it)
        chars += size
    return {"items": out, "entities": list(ents.values()), "truncated": truncated, "scope_stale": stale,
            "candidates": candidates, "relabel": relabel_rep, "ms": round((time.monotonic() - t0) * 1000, 2)}


VEC_TOP = 50
VEC_MIN = 0.35


def _with_vectors(store: S.Store, hits: List[tuple], query_vec) -> List[tuple]:
    """Section 11.2: with query_vec, cosine over the allowed snippets' stored vectors is a second candidate source. A
    snippet found by both keeps its text score plus the cosine; one found only by its vector enters with the cosine
    alone (on the bm25 sign: lower is better). Only passing rows are read, so ranking still sees nothing denied."""
    q = [float(x) for x in query_vec]
    qn = sum(x * x for x in q) ** 0.5 or 1.0
    sims = []
    for r in store.q("SELECT sn.id, sn.vec FROM snippet sn JOIN _snip x ON x.sid=sn.id WHERE x.cls='pass' AND x.tok=1"
                     " AND sn.vec IS NOT NULL"):
        v = struct.unpack("<{}e".format(len(r["vec"]) // 2), r["vec"])
        if len(v) != len(q):
            continue
        vn = sum(x * x for x in v) ** 0.5 or 1.0
        c = sum(a * b for a, b in zip(q, v)) / (qn * vn)
        if c >= VEC_MIN:
            sims.append((r["id"], c))
    sims.sort(key=lambda s: -s[1])
    score = {sid: sc for sid, sc in hits}
    for sid, c in sims[:VEC_TOP]:
        score[sid] = score.get(sid, 0.0) - c
    return sorted(score.items(), key=lambda h: h[1])


def _score(hits, row, store, kind) -> float:
    best = {sid: -float(sc) for sid, sc in hits}
    ids = [r[0] for r in store.q("SELECT snippet_id FROM evidence WHERE target=? AND target_id=?", (kind, row["id"]))]
    return max([best.get(i, 0.0) for i in ids] + [0.0]) + 1.0


def _name(store, eid, ents, as_of, fold) -> str:
    if eid in ents:
        return ents[eid].get("name") or eid
    st = entity_state(store, eid, as_of, fold)
    return st.get("name") or eid


def _alias_passes(store: S.Store, alias_norm: str, eid: str) -> bool:
    r = store.one("SELECT EXISTS (SELECT 1 FROM alias_seen a JOIN _snip x ON x.sid=a.snippet_id WHERE a.alias_norm=?"
                  " AND a.entity_id=? AND x.cls='pass' AND x.tok=1) AND NOT EXISTS (SELECT 1 FROM alias_seen a JOIN _snip x"
                  " ON x.sid=a.snippet_id WHERE a.alias_norm=? AND a.entity_id=? AND x.cls='denied')",
                  (alias_norm, eid, alias_norm, eid))
    return bool(r[0])


def _alias_ok(store: S.Store, alias_norm: str, origin: str, eid: str, fold: Optional[Dict[str, dict]]) -> bool:
    """An alias the request may see: an LLM alias by its alias_seen rows; for a register task, a g1 id only when its
    own events pass (_rev) and a Tracker id only when the item of the visible events carries it."""
    if origin == "llm":
        return _alias_passes(store, alias_norm, eid)
    if fold is None or origin not in ("g1", "tracker") or not store.one(
            "SELECT 1 FROM entity WHERE id=? AND type='task' AND origin='g1'", (eid,)):
        return True
    if origin == "g1":
        return bool(store.one("SELECT 1 FROM _rev WHERE norm=?", (alias_norm,)))
    it = _task_item(store, eid, fold)
    return bool(it) and S.norm_alias(ID.tracker_id(it)) == alias_norm


def visible_aliases(store: S.Store, eid: str, fold: Optional[Dict[str, dict]] = None) -> List[str]:
    return [r["alias_norm"] for r in store.q("SELECT alias_norm, origin FROM alias WHERE entity_id=? AND status<>'rejected'",
                                             (eid,)) if _alias_ok(store, r["alias_norm"], r["origin"], eid, fold)]


# ---------------------------------------------------------------- entity card

def entity_card(store: S.Store, entity_id: str, *, scope: Optional[dict] = None, as_of=None, window=None,
                exclude=()) -> Optional[dict]:
    """Claims and edges of one entity, dated; a claim whose evidence sits only on a replaced revision is in the
    timeline marked "changed later". None when the entity is not visible to the request."""
    as_of = S.utc(as_of) if as_of else None
    stale, _rep = _relabel_if_stale(store, scope, "prep")
    prepare(store, scope, as_of, exclude, stale=stale, window=window)
    fold = IG.register_fold_at(store, as_of, passing=True)
    if not store.one("SELECT 1 FROM _ent WHERE id=?", (entity_id,)):
        # a register task asked for by a joined mention's id: the request sees the mention when its events pass
        hit = next((r["entity_id"] for r in store.q("SELECT a.entity_id FROM alias a JOIN _ent v ON v.id=a.entity_id"
                                                    " WHERE a.alias_norm=? AND a.origin='g1'", (S.norm_alias(entity_id),))
                    if _alias_ok(store, S.norm_alias(entity_id), "g1", r["entity_id"], fold)), None)
        if not hit:
            return None
        entity_id = hit
    elif store.one("SELECT 1 FROM entity WHERE id=? AND type='task' AND origin='g1'", (entity_id,)) and not store.one(
            "SELECT 1 FROM _rev WHERE id=?", (entity_id,)):
        return None         # a task id known only from events the request may not see: visible only by its mention
    st = entity_state(store, entity_id, as_of, fold)
    if not st:
        return None
    ents = {entity_id: st}
    claims = [_fact(store, "claim", r, ents) for r in store.q(
        "SELECT cl.* FROM claim cl JOIN _claim x ON x.id=cl.id WHERE cl.subject_id=?", (entity_id,))]
    current = {c["id"] for c in claims}
    history = []
    for r in store.q("SELECT cl.* FROM claim cl WHERE cl.subject_id=? AND cl.id NOT IN (SELECT id FROM _claim) AND NOT EXISTS"
                     " (SELECT 1 FROM pm_override o WHERE o.target='claim' AND o.target_key=cl.nkey AND o.field='reject')",
                     (entity_id,)):           # a claim the PM rejected is not history either, at every as_of
        rows = [dict(x) for x in store.q(
            "SELECT ev.quote, sn.said_at, s.uri FROM evidence ev JOIN _snip x ON x.sid=ev.snippet_id AND x.cls='pass'"
            " JOIN snippet sn ON sn.id=ev.snippet_id JOIN revision rv ON rv.id=sn.revision_id JOIN source s"
            " ON s.id=rv.source_id WHERE ev.target='claim' AND ev.target_id=? AND (?='' OR sn.said_at<=?)"
            " AND coalesce(sn.said_at, rv.valid_from)>=? AND coalesce(sn.said_at, rv.valid_from)<=?"   # window, as prepare
            " AND NOT EXISTS (SELECT 1 FROM evidence e2 JOIN _snip y ON y.sid=e2.snippet_id WHERE e2.target='claim'"
            " AND e2.target_id=ev.target_id AND y.cls='denied')",
            (r["id"], as_of or "", as_of or "", (window or ("", ""))[0] or "", (window or ("", "9999"))[1] or "9999"))]
        if rows and r["id"] not in current:
            history.append({"kind": "claim", "id": r["id"], "predicate": r["predicate"], "value": r["value"],
                            "modality": r["modality"], "quotes": rows, "said_at": rows[0]["said_at"],
                            "note": "changed later"})
    edges = []
    for r in store.q("SELECT e.* FROM edge e JOIN _edge x ON x.id=e.id WHERE e.src_id=? OR e.dst_id=?", (entity_id, entity_id)):
        if store.one("SELECT 1 FROM _ent WHERE id=?", (r["dst_id"] if r["src_id"] == entity_id else r["src_id"],)):
            edges.append(_fact(store, "edge", r, ents))
    for s in supersedes(store, as_of):
        if entity_id in (s["old"], s["new"]) and s["new"]:
            other = s["new"] if s["old"] == entity_id else s["old"]
            if store.one("SELECT 1 FROM _ent WHERE id=?", (other,)):
                edges.append({"kind": "edge", "src": s["new"], "rel": "supersedes", "dst": s["old"], "origin": "pm",
                              "modality": "confirmation", "status": "accepted", "said_at": s["at"],
                              "first_said_at": s["at"], "last_said_at": s["at"]})
    return dict(st, aliases=visible_aliases(store, entity_id, fold), claims=claims, history=history, edges=edges)


# ---------------------------------------------------------------- global questions (S4, section 8.7)

def prepare_topics(store: S.Store, as_of: Optional[str], scope: Optional[dict] = None, stale: bool = False) -> None:
    """TEMP _ttop(id): the topics this request may see; TEMP _tsent(id, topic_id): their sentences it may see, after
    prepare(). With a scope, a topic passes only when it is shared (client_set '') or the request's own client: a
    sentence's text was written from all of its topic's facts, so a sentence citing only shared facts of another
    client's topic could still carry that client's words (the one access check here that reads a stored label; the
    topic's partition is fixed at detect, section 8.7). A stale scope (the live reader cannot relabel) passes no topic.
    The stored label can miss a client the scope names (a label outside the hinted ones, or one a relabel added), so a
    scoped request also passes no sentence of a topic whose summary input (topic_input, every fact of the request)
    has a row on another client's side for this scope (_toth: prepare()'s side rule on those rows only, not a
    `denied` class, so excluded, revoked and hidden sources stay per sentence), nor of one with no recorded input.
    A sentence passes when its topic passes; its summary was written at or before as_of; it has at least one cite and
    every cited claim or edge passes (in _claim/_edge: a passing row valid at as_of and no denied row); and no cited
    target has an ignored row (a deleted source, or one in this request's exclude) on a source the summary was written
    from (topic_source). Ignored rows are strict for sentences (a sentence cannot be split by row), and a re-summarise
    written without that source returns."""
    c = store.conn
    c.execute("DROP TABLE IF EXISTS temp._tsent")
    c.execute("DROP TABLE IF EXISTS temp._ttop")
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='topic_input'").fetchone():
        c.execute("CREATE TEMP TABLE _ttop(id INTEGER PRIMARY KEY)")
        c.execute("CREATE TEMP TABLE _tsent(id INTEGER, topic_id INTEGER)")     # a store written before S4
        return
    sp = _scope_parts(scope)
    if sp and stale:
        c.execute("CREATE TEMP TABLE _ttop(id INTEGER PRIMARY KEY)")
    elif sp:
        c.execute("CREATE TEMP TABLE _ttop AS SELECT id FROM topic WHERE client_set='' OR client_set=?", (sp[0],))
    else:
        c.execute("CREATE TEMP TABLE _ttop AS SELECT id FROM topic")
    c.execute("CREATE INDEX temp._ttop_i ON _ttop(id)")
    c.execute("DROP TABLE IF EXISTS temp._toth")
    if sp and not stale:
        # topics whose summary input has a row on another client's side for this scope: the side rule of prepare(),
        # evaluated only on the evidence of those inputs (the search path's _snip stays as it was)
        side, params = getattr(store, "_side", ("'shared'", {}))
        c.execute("CREATE TEMP TABLE _toth AS SELECT DISTINCT ti.topic_id AS id FROM topic_input ti JOIN _ttop tt"
                  " ON tt.id=ti.topic_id JOIN evidence ev ON ev.target=ti.target AND ev.target_id=ti.target_id"
                  " JOIN snippet sn ON sn.id=ev.snippet_id JOIN revision rv ON rv.id=sn.revision_id"
                  " JOIN source s ON s.id=rv.source_id WHERE ({})='other'".format(side), params)
    else:
        c.execute("CREATE TEMP TABLE _toth(id INTEGER PRIMARY KEY)")
    c.execute("CREATE TEMP TABLE _tsent AS SELECT ts.id AS id, ts.topic_id AS topic_id FROM topic_sentence ts"
              " JOIN topic t ON t.id=ts.topic_id JOIN _ttop tt ON tt.id=t.id"
              " WHERE t.summary_at IS NOT NULL AND (?1='' OR t.summary_at<=?1)"
              " AND EXISTS (SELECT 1 FROM topic_cite tc WHERE tc.sentence_id=ts.id)"
              " AND NOT EXISTS (SELECT 1 FROM topic_cite tc WHERE tc.sentence_id=ts.id AND NOT ("
              "(tc.target='claim' AND tc.target_id IN (SELECT id FROM _claim))"
              " OR (tc.target='edge' AND tc.target_id IN (SELECT id FROM _edge))))"
              " AND NOT EXISTS (SELECT 1 FROM topic_cite tc JOIN evidence ev ON ev.target=tc.target"
              " AND ev.target_id=tc.target_id JOIN _snip x ON x.sid=ev.snippet_id AND x.cls='ignored'"
              " JOIN topic_source tsrc ON tsrc.topic_id=ts.topic_id AND tsrc.source_id=x.src WHERE tc.sentence_id=ts.id)"
              + ("" if not sp else
                 " AND EXISTS (SELECT 1 FROM topic_input ti WHERE ti.topic_id=ts.topic_id)"
                 " AND ts.topic_id NOT IN (SELECT id FROM _toth)"),
              (as_of or "",))
    c.execute("CREATE INDEX temp._tsent_i ON _tsent(id)")
    if c.in_transaction:
        c.commit()


def _history(store: S.Store, eid: str, as_of: Optional[str], window) -> List[dict]:
    """Claims of an entity that are not current but have passing rows at or before as_of: "changed later" (11.2)."""
    out = []
    for r in store.q("SELECT cl.* FROM claim cl WHERE cl.subject_id=? AND cl.status='accepted'"
                     " AND cl.id NOT IN (SELECT id FROM _claim) AND NOT EXISTS (SELECT 1 FROM pm_override o"
                     " WHERE o.target='claim' AND o.target_key=cl.nkey AND o.field='reject')", (eid,)):
        rows = [dict(x) for x in store.q(
            "SELECT ev.quote, sn.said_at, sn.locator, s.uri FROM evidence ev JOIN _snip x ON x.sid=ev.snippet_id"
            " AND x.cls='pass' JOIN snippet sn ON sn.id=ev.snippet_id JOIN revision rv ON rv.id=sn.revision_id"
            " JOIN source s ON s.id=rv.source_id WHERE ev.target='claim' AND ev.target_id=? AND (?='' OR sn.said_at<=?)"
            " AND coalesce(sn.said_at, rv.valid_from)>=? AND coalesce(sn.said_at, rv.valid_from)<=?"
            " AND NOT EXISTS (SELECT 1 FROM evidence e2 JOIN _snip y ON y.sid=e2.snippet_id WHERE e2.target='claim'"
            " AND e2.target_id=ev.target_id AND y.cls='denied') ORDER BY sn.said_at, sn.id",
            (r["id"], as_of or "", as_of or "", (window or ("", ""))[0] or "", (window or ("", "9999"))[1] or "9999"))]
        if rows:
            out.append({"kind": "claim", "id": r["id"], "subject": eid, "predicate": r["predicate"], "value": r["value"],
                        "modality": r["modality"], "status": r["status"], "origin": r["origin"],
                        "quotes": [{"quote": x["quote"], "uri": x["uri"], "locator": json.loads(x["locator"]),
                                    "said_at": x["said_at"]} for x in rows],
                        "said_at": rows[0]["said_at"], "text": rows[0]["quote"], "source": rows[0]["uri"],
                        "note": "changed later"})
    return out


def ask_global(store: S.Store, query: str, *, scope: Optional[dict] = None, as_of=None, window=None, exclude=(),
               budget=None, mode: str = "prep", include_suggestions: bool = False, max_topics: int = 3,
               timeline_max: int = 40) -> dict:
    """A global question ("what about SSO over all time", section 8.7): the topics found by topic_fts over the
    sentences this request may see and by alias match of the query to visible member entities, their passing summary
    sentences, and the dated claims and edges of their members as one timeline (oldest first; claims only on a
    replaced revision marked "changed later"). Read-only, no model request; answering in prose is the caller's (G3 or
    G5), never the live path: mode="live" is refused. A topic is returned with its summary only when at least one of
    its sentences passes; its members' facts still reach the timeline (a replay older than the summary gets claims
    and timelines but no summary, section 11.2)."""
    t0 = time.monotonic()
    if mode == "live":
        raise ValueError("ask_global is not for the live path (section 11.4)")
    budget = budget or Budget()
    as_of = S.utc(as_of) if as_of else None
    stale, relabel_rep = _relabel_if_stale(store, scope, mode)
    prepare(store, scope, as_of, exclude, stale=stale, window=window)
    prepare_topics(store, as_of, scope, stale)
    fold = IG.register_fold_at(store, as_of, passing=True)
    score: Dict[int, float] = {}
    q = fts_query(query)
    if q and store.fts:
        for r in store.q("SELECT f.rowid, x.topic_id, bm25(topic_fts) AS b FROM topic_fts f JOIN _tsent x ON x.id=f.rowid"
                         " WHERE topic_fts MATCH ? ORDER BY bm25(topic_fts) LIMIT 200", (q,)):
            score[r["topic_id"]] = score.get(r["topic_id"], 0.0) - float(r["b"]) + 1.0
    elif q:
        words = [w.strip('"*') for w in q.split(" OR ")]
        for r in store.q("SELECT ts.text, x.topic_id FROM topic_sentence ts JOIN _tsent x ON x.id=ts.id"):
            n = sum(r["text"].lower().count(w) for w in words)
            if n:
                score[r["topic_id"]] = score.get(r["topic_id"], 0.0) + n
    qn = " " + S.norm_alias(" ".join(WORD.findall(query or ""))) + " "
    matched = set()
    for r in store.q("SELECT DISTINCT a.entity_id, a.alias_norm, a.origin FROM alias a JOIN _ent v ON v.id=a.entity_id"
                     " ORDER BY a.entity_id, a.alias_norm"):
        an = " " + " ".join(WORD.findall(r["alias_norm"])) + " "
        if len(an.strip()) < 3 or an not in qn or r["entity_id"] in matched:
            continue
        if _alias_ok(store, r["alias_norm"], r["origin"], r["entity_id"], fold) and entity_state(store, r["entity_id"],
                                                                                                    as_of, fold):
            matched.add(r["entity_id"])
    for eid in sorted(matched):
        for r in store.q("SELECT m.topic_id, m.weight FROM topic_member m JOIN _ttop tt ON tt.id=m.topic_id"
                         " WHERE m.entity_id=?", (eid,)):
            score[r["topic_id"]] = score.get(r["topic_id"], 0.0) + 2.0
    picked = sorted(score, key=lambda t: (-round(score[t], 9), t))[:max_topics]
    visible = {r[0] for r in store.q("SELECT id FROM _ent")}
    sup = {s["old"] for s in supersedes(store, as_of)}
    topics, members, pending = [], [], 0
    for tid in picked:
        t = store.one("SELECT * FROM topic WHERE id=?", (tid,))
        mem = [r[0] for r in store.q("SELECT entity_id FROM topic_member WHERE topic_id=? ORDER BY weight DESC, entity_id",
                                     (tid,)) if r[0] in visible]
        members += [m for m in mem if m not in members]
        if t["state"] in ("pending", "stale"):
            pending += 1
        sents = []
        for s in store.q("SELECT ts.id, ts.text FROM topic_sentence ts JOIN _tsent x ON x.id=ts.id WHERE ts.topic_id=?"
                         " ORDER BY ts.ord", (tid,)):
            cites = [(c["target"], c["target_id"]) for c in store.q("SELECT target, target_id FROM topic_cite"
                                                                     " WHERE sentence_id=? ORDER BY target, target_id",
                                                                     (s["id"],))]
            if sup and any(tgt == "claim" and (store.one("SELECT subject_id FROM claim WHERE id=?", (tid2,))
                                               or [None])[0] in sup for tgt, tid2 in cites):
                continue            # a superseded entity's claim is never returned as current (as the timeline)
            dates = [r[0] for tgt, tid2 in cites for r in store.q(
                "SELECT min(sn.said_at) FROM evidence ev JOIN _snip x ON x.sid=ev.snippet_id AND x.cls='pass' AND x.tok=1"
                " JOIN snippet sn ON sn.id=ev.snippet_id WHERE ev.target=? AND ev.target_id=?", (tgt, tid2)) if r[0]]
            sents.append({"kind": "topic", "id": s["id"], "topic": tid, "text": s["text"],
                          "said_at": min(dates) if dates else None,
                          "cites": [{"kind": a, "id": b} for a, b in cites]})
        if not sents:
            continue
        label = next((entity_state(store, m, as_of, fold).get("name") for m in mem
                      if entity_state(store, m, as_of, fold)), None)
        topics.append({"id": tid, "label": label, "summary_at": t["summary_at"], "state": t["state"],
                       "sentences": sents})
    ents = {m: entity_state(store, m, as_of, fold) for m in members}
    ents = {k: v for k, v in ents.items() if v}
    timeline, seen = [], set()
    for r in store.q("SELECT cl.* FROM claim cl JOIN _claim x ON x.id=cl.id WHERE cl.subject_id IN (SELECT value FROM"
                     " json_each(?))", (json.dumps(sorted(ents)),)):
        if r["subject_id"] in sup or (r["status"] == "suggestion" and not include_suggestions):
            continue
        timeline.append(_fact(store, "claim", r, ents))
        seen.add(("claim", r["id"]))
    for r in store.q("SELECT e.* FROM edge e JOIN _edge x ON x.id=e.id WHERE e.src_id IN (SELECT value FROM json_each(?1))"
                     " OR e.dst_id IN (SELECT value FROM json_each(?1))", (json.dumps(sorted(ents)),)):
        if r["src_id"] not in visible or r["dst_id"] not in visible or (r["status"] == "suggestion"
                                                                         and not include_suggestions):
            continue
        f = _fact(store, "edge", r, ents)
        f["why"] = "{} -> {} -> {}".format(_name(store, r["src_id"], ents, as_of, fold), r["rel"],
                                           _name(store, r["dst_id"], ents, as_of, fold))
        timeline.append(f)
    for eid in sorted(ents):
        timeline += _history(store, eid, as_of, window)
    timeline.sort(key=lambda i: (i.get("said_at") or "", i["kind"], i["id"]))
    truncated = len(timeline) > timeline_max
    return {"topics": topics, "timeline": timeline[:timeline_max], "pending_topics": pending, "truncated": truncated,
            "scope_stale": stale, "relabel": relabel_rep, "ms": round((time.monotonic() - t0) * 1000, 2)}
