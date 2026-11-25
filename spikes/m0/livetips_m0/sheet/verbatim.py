"""PB-102 (Meeting Sheet, owner 24.11): the Verbatim block.

A Verbatim block holds a text copied unchanged from one source: a file (one of the call's sources or any file on this
Mac) or a text pasted into the block's settings. The model never sees the block and never rewrites or shortens it.
Optionally only some sections are taken: "3" or "1-4", counted by the Markdown headings of the file.

The block keeps its settings and its copy in `block["verbatim"]` (never as facts, so Refresh, the clean-up and the
readiness rules leave it alone):
    {"source": {"kind": "file", "path": "..."} | {"kind": "text", "text": "..."},
     "sections": "1-4" | "",
     "text": the copy, "status": "ok" | "failed", "error": why it failed, "sha256": of the copy, "checked_at": ...}

The copy is checked against the source after the sheet is written (check_written): the source is read again, the same
sections are taken and the text must be the same, character for character. Any difference, a missing file or a
section that is not there fails the block visibly (status failed, the reason, no text shown as current).
"""
import hashlib
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from . import readers

TYPE = "verbatim"
# Markdown and plain text are copied byte for byte (strict UTF-8, line ends kept); other formats as the text read from them
RAW_EXT = (".md", ".markdown", ".txt", ".text")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")
_HEAD = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t#]*$")
_SPEC = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def is_verbatim(b: dict) -> bool:
    return isinstance(b, dict) and b.get("type") == TYPE


def settings_of(d: dict) -> dict:
    """The block's settings from a request block or an old sheet block: source and sections (nothing else)."""
    v = d.get("verbatim") if isinstance(d.get("verbatim"), dict) else {}
    src = v.get("source") if isinstance(v.get("source"), dict) else {}
    if src.get("kind") == "text":
        source = {"kind": "text", "text": str(src.get("text") or "")}
    elif src.get("path"):
        source = {"kind": "file", "path": str(src["path"])}
    else:
        source = {}
    return {"source": source, "sections": str(v.get("sections") or "").strip()}


def read_source(source: dict) -> Tuple[Optional[str], Optional[str]]:
    """(text, None) or (None, why). A file is read again on every build, so Refresh takes its new text."""
    if source.get("kind") == "text":
        t = source.get("text") or ""
        return (t, None) if t.strip() else (None, "no text pasted")
    path = source.get("path")
    if not path:
        return None, "no source chosen"
    p = os.path.expanduser(path)
    if not os.path.isfile(p):
        return None, "file not found: " + os.path.basename(p)
    if p.lower().endswith(RAW_EXT):
        try:
            return Path(p).read_bytes().decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "the file is not UTF-8 text"
        except OSError as e:
            return None, "cannot read the file (" + type(e).__name__ + ")"
    r = readers.read(p)
    if r.get("error"):
        return None, str(r["error"])
    return r.get("text") or "", None


def headings(text: str) -> List[Tuple[int, int, str]]:
    """(character offset, level, title) of each Markdown heading; a '#' line inside a code fence is not a heading."""
    out, pos, fence = [], 0, None
    for line in text.splitlines(keepends=True):
        m = _FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
        elif fence is None:
            h = _HEAD.match(line.rstrip("\r\n"))
            if h:
                out.append((pos, len(h.group(1)), h.group(2).strip()))
        pos += len(line)
    return out


def sections(text: str) -> List[Tuple[int, int, str]]:
    """The numbered sections: (start, end, title). A section starts at a heading of the section level and runs to the
    next heading of that level or higher (or the end). The section level is the highest heading level used at least
    twice in the file (one '# Title' over several '## ' parts gives the '## ' parts), else the highest one used."""
    hs = headings(text)
    if not hs:
        return []
    levels = sorted({lv for _, lv, _ in hs})
    twice = [lv for lv in levels if sum(1 for _, x, _ in hs if x == lv) >= 2]
    level = twice[0] if twice else levels[0]
    starts = [(pos, title) for pos, lv, title in hs if lv == level]
    out = []
    for i, (pos, title) in enumerate(starts):
        end = len(text)
        for p2, lv2, _ in hs:
            if p2 > pos and lv2 <= level:
                end = p2
                break
        out.append((pos, end, title))
    return out


