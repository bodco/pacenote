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
    assert r["useful"]["min"] >= CAT["types"]["small"]["min"] and r["useful"]["max"] <= CAT["types"]["small"]["max"]
    assert set(Q["judge"]["judged_kinds"]) == set(Q["new_enums"]["quality_tip_kind"])
    assert Q["judge"]["chain"] is False


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
