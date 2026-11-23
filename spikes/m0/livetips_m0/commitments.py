"""Commitments register (Pacenote 0.8, idea 01; rules/commitments.yaml).

One append-only JSON-lines file per project, next to the transcripts: <project>/live/commitments.jsonl.
The app's engine and the skill both write it; the register is the events folded by id. Nothing here talks to
the network.

  python -m livetips_m0.commitments list <live folder> [--all]
  python -m livetips_m0.commitments done <live folder> <id>
"""
import argparse
import contextlib
import datetime as dt
import functools
import hashlib
import json
import re
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

from . import identity as ID

FILE = "commitments.jsonl"
WORD = re.compile(r"[\w']+", re.U)
_lock = threading.Lock()


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


@functools.lru_cache(maxsize=65536)
def call_key(call: str) -> str:
    """The call's transcript file name without folders, .md and the app's .app part (app and skill files match)."""
    n = Path(call or "").name
    for suf in (".md",):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return n.replace(".app", "")


def series_key(project: str, title: str) -> str:
    return "{}|{}".format(project or "", re.sub(r"[^\w]|_", "", (title or "").lower()))


def make_id(call: str, owner: str, text: str) -> str:
    raw = "{}|{}|{}".format(call_key(call), (owner or "me").strip().lower(), (text or "").strip().lower())
    return "c-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def words(s: str) -> set:
    return {w for w in WORD.findall((s or "").lower()) if len(w) > 2}


def similar(a: str, b: str, share: float = 0.6) -> bool:
    x, y = words(a), words(b)
    if not x or not y:
        return (a or "").strip().lower() == (b or "").strip().lower()
    return len(x & y) / min(len(x), len(y)) >= share


def norm_text(s: str) -> str:
    """The window's key of an On me line: lower case, letters and digits, single spaces (feed.js onMe)."""
    return " ".join(re.findall(r"[^\W_]+", (s or "").lower(), re.U))


def path_for(folder) -> Path:
    p = Path(folder)
    return p if p.name == FILE else p / FILE


def read_events(folder) -> List[dict]:
    p = path_for(folder)
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("op") and e.get("id"):
            out.append(e)
    return out


def history_of(item: dict) -> List[dict]:
    return item.get("history") or [{"id": item.get("id", ""), "call": item.get("call", ""), "text": item.get("text", "")}]


def in_call(item: dict, call: str) -> bool:
    """The item was said on this call (its own add or a later mention linked to it, PB-51)."""
    return any(call_key(h.get("call")) == call_key(call) for h in history_of(item))


def same_slot(item: dict, call: str, owner: str, text: str) -> bool:
    """The same call, the same owner and a similar text (rules/commitments.yaml > duplicates). PB-51: a mention of
    the item on another call that was linked to it counts as its call too."""
    if (item.get("owner") or "me").lower() != (owner or "me").lower():
        return False
    return any(call_key(h.get("call")) == call_key(call) and (similar(item["text"], text) or similar(h.get("text", ""), text))
               for h in history_of(item))


ITEM_KEYS = ("id", "text", "owner", "project", "series", "call", "t", "created", "due", "by", "from", "client")
NOT_DONE = dict(done_at="", done_source="", done_call="")
LIVE = ("open", "proposal")     # PB-51: a "we will try" item is a proposal, not an agreed open commitment


def _apply(it: dict, op: str, e: dict) -> None:
    if op == "done":
        it.update(status="done", done_at=e.get("at", ""), done_source=e.get("source", "manual"), done_call=e.get("call", ""))
    elif op == "reopen":
        it.update(status="proposal" if it.get("modality") == "tentative" else "open", **NOT_DONE)
    elif op == "tentative":     # PB-51: the item is a "we will try" (migration or the PM)
        it["modality"] = "tentative"
        if it["status"] == "open":
            it["status"] = "proposal"
    elif op == "agree":         # PB-51: the PM (or a later agreed mention) made the proposal a commitment
        it["modality"] = "agreed"
        if it["status"] == "proposal":
            it["status"] = "open"
    elif op == "crm_link":      # the Swift fold's HubSpot task link (CRMTaskFlow), read here for the Tracker ID key
        it.update(crm_task=e.get("crm_task", ""), crm_portal=e.get("crm_portal", ""), notion_note="")
    elif op == "crm_unlink":
        it.update(crm_task="", crm_portal="")
    elif op == "drop":
        it["status"] = "dropped"
    elif op == "edit":          # 0.8.7 (rules/commitments.yaml > edit): the PM changed text, owner or due on the call page
        for k in ("text", "owner", "due"):
            if k in e and e[k] is not None:
                it[k] = str(e[k])
        if e.get("source") != "notes" and (e.get("text") is not None or e.get("owner") is not None):
            it["edited_at"] = str(e.get("at") or "0")       # PB-51: a PM edit, it wins when the item is joined
    elif op == "link":          # 0.8.6 (rules/notion-tasks.yaml): the item is a task in the PM's Notion
        it.update(notion_page=e.get("notion_page", ""), notion_note="")
    elif op == "unlink":
        it.update(notion_page="", notion_note="removed" if e.get("reason", "removed") == "removed" else "")