def parse_spec(spec: str) -> Optional[Tuple[int, int]]:
    """'3' -> (3, 3), '1-4' -> (1, 4); '' -> None (the whole text). Raises ValueError on anything else."""
    s = (spec or "").strip()
    if not s:
        return None
    m = _SPEC.match(s.replace("–", "-"))
    if not m:
        raise ValueError("sections must be a number or a range like 1-4")
    a = int(m.group(1))
    b = int(m.group(2) or a)
    if a < 1 or b < a:
        raise ValueError("sections must be a number or a range like 1-4")
    return a, b


def pick(text: str, spec: str) -> Tuple[Optional[str], Optional[str]]:
    """The part of `text` the block shows: all of it, or sections a..b as one unchanged slice of the source."""
    try:
        rng = parse_spec(spec)
    except ValueError as e:
        return None, str(e)
    if rng is None:
        return text, None
    secs = sections(text)
    if not secs:
        return None, "the source has no headings to count sections by"
    a, b = rng
    if b > len(secs):
        return None, "the source has only %d section(s)" % len(secs)
    out = text[secs[a - 1][0]:secs[b - 1][1]]
    return out, None


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _fail(block: dict, why: str, checked_at: str) -> None:
    v = block.setdefault("verbatim", {})
    v.update({"text": "", "status": "failed", "error": why, "checked_at": checked_at})
    v.pop("sha256", None)


def fill(block: dict, checked_at: str) -> None:
    """Copies the text into the block (never through a model). A failure leaves no old text behind."""
    st = settings_of(block)
    block["verbatim"] = dict(st)
    block["facts"] = []
    if not st["source"]:
        return _fail(block, "no source chosen", checked_at)
    src, why = read_source(st["source"])
    if src is None:
        return _fail(block, why or "cannot read the source", checked_at)
    out, why = pick(src, st["sections"])
    if out is None:
        return _fail(block, why or "cannot take the sections", checked_at)
    if not out.strip():
        return _fail(block, "the chosen part of the source is empty", checked_at)
    block["verbatim"].update({"text": out, "status": "ok", "sha256": _sha(out), "checked_at": checked_at})
    block["verbatim"].pop("error", None)


def check(block: dict) -> Optional[str]:
    """None when the block's text is exactly the source's text now (same sections), else why not."""
    v = block.get("verbatim") or {}
    if v.get("status") != "ok":
        return None                      # already failed with its reason
    st = settings_of(block)
    src, why = read_source(st["source"])
    if src is None:
        return why or "cannot read the source"
    want, why = pick(src, st["sections"])
    if want is None:
        return why
    have = v.get("text")
    if not isinstance(have, str) or have != want or v.get("sha256") != _sha(have):
        return "the copy does not match the source"
    return None


def check_written(sheet: dict, checked_at: str) -> List[str]:
    """Checks every Verbatim block of a sheet as read back from its file; a mismatch fails that block in `sheet`.
    Returns the titles of the blocks that failed here."""
    failed = []
    for b in sheet.get("blocks") or []:
        if not is_verbatim(b):
            continue
        why = check(b)
        if why:
            _fail(b, why, checked_at)
            failed.append(str(b.get("title") or TYPE))
    return failed


def carry_over(old: dict, blocks: List[dict]) -> List[dict]:
    """Refresh without the wizard's block list: the old sheet's Verbatim blocks (id, title, settings, visibility) come
    back at their old place, right after the block that was before them."""
    olds = sorted([b for b in (old or {}).get("blocks") or [] if isinstance(b, dict)], key=lambda b: b.get("order", 0))
    out = list(blocks)
    have = {b.get("id") for b in out}
    for i, ob in enumerate(olds):
        if not is_verbatim(ob) or ob.get("id") in have:
            continue
        nb = {"id": ob.get("id") or "b-verbatim", "type": TYPE, "title": ob.get("title") or "Verbatim",
              "visible": bool(ob.get("visible", True)), "only_if_data": False, "facts": [], "edited_by_user": False,
              "verbatim": settings_of(ob)}
        at = 0
        for prev in reversed(olds[:i]):
            j = next((k for k, x in enumerate(out) if x.get("id") == prev.get("id")
                      or (not is_verbatim(prev) and x.get("type") == prev.get("type"))), None)
            if j is not None:
                at = j + 1
                break
        out.insert(at, nb)
        have.add(nb["id"])
    for k, b in enumerate(out):
        b["order"] = k
    return out
