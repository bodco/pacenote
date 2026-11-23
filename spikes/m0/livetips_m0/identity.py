"""PB-51 GraphRAG G1: one task, decision, risk or person across calls (rules/commitments.yaml > identity).

Pure helpers, no network. The commitments register uses them; decisions and risks can key on the same functions.

Order of the stable key of an item (the first that applies wins; a PM's own link, split or reject always wins):
  1. tracker  the same Notion page or HubSpot task id
  2. ref      an explicit reference in the text ("item 13", "пункт 13", "#13") in the same project AND client, for
              the same person; without a known client the meeting series is the scope (narrower, never two clients)
  3. suggest  a similar text of the same person in the same scope from an earlier call: only a suggestion
              ("looks like the task from 12.11, is it the same?") that becomes a link when the PM confirms it
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

REF_RX = re.compile(r"(?:\b(?:item|task|ticket|point|issue)|(?<!\w)(?:пункт|задач[аіуи]|тікет|питання))\s*(?:№|#|no\.?)?\s*(\d{1,5})\b"
                    r"|(?<![\w#])#(\d{1,5})\b", re.I | re.U)
TENTATIVE_RX = re.compile(r"(?<!\w)(?:try to|will try|we'll try|attempt to|maybe|if possible|спроб\w*|постара\w*|"
                          r"можливо|якщо вийде|по можливості)(?!\w)", re.I | re.U)
ME = "me"


def ref_of(text: str) -> str:
    """The number of an explicit reference in the text ("item 13", "пункт 13", "task #13", "#13"); "" when none or
    when the text names two different numbers (then it is not one item)."""
    found = {a or b for a, b in REF_RX.findall(text or "")}
    return found.pop() if len(found) == 1 else ""


def is_tentative(text: str) -> bool:
    """A "we will try" promise ("try to confirm the list", "спробуємо надіслати"): a proposal, not an agreed task."""
    return bool(TENTATIVE_RX.search(text or ""))


def tracker_id(item: dict) -> str:
    """The item's id in the PM's tracker: the Notion page, else the HubSpot task; "" when it is in none."""
    if item.get("notion_page"):
        return "notion:" + str(item["notion_page"]).replace("-", "").lower()
    if item.get("crm_task"):
        return "crm:{}:{}".format(item.get("crm_portal") or "", item["crm_task"])
    return ""


def scope_of(item: dict) -> str:
    """Project and client of an item. Without a client the meeting series stands for it (a series is one client)."""
    project = str(item.get("project") or "")
    client = str(item.get("client") or "").strip().lower()
    if client:
        return "{}|client:{}".format(project, client)
    series = str(item.get("series") or "")
    return "{}|series:{}".format(project, series.split("|", 1)[-1])


# ---------------------------------------------------------------- people

def voice_aliases(path=None) -> Dict[str, str]:
    """The alias map of the voice library ({"aliases": {"<spelling, lower case>": "<name kept>"}}, rules/voices.yaml):
    the library's Merge into and the Settings aliases both write it. `path` or $LT_VOICES; {} when missing."""
    p = path or os.environ.get("LT_VOICES") or ""
    if not p:
        return {}
    try:
        data = json.loads(Path(p).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    al = data.get("aliases") if isinstance(data, dict) else None
    return {str(k).strip().lower(): str(v) for k, v in al.items()} if isinstance(al, dict) else {}


def person_id(name: str, aliases: Optional[Dict[str, str]] = None, me: Optional[List[str]] = None) -> str:
    """One id per person: "me" for the PM (his name aliases), else the name the voice library keeps for this spelling
    (aliases followed at most 5 steps, as VoiceBook.resolve), lower case."""
    n = (name or "").strip()
    mine = {x.strip().lower() for x in (me or []) if x and x.strip()} | {ME, "я", "pm"}
    if not n or n.lower() in mine:
        return ME
    al = aliases or {}
    for _ in range(5):
        nx = al.get(n.lower())
        if not nx or nx.strip().lower() == n.lower():
            break
        n = nx.strip()
    return ME if n.lower() in mine else n.lower()


def same_person(a: str, b: str, aliases: Optional[Dict[str, str]] = None) -> bool:
    """The same person: equal ids after the voice library, or the same first name ("David" and "David Kim")."""
    x, y = person_id(a, aliases), person_id(b, aliases)
    return x == y or (x != ME and y != ME and x.split()[:1] == y.split()[:1])
