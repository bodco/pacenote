"""Live mode of the spike: runs next to the skill on a real call (comparison mode).

Tails the transcript that live-transcriber writes (<stem>.md and <stem>.md.timing.jsonl) and
feeds each new line to the engine as soon as it appears. Writes only <stem>.app.* files into
--out (a private folder by default, never the skill's files). Stops when the recorder reports
`stopped`, the file is idle for --idle-min minutes, or on Ctrl+C.

  python -m livetips_m0.live --config cfg.json --md <project>/live/<stem>.md --out runs/live-<stem> \
      [--status <tool_root>/state/status.json]
"""
import argparse
import datetime as dt
import json
import time
from pathlib import Path

from . import call_layout

from .commitments import norm_text
from .replay import build_engine, finish_run_info, run_info
from .transcript import hms_to_s, mute_marker, parse_line


def now_sod() -> float:
    n = dt.datetime.now()
    return n.hour * 3600 + n.minute * 60 + n.second + n.microsecond / 1e6


class Tail:
    """Reads complete new lines from a growing file."""

    def __init__(self, path: Path):
        self.path = path
        self.pos = 0
        self.buf = ""
        self.ino = None
        self.replaced = False        # PB-30: the file was replaced or truncated since the caller last cleared this

    def read(self):
        if not self.path.exists():
            return []
        st = self.path.stat()
        size = st.st_size
        # truncated, or replaced by a new file (PB-30: the recorder's end-of-call rewrite, which may also be longer)
        if size < self.pos or (self.ino is not None and st.st_ino != self.ino):
            self.pos = 0
            self.buf = ""
            self.replaced = True
        self.ino = st.st_ino
        if size == self.pos:
            return []
        with self.path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(self.pos)
            data = f.read()
            self.pos = f.tell()
        self.buf += data
        *lines, self.buf = self.buf.split("\n")
        return lines


ACK_KEYS = {"call_type", "hiring_role", "audience", "comment_rate_seconds", "proactive", "ui_lang", "tips_paused", "tips_lang", "tips"}


def _on(value) -> bool:
    return str(value).lower() in ("on", "true", "1")


def _paused(eng, value) -> None:
    eng.paused = _on(value)


def _proactive(eng, value) -> None:
    eng.proactive = _on(value)        # 2026-10-27: the window switch now really changes the engine
    eng.cfg["proactive"] = eng.proactive
    try:                              # 2026-11-02: and the model's rules (add_fact, fix_owner_date were still off)
        eng.prompt.set_meeting({"proactive": eng.proactive})
    except Exception:  # noqa: BLE001
        pass


def _set_meeting(eng, change: dict) -> None:
    try:
        eng.prompt.set_meeting(change)
    except Exception:  # noqa: BLE001
        pass


def _title(eng, value) -> None:
    if str(value or "").strip():      # renamed during the call
        eng.cfg["title"] = str(value).strip()[:120]
        _set_meeting(eng, {"title": eng.cfg["title"]})


def _call_lang(eng, value) -> None:
    v = str(value or "")              # Pacenote 0.8: the call's languages changed in Call settings
    if v:
        _set_meeting(eng, {"langs": ["uk", "en"] if v == "mix" else [c for c in v.split("+") if c]})


def _onme(eng, value) -> None:
    text, _, st = str(value or "").rpartition("|")   # "<action text>|done" or "|open": a tick in the window's On me
    if text.strip():
        eng.onme_ticked[norm_text(text)] = st == "done"


def _onme_fix(eng, value) -> None:
    """0.8.5: "<line>|" = not on the PM; "<line>|<new text>" = reworded (the window's On me Edit / Not mine)."""
    text, _, new = str(value or "").partition("|")
    if text.strip():
        eng.onme_fixed[norm_text(text)] = new.strip()


def _onme_mine(eng, value) -> None:
    """0.8.6: "<raw text>|<id of the on-them item or empty>|<its text or empty>": "Mine" in the window."""
    raw, _, rest = str(value or "").partition("|")
    fid, _, ftext = rest.partition("|")
    eng.add_mine(raw, fid.strip(), ftext.strip())


