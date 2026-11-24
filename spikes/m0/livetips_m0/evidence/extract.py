"""PB-52 G2 (S3): the extraction contract in code (spec sections 8.3, 8.4 and 8.6). Pure: no model, network or embed
request here; the worker (worker.py) sends what this module builds and gives the answers back to it.

    load_rules(rules_dir) -> dict             rules/evidence.yaml with the schema and the compiled hedge patterns
    extractor_id(rules) -> str                "<rules_version>:<sha256 of the prompt prefix>:10"
    prefix(rules) -> str                      the fixed, cacheable part of every request
    segment_view(store, seg) -> dict          the segment's snippets with real file line numbers, its context snippet
    neutral_scope(root, label, labels)        the scope a catalog is chosen with (section 8.3)
    catalog(store, view, root, ...) -> list   at most 60 known entities visible to the segment's client label
    request_text(rules, view, catalog, meta)  the variable part
    parse(text) -> (obj, error)               JSON with the schema's shape, or an error for the repair retry
    cut(answer) -> bool                       the reply ended at the output cap (stop reason, or the JSON ends early)
    validate(store, view, obj, rules, ...)    items that pass section 8.4, each with its snippet, plus the counts
    merge(parts) -> obj                       the outputs of split halves as one answer (refs renamed, no collisions)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml
from jsonschema import Draft202012Validator

from .. import client_scope as CS
from . import ingest as IG
from . import search as SE
from . import store as S

RULES_DIR = Path(__file__).resolve().parents[4] / "rules"
SHARED = "\x00shared"         # the client of the neutral scope of a shared segment: a label no folder has


# ---------------------------------------------------------------- rules

def load_rules(rules_dir: Optional[Path] = None) -> dict:
    d = Path(rules_dir or RULES_DIR)
    r = yaml.safe_load((d / "evidence.yaml").read_text(encoding="utf-8"))
    r["schema"] = json.loads((d / "evidence_schema.json").read_text(encoding="utf-8"))
    r["_hedge"] = [re.compile(p, re.I | re.U) for p in r.get("hedge_anywhere") or []]
    item_checks(r)
    return r


LISTS = ("entities", "claims", "relations")


def item_checks(rules: dict) -> Dict[str, Draft202012Validator]:
    """One validator per top-level list, built from the item schema in rules/evidence_schema.json (section 8.4 item 1),
    so the code and the schema cannot drift. Cached on the rules dict."""
    v = rules.get("_items")
    if v is None:
        props = (rules.get("schema") or {}).get("properties") or {}
        v = {k: Draft202012Validator(props[k]["items"]) for k in LISTS if (props.get(k) or {}).get("items")}
        rules["_items"] = v
    return v


def item_error(rules: dict, key: str, item) -> Optional[str]:
    """The first schema error of one item (missing key, wrong type, lines not two integers), or None."""
    v = item_checks(rules).get(key)
    if v is None:
        return None if isinstance(item, dict) else "not an object"
    err = next(iter(v.iter_errors(item)), None)
    return err.message[:120] if err is not None else None


def prefix(rules: dict) -> str:
    """Instructions, the type, relation and predicate lists, the modality table, the schema and two invented
    examples: identical on every request, so the providers' prompt cache applies (section 9)."""
    lines = [rules["prompt"].rstrip(), "",
             "Entity types: " + ", ".join(rules["types"]),
             "Relations: " + ", ".join(rules["relations"]),
             "Claim predicates: " + ", ".join(rules["predicates"]), "", "Modality:"]
    lines += ["- {}: {}".format(k, v) for k, v in rules["modality"].items()]
    lines += ["", "Output JSON schema:", json.dumps(rules["schema"], sort_keys=True, separators=(",", ":")), "",
              "Examples (invented):"]
    for ex in rules.get("examples") or []:
        lines += ["Text:", ex["text"], "Output:", ex["output"], ""]
    return "\n".join(lines).rstrip() + "\n"


def extractor_id(rules: dict) -> str:
    return "{}:{}".format(rules.get("rules_version", "g2-s3"), hashlib.sha256(prefix(rules).encode("utf-8")).hexdigest()[:10])


# ---------------------------------------------------------------- the segment as the model sees it

def _lines_of(row) -> Tuple[int, int]:
    try:
        a, b = json.loads(row["locator"]).get("lines") or [0, 0]
        return int(a), int(b)
    except (ValueError, TypeError):
        return 0, 0


