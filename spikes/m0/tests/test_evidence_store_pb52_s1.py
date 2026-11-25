"""PB-52 G2 S1: the evidence store, deterministic ingest, search with access and time, the CLI.

One test (or a small group) per acceptance line of docs/specs/PB-52-g2-evidence-store.md section 17, S1. Every claim
and edge of a file comes from store.apply_extraction with hand-written items (no model, no segment cache). Invented
data only (Mike, Sarah, David, Emily; Acme, Globex, Initech)."""
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from livetips_m0 import call_layout as CL
from livetips_m0 import client_scope as CS
from livetips_m0 import commitments as CM
from livetips_m0 import identity as ID
from livetips_m0.evidence import __main__ as CLI
from livetips_m0.evidence import ingest as IG
from livetips_m0.evidence import search as SE
from livetips_m0.evidence import store as S
from livetips_m0.sheet import lookup as LK

M0 = Path(__file__).absolute().parents[1]
G0 = M0 / "bench" / "g0" / "fixtures" / "northpoint"
OLD = time.time() - 30 * 86400
GID = "1AbCdEfGhIjKlMnOpQrStUv"            # an invented Google file id (the copy keeps its first 16 characters)
NID = "0123456789abcdef0123456789abcdef"   # an invented Notion page id


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_SUPPORT_DIR", str(tmp_path / "support"))
    monkeypatch.setenv("LT_EVIDENCE", "1")
    monkeypatch.delenv("LT_AI_STATE", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    return root


def put(root, rel, text, mtime=None):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    t = OLD if mtime is None else mtime
    os.utime(p, (t, t))
    return p


def ts(iso):
    return S._dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def open_store(name="p-1", root=None):
    return S.Store.open(uid=name, root=str(root or ""))


def run(st, root, **kw):
    kw.setdefault("quiet_s", 0)
    kw.setdefault("voices", {})
    return IG.ingest(st, str(root), **kw)


def rev_of(st, uri, active=True):
    q = "SELECT rv.* FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri=?"
    q += " AND rv.superseded_at IS NULL" if active else " ORDER BY rv.id"
    rows = st.q(q, (uri,))
    return rows[0] if active else rows


def texts(res, kind=None):
    out = []
    for i in res["items"]:
        if kind and i["kind"] != kind:
            continue
        out.append(i.get("text") or "")
        out += [q["quote"] for q in i.get("quotes", [])]
    return "\n".join(out)


def passing(st, scope=None, uri=None, as_of=None, exclude=()):
    """The text of every snippet that passes access and time for the request (search ranks only these)."""
    SE.prepare(st, scope, S.utc(as_of) if as_of else None, exclude)
    q = ("SELECT sn.text FROM snippet sn JOIN _snip x ON x.sid=sn.id JOIN revision rv ON rv.id=sn.revision_id JOIN source s"
         " ON s.id=rv.source_id WHERE x.cls='pass' AND x.tok=1" + (" AND s.uri=?" if uri else "") + " ORDER BY s.uri, sn.ord")
    return "\n".join(r[0] for r in st.q(q, (uri,) if uri else ()))


def two_clients(root):
    """docs/acme and docs/globex with a Globex call series, so Acme and Globex are both client folders."""
    put(root, "docs/acme/plan.md", "# Acme plan\n\nAcme pilot starts in January.\n")
    put(root, "docs/globex/plan.md", "# Globex plan\n\nGlobex wants the invoice export by Friday.\n")
    put(root, "live/2026-11-04-globex-weekly/2026-11-04-1100-globex-weekly.md",
        "# Live transcript\n\n- Start: 2026-11-04 11:00:00\n\n[11:00:10] Sarah (en): Globex budget is 120 hours.\n")
    put(root, "live/2026-11-03-acme-weekly-status/2026-11-03-1000-acme-weekly-status.md",
        "# Live transcript\n\n- Start: 2026-11-03 10:00:00\n\n[10:00:10] Mike (en): Acme needs SSO first.\n")


def acme(root, companies=("Acme",)):
    sc = CS.decide(str(root), "Acme weekly status", list(companies))
    assert sc and sc["client"] == "acme" and "globex" in sc["others"]
    return sc


# ---------------------------------------------------------------- where the store lives, the flag, the CLI refusals

def test_store_only_under_support_and_nothing_under_the_project(tmp_path, monkeypatch):
    root = tmp_path / "northpoint"
    shutil.copytree(G0, root)
    support = tmp_path / "support"
    monkeypatch.setenv("LT_SUPPORT_DIR", str(support))
    monkeypatch.delenv("LT_AI_STATE", raising=False)

    def snap():
        return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime) for p in root.rglob("*"))

    before = snap()
    monkeypatch.delenv("LT_EVIDENCE", raising=False)
    assert CLI.main(["ingest", "--project-uid", "np-1", "--root", str(root), "--no-embed"]) == 2   # flag off
    assert not support.exists()
    monkeypatch.setenv("LT_EVIDENCE", "1")
    assert CLI.main(["ingest", "--root", str(root), "--no-embed"]) == 2                             # no uid
    assert CLI.main(["ingest", "--project-uid", "np-1", "--root", str(root), "--no-embed", "--quiet-seconds", "0"]) == 0
    assert (support / "evidence" / "np-1.sqlite").exists()
    assert snap() == before
    with pytest.raises(S.Refused):
        S.Store.open(path=root / "x.sqlite", root=str(root))


def test_flag_from_config(tmp_path, monkeypatch):
    from livetips_m0 import evidence as EV
    monkeypatch.delenv("LT_EVIDENCE", raising=False)
    assert not EV.enabled() and not EV.enabled({"features": {}})
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"features": {"evidence_store": True}}))
    assert EV.enabled(str(cfg))


# ---------------------------------------------------------------- revisions, touch, delete, register refold

def test_one_revision_per_change_none_on_touch_deleted_absent(env):
    root = env
    put(root, "docs/plan.md", "# Plan\n\nThe zephyr budget is 40 hours.\n")
    st = open_store(root=root)
    run(st, root)
    assert len(rev_of(st, "docs/plan.md", active=False)) == 1
    put(root, "docs/plan.md", "# Plan\n\nThe zephyr budget is 48 hours.\n", mtime=OLD + 100)
    run(st, root)
    assert len(rev_of(st, "docs/plan.md", active=False)) == 2
    os.utime(root / "docs/plan.md", (OLD + 500, OLD + 500))
    rep = run(st, root)
    assert len(rev_of(st, "docs/plan.md", active=False)) == 2 and rep["touched"] == 1
    assert "48 hours" in texts(SE.search(st, "zephyr budget"))
    (root / "docs/plan.md").unlink()
    run(st, root)
    assert st.one("SELECT state FROM source WHERE uri='docs/plan.md'")[0] == "deleted"
    assert SE.search(st, "zephyr budget")["items"] == []


def test_register_rollback_refolds(env):
    root = env
    a = {"v": 1, "op": "add", "id": "c-aaaaaaaaaa", "text": "send the pilot timeline to Mike", "owner": "me",
         "series": "|acmeweeklystatus", "call": "2026-11-03-1000-acme-weekly-status.md", "created": "2026-11-03T10:00:00+00:00"}
    b = dict(a, id="c-bbbbbbbbbb", call="2026-11-10-1000-acme-weekly-status.md", created="2026-11-10T10:00:00+00:00")
    same = {"v": 1, "op": "same", "id": "c-bbbbbbbbbb", "to": "c-aaaaaaaaaa", "mig": "g1-x", "at": "2026-11-10T11:00:00+00:00"}
    lines = [json.dumps(x) for x in (a, b, same)]
    put(root, "live/commitments.jsonl", "\n".join(lines) + "\n")
    st = open_store(root=root)
    run(st, root)
    assert st.one("SELECT 1 FROM entity WHERE id='c-bbbbbbbbbb'") is None
    assert st.one("SELECT entity_id FROM alias WHERE alias_norm='c-bbbbbbbbbb'")[0] == "c-aaaaaaaaaa"
    put(root, "live/commitments.jsonl", "\n".join(lines + [json.dumps({"v": 1, "op": "rollback", "id": "x", "tag": "g1-x",
                                                                         "at": "2026-11-11T10:00:00+00:00"})]) + "\n")
    run(st, root)
    assert st.one("SELECT 1 FROM entity WHERE id='c-bbbbbbbbbb'") is not None       # the rolled back join is undone


# ---------------------------------------------------------------- deleted is ignored, revoked is denied

def _claim_on(st, uri, quote, subject="e-sso", value="blocked", modality="statement", rel=None, dst=None):
    rid = rev_of(st, uri)["id"]
    if not st.one("SELECT 1 FROM entity WHERE id=?", (subject,)):
        with st.conn:
            st.conn.execute("INSERT INTO entity(id, type, name, origin, updated_at) VALUES (?, 'system', ?, 'llm', ?)",
                            (subject, subject[2:], S.now_utc()))
    items = {"entities": [], "claims": [], "relations": []}
    if rel:
        if not st.one("SELECT 1 FROM entity WHERE id=?", (dst,)):
            with st.conn:
                st.conn.execute("INSERT INTO entity(id, type, name, origin, updated_at) VALUES (?, 'system', ?, 'llm', ?)",
                                (dst, dst[2:], S.now_utc()))
        items["relations"].append({"from": subject, "rel": rel, "to": dst, "modality": modality, "quote": quote,
                                   "confidence": 0.9})
    else:
        items["claims"].append({"subject": subject, "predicate": "status", "value": value, "modality": modality,
                                "quote": quote, "confidence": 0.9})
    return S.apply_extraction(st, rid, items)


def _claims(st, q="sso", **kw):
    return [i for i in SE.search(st, q, **kw)["items"] if i["kind"] == "claim"]


def test_deleted_is_ignored_not_denied_revoked_is_denied(env):
    root = env
    put(root, "docs/a.md", "# A\n\nThe SSO integration is blocked by item 13.\n", mtime=ts("2026-11-10T09:00:00Z"))
    put(root, "docs/b.md", "# B\n\nNote: the SSO integration is blocked by item 13.\n", mtime=ts("2026-11-12T09:00:00Z"))
    put(root, "live/google/google-file-{}.md".format(GID[:16]), "# G\n\nAgain the SSO integration is blocked by item 13.\n",
        mtime=ts("2026-11-08T09:00:00Z"))
    st = open_store(root=root)
    run(st, root)
    for uri in ("docs/a.md", "docs/b.md", "live/google/google-file-{}.md".format(GID[:16])):
        _claim_on(st, uri, "the SSO integration is blocked by item 13")
    assert len(_claims(st)) == 1 and len(_claims(st)[0]["quotes"]) == 3
    (root / "docs/a.md").unlink()
    run(st, root)
    c = _claims(st)
    assert len(c) == 1 and {q["uri"] for q in c[0]["quotes"]} == {"docs/b.md", "live/google/google-file-{}.md".format(GID[:16])}
    assert c[0]["said_at"] == "2026-11-08T09:00:00Z"      # dated by a passing row
    IG.apply_state(st, revoked=[GID])
    assert _claims(st) == []                              # one revoked row denies the claim (strict)
    copy = root / "live/google/google-file-{}.md".format(GID[:16])
    copy.rename(root / "copy.keep")
    run(st, root)                                         # the revoked copy leaves the disk: still denied, not deleted
    assert st.one("SELECT state FROM source WHERE uri=?", ("live/google/google-file-{}.md".format(GID[:16]),))[0] == "revoked"
    assert _claims(st) == []
    (root / "copy.keep").rename(copy)
    run(st, root)
    IG.apply_state(st, revoked=[])
    assert len(_claims(st)) == 1