def _deal_fix(eng, value) -> None:
    """0.8.7: "<criterion>|<unknown|partial|known>|<what>": the PM's click in the Deal tab."""
    k, _, rest = str(value or "").partition("|")
    st, _, what = rest.partition("|")
    if hasattr(eng, "sales_fix"):
        eng.sales_fix(k.strip(), st.strip(), what.strip())


def _tips_lang(eng, value) -> None:
    """0.8.5 (rules/meeting.yaml > tips_language): the window's language switch for tips and cards."""
    v = str(value or "").strip().lower()
    if v in ("uk", "en"):
        eng.cfg["tips_lang"] = v
        if getattr(eng, "prompt", None) is not None and hasattr(eng.prompt, "fmt"):
            eng.prompt.fmt["tips_lang"] = v


def _tips(eng, value) -> None:
    eng.tips_set = str(value).lower() not in ("off", "false", "0", "no")   # 0.8.5: the Tips switch in Call settings


def _call_type(eng, value) -> None:
    if (str(value or "").lower() == "transcript") != (eng.meta().get("call_type") == "transcript"):
        eng.tips_set = str(value or "").lower() != "transcript"          # the type switches tips, as in the recorder


SETTINGS = {
    "tips": _tips,
    "call_type": _call_type,
    "tips_paused": _paused,
    "proactive": _proactive,
    "title": _title,
    "call_lang": _call_lang,
    "agenda_undo": lambda eng, v: eng.undo_agenda_refresh(),
    "agenda_state": lambda eng, v: eng.set_agenda_state(str(v or "")),        # an agenda item's state set in the window
    "onme_state": _onme,
    "onme_fix": _onme_fix,
    "onme_mine": _onme_mine,
    "deal_fix": _deal_fix,
    "tips_lang": _tips_lang,
    "tip_feedback": lambda eng, v: eng.rate_tip(v),                          # 0.9.4: a check, wave or cross on a card
    "commitment_closed": lambda eng, v: eng.commitment_closed(str(v or "")),  # the app wrote the register
}


def handle_setting(eng, key, value) -> None:
    if key in SETTINGS:
        SETTINGS[key](eng, value)
    if key in ACK_KEYS:
        eng.out_tips.write({"kind": "ack", "key": key, "value": value, "t": time.strftime("%H:%M:%S")})


def handle_request(eng, text: str) -> None:
    low = text.lower()
    if low == "sos":
        eng.on_request("sos")
    elif low.startswith("find:"):
        if text[5:].strip():
            eng.on_request("find", text[5:].strip())
    elif low == "refresh context":
        eng.reload_prep()             # the window's Refresh or the app after a late preparation
    elif low != "off topic":
        eng.on_request("ask", text)


def handle_inbox(eng, raw: str) -> None:
    """Questions, HELP NOW and settings from the tips window (the skill gets the same via read_delta)."""
    try:
        d = json.loads(raw)
    except ValueError:
        return
    if d.get("kind") == "context_correction":
        eng.correct_context(str(d.get("text") or ""))
        return
    if d.get("kind") == "context_restore_agenda":     # the prep after a correction failed: the old agenda comes back
        eng.restore_agenda()
        return
    if d.get("kind") == "setting":
        handle_setting(eng, d.get("key"), d.get("value"))
        return
    text = (d.get("text") or "").strip()
    if text:
        handle_request(eng, text)


def wait_for(path: Path, limit_s: float = 3 * 3600, step: float = 0.5) -> None:
    """The app starts the engine while the recorder waits for the call (standby): the transcript file
    appears with the first line. 2026-10-23: the engine crashed here and was restarted by the app."""
    waited = 0.0
    while not path.exists() and waited < limit_s:
        time.sleep(step)
        waited += step


def skip_existing(*tails) -> None:
    for t in tails:
        if t and t.path.exists():
            st = t.path.stat()
            t.pos, t.ino = st.st_size, st.st_ino


