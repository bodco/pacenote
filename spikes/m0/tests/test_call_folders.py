"""Call folders (rules/call_layout.yaml, 19.11): Python layout and migration; the two Python copies and the Swift core agree."""
import os
import re
import time
from pathlib import Path

from livetips_m0 import call_layout as L

APP = Path(__file__).resolve().parents[3]
K = "2026-11-18-1512-pacenote-sync.app"
REAL = [".brief.md", ".cards.json", ".clean.md", ".facts.json", ".inbox.jsonl", ".md", ".md.timing.jsonl", ".meet.md", ".named.md",
        ".notes.md", ".semantic-summary.json", ".semantic.jsonl", ".semantic.npz", ".sheet-live.json", ".sheet.json", ".speakers.json",
        ".sync.json", ".tips.jsonl"]


def _touch(p: Path, age: float = 3600):
    p.write_text("x")
    t = time.time() - age
    os.utime(p, (t, t))


def test_copies_identical_and_match_swift():
    assert (APP / "recorder/src/call_layout.py").read_text() == (APP / "spikes/m0/livetips_m0/call_layout.py").read_text()
    sw = (APP / "app/Sources/LiveTipsCore/CallLayout.swift").read_text()
    sets = re.findall(r"\[([^\]]*)\]", sw.split("visibleCall: Set<String> = ")[1].split("\n")[0])[0]
    assert set(re.findall(r'"([^"]+)"', sets)) == L.VISIBLE_CALL
    rules = (APP / "rules/call_layout.yaml").read_text()
    for s in L.VISIBLE_CALL | L.VISIBLE_PREP:
        assert f'"{s}"' in rules


def test_paths():
    call = "/p/live/2026-11-18-pacenote-sync/2026-11-18-1512-pacenote-sync.app.md"
    assert str(L.sidecar_of(call, ".tips.jsonl")) == "/p/live/2026-11-18-pacenote-sync/.work/2026-11-18-1512-pacenote-sync.app.tips.jsonl"
    assert str(L.sidecar_of(call, ".notes.md")) == "/p/live/2026-11-18-pacenote-sync/2026-11-18-1512-pacenote-sync.app.notes.md"
    assert str(L.sidecar_of("/p/live/2026-11-18-1512-x.app.md", ".tips.jsonl")) == "/p/live/2026-11-18-1512-x.app.tips.jsonl"
    assert str(L.live_folder(call)) == "/p/live"


def test_migrate(tmp_path):
    live = tmp_path / "live"
    (live / "sources").mkdir(parents=True)
    for s in REAL:
        _touch(live / (K + s))
    _touch(live / "prep-2026-11-18-1515-pacenote-sync.sheet.md")
    _touch(live / "prep-2026-11-18-1515-pacenote-sync.brief.md")
    _touch(live / "commitments.jsonl")
    (live / "2026-11-19-0900-standup.app.md").write_text("now")
    rep = L.migrate(live)
    d = live / "2026-11-18-pacenote-sync"
    assert sorted(p.name for p in d.iterdir() if not p.name.startswith(".")) == sorted(
        [K + ".brief.md", K + ".md", K + ".notes.md", "prep-2026-11-18-1515-pacenote-sync.sheet.md"])
    assert (d / ".work" / (K + ".semantic.npz")).exists() and (d / ".work" / "prep-2026-11-18-1515-pacenote-sync.brief.md").exists()
    assert (live / "commitments.jsonl").exists() and (live / "2026-11-19-0900-standup.app.md").exists()
    assert len(rep["moved"]) == 20 and rep["skipped_busy"] == ["2026-11-19-0900-standup"]
    assert L.migrate(live, now=time.time() + 7200)["moved"]
    again = L.migrate(live, now=time.time() + 7200)
    assert not any(again.values())
    names = [p.name for p in L.files(live)]
    assert K + ".md" in names and "2026-11-19-0900-standup.app.md" in names and K + ".sync.json" not in names


# ---- B2: the recorder (rules/call_layout.yaml)
REC = Path(__file__).resolve().parents[3] / "recorder"