def _add(items: Dict[str, dict], alias: Dict[str, str], e: dict) -> None:
    cid = e.get("id")
    if cid in items or cid in alias:
        return
    twin = next((i for i in items.values() if i["status"] in LIVE and same_slot(i, e.get("call"), e.get("owner"), e.get("text", ""))), None)
    if twin:
        alias[cid] = twin["id"]
        return
    modality = "tentative" if e.get("modality") == "tentative" else ("agreed" if e.get("modality") == "agreed" else "")
    items[cid] = dict({k: e.get(k, "") for k in ITEM_KEYS}, status="proposal" if modality == "tentative" else "open",
                      modality=modality, notion_page="", notion_note="", crm_task="", crm_portal="", ids=[cid], edited_at="",
                      history=[{"id": cid, "call": e.get("call", ""), "text": e.get("text", ""), "created": e.get("created", "")}],
                      **NOT_DONE)


def root(alias: Dict[str, str], cid: str) -> str:
    for _ in range(50):
        if cid not in alias:
            break
        cid = alias[cid]
    return cid


def _join(items: Dict[str, dict], alias: Dict[str, str], parked: Dict[str, dict], cid: str, to: str, n: int = -1) -> None:
    """PB-51 op same: item `cid` is item `to` (a Tracker ID, an explicit reference or the PM's confirm). The older
    item keeps its id, text, owner and status; the newer one becomes its alias and its mentions join the history. A
    PM edit wins: when the newer item carries the later PM edit, its text and owner go onto the joined item. The newer
    item is parked as it was, with `n` (the index of this same) for fold: a move of either call cancels the same like
    an op split does, and an op split never reaches here (fold skips the same it undoes, _split_pairs)."""
    a, b = root(alias, cid), root(alias, to or "")
    if a == b or a not in items or b not in items:
        return
    src, dst = items.pop(a), items[b]
    parked[a] = dict(src, ids=list(src["ids"]), history=list(src["history"]), _same=n,
                     _dst={k: dst.get(k, "") for k in ("text", "owner", "due", "edited_at")})
    alias[a] = b
    if (src.get("edited_at") or "") > (dst.get("edited_at") or ""):
        dst.update({k: src.get(k, "") for k in ("text", "owner", "edited_at")})
        if src.get("due"):
            dst["due"] = src["due"]
    dst["ids"] = dst["ids"] + [x for x in src["ids"] if x not in dst["ids"]]
    dst["history"] = dst["history"] + src["history"]
    if not dst.get("due") and src.get("due") and src["status"] in LIVE:
        dst["due"] = src["due"]
    if dst["status"] == "proposal" and src["status"] == "open":
        dst.update(status="open", modality="agreed")     # an agreed mention makes the proposal a commitment
    if dst["status"] in LIVE and src["status"] == "done":  # the PM ticked the mention on the later call: the item is done
        dst.update({k: src[k] for k in ("status", "done_at", "done_source", "done_call")})


def _split(items: Dict[str, dict], alias: Dict[str, str], parked: Dict[str, dict], cid: str) -> int:
    """PB-51: take a joined mention out again. The parked item comes back as it was when it was joined; the older item
    gets back its own text, owner and due when they came from it. Returns the index of the same it undid (-1 when
    none), so fold can cancel that same and give the older item back all its own state."""
    a = cid if cid in parked else next((k for k, v in parked.items() if cid in v["ids"]), "")
    if not a or a not in alias:
        return -1
    b = root(alias, a)
    alias.pop(a)
    src = parked.pop(a)
    was = src.pop("_dst", None)
    n = src.pop("_same", -1)
    dst = items.get(b)
    if dst:
        gone = set(src["ids"])
        dst["ids"] = [x for x in dst["ids"] if x not in gone]
        dst["history"] = [h for h in dst["history"] if h.get("id") not in gone]
        if was and src.get("edited_at") and dst.get("edited_at") == src["edited_at"]:   # its words came from src
            dst.update(was)
    items[a] = src
    return n


def _split_pairs(events: List[dict], rolled: set) -> set:
    """PB-51: the indexes of each op split and of the op same it undoes (the latest same of that id before it, as
    split() writes it). fold skips both, so a link and its undo leave the register as if the link never was."""
    stack: Dict[str, List[int]] = {}
    out = set()
    for n, e in enumerate(events):
        if e.get("mig") and str(e["mig"]) in rolled:
            continue
        if e.get("op") == "same":
            stack.setdefault(str(e.get("id")), []).append(n)
        elif e.get("op") == "split" and stack.get(str(e.get("id"))):
            out.update((stack[str(e.get("id"))].pop(), n))
    return out


