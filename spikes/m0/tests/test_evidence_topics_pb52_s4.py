"""PB-52 G2 S4: topic summaries and global questions (docs/specs/PB-52-g2-evidence-store.md sections 8.7 and 17, S4).

One test (or a small group) per acceptance line of S4, plus the worker, CLI and staleness paths. Claims and edges come
from store.apply_extraction with hand-written items; summaries come from a fake `ask` or the fake provider of the S3
tests: no model, network or embed request leaves the Mac, no app or GUI program is started (L33). Invented data only
(Mike, Sarah, David, Emily; Acme, Globex).
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from livetips_m0.evidence import __main__ as CLI  # noqa: E402
from livetips_m0.evidence import extract as X  # noqa: E402
from livetips_m0.evidence import ingest as IG  # noqa: E402
from livetips_m0.evidence import search as SE  # noqa: E402
from livetips_m0.evidence import store as S  # noqa: E402
from livetips_m0.evidence import topics as T  # noqa: E402
from livetips_m0.evidence import worker as W  # noqa: E402
from test_evidence_extract_pb52_s3 import (  # noqa: E402,F401  (env is a fixture)
    FakeTransport, env, ingest, open_store, put, run_pass)
from test_evidence_store_pb52_s1 import ts  # noqa: E402

RULES = X.load_rules()
NID = "0123456789abcdef0123456789abcdef"     # an invented Notion page id


# ---------------------------------------------------------------- an invented two-client project

def project(root):
    put(root, "docs/acme/sso.md", "# SSO\nThe Acme SSO integration is blocked by item 13.\nItem 13 waits for Mike.\n"
                                  "Mike owns the Acme SSO integration.\n", mtime=ts("2026-11-03T09:00:00Z"))
    put(root, "docs/acme/notes-b.md", "# B\nAgain: the Acme SSO integration is blocked by item 13.\n"
                                      "The Acme dashboard uses the blue palette.\n", mtime=ts("2026-11-04T09:00:00Z"))
    put(root, "docs/globex/ledger.md", "# Ledger\nThe Globex ledger feed depends on the Globex data agreement.\n"
                                       "Sarah owns the Globex ledger feed.\n", mtime=ts("2026-11-05T09:00:00Z"))
    put(root, "live/2026-11-04-1000-acme-weekly.md", "[10:00:01] Mike (en): The Acme SSO integration is blocked by"
                                                     " item 13.\n", mtime=ts("2026-11-04T10:30:00Z"))
    put(root, "live/2026-11-06-1000-globex-weekly.md", "[10:00:01] Sarah (en): Hello.\n", mtime=ts("2026-11-06T10:30:00Z"))
    put(root, "_notion/Acme Review {}.md".format(NID), "# Review\nThe Acme SSO integration is blocked by item 13.\n",
        mtime=ts("2026-11-07T09:00:00Z"))


def rid(st, uri):
    return st.one("SELECT rv.id FROM revision rv JOIN source s ON s.id=rv.source_id WHERE s.uri=? AND rv.superseded_at"
                  " IS NULL", (uri,))[0]


def ent(name, etype="system", ref=None):
    return {"ref": ref or name, "type": etype, "name": name, "aliases": [], "quote": name}


def extract(st, uri, entities=(), claims=(), rels=()):
    items = {"entities": [ent(*e) if isinstance(e, tuple) else ent(e) for e in entities],
             "claims": [{"subject": s, "predicate": "value", "value": v, "modality": "statement", "quote": q,
                         "confidence": 0.9} for s, v, q in claims],
             "relations": [{"from": a, "rel": r, "to": b, "modality": "statement", "quote": q, "confidence": 0.9}
                           for a, r, b, q in rels]}
    return S.apply_extraction(st, rid(st, uri), items)


BLOCKED = "the Acme SSO integration is blocked by item 13"


def world(root):
    """Acme: SSO integration blocked by item 13 (stated in 4 sources), item 13 waits for Mike, Mike owns SSO, the blue
    palette (one file); Globex: the ledger feed depends on the data agreement, Sarah owns the feed."""
    project(root)
    st = open_store(root)
    ingest(st, root)
    extract(st, "docs/acme/sso.md", ["Acme SSO integration", ("Item 13", "task"), ("Mike", "person")],
            claims=[("Item 13", "waits for Mike", "Item 13 waits for Mike")],
            rels=[("Item 13", "blocks", "Acme SSO integration", "blocked by item 13"),
                  ("Mike", "owns", "Acme SSO integration", "Mike owns the Acme SSO integration")])
    for uri in ("docs/acme/notes-b.md", "live/2026-11-04-1000-acme-weekly.md", "_notion/Acme Review {}.md".format(NID)):
        extract(st, uri, ["Acme SSO integration"], claims=[("Acme SSO integration", "blocked by item 13", BLOCKED)])
    extract(st, "docs/acme/notes-b.md", ["Acme dashboard", "Acme SSO integration"],
            claims=[("Acme dashboard", "blue palette", "The Acme dashboard uses the blue palette")])
    extract(st, "docs/globex/ledger.md", ["Globex ledger feed", "Globex data agreement", ("Sarah", "person")],
            rels=[("Globex ledger feed", "depends_on", "Globex data agreement", "depends on the Globex data agreement"),
                  ("Sarah", "owns", "Globex ledger feed", "Sarah owns the Globex ledger feed")])
    return st


class Ask:
    """A fake summary model: one sentence per fact, citing it (or `answer(user)` when given); counts requests."""

    def __init__(self, answer=None):
        self.users = []
        self.answer = answer

    def __call__(self, user, cap):
        self.users.append(user)
        if self.answer:
            return {"text": self.answer(user)}
        sents = []
        for ln in user.split("\n"):
            if ln.startswith("f") and " | " in ln:
                ref, date, what = [x.strip() for x in ln.split(" | ")[:3]]
                sents.append({"text": "{}: {}".format(date, what), "cites": [ref]})
        return {"text": json.dumps({"sentences": sents})}


def topic_of(st, name):
    eid = S.entity_llm_id("system", name)
    r = st.one("SELECT topic_id FROM topic_member WHERE entity_id=?", (eid,))
    return r[0] if r else None


def summarise(st, at="2026-11-20T12:00:00Z", ask=None):
    ask = ask or Ask()
    T.detect(st)
    rep = T.summarise(st, ask, RULES, at=at)
    return ask, rep


def texts(res):
    return [s["text"] for t in res["topics"] for s in t["sentences"]]


def acme_scope(root):
    from livetips_m0 import client_scope as CS
    return CS.decide(str(root), "Acme weekly", ["Acme"])


# ---------------------------------------------------------------- 1. same input, same topics; never two clients

def test_same_input_same_topics_and_no_topic_mixes_two_clients(env, tmp_path):
    root = env
    st = world(root)
    T.detect(st)
    a = [(r["label"], r["client_set"], r["member_hash"]) for r in st.q("SELECT * FROM topic ORDER BY member_hash")]
    st2 = S.Store.open(path=tmp_path / "copy.sqlite", root=str(root))
    st2.conn.close()
    import shutil
    st.conn.execute("PRAGMA wal_checkpoint(FULL)")
    shutil.copy(st.path, tmp_path / "copy.sqlite")
    st2 = S.Store.open(path=tmp_path / "copy.sqlite")
    with st2.conn:
        st2.conn.execute("DELETE FROM topic")
    T.detect(st2)
    b = [(r["label"], r["client_set"], r["member_hash"]) for r in st2.q("SELECT * FROM topic ORDER BY member_hash")]
    assert a == b and len(a) == 2
    assert T.detect(st)["kept"] == 2                        # again on the same store: nothing changes
    sets = {r["client_set"] for r in st.q("SELECT client_set FROM topic")}
    assert sets == {"acme", "globex"}
    # no topic's facts come from a snippet of another client
    fs = T.facts(st)
    for t in st.q("SELECT * FROM topic"):
        _user, refs, _src = T.request_text(st, t["id"], fs)
        for k in refs.values():
            assert all(row["set"] in ("", t["client_set"]) for row in fs[k]["rows"])
    # an entity stated by both clients' files: its claim is in no topic (a topic never mixes two clients)
    put(root, "docs/globex/sso.md", "# SSO\nGlobex also uses the Acme SSO integration.\n", mtime=ts("2026-11-08T09:00:00Z"))
    ingest(st, root)
    extract(st, "docs/globex/sso.md", ["Acme SSO integration"],
            claims=[("Acme SSO integration", "blocked by item 13", "the Acme SSO integration")])
    assert not any(k[0] == "claim" and "blocked by item 13" in f["key"] for k, f in T.facts(st).items())


def test_communities_seeded_and_capped():
    import random
    rng = random.Random(7)
    adj = {}
    for i in range(95):                       # one dense cluster of 95 nodes: cut into parts of at most 40
        adj.setdefault("n{:03d}".format(i), {})
    names = sorted(adj)
    for a in names:
        for b in rng.sample(names, 6):
            if a != b:
                adj[a][b] = adj[a].get(b, 0) + 1.0
                adj[b][a] = adj[b].get(a, 0) + 1.0
    one, two = T.communities(adj), T.communities(json.loads(json.dumps(adj)))
    assert one == two
    assert all(len(g) <= T.CAP for g in one) and sorted(n for g in one for n in g) == names


# ---------------------------------------------------------------- 2. uncited sentences dropped; never evidence

def test_uncited_sentence_dropped_and_summary_never_evidence(env):
    root = env
    st = world(root)
    before = {"evidence": st.one("SELECT count(*) FROM evidence")[0], "alias": st.one("SELECT count(*) FROM alias")[0]}
    conf = {i["id"]: i["confirmations"] for i in SE.search(st, "SSO integration blocked")["items"] if i["kind"] == "claim"}

    def answer(user):
        return json.dumps({"sentences": [
            {"text": "The SSO integration was blocked by item 13.", "cites": ["f1"]},
            {"text": "Acme cancelled the whole project.", "cites": []},
            {"text": "Mike resigned.", "cites": ["f99"]},
            {"text": "No cites key at all."},
            "not an object"]})
    _ask, rep = summarise(st, ask=Ask(answer))
    rows = st.q("SELECT ts.text FROM topic_sentence ts JOIN topic t ON t.id=ts.topic_id WHERE t.client_set='acme'")
    assert [r[0] for r in rows] == ["The SSO integration was blocked by item 13."]
    assert rep["dropped"] >= 4
    assert st.one("SELECT count(*) FROM evidence")[0] == before["evidence"]          # no evidence row from a summary
    assert st.one("SELECT count(*) FROM alias")[0] == before["alias"]
    after = {i["id"]: i["confirmations"] for i in SE.search(st, "SSO integration blocked")["items"] if i["kind"] == "claim"}
    assert after == conf                                                              # never a confirmation
    assert all(i["kind"] != "topic" for i in SE.search(st, "SSO integration blocked")["items"])
    assert st.one("SELECT count(*) FROM topic_cite tc LEFT JOIN claim c ON tc.target='claim' AND c.id=tc.target_id"
                  " LEFT JOIN edge e ON tc.target='edge' AND e.id=tc.target_id WHERE c.id IS NULL AND e.id IS NULL")[0] == 0


def test_parse_failure_retried_once_then_invalid(env):
    root = env
    st = world(root)
    ask = Ask(lambda user: "not json")
    _a, rep = summarise(st, ask=ask)
    assert rep["invalid"] == 2 and rep["requests"] == 4                    # one repair retry per topic
    assert {r[0] for r in st.q("SELECT state FROM topic")} == {"invalid"}
    assert T.due(st) == []                                                 # re-tried only when the members change


# ---------------------------------------------------------------- 3. only changed topics re-summarised

def test_only_changed_or_stale_topics_resummarised(env):
    root = env
    st = world(root)
    ask, rep = summarise(st)
    assert rep["requests"] == 2
    ask2, rep2 = summarise(st)
    assert rep2["requests"] == 0                                            # nothing changed: no request
    acme, globex = topic_of(st, "Acme SSO integration"), topic_of(st, "Globex ledger feed")
    # a new fact on a member of the Globex topic: only that topic again
    put(root, "docs/globex/ledger.md", "# Ledger\nThe Globex ledger feed depends on the Globex data agreement.\n"
                                       "Sarah owns the Globex ledger feed.\nThe Globex ledger feed runs nightly.\n",
        mtime=ts("2026-11-09T09:00:00Z"))
    ingest(st, root)
    extract(st, "docs/globex/ledger.md", ["Globex ledger feed", "Globex data agreement", ("Sarah", "person")],
            claims=[("Globex ledger feed", "nightly", "The Globex ledger feed runs nightly")],
            rels=[("Globex ledger feed", "depends_on", "Globex data agreement", "depends on the Globex data agreement"),
                  ("Sarah", "owns", "Globex ledger feed", "Sarah owns the Globex ledger feed")])
    ask3, rep3 = summarise(st)
    assert rep3["requests"] == 1 and "Globex" in ask3.users[0]
    assert topic_of(st, "Globex ledger feed") == globex                     # the same topic row, re-summarised
    # a cited source changes state (the Notion page is revoked): only the Acme topic, marked stale by the trigger
    IG.apply_state(st, revoked=[NID])
    assert st.one("SELECT state FROM topic WHERE id=?", (acme,))[0] in ("stale", "pending")
    assert st.one("SELECT state FROM topic WHERE id=?", (globex,))[0] == "ready"
    ask4, rep4 = summarise(st)
    assert rep4["requests"] == 1 and "Acme" in ask4.users[0]
    assert "Review" not in ask4.users[0]                                     # the revoked page's rows are not read


def test_every_state_path_marks_cited_topics_stale(env):
    root = env
    st = world(root)
    summarise(st)
    acme = topic_of(st, "Acme SSO integration")
    state = lambda: st.one("SELECT state FROM topic WHERE id=?", (acme,))[0]     # noqa: E731
    IG.apply_state(st, hidden_calls=["2026-11-04-1000-acme-weekly.md"])          # hide
    assert state() == "stale"
    summarise(st)
    assert state() == "ready"
    IG.apply_state(st, hidden_calls=[])                                          # back to active: the summary
    T.detect(st)                                    # written while hidden cites nothing of it; its facts return
    assert state() == "pending"                     # with it, so the topic's member_hash changed
    summarise(st)
    assert state() == "ready"
    IG.apply_state(st, hidden_calls=[])             # the same list again: no state change, nothing stale
    assert state() == "ready"
    (root / "docs/acme/notes-b.md").unlink()                                     # deleted by an ingest pass
    ingest(st, root)
    assert state() == "stale"


# ---------------------------------------------------------------- 4. excluded, revoked, hidden or in exclude

def _two_sentences(user):
    """Sentence 1 cites the blocked claim (four sources), sentence 2 only Mike's ownership edge (one file)."""
    refs = {}
    for ln in user.split("\n"):
        if ln.startswith("f") and " | " in ln:
            ref, _d, what = [x.strip() for x in ln.split(" | ")[:3]]
            refs[what] = ref
    blocked = next(r for w, r in refs.items() if "blocked by item 13" in w and "->" not in w)
    owns = next(r for w, r in refs.items() if "owns" in w and "Mike" in w)
    return json.dumps({"sentences": [{"text": "The SSO integration was blocked by item 13.", "cites": [blocked]},
                                     {"text": "Mike owns the SSO integration.", "cites": [owns]}]})


