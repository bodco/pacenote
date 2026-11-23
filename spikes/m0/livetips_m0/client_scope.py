"""PB-70 (owner 23.11): the client scope of a call, decided before anything is read or ranked.

One project folder can hold the calls of two clients (docs/acme and docs/globex, the series "Acme weekly status" and
"Globex weekly"). Without a scope the preparation and the search during the call mixed in the other client's facts:
a fresh file of the other client's folder, the other client's items of the Tracker, its lines in the decisions and in
the Current State page, its notes in the folder search.

- Client folders: folders of the project (up to 3 levels down) whose name is also the first word of a meeting series
  in live/ or a company of the call's other side. Only siblings count as other clients: docs/acme and docs/globex.
- The call's client: the client folder named by the other side's companies, else by the call's title or its file
  name, but only a folder a company backs (this call's or one kept in an earlier Meeting Sheet). Two clients named,
  none named, or no sibling client folder: no scope, nothing is hidden (today's behaviour).
- The preparation keeps its decision in the sheet (client_scope), "none" included, and the search during the call
  follows it; it decides by itself only for a call without a sheet.
- Left out: a file or a call of another client (by its path), a register item of another client's series, a passage
  or a line of a shared file (decisions, Current State) that names only another client, with its section. Items
  without a client stay. A source the PM added by hand for this call is never left out (sheet/build.py, lookup.py).

    decide(root, title, companies, call="") -> scope or None
    side(scope, path) / series_side(scope, series) / text_side(scope, text) -> "own" | "other" | None
    filter_text(scope, text) -> (text, lines left out)
"""
import glob
import json
import os
import re
from typing import Dict, List, Optional, Tuple

MAX_DEPTH = 3
MAX_SHEETS = 200
MAX_SHEET_BYTES = 2_000_000
SKIP_DIRS = {"live", "_notion", "pacenote", "meeting-sheet", "node_modules", "__pycache__", "repos", "build", "dist"}
CALL_NAME = re.compile(r"^\d{4}-\d\d-\d\d-(?:\d{4}-)?([^\W_]+)", re.U)
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
WORD = re.compile(r"[^\W_]+", re.U)


def slug(s: str) -> str:
    return "".join(WORD.findall((s or "").lower()))


def _names(text: str) -> set:
    """The words of a text and every two words written together ("Acme Corp" also names the folder acme-corp)."""
    w = [x.lower() for x in WORD.findall(text or "")]
    return set(w) | {a + b for a, b in zip(w, w[1:])}


def _series_words(live: str) -> set:
    """The first word of every meeting series of the project (call folders and call files in live/)."""
    out = set()
    try:
        names = os.listdir(live)
    except OSError:
        return out
    for n in names:
        m = CALL_NAME.match(n.lower())
        if m:
            out.add(m.group(1))
        full = os.path.join(live, n)
        if m and os.path.isdir(full):
            try:
                out.update(mm.group(1) for mm in (CALL_NAME.match(x.lower()) for x in os.listdir(full)) if mm)
            except OSError:
                pass
    return out


def _client_dirs(root: str, hints: set) -> Dict[str, Dict[str, str]]:
    """{parent rel path: {label: rel path}} of the folders whose name (as one word) is a hint."""
    out: Dict[str, Dict[str, str]] = {}
    for d, dirs, _files in os.walk(root):
        rel_d = os.path.relpath(d, root)
        depth = 0 if rel_d == "." else rel_d.count(os.sep) + 1
        dirs[:] = sorted(x for x in dirs if not x.startswith((".", "_archived", "_to_delete")) and x.lower() not in SKIP_DIRS)
        if depth >= MAX_DEPTH:
            dirs[:] = []
        for x in dirs:
            label = slug(x)
            if label and label in hints:
                out.setdefault("" if rel_d == "." else rel_d, {})[label] = os.path.normpath(os.path.join(rel_d, x))
    return out