def fold(events: List[dict]) -> List[dict]:
    """Items in the order they were added, with status open|proposal|done|dropped. A duplicate add (same call,
    similar text, rules/commitments.yaml > duplicates) points at the first item; later events on it apply to the
    first. PB-51 (rules/commitments.yaml > identity): op same joins an item of another call to an older one (its id
    stays an alias, its events keep applying), op split cancels that same as if it never happened (each item keeps
    its own status, modality, due and done, and every event stays with the id it named), and the events of a rolled
    back migration are skipped. A move of a call cancels the same of its mentions joined across calls the same way
    (fold runs again without them). Each item lists its ids and its history (the calls that mentioned it)."""
    rolled = {str(e.get("tag")) for e in events if e.get("op") == "rollback" and e.get("tag")}
    undone = _split_pairs(events, rolled)
    while True:
        items, moved = _fold_once(events, rolled, undone)
        if not moved - undone:
            return items
        undone |= moved


def _fold_once(events: List[dict], rolled: set, undone: set):
    """One pass of fold without the events in `undone`. Returns the items and the indexes of the same events a move
    took apart in this pass."""
    moved = set()
    items: Dict[str, dict] = {}
    alias: Dict[str, str] = {}
    parked: Dict[str, dict] = {}
    for n, e in enumerate(events):
        if n in undone or (e.get("mig") and str(e["mig"]) in rolled):
            continue
        op, cid = e.get("op"), e.get("id")
        if op == "add":
            _add(items, alias, e)
        elif op == "move":      # 0.10.1 (A2): the item went with its call to another project
            if cid in parked:   # PB-51: a mention joined to an item of another call leaves alone
                moved.add(_split(items, alias, parked, cid))
            for k in [k for k, v in alias.items() if v == cid and k in parked]:
                moved.add(_split(items, alias, parked, k))  # the mentions of other calls joined to it stay their own
            items.pop(cid, None)
            for k in [k for k, v in alias.items() if v == cid or k == cid]:
                alias.pop(k, None)
        elif op == "same":
            _join(items, alias, parked, cid, e.get("to", ""), n)
        elif op == "split":
            _split(items, alias, parked, cid)
        elif items.get(root(alias, cid)):
            _apply(items[root(alias, cid)], op, e)
    moved.discard(-1)
    return list(items.values()), moved


def load(folder) -> List[dict]:
    return fold(read_events(folder))


def append(folder, events: List[dict]) -> None:
    if not events:
        return
    p = path_for(folder)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock, p.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(dict({"v": 1}, **e), ensure_ascii=False) + "\n")


def open_items(folder, series: str = "", limit: int = 15) -> List[dict]:
    """Open items of the project, this series first, newest first inside each group."""
    items = [i for i in load(folder) if i["status"] == "open"]
    items.sort(key=lambda i: (0 if series and i.get("series") == series else 1, _neg(i.get("created", ""))))
    return items[:limit]


def _neg(s: str) -> str:
    return "".join(chr(0x10FFFF - ord(c)) for c in s)


class _CallRecord:
    """The add (and done) events of one call's commitments, skipping what the register already holds."""

    def __init__(self, have: List[dict], call: str, project: str, series: str, created: str, by: str, client: str = ""):
        self.have, self.call, self.project, self.series, self.created, self.by = have, call, project, series, created, by
        self.client = client
        self.events: List[dict] = []

    def add(self, text, owner: str, due, t, done: bool, replaces: str = "") -> None:
        text = re.sub(r"\s+", " ", str(text or "")).strip()[:120]
        if not text:
            return
        cid = make_id(self.call, owner, text)
        if any(i["id"] == cid or cid in (i.get("ids") or []) or same_slot(i, self.call, owner, text) for i in self.have):
            return
        name = Path(self.call).name
        e = {"op": "add", "id": cid, "text": text, "owner": owner, "project": self.project, "series": self.series,
             "call": name, "t": t or "", "created": self.created, "due": str(due or "")[:40], "by": self.by}
        if self.client:                 # PB-51: the scope of an explicit reference ("item 13" of this client)
            e["client"] = self.client
        if ID.is_tentative(text):       # PB-51: "we will try" is a proposal, not an agreed commitment
            e["modality"] = "tentative"
        if replaces:                    # 0.8.6 > mine: an "on them" item of an earlier call became the PM's
            e["from"] = replaces
            self.events.append({"op": "drop", "id": replaces, "at": self.created})
        self.events.append(e)
        self.have.append(dict(e, status="open"))
        if done:
            self.events.append({"op": "done", "id": cid, "at": self.created, "source": "manual", "call": name})