# ---------------------------------------------------------------- move

class StubEmbed:
    def __init__(self):
        self.requests, self.texts = 0, []

    def __call__(self, texts):
        self.requests += 1
        self.texts += list(texts)
        return [[0.5] * 4 for _ in texts]


def test_move_keeps_the_source_and_costs_nothing(env):
    root = env
    live = root / "live"
    put(root, "live/2026-11-03-1000-acme-weekly-status.md",
        "# Live transcript\n\n- Start: 2026-11-03 10:00:00\n\n[10:00:10] Mike (en): The SSO integration waits for item 13.\n")
    put(root, "live/2026-11-03-1000-acme-weekly-status.notes.md", "# Notes\n\n### TL;DR\nThe SSO integration waits for item 13.\n")
    emb = StubEmbed()
    st = open_store(root=root)
    run(st, root, embedder=emb)
    uri0 = "live/2026-11-03-1000-acme-weekly-status.notes.md"
    sid = st.one("SELECT id FROM source WHERE uri=?", (uri0,))[0]
    _claim_on(st, uri0, "The SSO integration waits for item 13")
    IG.write_override(st, {"target": "claim", "key": st.one("SELECT nkey FROM claim")[0], "field": "confirm", "at": "2026-11-04"})
    revs = st.one("SELECT count(*) FROM revision")[0]
    before = emb.requests
    rep = CL.migrate(live, now=time.time())
    assert rep["moved"]
    assert len(_claims(st)) == 1                          # between the move and the next pass nothing is lost
    out = run(st, root, embedder=emb)
    uri1 = "live/2026-11-03-acme-weekly-status/2026-11-03-1000-acme-weekly-status.notes.md"
    assert st.one("SELECT id FROM source WHERE uri=?", (uri1,))[0] == sid and out["moved"] == 2
    assert st.one("SELECT count(*) FROM revision")[0] == revs and emb.requests == before
    c = _claims(st)
    assert len(c) == 1 and c[0]["quotes"][0]["uri"] == uri1
    assert st.one("SELECT count(*) FROM pm_override WHERE via='app'")[0] == 1
    # two identical new files for one vanished path: deleted plus two new
    put(root, "docs/x.md", "# X\n\nSame text.\n")
    run(st, root)
    (root / "docs/x.md").unlink()
    put(root, "docs/y.md", "# X\n\nSame text.\n")
    put(root, "docs/z.md", "# X\n\nSame text.\n")
    run(st, root)
    assert st.one("SELECT state FROM source WHERE uri='docs/x.md'")[0] == "deleted"
    assert st.one("SELECT count(*) FROM source WHERE uri IN ('docs/y.md','docs/z.md')")[0] == 2


# ---------------------------------------------------------------- revert

def test_revert_three_revisions_each_period_and_vectors_copied(env):
    root = env
    A, B = "# Budget\n\nThe quartz budget is 120 hours.\n", "# Budget\n\nThe quartz budget is 150 hours.\n"
    emb = StubEmbed()
    st = open_store(root=root)
    put(root, "docs/budget.md", A, mtime=OLD)
    run(st, root, embedder=emb, valid_from="2026-11-12T09:00:00Z")
    put(root, "docs/budget.md", B, mtime=OLD + 10)
    run(st, root, embedder=emb, valid_from="2026-11-19T09:00:00Z")
    n = emb.requests
    embedded = len(emb.texts)
    put(root, "docs/budget.md", A, mtime=OLD + 20)
    rep = run(st, root, embedder=emb, valid_from="2026-11-21T09:00:00Z")
    revs = rev_of(st, "docs/budget.md", active=False)
    assert len(revs) == 3 and revs[0]["valid_to"] == "2026-11-19T09:00:00Z" and revs[1]["valid_to"] == "2026-11-21T09:00:00Z"
    assert emb.requests == n and len(emb.texts) == embedded and rep["vectors_copied"] >= 1
    assert st.one("SELECT count(*) FROM snippet WHERE revision_id=? AND vec IS NULL", (revs[2]["id"],))[0] == 0
    for as_of, want, not_want in (("2026-11-17T00:00:00Z", "120", "150"), ("2026-11-20T00:00:00Z", "150", "120"),
                                  (None, "120", "150")):
        t = texts(SE.search(st, "quartz budget", as_of=as_of))
        assert want in t and not_want not in t


# ---------------------------------------------------------------- content time

def test_content_time_rules(env):
    root = env
    st = open_store(root=root)
    # (1) notes: revision 1 at Nov 12 16:10, revision 2 at Nov 15 09:00; call X Nov 12 15:00, call Y Nov 14 15:00
    notes = "live/2026-11-12-acme-sync/2026-11-12-1500-acme-sync.notes.md"
    put(root, notes, "# Notes\n\n### Staffing\nThe team has 4 engineers.\n\n### Other\nNothing else.\n",
        mtime=ts("2026-11-12T16:10:00Z"))
    run(st, root)
    put(root, notes, "# Notes\n\n### Staffing\nThe team has 6 engineers.\n\n### Other\nNothing else.\n",
        mtime=ts("2026-11-15T09:00:00Z"))
    run(st, root)
    y = texts(SE.search(st, "team engineers", as_of="2026-11-14T15:00:00Z"))
    assert "4 engineers" in y and "6 engineers" not in y
    x = texts(SE.search(st, "team engineers", as_of="2026-11-12T15:00:00Z"))
    assert "engineers" not in x
    # (5) an unchanged notes snippet keeps its said_at; the new one gets revision 2's valid_from
    r2 = rev_of(st, notes)
    said = {r["text"]: r["said_at"] for r in st.q("SELECT text, said_at FROM snippet WHERE revision_id=?", (r2["id"],))}
    assert said["### Other\nNothing else."] == "2026-11-12T16:10:00Z"
    assert said["### Staffing\nThe team has 6 engineers."] == "2026-11-15T09:00:00Z"
    # (2) the same day: revision 1 at 08:30, revision 2 at 16:45, replay as of 12:00
    put(root, "docs/pilot.md", "# Pilot\n\nThe pilot is in January.\n", mtime=ts("2026-11-19T08:30:00Z"))
    run(st, root)
    put(root, "docs/pilot.md", "# Pilot\n\nThe pilot is in March.\n", mtime=ts("2026-11-19T16:45:00Z"))
    run(st, root)
    t = texts(SE.search(st, "pilot", as_of="2026-11-19T12:00:00Z"))
    assert "January" in t and "March" not in t
    # (3) a revision 2 whose mtime is older than revision 1's valid_from gets observed_at; revision 1 stays non-empty
    put(root, "docs/old.md", "# Old\n\nVersion one.\n", mtime=ts("2026-11-10T10:00:00Z"))
    run(st, root)
    put(root, "docs/old.md", "# Old\n\nVersion two.\n", mtime=ts("2026-11-01T10:00:00Z"))
    run(st, root)
    r1, r2 = rev_of(st, "docs/old.md", active=False)
    assert r2["valid_from"] == r2["observed_at"] and r1["valid_from"] < r1["valid_to"]
    # (4) a mtime after observed_at is cut to it
    put(root, "docs/future.md", "# F\n\nOne.\n", mtime=ts("2026-11-10T10:00:00Z"))
    run(st, root)
    put(root, "docs/future.md", "# F\n\nTwo.\n", mtime=time.time() + 86400 * 30)
    run(st, root)
    r2 = rev_of(st, "docs/future.md")
    assert r2["valid_from"] == r2["observed_at"]
    # (5b) a transcript re-saved with renamed speakers keeps every line's said_at
    tr = "live/2026-11-12-acme-sync/2026-11-12-1500-acme-sync.md"
    body = "# Live transcript\n\n- Start: 2026-11-12 15:00:00\n\n[15:00:05] {} (en): The pilot needs SSO.\n\n" + "x\n" * 0
    put(root, tr, body.format("Speaker 1"), mtime=ts("2026-11-12T16:00:00Z"))
    run(st, root)
    first = [r["said_at"] for r in st.q("SELECT said_at FROM snippet WHERE revision_id=? ORDER BY ord", (rev_of(st, tr)["id"],))]
    put(root, tr, body.format("Mike"), mtime=ts("2026-11-13T09:00:00Z"))
    run(st, root)
    second = [r["said_at"] for r in st.q("SELECT said_at FROM snippet WHERE revision_id=? ORDER BY ord", (rev_of(st, tr)["id"],))]
    assert len(rev_of(st, tr, active=False)) == 2 and first[-1] == second[-1]
    local = S.utc(S._dt.datetime(2026, 11, 12, 15, 0, 5))
    assert second[-1] == local
    # (6) full UTC date and time, never a day
    for r in st.q("SELECT valid_from, said_at FROM revision rv JOIN snippet sn ON sn.revision_id=rv.id"):
        assert len(r["valid_from"]) == 20 and r["valid_from"].endswith("Z") and len(r["said_at"]) == 20


# ---------------------------------------------------------------- access

def access_world(root):
    """Every access case in one project: an Acme call hidden, a revoked Google copy and Notion page, Globex files and a
    Globex line in a shared file, a claim with one out-of-scope row, an entity known only from Globex files."""
    two_clients(root)
    put(root, "pacenote/decisions.md", "# Active decisions\n\nShared: weekly reports on Fridays.\n\n"
                                       "Globex: the kestrel export is CSV only.\n\nShared: demos on Tuesdays.\n")
    put(root, "live/2026-11-10-acme-weekly-status/2026-11-10-1000-acme-weekly-status.md",
        "# Live transcript\n\n- Start: 2026-11-10 10:00:00\n\n[10:00:10] Mike (en): The osprey migration slips a week.\n")
    put(root, "live/google/google-file-{}.md".format(GID[:16]), "# Doc\n\nThe heron contract ends in May.\n")
    put(root, "_notion/Page {}.md".format(NID), "# Page\n\nThe falcon budget is frozen.\n")
    put(root, "docs/acme/sso.md", "# SSO\n\nThe SSO integration is blocked by item 13.\n")
    put(root, "docs/globex/sso.md", "# SSO\n\nThe SSO integration is blocked by item 13 for Globex too.\n")
    put(root, "docs/globex/lark.md", "# Lark\n\nThe lark gateway is owned by Sarah.\n")


