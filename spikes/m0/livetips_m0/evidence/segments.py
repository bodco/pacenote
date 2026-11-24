"""PB-52 G2: the segmenter of spec section 8.2 (deterministic, free), on the snippets lookup.chunks_at wrote.

    segments(rows, kind) -> [{"ords", "snippet_ids", "label", "heading", "chars", "seg_hash"}]
    of_revision(store, revision_id) -> the same for one stored revision

A segment is whole snippets of one heading section, up to 6000 characters; it never crosses a heading and never packs
two sections together. A transcript and call notes are cut from their start across headings. For every kind a
segment also ends where the snippets' client_set changes, so it carries one client label. It does not end at the
finer cuts of the chunk invariant: neighbouring snippets with the same client_set stay together.
seg_hash = sha256(client label | heading path | segment snippet texts); the context snippet and the extractor are not
in it, so a prompt or rules change keeps it and an edit in another section leaves it byte-equal.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, List

MAX_CHARS = 6000
ACROSS_HEADINGS = ("transcript", "notes")       # cut from the start, across headings (section 8.2)


def seg_hash(label: str, heading: str, texts: Iterable[str]) -> str:
    h = hashlib.sha256()
    h.update((label or "").encode("utf-8"))
    h.update(b"|")
    h.update((heading or "").encode("utf-8"))
    h.update(b"|")
    h.update("\n".join(texts).encode("utf-8"))
    return h.hexdigest()


def segments(rows: List[dict], kind: str) -> List[dict]:
    """rows: the revision's snippets in `ord` order, each with id, ord, text, heading and client_set."""
    across = kind in ACROSS_HEADINGS
    out: List[dict] = []
    cur: List[dict] = []

    def flush():
        if cur:
            label = cur[0]["client_set"] or ""
            heading = "" if across else (cur[0]["heading"] or "")
            texts = [r["text"] for r in cur]
            out.append({"ords": [r["ord"] for r in cur], "snippet_ids": [r["id"] for r in cur], "label": label,
                        "heading": heading, "chars": sum(len(t) for t in texts),
                        "seg_hash": seg_hash(label, heading, texts)})
            cur.clear()

    for r in rows:
        if cur:
            size = sum(len(x["text"]) for x in cur)
            if (r["client_set"] or "") != (cur[0]["client_set"] or "") or size + len(r["text"]) > MAX_CHARS or (
                    not across and (r["heading"] or "") != (cur[0]["heading"] or "")):
                flush()
        cur.append(r)
    flush()
    return out


def of_revision(store, revision_id: int) -> List[dict]:
    kind = store.one("SELECT s.kind FROM revision rv JOIN source s ON s.id=rv.source_id WHERE rv.id=?", (revision_id,))
    rows = [dict(r) for r in store.q("SELECT id, ord, text, heading, client_set FROM snippet WHERE revision_id=?"
                                     " ORDER BY ord", (revision_id,))]
    return segments(rows, kind[0] if kind else "file")