def _recorded_companies(live: str) -> List[str]:
    """The other side's companies kept in the project's earlier Meeting Sheets (live/, call folders and their .work/)."""
    out: List[str] = []
    n = 0
    for pattern in ("*.sheet.json", "*/*.sheet.json", "*/.work/*.sheet.json"):
        for p in sorted(glob.glob(os.path.join(glob.escape(live), pattern))):
            n += 1
            if n > MAX_SHEETS:
                return out
            try:
                if os.path.getsize(p) > MAX_SHEET_BYTES:
                    continue
                with open(p, encoding="utf-8") as f:
                    theirs = ((json.load(f).get("participants") or {}).get("theirs")) or []
            except (OSError, ValueError, AttributeError):
                continue
            out += [str(x.get("company") or "") for x in theirs if isinstance(x, dict) and str(x.get("company") or "").strip()]
    return out


def _backs(company: str, label: str) -> bool:
    return label in _names(company) or label == slug(company)


def decide(root: str, title: str = "", companies: Optional[List[str]] = None, call: str = "") -> Optional[dict]:
    """The scope of one call: {client, others, folders: {label: [rel paths]}, root, why}, or None when the client
    cannot be told apart (then nothing is hidden).

    The call's client folder must be backed by a company: one of this call's other side, or one kept in an earlier
    Meeting Sheet of the project. The title or the call's file name only picks among such folders and never makes a
    folder a client on its own, so a project with only topic folders (docs/design, docs/research, series "Design
    review") gets no scope. The other clients are the own folder's siblings named like a company or a series (the
    G0 fixture has no earlier sheets to back them). The engine passes no companies, so without a sheet only the
    earlier sheets can back a client."""
    root = os.path.realpath(os.path.expanduser(root or ""))
    if not root or not os.path.isdir(root):
        return None
    companies = [c for c in (companies or []) if str(c or "").strip()]
    live = os.path.join(root, "live")
    recorded = _recorded_companies(live)
    backing = companies + recorded
    hints = _series_words(live) | {slug(c) for c in backing} | set().union(*[_names(c) for c in backing])
    groups = _client_dirs(root, hints)
    labels = {lb for g in groups.values() for lb in g}
    if len(labels) < 2:
        return None
    backed = {lb for lb in labels if any(_backs(c, lb) for c in backing)}
    named = {lb for lb in backed if any(_backs(c, lb) for c in companies)}
    why = "the other side's company"
    by_title = {lb for lb in labels if lb in _names(title) or lb in _names(os.path.basename(call or "").replace("-", " "))}
    if not named:
        named, why = by_title & backed, "the call's title"
    elif by_title and by_title != named:
        return None                     # the company says one client, the title another: not sure, hide nothing
    if len(named) != 1:
        return None
    own = next(iter(named))
    others = sorted({lb for g in groups.values() if own in g for lb in g if lb != own})
    if not others:
        return None
    folders: Dict[str, List[str]] = {}
    for g in groups.values():
        for lb, rel in g.items():
            if lb == own or lb in others:
                folders.setdefault(lb, []).append(rel)
    return {"client": own, "others": others, "folders": folders, "root": root, "why": why}


def from_sheet(sheet) -> Optional[dict]:
    """The scope the preparation decided and kept in the sheet (client_scope), or None."""
    sc = sheet.get("client_scope") if isinstance(sheet, dict) else None
    return sc if isinstance(sc, dict) and sc.get("client") and sc.get("others") and sc.get("root") else None


def for_sheet(req: dict) -> dict:
    """What the sheet keeps: the scope, or {client: None, why} when the preparation decided to hide nothing (switched
    off, or not sure). Always written, so the search during the call never decides differently."""
    sc = req.get("_client_scope")
    if sc:
        return public(sc)
    return {"client": None, "why": "off" if req.get("client_scope") is False else "not sure"}


def for_request(req: dict) -> Optional[dict]:
    """The scope of a preparation request (project root, meeting title, the other side's companies). A request can
    switch it off with client_scope: false."""
    if req.get("client_scope") is False:
        return None
    project = req.get("project") or {}
    theirs = (req.get("participants") or {}).get("theirs") or []
    companies = [p.get("company", "") for p in theirs if isinstance(p, dict)]
    title = (req.get("meeting") or {}).get("title") or req.get("title") or ""
    try:
        return decide(project.get("root") or req.get("root") or "", title, companies)
    except Exception:  # noqa: BLE001  (no scope is today's behaviour)
        return None


