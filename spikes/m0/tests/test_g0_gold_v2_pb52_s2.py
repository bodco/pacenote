"""PB-52 G2 S2: G0 gold v2, the scale set, variants B and C (docs/specs/PB-52-g2-evidence-store.md section 17, S2).

One test (or a small group) per acceptance line, plus the segmenter of section 8.2 and the query_vec candidate
source of section 11.2 that S2 needs. Invented data only (Mike, Emily, Sarah, David, Kevin, Brian; Acme, Globex,
Initech, Northpoint)."""
import hashlib
import importlib.util
import json
import socket
from pathlib import Path

import pytest

from livetips_m0.evidence import ingest as IG
from livetips_m0.evidence import search as SE
from livetips_m0.evidence import segments as SG
from livetips_m0.evidence import store as S
from livetips_m0.sheet import lookup as LK

G0 = Path(__file__).resolve().parents[1] / "bench" / "g0"
TIMING = {"lookup_p50_ms", "lookup_p95_ms"}


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("g0_run_s2", G0 / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def results(bench):
    """A, B and C on the small and the scale set, once for the module."""
    return {v: bench.run(v) for v in ("A", "B", "C")}


@pytest.fixture
def small(bench, tmp_path):
    """The small gold v2 project and its store, built pass by pass (the run's timeline)."""
    with bench.store_env(tmp_path):
        ctx = bench.build_v2(tmp_path / "v2", bench.load_gold(), bench.load_gold(bench.GOLD2), scale=False)
        yield ctx
        ctx["store"].close()


# ---------------------------------------------------------------- acceptance 1: offline, both sets, new columns, A unchanged

NEW_COLUMNS = ("hop_answer_rate", "paraphrase_answer_rate", "timeline_recall", "evidence_recall", "quote_validity",
               "modality_accuracy", "derived_confirmed", "future_leaks", "excluded_leaks", "client_leaks",
               "shared_facts_kept")


def test_every_variant_runs_offline_on_both_sets_with_the_new_columns(results):
    for v, res in results.items():
        assert res["blocked_connections"] == 0, v
        sets = res["v2"]["sets"]
        assert set(sets) == {"small", "scale"}, v
        for s, modes in sets.items():
            run = modes["no_vectors"]
            for k in NEW_COLUMNS:
                assert k in run, (v, s, k)
            for k in ("live_p50_ms", "live_p95_ms", "prep_p50_ms", "prep_p95_ms"):
                assert k in run["latency"]
            for k in ("segments", "tokens", "changed_section_resegments", "revert_resegments"):
                assert k in run["index_cost"]
            if v != "A":            # B and C run twice; without the model folder the run with vectors is skipped
                assert "vectors" in modes
                assert "skipped" in modes["vectors"] or "future_leaks" in modes["vectors"]


def test_offline_blocks_every_host_but_loopback(bench):
    with bench.offline() as blocked:
        with pytest.raises(OSError):
            socket.create_connection(("192.0.2.10", 443), timeout=0.2)
    assert blocked == ["192.0.2.10"]


def test_a_existing_columns_equal_the_last_committed_a_result(bench, results):
    last = json.loads((G0 / "results" / "A-2026-11-24-3.json").read_text())     # the newest A result on main before S2
    mine = results["A"]["totals"]
    assert {k: v for k, v in mine.items() if k not in TIMING} == {k: v for k, v in last["totals"].items() if k not in TIMING}
    assert results["A"]["fixture_sha"] == last["fixture_sha"]          # gold v2 files are outside the v1 hash
    assert results["A"]["gold_sha"] == last["gold_sha"]


def test_b_and_c_meet_the_s2_gate(results):
    for v in ("B", "C"):
        gate = results[v]["v2"]["gate"]
        assert gate["pass"], (v, [k for k, x in gate["checks"].items() if x is False])
        scale = results[v]["v2"]["sets"]["scale"]["no_vectors"]
        assert scale["latency"]["live_p95_ms"] <= 150


# ---------------------------------------------------------------- acceptance 2: every row says who should pass it and why

def test_every_row_states_the_expected_variant_and_why(bench):
    gold2 = bench.load_gold(bench.GOLD2)
    assert bench.check_v2_invariants(gold2, bench.load_gold()) == []
    for r in gold2["rows"]:
        assert set(r["expected"]) == {"A", "B", "C"} and r["why"].strip(), r["id"]
    broken = dict(gold2, rows=[dict(gold2["rows"][0], why="")] + gold2["rows"][1:])
    assert any("why" in b for b in bench.check_v2_invariants(broken, bench.load_gold()))


def test_multi_hop_quotes_share_no_word_with_the_question(bench):
    gold2 = bench.load_gold(bench.GOLD2)
    hop = next(r for r in gold2["rows"] if r["kind"] == "hop")
    cheat = dict(hop, expect=hop["expect"] + [{"any": ["blocking the pilot launch"]}])
    rows = [cheat if r is hop else r for r in gold2["rows"]]
    assert any("shares" in b for b in bench.check_v2_invariants(dict(gold2, rows=rows), bench.load_gold()))


# ---------------------------------------------------------------- acceptance 3: what the leak metrics count

def test_future_leak_counts_state_and_claim_values_after_as_of(bench, small):
    gold2 = bench.load_gold(bench.GOLD2)
    row = next(r for r in gold2["rows"] if r["id"] == "t2-task-open-nov17")
    ent = {"id": "c-cd6930cc17", "name": "send Mike the updated rollout checklist", "status": "done", "aliases": []}
    got = bench.judge(bench.variant("C"), small, gold2, row, {"items": [], "entities": [ent]})
    assert "status:c-cd6930cc17=done" in got["leaks"]["future"] and got["status"] == "fail"
    claim = {"kind": "claim", "text": "x", "source": "docs/acme/pilot-launch-plan.md", "said_at": "2026-11-10T00:00:00Z",
             "quotes": [{"quote": "x", "uri": "docs/acme/pilot-launch-plan.md", "said_at": "2026-11-19T12:00:00Z"}]}
    got = bench.judge(bench.variant("C"), small, gold2, row, {"items": [claim], "entities": []})
    assert any(x.startswith("item:") and "2026-11-19" in x for x in got["leaks"]["future"])


def test_excluded_leak_counts_entity_names_aliases_and_topic_sentences(bench, small):
    gold2 = bench.load_gold(bench.GOLD2)
    row = next(r for r in gold2["rows"] if r["id"] == "x2-deleted")
    C = bench.variant("C")
    by_name = {"id": "d-1", "name": "The dashboard uses the teal palette.", "status": None, "aliases": []}
    assert "teal palette" in bench.judge(C, small, gold2, row, {"items": [], "entities": [by_name]})["leaks"]["excluded"]
    by_alias = {"id": "e-1", "name": "vendor", "status": None, "aliases": ["bluebird analytics"]}
    assert "bluebird analytics" in bench.judge(C, small, gold2, row, {"items": [], "entities": [by_alias]})["leaks"]["excluded"]
    topic = {"kind": "topic", "text": "The penetration test window opens on March 3.", "source": "", "said_at": None}
    assert "penetration test window" in bench.judge(C, small, gold2, row, {"items": [topic], "entities": []})["leaks"]["excluded"]


def test_a_row_from_a_denied_source_is_an_excluded_leak_even_without_its_phrase(bench, small):
    gold2 = bench.load_gold(bench.GOLD2)
    row = next(r for r in gold2["rows"] if r["id"] == "x2-deleted")
    item = {"kind": "snippet", "id": 0, "text": "nothing unique", "source": "docs/acme/vendor-shortlist.md"}
    got = bench.judge(bench.variant("A"), small, gold2, row, {"items": [item], "entities": []})
    assert "item:docs/acme/vendor-shortlist.md" in got["leaks"]["excluded"]


# ---------------------------------------------------------------- acceptance 4: fixture hits and misses, stale on a chunker change

def _record(bench, ctx, folder: Path) -> int:
    """A fixture file for every segment the replay will look up (items empty: the loader is what is tested)."""
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for r in ctx["store"].q("SELECT id FROM revision WHERE extract_state='pending'"):
        for seg in SG.of_revision(ctx["store"], r["id"]):
            (folder / (seg["seg_hash"] + ".json")).write_text(json.dumps({"extractor": "test", "items": {}}))
            n += 1
    return n


def test_fixture_hits_then_a_chunker_change_makes_the_run_stale(bench, tmp_path, monkeypatch):
    gold, gold2 = bench.load_gold(), bench.load_gold(bench.GOLD2)
    fx = tmp_path / "extract"
    with bench.store_env(tmp_path):
        ctx = bench.build_v2(tmp_path / "a", gold, gold2, scale=False)
        n = _record(bench, ctx, fx)
        rep = bench.replay_fixture(ctx, fx, small=True)
        ctx["store"].close()
    assert n > 0 and rep["hits"] == n and rep["misses"] == 0 and rep["state"] == "ok"
    # the whole C run on that fixture: hits counted, no stale failure
    monkeypatch.setattr(bench, "EXTRACT", fx)
    res = bench.run("C", sets=("small",))
    assert res["v2"]["sets"]["small"]["no_vectors"]["fixture"]["misses"] == 0
    # a test copy with another chunker: the same fixture misses, the run fails as "fixture stale"
    real = LK.chunks_at
    monkeypatch.setattr(LK, "chunks_at", lambda text, universe=(), width=900: real(text, universe, width=120))
    with pytest.raises(SystemExit, match="fixture stale"):
        bench.run("C", sets=("small",))


def test_no_fixture_recorded_is_not_stale(bench, small, tmp_path):
    rep = bench.replay_fixture(small, tmp_path / "none", small=True)
    assert rep["state"] == "not_recorded" and rep["hits"] == 0 and rep["misses"] > 0


# ---------------------------------------------------------------- acceptance 5: the scale set

def test_scale_set_is_reproducible_and_its_extraction_is_marked_generated(bench, results):
    a_files, a_gen = bench.scale_set(bench.SEED)
    b_files, b_gen = bench.scale_set(bench.SEED)
    blob = lambda f: "".join(k + f[k] for k in sorted(f)).encode("utf-8")      # noqa: E731
    assert blob(a_files) == blob(b_files) and json.dumps(a_gen, sort_keys=True) == json.dumps(b_gen, sort_keys=True)
    assert blob(bench.scale_set(bench.SEED + 1)[0]) != blob(a_files)
    size = sum(len(t.encode("utf-8")) for t in a_files.values())
    assert 1_800_000 <= size <= 2_600_000
    assert sum(1 for k in a_files if k.startswith("docs/")) == 40 and sum(1 for k in a_files if k.startswith("live/")) == 10
    assert all(g["generated"] is True and g["seed"] == bench.SEED for g in a_gen.values())
    for v in ("A", "B", "C"):
        scale = results[v]["v2"]["sets"]["scale"]["no_vectors"]
        assert scale["generated_extraction"] == {"docs": 40, "gated": False}
        assert "generated_extraction" not in results[v]["v2"]["sets"]["small"]["no_vectors"]
    assert results["C"]["v2"]["sets"]["scale"]["no_vectors"]["fixture"]["generated_docs"] == 40


# ---------------------------------------------------------------- acceptance 6: needs, and what C does not evaluate yet

def test_needs_on_every_row_a_b_everywhere_c_only_where_its_code_is(bench, results):
    gold2 = bench.load_gold(bench.GOLD2)
    needs = {r["id"]: r["needs"] for r in gold2["rows"]}
    assert set(needs.values()) == {"S2", "S3", "S4"}
    for v in ("A", "B"):
        run = results[v]["v2"]["sets"]["small"]["no_vectors"]
        assert run["rows_not_evaluated"] == 0 and len(run["rows"]) == len(needs)
    run = results["C"]["v2"]["sets"]["small"]["no_vectors"]
    for row in run["rows"]:
        if needs[row["id"]] in bench.READY:
            assert row["status"] in ("pass", "fail"), row["id"]
        else:
            assert row["status"] == "not_evaluated" and row["missing"].startswith(needs[row["id"]]), row["id"]
    assert run["rows_evaluated"] == sum(1 for n in needs.values() if n == "S2")
    # items 1, 2, 3 and 5 are S3 or S4: C's rates over them are empty, never a pass or a fail
    assert run["hop_answer_rate"]["of"] == 0 and run["paraphrase_answer_rate"]["of"] == 0
    assert run["timeline_recall"] is None
    item_needs = {(r["item"], r["needs"]) for r in gold2["rows"]}
    assert {(1, "S3"), (2, "S3"), (3, "S4"), (5, "S3"), (4, "S2"), (4, "S3"), (4, "S4"), (6, "S2"), (6, "S3"),
            (6, "S4")} <= item_needs


# ---------------------------------------------------------------- acceptance 7: the mtime cases, the gold hash

def test_mtime_cases_run_through_utime_and_a_normal_pass(bench, tmp_path, monkeypatch):
    seen = []
    real = IG.ingest

    def spy(store, root, **kw):
        changed = sorted(p for p in ("live/2026-11-12-acme-staffing-review/2026-11-12-1500-acme-staffing-review.notes.md",
                                     "docs/acme/onboarding-pilot.md") if (Path(root) / p).exists())
        seen.append((kw.get("valid_from"), changed))
        return real(store, root, **kw)
    monkeypatch.setattr(IG, "ingest", spy)
    with bench.store_env(tmp_path):
        ctx = bench.build_v2(tmp_path / "v2", bench.load_gold(), bench.load_gold(bench.GOLD2), scale=False)
        st = ctx["store"]
        rows = {}
        for uri in ("live/2026-11-12-acme-staffing-review/2026-11-12-1500-acme-staffing-review.notes.md",
                    "docs/acme/onboarding-pilot.md"):
            rows[uri] = [r["valid_from"] for r in st.q("SELECT rv.valid_from FROM revision rv JOIN source s ON"
                                                       " s.id=rv.source_id WHERE s.uri=? ORDER BY rv.id", (uri,))]
        st.close()
    # the passes that brought the mtime-case states had no valid_from (passes with it exist for the other files)
    assert any(vf for vf, _c in seen)
    assert rows["live/2026-11-12-acme-staffing-review/2026-11-12-1500-acme-staffing-review.notes.md"] == [
        S.utc("2026-11-12T16:10:00Z"), S.utc("2026-11-15T09:00:00Z")]
    assert rows["docs/acme/onboarding-pilot.md"] == [S.utc("2026-11-19T08:30:00Z"), S.utc("2026-11-19T16:45:00Z")]
    states = [(vf, c) for vf, c in seen if c]
    first_notes = next(i for i, (_vf, c) in enumerate(states) if c and c[-1].startswith("live/"))
    assert states[first_notes][0] is None


def test_results_hold_the_gold_sha256(bench, results):
    want = hashlib.sha256((G0 / "gold_v2.yaml").read_bytes()).hexdigest()
    for v, res in results.items():
        assert res["v2"]["gold_sha256"] == want and len(want) == 64


def test_replay_rows_hold_on_content_time(results):
    """The gold the mtime cases and the revert stand for: B and C pass them, A (no time) fails the past ones."""
    for v in ("B", "C"):
        rows = {r["id"]: r for r in results[v]["v2"]["sets"]["small"]["no_vectors"]["rows"]}
        for rid in ("t5-notes-as-of-y", "t5-notes-as-of-x", "t5-notes-now", "t6-same-day-at-call", "t4-budget-nov20",
                    "t1-golive-nov17"):
            assert rows[rid]["status"] == "pass", (v, rid, rows[rid]["leaks"])
    a = {r["id"]: r for r in results["A"]["v2"]["sets"]["small"]["no_vectors"]["rows"]}
    assert a["t5-notes-as-of-y"]["leaks"]["future"] == ["6 engineers"]
    cost = results["B"]["v2"]["sets"]["small"]["no_vectors"]["index_cost"]
    assert cost["changed_section_resegments"] == 1 and cost["revert_resegments"] == 0
    assert cost["move_new_revisions"] == 0 and cost["move_resegments"] == 0 and cost["moved_files"] == 2


def test_c_states_follow_as_of(results):
    rows = {r["id"]: r for r in results["C"]["v2"]["sets"]["small"]["no_vectors"]["rows"]}
    for rid in ("t2-task-open-nov17", "t2-task-done-now", "t3-decision-nov17", "t3-decision-now"):
        assert rows[rid]["state_ok"] is True and rows[rid]["status"] == "pass", rid


# ---------------------------------------------------------------- the segmenter (section 8.2) and query_vec (section 11.2)

def _row(i, text, heading="", cs=""):
    return {"id": i, "ord": i, "text": text, "heading": heading, "client_set": cs}


def test_segments_never_cross_a_heading_or_a_client_label_and_stay_under_6000():
    rows = [_row(0, "a", "Plan > Scope"), _row(1, "b", "Plan > Scope"), _row(2, "c", "Plan > Dates"),
            _row(3, "d", "Plan > Dates", "globex"), _row(4, "x" * 5000, "Plan > Owners"), _row(5, "y" * 1500, "Plan > Owners")]
    segs = SG.segments(rows, "file")
    assert [s["ords"] for s in segs] == [[0, 1], [2], [3], [4], [5]]
    notes = SG.segments(rows[:3], "notes")               # notes and transcripts are cut across headings
    assert [s["ords"] for s in notes] == [[0, 1, 2]] and notes[0]["heading"] == ""


def test_seg_hash_is_content_only():
    a = SG.segments([_row(0, "Phase two adds the billing portal.", "Plan > Scope")], "file")[0]
    b = SG.segments([_row(7, "Phase two adds the billing portal.", "Plan > Scope")], "file")[0]
    c = SG.segments([_row(0, "Phase two adds the billing portal.", "Plan > Scope", "acme")], "file")[0]
    assert a["seg_hash"] == b["seg_hash"] != c["seg_hash"]


def test_query_vec_adds_allowed_snippets_by_cosine_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_SUPPORT_DIR", str(tmp_path / "support"))
    monkeypatch.setenv("LT_EVIDENCE", "1")
    root = tmp_path / "proj"
    (root / "docs" / "acme").mkdir(parents=True)
    (root / "docs" / "acme" / "a.md").write_text("# A\n\nThe kestrel rollout waits for legal.\n")
    (root / "docs" / "acme" / "b.md").write_text("# B\n\nThe heron contract ends in May.\n")
    (root / "live").mkdir()
    st = S.Store.open(uid="qv", root=str(root))

    def embed(texts):
        return [[1.0, 0.0] if "kestrel" in t else [0.0, 1.0] for t in texts]
    IG.ingest(st, str(root), embedder=embed, voices={}, quiet_s=0)
    plain = SE.search(st, "approval", kinds=("snippet",))
    assert plain["items"] == []
    res = SE.search(st, "approval", kinds=("snippet",), query_vec=[1.0, 0.0])
    assert [i["source"] for i in res["items"]] == ["docs/acme/a.md"]
    res = SE.search(st, "approval", kinds=("snippet",), query_vec=[1.0, 0.0], exclude=["docs/acme/a.md"])
    assert res["items"] == [] and not res["candidates"]
    with pytest.raises(ValueError):
        SE.search(st, "approval", mode="live", query_vec=[1.0, 0.0])
    st.close()