def acme_only(st):
    with st.conn:              # the Globex topic is not part of these cases
        st.conn.execute("DELETE FROM topic WHERE client_set='globex'")


@pytest.mark.parametrize("how", ["excluded", "revoked", "hidden", "request_exclude"])
def test_summary_before_exclusion_returns_none_of_its_sentences(env, how):
    root = env
    st = world(root)
    T.detect(st)
    acme_only(st)
    T.summarise(st, Ask(_two_sentences), RULES, at="2026-11-20T12:00:00Z")
    q = "What happened with the Acme SSO integration?"
    full = texts(SE.ask_global(st, q, scope=acme_scope(root)))
    assert full == ["The SSO integration was blocked by item 13.", "Mike owns the SSO integration."]
    exclude = ()
    if how == "excluded":          # a PM exclusion of the transcript (the same class as a hidden call)
        with st.conn:
            st.conn.execute("UPDATE source SET state='excluded', state_reason='pm' WHERE uri=?",
                            ("live/2026-11-04-1000-acme-weekly.md",))
    elif how == "revoked":
        IG.apply_state(st, revoked=[NID])
    elif how == "hidden":
        IG.apply_state(st, hidden_calls=["2026-11-04-1000-acme-weekly.md"])
    else:
        exclude = ("docs/acme/notes-b.md",)
    res = SE.ask_global(st, q, scope=acme_scope(root), exclude=exclude)
    # the cited claim still has passing rows in docs/acme/sso.md, yet its sentence is not returned
    assert texts(res) == ["Mike owns the SSO integration."]
    # search: a denied row drops the claim (strict); an ignored row (request exclude) leaves it on its other rows
    blocked = [i for i in SE.search(st, "SSO integration blocked", scope=acme_scope(root), exclude=exclude)["items"]
               if i["kind"] == "claim" and i.get("value") == "blocked by item 13"]
    assert bool(blocked) == (how == "request_exclude")


