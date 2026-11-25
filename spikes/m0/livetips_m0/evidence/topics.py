"""PB-52 G2 (S4): topics and their summaries (docs/specs/PB-52-g2-evidence-store.md section 8.7).

    detect(store) -> report                      deterministic communities per client label, matched to the stored
                                                  topics; a topic whose members or facts changed goes `pending`
    summarise(store, ask, ...) -> report         one request per `pending` or `stale` topic through `ask` (the worker's
                                                  gated send, or the bench's replay); validated, one row per sentence
    request_text(store, topic_id, ...) -> (user, refs, sources)
    validate(obj, refs, rules) -> (sentences, dropped)

Topics are built from accepted claims and edges (not rejected by the PM) whose evidence sits on the current revision
of an active source and on snippets of one client label or shared, never two clients: a claim or edge with a row on
another client's snippet, or any row on an excluded or revoked source (or one a pending hide or revoke in the
state-request log names), is left out of the graph and of every summary input. Communities come from weighted label
propagation (accepted edges 1.0, two entities cited by the same snippet 0.5) with a fixed seed, ties broken by id and
nothing iterated in set order, so the same store gives the same topics; a community above 40 entities is cut into
parts of at most 40 by weighted degree. A topic's member_hash covers its client label, its entities and the keys of
the facts its summary reads, so a new fact on a member re-summarises it and nothing else does (section 8.7, "only
topics whose members changed"). Summaries are derived, never evidence: no evidence row, no confirmation, no alias.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Callable, Dict, List, Optional, Tuple

from . import ingest as IG
from . import store as S

SEED = 52
CAP = 40                  # entities per topic (section 8.7)
MIN_SIZE = 2              # a lone entity is not a topic; its facts stay in search and entity_card
ITERATIONS = 30
EDGE_W = 1.0
CO_W = 0.5
INPUT_TOKENS = 6000       # the facts of one summary request (section 8.7)
MAX_SENTENCES = 12
SENTENCE_CHARS = 400
OVERLAP = 0.5             # a re-detected community keeps the stored topic it shares at least half its entities with


# ---------------------------------------------------------------- the facts topics are built from

def _denied_sources(store: S.Store) -> set:
    """Sources whose rows never enter a topic or a summary: excluded or revoked, or named by a pending hide or revoke
    in the state-request log (read as denied before it applies, section 13.1)."""
    out = {r[0] for r in store.q("SELECT id FROM source WHERE state IN ('excluded', 'revoked')")}
    stems, rev = set(), set()
    for req in store.pending_requests():
        p = req.get("payload") or {}
        if req["kind"] == "hidden_calls":
            stems |= set(IG._stems(p.get("hidden_calls") or []))
        elif req["kind"] == "revoked":
            rev |= set(IG._revoked_ids(p.get("revoked") or []))
    for s in sorted(stems):
        out |= {r[0] for r in store.q("SELECT id FROM source WHERE call_key=? AND kind IN ({})".format(
            ",".join("'{}'".format(k) for k in IG.CALL_KINDS)), (s,))}
    for x in sorted(rev):
        out |= {r[0] for r in store.q("SELECT id FROM source WHERE ext_id=?", (x,))}
    return out


def _root(store: S.Store, eid: str) -> str:
    seen = set()
    while eid and eid not in seen:
        seen.add(eid)
        r = store.one("SELECT merged_into FROM entity WHERE id=?", (eid,))
        if not r or not r[0]:
            break
        eid = r[0]
    return eid


def facts(store: S.Store) -> Dict[Tuple[str, int], dict]:
    """Every accepted, not rejected claim and edge with its usable rows: (target, id) -> {key, ends, rows: [(snippet,
    client_set, source, said_at, quote)], sets}. A fact with a denied row anywhere is left out (strict, like search)."""
    denied = _denied_sources(store)
    rejected = {(r["target"], r["target_key"]) for r in store.q("SELECT target, target_key FROM pm_override"
                                                               " WHERE field='reject'")}
    out: Dict[Tuple[str, int], dict] = {}
    for r in store.q("SELECT id, subject_id, nkey FROM claim WHERE status='accepted' ORDER BY id"):
        if ("claim", r["nkey"]) not in rejected:
            out[("claim", r["id"])] = {"key": "c|" + r["nkey"], "ends": [_root(store, r["subject_id"])], "rows": []}
    for r in store.q("SELECT id, src_id, rel, dst_id, modality FROM edge WHERE status='accepted' AND rel<>'same_as'"
                     " ORDER BY id"):
        key = S.edge_key(r["src_id"], r["rel"], r["dst_id"], r["modality"])
        a, b = _root(store, r["src_id"]), _root(store, r["dst_id"])
        if ("edge", key) not in rejected and a != b:
            out[("edge", r["id"])] = {"key": "e|" + key, "ends": [a, b], "rows": []}
    bad = set()
    for r in store.q("SELECT ev.target, ev.target_id, ev.snippet_id, ev.quote, sn.client_set, sn.said_at, rv.valid_from,"
                     " rv.superseded_at, s.id AS src, s.state FROM evidence ev JOIN snippet sn ON sn.id=ev.snippet_id"
                     " JOIN revision rv ON rv.id=sn.revision_id JOIN source s ON s.id=rv.source_id"
                     " ORDER BY ev.target, ev.target_id, sn.said_at, ev.snippet_id"):
        k = (r["target"], r["target_id"])
        if k not in out:
            continue
        if r["src"] in denied:
            bad.add(k)
            continue
        if r["state"] != "active" or r["superseded_at"] is not None:
            continue                    # deleted sources and replaced revisions are not read (absence, not denial)
        out[k]["rows"].append({"snippet": r["snippet_id"], "set": r["client_set"] or "", "source": r["src"],
                               "said_at": r["said_at"] or r["valid_from"], "quote": r["quote"]})
    for k in list(out):
        sets = {x["set"] for x in out[k]["rows"]}
        if k in bad or not out[k]["rows"] or any("|" in s for s in sets) or len(sets - {""}) > 1:
            del out[k]                  # denied somewhere, nothing current, or two clients: never in a topic
        else:
            out[k]["client"] = next(iter(sorted(sets - {""})), "")
    return out


# ---------------------------------------------------------------- communities

def _graph(fs: Dict[Tuple[str, int], dict], label: str):
    """Weighted adjacency over the facts of one partition: the label's own facts and shared ones ('' = shared only)."""
    adj: Dict[str, Dict[str, float]] = {}
    per_snip: Dict[int, set] = {}
    own: set = set()
    for k in sorted(fs):
        f = fs[k]
        if f["client"] not in ("", label):
            continue
        for e in f["ends"]:
            adj.setdefault(e, {})
            if label and f["client"] == label:
                own.add(e)
        if k[0] == "edge":
            a, b = f["ends"]
            adj[a][b] = adj[a].get(b, 0.0) + EDGE_W
            adj[b][a] = adj[b].get(a, 0.0) + EDGE_W
        for row in f["rows"]:
            per_snip.setdefault(row["snippet"], set()).update(f["ends"])
    for sid in sorted(per_snip):
        ents = sorted(per_snip[sid])
        for i, a in enumerate(ents):
            for b in ents[i + 1:]:
                adj[a][b] = adj[a].get(b, 0.0) + CO_W
                adj[b][a] = adj[b].get(a, 0.0) + CO_W
    return adj, own