def access_checks(st, root, sc):
    """Every case of the access acceptance line: none returned, and none ever a ranked candidate."""
    st_ex = SE.search(st, "osprey migration", scope=sc)
    assert "osprey" not in texts(st_ex)
    for q, word in (("heron contract", "heron"), ("falcon budget", "falcon"), ("invoice export", "invoice"),
                    ("kestrel export", "kestrel"), ("lark gateway", "lark")):
        res = SE.search(st, q, scope=sc)
        assert word not in texts(res).lower(), q
        denied = {r[0] for r in st.q("SELECT sid FROM _snip WHERE cls<>'pass'")}
        assert not denied & set(res["candidates"]), q
    shared = texts(SE.search(st, "weekly reports demos", scope=sc))
    assert "Fridays" in shared and "Tuesdays" in shared
    assert _claims(st, "sso integration", scope=sc) == []         # one row in docs/globex denies it
    lark = st.one("SELECT id FROM entity WHERE name='lark'")[0]
    assert SE.entity_card(st, lark, scope=sc) is None             # known only from Globex files
    assert not any(e["id"] == lark for e in SE.search(st, "lark gateway", scope=sc)["entities"])


def test_access_cases_filtered_in_sql(env):
    root = env
    access_world(root)
    st = open_store(root=root)
    run(st, root)
    _claim_on(st, "docs/acme/sso.md", "The SSO integration is blocked by item 13")
    _claim_on(st, "docs/globex/sso.md", "The SSO integration is blocked by item 13")
    S.apply_extraction(st, rev_of(st, "docs/globex/lark.md")["id"], {"entities": [
        {"ref": "n1", "known_id": None, "type": "system", "name": "lark", "aliases": ["lark gateway"],
         "quote": "The lark gateway", "lines": [3, 3]}], "claims": [
        {"subject": "n1", "predicate": "owner", "value": "Sarah", "modality": "statement",
         "quote": "The lark gateway is owned by Sarah", "confidence": 0.9}]})
    IG.apply_state(st, hidden_calls=["2026-11-10-1000-acme-weekly-status.md"], revoked=[GID, NID])
    sc = acme(root)
    access_checks(st, root, sc)
    # no access query reads client_set or entity.client
    with st.conn:
        st.conn.execute("UPDATE snippet SET client_set=''")
        st.conn.execute("UPDATE claim SET client_set=''")
        st.conn.execute("UPDATE edge SET client_set=''")
        st.conn.execute("UPDATE alias SET client_set=''")
        st.conn.execute("UPDATE entity SET client=''")
    access_checks(st, root, sc)
    # without a scope the Globex facts are back (one-client view), the denied states still hold
    assert "lark" in texts(SE.search(st, "lark gateway")).lower()
    assert "heron" not in texts(SE.search(st, "heron contract"))


# ---------------------------------------------------------------- the state-only pass and the request log

HIDDEN = "live/2026-11-10-acme-weekly-status/2026-11-10-1000-acme-weekly-status"


def hidden_world(root):
    put(root, HIDDEN + ".md", "# Live transcript\n\n- Start: 2026-11-10 10:00:00\n\n[10:00:10] Mike (en): The osprey migration slips.\n")
    put(root, HIDDEN + ".notes.md", "# Notes\n\n### Decisions\n- The osprey migration moves to March.\n")
    put(root, HIDDEN + ".notion.json", json.dumps({"decisions": {"the condor rollout is paused": {"id": NID, "url": ""}}}))
    put(root, "docs/other.md", "# Other\n\nThe osprey migration was planned in October.\n")


def test_state_requests(env, monkeypatch):
    root = env
    hidden_world(root)
    st = open_store("p-h", root)
    run(st, root)
    osprey_tr = HIDDEN + ".md"
    _claim_on(st, "docs/other.md", "The osprey migration was planned", subject="e-osprey", value="planned")
    _claim_on(st, osprey_tr, "The osprey migration", subject="e-osprey", value="planned")
    did = ID.item_id("d", "2026-11-10-1000-acme-weekly-status", "the condor rollout is paused")
    assert SE.entity_card(st, did) is not None                  # the sidecar-only decision is known

    def blocked(*a, **k):
        raise AssertionError("no network or embed request in the state-only pass")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(IG.LoopbackEmbedder, "__call__", blocked)
    base = ["--project-uid", "p-h", "--root", str(root)]
    # a full pass holds the lock: two requests are two lines; readers deny the pending hide at once
    lock = st.project_lock(0)
    assert lock
    assert CLI.main(["exclude", "--hidden-calls", osprey_tr, "--request-id", "r-1"] + base) == 0
    assert CLI.main(["exclude", "--revoked", NID, "--request-id", "r-2"] + base) == 0
    assert len(st.read_log()) == 2 and st.pending_requests()
    assert "osprey migration slips" not in texts(SE.search(st, "osprey migration"))
    assert _claims(st, "osprey migration") == []                # same result before and after the request applies
    assert SE.entity_card(st, did) is None                      # the decision name known only from the sidecar
    assert not any(e["id"] == did for e in SE.search(st, "condor rollout paused")["entities"])
    # the pass applies the lines first, between sources and before it exits
    seen = []
    run(st, root, between=lambda: seen.append(len(st.pending_requests())))
    lock.close()
    assert seen and seen[0] == 0 and st.applied_ids() >= {"r-1", "r-2"}
    assert _claims(st, "osprey migration") == []
    assert SE.entity_card(st, did) is None
    # a re-sent request id is a no-op
    assert IG.apply_request(st, {"request_id": "r-1", "kind": "hidden_calls", "payload": {"hidden_calls": []}}) is False
    assert st.one("SELECT state FROM source WHERE uri=?", (osprey_tr,))[0] == "excluded"
    # unhide by the CLI with the lock free: applied at once
    assert CLI.main(["exclude", "--hidden-calls", "[]", "--request-id", "r-3"] + base) == 0
    assert st.one("SELECT state FROM source WHERE uri=?", (osprey_tr,))[0] == "active"
    st.compact_log()
    assert st.read_log() == []


def test_a_request_applied_during_a_pass_is_not_undone_by_its_lists(env):
    """PB-88 round 3: a hide or revoke that lands while a pass runs (the app quit, its re-send never came) stays."""
    root = env
    hidden_world(root)
    st = open_store("p-m", root)
    osprey_tr = HIDDEN + ".md"
    run(st, root, hidden_calls=[], revoked=[])
    assert st.one("SELECT state FROM source WHERE uri=?", (osprey_tr,))[0] == "active"
    sent = []

    def between():
        if not sent:
            st.append_request("hidden_calls", {"hidden_calls": [osprey_tr]}, "r-mid")
            st.append_request("revoked", {"revoked": [NID]}, "r-mid-revoked")
            sent.append(1)
    put(root, "docs/new.md", "# New\n\nA new file, so the pass has a source to write.\n")
    run(st, root, hidden_calls=[], revoked=[], between=between)       # the pass started with empty lists
    assert sent and st.applied_ids() >= {"r-mid", "r-mid-revoked"}
    assert st.one("SELECT state FROM source WHERE uri=?", (osprey_tr,))[0] == "excluded"
    assert json.loads(st.meta("hidden_calls")) and json.loads(st.meta("revoked"))
    assert "osprey migration slips" not in texts(SE.search(st, "osprey migration"))
    # a later pass with the app's current lists applies them as usual
    run(st, root, hidden_calls=[], revoked=[])
    assert st.one("SELECT state FROM source WHERE uri=?", (osprey_tr,))[0] == "active"


def test_partial_log_line_is_skipped(env):
    st = open_store("p-l", env)
    st.append_request("hidden_calls", {"hidden_calls": ["a.md"]}, "r-a")
    with open(st.log_path, "a") as f:
        f.write('{"request_id": "r-b", "kind": "hidden')
    assert [r["request_id"] for r in st.read_log()] == ["r-a"]


def test_sigkill_leaves_lines_for_the_next_writer(env):
    root = env
    hidden_world(root)
    st = open_store("p-k", root)
    run(st, root)
    code = ("import sys, time; sys.path.insert(0, {m0!r}); from livetips_m0.evidence import store as S;"
            "st = S.Store.open(path={p!r}); lk = st.project_lock(0); print('locked', flush=True); time.sleep(60)").format(
        m0=str(M0), p=str(st.path))
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "locked"
    assert st.project_lock(0) is None
    assert CLI.main(["exclude", "--hidden-calls", HIDDEN + ".md", "--request-id", "r-k", "--project-uid", "p-k"]) == 0
    assert st.pending_requests()
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    lk = st.project_lock(1)
    assert lk
    IG.apply_pending(st)
    lk.close()
    assert "r-k" in st.applied_ids()
    assert st.one("SELECT state FROM source WHERE uri=?", (HIDDEN + ".md",))[0] == "excluded"


def test_request_exclude_ignores_rows(env):
    root = env
    put(root, "docs/a.md", "# A\n\nThe SSO integration is blocked.\n", mtime=ts("2026-11-05T09:00:00Z"))
    put(root, "docs/b.md", "# B\n\nThe SSO integration is blocked.\n", mtime=ts("2026-11-09T09:00:00Z"))
    put(root, "docs/c.md", "# C\n\nOnly here: the SSO vendor is Okta.\n")
    st = open_store(root=root)
    run(st, root)
    with st.conn:
        st.conn.execute("INSERT INTO entity(id, type, name, origin, updated_at) VALUES ('person:mike','person','Mike','voices','x')")
    _claim_on(st, "docs/a.md", "The SSO integration is blocked")
    with st.conn:
        st.conn.execute("UPDATE evidence SET said_by='person:mike'")
    _claim_on(st, "docs/b.md", "The SSO integration is blocked")
    _claim_on(st, "docs/c.md", "the SSO vendor is Okta", value="okta")
    c = [x for x in _claims(st, "sso", exclude=["docs/a.md"]) if x["value"] == "blocked"]
    assert len(c) == 1 and [q["uri"] for q in c[0]["quotes"]] == ["docs/b.md"]
    assert c[0]["said_at"] == "2026-11-09T09:00:00Z" and c[0]["said_by"] is None and c[0]["confirmations"] == 1
    assert not [x for x in _claims(st, "sso vendor", exclude=["docs/c.md"]) if x["value"] == "okta"]


# ---------------------------------------------------------------- decisions, supersede

CALL = "live/2026-11-12-acme-technical-sync/2026-11-12-1400-acme-technical-sync"


def test_decision_id_stable_across_the_sidecar_and_decisions_md_is_a_suggestion(env):
    root = env
    put(root, CALL + ".notes.md", "# Notes\n\n### Decisions\n- [14:01:05] Acme SSO uses SAML, not OIDC.\n")
    st = open_store(root=root)
    run(st, root)
    did = ID.item_id("d", CALL, "Acme SSO uses SAML, not OIDC.")
    assert st.one("SELECT 1 FROM entity WHERE id=?", (did,))
    claims = st.q("SELECT id FROM claim WHERE subject_id=?", (did,))
    put(root, CALL + ".notion.json", json.dumps({"decisions": {"acme sso uses saml not oidc": {"id": NID, "url": ""}}}),
        mtime=OLD + 50)
    put(root, "pacenote/decisions.md", "# Active decisions\n\n- 2026-11-12 | Acme SSO uses SAML, not OIDC. | Emily | SSO\n")
    run(st, root)
    assert st.one("SELECT count(*) FROM entity WHERE type='decision' AND id LIKE 'd-%' AND name LIKE '%SAML%'")[0] == 2
    assert [r["id"] for r in st.q("SELECT id FROM claim WHERE subject_id=?", (did,))] == [r["id"] for r in claims]
    assert st.one("SELECT entity_id FROM alias WHERE alias_norm=?", ("notion:" + NID,))[0] == did
    e = st.one("SELECT * FROM edge WHERE rel='same_as'")
    assert e and e["dst_id"] == did and e["status"] == "suggestion" and e["modality"] == "statement"
    assert st.one("SELECT merged_into FROM entity WHERE id=?", (did,))[0] is None