def test_pending_hide_in_the_request_log_hides_the_sentence(env):
    root = env
    st = world(root)
    T.detect(st)
    acme_only(st)
    T.summarise(st, Ask(_two_sentences), RULES, at="2026-11-20T12:00:00Z")
    st.append_request("hidden_calls", {"hidden_calls": ["2026-11-04-1000-acme-weekly.md"]})
    assert texts(SE.ask_global(st, "Acme SSO integration")) == ["Mike owns the SSO integration."]


def test_topic_with_no_passing_sentence_is_not_returned(env):
    root = env
    st = world(root)
    T.detect(st)
    acme_only(st)
    def both_cite_blocked(user):
        s = json.loads(_two_sentences(user))["sentences"]
        s[1]["cites"] = s[0]["cites"]
        return json.dumps({"sentences": s})
    T.summarise(st, Ask(both_cite_blocked), RULES, at="2026-11-20T12:00:00Z")
    assert len(SE.ask_global(st, "What about item 13?", scope=acme_scope(root))["topics"]) == 1
    IG.apply_state(st, revoked=[NID])
    res = SE.ask_global(st, "What about item 13?", scope=acme_scope(root))
    assert res["topics"] == []                              # no passing sentence: the topic is not returned
    assert res["timeline"] and "blocked by item 13" not in [i.get("value") for i in res["timeline"]]
    assert any(i.get("value") == "waits for Mike" for i in res["timeline"])     # its members' passing facts are