def is_ticked(a: dict, ticked: Dict[str, bool]) -> bool:
    """Ticked in the window's On me, by action id or by its normalised text."""
    return bool(ticked.get(str(a.get("id") or "")) or ticked.get(norm_text(a.get("what") or "")))


def promiser(a: dict) -> str:
    """Who made a promise to the PM's side; empty for the PM or nobody."""
    who = str(a.get("who") or "").strip()[:40]
    return who if who and who.lower() not in ("pm", "me") else ""


def record_call(folder, *, call: str, project: str, title: str, actions: List[dict], theirs: List[dict],
                ticked: Optional[Dict[str, bool]] = None, by: str = "app", replaces: Optional[Dict[str, str]] = None,
                client: str = "", people: Optional[Dict[str, str]] = None) -> List[dict]:
    """End of a call (rules/commitments.yaml > write_when): the PM's actions (owner me) and promises to the PM's
    side (owner = name). `ticked` = the window's On me ticks, by action id. PB-51: `client` scopes explicit
    references, `people` = the voice library's aliases (identity.voice_aliases). Returns the events written."""
    ticked = ticked or {}       # by action id or by norm_text(what)
    people = ID.voice_aliases() if people is None else people
    ensure_migrated(folder, people)
    before = read_events(folder)
    rec = _CallRecord(fold(before), call, project, series_key(project, title), now_iso(), by, client)
    for a in actions or []:
        rec.add(a.get("what"), "me", a.get("due"), a.get("t"), is_ticked(a, ticked),
                (replaces or {}).get(str(a.get("from") or "")) or "")
    for a in theirs or []:
        if promiser(a):
            rec.add(a.get("what"), promiser(a), a.get("due"), a.get("t"), False)
    return _write_linked(folder, before, rec.events, people, rec.created, by)


def _write_linked(folder, before: List[dict], new: List[dict], people: Optional[Dict[str, str]], at: str, by: str) -> List[dict]:
    """PB-51: the call's events, then the op same of each new item that is an older item by its Tracker ID or by an
    explicit reference (link_events). Returns everything written."""
    ids = [e["id"] for e in new if e.get("op") == "add"]
    links = link_events(before + new, ids, people, at, by) if ids else []
    append(folder, new + links)
    return new + links


OWNER_RX = re.compile(r"^\*\*([^*]{1,60}?):?\*\*\s*:?\s*")
DUE_RX = re.compile(r"\s*\((?:due|до|термін|deadline)\s*:?\s*([^)]*)\)\s*\.?$", re.I)


def notes_task(text: str) -> dict:
    """0.12.2 (L4): a Meeting notes action item, "**Owner**: what (due: when)" (rules/notes.yaml), as owner, what, due.
    The same reading as the app's CallLists.task."""
    s = str(text or "").replace(chr(0x2014), "-").strip()
    owner, due = "", ""
    m = OWNER_RX.match(s)
    if m:
        owner, s = m.group(1).strip(), s[m.end():]
    d = DUE_RX.search(s)
    if d:
        due, s = d.group(1).strip(), s[:d.start()]
    return {"owner": owner, "what": s.replace("**", "").strip(), "due": due}


NOTHING = re.compile(r"^(немає|нема|нічого|відсутні|none|nothing|no|n/a|na|-|жодних .*|no action items.*|no tasks.*)$", re.I)


def owner_match(a: str, b: str) -> bool:
    """The same person: equal names, or the same first name ("David" and "David Kim")."""
    a, b = (a or "me").strip().lower(), (b or "me").strip().lower()
    return a == b or (a.split()[:1] == b.split()[:1] and a != "me" and b != "me")


