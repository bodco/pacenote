"""PB-101 (Meeting Sheet, owner 24.11): custom blocks.

A custom block is the PM's own block: a title, an instruction (what it does on this call), its own sources (a file or
a folder, one of the call's sources, the transcript of a past call, a link, free text), an output format and a length.
The app keeps the blocks in a personal library and adds them to a call; the request carries each one as
    {"type": "custom", "id": "b-custom-...", "title": "...",
     "custom": {"lib_id": "...", "instruction": "...", "format": "bullets", "length": "medium",
                "sources": [{"kind": "file" | "folder" | "call_source" | "transcript" | "url" | "text",
                             "path": "...", "uri": "...", "text": "...", "sections": "1-4", "title": "..."}]}}

Format "verbatim" is the built-in Verbatim block (sheet/verbatim.py, PB-102): the build turns the block into a
Verbatim block (type "verbatim") that copies its first file or text source unchanged and is checked after the write;
the custom settings ride along under "custom" so the app can edit it again. Every other format goes to the model like
a built-in block, answered under the block's id (several custom blocks per call), from its own sources only.
"""
import os
import re
from typing import Dict, List, Optional

TYPE = "custom"
FORMATS = ("summary", "bullets", "table", "qa", "checklist", "verbatim")
LENGTHS = {"short": 3, "medium": 6, "long": 12}
KINDS = ("file", "folder", "call_source", "transcript", "url", "text")

_FORMAT_RULE = {
    "summary": "a short summary: each fact one or two sentences of plain prose; no label needed",
    "bullets": "a list of points: each fact one point with a short label",
    "table": "a table: each fact one row, label = the row's name, text = its values separated by ' | '",
    "qa": "questions and answers: label = the question, text = the answer the sources give (not_found when they do not)",
    "checklist": "a checklist: each fact one item to check or do on the call, label = a short name of the item",
}


def is_custom(b) -> bool:
    return isinstance(b, dict) and b.get("type") == TYPE


def key(b: dict) -> str:
    """The key of a block's facts: a custom block's id (several per call), else the block type."""
    return str(b.get("id") or TYPE) if is_custom(b) else b.get("type", "")


def uid(raw, seen: set, i: int, fallback: str) -> str:
    """A safe, unique block id (shared with the Verbatim blocks: one id space per sheet)."""
    vid = re.sub(r"[^a-zA-Z0-9_-]", "-", str(raw or ""))[:60] or fallback
    while vid in seen:
        vid += "-%d" % i
    seen.add(vid)
    return vid


def _source(x) -> Optional[dict]:
    if not isinstance(x, dict) or x.get("kind") not in KINDS:
        return None
    out = {"kind": x["kind"]}
    for k in ("path", "uri", "title", "sections", "source_id"):
        if x.get(k) not in (None, ""):
            out[k] = str(x[k]).strip() if k == "sections" else str(x[k])
    if x["kind"] == "text":
        out["text"] = str(x.get("text") or "")
    if not out.get("sections"):
        out.pop("sections", None)
    return out


def settings_of(d: dict) -> dict:
    """A block's custom settings from a request block or an old sheet block, cleaned (unknown values to defaults)."""
    c = d.get("custom") if isinstance(d.get("custom"), dict) else {}
    out = {"instruction": str(c.get("instruction") or "").strip()[:2000],
           "format": c.get("format") if c.get("format") in FORMATS else "bullets",
           "length": c.get("length") if c.get("length") in LENGTHS else "medium",
           "sources": [s for s in (_source(x) for x in (c.get("sources") or [])) if s]}
    if c.get("lib_id"):
        out["lib_id"] = str(c["lib_id"])
    return out


def is_verbatim_format(d: dict) -> bool:
    return settings_of(d)["format"] == "verbatim"


VERBATIM_NO_SOURCE = "a Verbatim block copies a file or pasted text, not a link or a folder"