# ---------------------------------------------------------------- 5. deleted rows are strict until re-summarised

def test_deleted_file_row_hides_the_sentence_until_resummarised(env):
    root = env
    st = world(root)
    T.detect(st)
    acme_only(st)
    T.summarise(st, Ask(_two_sentences), RULES, at="2026-11-20T12:00:00Z")
    q = "Acme SSO integration"
    (root / "docs/acme/notes-b.md").unlink()
    ingest(st, root)
    # the claim has a row in the deleted file and rows in other files: search still returns it (ignored rows) ...
    assert any(i["kind"] == "claim" and "blocked" in (i.get("value") or "") for i in SE.search(st, "SSO blocked")["items"])
    # ... but the sentence written from the deleted file is strict
    assert texts(SE.ask_global(st, q)) == ["Mike owns the SSO integration."]
    acme = topic_of(st, "Acme SSO integration")
    assert st.one("SELECT state FROM topic WHERE id=?", (acme,))[0] == "stale"
    T.detect(st)
    acme_only(st)
    rep = T.summarise(st, Ask(_two_sentences), RULES, at="2026-11-21T12:00:00Z")
    assert rep["summarised"] == 1
    assert texts(SE.ask_global(st, q)) == ["The SSO integration was blocked by item 13.", "Mike owns the SSO integration."]
    # the same claim with a file it was summarised from in the request's exclude: strict, its sentence is not returned
    assert texts(SE.ask_global(st, q, exclude=("live/2026-11-04-1000-acme-weekly.md",))) == \
        ["Mike owns the SSO integration."]


