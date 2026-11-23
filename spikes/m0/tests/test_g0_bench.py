"""PB-50 GraphRAG G0: the synthetic call-context benchmark (bench/g0) runs offline, fast, and its fixture keeps the
cases it was built for. The scores themselves are not asserted: G0 is a baseline, later steps compare against it.
Invented data only (Mike, Emily, Sarah, David; Acme, Globex, Northpoint)."""
import importlib.util
import json
import re
import time
from pathlib import Path

import pytest

G0 = Path(__file__).resolve().parents[1] / "bench" / "g0"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("g0_run", G0 / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def result(bench):
    t = time.time()
    res = bench.run("A")
    res["_wall"] = time.time() - t
    return res


def test_fixture_invariants_hold(bench):
    gold = bench.load_gold()
    assert bench.check_invariants(gold) == []
    inv = gold["invariants"]["item13"]
    acme = (bench.PROJECT / inv["acme"]["file"]).read_text(encoding="utf-8")
    globex = (bench.PROJECT / inv["globex"]["file"]).read_text(encoding="utf-8")
    # two different item 13s: each call says item 13 and only its own meaning
    assert bench.has_phrase(acme, ["item 13"]) and bench.has_phrase(globex, ["item 13"])
    assert bench.has_phrase(acme, inv["acme"]["any"]) and not bench.has_phrase(acme, inv["globex"]["any"])
    assert bench.has_phrase(globex, inv["globex"]["any"]) and not bench.has_phrase(globex, inv["acme"]["any"])
    # the cross-series fact: waiting for the client in the weekly series, blocking SSO in the technical one
    cs = gold["invariants"]["cross_series"]
    weekly = (bench.PROJECT / cs["weekly"]["file"]).read_text(encoding="utf-8")
    assert bench.has_phrase(weekly, cs["weekly"]["any"]) and not bench.has_phrase(weekly, cs["technical"]["any"])
    assert bench.has_phrase((bench.PROJECT / cs["technical"]["file"]).read_text(encoding="utf-8"), cs["technical"]["any"])


def test_fixture_register_is_what_the_app_writes(bench):
    from livetips_m0 import commitments as CM
    items = CM.load(str(bench.PROJECT / "live"))
    assert len(items) >= 6 and all(i["id"] == CM.make_id(i["call"], i["owner"], i["text"]) for i in items)
    series = {i["series"].split("|")[-1] for i in items}
    assert {"acmeweeklystatus", "acmetechnicalsync", "globexweekly"} <= series
    # the Acme and the Globex item 13 are both open in the one project register
    assert sum(1 for i in items if "item 13" in i["text"]) >= 2


def test_harness_runs_offline_in_time_and_reports_every_metric(bench, result):
    assert result["_wall"] < 60
    assert result["blocked_connections"] == 0
    assert [t["id"] for t in result["targets"]] == sorted((t["id"] for t in bench.load_gold()["targets"]),
                                                          key=lambda i: re.search(r"\d{4}-\d\d-\d\d", i).group(0))
    for t in result["targets"]:
        assert t["prep"]["error"] is None and t["prep"]["passes"] >= 1 and t["prep"]["size"]["per_pass"] > 0
        assert t["prep"]["context_chars"] > 0 and t["live"]["total"] >= 3
    for k in ("prep_coverage", "prep_leaks", "live_answer_rate", "live_leaks", "lookup_p50_ms", "lookup_p95_ms",
              "duplicates", "wrong_identity", "item13_cross_client", "false_agreed"):
        assert result["totals"][k] is not None, k
    assert "| **All (A)** |" in bench.table(result)


def test_results_file_has_no_absolute_paths_and_compare_works(bench, result, tmp_path, monkeypatch):
    clean = {k: v for k, v in result.items() if not k.startswith("_")}
    monkeypatch.setattr(bench, "RESULTS", tmp_path)
    p = bench.save(clean)
    text = p.read_text(encoding="utf-8")
    assert "/Users/" not in text and "/var/folders" not in text and "/tmp/" not in text
    out = bench.compare(json.loads(text), json.loads(text))
    assert "| Prep coverage |" in out and "not comparable" not in out


def test_later_variants_are_not_faked(bench):
    with pytest.raises(SystemExit):
        bench.variant("C")