def verbatim_settings(cs: dict) -> dict:
    """The Verbatim block's settings (sheet/verbatim.py) from the custom block's first file or text source."""
    for s in cs.get("sources") or []:
        if s["kind"] == "text" and s.get("text", "").strip():
            return {"source": {"kind": "text", "text": s["text"]}, "sections": s.get("sections", "")}
        if s["kind"] in ("file", "call_source", "transcript") and s.get("path"):
            p = os.path.expanduser(s["path"])
            if not os.path.isdir(p):
                return {"source": {"kind": "file", "path": s["path"]}, "sections": s.get("sections", "")}
    return {"sections": ""}


def block(d: dict, bid: str, title: str, order: int) -> dict:
    """The sheet block of a request's custom block: a Verbatim block for format verbatim, else a custom block."""
    cs = settings_of(d)
    b = {"id": bid, "title": title, "order": order, "visible": bool(d.get("visible", True)), "only_if_data": False,
         "facts": [], "edited_by_user": False, "custom": cs}
    if cs["format"] == "verbatim":
        b.update({"type": "verbatim", "verbatim": verbatim_settings(cs)})
    else:
        b["type"] = TYPE
    return b


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def add_sources(blocks: List[dict], raw: List[dict]) -> List[dict]:
    """Each custom block's sources join the build's sources (one read, one report). A source the call already has
    (same file or link, no section pick) is used under its id; the others get ids s-<block id>-<n>. The ids land in
    the block's custom.source_ids; a folder's files are added after the folders are expanded (with_folder_files).
    A source added here names its block ("block"): only that block may cite it, and call sync keeps its path, link
    and title on this Mac."""
    out = list(raw)
    by_path = {_real(s["path"]): s for s in out if s.get("path")}
    by_uri = {s.get("uri") or s.get("url"): s for s in out if s.get("uri") or s.get("url")}
    by_id = {s.get("id"): s for s in out if s.get("id")}
    for b in blocks:
        if not is_custom(b) or not b.get("visible", True):
            continue                    # a hidden block is not asked, so its sources are not read either
        cs = b.setdefault("custom", settings_of(b))
        ids: List[str] = []
        for n, s in enumerate(cs.get("sources") or [], 1):
            k = s["kind"]
            sid = "s-%s-%d" % (b["id"], n)
            if k == "text":
                if not s.get("text", "").strip():
                    continue
                out.append({"id": sid, "type": "text", "title": s.get("title") or "Text for " + str(b.get("title") or ""),
                            "text": s["text"], "block": b["id"]})
                ids.append(sid)
                continue
            if k == "url" or (k == "call_source" and s.get("uri") and not s.get("path")):
                u = s.get("uri") or ""
                if not u:
                    continue
                have = by_uri.get(u)
                if have:
                    ids.append(have["id"])
                    continue
                x = {"id": sid, "uri": u, "block": b["id"]}
                if s.get("title"):
                    x["title"] = s["title"]
                out.append(x)
                by_uri[u] = x
                ids.append(sid)
                continue
            if k == "call_source" and s.get("source_id") in by_id and not s.get("sections"):
                ids.append(s["source_id"])
                continue
            p = s.get("path")
            if not p:
                continue
            have = by_path.get(_real(p))
            if have and not s.get("sections") and have.get("id"):
                ids.append(have["id"])
                continue
            typ = "folder" if (k == "folder" or os.path.isdir(os.path.expanduser(p))) else ("transcript" if k == "transcript" else "file")
            x = {"id": sid, "type": typ, "path": p, "block": b["id"]}
            if s.get("title"):
                x["title"] = s["title"]
            if s.get("sections") and typ != "folder":
                x["sections"] = s["sections"]          # only these sections are read (sheet/verbatim.py pick)
            out.append(x)
            if not s.get("sections"):
                by_path[_real(p)] = x
            ids.append(sid)
        cs["source_ids"] = list(dict.fromkeys(ids))
    return out