# ---------------------------------------------------------------- 6. as_of never returns a later summary

def test_ask_global_as_of_never_returns_a_later_summary(env):
    root = env
    st = world(root)
    T.detect(st)
    acme_only(st)
    T.summarise(st, Ask(_two_sentences), RULES, at="2026-11-20T12:00:00Z")
    before = SE.ask_global(st, "Acme SSO integration", as_of="2026-11-19T12:00:00Z")
    assert before["topics"] == []
    assert before["timeline"] and all(i["said_at"] <= "2026-11-19T12:00:00Z" for i in before["timeline"])
    assert texts(SE.ask_global(st, "Acme SSO integration", as_of="2026-11-20T12:00:00Z"))
    early = SE.ask_global(st, "Acme SSO integration", as_of="2026-11-03T12:00:00Z")
    assert early["topics"] == [] and all(i["said_at"] <= "2026-11-03T12:00:00Z" for i in early["timeline"])
    assert not any("palette" in json.dumps(i) for i in early["timeline"])     # Nov 4 file: not yet


def test_ask_global_timeline_is_dated_and_refuses_live(env):
    root = env
    st = world(root)
    summarise(st)
    res = SE.ask_global(st, "What happened with the Acme SSO integration?")
    dates = [i["said_at"] for i in res["timeline"]]
    assert dates == sorted(dates) and res["topics"][0]["sentences"][0]["said_at"]
    with pytest.raises(ValueError):
        SE.ask_global(st, "SSO", mode="live")
    assert all("ledger" not in json.dumps(t) for t in SE.ask_global(st, "Acme SSO integration",
                                                                     scope=acme_scope(root))["topics"])