@pytest.mark.parametrize("order", ["no store yet", "before the sidecar"])
def test_supersede_override_through_the_cli(env, order):
    root = env
    old_id, new_id = NID, NID[::-1]
    base = ["--project-uid", "p-s", "--root", str(root), "--no-embed", "--quiet-seconds", "0"]
    put(root, CALL + ".notes.md", "# Notes\n\n### Decisions\n- Acme SSO uses SAML, not OIDC.\n- Acme SSO uses OIDC after all.\n")
    if order == "before the sidecar":
        assert CLI.main(["ingest"] + base) == 0
    ov = ["override", "--project-uid", "p-s", "--target", "entity", "--key", "notion:" + old_id, "--field", "supersede",
          "--value", "notion:" + new_id, "--at", "2026-11-19T10:00:00Z", "--via", "app"]
    assert CLI.main(ov) == 0
    put(root, CALL + ".notion.json", json.dumps({"decisions": {"acme sso uses saml not oidc": {"id": old_id},
                                                               "acme sso uses oidc after all": {"id": new_id}}}))
    assert CLI.main(["ingest"] + base) == 0
    st = S.Store.open(uid="p-s")
    old = ID.item_id("d", CALL, "Acme SSO uses SAML, not OIDC.")
    new = ID.item_id("d", CALL, "Acme SSO uses OIDC after all.")
    card = SE.entity_card(st, old)
    assert card["status"] == "superseded"
    sup = [e for e in card["edges"] if e["rel"] == "supersedes"]
    assert sup and sup[0]["src"] == new and sup[0]["said_at"] == "2026-11-19T10:00:00Z"
    early = SE.entity_card(st, old, as_of="2026-11-18T00:00:00Z")
    assert early and early["status"] != "superseded" and not [e for e in early["edges"] if e["rel"] == "supersedes"]
    assert not [c for c in _claims(st, "SAML OIDC") if c["subject"] == old]   # never returned as current


# ---------------------------------------------------------------- chunks and client parity

def test_chunk_invariant_and_heading_without_blank_line(env):
    uni = {"acme", "globex", "docs"}
    text = "L1 shared line\nL2 Globex line\nL3 shared line\n\n# Notes\nL1 shared line\nL2 Globex line\nL3 shared line\n"
    chunks = LK.chunks_at(text, uni)
    for c in chunks:
        lines = [ln for ln in c["text"].split("\n") if ln.strip() and not CS.HEADING.match(ln.strip())]
        assert len({frozenset(CS._names(ln) & uni) for ln in lines}) <= 1
        assert text[c["chars"][0]:c["chars"][1]] == c["text"]
    root = env
    two_clients(root)
    put(root, "pacenote/answer-cards.md", "# Cards\n\nL1 shared line about the pilot.\nL2 the Globex line about the pilot.\n"
                                          "L3 shared line about the pilot.\n\n## Under\nM1 shared pilot.\nM2 Globex pilot.\nM3 shared pilot.\n")
    st = open_store(root=root)
    run(st, root)
    sc = acme(root)
    t = passing(st, sc, "pacenote/answer-cards.md")
    assert "L1" in t and "L3" in t and "M1" in t and "M3" in t and "L2" not in t and "M2" not in t
    assert "L2" not in texts(SE.search(st, "Globex line pilot", scope=sc))
    g = dict(sc, client="globex", others=["acme"])
    t = passing(st, g, "pacenote/answer-cards.md")
    assert all(x in t for x in ("L1", "L2", "L3", "M1", "M2", "M3"))


def test_no_cut_inside_a_line(env):
    root = env
    two_clients(root)
    line = "Globex note: " + "the condor ledger total " * 85
    assert len(line) > 2000 and "Globex" in line[:20]
    put(root, "pacenote/answer-cards.md", "# Cards\n\nA shared line about the ledger.\n" + line + "\n")
    assert all("Globex" in c["text"] or "condor" not in c["text"] for c in LK.chunks_at(line, {"globex"}))
    st = open_store(root=root)
    run(st, root)
    t = texts(SE.search(st, "condor ledger total", scope=acme(root), budget=SE.Budget(k=50, chars=10 ** 6)))
    assert "condor" not in t


def test_nested_subheading_takes_the_client_from_the_ancestor(env):
    root = env
    two_clients(root)
    put(root, "pacenote/answer-cards.md", "# State\n\n## Globex\n### Timeline\nThe wren milestone is Friday.\n\n## Shared\nThe wren review is Monday.\n")
    st = open_store(root=root)
    run(st, root)
    t = passing(st, acme(root), "pacenote/answer-cards.md")
    assert "Monday" in t and "Friday" not in t and "Friday" not in texts(SE.search(st, "wren milestone", scope=acme(root)))


def _kept_lines(scope, uri, text):
    """The non-blank line numbers side() and filter_text keep for a scope (the query-time functions)."""
    path = os.path.join(scope["root"], uri)
    s = CS.side(scope, path)
    if s == "other":
        return set()
    lines = text.split("\n")
    if s == "own":
        return {i + 1 for i, ln in enumerate(lines) if ln.strip()}
    kept, _n = CS.filter_text(scope, text)
    # filter_text keeps lines in order: walk both to number the kept ones
    out, j = set(), 0
    kept_lines = kept.split("\n")
    for i, ln in enumerate(lines):
        if j < len(kept_lines) and kept_lines[j] == ln:
            if ln.strip():
                out.add(i + 1)
            j += 1
    return out


def parity(st, scope):
    SE.prepare(st, scope, None)
    for src in st.q("SELECT s.id, s.uri FROM source s WHERE s.kind NOT IN ('register','tracker','link_sidecar')"):
        rv = rev_of(st, src["uri"])
        snips = st.q("SELECT sn.id, sn.text, sn.locator, x.cls FROM snippet sn JOIN _snip x ON x.sid=sn.id WHERE sn.revision_id=?",
                     (rv["id"],))
        text = "\n".join(r["text"] for r in snips)
        got, full = set(), {}
        for r in snips:
            a, _b = json.loads(r["locator"])["lines"]
            for k, ln in enumerate(r["text"].split("\n")):
                full[a + k] = ln
                if r["cls"] == "pass" and ln.strip():
                    got.add(a + k)
        n = max(full) if full else 0
        whole = "\n".join(full.get(i, "") for i in range(1, n + 1))
        want = _kept_lines(scope, src["uri"], whole)
        assert got == want, (scope["client"], src["uri"], sorted(got ^ want))
    reg = st.one("SELECT id FROM source WHERE kind='register'")
    if reg:
        rv = st.one("SELECT id FROM revision WHERE source_id=? AND superseded_at IS NULL", (reg["id"],))
        got = {json.loads(r["locator"])["lines"][0] for r in st.q(
            "SELECT sn.locator FROM snippet sn JOIN _snip x ON x.sid=sn.id WHERE sn.revision_id=? AND x.cls='pass'",
            (rv["id"],))}
        assert got == _kept_events(scope), (scope["client"], "register", sorted(got ^ _kept_events(scope)))


def _kept_events(scope):
    """The line numbers of the register events filter_register keeps for a scope (events with an op and an id)."""
    raw = Path(scope["root"], IG.REGISTER).read_text(encoding="utf-8")
    kept, j, out = CS.filter_register(scope, raw).split("\n"), 0, set()
    for n, ln in enumerate(raw.split("\n"), 1):
        if j < len(kept) and kept[j] == ln:
            j += 1
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("op") and e.get("id"):
                out.add(n)
    return out


def test_client_parity_on_the_g0_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_SUPPORT_DIR", str(tmp_path / "support"))
    root = tmp_path / "northpoint"
    shutil.copytree(G0, root)
    st = open_store("g0", root)
    run(st, root)
    scopes = []
    for title, comp in (("Acme weekly status", ["Acme"]), ("Globex weekly", ["Globex"]), ("Acme weekly status", []),
                        ("Globex weekly", [])):
        sc = CS.decide(str(root), title, comp)
        if sc:
            scopes.append(sc)
    assert {s["client"] for s in scopes} == {"acme", "globex"}
    for sc in scopes:
        parity(st, sc)


def test_register_parity_with_filter_register(env):
    """A register event's side is its own add's item_side, as filter_register decides it: a moved Globex task (fold
    drops it), a Globex add joined to an Acme task and a plain task edited to name Globex all keep the reference
    lines for both clients, not the folded entity's side."""
    root = env
    two_clients(root)
    reg = [{"v": 1, "op": "add", "id": "c-gggggggggg", "text": "send the pelican pricing sheet", "owner": "me",
            "series": "|globexweekly", "created": "2026-11-04T11:00:00+00:00"},
           {"v": 1, "op": "move", "id": "c-gggggggggg", "at": "2026-11-05T10:00:00+00:00"},
           {"v": 1, "op": "edit", "id": "c-gggggggggg", "text": "send the pelican pricing sheet v2", "at": "2026-11-06T10:00:00+00:00"},
           {"v": 1, "op": "add", "id": "c-aaaaaaaaaa", "text": "book the heron demo", "owner": "me",
            "series": "|acmeweeklystatus", "created": "2026-11-03T10:00:00+00:00"},
           {"v": 1, "op": "add", "id": "c-hhhhhhhhhh", "text": "Globex heron demo slides", "owner": "me",
            "created": "2026-11-04T11:05:00+00:00"},
           {"v": 1, "op": "same", "id": "c-hhhhhhhhhh", "to": "c-aaaaaaaaaa", "at": "2026-11-05T11:00:00+00:00"},
           {"v": 1, "op": "add", "id": "c-pppppppppp", "text": "order the plover badges", "owner": "me",
            "created": "2026-11-04T12:00:00+00:00"},
           {"v": 1, "op": "edit", "id": "c-pppppppppp", "text": "order the plover badges for Globex", "at": "2026-11-06T12:00:00+00:00"},
           {"v": 1, "op": "done", "id": "c-hhhhhhhhhh", "at": "2026-11-07T10:00:00+00:00"},
           {"v": 1, "op": "add", "id": "c-dddddddddd", "text": "print the osprey labels", "owner": "me",
            "series": "|acmeweeklystatus", "created": "2026-11-03T10:30:00+00:00"},
           {"v": 1, "op": "add", "id": "c-dddddddddd", "text": "print the osprey labels", "owner": "me",
            "call": "2026-11-04-1100-globex-weekly.md", "created": "2026-11-04T11:30:00+00:00"}]
    put(root, IG.REGISTER, "\n".join(json.dumps(e) for e in reg) + "\n")
    st = open_store(root=root)
    run(st, root)
    sc_a = acme(root)
    sc_g = CS.decide(str(root), "Globex weekly", ["Globex"])
    assert sc_g and sc_g["client"] == "globex"
    assert _kept_events(sc_a) == {4, 7, 8} and _kept_events(sc_g) == {1, 2, 3, 5, 6, 7, 8, 9}   # both sides covered
    for sc in (sc_a, sc_g):
        parity(st, sc)
    assert "pelican" not in texts(SE.search(st, "pelican pricing sheet", scope=sc_a))
    assert "heron demo slides" not in texts(SE.search(st, "heron demo slides", scope=sc_a))
    CLI.rebuild(st, str(root), quiet_s=0, voices={})
    for sc in (sc_a, sc_g):
        parity(st, sc)