def communities(adj: Dict[str, Dict[str, float]], seed: int = SEED) -> List[List[str]]:
    """Label propagation: each node takes the label its neighbours weigh most (ties: the smallest label), in an order
    shuffled by a fixed seed, until nothing changes. Groups above CAP are cut by weighted degree inside the group."""
    order = sorted(adj)
    lab = {n: n for n in order}
    rng = random.Random(seed)
    for _ in range(ITERATIONS):
        seq = order[:]
        rng.shuffle(seq)
        changed = False
        for n in seq:
            if not adj[n]:
                continue
            score: Dict[str, float] = {}
            for m in sorted(adj[n]):
                score[lab[m]] = score.get(lab[m], 0.0) + adj[n][m]
            best = min(score.items(), key=lambda kv: (-round(kv[1], 9), kv[0]))[0]
            if best != lab[n]:
                lab[n] = best
                changed = True
        if not changed:
            break
    groups: Dict[str, List[str]] = {}
    for n in order:
        groups.setdefault(lab[n], []).append(n)
    out = []
    for g in sorted(groups.values(), key=lambda g: (g[0], len(g))):
        if len(g) <= CAP:
            out.append(sorted(g))
            continue
        inside = set(g)
        deg = {n: sum(w for m, w in adj[n].items() if m in inside) for n in g}
        ranked = sorted(g, key=lambda n: (-round(deg[n], 9), n))
        out += [sorted(ranked[i:i + CAP]) for i in range(0, len(ranked), CAP)]
    return out