def test_vacuum_removes_sentences_citing_a_revoked_fact(env):
    root = env
    st = world(root)
    summarise(st)
    IG.apply_state(st, revoked=[NID])
    CLI.vacuum(st)
    assert not st.one("SELECT 1 FROM topic_sentence WHERE text LIKE '%blocked by item 13%'")
    assert st.one("SELECT count(*) FROM topic_sentence ts LEFT JOIN topic_cite tc ON tc.sentence_id=ts.id"
                  " WHERE tc.sentence_id IS NULL")[0] == 0


# ---------------------------------------------------------------- the worker: eager, lazy, ceiling, call pause

def summary_answer(req):
    sents = [{"text": "{}: {}".format(*[x.strip() for x in ln.split(" | ")[1:3]]), "cites": [ln.split(" | ")[0]]}
             for ln in req["user"].split("\n") if ln.startswith("f") and " | " in ln]
    return {"text": json.dumps({"sentences": sents})}


def fake():
    """The S3 fake provider: extraction answers nothing new, summaries one dated sentence per fact."""
    return FakeTransport(lambda req: {"text": json.dumps({"entities": [], "claims": [], "relations": []})},
                         summarizer=summary_answer)


def test_pass_summarises_on_the_summary_model_inside_the_same_run(env):
    root = env
    st = world(root)
    tr = fake()
    r = run_pass(st, root, tr, route="api", topics="eager")
    assert r["status"] == "done" and r["topics"]["summarised"] == 2
    summ = tr.summaries
    assert len(summ) == 2 and {q["model"] for q in summ} == {W.models("api")["summary"]}
    assert all(q["max_tokens"] == RULES["summary_output_tokens"] for q in summ)
    assert st.one("SELECT count(*) FROM run WHERE kind='extract'")[0] == 1 and r["usd"] > 0
    led = [json.loads(x) for x in W.ledger_path().read_text().splitlines()]
    assert {x["model"] for x in led if x["job"] == "evidence"} >= {W.models("api")["summary"]}