def record_notes(folder, *, call: str, notes: str, aliases: Optional[List[str]] = None, by: str = "app",
                 client: str = "", people: Optional[Dict[str, str]] = None) -> List[dict]:
    """0.12.2 (L4, rules/commitments.yaml > write_when): after the call notes are written, their ### Action items
    complete the register. An item with the same owner and a similar text as one this call already has (open, done,
    dropped or edited) is the same commitment: the register keeps its item and only takes the notes' due date when it
    had none. Owner: me when the notes name the PM (aliases) or nobody, else the name. PB-51: the same person also
    through the voice library (`people`), and a new item that is an older one by its Tracker ID or explicit reference
    joins it (link_events). Returns the events written."""
    from .callnotes import parse_meeting
    acts = parse_meeting(notes or "").get("actions") or []
    if not acts:
        return []
    names = {x.strip().lower() for x in (aliases or []) if x.strip()} | {"me", "я", "pm"}
    first = {n.split()[0] for n in names if n.split()}
    people = ID.voice_aliases() if people is None else people
    ensure_migrated(folder, people)
    events = read_events(folder)
    have = [i for i in fold(events) if in_call(i, call)]
    added = {e["id"]: e.get("text", "") for e in events if e.get("op") == "add" and call_key(e.get("call")) == call_key(call)}
    project = next((i.get("project", "") for i in have if i.get("project")), "")
    series = next((i.get("series", "") for i in have if i.get("series")), "")
    if not series:
        title = re.sub(r"^\d{4}-\d\d-\d\d-\d{4}-", "", Path(call).name.split(".")[0]).replace("-", " ")
        series = series_key(project, title)
    client = client or next((i.get("client", "") for i in have if i.get("client")), "")
    rec = _CallRecord(have, call, project, series, now_iso(), by, client)
    out: List[dict] = []
    for a in acts:
        tk = notes_task(a.get("text", ""))
        what = re.sub(r"\s+", " ", tk["what"]).strip()
        if not what or NOTHING.match(what.rstrip(".!")):
            continue
        o = tk["owner"].strip()
        owner = "me" if not o or o.lower() in names or o.lower().split()[0] in first else o[:40]
        twin = next((i for i in rec.have if ID.same_person(i.get("owner") or "me", owner, people)
                     and (similar(i.get("text", ""), what) or any(similar(added.get(x, ""), what) for x in i.get("ids") or [i.get("id")]))), None)
        if twin:
            if tk["due"] and not twin.get("due") and twin.get("status", "open") in LIVE:
                out.append({"op": "edit", "id": twin["id"], "at": rec.created, "due": tk["due"][:40], "source": "notes"})
                twin["due"] = tk["due"][:40]
            continue
        n = len(rec.events)
        rec.add(what, owner, tk["due"], a.get("t"), False)
        for e in rec.events[n:]:
            if e.get("op") == "add":
                e["source"] = "notes"
    return _write_linked(folder, events, rec.events + out, people, rec.created, by)


def migrate_owners(folder, *, call: str, notes: str, aliases: Optional[List[str]] = None, by: str = "app") -> List[dict]:
    """0.26.2 (speakers on the call page, Rewrite notes): the PM renamed, merged or moved speakers and the notes were
    written again. An open item of this call that the PM has not touched gets the new owner when the new notes have a
    similar action under another name, so record_notes then sees it as the same commitment (no "Speaker 2" twin next to
    "Mike"). The PM's own ticks from the call window (owner me, not from the notes) stay as they are. Returns the
    events written."""
    from .callnotes import parse_meeting
    acts = parse_meeting(notes or "").get("actions") or []
    if not acts:
        return []
    names = {x.strip().lower() for x in (aliases or []) if x.strip()} | {"me", "я", "pm"}
    first = {n.split()[0] for n in names if n.split()}
    events = read_events(folder)
    mine = [e for e in events if call_key(e.get("call")) == call_key(call) or e.get("op") != "add"]
    from_notes = {e["id"] for e in mine if e.get("op") == "add" and e.get("source") == "notes"}
    touched = {e.get("id") for e in mine if e.get("op") in ("edit", "drop", "done", "reopen")
               and e.get("source") not in ("notes", "rewrite")}
    added = {e["id"]: e.get("text", "") for e in mine if e.get("op") == "add"}
    new = []
    for a in acts:
        tk = notes_task(a.get("text", ""))
        what = re.sub(r"\s+", " ", tk["what"]).strip()
        o = tk["owner"].strip()
        if what and not NOTHING.match(what.rstrip(".!")):
            new.append((what, "me" if not o or o.lower() in names or o.lower().split()[0] in first else o[:40]))
    out, at = [], now_iso()
    for it in fold(events):
        if call_key(it.get("call")) != call_key(call) or it.get("status") != "open" or it["id"] in touched:
            continue
        old = it.get("owner") or "me"
        if old == "me" and it["id"] not in from_notes:
            continue
        texts = (it.get("text", ""), added.get(it["id"], ""))
        if any(owner_match(old, o) and any(similar(t, w) for t in texts if t) for w, o in new):
            continue          # the new notes still give it to the same person
        hit = next((o for w, o in new if not owner_match(old, o) and any(similar(t, w) for t in texts if t)), None)
        if hit:
            out.append({"op": "edit", "id": it["id"], "at": at, "owner": hit, "source": "rewrite", "by": by})
    append(folder, out)
    return out


def mark(folder, cid: str, op: str = "done", source: str = "manual", call: str = "", notion_page: str = "") -> dict:
    e = {"op": op, "id": cid, "at": now_iso()}
    if op == "link":
        e["notion_page"] = notion_page
    if op == "unlink":
        e["reason"] = source if source in ("removed", "undo") else "removed"
    if op == "done":
        e["source"] = source
        if call:
            e["call"] = Path(call).name
    append(folder, [e])
    return e


# ---------------------------------------------------------------- PB-51 identity across calls (rules/commitments.yaml > identity)