def test_register_written_client_parity_pb73(env):
    """PB-73: a register item's written client decides its side first, in the store as in filter_register: two clients
    share the series "Weekly sync" with no client word in the call name or text, an Acme task names only Globex."""
    root = env
    two_clients(root)
    add = lambda cid, text, client: {"v": 1, "op": "add", "id": cid, "text": text, "owner": "me",  # noqa: E731
                                     "series": "|weeklysync", "call": "2026-11-10-1000-weekly-sync.md",
                                     "client": client, "created": "2026-11-10T10:00:00+00:00"}
    reg = [add("c-aaaaaaaaaa", "confirm the osprey user mapping", "acme"),
           add("c-gggggggggg", "send the pelican pricing sheet", "globex"),
           add("c-pppppppppp", "ask the Globex reseller for the heron quote", "acme"),
           add("c-nnnnnnnnnn", "book the plover review room", "")]
    put(root, IG.REGISTER, "\n".join(json.dumps(e) for e in reg) + "\n")
    st = open_store(root=root)
    run(st, root)
    sc_a = acme(root)
    sc_g = CS.decide(str(root), "Globex weekly", ["Globex"])
    assert _kept_events(sc_a) == {1, 3, 4} and _kept_events(sc_g) == {2, 4}
    for sc in (sc_a, sc_g):
        parity(st, sc)
    assert "pelican" not in texts(SE.search(st, "pelican pricing sheet", scope=sc_a))
    assert "osprey" not in texts(SE.search(st, "osprey user mapping", scope=sc_g))
    SE.prepare(st, sc_a, None)
    side = {r["id"]: r["side"] for r in st.q("SELECT id, side FROM _eside WHERE id LIKE 'c-%'")}
    assert side == {"c-aaaaaaaaaa": "own", "c-gggggggggg": "other", "c-pppppppppp": "own", "c-nnnnnnnnnn": "shared"}
    # a later join of an Acme task with a Globex mention: two written clients, the store follows item_side again
    reg.append({"v": 1, "op": "same", "id": "c-gggggggggg", "to": "c-aaaaaaaaaa", "at": "2026-11-12T10:00:00+00:00"})
    put(root, IG.REGISTER, "\n".join(json.dumps(e) for e in reg) + "\n", mtime=OLD + 60)
    run(st, root)
    item = next(i for i in CM.load(str(root / "live")) if i["id"] == "c-aaaaaaaaaa")
    assert CS.item_clients(item) == {"acme", "globex"}
    for sc in (sc_a, sc_g):
        SE.prepare(st, sc, None)
        got = st.one("SELECT side FROM _eside WHERE id='c-aaaaaaaaaa'")["side"]
        assert got == {"own": "own", "other": "other", None: "shared"}[CS.item_side(sc, item)], sc["client"]


def test_task_name_and_status_come_from_the_visible_register_events(env):
    """A Globex mention joined to an Acme task and then edited and ticked: under the Acme scope the task keeps its own
    name and stays open, with no Globex text or id; under the Globex scope only the Globex mention is seen, by its own
    id. Both on entity_card and search (PB-84 review round 4)."""
    root = env
    two_clients(root)
    reg = [{"v": 1, "op": "add", "id": "c-aaaaaaaaaa", "text": "book the heron demo", "owner": "me",
            "series": "|acmeweeklystatus", "created": "2026-11-03T10:00:00+00:00"},
           {"v": 1, "op": "add", "id": "c-hhhhhhhhhh", "text": "Globex heron demo slides", "owner": "me",
            "series": "|globexweekly", "created": "2026-11-04T11:05:00+00:00"},
           {"v": 1, "op": "same", "id": "c-hhhhhhhhhh", "to": "c-aaaaaaaaaa", "at": "2026-11-05T11:00:00+00:00"},
           {"v": 1, "op": "edit", "id": "c-hhhhhhhhhh", "text": "pelican secret price 9000", "at": "2026-11-06T10:00:00+00:00"},
           {"v": 1, "op": "done", "id": "c-hhhhhhhhhh", "at": "2026-11-07T10:00:00+00:00"}]
    put(root, IG.REGISTER, "\n".join(json.dumps(e) for e in reg) + "\n")
    st = open_store(root=root)
    run(st, root)
    sc_a = acme(root)
    sc_g = CS.decide(str(root), "Globex weekly", ["Globex"])
    assert sc_g and sc_g["client"] == "globex"
    assert _kept_events(sc_a) == {1} and _kept_events(sc_g) == {2, 3, 4, 5}
    a_card = SE.entity_card(st, "c-aaaaaaaaaa", scope=sc_a)
    assert a_card["name"] == "book the heron demo" and a_card["status"] == "open" and a_card["id"] == "c-aaaaaaaaaa"
    assert SE.entity_card(st, "c-hhhhhhhhhh", scope=sc_a) is None      # the mention's id is Globex's
    a_hits = [SE.search(st, q, scope=sc_a) for q in ("c-aaaaaaaaaa", "c-hhhhhhhhhh", "heron demo", "pelican price")]
    assert not a_hits[1]["entities"]
    for blob in [a_card] + a_hits:
        text = json.dumps(blob).lower()
        assert "hhhhhhhhhh" not in text and "pelican" not in text and "globex" not in text, text
    g_card = SE.entity_card(st, "c-hhhhhhhhhh", scope=sc_g)
    assert g_card["id"] == "c-hhhhhhhhhh" and g_card["name"] == "pelican secret price 9000" and g_card["status"] == "done"
    assert SE.entity_card(st, "c-aaaaaaaaaa", scope=sc_g) is None      # the task's own id is Acme's
    g_hits = [SE.search(st, q, scope=sc_g) for q in ("c-hhhhhhhhhh", "c-aaaaaaaaaa", "heron demo", "pelican price")]
    assert [(e["id"], e["name"], e["status"]) for e in g_hits[0]["entities"]] == [
        ("c-hhhhhhhhhh", "pelican secret price 9000", "done")]
    assert "pelican" in texts(g_hits[3])
    for blob in [g_card] + [h["entities"] for h in g_hits]:     # the kept op same line itself names its target
        text = json.dumps(blob).lower()
        assert "aaaaaaaaaa" not in text and "book the heron" not in text, text
    unscoped = SE.entity_card(st, "c-aaaaaaaaaa")                     # no scope: the whole register, as before
    assert unscoped["name"] == "pelican secret price 9000" and unscoped["status"] == "done"


def late_client(root):
    put(root, "docs/acme/notes.md", "# Acme\n\nThe Acme pilot has 60 users.\n")
    put(root, "docs/globex/notes.md", "# Globex\n\nThe Globex budget is 120 hours.\n")
    put(root, "live/2026-11-04-globex-weekly/2026-11-04-1100-globex-weekly.md",
        "# Live transcript\n\n- Start: 2026-11-04 11:00:00\n\n[11:00:10] Sarah (en): Globex budget is 120 hours.\n")
    put(root, "pacenote/decisions.md", "# Decisions\n\nShared one about the pilot.\nOnly Globex: invoices in CSV for the pilot.\n"
                                       "Shared two about the pilot.\n\nShared three about the pilot.\nOnly Acme: SSO via SAML for the pilot.\n"
                                       "Shared four about the pilot.\n")


def test_late_client_and_parity(env):
    root = env
    late_client(root)
    assert CS.labels(str(root)) == ["globex"]               # ingest knows only globex
    st = open_store(root=root)
    run(st, root)
    sc = CS.decide(str(root), "Kickoff", ["Acme"])
    assert sc["client"] == "acme" and sc["others"] == ["globex"]
    t = passing(st, sc) + texts(SE.search(st, "pilot budget hours users invoices SSO", scope=sc, budget=SE.Budget(k=50)))
    assert "120 hours" not in t and "invoices in CSV" not in t
    assert "60 users" in t and "SAML" in t and all(x in t for x in ("Shared one", "Shared two", "Shared three", "Shared four"))
    parity(st, sc)
    # second half: an Acme sheet makes Acme backed; a Globex call's scope with a label labels() did not know
    put(root, "live/2026-11-05-acme-kickoff/.work/prep-2026-11-05-0900-acme-kickoff.sheet.json",
        json.dumps({"participants": {"theirs": [{"name": "Mike", "company": "Acme"}]}}))
    g = CS.decide(str(root), "Globex weekly", ["Globex"])
    assert g["client"] == "globex" and g["others"] == ["acme"]
    t = passing(st, g)
    assert "120 hours" in t and "60 users" not in t and "SAML" not in t and "invoices in CSV" in t
    assert all(x in t for x in ("Shared one", "Shared two", "Shared three", "Shared four"))
    parity(st, g)


def test_universe_change_splits_snippets_and_stale_live(env):
    root = env
    two_clients(root)
    put(root, "pacenote/answer-cards.md", "# Cards\n\nShared alpha about the rollout.\nInitech wants the rollout early.\n"
                                          "Shared beta about the rollout.\nAcme Corp asks about the rollout.\nShared gamma about the rollout.\n",
        mtime=ts("2026-11-10T09:00:00Z"))
    st = open_store(root=root)
    run(st, root)
    uri = "pacenote/answer-cards.md"
    for q in ("Shared alpha about the rollout", "Shared beta about the rollout", "Shared gamma about the rollout"):
        _claim_on(st, uri, q, subject="e-rollout", value=q)
    put(root, "pacenote/answer-cards.md", "# Cards\n\nShared alpha about the rollout.\nInitech wants the rollout early.\n"
                                          "Shared beta about the rollout.\nAcme Corp asks about the rollout.\nShared gamma about the rollout.\n"
                                          "Shared delta about the rollout.\n", mtime=ts("2026-11-15T09:00:00Z"))
    run(st, root)
    for q in ("Shared alpha about the rollout", "Shared beta about the rollout", "Shared gamma about the rollout"):
        _claim_on(st, uri, q, subject="e-rollout", value=q)
    nkeys = sorted(r[0] for r in st.q("SELECT nkey FROM claim"))
    (root / "docs" / "initech").mkdir()
    (root / "docs" / "acme-corp").mkdir()
    # a Globex call that knows the two new folders as other clients ("Acme Corp" names acme too, so the own client
    # must not be acme: a line naming the own client passes, as in filter_text)
    sc = {"client": "globex", "others": ["initech", "acmecorp"], "root": str(root),
          "folders": {"globex": ["docs/globex"], "initech": ["docs/initech"], "acmecorp": ["docs/acme-corp"]}}
    live = SE.search(st, "rollout", scope=sc, mode="live", budget=SE.Budget(k=50))
    assert live["scope_stale"] and texts(live) == ""
    rev_before = st.one("SELECT count(*) FROM revision")[0]
    res = SE.search(st, "rollout", scope=sc, budget=SE.Budget(k=50, chars=10 ** 6))
    assert not res["scope_stale"] and res["relabel"]["split"] >= 2
    t = texts(res) + passing(st, sc)
    assert "Initech" not in t and "Acme Corp" not in t and "Shared alpha" in t and "Shared gamma" in t
    assert sorted(r[0] for r in st.q("SELECT nkey FROM claim")) == nkeys
    assert st.one("SELECT count(*) FROM revision")[0] == rev_before
    for q in ("Shared alpha", "Shared beta", "Shared gamma"):
        assert any(q in c["value"] for c in _claims(st, q.lower() + " rollout", scope=sc))
    old = passing(st, sc, as_of="2026-11-12T00:00:00Z")
    assert "Initech" not in old and "Acme Corp" not in old and "Shared beta" in old and "delta" not in old