def test_lazy_pass_sends_no_summary_and_topics_pass_does(env):
    root = env
    st = world(root)
    tr = fake()
    r = run_pass(st, root, tr, route="api", topics="lazy")
    assert r["topics"]["lazy"] and r["topics"]["due"] == 2
    assert tr.summaries == []
    r2 = W.topics_pass(st, str(root), {"route": "api", "poll_s": 0.05}, tr, sleep=lambda s: None)
    assert r2["status"] == "done" and r2["topics"]["summarised"] == 2


def test_summaries_stop_at_the_pass_ceiling_and_on_a_call(env, monkeypatch):
    root = env
    st = world(root)
    tr = fake()
    one = W.Pass(st, str(root), {"route": "api"}, tr).estimate("x" * 2000, T.prefix(RULES), W.models("api")["summary"],
                                                              RULES["summary_output_tokens"])
    r = run_pass(st, root, tr, route="api", topics="eager", ceilings={"usd": {"pass": one * 1.2}})
    assert r["status"] == "ceiling" and r["usd"] <= one * 1.2 + 1e-9
    monkeypatch.setattr(W, "call_active", lambda *a, **k: True)
    tr2 = fake()
    r2 = W.topics_pass(st, str(root), {"route": "api", "poll_s": 0.05}, tr2, sleep=lambda s: None)
    assert r2["status"] == "paused_call" and tr2.summaries == [] and tr2.requests == []


def test_flag_off_topics_and_global_refuse(env, monkeypatch, capsys):
    root = env
    world(root).close()
    monkeypatch.delenv("LT_EVIDENCE", raising=False)
    base = ["--project-uid", "p-1", "--root", str(root)]
    for cmd in (["topics", "--route", "api"], ["global", "--query", "SSO"]):
        assert CLI.main([cmd[0]] + base + cmd[1:]) == 2
        assert json.loads(capsys.readouterr().out)["error"] == "evidence_store is off"


def test_local_overflow_at_a_summary_sends_the_local_summary_model(env, monkeypatch):
    """The pass ceiling is crossed at the first summary request with local overflow on: that request and the rest go
    to the model on this Mac under its own name, never the cloud summary model's name on the local route."""
    monkeypatch.delenv("LT_LOCAL_MODEL", raising=False)
    root = env
    st = world(root)
    with st.conn:
        st.conn.execute("UPDATE revision SET extract_state='done'")       # nothing to extract: summaries only
    tr = fake()
    r = run_pass(st, root, tr, route="api", topics="eager", local_overflow=True,
                 ceilings={"usd": {"pass": 0.000001}, "minutes": {"pass": 60}})
    assert r["status"] == "done" and r["route"] == "local" and r["fallback_from"] == "api"
    assert tr.summaries and {(q["route"], q["model"]) for q in tr.summaries} == {("local", W.models("local")["summary"])}


def test_cli_topics_and_global(env, capsys):
    root = env
    st = world(root)
    st.close()
    base = ["--project-uid", "p-1", "--root", str(root)]
    T_ = fake()
    orig = W.ChildTransport
    W.ChildTransport = lambda: T_
    try:
        assert CLI.main(["topics"] + base + ["--route", "api"]) == 0
    finally:
        W.ChildTransport = orig
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "done" and out["run"]["topics"]["summarised"] == 2
    assert CLI.main(["global"] + base + ["--query", "Acme SSO integration", "--title", "Acme weekly",
                                         "--companies", "Acme"]) == 0
    g = json.loads(capsys.readouterr().out)
    assert g["topics"] and all("Globex" not in s["text"] for t in g["topics"] for s in t["sentences"])
    assert CLI.main(["global"] + base + ["--query", "SSO", "--mode", "live"]) == 1
    assert CLI.main(["status"] + base) == 0
    assert json.loads(capsys.readouterr().out.strip().split("\n")[-1])["topics"] == {"ready": 2}
