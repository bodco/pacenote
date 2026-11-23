"""Transcript and timing parsing (live-transcriber file format)."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from . import call_layout
from typing import Dict, List, Optional

LINE_RE = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s+(.+?)(?:\s+\[(S\d+)\])?\s+\((\w{2})\):\s?(.*)$")
TR_MARK_RE = re.compile(r"\s\(\u2190 ([a-z]{2,3}|\?)\)$")   # PB-30: " (← fr)" ends a row whose speech was in another language


def hms_to_s(v: str) -> float:
    v = v.strip()
    h, m, s = v.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def s_to_hms(v: float) -> str:
    v = int(v) % 86400
    return "{:02d}:{:02d}:{:02d}".format(v // 3600, (v % 3600) // 60, v % 60)


@dataclass
class Line:
    line_id: int
    t: str                      # HH:MM:SS, segment start as written by the recorder
    speaker: str                # "me" or "them"
    label: str
    stag: Optional[str]
    lang: str
    text: str
    seg_end: Optional[float] = None   # seconds of day: end of the speech segment
    written: Optional[float] = None   # seconds of day: line appended to the transcript
    dur: Optional[float] = None
    seq: Optional[int] = None         # PB-30: the recorder's id of the line (timing record), keys a "translated" event
    translated_from: Optional[str] = None   # PB-30: the row's " (\u2190 fr)" marker, "und" for " (← ?)"

    @property
    def t_s(self) -> float:
        return hms_to_s(self.t)


def parse_line(raw: str, line_id: int, me_label: str) -> Optional[Line]:
    m = LINE_RE.match(raw.rstrip("\n"))
    if not m:
        return None
    t, label, stag, lang, text = m.groups()
    speaker = "me" if label.strip() == me_label else "them"
    text = text.strip()
    mk = TR_MARK_RE.search(text)
    src = None
    if mk:                                   # the tips see the words only; the row event and translations match them
        src = "und" if mk.group(1) == "?" else mk.group(1)
        text = text[:mk.start()].rstrip()
    return Line(line_id, t, speaker, label.strip(), stag, lang, text, translated_from=src)


def load_timing(path: Path) -> List[dict]:
    out = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def load_call(md_path: Path, me_label: str) -> List[Line]:
    """Parse a transcript and attach timing records (same order, matched by t and channel)."""
    lines = []
    n = 0
    for raw in md_path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = parse_line(raw, n, me_label)
        if ln:
            lines.append(ln)
            n += 1
    timing = load_timing(call_layout.sidecar(str(md_path), ".timing.jsonl"))
    pool: Dict[tuple, List[dict]] = {}
    for rec in timing:
        if rec.get("event"):
            continue                              # PB-30: a "translated" event, not a line's timing
        pool.setdefault((rec.get("t"), rec.get("ch")), []).append(rec)
    for ln in lines:
        recs = pool.get((ln.t, ln.speaker))
        if recs:
            rec = recs.pop(0)
            if rec.get("seg_end"):
                ln.seg_end = hms_to_s(rec["seg_end"])
            if rec.get("written"):
                ln.written = hms_to_s(rec["written"])
            ln.dur = rec.get("dur")
    return lines