def segment_view(store: S.Store, revision_id: int, seg: dict) -> dict:
    """The segment's own snippets (id, text, file lines, said_at, speaker) in order, the snippet before it (context,
    never quoted from), the source's kind, origin, uri, title and date."""
    ids = list(seg["snippet_ids"])
    rows = {r["id"]: r for r in store.q("SELECT id, ord, text, locator, heading, speaker, said_at FROM snippet WHERE id IN"
                                        " ({})".format(",".join("?" * len(ids))), ids)}
    snips = []
    for sid in ids:
        r = rows[sid]
        a, b = _lines_of(r)
        snips.append({"id": sid, "ord": r["ord"], "text": r["text"], "lines": (a, b), "heading": r["heading"] or "",
                      "said_at": r["said_at"], "speaker": r["speaker"]})
    first = min(s["ord"] for s in snips)
    ctx = store.one("SELECT text, locator, client_set FROM snippet WHERE revision_id=? AND ord=?", (revision_id, first - 1))
    if ctx and (ctx["client_set"] or "") != (seg.get("label") or ""):
        ctx = None                      # the context snippet goes only with a segment of the same client label
    src = store.one("SELECT s.kind, s.origin, s.uri, s.title, rv.valid_from FROM revision rv JOIN source s"
                    " ON s.id=rv.source_id WHERE rv.id=?", (revision_id,))
    return {"revision_id": revision_id, "seg_hash": seg["seg_hash"], "label": seg.get("label") or "",
            "heading": seg.get("heading") or "", "snippets": snips,
            "context": ctx["text"] if ctx else "", "context_lines": _lines_of(ctx) if ctx else (0, 0),
            "kind": src["kind"], "origin": src["origin"], "uri": src["uri"], "title": src["title"] or "",
            "date": src["valid_from"]}


def numbered(text: str, first: int) -> str:
    return "\n".join("{} {}".format(first + i, ln) for i, ln in enumerate(text.split("\n")))