def _rejected(events: List[dict]) -> set:
    """Pairs the PM said are not the same item (op reject, or op split of a link): never linked or suggested again."""
    return {frozenset((e.get("id"), e.get("to"))) for e in events
            if e.get("op") in ("reject", "split") and e.get("id") and e.get("to")}


def _is_rejected(rej: set, a: dict, b: dict) -> bool:
    return any(frozenset((x, y)) in rej for x in a.get("ids") or [a["id"]] for y in b.get("ids") or [b["id"]])


def _texts(item: dict) -> List[str]:
    return [item.get("text", "")] + [h.get("text", "") for h in history_of(item)]


def _ref(item: dict) -> str:
    return next((r for r in (ID.ref_of(t) for t in _texts(item)) if r), "")


def _calls(item: dict) -> set:
    return {call_key(h.get("call")) for h in history_of(item)}


def _overlap(a: dict, b: dict) -> float:
    x, y = words(a.get("text", "")), words(b.get("text", ""))
    return len(x & y) / max(1, min(len(x), len(y)))


def _same_scope(a: dict, b: dict, people: Optional[Dict[str, str]], link: bool = False) -> bool:
    """Two items of different calls that may be one: for a link the same known client and one person id in the
    voice library; for a suggestion also the same meeting series without a client and the same first name."""
    scope = ID.scope_match(a, b)
    return ((scope == "link" if link else bool(scope))
            and ID.same_person(a.get("owner") or "me", b.get("owner") or "me", people, strict=link)
            and not (_calls(a) & _calls(b)))


def link_events(events: List[dict], new_ids: List[str], people: Optional[Dict[str, str]] = None, at: str = "",
                by: str = "app") -> List[dict]:
    """The op same events that join the new items to older ones by the first two keys: the same Tracker ID, or the
    same explicit reference ("item 13") of the same known client for the same person. Without a known client a
    reference is only a suggestion (suggestions()). An older item the PM split from it or rejected is never taken,
    and only an open item or a proposal is a target. Several candidates: the most similar text, then the oldest."""
    items = fold(events)
    by_id = {x: i for i in items for x in i.get("ids") or [i["id"]]}
    rej, out, done = _rejected(events), [], set()
    client_of = {e["id"]: e.get("client") or "" for e in events if e.get("op") == "add" and e.get("id")}

    def tracker_ok(it: dict, o: dict) -> bool:       # every mention joined into o, not only the oldest one's client
        return all(ID.tracker_scope(it, dict(o, client=client_of.get(x, ""))) for x in o.get("ids") or [o["id"]])

    for nid in new_ids:
        it = by_id.get(nid)
        if not it or it["id"] != nid or it["status"] == "dropped" or nid in done:   # a ticked (done) mention still joins
            continue
        tid, ref = ID.tracker_id(it), _ref(it)
        pool = [o for o in items if o["id"] != nid and o["id"] not in new_ids and o["status"] in LIVE
                and o.get("created", "") <= it.get("created", "") and not _is_rejected(rej, it, o)]
        hit = [o for o in pool if tid and ID.tracker_id(o) == tid and tracker_ok(it, o)]
        why = "tracker"
        if not hit and ref:
            hit = [o for o in pool if _ref(o) == ref and _same_scope(it, o, people, link=True)]
            why = "ref"
        if hit:
            best = sorted(hit, key=lambda o: (-_overlap(it, o), o.get("created", "")))[0]
            out.append({"op": "same", "id": nid, "to": best["id"], "by": by, "source": why, "at": at or now_iso()})
            done.add(nid)
    return out


def migration_links(events: List[dict], people: Optional[Dict[str, str]] = None, at: str = "", by: str = "app") -> List[dict]:
    """The links of the migration in one pass over one fold (a register of 1000 items in about a second): oldest item
    first, each joins the first older item with its Tracker ID, else the most similar older item with its reference
    of the same known client and person (link_events' keys). Only open items and proposals take part, so the
    migration never closes or reopens anything; a done or dropped item stays as it was."""
    rej, out = _rejected(events), []
    by_tid: Dict[tuple, List[dict]] = {}
    by_ref: Dict[tuple, List[dict]] = {}
    for it in sorted(fold(events), key=lambda i: i.get("created", "")):
        if it["status"] not in LIVE:
            continue
        tid, ref, client = ID.tracker_id(it), _ref(it), str(it.get("client") or "").strip().lower()
        project = str(it.get("project") or "")
        key = (project, client, ID.person_id(it.get("owner") or "me", people), ref)
        mine = _calls(it)
        hit, why = None, "tracker"
        if tid:
            hit = next((o for o in by_tid.get((project, tid), [])
                        if ID.tracker_scope(it, o) and not _is_rejected(rej, it, o)), None)
        if not hit and ref and client:
            cands = [o for o in by_ref.get(key, []) if not (o["calls"] & mine) and not _is_rejected(rej, it, o)]
            hit = sorted(cands, key=lambda o: (-_overlap(it, o), o.get("created", "")))[0] if cands else None
            why = "ref"
        if hit:
            out.append({"op": "same", "id": it["id"], "to": hit["id"], "by": by, "source": why, "at": at or now_iso()})
            hit["calls"] |= mine
            hit["ids"] = hit["ids"] + it["ids"]
            hit["client"] = hit.get("client") or it.get("client") or ""    # a later join keeps to this client
            if tid and not any(o is hit for o in by_tid.setdefault((project, tid), [])):
                by_tid[(project, tid)].append(hit)
            continue
        node = dict(it, calls=set(mine), ids=list(it["ids"]))
        if tid:
            by_tid.setdefault((project, tid), []).append(node)
        if ref and client:
            by_ref.setdefault(key, []).append(node)
    return out