def _topic_facts(fs, members: List[str], label: str) -> List[Tuple[str, int]]:
    ms = set(members)
    return [k for k in sorted(fs) if fs[k]["client"] in ("", label) and
            (fs[k]["ends"][0] in ms if k[0] == "claim" else (fs[k]["ends"][0] in ms or fs[k]["ends"][1] in ms))]


def member_hash(label: str, members: List[str], keys: List[str]) -> str:
    return hashlib.sha256(json.dumps([label, sorted(members), sorted(keys)], ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def detect(store: S.Store, seed: int = SEED) -> dict:
    """Topics of the store now, written in one transaction. A community with the member_hash of a stored topic keeps it
    (and its summary); one that shares at least half its entities with a stored topic of the same label takes that row
    over as `pending` (its old sentences stay, filtered per sentence, until the re-summarise); a new one is `pending`;
    a stored topic matched by nothing is deleted with its sentences."""
    fs = facts(store)
    labels = sorted({f["client"] for f in fs.values() if f["client"]})
    found = []                                      # (label, members, keys, weights)
    for label in labels + [""]:
        adj, own = _graph(fs, label)
        for g in communities(adj, seed):
            if len(g) < MIN_SIZE or (label and not (set(g) & own)):
                continue
            ks = _topic_facts(fs, g, label)
            if label and not any(fs[k]["client"] == label for k in ks):
                continue                            # all shared: the shared partition has it
            w = {n: round(sum(adj[n].values()), 6) for n in g}
            found.append((label, g, sorted(fs[k]["key"] for k in ks), w))
    rep = {"topics": 0, "kept": 0, "changed": 0, "new": 0, "deleted": 0}
    with store.conn:
        old = [dict(r) for r in store.q("SELECT id, client_set, member_hash, state FROM topic ORDER BY id")]
        old_members = {r["id"]: {x[0] for x in store.q("SELECT entity_id FROM topic_member WHERE topic_id=?", (r["id"],))}
                       for r in old}
        by_hash = {r["member_hash"]: r for r in old}
        used = set()
        plan = []
        for label, g, keys, w in found:
            h = member_hash(label, g, keys)
            if h in by_hash and by_hash[h]["id"] not in used:
                used.add(by_hash[h]["id"])
                plan.append((by_hash[h]["id"], label, g, h, w, "kept"))
            else:
                plan.append((None, label, g, h, w, None))
        for i, (tid, label, g, h, w, how) in enumerate(plan):
            if tid is not None:
                continue
            best, score = None, 0.0
            for r in old:
                if r["id"] in used or r["client_set"] != label:
                    continue
                a, b = set(g), old_members[r["id"]]
                j = len(a & b) / float(len(a | b) or 1)
                if j >= OVERLAP and (j > score or (j == score and best is not None and r["id"] < best)):
                    best, score = r["id"], j
            if best is not None:
                used.add(best)
                plan[i] = (best, label, g, h, w, "changed")
            else:
                plan[i] = (None, label, g, h, w, "new")
        for r in old:
            if r["id"] not in used:
                drop_sentences(store, r["id"])
                store.conn.execute("DELETE FROM topic WHERE id=?", (r["id"],))
                rep["deleted"] += 1
        for tid, label, g, h, w, how in plan:
            name = _label(store, g, w)
            if how == "new":
                tid = store.conn.execute("INSERT INTO topic(label, client_set, member_hash, state) VALUES (?,?,?,"
                                         " 'pending')", (name, label, h)).lastrowid
            elif how == "changed":
                store.conn.execute("UPDATE topic SET label=?, member_hash=?, state='pending' WHERE id=?", (name, h, tid))
            else:
                store.conn.execute("UPDATE topic SET label=? WHERE id=?", (name, tid))
            store.conn.execute("DELETE FROM topic_member WHERE topic_id=?", (tid,))
            store.conn.executemany("INSERT INTO topic_member(topic_id, entity_id, weight) VALUES (?,?,?)",
                                   [(tid, e, w[e]) for e in g])
            rep[how] += 1
            rep["topics"] += 1
    return rep


def _label(store: S.Store, members: List[str], w: Dict[str, float]) -> str:
    top = sorted(members, key=lambda n: (-w.get(n, 0.0), n))[0]
    r = store.one("SELECT name FROM entity WHERE id=?", (top,))
    return (r[0] if r else top)[:120]


def drop_sentences(store: S.Store, topic_id: int) -> None:
    """A topic's sentences, their cites and topic_fts rows (external content: the old text is deleted explicitly)."""
    if store.fts:
        for r in store.q("SELECT id, text FROM topic_sentence WHERE topic_id=?", (topic_id,)):
            store.conn.execute("INSERT INTO topic_fts(topic_fts, rowid, text) VALUES ('delete', ?, ?)", (r["id"], r["text"]))
    store.conn.execute("DELETE FROM topic_sentence WHERE topic_id=?", (topic_id,))
    store.conn.execute("DELETE FROM topic_source WHERE topic_id=?", (topic_id,))
    store.conn.execute("DELETE FROM topic_input WHERE topic_id=?", (topic_id,))


# ---------------------------------------------------------------- the summary request

def _name(store: S.Store, eid: str) -> str:
    r = store.one("SELECT name FROM entity WHERE id=?", (eid,))
    return r[0] if r else eid


def request_text(store: S.Store, topic_id: int, fs: Optional[dict] = None, tokens: Optional[Callable[[str], int]] = None,
                 limit: int = INPUT_TOKENS) -> Tuple[str, Dict[str, Tuple[str, int]], List[int]]:
    """The user text of one summary request: the topic's facts oldest first, each with a short ref (f1, f2, ...), its
    date, modality and one quote, up to `limit` tokens. Refs are ordinal over content (date, key), never row ids, so
    the same facts give the same text (the bench keys its fixture by it). Returns the text, ref -> (target, id) and
    the sources whose rows the text quotes (topic_source)."""
    fs = facts(store) if fs is None else fs
    t = store.one("SELECT * FROM topic WHERE id=?", (topic_id,))
    members = [r[0] for r in store.q("SELECT entity_id FROM topic_member WHERE topic_id=? ORDER BY entity_id", (topic_id,))]
    keys = _topic_facts(fs, members, t["client_set"])
    keys.sort(key=lambda k: (fs[k]["rows"][0]["said_at"] or "", fs[k]["key"]))
    head = ["Topic: {}".format(t["label"]), "Client: {}".format(t["client_set"] or "shared"), "",
            "Facts, oldest first (ref | date | fact | modality | quote):"]
    tok = tokens or (lambda s: len(s) // 4)
    used = tok("\n".join(head))
    lines, refs, sources = [], {}, set()
    for k in keys:
        f = fs[k]
        row = f["rows"][0]
        if k[0] == "claim":
            c = store.one("SELECT predicate, value, modality FROM claim WHERE id=?", (k[1],))
            what = "{}: {} = {}".format(_name(store, f["ends"][0]), c["predicate"], c["value"] or "")
            mod = c["modality"]
        else:
            e = store.one("SELECT rel, modality FROM edge WHERE id=?", (k[1],))
            what = "{} -> {} -> {}".format(_name(store, f["ends"][0]), e["rel"], _name(store, f["ends"][1]))
            mod = e["modality"]
        ref = "f{}".format(len(lines) + 1)
        line = "{} | {} | {} | {} | \"{}\"".format(ref, (row["said_at"] or "")[:10], what, mod,
                                                   " ".join((row["quote"] or "").split()))
        n = tok(line)
        if lines and used + n > limit:
            break
        used += n
        lines.append(line)
        refs[ref] = k
        sources |= {x["source"] for x in f["rows"]}
    return "\n".join(head + lines), refs, sorted(sources)


# ---------------------------------------------------------------- the answer

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse(text: str) -> Tuple[Optional[dict], Optional[str]]:
    s = FENCE.sub("", (text or "").strip())
    try:
        obj, _end = json.JSONDecoder().raw_decode(s)
    except ValueError as e:
        return None, "not valid JSON: {}".format(str(e)[:120])
    if not isinstance(obj, dict) or not isinstance(obj.get("sentences"), list):
        return None, "the answer must be a JSON object with a list `sentences`"
    return obj, None


def validate(obj: dict, refs: Dict[str, Tuple[str, int]], max_sentences: int = MAX_SENTENCES) -> Tuple[List[dict], int]:
    """Section 8.7: every sentence cites at least one fact of the request; unknown refs are removed and a sentence left
    with none is dropped, as is one with no text. Text is collapsed and cut at SENTENCE_CHARS; at most max_sentences."""
    out, dropped = [], 0
    for s in obj.get("sentences") or []:
        if not isinstance(s, dict):
            dropped += 1
            continue
        text = " ".join(str(s.get("text") or "").split())[:SENTENCE_CHARS]
        cites = s.get("cites") if isinstance(s.get("cites"), list) else []
        keep = []
        for c in cites:
            k = refs.get(str(c).strip())
            if k and k not in keep:
                keep.append(k)
        if not text or not keep or len(out) >= max_sentences:
            dropped += 1
            continue
        out.append({"text": text, "cites": keep})
    return out, dropped


def write(store: S.Store, topic_id: int, sentences: List[dict], sources: List[int], at: Optional[str] = None,
          run_id: Optional[int] = None, inputs: Optional[List[Tuple[str, int]]] = None) -> None:
    """Replace the topic's summary: one row per sentence, its cites, topic_fts, the sources it was written from and
    every fact of the request (topic_input, cited or not: the access check of a scoped request reads it)."""
    with store.conn:
        drop_sentences(store, topic_id)
        for i, s in enumerate(sentences):
            sid = store.conn.execute("INSERT INTO topic_sentence(topic_id, ord, text) VALUES (?,?,?)",
                                     (topic_id, i, s["text"])).lastrowid
            store.conn.executemany("INSERT OR IGNORE INTO topic_cite(sentence_id, target, target_id) VALUES (?,?,?)",
                                   [(sid, t, int(x)) for t, x in s["cites"]])
            if store.fts:
                store.conn.execute("INSERT INTO topic_fts(rowid, text) VALUES (?,?)", (sid, s["text"]))
        store.conn.executemany("INSERT OR IGNORE INTO topic_source(topic_id, source_id) VALUES (?,?)",
                               [(topic_id, x) for x in sources])
        store.conn.executemany("INSERT OR IGNORE INTO topic_input(topic_id, target, target_id) VALUES (?,?,?)",
                               [(topic_id, t, int(x)) for t, x in (inputs or [])])
        store.conn.execute("UPDATE topic SET summary_at=?, summary_run=?, state=? WHERE id=?",
                           (at or S.now_utc(), run_id, "ready" if sentences else "empty", topic_id))


def due(store: S.Store) -> List[int]:
    """Topics to summarise: new or changed (`pending`) and those a cited source's state change marked `stale`."""
    return [r[0] for r in store.q("SELECT id FROM topic WHERE state IN ('pending', 'stale') ORDER BY id")]


class Skip(Exception):
    """Raised by `ask` for a route error: the topic stays as it is (pending or stale) for the next pass."""


def summarise(store: S.Store, ask: Callable[[str, int], dict], rules: dict, *, at: Optional[str] = None,
              run_id: Optional[int] = None, tokens: Optional[Callable[[str], int]] = None,
              before_each: Optional[Callable[[], None]] = None, only: Optional[List[int]] = None) -> dict:
    """One request per due topic through `ask(user, max_tokens) -> answer` (the caller's gate, send and system
    prefix). A parse failure or a cut answer gets one repair retry; a second marks the topic `invalid` (re-tried
    when its members change). `ask` may raise (the worker's ceiling or call pause): topics done so far stay written."""
    cap = int(rules.get("summary_output_tokens") or 800)
    limit = int((rules.get("summary") or {}).get("input_tokens") or INPUT_TOKENS)
    most = int((rules.get("summary") or {}).get("max_sentences") or MAX_SENTENCES)
    rep = {"summarised": 0, "invalid": 0, "failed": 0, "sentences": 0, "dropped": 0, "requests": 0}
    fs = None
    for tid in (only if only is not None else due(store)):
        if before_each and before_each():
            fs = None                   # a state request applied: the facts are read again before this topic
        fs = facts(store) if fs is None else fs
        if not store.one("SELECT 1 FROM topic WHERE id=?", (tid,)):
            continue
        user, refs, sources = request_text(store, tid, fs, tokens, limit)
        if not refs:
            write(store, tid, [], [], at, run_id)
            continue
        try:
            ans = ask(user, cap)
            rep["requests"] += 1
            obj, err = (None, "cut at the output limit") if _cut(ans) else parse(ans.get("text") or "")
            if obj is None:
                ans = ask(user + "\n\nYour previous answer was not valid: {}. Return the JSON only, at most {} short"
                                 " sentences.".format(err, max(3, most // 2)), cap)
                rep["requests"] += 1
                obj, err = (None, "cut") if _cut(ans) else parse(ans.get("text") or "")
        except Skip:
            rep["failed"] += 1
            continue
        if obj is None:
            with store.conn:
                store.conn.execute("UPDATE topic SET state='invalid' WHERE id=?", (tid,))
            rep["invalid"] += 1
            continue
        sents, dropped = validate(obj, refs, most)
        write(store, tid, sents, sources, at, run_id, inputs=list(refs.values()))
        rep["summarised"] += 1
        rep["sentences"] += len(sents)
        rep["dropped"] += dropped
    return rep


def _cut(ans: dict) -> bool:
    if ans.get("incomplete") == "max_output_tokens" or str(ans.get("stop_reason") or "") in (
            "max_tokens", "MAX_TOKENS", "length", "response.incomplete"):
        return True
    return False


def prefix(rules: dict) -> str:
    """The system prefix of every summary request (identical on each, so the providers' prompt cache applies)."""
    s = rules.get("summary") or {}
    lines = [str(s.get("prompt") or "").rstrip(), "", "Output JSON schema:",
             json.dumps(rules.get("summary_schema") or {}, sort_keys=True, separators=(",", ":")), "", "Example (invented):"]
    for ex in s.get("examples") or []:
        lines += ["Facts:", ex["facts"].rstrip(), "Output:", ex["output"], ""]
    return "\n".join(lines).rstrip() + "\n"