def _initech_scope(root, client="acme"):
    others = sorted({"acme", "globex", "initech"} - {client})
    return {"client": client, "others": others, "root": str(root),
            "folders": {n: ["docs/" + n] for n in ("acme", "globex", "initech")}}


def test_client_folder_added_after_a_call_folder_names_it(env):
    """The call folder names Initech before docs/initech exists; the transcript text never does. Once the folder is
    created the path alone makes the call another client's (path marks are stored unfiltered, spec section 5.3)."""
    root = env
    two_clients(root)
    uri = "live/2026-11-06-initech-sync/2026-11-06-1000-initech-sync.md"
    put(root, uri, "# Live transcript\n\n- Start: 2026-11-06 10:00:00\n\n[10:00:10] Dana (en): The budget is 90 hours.\n")
    st = open_store(root=root)
    run(st, root)
    (root / "docs" / "initech").mkdir()
    put(root, "docs/initech/plan.md", "# Initech plan\n\nInitech wants a pilot.\n")
    sc = _initech_scope(root)
    assert CS.side(sc, os.path.join(str(root), uri)) == "other"
    t = texts(SE.search(st, "budget hours", scope=sc, budget=SE.Budget(k=50)))
    assert "90 hours" not in t and "90 hours" not in passing(st, sc)
    parity(st, sc)
    run(st, root)
    assert "90 hours" not in passing(st, sc)
    parity(st, sc)
    assert "90 hours" in passing(st, _initech_scope(root, "initech"))


def test_client_folder_added_after_a_tracker_item_names_it(env, tmp_path):
    """A Tracker item names Initech before docs/initech exists: its entity text marks are stored unfiltered, so the
    item is another client's once the folder is created (the snippet follows the item's side)."""
    root = env
    two_clients(root)
    snap = tmp_path / "tracker.json"
    snap.write_text(json.dumps({"items": [{"id": "1234abcd", "text": "Send Initech the revised SOW", "status": "open",
                                           "last_edited": "2026-11-06T10:00:00Z"}]}), encoding="utf-8")
    st = open_store(root=root)
    run(st, root, tracker_path=str(snap))
    (root / "docs" / "initech").mkdir()
    put(root, "docs/initech/plan.md", "# Initech plan\n\nInitech wants a pilot.\n")
    sc = _initech_scope(root)
    assert "revised SOW" not in texts(SE.search(st, "revised SOW", scope=sc, budget=SE.Budget(k=50)))
    assert "revised SOW" not in passing(st, sc)
    run(st, root, tracker_path=str(snap))
    assert "revised SOW" not in passing(st, sc)
    assert "revised SOW" not in texts(SE.search(st, "revised SOW", scope=sc, budget=SE.Budget(k=50)))
    assert "revised SOW" in passing(st, _initech_scope(root, "initech"))


# ---------------------------------------------------------------- time, modality, edges

def test_time_replay_on_content_time(env):
    root = env
    put(root, "docs/plan.md", "# Plan\n\nThe go-live date is December 8.\n", mtime=ts("2026-11-10T09:00:00Z"))
    reg = [{"v": 1, "op": "add", "id": "c-1111111111", "text": "send the plover checklist", "owner": "me",
            "series": "|acmeweeklystatus", "call": "2026-11-10-1000-acme-weekly-status.md", "created": "2026-11-10T10:00:00+00:00"},
           {"v": 1, "op": "done", "id": "c-1111111111", "at": "2026-11-19T10:00:00+00:00"},
           {"v": 1, "op": "add", "id": "c-2222222222", "text": "send the plover report", "owner": "me",
            "series": "|acmeweeklystatus", "call": "2026-11-12-1000-acme-weekly-status.md", "created": "2026-11-12T10:00:00+00:00"},
           {"v": 1, "op": "same", "id": "c-2222222222", "to": "c-1111111111", "at": "2026-11-20T10:00:00+00:00"}]
    put(root, "live/commitments.jsonl", "\n".join(json.dumps(e) for e in reg) + "\n")
    st = open_store(root=root)
    run(st, root)
    _claim_on(st, "docs/plan.md", "The go-live date is December 8", subject="e-golive", value="December 8")
    put(root, "docs/plan.md", "# Plan\n\nThe go-live date is January 12.\n", mtime=ts("2026-11-19T09:00:00Z"))
    run(st, root)
    _claim_on(st, "docs/plan.md", "The go-live date is January 12", subject="e-golive", value="January 12")
    assert texts(SE.search(st, "go-live date", as_of="2026-11-01T00:00:00Z")) == ""      # before the first revision
    old = _claims(st, "go-live date", as_of="2026-11-17T00:00:00Z")
    assert [c["value"] for c in old] == ["December 8"]
    now = _claims(st, "go-live date")
    assert [c["value"] for c in now] == ["January 12"]                                     # the old date is history
    card = SE.entity_card(st, "e-golive")
    assert [h["value"] for h in card["history"]] == ["December 8"] and card["history"][0]["note"] == "changed later"
    assert SE.entity_card(st, "e-golive", window=("2026-11-15T00:00:00Z", None))["history"] == []   # before the window
    assert SE.entity_state(st, "c-1111111111", "2026-11-17T00:00:00Z")["status"] == "open"
    assert SE.entity_state(st, "c-1111111111", None)["status"] == "done"
    assert SE.entity_state(st, "c-2222222222", "2026-11-11T00:00:00Z") == {}               # not said yet
    fold = IG.register_fold_at(st, "2026-11-19T12:00:00Z")
    assert fold["c-2222222222"]["id"] == "c-2222222222"                                    # the merge is later
    # a PM rejection applies at every as_of
    nkey = st.one("SELECT nkey FROM claim WHERE value='December 8'")[0]
    IG.write_override(st, {"target": "claim", "key": nkey, "field": "reject", "at": "2026-11-23T00:00:00Z"})
    assert _claims(st, "go-live date", as_of="2026-11-17T00:00:00Z") == []
    card = SE.entity_card(st, "e-golive")                                                 # nor in the card's history
    assert [c["value"] for c in card["claims"]] == ["January 12"] and card["history"] == []
    assert SE.entity_card(st, "e-golive", as_of="2026-11-17T00:00:00Z") is None            # its only claim then


OKTA = "Use Okta for single sign-on"
CALL10 = "live/2026-11-10-acme-technical-sync/2026-11-10-1400-acme-technical-sync"


@pytest.mark.parametrize("where", ["notes", "decisions.md", "sidecar"])
def test_decision_added_in_a_later_revision_is_not_visible_before_it(env, where):
    """A decision, from call notes, pacenote/decisions.md or only a sidecar key, that a later revision of its file
    adds: a replay before that revision neither names it in `entities` nor opens its card (PB-84 review round 7)."""
    root = env
    rel = {"notes": CALL10 + ".notes.md", "decisions.md": "pacenote/decisions.md", "sidecar": CALL10 + ".notion.json"}[where]
    first = {"notes": "# Notes\n\n### Summary\n- Kickoff with Mike.\n", "decisions.md": "# Active decisions\n",
             "sidecar": json.dumps({"decisions": {}})}[where]
    later = {"notes": "# Notes\n\n### Decisions\n- {}\n".format(OKTA),
             "decisions.md": "# Active decisions\n\n- 2026-11-14 | {} | Emily | SSO\n".format(OKTA),
             "sidecar": json.dumps({"decisions": {OKTA: {"id": NID, "url": ""}}})}[where]
    put(root, rel, first, mtime=ts("2026-11-10T12:00:00Z"))
    st = open_store(root=root)
    run(st, root)
    put(root, rel, later, mtime=ts("2026-11-15T12:00:00Z"))
    run(st, root)
    did = {"notes": ID.item_id("d", CALL10, OKTA), "decisions.md": ID.register_decision_id("2026-11-14", OKTA),
           "sidecar": ID.item_id("d", CALL10, OKTA)}[where]
    assert st.one("SELECT 1 FROM entity WHERE id=?", (did,))
    if where != "sidecar":            # a sidecar-only key has only a notion: alias, no name a search could match
        assert SE.search(st, OKTA, as_of="2026-11-12T00:00:00Z")["entities"] == []
        assert [e["id"] for e in SE.search(st, OKTA, as_of="2026-11-17T00:00:00Z")["entities"]] == [did]
    assert SE.entity_card(st, did, as_of="2026-11-12T00:00:00Z") is None
    assert SE.entity_state(st, did, "2026-11-12T00:00:00Z") == {}
    assert SE.entity_card(st, did, as_of="2026-11-17T00:00:00Z")["name"] == OKTA
    assert SE.entity_card(st, did)["name"] == OKTA


def test_decision_removed_in_a_later_revision_is_not_current(env):
    """A decision line a later notes revision removes: visible in a replay at a time its revision was valid, not as
    current (no card, not in `entities`)."""
    root = env
    rel = CALL10 + ".notes.md"
    put(root, rel, "# Notes\n\n### Decisions\n- {}\n".format(OKTA), mtime=ts("2026-11-10T12:00:00Z"))
    st = open_store(root=root)
    run(st, root)
    put(root, rel, "# Notes\n\n### Summary\n- Kickoff with Mike.\n", mtime=ts("2026-11-15T12:00:00Z"))
    run(st, root)
    did = ID.item_id("d", CALL10, OKTA)
    assert SE.entity_card(st, did, as_of="2026-11-12T00:00:00Z")["name"] == OKTA
    assert SE.entity_card(st, did) is None
    assert SE.search(st, OKTA)["entities"] == []


@pytest.mark.parametrize("raw,key", [("1234abcd", "notion:1234abcd"), ("crm:Sow-1", "crm:Sow-1")])
def test_tracker_task_closed_after_as_of_is_open(env, tmp_path, raw, key):
    """A Tracker item renamed and closed in a later snapshot: now it is done under its new name; a replay before the
    change reads the Tracker revision valid then (open, the old name), never the current entity row."""
    root = env
    put(root, "docs/plan.md", "# Plan\n\nAcme pilot starts in January.\n")
    snap = tmp_path / "tracker.json"
    item = {"id": raw, "text": "Send the revised SOW", "status": "open", "last_edited": "2026-11-06T10:00:00Z"}
    snap.write_text(json.dumps({"items": [item]}), encoding="utf-8")
    st = open_store(root=root)
    run(st, root, tracker_path=str(snap))
    snap.write_text(json.dumps({"items": [dict(item, text="Send the revised SOW to Mike", status="done",
                                               last_edited="2026-11-20T10:00:00Z")]}), encoding="utf-8")
    run(st, root, tracker_path=str(snap))
    eid, = SE.resolve_key(st, key)
    SE.prepare(st, None, None, ())
    now = SE.entity_state(st, eid, None)
    assert (now["status"], now["name"]) == ("done", "Send the revised SOW to Mike")
    SE.prepare(st, None, "2026-11-15T00:00:00Z", ())
    old = SE.entity_state(st, eid, "2026-11-15T00:00:00Z")
    assert (old["status"], old["name"]) == ("open", "Send the revised SOW")
    assert SE.entity_state(st, eid, "2026-11-01T00:00:00Z") == {}                         # not in the Tracker yet
    card = SE.entity_card(st, eid)
    assert (card["status"], card["name"]) == ("done", "Send the revised SOW to Mike")
    card = SE.entity_card(st, eid, as_of="2026-11-15T00:00:00Z")
    assert (card["status"], card["name"]) == ("open", "Send the revised SOW")