def _load_script(name, path):
    import importlib.machinery
    import importlib.util
    import sys
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def test_recorder_has_no_flat_sidecars_left():
    pat = re.compile(r"with_name\((\w+)\.stem \+ \"\.|\b(?:base|s) \+ f?\"\.(?!md\"\))")
    for f in ("src/transcriber.py", "src/fromfile.py", "bin/read_delta.py", "bin/lt"):
        text = (REC / f).read_text(encoding="utf-8")
        assert not pat.search(text), f


def test_recorder_sidecars_go_to_work(tmp_path):
    rd = _load_script("lt_read_delta_cf", REC / "bin/read_delta.py")
    live = tmp_path / "live"
    d = live / "2026-11-18-pacenote-sync"
    d.mkdir(parents=True)
    base = str(d / "2026-11-18-1512-pacenote-sync.app")
    assert rd.sc(base, ".tips.jsonl") == str(d / ".work" / "2026-11-18-1512-pacenote-sync.app.tips.jsonl")
    assert rd.sc(base, ".brief.md") == str(d / "2026-11-18-1512-pacenote-sync.app.brief.md")
    flat = str(live / "2026-11-18-1512-old.app")        # a file still directly in live/ keeps the old rule
    assert rd.sc(flat, ".tips.jsonl") == flat + ".tips.jsonl"


def test_engine_has_no_flat_sidecars_left():
    pat = re.compile(r"with_name\([^)]*\"\.(?:notes|named|sheet|sales|brief)|Path\((?:r\[\"base\"\]|base|stem|str\(md\)|str\(a\.md\)\[:-3\]) \+ f?\"\.(?!md\"\))")
    root = APP / "spikes/m0/livetips_m0"
    for f in list(root.glob("*.py")) + list(root.glob("sheet/*.py")):
        if f.name == "call_layout.py":
            continue
        text = f.read_text(encoding="utf-8")
        assert not pat.search(text), f.name


def test_engine_sidecars_go_to_work(tmp_path):
    from livetips_m0.sheet import build as B, aftercheck as AC, sources as SRC
    live = tmp_path / "proj" / "live"
    base = B.base_of({"project": {"root": str(tmp_path / "proj")}, "meeting": {"title": "Pacenote sync", "start": "2026-11-18T15:12"}})
    d = live / "2026-11-18-pacenote-sync"
    assert base == str(d / "prep-2026-11-18-1512-pacenote-sync")
    assert str(L.sidecar(base, ".sheet.json")) == str(d / ".work" / "prep-2026-11-18-1512-pacenote-sync.sheet.json")
    assert str(L.sidecar(base, ".sheet.md")) == str(d / "prep-2026-11-18-1512-pacenote-sync.sheet.md")
    (d / ".work").mkdir(parents=True)
    (d / ".work" / "prep-2026-11-18-1512-pacenote-sync.sheet.v1.json").write_text("{}")
    assert B.next_version(base) == 2
    (d / ".work" / "prep-2026-11-18-1512-pacenote-sync.sheet.json").write_text("{}")
    assert SRC.previous_sheets(str(live), "Pacenote sync") == [str(d / ".work" / "prep-2026-11-18-1512-pacenote-sync.sheet.json")]
    md = d / "2026-11-18-1612-pacenote-sync.app.md"
    md.write_text("x")
    assert AC.sheet_for(md) is None
    (d / ".work" / "2026-11-18-1612-pacenote-sync.app.sheet.json").write_text("{}")
    assert AC.sheet_for(md) == d / ".work" / "2026-11-18-1612-pacenote-sync.app.sheet.json"


