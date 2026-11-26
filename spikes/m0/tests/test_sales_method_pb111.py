"""PB-111 (SX4): one sales method per call and a clear picker. One default in rules/sales.yaml and
rules/meeting-sheet.yaml, four core methods (Essentials, SPICED, BANT, MEDDPICC) and the rest under More, a "when to
use" line per method, SPICED recommended. The chosen method reaches the sheet's Qualification block, the live tracker
and the after-call summary unchanged (the app side, prep request included, is in CoreChecks salesMethodEverywherePB111)."""
from livetips_m0.rules import Rules
from livetips_m0.sales import SalesTracker, methods_json
from livetips_m0.sheet import presets as P
from livetips_m0.sheet.prompt import block_lines

APP_DEFAULT = "general"     # LiveTipsCore/SalesMethod.swift > defaultKey


def test_one_default_in_both_rules_files_and_the_app():
    sales = Rules().sales["default_method"]
    sheet = P.load_rules()["qualification"]["default_method"]
    assert sales == sheet == APP_DEFAULT, (sales, sheet)


def test_a_sheet_without_a_call_method_takes_the_one_default():
    rules = P.load_rules()
    s = P.new_sheet(rules, "sales_intro", {"start": "2026-11-26T17:00:00+02:00", "title": "Intro"},
                    {"ours": [{"name": "Mike Carter"}], "theirs": [{"name": "Emily Parker"}]})
    q = [b for b in s["blocks"] if b["type"] == "qualification"][0]
    assert q["method"] == Rules().sales["default_method"]


def test_four_core_methods_the_rest_under_more():
    ms = methods_json(Rules(), "en")["methods"]
    core = [m["key"] for m in ms if m["tier"] == "core"]
    assert core == ["general", "spiced", "bant", "meddpicc"]
    assert {m["key"] for m in ms if m["tier"] == "more"} == {"meddic", "gpctba", "spin", "sandler", "challenger"}
    assert [m["key"] for m in ms if m["recommended"]] == ["spiced"]
    assert next(m for m in ms if m["key"] == "general")["name"] == "Essentials"
    assert next(m for m in methods_json(Rules(), "uk")["methods"] if m["key"] == "general")["name"] == "Основне"


def test_every_method_has_a_when_line_in_both_languages():
    for key, m in Rules().sales["methods"].items():
        assert m.get("tier") in ("core", "more"), key
        w = m.get("when") or {}
        assert w.get("en") and w.get("uk"), key
        assert chr(0x2014) not in w["en"] + w["uk"], key
    for lang in ("en", "uk"):
        assert all(m["when"] for m in methods_json(Rules(), lang)["methods"])


def test_the_chosen_method_reaches_sheet_live_and_summary_unchanged():
    sales = Rules().sales
    criteria = {k: v["criteria"] for k, v in sales["methods"].items()}
    lib = P.load_rules().get("blocks") or {}
    for key in sales["methods"]:
        # prep: the Qualification block of the sheet gets the call's method (build.py sets req.method on it)
        line = block_lines([{"type": "qualification", "title": "Qualification", "method": key}], lib, criteria)
        assert f"Method {key}:" in line
        assert all(c["key"] in line for c in sales["methods"][key]["criteria"])
        # live and after the call: the tracker and its summary keep the same key
        t = SalesTracker(sales, key, "en", [], 1800)
        assert t.method == key and t.summary()["method"] == key
    # Essentials: the sheet asks for its own criteria, not BANT's
    line = block_lines([{"type": "qualification", "title": "Qualification", "method": "general"}], lib, criteria)
    assert "pain" in line and "next_step" in line and "budget" not in line