def split_view(view: dict) -> Tuple[dict, dict]:
    """Two halves at a snippet boundary (section 8.6); each half keeps the parent's label and meta, the second half's
    context is the first half's last snippet."""
    sn = view["snippets"]
    k = max(1, len(sn) // 2)
    a = dict(view, snippets=sn[:k])
    b = dict(view, snippets=sn[k:], context=sn[k - 1]["text"], context_lines=sn[k - 1]["lines"])
    return a, b


# ---------------------------------------------------------------- the catalog (section 8.3)

def project_labels(root: str) -> List[str]:
    return CS.labels(root) if root and os.path.isdir(root) else []


def neutral_scope(root: str, label: str, labels: Iterable[str]) -> Optional[dict]:
    """The scope a segment's catalog is chosen with: client = its label, others = every other label of labels(root).
    A shared segment (no label) gets a client no folder has, so every client-labelled entity is 'other'. None when
    no other label exists (a one-client project: nothing to keep apart)."""
    labels = sorted(set(labels))
    client = label or SHARED
    others = [x for x in labels if x != label]
    if not others:
        return None
    folders: Dict[str, List[str]] = {}
    if root and os.path.isdir(root):
        for g in CS._client_dirs(os.path.realpath(root), set(labels)).values():
            for lb, rel in g.items():
                folders.setdefault(lb, []).append(rel)
    return {"client": client, "others": others, "folders": folders, "root": os.path.realpath(root or "."),
            "why": "evidence catalog"}


def _visible(store: S.Store, scope: Optional[dict]) -> Dict[str, Tuple[str, List[str]]]:
    """{entity id: (visible name, visible aliases)} for one scope (search.prepare's _ent and the alias rule of section
    11.2). The name is the one search shows this scope (search.entity_state with the scope's register fold): a
    register task joined with another client's mention is named from the visible mention, never by the stored name."""
    SE.prepare(store, scope, None)
    fold = SE.IG.register_fold_at(store, None, passing=True)
    out = {}
    for r in store.q("SELECT en.id FROM entity en JOIN _ent x ON x.id=en.id WHERE en.merged_into IS NULL"):
        st = SE.entity_state(store, r["id"], None, fold)
        if not st or not str(st.get("name") or "").strip() or st.get("id") != r["id"]:
            continue                            # nothing visible names it, or the scope sees it only by another id
        out[r["id"]] = (str(st["name"]), SE.visible_aliases(store, r["id"], fold))
    return out


def catalog(store: S.Store, view: dict, root: str = "", labels: Optional[List[str]] = None,
            limit: int = 60) -> List[dict]:
    """At most `limit` known entities for one segment, only from entities and aliases visible to the segment's own
    label (a segment labelled with two clients sees what both may see), chosen by name and alias match on the
    segment, then by shared words."""
    labels = project_labels(root) if labels is None else labels
    parts = [x for x in (view["label"] or "").split("|")] or [""]
    vis: Optional[Dict[str, Tuple[str, List[str]]]] = None
    for lb in parts:
        v = _visible(store, neutral_scope(root, lb, labels))
        # a two-client segment: an entity both may see, the aliases both may see, a name both are shown alike
        vis = v if vis is None else {k: (nm, [a for a in al if a in v[k][1]]) for k, (nm, al) in vis.items()
                                     if k in v and v[k][0] == nm}
    vis = vis or {}
    seg = S._fold(" ".join(s["text"] for s in view["snippets"]))
    words = {w for w in re.findall(r"\w{4,}", seg)}
    scored = []
    for eid, (name, aliases) in vis.items():
        e = store.one("SELECT id, type FROM entity WHERE id=?", (eid,))
        names = [S._fold(name)] + list(aliases)
        hits = sum(seg.count(n) for n in names if n)
        shared = len(words & set(re.findall(r"\w{4,}", " ".join(names))))
        if hits or shared:
            scored.append((-hits, -shared, eid, e, name, aliases))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return [{"id": e["id"], "type": e["type"], "name": nm,
             "aliases": [a for a in al if a != S._fold(nm)][:5]} for _h, _s, _i, e, nm, al in scored[:limit]]


def request_text(view: dict, cat: List[dict], clients: List[str]) -> str:
    """The variable part: source meta, the project's clients, the catalog, the context and the numbered segment."""
    meta = {"kind": view["kind"], "origin": view["origin"], "date": view["date"], "title": view["title"] or view["uri"],
            "client": view["label"] or "shared"}
    out = ["Source: " + json.dumps(meta, ensure_ascii=False),
           "Clients of the project: " + (", ".join(clients) or "none"),
           "Known entities (use their id as known_id): " + json.dumps(cat, ensure_ascii=False, separators=(",", ":"))]
    if view["context"]:
        out += ["", "Context before the part (do not quote from it):", view["context"]]
    out += ["", "The part, with line numbers:"]
    for s in view["snippets"]:
        out.append(numbered(s["text"], s["lines"][0] or 1))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the answer

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse(text: str, schema: dict) -> Tuple[Optional[dict], Optional[str]]:
    """The answer as a dict of the schema's shape (top-level lists of objects with their required keys), or the
    error the repair retry sends back."""
    s = FENCE.sub("", (text or "").strip())
    try:
        obj, _end = json.JSONDecoder().raw_decode(s)
    except ValueError as e:
        return None, "not valid JSON: {}".format(str(e)[:120])
    if not isinstance(obj, dict):
        return None, "the answer is not a JSON object"
    for key in schema.get("required") or ():
        if key not in obj:
            return None, "the key {} is missing (use an empty list when there is nothing)".format(key)
        if not isinstance(obj[key], list):
            return None, "{} must be a list".format(key)
    return obj, None


def cut(answer: dict) -> bool:
    """The reply ended at the output cap: the provider's stop reason says so, or the JSON ends early."""
    if answer.get("incomplete") == "max_output_tokens" or str(answer.get("stop_reason") or "") in (
            "max_tokens", "MAX_TOKENS", "length", "response.incomplete"):
        return True
    s = FENCE.sub("", (answer.get("text") or "").strip())
    if not s.startswith("{"):
        return False
    try:
        json.JSONDecoder().raw_decode(s)
        return False
    except ValueError:
        return s.count("{") > s.count("}")          # an object never closed: cut, not malformed


def merge(parts: List[dict]) -> dict:
    """Split halves as one answer: every half's refs get a prefix, so two halves' n1 stay two entities."""
    out = {"entities": [], "claims": [], "relations": []}
    for i, p in enumerate(parts):
        tag = "p{}.".format(i)
        refs = {str(e.get("ref")) for e in p.get("entities") or [] if e.get("ref")}
        rn = lambda x: tag + str(x) if str(x) in refs else x   # noqa: E731
        for e in p.get("entities") or []:
            out["entities"].append(dict(e, ref=rn(e.get("ref"))))
        for c in p.get("claims") or []:
            out["claims"].append(dict(c, subject=rn(c.get("subject")), said_by=rn(c.get("said_by")) if c.get("said_by") else None))
        for r in p.get("relations") or []:
            out["relations"].append(dict(r, **{"from": rn(r.get("from")), "to": rn(r.get("to"))}))
    return out


# ---------------------------------------------------------------- validation (section 8.4)

def _quote_in(view: dict, quote: str, lines) -> Optional[Tuple[dict, List[int]]]:
    """The segment snippet that holds the quote (whitespace collapse and case fold) with its file lines covering
    `lines`, and the file lines the quote itself sits on (stored with the item, so a cached item re-validates on a
    snippet longer than 60 lines). Only the segment's own snippets: never the context snippet, never the rest of the
    revision."""
    if not quote or len(quote) > 300 * 2:
        return None
    try:
        a, b = int(lines[0]), int(lines[1])
    except (TypeError, ValueError, IndexError):
        return None
    if a > b:
        return None
    for s in view["snippets"]:
        sa, sb = s["lines"]
        hit = S._find_quote(s["text"], quote)
        if not hit:
            continue
        # the file lines the quote sits on inside this snippet
        qa = sa + s["text"][:hit[0]].count("\n")
        qb = sa + s["text"][:hit[1]].count("\n")
        if a <= qa and qb <= b and b - a <= 60:   # the range covers the quote (section 8.4 item 3)
            # a snippet with no file lines (locator without "lines") keeps its own (0, 0): the store locates by it
            return s, ([qa, qb] if sa else list(s["lines"]))
    return None


def _overrides(store: S.Store) -> dict:
    rows = store.q("SELECT target, target_key, field FROM pm_override")
    return {"rejected": {(r["target"], r["target_key"]) for r in rows if r["field"] == "reject"},
            "fields": {(r["target_key"], r["field"]) for r in rows if r["field"] not in ("reject", "supersede")}}


def validate(store: S.Store, view: dict, obj: dict, rules: dict, cat: List[dict], root: str = "",
             labels: Optional[List[str]] = None) -> Tuple[dict, dict]:
    """Items that pass section 8.4 in its order, each failure dropping the item (never the segment). Returns the
    items for store.apply_extraction (lines set to the file lines the quote sits on, inside the snippet that holds it,
    so the write path finds the same snippet) and the counts. An item that does not match its item schema in
    rules/evidence_schema.json is dropped as "shape".
    Counts: {"kept", "dropped", "downgraded", "suggested", "reasons": {...}}."""
    labels = project_labels(root) if labels is None else labels
    types, rels, preds = set(rules["types"]), set(rules["relations"]), set(rules["predicates"])
    mods = set(rules["modality"])
    caps = rules.get("caps") or {}
    known_ids = {c["id"] for c in cat}
    label_set = {x for x in (view["label"] or "").split("|") if x}
    # the file's own client: a client folder on its path wins over the line labels (a line naming Globex inside
    # docs/acme/ is still Acme's file); a shared file's segment owns the clients its label names
    own = (IG.path_marks(view["uri"]) & set(labels)) if not view["uri"].startswith("/") else set()
    allowed = own or label_set
    ov = _overrides(store)
    counts = {"kept": 0, "dropped": 0, "downgraded": 0, "suggested": 0, "reasons": {}}

    def drop(why: str) -> None:
        counts["dropped"] += 1
        counts["reasons"][why] = counts["reasons"].get(why, 0) + 1

    def client_ok(quote: str) -> bool:
        """Item 6: a quote text_side labels as another client than the snippet's label."""
        names = CS._names(quote) & set(labels)
        return not names or bool(names & allowed)         # text_side: own when it names the file's client

    def two_clients(quote: str) -> bool:
        return len(CS._names(quote) & set(labels)) > 1

    def at_snippet(item: dict) -> Optional[List[int]]:
        hit = _quote_in(view, str(item.get("quote") or ""), item.get("lines"))
        if not hit:
            drop("quote")
            return None
        if not client_ok(str(item.get("quote"))):
            drop("client")
            return None
        return hit[1]

    def bad_shape(key: str, item) -> bool:
        if item_error(rules, key, item) is None:
            return False
        drop("shape")
        return True

    def modality(item: dict) -> Optional[str]:
        m = str(item.get("modality") or "")
        if m not in mods:
            return None
        q = str(item.get("quote") or "")
        if m == "confirmation" and view["origin"] == "derived":
            counts["downgraded"] += 1
            m = "statement"                       # item 9: derived never confirms (section 7.4)
        if m == "confirmation" and any(rx.search(q) for rx in rules["_hedge"]):
            counts["downgraded"] += 1
            m = "proposal"                        # item 9: a hedge word anywhere in the quote
        return m

    out = {"entities": [], "claims": [], "relations": []}
    names: Dict[str, List[str]] = {}            # ref -> folded name and aliases (item 5)
    resolved: Dict[str, str] = {}               # ref -> entity id
    for e in (obj.get("entities") or []):
        if bad_shape("entities", e):
            continue
        if not e["name"].strip() or e["type"] not in types:
            drop("enum" if e["type"] not in types else "shape")
            continue
        if len(out["entities"]) >= int(caps.get("entities", 40)):
            drop("cap")
            continue
        sn = at_snippet(e)
        if not sn:
            continue
        kid = e.get("known_id")
        if kid and kid not in known_ids:
            kid = None                            # item 4: an id not in the catalog sent is ignored
        eid = kid or S.entity_llm_id(str(e["type"]), str(e["name"]))
        ref = str(e.get("ref") or eid)
        resolved[ref] = eid
        cat_aliases = next((c["aliases"] + [S._fold(c["name"])] for c in cat if c["id"] == kid), [])
        names[ref] = [S._fold(x) for x in [e["name"]] + list(e.get("aliases") or [])] + cat_aliases
        out["entities"].append({"ref": ref, "known_id": kid, "type": e["type"], "name": str(e["name"]).strip()[:200],
                                "aliases": [str(a)[:120] for a in (e.get("aliases") or [])][:8],
                                "quote": str(e["quote"])[:300], "lines": sn})
        counts["kept"] += 1

    def ref_ok(x) -> bool:
        return str(x or "") in resolved or str(x or "") in known_ids

    def eid_of(x) -> str:
        return resolved.get(str(x), str(x))

    for c in (obj.get("claims") or []):
        if bad_shape("claims", c):
            continue
        if not ref_ok(c.get("subject")):
            drop("ref")
            continue
        if c.get("predicate") not in preds:
            drop("enum")
            continue
        m = modality(c)
        if m is None:
            drop("enum")
            continue
        if len(out["claims"]) >= int(caps.get("claims", 60)):
            drop("cap")
            continue
        sn = at_snippet(c)
        if not sn:
            continue
        subj = eid_of(c["subject"])
        nkey = S.claim_nkey(subj, str(c["predicate"]), m, c.get("value"))
        if ("claim", nkey) in ov["rejected"] or (subj, str(c["predicate"])) in ov["fields"]:
            drop("override")                       # item 8
            continue
        item = {"subject": c["subject"], "predicate": c["predicate"], "value": c.get("value"), "modality": m,
                "said_by": c.get("said_by") if ref_ok(c.get("said_by")) else None, "due": c.get("due"),
                "quote": str(c["quote"])[:300], "lines": sn, "confidence": c.get("confidence")}
        if two_clients(item["quote"]):
            item["suggestion"] = True              # section 7.5
            counts["suggested"] += 1
        out["claims"].append(item)
        counts["kept"] += 1

    for r in (obj.get("relations") or []):
        if bad_shape("relations", r):
            continue
        if not ref_ok(r.get("from")) or not ref_ok(r.get("to")):
            drop("ref")
            continue
        if r.get("rel") not in rels:
            drop("enum")
            continue
        m = modality(r)
        if m is None:
            drop("enum")
            continue
        if len(out["relations"]) >= int(caps.get("relations", 60)):
            drop("cap")
            continue
        sn = at_snippet(r)
        if not sn:
            continue
        a, b = eid_of(r["from"]), eid_of(r["to"])
        mod = S.FIXED_MODALITY.get(str(r["rel"]), m)
        key_prefix = "{}|{}|{}|".format(a, r["rel"], b)
        if ("edge", key_prefix + mod) in ov["rejected"] or (r["rel"] == "same_as" and any(
                t == "edge" and (k.startswith(key_prefix) or k.startswith("{}|same_as|{}|".format(b, a)))
                for t, k in ov["rejected"])):
            drop("override")                       # item 8: a pair the PM rejected is never re-suggested
            continue
        item = {"from": r["from"], "rel": r["rel"], "to": r["to"], "modality": m, "quote": str(r["quote"])[:300],
                "lines": sn, "confidence": r.get("confidence")}
        q = S._fold(item["quote"])

        def named(ref) -> bool:
            ns = names.get(str(ref)) or next(([S._fold(c["name"])] + c["aliases"] for c in cat if c["id"] == str(ref)), [])
            return any(w in q for n in ns for w in re.findall(r"\w{3,}", n))
        if a == b or not named(r["from"]) or not named(r["to"]) or two_clients(item["quote"]):
            item["suggestion"] = True              # item 5 and section 7.5
            counts["suggested"] += 1
        out["relations"].append(item)
        counts["kept"] += 1
    return out, counts