def with_folder_files(blocks: List[dict], srcs: List[dict]) -> None:
    """After the folders are expanded: a custom block that names a folder also reads the files taken from it (and they
    belong to custom blocks like the folder)."""
    own = {s["id"]: s["block"] for s in srcs if s.get("block")}
    for s in srcs:
        if s.get("parent") in own and not s.get("block"):
            s["block"] = own[s["parent"]]
    for b in blocks:
        if not is_custom(b):
            continue
        cs = b.get("custom") or {}
        ids = list(cs.get("source_ids") or [])
        ids += [s["id"] for s in srcs if s.get("parent") in ids and s["id"] not in ids]
        cs["source_ids"] = ids


def pick_sections(srcs: List[dict], texts: Dict[str, str], estimate=None) -> List[str]:
    """A file source of a custom block with a section pick: only those sections are read (Markdown as it is, so
    headings and lists stay). A pick that does not fit the file makes the source unreadable with the reason.
    estimate(text) gives the new size in tokens. Returns notes for the report."""
    from . import verbatim as V
    notes = []
    for s in srcs:
        spec = s.get("sections")
        if not spec or s["id"] not in texts:
            continue
        part, why = V.pick(texts[s["id"]], spec)
        if part is None or not part.strip():
            texts.pop(s["id"], None)
            s["access"] = "unsupported"
            s["access_reason"] = why or "the chosen part of the source is empty"
            notes.append(f"Source {s.get('title') or s['id']}: sections {spec} not read ({s['access_reason']}).")
            continue
        texts[s["id"]] = part
        s["chars"] = len(part)
        if estimate:
            s["size_tokens"] = estimate(part)
    return notes


def block_line(b: dict) -> str:
    """The prompt line of a custom block: its key (the id), title, instruction, sources, format and length."""
    cs = b.get("custom") or {}
    ids = cs.get("source_ids") or []
    n = LENGTHS.get(cs.get("length"), 6)
    line = f'- {key(b)} ("{b.get("title", "")}"): a block the user made; answer it under the key "{key(b)}". '
    line += "What it does on this call: " + (cs.get("instruction") or "what the title says") + ". "
    line += ("Use only the sources " + ", ".join(ids) + " for it. ") if ids else "Use the meeting and the sources given. "
    line += "Output: " + _FORMAT_RULE.get(cs.get("format"), _FORMAT_RULE["bullets"]) + f". At most {n} facts"
    return line


def own_sources(b: dict, facts: List[dict]) -> List[dict]:
    """A block with its own sources cites only them (the meeting header too): other ids are dropped, and a found
    fact left with no source becomes needs_check (never shown as sourced when it is not)."""
    ids = set((b.get("custom") or {}).get("source_ids") or [])
    if not ids:
        return list(facts)
    ids.add("s-meeting")
    out = []
    for f in facts:
        g = dict(f)
        g["source_ids"] = [i for i in f.get("source_ids") or [] if i in ids]
        if g.get("status") == "found" and not g["source_ids"]:
            g["status"] = "needs_check"
        out.append(g)
    return out


def not_custom_sources(facts: List[dict], srcs: List[dict]) -> List[dict]:
    """A built-in block never cites a custom block's own source (sources with "block"): those ids are dropped, and a
    found fact left with no source becomes needs_check."""
    ids = {s["id"] for s in srcs if s.get("block")}
    if not ids:
        return list(facts)
    out = []
    for f in facts:
        g = dict(f)
        g["source_ids"] = [i for i in f.get("source_ids") or [] if i not in ids]
        if g.get("status") == "found" and not g["source_ids"]:
            g["status"] = "needs_check"
        out.append(g)
    return out


def cap(facts: List[dict], length: str) -> List[dict]:
    """The length the PM chose, kept even when the model writes more."""
    return list(facts)[:LENGTHS.get(length, 6)]