def with_scope(req: dict) -> dict:
    """The request with its scope decided once (_client_scope; None = no scope)."""
    return req if "_client_scope" in req else dict(req, _client_scope=for_request(req))


def public(scope: Optional[dict]) -> Optional[dict]:
    return {k: scope[k] for k in ("client", "others", "folders", "root", "why")} if scope else None


# ---------------------------------------------------------------- what belongs where

def _pick(scope: dict, found: set) -> Optional[str]:
    if scope["client"] in found:
        return "own"
    return "other" if found & set(scope["others"]) else None


def side(scope: Optional[dict], path: str) -> Optional[str]:
    """own | other | None (project-wide, or outside the project) for a file or folder, by its path in the project:
    under a client folder, or a word of a folder or file name (live/2026-11-04-globex-weekly/...)."""
    if not scope or not path:
        return None
    try:
        rel = os.path.relpath(os.path.realpath(os.path.expanduser(str(path))), scope["root"])
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    for lb, rels in scope.get("folders", {}).items():
        if any(rel == r or rel.startswith(r + os.sep) for r in rels):
            return "own" if lb == scope["client"] else "other"
    parts = rel.split(os.sep)
    parts[-1] = os.path.splitext(parts[-1])[0]
    return _pick(scope, set().union(*(_names(p) for p in parts)))


def series_side(scope: Optional[dict], series: str) -> Optional[str]:
    """own | other | None for a register item's series ("|globexweekly")."""
    if not scope or not series:
        return None
    s = slug(str(series).split("|")[-1])
    if s.startswith(scope["client"]):
        return "own"
    return "other" if any(s.startswith(o) for o in scope["others"]) else None


def text_side(scope: Optional[dict], text: str) -> Optional[str]:
    """own when the text names the call's client, other when it names only another client, else None."""
    if not scope or not text:
        return None
    return _pick(scope, _names(text))


def item_side(scope: Optional[dict], item: dict) -> Optional[str]:
    """A register item: by its series, else by its call's file name, else by its text."""
    return (series_side(scope, item.get("series") or "") or side_of_name(scope, item.get("call") or "")
            or text_side(scope, item.get("text") or ""))


def side_of_name(scope: Optional[dict], name: str) -> Optional[str]:
    m = CALL_NAME.match(os.path.basename(name or "").lower()) if scope else None
    return _pick(scope, {m.group(1)}) if m else None


def filter_text(scope: Optional[dict], text: str) -> Tuple[str, int]:
    """A shared file without the other clients' parts: a heading that names only another client drops its section,
    a line that names only another client drops itself. Lines without a client stay."""
    if not scope or not text:
        return text, 0
    out, dropped, skip_level = [], 0, 0
    for line in text.split("\n"):
        h = HEADING.match(line.strip())
        if h:
            level = len(h.group(1))
            if skip_level and level > skip_level:
                dropped += 1
                continue
            skip_level = level if text_side(scope, h.group(2)) == "other" else 0
            if skip_level:
                dropped += 1
                continue
            out.append(line)
            continue
        if skip_level and line.strip():
            dropped += 1
            continue
        if line.strip() and text_side(scope, line) == "other":
            dropped += 1
            continue
        out.append(line)
    return "\n".join(out), dropped


def filter_register(scope: Optional[dict], raw: str) -> str:
    """commitments.jsonl without the events of other clients' items (for the folder search)."""
    if not scope:
        return raw
    rows, other_ids = [], set()
    for ln in raw.splitlines():
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict):
            rows.append((ln, e))
            if e.get("op") == "add" and item_side(scope, e) == "other":
                other_ids.add(e.get("id"))
    return "\n".join(ln for ln, e in rows if e.get("id") not in other_ids)