def test_start_call_carries_the_hubspot_link(tmp_path):
    """H2 (PB-58): Refresh saves the HubSpot link with the preparation; automatic adoption takes it to the call
    (from the adopted prep, else a matching link alone), without the call key and the HubSpot Call id."""
    import json
    lt = _load_script("lt_cli_crm", REC / "bin/lt")
    d = tmp_path / "proj" / "live" / "2026-11-22-orbit-renewal"
    (d / ".work").mkdir(parents=True)
    prep = d / "prep-2026-11-22-1500-orbit-renewal"
    Path(str(prep) + ".brief.md").write_text("# Live Brief: Orbit renewal\n", encoding="utf-8")
    link = {"portalId": "4711", "companyId": "c1", "dealId": "d1", "contactIds": ["p1"], "callKey": "old", "callId": "old-call"}
    (d / ".work" / (prep.name + ".crm.json")).write_text(json.dumps(link), encoding="utf-8")
    call = str(d / "2026-11-22-1502-orbit-renewal.app")
    mine = d / ".work" / "2026-11-22-1502-orbit-renewal.app.crm.json"
    got = lt.adopt_prep(call)
    assert got[".crm.json"] == prep.name + ".crm.json" and ".brief.md" in got
    assert json.loads(mine.read_text(encoding="utf-8")) == {"portalId": "4711", "companyId": "c1", "dealId": "d1", "contactIds": ["p1"]}
    # the call's own link stays
    mine.write_text(json.dumps({"callId": "c-9"}), encoding="utf-8")
    assert ".crm.json" not in lt.adopt_prep(call)
    assert json.loads(mine.read_text(encoding="utf-8")) == {"callId": "c-9"}
    # Refresh before Prepare: only the link exists, it is still carried
    other = tmp_path / "proj" / "live" / "2026-11-22-weekly-sync"
    (other / ".work").mkdir(parents=True)
    (other / ".work" / "prep-2026-11-22-1600-weekly-sync.crm.json").write_text(json.dumps(link), encoding="utf-8")
    assert ".crm.json" in lt.adopt_prep(str(other / "2026-11-22-1601-weekly-sync.app"))
    # a link of another call is not taken
    assert ".crm.json" not in lt.adopt_prep(str(other / "2026-11-22-1700-board-review.app"))


def test_hubspot_link_of_another_call_is_not_carried(tmp_path):
    """PB-58 fix round 4: a "Renewal" call adopts its own brief (no link); the "Acme renewal" link, whose title only
    contains the call's, is never carried, neither from another call folder nor through a partly matching prep."""
    import json
    lt = _load_script("lt_cli_crm_other", REC / "bin/lt")
    live = tmp_path / "proj" / "live"
    link = {"portalId": "4711", "companyId": "acme", "dealId": "d-acme", "contactIds": ["p-acme"]}
    acme = live / "2026-11-22-acme-renewal"
    (acme / ".work").mkdir(parents=True)
    Path(str(acme / "prep-2026-11-22-1500-acme-renewal") + ".brief.md").write_text("# Live Brief: Acme renewal\n", encoding="utf-8")
    (acme / ".work" / "prep-2026-11-22-1500-acme-renewal.crm.json").write_text(json.dumps(link), encoding="utf-8")
    own = live / "2026-11-22-renewal"
    (own / ".work").mkdir(parents=True)
    Path(str(own / "prep-2026-11-22-1600-renewal") + ".brief.md").write_text("# Live Brief: Renewal\n", encoding="utf-8")
    call = own / "2026-11-22-1602-renewal.app"
    got = lt.adopt_prep(str(call))
    assert got[".brief.md"] == "prep-2026-11-22-1600-renewal.brief.md" and ".crm.json" not in got
    assert not (own / ".work" / "2026-11-22-1602-renewal.app.crm.json").exists()
    # no prep of its own: the partly matching Acme prep is adopted for its brief, its link still stays out
    Path(str(own / "prep-2026-11-22-1600-renewal") + ".brief.md").unlink()
    lone = live / "2026-11-22-renewal-2"
    lone.mkdir()
    got = lt.adopt_prep(str(lone / "2026-11-22-1700-renewal.app"))
    assert got[".brief.md"] == "prep-2026-11-22-1500-acme-renewal.brief.md" and ".crm.json" not in got
    assert not (lone / ".work" / "2026-11-22-1700-renewal.app.crm.json").exists()
