import sys, datetime as dt, json, threading, time, tempfile
sys.path.insert(0, 'tests'); sys.path.insert(0, '.')
from pathlib import Path
from conftest import FIX
import livetips_m0.live as L
from livetips_m0.providers import FakeProvider
from test_engine import CFG, responder

orig_feed = L.LiveRun.feed
def feed(self, ln, rec):
    print("FEED", ln.t, ln.speaker, ln.text[:30], rec is not None, flush=True)
    orig_feed(self, ln, rec)
L.LiveRun.feed = feed
orig_parse = L.parse_line
def pl(raw, *a):
    r = orig_parse(raw, *a)
    print("PARSE", repr(raw[:50]), bool(r), flush=True)
    return r
L.parse_line = pl
tmp = Path(tempfile.mkdtemp())
md = tmp / "live-call.md"; md.write_text("# Live transcript: live-call\n\n")
tm = tmp / "live-call.md.timing.jsonl"; tm.write_text("")
src = [l for l in (FIX / "demo-call.md").read_text().splitlines() if l.startswith("[")]
def writer():
    time.sleep(0.3)
    for line in src[:3]:
        now = dt.datetime.now(); hms = now.strftime("%H:%M:%S")
        line = "[" + hms + "]" + line[10:]
        ch = "me" if "] Me " in line else "them"
        with md.open("a") as f: f.write(line + "\n")
        with tm.open("a") as f: f.write(json.dumps({"t": hms, "ch": ch, "seg_end": now.strftime("%H:%M:%S.%f")[:-3], "written": now.strftime("%H:%M:%S.%f")[:-3]}) + "\n")
        print("WROTE", line[:40], flush=True)
        time.sleep(0.2)
th = threading.Thread(target=writer); th.start()
cfg = dict(CFG, budget=dict(CFG["budget"], ledger=str(tmp / "spend.json")))
L.run(cfg, md, tmp / "out", idle_min=0.03, provider=FakeProvider(responder, 0.01, 0.02), max_s=8)
th.join()
print("LINES", json.loads((tmp / "out" / "live-call.app.summary.json").read_text())["lines"])
