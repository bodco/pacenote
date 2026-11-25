"""PB-90 (CQ1 spec): the draft rules/quality.yaml is consistent with the analytics catalog it will be copied into (CQ4),
so the events it drafts would pass the server's checks, and its selection defaults are complete."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
Q = yaml.safe_load((ROOT / "rules/quality.yaml").read_text(encoding="utf-8"))
CAT = yaml.safe_load((ROOT / "rules/analytics.yaml").read_text(encoding="utf-8"))
SPEC = ROOT / "docs/specs/PB-90-cq1-call-quality.md"


def _type_known(t: str) -> bool:
    return t in CAT["types"] or t in CAT["enums"] or t in Q["new_enums"]


def test_drafted_events_use_catalog_types_only():
    for ev, params in Q["events"].items():
        # before CQ4 the event is absent; after CQ4 the catalog must carry exactly the draft
        assert ev not in CAT["events"] or CAT["events"][ev] == params, ev + " differs from the catalog"
        for p, t in params.items():
            assert _type_known(t), f"{ev}.{p}: unknown type {t}"
            assert CAT["types"].get(t, {}).get("kind") != "code", f"{ev}.{p}: a code needs a closed list, use an enum"


def test_new_enums_are_new_and_snake_case():
    for name, values in Q["new_enums"].items():
        assert name not in CAT["types"], name
        assert name not in CAT["enums"] or CAT["enums"][name] == values, name + " differs from the catalog"
        assert values and len(values) == len(set(values)), name
        for v in values:
            assert isinstance(v, str), f"{name}: {v!r} is not a string (quote yes, no, off in YAML)"
            assert re.fullmatch(r"[a-z0-9_]{1,40}", v), f"{name}: {v}"


def test_rubric_lists_match_event_enums():
    r = Q["rubric"]
    assert r["reasons"] == Q["new_enums"]["quality_reason"]
    assert r["correct"] == Q["new_enums"]["quality_correct"]
    assert set(Q["judge"]["judged_kinds"]) == set(Q["new_enums"]["quality_tip_kind"])
    assert Q["judge"]["chain"] is False


def test_useful_scale_matches_spec_and_fits_events():
    """The rubric scale is the one the spec and PRIVACY state, and useful_avg (mean x 20) fits its `score` type."""
    lo, hi = Q["rubric"]["useful"]["min"], Q["rubric"]["useful"]["max"]
    s = SPEC.read_text(encoding="utf-8")
    assert (lo, hi) == (1, 5)
    assert f"| `useful` | {lo} to {hi} |" in s
    assert f"| `useful` | small ({lo} to {hi}) |" in s
    assert f"useful ({lo} to {hi})" in s
    assert f"корисна (від {lo} до {hi})" in s
    assert "mean `useful` x 20" in s
    avg = CAT["types"][Q["events"]["call_quality"]["useful_avg"]]
    assert avg["min"] <= lo * 20 and hi * 20 <= avg["max"]


def _section(s: str, n: int) -> str:
    start = s.index(f"\n## {n}. ")
    return s[start:s.index(f"\n## {n + 1}. ", start)]


def test_skip_order_matches_spec_section_6():
    """The `why` column of section 6, top to bottom, is the order of quality_skip after `judged` and of the
    eligibility keys (the first failing rule is the reason, so the order is behaviour)."""
    rows = re.findall(r"^\| \d+ \|.*\| `([a-z_]+)` \|$", _section(SPEC.read_text(encoding="utf-8"), 6), re.M)
    assert len(rows) == 16
    skip = Q["new_enums"]["quality_skip"]
    assert skip[0] == "judged" and skip[1:1 + len(rows)] == rows
    key_for = {"needs_switch": "off", "needs_analytics": "analytics_off", "skip_call_types": "call_type",
               "min_minutes": "short", "brief_kinds": "no_brief", "min_tips_shown": "few_tips", "min_lines": "few_lines",
               "min_other_lines": "one_sided", "max_muted_share": "muted", "per_day": "day_cap", "per_week": "week_cap",
               "skip_routes": "local_route", "skip_economy": "economy", "skip_near_limit": "near_limit"}
    keyed = [key_for[k] for k in Q["eligibility"] if k in key_for]
    assert len(keyed) == len(key_for)
    assert keyed == [w for w in rows if w in keyed]


def test_cap_slots_cover_queued_runs():
    """Review PB-90 r0: the caps count queued and started runs (slots), so two calls queued together cannot both be
    judged; only a run that never sent a request (expired, or rules 12 to 16 failed at start) gives its slot back."""
    s = SPEC.read_text(encoding="utf-8")
    states = re.search(r"- `judge\.state`: (.*?)\n- ", _section(s, 5), re.S).group(1)
    listed = re.findall(r"`([a-z_]+)`", states.split("holds a cap slot")[0])
    holding = re.findall(r"`([a-z_]+)`", states.split("holds a cap slot")[1])
    slots = Q["eligibility"]["slot_states"]
    assert holding == slots
    assert set(slots) <= set(listed)
    assert "queued" in slots and "expired" not in slots and "not_eligible" not in slots
    sec6 = _section(s, 6)
    assert "Cap slots" in sec6 and "Rules 1, 2 and 10 to 16 are checked again when the judge starts" in sec6
    assert Q["eligibility"]["slots_file"] in sec6



def test_switches_and_route_are_checked_again_at_start():
    """Review PB-90 r2: a waiting judge checks both switches again before its first request (a switch turned off
    while it waits sends nothing), and the judge runs on the saved "after" route only: AIRoute.effective and canRun
    may pick another AI, the model on this Mac included."""
    s = SPEC.read_text(encoding="utf-8")
    e, skip = Q["eligibility"], Q["new_enums"]["quality_skip"]
    rows = re.findall(r"^\| (\d+) \|.*\| `([a-z_]+)` \|$", _section(s, 6), re.M)
    by_n = {int(n): w for n, w in rows}
    expected = [by_n[n] for n in (1, 2, *range(10, 17))]
    assert e["recheck_at_start"] == expected
    assert set(e["recheck_at_start"]) <= set(skip)
    assert e["stop_on_switch_off"] is True
    sec6, sec7 = _section(s, 6), _section(s, 7)
    assert "A switch turned off during a run" in sec6 and "no `tip_scored` is sent for that call" in sec6
    assert Q["judge"]["route_source"] == "saved" and Q["judge"]["chain"] is False
    assert "(`AppConfig.ai.after`" in sec7 and "**not** from `AIRoute.effective`" in sec7
    for v in ("LT_AI_AUTO=0", "LT_LOCAL_FALLBACK=0", "LT_GEMINI_FALLBACK=0", "`LT_AI_CHAIN` with that route"):
        assert v in sec7, v
    rule13 = next(ln for ln in sec6.splitlines() if ln.startswith("| 13 |"))
    assert "Not `AIRoute.canRun`" in rule13 and "`AIRoute.available(route)`" in rule13 and "`AIRoute.due(route)`" in rule13
    assert "`routing.provider_from_env(job=\"after\")`" in sec7 and "does not use the `ai` block of `sidecar-config.json`" in sec7
    assert "the score\n  fields (`scored`, `useful_avg`" in sec6 or "score fields (`scored`, `useful_avg`" in sec6.replace("\n  ", " ")
    assert "including a scoring that is waiting or under way" in s and "зокрема те, що чекає або вже йде" in s


def test_open_questions_table_is_one_table():
    """A blank line inside the section 13 table would render the rows after it as a table without a header."""
    lines = _section(SPEC.read_text(encoding="utf-8"), 13).splitlines()
    first = next(i for i, ln in enumerate(lines) if ln.startswith("| # |"))
    block = []
    for ln in lines[first:]:
        if not ln.startswith("|"):
            break
        block.append(ln)
    ids = [ln.split("|")[1].strip() for ln in block[2:]]
    all_q = [ln.split("|")[1].strip() for ln in lines if re.match(r"^\| Q\d+ \|", ln)]
    assert ids == all_q == [f"Q{i}" for i in range(1, len(all_q) + 1)]


def test_only_sendable_models_are_sent():
    """The server drops a model that fails the `model` type (codex/default has a slash): those routes send none."""
    rx = CAT["types"]["model"]["re"]
    models = Q["judge"]["models"]
    for route in Q["judge"]["send_model"]:
        assert re.fullmatch(rx, models[route]), route + ": " + models[route]
    for route, m in models.items():
        if not re.fullmatch(rx, m):
            assert route not in Q["judge"]["send_model"], route


def test_eligibility_defaults_and_skip_codes():
    e = Q["eligibility"]
    for k in ("min_minutes", "min_tips_shown", "brief_kinds", "skip_call_types", "min_lines", "min_other_lines",
              "max_muted_share", "per_day", "per_week", "skip_routes", "expire_hours"):
        assert k in e, k
    assert set(e["brief_kinds"]) <= set(CAT["enums"]["brief_kind"]) and "none" not in e["brief_kinds"]
    assert set(e["skip_call_types"]) <= set(CAT["enums"]["call_type"])
    assert 0 < e["per_day"] <= e["per_week"]
    skip = Q["new_enums"]["quality_skip"]
    for code in ("judged", "off", "analytics_off", "call_type", "short", "no_brief", "few_tips", "few_lines", "one_sided",
                 "muted", "day_cap", "week_cap", "local_route", "no_route", "economy", "near_limit", "budget", "expired"):
        assert code in skip, code


def test_spec_names_every_skip_code_and_event_and_has_no_long_dash():
    s = SPEC.read_text(encoding="utf-8")
    for code in Q["new_enums"]["quality_skip"]:
        assert "`" + code + "`" in s, code
    for ev in Q["events"]:
        assert "`" + ev + "`" in s, ev
    for f in (SPEC, ROOT / "rules/quality.yaml", Path(__file__)):
        assert chr(0x2014) not in f.read_text(encoding="utf-8"), f.name
