"""PB-89 (owner 25.11): call_started.has_brief was always false. The catalog (rules/analytics.yaml, the server's
analytics_events.yaml is its byte copy) carries call_ended.brief and call_started sheet and from; the app fills them."""
import hashlib
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CAT = ROOT / "rules" / "analytics.yaml"
SRC = ROOT / "app" / "Sources"


def test_catalog_has_the_brief_signal():
    c = yaml.safe_load(CAT.read_text(encoding="utf-8"))
    assert c["enums"]["brief_kind"] == ["none", "chosen", "auto", "late"]
    assert c["events"]["call_ended"]["brief"] == "brief_kind"
    started = c["events"]["call_started"]
    assert started["has_brief"] == "bool" and started["sheet"] == "sheet_mode" and started["from"] == "start_from"


def test_embedded_catalog_is_current():
    swift = (SRC / "LiveTipsCore" / "AnalyticsCatalog.swift").read_text(encoding="utf-8")
    assert hashlib.sha256(CAT.read_bytes()).hexdigest() in swift, "run tools/gen_analytics_catalog.py"


def test_app_sends_the_signal():
    tracker = (SRC / "LiveTips" / "AnalyticsTracker.swift").read_text(encoding="utf-8")
    engine = (SRC / "LiveTips" / "CallEngine.swift").read_text(encoding="utf-8")
    # the old input: a "brief" key no call request ever had
    for text in (tracker, engine):
        assert 'request["brief"]' not in text
    assert '"has_brief": CallBriefSignal.chosen(request)' in tracker and '"has_brief": CallBriefSignal.chosen(request)' in engine
    assert '"sheet": CallBriefSignal.sheet(request)' in tracker and 'p["from"] = from' in tracker
    assert 'p["brief"] = b' in tracker and "CallBriefSignal.atEnd(" in engine


def test_every_from_the_app_sets_is_a_start_from_value():
    allowed = set(yaml.safe_load(CAT.read_text(encoding="utf-8"))["enums"]["start_from"])
    used = set()
    for f in (SRC / "LiveTips").glob("*.swift"):
        used |= set(re.findall(r'startFrom = "([a-z_]+)"', f.read_text(encoding="utf-8")))
    assert used and used <= allowed, used - allowed