PLAIN = {"the", "and", "for", "with", "from", "about", "this", "that", "our", "their", "them", "his", "her", "your",
         "send", "share", "give", "get", "make", "will", "can", "need", "back", "over", "out", "into", "him", "all",
         "надіслати", "надішле", "відправити", "передати", "для", "про", "щодо", "нам", "їм"}


def close_text(a: str, b: str, names: set) -> bool:
    """The suggestion test: at least two content words in common (no plain verbs, no people names) and 60% of the
    shorter text's content words. "send the pilot timeline to Mike" is not "send Mike the updated rollout checklist"."""
    x, y = words(a) - PLAIN - names, words(b) - PLAIN - names
    common = x & y
    return len(common) >= 2 and len(common) / max(1, min(len(x), len(y))) >= 0.6


def suggestions(folder_or_events, people: Optional[Dict[str, str]] = None) -> List[dict]:
    """The third key: an open item that looks like an open item of an earlier call (the same person, the same client
    or, without a client, the same meeting series), by the same explicit reference or a similar text. Only a question
    to the PM ("looks like the task from 12.11, is it the same?"); confirm() makes it a link, reject() keeps them
    apart for good. Newest item first."""
    events = folder_or_events if isinstance(folder_or_events, list) else read_events(folder_or_events)
    items, rej, out = fold(events), _rejected(events), []
    names = {w for i in items for w in words(i.get("owner", ""))} | {w for v in (people or {}).values() for w in words(v)}
    for it in sorted(items, key=lambda i: i.get("created", ""), reverse=True):
        if it["status"] not in LIVE:
            continue
        cands = [o for o in items if o["id"] != it["id"] and o["status"] in LIVE and o.get("created", "") < it.get("created", "")
                 and _same_scope(it, o, people) and not _is_rejected(rej, it, o) and _ref(o) == _ref(it)
                 and not (ID.tracker_id(o) and ID.tracker_id(it) and ID.tracker_id(o) != ID.tracker_id(it))
                 and (_ref(it) or any(close_text(a, b, names) for a in _texts(it) for b in _texts(o)))]
        if cands:
            o = sorted(cands, key=lambda c: (-_overlap(it, c), c.get("created", "")))[0]
            day = str(o.get("created", ""))[:10]
            out.append({"id": it["id"], "to": o["id"], "text": it.get("text", ""), "to_text": o.get("text", ""), "since": day,
                        "question": "Looks like the task from {}, is it the same?".format(".".join(reversed(day.split("-")[1:])))})
    return out


def confirm(folder, cid: str, to: str) -> dict:
    """The PM confirmed a suggestion: the two items are one (op same, by pm)."""
    e = {"op": "same", "id": cid, "to": to, "by": "pm", "source": "pm", "at": now_iso()}
    append(folder, [e])
    return e


def reject(folder, cid: str, to: str) -> dict:
    """The PM said the two items are different: never suggested or linked again."""
    e = {"op": "reject", "id": cid, "to": to, "by": "pm", "at": now_iso()}
    append(folder, [e])
    return e


def split(folder, cid: str) -> Optional[dict]:
    """The PM undid a link: the item is its own again, and the pair is never linked again. `cid` may be the newer id
    or the id the joined item kept (the one the PM sees in lists): then the latest link into that item is undone."""
    events = read_events(folder)
    link = next((e for e in reversed(events) if e.get("op") == "same" and e.get("id") == cid), None)
    if not link:
        item = next((i for i in fold(events) if cid in (i.get("ids") or [i["id"]])), None)
        ids = set(item.get("ids") or []) - {item["id"]} if item else set()
        link = next((e for e in reversed(events) if e.get("op") == "same" and e.get("id") in ids), None)
    if not link:
        return None
    e = {"op": "split", "id": link["id"], "to": link.get("to", ""), "by": "pm", "at": now_iso()}
    append(folder, [e])
    return e


MIGRATION = "g1"