class LiveRun:
    """One live call: the tails it reads, the lines waiting for their timing, what it has seen."""

    def __init__(self, cfg, eng, md: Path, inbox: Path = None, from_start: bool = False):
        self.cfg, self.eng = cfg, eng
        self.t_md, self.t_tm = Tail(md), Tail(call_layout.sidecar(str(md), ".timing.jsonl"))
        self.t_in = Tail(inbox) if inbox else None
        skip_existing(self.t_in)                     # only requests made after the engine attached
        self.rows_seen = 0                           # PB-30: transcript rows consumed from the file, in write order
        self.skip_rows = 0                           # PB-30: rows of a rewritten file that were consumed before it
        if not from_start:                           # join a running call: skip what is already there
            skip_existing(self.t_md, self.t_tm)
            if md.exists():                          # the end-of-call rewrite shows these rows again: count them
                with md.open("rb") as f:                # pos is in bytes: never count a row appended after it
                    head = f.read(self.t_md.pos).decode("utf-8", errors="replace")
                self.rows_seen = sum(1 for raw in head.split("\n")[:-1] if parse_line(raw, 0, cfg["me_label"]))
        self.read_seqs = set()                       # PB-30: seqs of the rows already read as lines
        if self.rows_seen:
            # the skipped rows are consumed: their seqs too (review round 7: S1's skipped "Bonjour" translated to "Hello"
            # took the seq of S2's unread "Hello"). The writer publishes a row event just before it appends the row,
            # so the first row events in the sidecar are the rows already in the file, in the same order.
            self.read_seqs.update(self.skipped_seqs(self.t_tm.path, self.t_tm.pos, self.rows_seen))
        self.timing, self.pending, self.n = {}, [], 0
        self.rows = {}                               # PB-30: "row" events (written by the recorder's writer): {(t, ch): [event]}
        self.unseq = []                              # PB-30: fed lines whose "row" event had not arrived yet
        self.translations = []                       # PB-30: "translated" events not applied yet: [(event, first seen)]
        self.seen_lines = set()
        self.row_total = {}                          # PB-30: (t, ch, text) -> "row" events read: rows the writer appended
        self.fed_rows = {}                           # PB-30: (t, ch, text) -> lines fed for it
        self.tr_events = {}                          # PB-30: (t, ch, translated text) -> "translated" events not matched to a row yet
        self.row_seqs = {}                           # PB-30: (t, ch, text) -> seqs of the rows the writer appended, in order
        self.read_unknown = {}                       # PB-30: (t, ch, text) -> lines read before their row event
        self.last_new = self.t0 = time.monotonic()
        self.last_touch = 0.0
        self.first_written = self.last_written = None

    def step(self) -> None:
        self.read_timing()
        self.read_lines()
        self.feed_ready()
        self.reconcile_seq()
        self.apply_translations()
        if self.t_in:
            for raw in self.t_in.read():
                handle_inbox(self.eng, raw)

    def read_timing(self) -> None:
        for raw in self.t_tm.read():
            try:
                r = json.loads(raw)
                if r.get("event"):                # PB-30: not a line's timing
                    if r["event"] == "translated":          # the translation replaces the line's text
                        self.translations.append((r, time.monotonic()))
                        k = self.row_key(r.get("t"), r.get("ch"), r.get("text"))
                        self.tr_events.setdefault(k, []).append(r)
                    elif r["event"] == "row":               # the writer appended this row: its stable seq
                        self.rows.setdefault((r.get("t"), r.get("ch")), []).append(r)
                        k = self.row_key(r.get("t"), r.get("ch"), r.get("text"))
                        self.row_total[k] = self.row_total.get(k, 0) + 1
                        self.row_seqs.setdefault(k, []).append(r.get("seq"))
                        if self.read_unknown.get(k, 0) > 0:          # its line was read already
                            self.read_unknown[k] -= 1
                            self.read_seqs.add(r.get("seq"))
                    continue
                self.timing.setdefault((r.get("t"), r.get("ch")), []).append(r)
            except ValueError:
                pass

    @staticmethod
    def skipped_seqs(path: Path, pos: int, n: int) -> list:
        """The seqs of the first `n` row events in the timing sidecar up to byte `pos` (the rows skipped on joining)."""
        seqs = []
        try:
            with path.open("rb") as f:
                head = f.read(pos).decode("utf-8", errors="replace")
        except OSError:
            return seqs
        for raw in head.split("\n")[:-1]:
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("event") == "row" and r.get("seq") is not None:
                seqs.append(r["seq"])
                if len(seqs) >= n:
                    break
        return seqs

    @staticmethod
    def row_key(t, ch, text) -> tuple:
        return (t, ch, " ".join(str(text or "").split()))

    def read_lines(self) -> None:
        self.read_timing()                   # the writer publishes a row's event before it appends the row: read it first
        raws = self.t_md.read()
        if self.t_md.replaced:
            # the end-of-call rewrite keeps every row in its place and only changes the text of translated ones: the
            # first rows of the new file are the ones already consumed (review round 6: matching them by text let a
            # rewritten S1 row take the seq of S2's unread row with the same words)
            self.t_md.replaced = False
            self.skip_rows, self.rows_seen = self.rows_seen, 0
            self.read_timing()               # every event of the old file was written before the rewrite
        for raw in raws:
            ln = parse_line(raw, self.n, self.cfg["me_label"])
            mk = None if ln else mute_marker(raw)
            if mk is not None and hasattr(self.eng, "mic_mute"):
                self.eng.mic_mute(*mk)            # PB-104: the PM's mic muted in Pacenote, from the recorder's marker
            if ln:
                self.rows_seen += 1
                if self.skip_rows:
                    self.skip_rows -= 1
                    self.drop_read_translations(ln)
                    continue
                identity = (ln.t, ln.label, ln.stag, ln.text)     # two people saying "Yes" in one second are two lines
                key = self.row_key(ln.t, ln.speaker, ln.text)
                # a file read again (the recorder rewrote it) shows a row twice; the writer's row events say how many
                # identical rows really exist, so the same speaker saying the same words twice is two lines
                # (a translated row whose seq is not read yet is also new: the rewrite shows it with a real row's text)
                unread = any(e.get("seq") is not None and e["seq"] not in self.read_seqs
                             for e in self.tr_events.get(key, []))
                if (identity in self.seen_lines and not self.row_total.get(key, 0) > self.fed_rows.get(key, 0)
                        and not unread):
                    continue
                # the end-of-call rewrite shows a translated row with its new text: it is the row of the "translated"
                # event's seq. Read already: the line has the text from the event. Not read yet (the engine polled
                # first after the rewrite): it is that row, fed now, once. When a real row has the same time, channel
                # and text (S1's "Bonjour" translated to S2's "Hello"), the file is in write order, so the lower unread
                # seq is the row read now (review round 6).
                tr = self.tr_events.get(key) or []
                fresh = [e for e in tr if e.get("seq") is not None and e["seq"] not in self.read_seqs]
                row_seq = next((q for q in self.row_seqs.get(key, []) if q not in self.read_seqs), None)
                ev = min(fresh, key=lambda e: e["seq"]) if fresh else None
                if ev is not None and row_seq is not None and row_seq < ev["seq"]:
                    ev = None                                     # the real row was written first
                if ev is None and tr and row_seq is None:
                    ev = next((e for e in tr if e.get("seq") is None or e["seq"] in self.read_seqs), None)
                if ev is not None:
                    tr.remove(ev)
                    seq = ev.get("seq")
                    self.seen_lines.add(identity)
                    if seq is None or seq in self.read_seqs:
                        continue                                  # a row read again after the rewrite
                    self.read_seqs.add(seq)
                    ln.preset_seq = seq
                    orig = self.row_key(ln.t, ln.speaker, ev.get("original"))
                    self.fed_rows[orig] = self.fed_rows.get(orig, 0) + 1
                else:
                    self.seen_lines.add(identity)
                    self.fed_rows[key] = self.fed_rows.get(key, 0) + 1
                    seq = row_seq
                    if seq is None:
                        self.read_unknown[key] = self.read_unknown.get(key, 0) + 1
                    else:
                        self.read_seqs.add(seq)
                self.n += 1
                self.pending.append((ln, time.monotonic()))
                self.last_new = time.monotonic()

    def drop_read_translations(self, ln) -> None:
        """A consumed row skipped after the rewrite: a "translated" event of a row already read is not kept for text
        matching, so it cannot take an identical row that is still unread."""
        key = self.row_key(ln.t, ln.speaker, ln.text)
        tr = self.tr_events.get(key)
        if tr:
            self.tr_events[key] = [e for e in tr if e.get("seq") is None or e["seq"] not in self.read_seqs]

    def feed_ready(self) -> None:
        keep = []
        for ln, seen in self.pending:
            recs = self.timing.get((ln.t, ln.speaker))
            if recs or time.monotonic() - seen > 1.0:    # timing usually follows within ms; never wait > 1 s
                self.feed(ln, recs.pop(0) if recs else None)
            else:
                keep.append((ln, seen))
        self.pending = keep

    def feed(self, ln, rec) -> None:
        if rec:
            ln.seg_end = hms_to_s(rec["seg_end"]) if rec.get("seg_end") else None
            ln.written = hms_to_s(rec["written"]) if rec.get("written") else now_sod()
        else:
            ln.written = now_sod()
        # the seq comes from the writer's "row" event for this exact row (time, channel, text), never from timing order:
        # the timing file also has entries for lines the writer dropped as echo
        ln.seq = None
        if getattr(ln, "preset_seq", None) is not None:      # a rewritten row: its seq came from the "translated" event
            self.claim_seq_of(ln, ln.preset_seq)
        elif not self.claim_seq(ln):
            self.unseq.append(ln)                # the row event may land after the line was read: see reconcile_seq
        self.eng.on_line(ln)
        self.first_written = self.first_written or ln.written
        self.last_written = ln.written

    def claim_seq(self, ln) -> bool:
        rows = self.rows.get((getattr(ln, "t", None), getattr(ln, "speaker", None))) or []
        text = " ".join(str(getattr(ln, "text", "")).split())
        i = next((k for k, r in enumerate(rows) if " ".join(str(r.get("text") or "").split()) == text), None)
        if i is None:
            return False
        with self.eng.lock:
            ln.seq = rows.pop(i).get("seq")
        return True

    def claim_seq_of(self, ln, seq) -> None:
        rows = self.rows.get((getattr(ln, "t", None), getattr(ln, "speaker", None))) or []
        i = next((k for k, r in enumerate(rows) if r.get("seq") == seq), None)
        with self.eng.lock:
            if i is not None:
                rows.pop(i)
            ln.seq = seq

    def reconcile_seq(self) -> None:
        """A line fed before its "row" event was read gets the seq now (the timing file is read before the Markdown)."""
        self.unseq = [ln for ln in self.unseq if not self.claim_seq(ln)]

    def apply_translations(self) -> None:
        """PB-30: a background translation changes the text of the line in place (same line_id, same object), keyed by
        the recorder's seq, else by (time, speaker, original text). A line the engine has not read yet gets it when it
        has; an event that still finds no line after a minute is dropped."""
        keep = []
        for ev, seen in self.translations:
            by_seq = ev.get("seq") is not None
            def hit(h):
                if by_seq:                                   # an id on the event: only the exact row, never a text match
                    return h.seq == ev["seq"]
                return h.t == ev.get("t") and h.speaker == ev.get("ch") and h.text == ev.get("original")
            with self.eng.lock:
                found = next((h for h in [p[0] for p in self.pending] + list(self.eng.history) if hit(h)), None)
                if found is not None:
                    found.text = str(ev.get("text") or found.text)
            if found is None and time.monotonic() - seen < 60:
                keep.append((ev, seen))
        self.translations = keep

    def touch_state(self, state_dir: Path) -> None:
        if not state_dir or time.monotonic() - self.last_touch <= 5:
            return
        self.last_touch = time.monotonic()
        eng = self.eng
        try:
            (state_dir / "listener").touch()      # the window shows "not listening" without it
            (state_dir / "llm.json").write_text(json.dumps({
                "spent_usd": round(eng.budget.call_spent, 4) if eng.budget else 0, "tips": len(eng.tips),
                "budget": eng.budget.state() if eng.budget else "ok", "updated": time.strftime("%H:%M:%S")}))
        except OSError:
            pass

    def follow_status(self, status: Path) -> bool:
        """The meeting concept changed in the window's menu: the engine follows it. True: the recorder stopped."""
        if not (status and status.exists()):
            return False
        try:
            st = json.loads(status.read_text())
            ch = {k: st.get(k) for k in ("call_type", "audience", "hiring_role") if st.get(k)}
            if ch and self.eng.set_meeting(ch):
                print("meeting:", ch, flush=True)
        except (OSError, ValueError):
            pass
        try:
            return json.loads(status.read_text()).get("state") == "stopped" and time.monotonic() - self.last_new > 5
        except ValueError:
            return False

    def timed_out(self, idle_min: float, max_s: float) -> bool:
        if idle_min and time.monotonic() - self.last_new > idle_min * 60:     # 0 = never (app mode)
            return True
        return bool(max_s and time.monotonic() - self.t0 > max_s)

    def summary(self) -> dict:
        span = (self.last_written - self.first_written) if self.first_written and self.last_written else 0
        eng = self.eng
        return {"lines": self.n, "call_span_s": round(span, 1), "tips": len(eng.tips),
                "spent_usd": round(eng.budget.call_spent, 4) if eng.budget else None}