def test_tracker_item_of_a_register_task_keeps_the_register_name(env, tmp_path):
    """A snapshot item whose Notion page a register task carries links to that task and never renames it."""
    root = env
    reg = [{"v": 1, "op": "add", "id": "c-3333333333", "text": "send the plover checklist", "owner": "me",
            "created": "2026-11-10T10:00:00+00:00"},
           {"v": 1, "op": "link", "id": "c-3333333333", "notion_page": "1234abcd", "at": "2026-11-10T11:00:00+00:00"}]
    put(root, "live/commitments.jsonl", "\n".join(json.dumps(e) for e in reg) + "\n")
    snap = tmp_path / "tracker.json"
    snap.write_text(json.dumps({"items": [{"id": "1234abcd", "text": "Plover checklist (Notion title)", "status": "done",
                                           "last_edited": "2026-11-12T10:00:00Z"}]}), encoding="utf-8")
    st = open_store(root=root)
    run(st, root, tracker_path=str(snap))
    assert SE.resolve_key(st, "notion:1234abcd") == ["c-3333333333"]
    SE.prepare(st, None, None, ())
    st_now = SE.entity_state(st, "c-3333333333", None)
    assert (st_now["name"], st_now["status"]) == ("send the plover checklist", "open")
    assert tuple(st.one("SELECT name, status FROM entity WHERE id='c-3333333333'")) == ("send the plover checklist", "open")


def test_app_snapshot_hubspot_key_links_the_register_task(env, tmp_path):
    """PB-88 (S5): the app keys a HubSpot task crm:<portal>:<task> with project and no last_edited; a register task
    linked to that task in that portal is the same task, never a second one."""
    root = env
    reg = [{"v": 1, "op": "add", "id": "c-4444444444", "text": "send the revised sow", "owner": "me",
            "created": "2026-11-10T10:00:00+00:00"},
           {"v": 1, "op": "crm_link", "id": "c-4444444444", "crm_task": "9001", "crm_portal": "111",
            "at": "2026-11-10T11:00:00+00:00"}]
    put(root, "live/commitments.jsonl", "\n".join(json.dumps(e) for e in reg) + "\n")
    snap = tmp_path / "tracker.json"
    snap.write_text(json.dumps({"items": [{"id": "crm:111:9001", "text": "Send the revised SOW", "status": "NOT_STARTED",
                                           "owner": "me", "project": "northpoint", "kind": "task"}]}), encoding="utf-8")
    st = open_store(root=root)
    run(st, root, tracker_path=str(snap))
    assert SE.resolve_key(st, "crm:111:9001") == ["c-4444444444"]
    assert st.one("SELECT count(*) FROM entity WHERE type='task'")[0] == 1


def test_edge_modality_rows(env):
    root = env
    put(root, "docs/one.md", "# One\n\nMaybe Globex blocks Northpoint next week.\n", mtime=ts("2026-11-05T09:00:00Z"))
    put(root, "docs/two.md", "# Two\n\nGlobex blocks Northpoint, confirmed.\n", mtime=ts("2026-11-12T09:00:00Z"))
    st = open_store(root=root)
    run(st, root)
    _claim_on(st, "docs/one.md", "Globex blocks Northpoint", subject="e-globex", rel="blocks", dst="e-northpoint", modality="proposal")
    _claim_on(st, "docs/two.md", "Globex blocks Northpoint", subject="e-globex", rel="blocks", dst="e-northpoint",
              modality="confirmation")
    assert st.one("SELECT count(*) FROM edge")[0] == 2

    def edges(**kw):
        return sorted((e["modality"], e["first_said_at"]) for e in SE.search(st, "Globex blocks Northpoint", **kw)["items"]
                      if e["kind"] == "edge")
    assert edges() == [("confirmation", "2026-11-12T09:00:00Z"), ("proposal", "2026-11-05T09:00:00Z")]
    assert edges(exclude=["docs/two.md"]) == [("proposal", "2026-11-05T09:00:00Z")]
    assert edges(exclude=["docs/one.md"]) == [("confirmation", "2026-11-12T09:00:00Z")]
    with st.conn:
        st.conn.execute("UPDATE source SET ext_id='twodoc' WHERE uri='docs/two.md'")
    IG.apply_state(st, revoked=["twodoc"])
    assert edges() == [("proposal", "2026-11-05T09:00:00Z")]
    IG.apply_state(st, revoked=[])
    IG.write_override(st, {"target": "edge", "key": S.edge_key("e-globex", "blocks", "e-northpoint", "confirmation"),
                           "field": "reject", "at": "2026-11-20T00:00:00Z"})
    assert edges() == [("proposal", "2026-11-05T09:00:00Z")]
    CLI.rebuild(st, str(root), quiet_s=0, voices={})
    _claim_on(st, "docs/one.md", "Globex blocks Northpoint", subject="e-globex", rel="blocks", dst="e-northpoint", modality="proposal")
    _claim_on(st, "docs/two.md", "Globex blocks Northpoint", subject="e-globex", rel="blocks", dst="e-northpoint",
              modality="confirmation")
    assert edges() == [("proposal", "2026-11-05T09:00:00Z")]                               # the reject survives a rebuild
    with st.conn:
        st.conn.execute("DELETE FROM pm_override")
    assert edges(as_of="2026-11-08T00:00:00Z") == [("proposal", "2026-11-05T09:00:00Z")]


def test_one_edge_shared_and_client_file(env):
    root = env
    two_clients(root)
    put(root, "pacenote/answer-cards.md", "# Cards\n\nThe ibis service depends on the SSO gateway.\n")
    put(root, "docs/acme/ibis.md", "# Ibis\n\nThe ibis service depends on the SSO gateway.\n")
    put(root, "pacenote/notes.md", "# Notes\n\nThe crane service depends on the SSO gateway.\n")
    st = open_store(root=root)
    run(st, root)
    for uri in ("pacenote/answer-cards.md", "docs/acme/ibis.md"):
        _claim_on(st, uri, "The ibis service depends on the SSO gateway", subject="e-ibis", rel="depends_on", dst="e-ssogw")
    _claim_on(st, "pacenote/notes.md", "The crane service depends on the SSO gateway", subject="e-crane", rel="depends_on",
              dst="e-ssogw")
    assert st.one("SELECT count(*) FROM edge WHERE src_id='e-ibis'")[0] == 1
    sc = acme(root)
    g = dict(sc, client="globex", others=["acme"])
    get = lambda s: {e["src"] for e in SE.search(st, "service depends SSO gateway", scope=s)["items"] if e["kind"] == "edge"}
    assert get(sc) == {"e-ibis", "e-crane"}
    assert get(g) == {"e-crane"}


def test_pm_edits_survive_a_rebuild(env):
    root = env
    reg = [{"v": 1, "op": "add", "id": "c-3333333333", "text": "send the egret plan", "owner": "me", "series": "|x",
            "call": "2026-11-10-1000-x.md", "created": "2026-11-10T10:00:00+00:00"},
           {"v": 1, "op": "edit", "id": "c-3333333333", "text": "send the egret plan v2", "at": "2026-11-11T10:00:00+00:00"}]
    put(root, "live/commitments.jsonl", "\n".join(json.dumps(e) for e in reg) + "\n")
    put(root, "docs/a.md", "# A\n\nThe egret API is slow. Maybe the egret API blocks the launch.\n")
    st = open_store(root=root)
    run(st, root)

    def facts():
        _claim_on(st, "docs/a.md", "The egret API is slow", subject="e-egret", value="slow")
        _claim_on(st, "docs/a.md", "the egret API blocks the launch", subject="e-egret", rel="blocks", dst="e-launch",
                  modality="proposal")
    facts()
    nkey = st.one("SELECT nkey FROM claim WHERE value='slow'")[0]
    IG.write_override(st, {"target": "claim", "key": nkey, "field": "reject", "at": "2026-11-12"})
    IG.write_override(st, {"target": "edge", "key": S.edge_key("e-egret", "blocks", "e-launch", "proposal"), "field": "reject",
                           "at": "2026-11-12"})
    for _ in range(2):
        assert not [i for i in SE.search(st, "egret API")["items"] if i["kind"] in ("claim", "edge")]
        assert st.one("SELECT name FROM entity WHERE id='c-3333333333'")[0] == "send the egret plan v2"
        assert st.one("SELECT count(*) FROM pm_override WHERE via='register' AND field='edit'")[0] == 1
        CLI.rebuild(st, str(root), quiet_s=0, voices={})
        facts()


def test_purge_after_revoke_and_vacuum(env):
    root = env
    put(root, "live/google/google-file-{}.md".format(GID[:16]), "# G\n\nThe ibex budget is 90 hours.\n")
    put(root, "docs/a.md", "# A\n\nThe ibex team is three people.\n")
    st = open_store(root=root)
    run(st, root)
    S.apply_extraction(st, rev_of(st, "live/google/google-file-{}.md".format(GID[:16]))["id"], {
        "entities": [{"ref": "n1", "type": "topic", "name": "ibex budget", "quote": "The ibex budget"}],
        "claims": [{"subject": "n1", "predicate": "value", "value": "90 hours", "modality": "statement",
                    "quote": "The ibex budget is 90 hours"}]})
    _claim_on(st, "docs/a.md", "The ibex team is three people", subject="e-ibexteam", value="three")
    IG.apply_state(st, revoked=[GID])
    rep = CLI.vacuum(st)
    assert rep["claims"] == 1
    assert st.one("SELECT count(*) FROM claim WHERE id NOT IN (SELECT target_id FROM evidence WHERE target='claim')")[0] == 0
    assert st.one("SELECT count(*) FROM alias WHERE origin='llm'")[0] == 0
    t = texts(SE.search(st, "ibex budget team"))
    assert "90 hours" not in t and "three people" in t


def test_vacuum_command_applies_a_queued_revoke_first(env):
    root = env
    g = "live/google/google-file-{}.md".format(GID[:16])
    put(root, g, "# G\n\nThe ibex budget is 90 hours.\n")
    st = open_store("p-v", root)
    run(st, root)
    st.append_request("revoked", {"revoked": [GID]}, "r-v")     # queued while a pass held the lock
    assert CLI.main(["vacuum", "--project-uid", "p-v", "--root", str(root)]) == 0
    assert st.one("SELECT state FROM source WHERE uri=?", (g,))[0] == "revoked"
    assert st.one("SELECT count(*) FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri=?", (g,))[0] == 0


