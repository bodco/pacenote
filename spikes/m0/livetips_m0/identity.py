"""PB-51 GraphRAG G1: one task, decision, risk or person across calls (rules/commitments.yaml > identity).

Pure helpers, no network. The commitments register uses them; decisions and risks can key on the same functions.

Order of the stable key of an item (the first that applies wins; a PM's own link, split or reject always wins):
  1. tracker  the same Notion page or HubSpot task id (with its portal) in the same project, never for another
              known client
  2. ref      an explicit reference in the text ("item 13", "пункт 13", "task #13") in the same project AND the same
              known client, for the same person (one id in the voice library, not just the same first name)
  3. suggest  the same reference or a similar text of the same person in an earlier call of the same client, or of
              the same meeting series when the client is unknown (two clients can share a meeting title): only a
              suggestion ("looks like the task from 12.11, is it the same?") that becomes a link when the PM confirms
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

REF_RX = re.compile(r"(?:\b(?:item|task|ticket|point|issue)|(?<!\w)(?:пункт|задач[аіуи]|тікет|питання))\s*(?:№|#|no\.?)?\s*(\d{1,5})\b",
                    re.I | re.U)    # a bare "#1" is not a reference ("our #1 priority"); "task #13" is
TENTATIVE_RX = re.compile(r"^\W*(?:"
                          r"(?:(?:we|i)\s+(?:will|shall)\s+|(?:we|i)['’]ll\s+|will\s+)(?:try|attempt)(?:\s+to)?"
                          r"|(?:(?:we|i)\s+)?(?:try|attempt)\s+to"
                          r"|(?:(?:ми|я)\s+)?(?:спробу(?:ю|єш|є|ємо|єте|ють|вати|ймо)"
                          r"|постара(?:юсь|юся|ємось|ємося|ється|ються|тися|тись)))(?!\w)",
                          re.I | re.U)
# the promise opens the item, optionally after a first-person subject ("try to confirm", "we'll try", "спробуємо"):
# someone else trying inside a firm task ("fix the crash when users try to export") is not; verb forms only
# ("спробую", not the noun "спробу")
HEDGE_RX = re.compile(r"^\W*(?:maybe|if possible|можливо|мабуть|якщо вийде|по можливості)(?![\w-])", re.I | re.U)
# a weak hedge counts only when it opens the item ("maybe send the report"): after the task ("send the SOW by Friday,
# maybe earlier", "send the deck if possible") it softens the date, the task stays agreed
QUOTED_RX = re.compile(r"(?<!\w)(?:'[^'\n]*'|\"[^\"\n]*\"|‘[^’\n]*’|“[^”\n]*”|«[^»\n]*»|„[^“”\n]*[“”])(?!\w)", re.U)
# quoted text is a name or a label ("fix the 'try to login' banner"), not the speaker's promise; an apostrophe inside a
# word ("we'll", "Dana's", "п'ятниця") opens no quote
ME = "me"


def ref_of(text: str) -> str:
    """The number of an explicit reference in the text ("item 13", "пункт 13", "task #13"); "" when none or when the
    text names two different numbers (then it is not one item)."""
    found = set(REF_RX.findall(text or ""))
    return found.pop() if len(found) == 1 else ""


def is_tentative(text: str) -> bool:
    """A "we will try" promise ("try to confirm the list", "спробуємо надіслати", "maybe send the report"): a proposal,
    not an agreed task. A trailing "maybe earlier" or "if possible" and quoted text do not count."""
    text = QUOTED_RX.sub(" ", text or "")
    return bool(TENTATIVE_RX.search(text) or HEDGE_RX.search(text))


def tracker_id(item: dict) -> str:
    """The item's id in the PM's tracker: the Notion page, else the HubSpot task of its portal; "" when it is in none
    or the task has no portal (task ids repeat across portals, as CRMTaskFlow reads it)."""
    if item.get("notion_page"):
        return "notion:" + str(item["notion_page"]).replace("-", "").lower()
    if item.get("crm_task") and item.get("crm_portal"):
        return "crm:{}:{}".format(item["crm_portal"], item["crm_task"])
    return ""


def tracker_scope(a: dict, b: dict) -> bool:
    """Two items with one Tracker ID may join: the same project and not two different known clients (the PM put a
    page of one client on another's task by mistake; never one item across clients or projects). An unknown client
    on either side still joins: the tracker link is the PM's own."""
    if str(a.get("project") or "") != str(b.get("project") or ""):
        return False
    ca, cb = (str(x.get("client") or "").strip().lower() for x in (a, b))
    return not (ca and cb and ca != cb)


def scope_of(item: dict) -> str:
    """Project and client of an item. Without a client the meeting series stands for it (only for suggestions)."""
    project = str(item.get("project") or "")
    client = str(item.get("client") or "").strip().lower()
    if client:
        return "{}|client:{}".format(project, client)
    series = str(item.get("series") or "")
    return "{}|series:{}".format(project, series.split("|", 1)[-1])


def scope_match(a: dict, b: dict) -> str:
    """"link" when both items name the same client in the same project (a reference may join them), "suggest" when
    a client is unknown and the meeting series is the same (two clients can share a title such as "Weekly sync", so
    the PM decides), else "" (another project or another client: never the same item)."""
    if str(a.get("project") or "") != str(b.get("project") or ""):
        return ""
    ca, cb = (str(x.get("client") or "").strip().lower() for x in (a, b))
    if ca and cb:
        return "link" if ca == cb else ""
    sa, sb = (str(x.get("series") or "").split("|", 1)[-1] for x in (a, b))
    return "suggest" if sa and sa == sb else ""


# ---------------------------------------------------------------- decisions and risks (PB-52 G2, S1)

def _sha10(raw: str) -> str:
    import hashlib
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def call_stem(call: str) -> str:
    """The call file's stem: the base name without folders, the call file suffixes and the app's .app part
    (call_layout.migrate keeps names, so the stem survives a move into a call folder)."""
    n = os.path.basename(str(call or ""))
    for suf in (".notion.json", ".notes.md", ".brief.md", ".sheet.md", ".sheet.json", ".md"):
        if n.endswith(suf):
            n = n[: -len(suf)]
            break
    return n[:-4] if n.endswith(".app") else n


def item_id(kind: str, call: str, text: str) -> str:
    """d- (decision) or r- (risk) id of an item of a call: sha1(call stem | notion_links.norm(text)), the dedupe key
    of rules/decisions.yaml that notion_links.put writes into <stem>.notion.json. It never changes once minted: it
    does not depend on whether the item was sent to Notion."""
    from .notion_links import norm
    return "{}-{}".format("r" if kind.startswith("r") else "d", _sha10("{}|{}".format(call_stem(call), norm(text))))


def register_decision_id(date: str, title: str) -> str:
    """d- id of a pacenote/decisions.md line (date, title; no page id and no call file). Joining it to a call's d- id
    is a same_as suggestion only."""
    from .notion_links import norm
    return "d-" + _sha10("decisions.md|{}|{}".format((date or "").strip(), norm(title)))


def notion_alias(page_id: str) -> str:
    """notion:<page id> without dashes, lower case (as tracker_id writes it)."""
    return "notion:" + str(page_id or "").replace("-", "").strip().lower()


# ---------------------------------------------------------------- people

def voices_path() -> str:
    """The voice library the app and the recorder keep: $LT_VOICES when set (empty = none), else <app root>/voices/
    voices.json next to the app's state folder ($LT_AI_STATE), else the default app root in Application Support."""
    if "LT_VOICES" in os.environ:
        return os.environ["LT_VOICES"]
    state = os.environ.get("LT_AI_STATE")
    root = Path(state).expanduser().parent.parent if state else Path("~/Library/Application Support/LiveTips").expanduser()
    return str(root / "voices" / "voices.json")

def voice_aliases(path=None) -> Dict[str, str]:
    """The alias map of the voice library ({"aliases": {"<spelling, lower case>": "<name kept>"}}, rules/voices.yaml):
    the library's Merge into and the Settings aliases both write it. `path` or voices_path(); {} when missing."""
    p = path or voices_path()
    if not p:
        return {}
    try:
        data = json.loads(Path(p).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    al = data.get("aliases") if isinstance(data, dict) else None
    return {str(k).strip().casefold(): str(v) for k, v in al.items()} if isinstance(al, dict) else {}


def person_id(name: str, aliases: Optional[Dict[str, str]] = None, me: Optional[List[str]] = None) -> str:
    """One id per person: "me" for the PM (the PM's name aliases), else the name the voice library keeps for this
    spelling (aliases followed at most 5 steps, as VoiceBook.resolve), case-folded like VoiceLibrary.canonical."""
    n = (name or "").strip()
    mine = {x.strip().casefold() for x in (me or []) if x and x.strip()} | {ME, "я", "pm"}
    if not n or n.casefold() in mine:
        return ME
    al = aliases or {}                  # keys case-folded (voice_aliases)
    for _ in range(5):
        nx = al.get(n.casefold())
        if not nx or nx.strip().casefold() == n.casefold():
            break
        n = nx.strip()
    return ME if n.casefold() in mine else n.casefold()


def same_person(a: str, b: str, aliases: Optional[Dict[str, str]] = None, strict: bool = False) -> bool:
    """The same person: equal ids after the voice library. Not strict (suggestions, the notes' twin of a task): also
    the same first name ("David" and "David Kim"); strict (an automatic link): "Mike Turner" is not "Mike Brown"."""
    x, y = person_id(a, aliases), person_id(b, aliases)
    return x == y or (not strict and x != ME and y != ME and x.split()[:1] == y.split()[:1])