def run(cfg, md: Path, out: Path, status: Path = None, idle_min: float = 6.0, poll_s: float = 0.15,
        from_start: bool = False, provider=None, max_s: float = None, inbox: Path = None, state_dir: Path = None):
    out.mkdir(parents=True, exist_ok=True)
    wait_for(md)
    if cfg.get("variant") == "app":           # Pacenote 0.8: the register next to the transcript
        cfg.setdefault("register_dir", str(md.parent))
    cfg.setdefault("call_md", str(md))         # 0.8.7: the Sales summary goes next to the transcript
    eng, stem = build_engine(cfg, md, out, now_sod, provider)
    (call_layout.sidecar(out / stem, ".app.run.json")).write_text(json.dumps(run_info(
        eng.provider, cfg, md=md.name, mode="live", variant=cfg.get("variant"),
        cache_ttl=cfg.get("cache_ttl"), started=time.strftime("%Y-%m-%d %H:%M:%S")), ensure_ascii=False, indent=1))
    lr = LiveRun(cfg, eng, md, inbox, from_start)
    while True:
        lr.step()
        lr.touch_state(state_dir)
        if lr.follow_status(status) or lr.timed_out(idle_min, max_s):
            break
        time.sleep(poll_s)
    eng.drain(60)
    try:
        eng.final_wrapup()
    except Exception as e:  # noqa: BLE001
        print("wrapup:", repr(e), flush=True)
    (call_layout.sidecar(out / stem, ".app.summary.json")).write_text(json.dumps(lr.summary(), indent=1))
    finish_run_info(out, stem)
    return eng


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--md", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--status", type=Path)
    ap.add_argument("--idle-min", type=float, default=6.0)
    ap.add_argument("--from-start", action="store_true")
    ap.add_argument("--tips-out", type=Path, help="write tips into this file (the recorder's tips file in app mode)")
    ap.add_argument("--inbox", type=Path)
    ap.add_argument("--state-dir", type=Path)
    a = ap.parse_args(argv)
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    if a.tips_out:
        cfg["tips_out"] = str(a.tips_out)
    run(cfg, a.md, a.out, a.status, a.idle_min, from_start=a.from_start, inbox=a.inbox, state_dir=a.state_dir)


if __name__ == "__main__":
    main()