def test_vacuum_drops_a_claim_and_edge_the_revoked_file_shares_with_another(env):
    root = env
    g = "live/google/google-file-{}.md".format(GID[:16])
    put(root, g, "# G\n\nThe ibex sync is blocked.\n")
    put(root, "docs/a.md", "# A\n\nThe ibex sync is blocked.\n")
    put(root, "docs/b.md", "# B\n\nThe heron report is late.\n")
    st = open_store(root=root)
    run(st, root)
    for uri in (g, "docs/a.md"):
        _claim_on(st, uri, "The ibex sync is blocked", subject="e-ibex", value="blocked")
        _claim_on(st, uri, "The ibex sync is blocked", subject="e-ibex", rel="blocks", dst="e-sync")
    _claim_on(st, "docs/b.md", "The heron report is late", subject="e-heron", value="late")
    assert st.one("SELECT count(*) FROM claim WHERE subject_id='e-ibex'")[0] == 1      # one claim, two files quote it
    facts = lambda q: [i for i in SE.search(st, q)["items"] if i["kind"] in ("claim", "edge")]
    assert len(facts("ibex sync blocked")) == 2
    IG.apply_state(st, revoked=[GID])
    assert facts("ibex sync blocked") == []
    rep = CLI.vacuum(st)
    assert rep["claims"] == 1 and rep["edges"] == 1
    assert st.one("SELECT count(*) FROM claim WHERE subject_id='e-ibex'")[0] == 0
    assert st.one("SELECT count(*) FROM edge WHERE src_id='e-ibex'")[0] == 0
    assert st.one("SELECT count(*) FROM evidence ev WHERE (ev.target='claim' AND ev.target_id NOT IN (SELECT id FROM"
                  " claim)) OR (ev.target='edge' AND ev.target_id NOT IN (SELECT id FROM edge))")[0] == 0
    assert facts("ibex sync blocked") == []
    assert [i["value"] for i in facts("heron report late")] == ["late"]


def test_register_item_before_a_rename_joins_one_after(env):
    root = env
    a = {"v": 1, "op": "add", "id": "c-4444444444", "text": "fix the tern export", "owner": "Mike", "project": "acme-old",
         "series": "|x", "call": "2026-11-03-1000-x.md", "created": "2026-11-03T10:00:00+00:00", "notion_page": ""}
    b = dict(a, id="c-5555555555", project="acme-new", call="2026-11-10-1000-x.md", created="2026-11-10T10:00:00+00:00")
    link = lambda cid: {"v": 1, "op": "link", "id": cid, "notion_page": NID, "at": "2026-11-11T10:00:00+00:00"}
    put(root, "live/commitments.jsonl", "\n".join(json.dumps(e) for e in (a, b, link(a["id"]), link(b["id"]))) + "\n")
    st = S.Store.open(uid="p-r", root=str(root), slug="acme-old")
    run(st, root)
    assert st.one("SELECT merged_into FROM entity WHERE id='c-5555555555'")[0] is None      # two projects: no join
    st2 = S.Store.open(uid="p-r2", root=str(root), slug="acme-old")
    IG.ingest(st2, str(root), quiet_s=0, voices={}, slug="acme-new")
    assert st2.meta_json("slug_history") == ["acme-old", "acme-new"]
    assert st2.one("SELECT merged_into FROM entity WHERE id='c-5555555555'")[0] == "c-4444444444"


def test_reader_during_a_write_sees_the_previous_revision(env):
    root = env
    put(root, "docs/a.md", "# A\n\nThe puffin count is 10.\n")
    st = open_store(root=root)
    run(st, root)
    w = S.Store.open(uid="p-1")
    w.conn.execute("BEGIN IMMEDIATE")
    w.conn.execute("UPDATE revision SET superseded_at='2026-11-24T00:00:00Z'")
    w.conn.execute("INSERT INTO revision(source_id, content_hash, valid_from, observed_at) VALUES (1, 'x', 'a', 'b')")
    code = ("import sys; sys.path.insert(0, {m0!r}); from livetips_m0.evidence import store as S, search as SE;"
            "r = S.Store.open(path={p!r}, readonly=True);"
            "print(' '.join(i['text'] for i in SE.search(r, 'puffin count', mode='live')['items']))").format(
        m0=str(M0), p=str(w.path))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    w.conn.rollback()
    assert "count is 10" in out.stdout, out.stderr


def test_live_mode_refuses_query_vectors_and_stays_fast(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_SUPPORT_DIR", str(tmp_path / "support"))
    root = tmp_path / "northpoint"
    shutil.copytree(G0, root)
    st = open_store("g0", root)
    run(st, root)
    ro = S.Store.open(uid="g0", readonly=True)
    sc = CS.decide(str(root), "Acme weekly status", ["Acme"])
    with pytest.raises(ValueError):
        SE.search(ro, "item 13", scope=sc, mode="live", query_vec=[0.1])
    ms = sorted(SE.search(ro, q, scope=sc, mode="live")["ms"] for q in ["item 13", "SSO", "pilot users", "go-live"] * 10)
    assert ms[int(len(ms) * 0.95) - 1] <= 150


# ---------------------------------------------------------------- review round 1: moves re-mark, no hidden window

def test_move_into_and_out_of_a_client_word_remarks_the_path(env):
    root = env
    two_clients(root)
    put(root, "notes/budget.md", "Budget secret: the rate is 150 per hour.\n")
    st = open_store(root=root)
    run(st, root, valid_from="2026-11-10T09:00:00Z")
    put(root, "notes/budget.md", "Budget secret: the rate is 175 per hour.\n", mtime=OLD + 60)
    run(st, root, valid_from="2026-11-12T09:00:00Z")
    sc = acme(root)
    g = CS.decide(str(root), "Globex weekly", ["Globex"])
    assert "rate is 175" in passing(st, sc) and "rate is 150" in passing(st, sc, as_of="2026-11-11T09:00:00Z")
    os.rename(root / "notes/budget.md", root / "notes/globex-budget.md")
    assert run(st, root)["moved"] == 1
    assert CS.side(sc, str(root / "notes/globex-budget.md")) == "other"
    t = passing(st, sc) + texts(SE.search(st, "budget rate", scope=sc, budget=SE.Budget(k=50)))
    assert "rate is 1" not in t                                        # the current revision follows the new path
    assert "rate is 150" not in passing(st, sc, as_of="2026-11-11T09:00:00Z")   # and so does the superseded one
    assert {r[0] for r in st.q("SELECT sn.client_set FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id JOIN source s"
                                " ON s.id=rv.source_id WHERE s.uri='notes/globex-budget.md'")} == {"globex"}
    parity(st, sc)
    parity(st, g)
    os.rename(root / "notes/globex-budget.md", root / "notes/budget.md")
    assert run(st, root)["moved"] == 1
    assert "rate is 175" in passing(st, sc) and "rate is 150" in passing(st, sc, as_of="2026-11-11T09:00:00Z")
    assert not st.q("SELECT 1 FROM snippet_mark WHERE kind='path' AND name='globex' AND snippet_id IN (SELECT sn.id FROM"
                    " snippet sn JOIN revision rv ON rv.id=sn.revision_id JOIN source s ON s.id=rv.source_id"
                    " WHERE s.uri='notes/budget.md')")
    parity(st, sc)
    parity(st, g)


def test_hidden_and_revoked_are_never_visible_during_a_pass_or_a_rebuild(env):
    root = env
    put(root, "live/2026-11-05-acme-weekly/2026-11-05-1000-acme-weekly.md",
        "# Live transcript\n\n- Start: 2026-11-05 10:00:00\n\n[10:00:10] Mike (en): the hidden merger codename is Bluebird.\n")
    put(root, "live/google/google-file-{}.md".format(GID[:16]), "# G\n\nThe shared codename is Kestrel.\n")
    put(root, "zz/a.md", "# A\n\nFiller about the codename list.\n")
    put(root, "zz/b.md", "# B\n\nMore filler about the codename list.\n")
    st = open_store(root=root)
    emb = StubEmbed()
    seen = []

    def between():
        r = S.Store.open(uid="p-1", readonly=True)
        t = texts(SE.search(r, "Bluebird Kestrel codename", mode="live", budget=SE.Budget(k=50)))
        seen.append(("Bluebird" in t, "Kestrel" in t))

    def check():
        assert seen and seen == [(False, False)] * len(seen)
        assert not [x for x in emb.texts if "Bluebird" in x or "Kestrel" in x]
        assert {r[0] for r in st.q("SELECT state FROM source WHERE uri LIKE 'live/%'")} == {"excluded", "revoked"}
        seen.clear()

    hide = {"hidden_calls": ["2026-11-05-1000-acme-weekly.md"], "revoked": [GID]}
    run(st, root, embedder=emb, between=between, **hide)                   # a first index
    check()
    CLI.rebuild(st, str(root), quiet_s=0, voices={}, embedder=emb, between=between, **hide)
    check()
    CLI.rebuild(st, str(root), quiet_s=0, voices={}, embedder=emb, between=between)   # no lists: the app's states stay
    check()
    # the call's notes arrive later in a pass with no lists: born excluded like the rest of the call
    put(root, "live/2026-11-05-acme-weekly/2026-11-05-1000-acme-weekly.notes.md", "# Notes\n\n### TL;DR\nBluebird again.\n")
    run(st, root, embedder=emb, between=between)
    check()
    # leaving the lists makes them searchable and the next pass embeds them
    run(st, root, embedder=emb, hidden_calls=[], revoked=[])
    run(st, root, embedder=emb)
    t = texts(SE.search(st, "Bluebird Kestrel codename", budget=SE.Budget(k=50)))
    assert "Bluebird" in t and "Kestrel" in t
    assert [x for x in emb.texts if "Bluebird" in x] and [x for x in emb.texts if "Kestrel" in x]
    assert st.one("SELECT count(*) FROM snippet sn JOIN revision rv ON rv.id=sn.revision_id WHERE rv.superseded_at IS NULL"
                  " AND sn.vec IS NULL")[0] == 0


def test_notion_page_archived_by_the_mirror_is_revoked_not_deleted(env):
    """PB-88 r7 (owner decision 24.11, option a): the mirror moves a page it can no longer read to _notion/_archived/
    and the app passes its id as revoked: the source is revoked (strict deny), not deleted, and a page shared again
    (its file live, its id off the list) is active again."""
    root = env
    page = "_notion/tasks/2026-10-05 Falcon budget [{}].md".format(NID)
    put(root, page, "# Falcon budget\n\nThe falcon budget is frozen.\n")
    st = open_store(root=root)
    run(st, root)
    assert st.one("SELECT state FROM source WHERE uri=?", (page,))[0] == "active"
    arch = Path(root) / "_notion/_archived/tasks" / Path(page).name
    arch.parent.mkdir(parents=True)
    os.replace(Path(root) / page, arch)
    run(st, root, revoked=[NID])
    assert st.one("SELECT state FROM source WHERE uri=?", (page,))[0] == "revoked"
    assert "falcon" not in texts(SE.search(st, "falcon budget")).lower()
    os.replace(arch, Path(root) / page)
    run(st, root, revoked=[])
    assert st.one("SELECT state FROM source WHERE uri=?", (page,))[0] == "active"
