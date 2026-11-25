"""PB-52 G2 (S1): the evidence store CLI, JSON on stdout, one interface for the app and the skill (spec section 11.6).

    python -m livetips_m0.evidence <command> --project-uid <uid> [--store <file>] --root <project folder> ...

    status    the store's counts, the applied request ids (--request-id asks about one)
    ingest    deterministic pass: --hidden-calls, --revoked, --web-dirs, --tracker, --valid-from (tests and the bench
              only), --quiet-seconds, --no-embed
    pass      ingest, then LLM extraction of the pending revisions on the evidence route (S3): --route, --model,
              --settings-json (ceilings, poll_s, local_overflow: go on with the model on this Mac at a ceiling,
              off by default), --no-extract (the switch "Use AI to read sources" off)
    estimate  the pending segments and the cost of extracting them on --route, typical to cap
    exclude   the state-only pass: --hidden-calls and/or --revoked (full current lists), --request-id
    override  one pm_override row: --target --key --field --value --at --via, --request-id (any field is stored;
              in S1 search applies only reject and supersede)
    search    --query, --as-of, --exclude, --mode, --k, scope from --scope-json or --title/--companies (decide)
    card      --entity
    rebuild   derived tables again from the files; pm_override rows and source states are kept
    vacuum    purge revoked sources and superseded snippets nobody cites, then facts left without evidence
    delete    the store file, its WAL, lock, log and snapshots

Refuses without --project-uid or --store (never derives a key from a path) and when the flag evidence_store is off.
`exclude` and `override` wait at most 2 seconds for the project lock; while a pass holds it they append the request
to the state-request log, which every writer applies first and every reader treats as denied while pending.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from .. import client_scope as CS
from . import enabled
from . import ingest as IG
from . import search as SE
from . import store as S


def _list(values) -> list:
    out = []
    for v in values or []:
        v = v.strip()
        if v.startswith("["):
            try:
                out += [str(x) for x in json.loads(v)]
                continue
            except ValueError:
                pass
        if v.startswith("@") and os.path.exists(v[1:]):
            out += _list([Path(v[1:]).read_text(encoding="utf-8")])
            continue
        out += [x.strip() for x in v.replace("\n", ",").split(",") if x.strip()]
    return out


def _store(a, create: bool = True, readonly: bool = False) -> S.Store:
    if not a.project_uid and not a.store:
        raise S.Refused("--project-uid or --store is required")
    path = a.store or S.store_path(a.project_uid)
    if not create and not Path(path).exists():
        raise S.Refused("no store yet")
    return S.Store.open(path=path, uid=a.project_uid or "", root=a.root or "", slug=a.slug or "", readonly=readonly)


def _scope(a, root: str):
    if a.scope_json:
        sc = json.loads(Path(a.scope_json[1:]).read_text() if a.scope_json.startswith("@") else a.scope_json)
        return sc or None
    if a.title or a.companies:
        return CS.decide(root, a.title or "", _list(a.companies))
    return None


def cmd_status(a) -> dict:
    st = _store(a, create=False, readonly=True)
    c = lambda sql: st.one(sql)[0]
    out = {"ok": True, "uid": st.meta("evidence_uid"), "schema": st.meta("schema_version"), "fts5": st.fts,
           "sources": {r[0]: r[1] for r in st.q("SELECT state, count(*) FROM source GROUP BY state")},
           "revisions": c("SELECT count(*) FROM revision"), "snippets": c("SELECT count(*) FROM snippet"),
           "entities": c("SELECT count(*) FROM entity"), "claims": c("SELECT count(*) FROM claim"),
           "edges": c("SELECT count(*) FROM edge"), "pending_extraction": c("SELECT count(*) FROM revision WHERE"
                                                                             " extract_state='pending'"),
           "label_universe": st.meta_json("label_universe", []), "last_pass": st.meta("last_pass"),
           # S3: segments a model answer could not fill (invalid twice, or one snippet still cut at the output cap)
           "segments_invalid": c("SELECT count(DISTINCT seg_hash) FROM run_item WHERE status='invalid'"),
           "segments_truncated": c("SELECT count(DISTINCT seg_hash) FROM run_item WHERE error LIKE 'truncated%'"),
           "last_extract": dict(st.one("SELECT id, status, route, model, usd, pct_before, pct_after, seconds, error FROM run"
                                       " WHERE kind='extract' ORDER BY id DESC LIMIT 1") or {}),
           "applied_requests": sorted(st.applied_ids()), "pending_requests": [r["request_id"] for r in st.pending_requests()]}
    if a.request_id:
        out["request"] = {"id": a.request_id, "applied": a.request_id in st.applied_ids()}
    if not st.fts:
        out["note"] = "FTS5 missing: search falls back to a word scan"
    return out


def cmd_ingest(a) -> dict:
    st = _store(a)
    wl = st.worker_lock(0.0)
    if not wl:
        return {"ok": False, "status": "busy", "why": "another pass is running"}
    with S.held(wl):
        pl = st.project_lock(2.0)
        if not pl:
            return {"ok": False, "status": "busy", "why": "the project is locked"}
        with S.held(pl):
            run = st.conn.execute("INSERT INTO run(kind, started_at, status) VALUES ('ingest', ?, 'running')",
                                  (S.now_utc(),)).lastrowid
            st.conn.commit()
            st.conn.execute("UPDATE run SET status='failed', error='killed' WHERE status='running' AND id<>?", (run,))
            st.conn.commit()
            rep = IG.ingest(st, a.root, hidden_calls=_list(a.hidden_calls) if a.hidden_calls is not None else None,
                            revoked=_list(a.revoked) if a.revoked is not None else None, web_dirs=_list(a.web_dirs),
                            valid_from=S.utc(a.valid_from) if a.valid_from else None,
                            embedder=None if a.no_embed else IG.LoopbackEmbedder(), tracker_path=a.tracker,
                            quiet_s=float(a.quiet_seconds), slug=a.slug or "")
            with st.conn:
                st.conn.execute("UPDATE run SET status='done', ended_at=? WHERE id=?", (S.now_utc(), run))
            ext = None
            if a.cmd == "pass" and not a.no_extract:
                from . import worker as W
                s = dict(W.app_settings(), **_settings(a))
                # "Use AI to read sources" off in the app (LT_EVIDENCE_LIMITS extract: false): like --no-extract, no
                # run row and no request; an unreadable setting still runs the worker, which ends the pass failed
                if s.get("extract") is not False or s.get("limits_error"):
                    ext = W.extract_pass(st, a.root, _settings(a))
            st.compact_log()                    # under both locks (section 13.1)
    out = {"ok": True, "status": "done", "report": rep}
    if ext is not None:
        out.update(extract=ext, status=ext["status"])
    return out


def _settings(a) -> dict:
    s = {}
    if a.settings_json:
        raw = Path(a.settings_json[1:]).read_text(encoding="utf-8") if a.settings_json.startswith("@") else a.settings_json
        s = json.loads(raw)
    if a.route:
        s["route"] = a.route
    if a.model:
        s["model"] = a.model
    return s


def cmd_estimate(a) -> dict:
    from . import worker as W
    st = _store(a, create=False, readonly=True)
    s = _settings(a)
    return dict(W.estimate(st, a.root or st.meta("root_hint"), s.get("route") or "api", settings=s), ok=True)


def _state_request(a, kind: str, payload: dict) -> dict:
    st = _store(a)
    rid = a.request_id or str(uuid.uuid4())
    pl = st.project_lock(2.0)
    if not pl:
        st.append_request(kind, payload, rid)
        return {"ok": True, "status": "queued", "request_id": rid}
    with S.held(pl):
        IG.apply_pending(st)
        if kind == "override" and a.root:
            IG.ingest_sidecars(st, a.root)
        applied = IG.apply_request(st, {"request_id": rid, "kind": kind, "payload": payload, "at": S.now_utc()})
    return {"ok": True, "status": "applied" if applied else "already applied", "request_id": rid}


def cmd_exclude(a) -> dict:
    out = []
    if a.hidden_calls is not None:
        out.append(_state_request(a, "hidden_calls", {"hidden_calls": _list(a.hidden_calls)}))
    if a.revoked is not None:
        if out:
            a.request_id = (a.request_id or "") and a.request_id + "-revoked"
        out.append(_state_request(a, "revoked", {"revoked": _list(a.revoked)}))
    if not out:
        return {"ok": False, "error": "exclude needs --hidden-calls and/or --revoked"}
    return out[0] if len(out) == 1 else {"ok": True, "requests": out}


def cmd_override(a) -> dict:
    if not (a.key and a.field):
        return {"ok": False, "error": "override needs --key and --field"}
    payload = {"target": a.target or "entity", "key": a.key, "field": a.field, "value": a.value,
               "at": S.utc(a.at) if a.at else S.now_utc(), "via": a.via or "app"}
    return _state_request(a, "override", payload)


def cmd_search(a) -> dict:
    st = _store(a, create=False, readonly=a.mode == "live")
    root = a.root or st.meta("root_hint")
    res = SE.search(st, a.query or "", scope=_scope(a, root), as_of=a.as_of, exclude=_list(a.exclude), mode=a.mode,
                    budget=SE.Budget(k=a.k, ms=150 if a.mode == "live" else 1000),
                    include_suggestions=a.include_suggestions)
    res.pop("candidates", None)
    return dict(res, ok=True)


def cmd_card(a) -> dict:
    st = _store(a, create=False)
    root = a.root or st.meta("root_hint")
    card = SE.entity_card(st, a.entity or "", scope=_scope(a, root), as_of=a.as_of, exclude=_list(a.exclude))
    return {"ok": card is not None, "card": card}


DERIVED = ("evidence", "alias_seen", "alias", "claim", "edge", "entity_mark", "entity_rev", "entity_source", "entity", "snippet_mark",
           "snippet", "revision", "topic_cite", "topic_sentence", "topic_member", "topic", "run_item")


def rebuild(st: S.Store, root: str, **kw) -> dict:
    """Derived tables again from the files (free, spec section 13.4). pm_override rows the PM wrote (not via the
    register), state_request rows and the source rows stay, so a hidden or revoked source keeps its state for the
    whole rebuild and is never searchable or embedded in between."""
    with st.conn:
        for t in DERIVED:
            st.conn.execute("DELETE FROM {}".format(t))
        st.conn.execute("DELETE FROM pm_override WHERE via='register'")
        if st.fts:
            st.conn.execute("INSERT INTO snippet_fts(snippet_fts) VALUES ('rebuild')")
        st.set_meta("label_universe", "[]")
    return IG.ingest(st, root, **kw)


def vacuum(st: S.Store) -> dict:
    """Purge revoked sources and superseded snippets nobody cites, then, in the same transaction, every claim and
    edge left with no evidence and every llm alias with no alias_seen row (spec sections 6 and 13.4). A claim or edge
    a revoked source quotes goes first, with all its evidence, even when another file quotes it too (spec 13.4)."""
    with st.conn:
        c = st.conn
        c.execute("DROP TABLE IF EXISTS _gone")
        c.execute("CREATE TEMP TABLE _gone AS SELECT DISTINCT ev.target, ev.target_id FROM evidence ev"
                  " JOIN snippet sn ON sn.id=ev.snippet_id JOIN revision r ON r.id=sn.revision_id"
                  " JOIN source s ON s.id=r.source_id WHERE s.state='revoked'")
        n_cl = c.execute("DELETE FROM claim WHERE id IN (SELECT target_id FROM _gone WHERE target='claim')").rowcount
        n_ed = c.execute("DELETE FROM edge WHERE id IN (SELECT target_id FROM _gone WHERE target='edge')").rowcount
        for t in ("evidence", "topic_cite"):
            c.execute("DELETE FROM {} WHERE EXISTS (SELECT 1 FROM _gone g WHERE g.target={}.target AND"
                      " g.target_id={}.target_id)".format(t, t, t))
        c.execute("DROP TABLE _gone")
        n_rev = c.execute("DELETE FROM revision WHERE source_id IN (SELECT id FROM source WHERE state='revoked')").rowcount
        n_sn = c.execute("DELETE FROM snippet WHERE revision_id IN (SELECT id FROM revision WHERE superseded_at IS NOT"
                         " NULL) AND id NOT IN (SELECT snippet_id FROM evidence)").rowcount
        n_cl += c.execute("DELETE FROM claim WHERE id NOT IN (SELECT target_id FROM evidence WHERE target='claim')").rowcount
        n_ed += c.execute("DELETE FROM edge WHERE id NOT IN (SELECT target_id FROM evidence WHERE target='edge')").rowcount
        st.conn.execute("DELETE FROM alias WHERE origin='llm' AND NOT EXISTS (SELECT 1 FROM alias_seen s WHERE"
                        " s.alias_norm=alias.alias_norm AND s.entity_id=alias.entity_id)")
        if st.fts:
            st.conn.execute("INSERT INTO snippet_fts(snippet_fts) VALUES ('rebuild')")
    st.conn.execute("VACUUM")
    return {"revisions": n_rev, "snippets": n_sn, "claims": n_cl, "edges": n_ed}


def cmd_rebuild(a) -> dict:
    st = _store(a)
    pl = st.project_lock(2.0)
    if not pl:
        return {"ok": False, "status": "busy"}
    with S.held(pl):
        IG.apply_pending(st)
        rep = rebuild(st, a.root, embedder=None if a.no_embed else IG.LoopbackEmbedder(), quiet_s=float(a.quiet_seconds))
    return {"ok": True, "report": rep}


def cmd_vacuum(a) -> dict:
    st = _store(a, create=False)
    pl = st.project_lock(2.0)
    if not pl:
        return {"ok": False, "status": "busy"}
    with S.held(pl):
        IG.apply_pending(st)            # a revoke still in the request log is purged by this vacuum, not the next
        return {"ok": True, "report": vacuum(st)}


def cmd_delete(a) -> dict:
    if not a.project_uid and not a.store:
        raise S.Refused("--project-uid or --store is required")
    path = Path(a.store or S.store_path(a.project_uid))
    gone = []
    for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"), path.with_suffix(".lock"),
              path.with_suffix(".state-requests.jsonl"), path.with_suffix(".state-requests.lock")):
        if p.exists():
            p.unlink()
            gone.append(p.name)
    snaps = path.parent / "snapshots" / path.stem
    if snaps.is_dir():
        shutil.rmtree(snaps)
        gone.append("snapshots/" + path.stem)
    return {"ok": True, "deleted": gone}


COMMANDS = {"status": cmd_status, "ingest": cmd_ingest, "pass": cmd_ingest, "exclude": cmd_exclude,
            "override": cmd_override, "search": cmd_search, "card": cmd_card, "rebuild": cmd_rebuild,
            "vacuum": cmd_vacuum, "delete": cmd_delete, "estimate": cmd_estimate}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m livetips_m0.evidence")
    ap.add_argument("cmd", choices=sorted(COMMANDS))
    ap.add_argument("--project-uid", default="")
    ap.add_argument("--store", default="")
    ap.add_argument("--root", default="")
    ap.add_argument("--slug", default="")
    ap.add_argument("--config", default="")
    ap.add_argument("--hidden-calls", action="append")
    ap.add_argument("--revoked", action="append")
    ap.add_argument("--web-dirs", action="append")
    ap.add_argument("--tracker", default=None)
    ap.add_argument("--valid-from", default="")
    ap.add_argument("--quiet-seconds", default="600")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--no-extract", action="store_true")
    ap.add_argument("--route", default="", choices=("", "api", "code", "local", "openai", "codex", "gemini"))
    ap.add_argument("--model", default="")
    ap.add_argument("--settings-json", default="")
    ap.add_argument("--request-id", default="")
    ap.add_argument("--target", default="entity")
    ap.add_argument("--key", default="")
    ap.add_argument("--field", default="", help="stored as given; in S1 search applies only reject and supersede")
    ap.add_argument("--value", default=None)
    ap.add_argument("--at", default="")
    ap.add_argument("--via", default="app")
    ap.add_argument("--query", default="")
    ap.add_argument("--entity", default="")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--exclude", action="append")
    ap.add_argument("--mode", default="prep", choices=("prep", "live", "after"))
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--include-suggestions", action="store_true")
    ap.add_argument("--scope-json", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--companies", action="append")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    if not enabled(a.config or None):
        print(json.dumps({"ok": False, "error": "evidence_store is off"}))
        return 2
    try:
        out = COMMANDS[a.cmd](a)
    except S.Refused as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