def migration_tag(events: List[dict]) -> str:
    """The tag of the G1 migration this register holds and has not rolled back; "" when none."""
    rolled = {e.get("tag") for e in events if e.get("op") == "rollback"}
    return next((e["tag"] for e in reversed(events) if e.get("op") == "migrate" and str(e.get("tag", "")).startswith(MIGRATION)
                 and e.get("tag") not in rolled), "")


@contextlib.contextmanager
def _migration_lock(folder):
    """The app's engine and the skill can both start the migration: one process at a time (a lock file next to the
    register, released when the process ends)."""
    p = path_for(folder)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.parent / ".commitments.lock", "a") as f:
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        except (ImportError, OSError) as err:
            print("commitments: migration lock not taken ({}), running unlocked".format(err), file=sys.stderr)
        yield


def migrate(folder, people: Optional[Dict[str, str]] = None, by: str = "app") -> dict:
    """PB-51: the G1 identity over the register written before it. Append only, every historical line stays: the "we
    will try" items become proposals (op tentative), open items with the same Tracker ID or the same explicit
    reference of one known client join the older one (op same; the newer id stays its alias). Every event carries
    the migration tag, so rollback() undoes all of it; an older app ignores these ops and reads the register as
    before. Once per register; a new tag never repeats a tag the register already has."""
    with _migration_lock(folder):
        return _migrate(folder, people, by)


def _migrate(folder, people: Optional[Dict[str, str]], by: str) -> dict:
    events = read_events(folder)
    if not events or migration_tag(events):
        return {"tag": migration_tag(events), "events": 0}
    at = now_iso()
    used = {str(e.get("tag")) for e in events if e.get("tag")}
    base = tag = "{}-{}".format(MIGRATION, re.sub(r"\D", "", at)[:14])
    n = 1
    while tag in used:
        n += 1
        tag = "{}-{}".format(base, n)
    out = [{"op": "migrate", "id": tag, "tag": tag, "at": at, "by": by}]
    for it in fold(events):
        if it["status"] == "open" and not it.get("modality") and ID.is_tentative(it.get("text", "")):
            out.append({"op": "tentative", "id": it["id"], "at": at, "by": by})
    links = migration_links(events + out, people, at, by)
    out += links
    for e in out:
        e["mig"] = tag
    append(folder, out)
    return {"tag": tag, "events": len(out), "tentative": sum(1 for e in out if e["op"] == "tentative"),
            "links": len(links)}


def rollback(folder, tag: str = "") -> List[dict]:
    """Undo the G1 migration: one op rollback per migration this register holds (two processes may each have
    migrated), or only `tag`. After it the register folds exactly as before the migration (links made later on new
    calls stay). Returns the events written."""
    with _migration_lock(folder):
        events = read_events(folder)
        rolled = {e.get("tag") for e in events if e.get("op") == "rollback"}
        tags = [tag] if tag else [e["tag"] for e in events if e.get("op") == "migrate" and e.get("tag") not in rolled
                                  and str(e.get("tag", "")).startswith(MIGRATION)]
        out = [{"op": "rollback", "id": t, "tag": t, "at": now_iso()} for t in dict.fromkeys(tags) if t]
        append(folder, out)
    return out


def ensure_migrated(folder, people: Optional[Dict[str, str]] = None) -> None:
    """Run the migration once, before the first write of the G1 code (rolled back registers stay rolled back)."""
    events = read_events(folder)
    if events and not any(e.get("op") in ("migrate", "rollback") for e in events):
        with _migration_lock(folder):
            events = read_events(folder)
            if not any(e.get("op") in ("migrate", "rollback") for e in events):
                _migrate(folder, people, "app")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "done", "reopen", "drop", "link", "unlink", "suggest", "same", "reject", "split",
                                    "agree", "migrate", "rollback"])     # agree: the PM makes a "we will try" open
    ap.add_argument("--to", default="", help="same/reject: the older item")
    ap.add_argument("--page", default="")
    ap.add_argument("folder", type=Path)
    ap.add_argument("id", nargs="?")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for i in load(a.folder):
            if a.all or i["status"] == "open":
                print(json.dumps(i, ensure_ascii=False))
        return 0
    if a.cmd in ("suggest", "migrate", "rollback"):
        res = {"suggest": lambda: suggestions(a.folder, ID.voice_aliases()), "migrate": lambda: migrate(a.folder, ID.voice_aliases()),
               "rollback": lambda: rollback(a.folder, a.id or "")}[a.cmd]()
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if not a.id:
        ap.error("id is required")
    if a.cmd in ("same", "reject"):
        if not a.to:
            ap.error("--to is required")
        print(json.dumps((confirm if a.cmd == "same" else reject)(a.folder, a.id, a.to), ensure_ascii=False))
        return 0
    if a.cmd == "split":
        print(json.dumps(split(a.folder, a.id), ensure_ascii=False))
        return 0
    print(json.dumps(mark(a.folder, a.id, a.cmd, notion_page=a.page), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
