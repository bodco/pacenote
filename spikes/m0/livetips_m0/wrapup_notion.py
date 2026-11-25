"""After the call: the Meetings DB page in Notion (same result as the skill's Step 4-5).

  python -m livetips_m0.wrapup_notion --md <transcript.md> --project-config <projects/<slug>.md>
      --lt <python> <lt> [--notes pending|failed] [--page <id> [--fill]] [--dry-run]

Env: NOTION_TOKEN (internal integration token with access to the Meetings DB).
Steps: `lt wrapup-data` (numbers, clean transcript) -> the call's ready Meeting notes (<stem>.notes.md) and its lists
(call_lists, the call page's rule) -> find this meeting's page in the Meetings DB (Date within 20 min of the start; a
Notion AI page made by the calendar counts) -> append a "Pacenote" section there, or create a new page -> the
transcript as a sub-page. PB-127 (owner 25.11): no model request here; notes not ready yet give a "being written"
line that `--page <id> --fill` replaces once they are. Prints one JSON line: {"ok", "url", "created", "error"}.
Nothing is sent anywhere else. Audio never leaves the Mac; only the transcript text and the notes go to Notion.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import call_language, call_layout

NOTION = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"


# ---------------------------------------------------------------- helpers
def http(method: str, url: str, headers: dict, body=None, timeout=60, raw: bytes | None = None, ctype=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = dict(headers)
    if data is not None:
        h["Content-Type"] = ctype or "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t = r.read()
                return json.loads(t) if t else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:400]
            if not retry_later(e.code, attempt):
                raise RuntimeError(f"{method} {url.split('?')[0]} -> {e.code}: {msg}") from None


def retry_later(code: int, attempt: int) -> bool:
    """Rate limits and overloads wait 2, 5, 8 s; three tries more at most."""
    if code in (429, 502, 503, 529) and attempt < 3:
        time.sleep(2 + attempt * 3)
        return True
    return False

def notion(method, path, body=None):
    tok = os.environ.get("NOTION_TOKEN", "")
    return http(method, NOTION + path, {"Authorization": f"Bearer {tok}", "Notion-Version": NOTION_VERSION}, body)


def config_ids(cfg_path: Path) -> dict:
    """meetings_db, project_page_id, workspace_page_id from the project config (projects/<slug>.md)."""
    t = cfg_path.read_text(encoding="utf-8")
    out = {}
    for key in ("meetings_db", "project_page_id", "workspace_page_id", "risks_db", "decisions_db"):
        m = re.search(r"\*\*" + key + r"\*\*\s*:\s*`?([^`\s]+)", t) or re.search(key + r"\s*:\s*`?([^`\s]+)", t)
        if m:
            out[key] = m.group(1).replace("collection://", "").strip()
    return out


def _head_value(head, prefixes, default=""):
    return next((l.split(":", 1)[1].strip() for l in head if l.startswith(prefixes)), default)


def header_info(md: Path) -> dict:
    """Title, start and timezone from the transcript header and the recorder's state (the language: call_language)."""
    head = md.read_text(encoding="utf-8").splitlines()[:12]
    title = next((l[len("# Live transcript:"):].strip() for l in head if l.startswith("# Live transcript:")), md.stem)
    start = _head_value(head, ("- Початок:", "- Start:"))
    try:
        t0 = dt.datetime.strptime(start, "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        t0 = dt.datetime.fromtimestamp(md.stat().st_ctime).astimezone()
    tz = os.environ.get("LT_TIMEZONE") or "Europe/Kiev"
    return {"title": title, "start": t0, "tz": "Europe/Kiev" if tz in ("Europe/Kyiv", "Europe/Uzhgorod") else tz}


TXT = {
    "uk": {"actions": "Action items", "decisions": "Рішення", "agenda": "Агенда", "commit": "Зобов'язання з нашого боку",
           "new": "Нові вимоги (кандидати на зміну обсягу)", "open": "Відкриті питання", "risks": "Ризики",
           "tips": "Підказки: показано {shown}, карток {cards}, фактів {facts}, запізнілих {late}; затримка медіана {med} с, макс {mx} с",
           "prep": "Підготовка: ", "prep_yes": "використана", "prep_no": "не було",
           "tr": "Транскрипт: {n} рядків, мови {langs}, локальний файл {f}", "summary": "Підсумок", "transcript": "Транскрипт",
           "append": "Pacenote: підсумок ({hhmm})", "tr_app": "Транскрипт (Pacenote)", "write": "Ukrainian", "sales": "Sales",
           "next": "Наступний крок", "objection": "Заперечення", "topics": "Теми", "unclear": "Під питанням", "recap": "Що було",
           "stage": "Стадія", "stakeholder": "Стейкхолдер", "map": "План дій", "competitor": "Конкурент",
           "price": "Ціна", "over_limit": "Понад межу", "us": "ми", "them": "вони",
           "footer": "Нотатки Pacenote · транскрибовано на пристрої",
           "pending": "Нотатки зустрічі пишуться. Коли будуть готові, вони з'являться тут.",
           "failed": "Нотатки зустрічі не записано. Відкрий дзвінок у Pacenote і натисни «Зробити нотатки зустрічі»: "
                     "ця сторінка оновиться."},
    "en": {"actions": "Action items", "decisions": "Decisions", "agenda": "Agenda", "commit": "Our commitments",
           "new": "New requests (possible scope changes)", "open": "Open questions", "risks": "Risks",
           "tips": "Tips: shown {shown}, cards {cards}, facts {facts}, late {late}; delay median {med} s, max {mx} s",
           "prep": "Preparation: ", "prep_yes": "used", "prep_no": "none",
           "tr": "Transcript: {n} lines, languages {langs}, local file {f}", "summary": "Summary", "transcript": "Transcript",
           "append": "Pacenote: summary ({hhmm})", "tr_app": "Transcript (Pacenote)", "write": "English", "sales": "Sales",
           "next": "Next step", "objection": "Objection", "topics": "Topics", "unclear": "Unclear", "recap": "What happened",
           "stage": "Stage", "stakeholder": "Stakeholder", "map": "Mutual plan", "competitor": "Competitor",
           "price": "Price", "over_limit": "Over the limit", "us": "us", "them": "them",
           "footer": "Notes by Pacenote · transcribed on device",
           "pending": "The meeting notes are being written. They appear here when they are ready.",
           "failed": "The meeting notes were not written. Open the call in Pacenote and choose Make meeting notes: "
                     "this page is updated then."},
}


# ---------------------------------------------------------------- the PM's corrections
def _correction(d: dict) -> str:
    act, before, after = d.get("action", ""), d.get("before") or {}, d.get("after") or {}
    if act == "onme_add":
        return f"- On the PM (added by the PM): {after.get('text', '')}"
    if act == "onme_remove":
        return f"- NOT on the PM (removed by the PM): {before.get('text', '')}"
    if act == "onme_edit":
        return f"- Corrected by the PM: \"{before.get('text', '')}\" is really \"{after.get('text', '')}\""
    if act == "remove":
        return f"- Wrong or irrelevant (removed by the PM): {before.get('gist', '')}"
    if act == "edit":
        return edited_card(after)
    return ""


def edited_card(after: dict) -> str:
    pts = "; ".join(after.get("items") or [])
    return (f"- Corrected by the PM: {after.get('gist', '')}" + (f" ({after.get('phrase', '')})" if after.get("phrase") else "")
            + (f": {pts}" if pts else ""))


def pm_corrections(path: Path) -> str:
    """2026-10-28 (owner): cards and "On me" lines the PM corrected, removed or added in the tips window during the
    call (<stem>.edits.jsonl). They are true and win over the transcript and the call picture."""
    if not path.exists():
        return ""
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            line = _correction(json.loads(raw))
        except Exception:  # noqa: BLE001
            continue
        if line:
            out.append(line)
    return ("\n\nCORRECTIONS BY THE PM DURING THE CALL (true; they win over the transcript):\n" + "\n".join(out[-60:])) if out else ""


def parse_object(text: str) -> dict:
    """The first JSON object in a model reply; a straight quote inside a value is escaped when that is the only fault."""
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object in the reply")
    raw = text[i:j + 1]
    try:
        return json.loads(raw)
    except ValueError:
        pass
    # A '"' that neither opens nor closes a string (the next non-space is not one of , : } ]) is part of the text.
    out, in_str, k = [], False, 0
    while k < len(raw):
        ch = raw[k]
        if ch == "\\" and in_str:
            out.append(raw[k:k + 2]); k += 2; continue
        if ch == '"':
            if not in_str:
                in_str = True
            else:
                nxt = raw[k + 1:].lstrip()[:1]
                if nxt and nxt not in ",:}]":
                    out.append('\\"'); k += 1; continue
                in_str = False
        out.append(ch); k += 1
    return json.loads("".join(out))


# ---------------------------------------------------------------- blocks
def rt(s: str) -> list:
    s = s.replace(chr(0x2014), "-")   # never the long dash in Notion
    return [{"type": "text", "text": {"content": s[i:i + 1900]}} for i in range(0, len(s), 1900)] or [{"type": "text", "text": {"content": ""}}]


def rt_md(s: str) -> list:
    """0.9.5.2: inline **bold** of the meeting notes as Notion bold text, not as stars."""
    out = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", s)):
        if part:
            for r in rt(part):
                if i % 2:
                    r["annotations"] = {"bold": True}
                out.append(r)
    return out or rt("")


def h(level: int, s: str) -> dict:
    k = f"heading_{level}"
    return {"type": k, k: {"rich_text": rt(s)}}


def para(s: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": rt(s)}}


def footer_blocks(lang: str) -> list:
    """Pacenote 0.8: the last blocks of a meeting page, in the page's language: a divider and one quiet line."""
    return [{"type": "divider", "divider": {}},
            {"type": "paragraph", "paragraph": {"rich_text": rt(TXT[lang]["footer"]), "color": "gray"}}]


def bullet(s: str) -> dict:
    return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(s)}}


def todo(s: str) -> dict:
    return {"type": "to_do", "to_do": {"rich_text": rt(s), "checked": False}}


def timed(it: dict) -> list:
    """"10:06 " in gray, then the text with its bold."""
    t = it.get("t", "")
    lead = [{"type": "text", "text": {"content": t[:5] + "  "}, "annotations": {"color": "gray"}}] if t else []
    return lead + rt_md(it.get("text", ""))


def owned_line(t: str, owner: str, text: str) -> list:
    """PB-127: the gray time, the owner in bold, then the line exactly as the call page has it."""
    lead = [{"type": "text", "text": {"content": t[:5] + "  "}, "annotations": {"color": "gray"}}] if t else []
    who = [{"type": "text", "text": {"content": owner}, "annotations": {"bold": True}}, {"type": "text", "text": {"content": ": "}}] if owner else []
    return lead + who + rt(text)


def page_lists(m: dict, md: Path) -> dict:
    """PB-127 (one source): Decisions, Action items and Risks by the call page's own rule (call_lists: the PM's edit, else
    these Meeting notes, else the live picture), so the page and the call page say the same words."""
    from . import call_lists
    return call_lists.resolve(call_lists.situations(md), None, mn=m)


def meeting_blocks(m: dict, md: Path, stats: dict, prep_used: bool, lang: str = "uk", actions=None) -> list:
    """0.9.5.2 (owner): the meeting page carries the call's Meeting notes (rules/notes.yaml, schema 3): TL;DR, decisions,
    action items as to-dos, open questions, risks (0.12.2), the topics with their outcome and points; then the agenda, Sales, Pacenote.
    PB-127: decisions, action items and risks are the call page's lists (page_lists), word for word; `actions` the call
    page's own task lines from its register (the app passes them, as CallPageSync writes them), when it has any."""
    T = TXT[lang]
    b = [para(m["tldr"])] if m.get("tldr") else []
    if m.get("recap"):   # 0.26.1: "what happened on the call?" right under the TL;DR
        b.append(h(3, T["recap"]))
        b += [{"type": "numbered_list_item", "numbered_list_item": {"rich_text": rt_md(x)}} for x in m["recap"]]
        if m.get("recap_note"):
            b.append({"type": "paragraph", "paragraph": {"rich_text": rt_md(m["recap_note"])}})
    L = page_lists(m, md)
    from .call_lists import task_line
    rows = {"decisions": [owned_line(d.get("t", ""), d.get("by", ""), d["text"]) for d in L["decision_items"]],
            "actions": [rt(str(x)) for x in actions if str(x).strip()] if actions else
                       [owned_line(x.get("t", ""), x["owner"], task_line(dict(x, owner=""))) for x in L["tasks"]],
            "open": [timed(it) for it in m.get("open") or []],
            "risks": [timed(it) for it in L["risk_items"]]}
    for title, key, kind in ((T["decisions"], "decisions", "bulleted_list_item"), (T["actions"], "actions", "to_do"),
                             (T["open"], "open", "bulleted_list_item"), (T["risks"], "risks", "bulleted_list_item")):
        if rows[key]:
            b.append(h(3, title))
            for r in rows[key]:
                body = {"rich_text": r}
                if kind == "to_do":
                    body["checked"] = False
                b.append({"type": kind, kind: body})
    if m.get("topics"):
        b.append(h(2, T["topics"]))
        for tp in m["topics"]:
            b.append(h(3, tp["title"] + (f"  {tp['t'][:5]}" if tp.get("t") else "")))
            if tp.get("outcome"):
                b.append({"type": "paragraph", "paragraph": {"rich_text": [dict(r, annotations={"italic": True, **r.get("annotations", {})})
                                                                         for r in rt_md(tp["outcome"])]}})
            b += [{"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": timed(it)}} for it in tp.get("points", [])]
    if m.get("unclear"):
        b.append(h(3, T["unclear"]))
        b += [bullet(it["text"]) for it in m["unclear"]]
    return b + extra_blocks(md, stats, prep_used, T, lang)


def extra_blocks(md: Path, stats: dict, prep_used: bool, T: dict, lang: str) -> list:
    b = []
    for title, items in ((T["agenda"], agenda_lines(md, lang)), (T["sales"], sales_lines(md, T))):
        if items:
            b.append(h(3, title))
            b += [bullet(str(i)) for i in items]
    return b + pacenote_blocks(stats, md, prep_used, T)


def call_meeting(md: Path) -> dict:
    """The parsed ## Meeting notes of <stem>.notes.md, {} when the notes or the section are not there."""
    from .callnotes import meeting_text, parse_meeting
    f = call_layout.sidecar_of(str(md), ".notes.md") if str(md).endswith(".md") else call_layout.sidecar(str(md), ".notes.md")
    try:
        t = f.read_text(encoding="utf-8")
    except OSError:
        return {}
    return parse_meeting(t) if meeting_text(t) else {}


def sales_lines(md: Path, T: dict) -> list:
    """0.8.7 (rules/sales.yaml > after_call): the Sales section from <stem>.sales.json, when the call was a sales call."""
    p = call_layout.sidecar_of(str(md), ".sales.json") if str(md).endswith(".md") else call_layout.sidecar(str(md), ".sales.json")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = ["{}: {}{}".format(c.get("criterion", ""), c.get("state", ""), (" · " + c["what"]) if c.get("what") else "")
           for c in d.get("criteria") or []]
    out += ["{}: {}{}".format(T["objection"], o.get("what", ""), (" -> " + o["answer"]) if o.get("answer") else "")
            for o in d.get("objections") or []]
    ns = d.get("next_step") or {}
    if ns.get("what"):
        out.append("{}: {}{}".format(T["next"], ns["what"], (" (" + ns["due"] + ")") if ns.get("due") else ""))
    return out + block_lines(d, T)


def block_lines(d: dict, T: dict) -> list:
    """S1 (rules/sales.yaml > after_call): stakeholders, the mutual action plan, competitors, prices and the stage."""
    g = lambda k, en: T.get(k, en)  # noqa: E731
    who = lambda w: g("us", "us") if w == "us" else g("them", "them") if w == "them" else w  # noqa: E731
    out = ["{}: {} ({}{}){}".format(g("stakeholder", "Stakeholder"), p.get("name", ""), p.get("role", "unknown"),
                                    ", " + p["stance"] if p.get("stance") not in (None, "", "unknown") else "",
                                    (": " + p["quote"]) if p.get("quote") else "")
           for p in d.get("stakeholders") or [] if p.get("name")]
    out += ["{}: {}{}{}".format(g("map", "Mutual plan"), m.get("step", ""), (" · " + who(m["owner"])) if m.get("owner") else "",
                                (" (" + m["due"] + ")") if m.get("due") else "")
            for m in d.get("mutual_plan") or [] if m.get("step")]
    out += ["{}: {}{}".format(g("competitor", "Competitor"), c.get("name", ""), (" · " + c["what"]) if c.get("what") else "")
            for c in d.get("competitors") or [] if c.get("name")]
    out += ["{}: {} · {}".format(g("over_limit", "Over the limit"), b.get("what", ""), b.get("limit", ""))
            for b in d.get("limit_breach") or [] if b.get("what")]
    out += ["{} ({}): {}".format(g("price", "Price"), who(p.get("who", "")), p.get("what", ""))
            for p in d.get("price_talk") or [] if p.get("what")]
    if d.get("stage_crm"):
        out.append("{}: {}".format(g("stage", "Stage"), d["stage_crm"]))
    return out


def agenda_lines(md: Path, lang: str) -> list:
    """Pacenote 0.8 (rules/agenda.yaml > wrapup): each agenda item with Discussed, Raised or Not raised."""
    from .agenda import last_from_tips, status_label
    stem = str(md)[:-3] if str(md).endswith(".md") else str(md)
    strip = next((x for x in (last_from_tips(Path(p)) for p in (call_layout.sidecar(str(md), ".tips.jsonl"), call_layout.sidecar(stem, ".tips.jsonl"))) if x), None)
    return ["{}: {}".format(str(it.get("text", "")).strip(), status_label(it, lang == "uk"))
            for it in strip or [] if str(it.get("text", "")).strip()]


def pacenote_blocks(stats: dict, md: Path, prep_used: bool, T: dict) -> list:
    tips = stats.get("tips", {})
    tr = stats.get("transcript", {})
    return [h(3, "Pacenote"),
            bullet(T["tips"].format(shown=tips.get('shown', 0), cards=tips.get('cards_fired', 0), facts=tips.get('facts_shown', 0),
                                    late=tips.get('late', 0), med=tips.get('delay_median_s', '?'), mx=tips.get('delay_max_s', '?'))),
            bullet(T["prep"] + (T["prep_yes"] if prep_used else T["prep_no"])),
            bullet(T["tr"].format(n=tr.get('lines_real', '?'), langs=", ".join((tr.get('langs') or {}).keys()), f=md.name))]


# ---------------------------------------------------------------- Notion
LINE_RX = re.compile(r"^\[(\d\d:\d\d:\d\d)\] (.+?) \(([\w+-]+)\): (.*)$")


def transcript_blocks(text: str) -> list:
    """The transcript as Notion blocks (2026-10-27, owner: a page, not a file): one paragraph per line with
    the time and the speaker in bold, headings and markers kept."""
    out = []
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        m = LINE_RX.match(ln)
        if m:
            who = m.group(1) + " " + m.group(2) + ": "
            out.append({"type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": who}, "annotations": {"bold": True}}] + rt(m.group(4))}})
        elif ln.startswith("# "):
            out.append(h(2, ln[2:]))
        elif ln.startswith("## "):
            out.append(h(3, ln[3:]))
        elif ln.startswith("---") and ln.endswith("---"):
            out.append({"type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": ln.strip("- ")}, "annotations": {"italic": True, "color": "gray"}}]}})
        else:
            out.append(para(ln))
    return out


def transcript_page(parent_id: str, title: str, text: str) -> dict:
    """A child page under the meeting page holding the whole transcript."""
    blocks = transcript_blocks(text)
    p = notion("POST", "/pages", {"parent": {"type": "page_id", "page_id": parent_id},
                                  "properties": {"title": {"title": rt(title)}},
                                  "icon": {"type": "emoji", "emoji": "📝"},
                                  "children": blocks[:90]})
    append(p["id"], blocks[90:])
    return p


def upload_md(name: str, text: str) -> str:
    fu = notion("POST", "/file_uploads", {"filename": name, "content_type": "text/markdown"})
    boundary = "----lt" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: text/markdown\r\n\r\n").encode() + text.encode("utf-8") + f"\r\n--{boundary}--\r\n".encode()
    tok = os.environ.get("NOTION_TOKEN", "")
    url = str(fu.get("upload_url", ""))
    # Security (audit 2026-10-27): the token goes only to Notion's own API host, never to a URL from a response.
    host = urllib.parse.urlsplit(url)
    if host.scheme != "https" or not (host.hostname == "api.notion.com" or (host.hostname or "").endswith(".notion.com")):
        raise RuntimeError("Notion returned an unexpected upload address; not sending the token there")
    http("POST", url, {"Authorization": f"Bearer {tok}", "Notion-Version": NOTION_VERSION},
         raw=body, ctype=f"multipart/form-data; boundary={boundary}")
    return fu["id"]


def file_block(upload_id: str, name: str) -> dict:
    return {"type": "file", "file": {"type": "file_upload", "file_upload": {"id": upload_id}, "name": name}}


def schema(ds: str) -> dict:
    """2026-10-26: any Meetings database works. The title property, a date property ("Date" first), a text
    property for the summary ("Summary" first) and Project/Workspace (relation or select) are found by type;
    what is missing is skipped instead of failing."""
    props = notion("GET", f"/data_sources/{ds}").get("properties", {})
    by = lambda t: [n for n, v in props.items() if v.get("type") == t]  # noqa: E731
    pick = lambda t, pref: (pref if (props.get(pref) or {}).get("type") == t else (by(t) or [None])[0])  # noqa: E731
    return {"title": (by("title") or ["Name"])[0], "date": pick("date", "Date"),
            "summary": "Summary" if (props.get("Summary") or {}).get("type") == "rich_text" else None,
            "project": link_prop(props, "Project"), "workspace": link_prop(props, "Workspace")}


PROJECT_PROP = {"name": ""}   # 0.8.7 (F3): Settings > Integrations > Notion > Databases > project property
WORKSPACE_PROP = {"name": ""}  # 0.8.8: the Workspace property chosen next to it


def link_prop(props: dict, name: str):
    """(name, type) when the property can carry a link (relation, select, multi_select or text), else None.
    For "Project" and "Workspace" the property chosen in Settings wins when the database has it."""
    chosen = {"Project": PROJECT_PROP, "Workspace": WORKSPACE_PROP}.get(name)
    if chosen and chosen["name"] and chosen["name"] in props:
        name = chosen["name"]
    t = (props.get(name) or {}).get("type")
    return (name, t) if t in ("relation", "select", "multi_select", "rich_text") else None


def add_link_args(ap) -> None:
    """0.8.7 / 0.8.8: the Project and Workspace properties and their values, the same for every Notion write."""
    ap.add_argument("--project-name", default="", help="written to a Project select, multi-select or text property")
    ap.add_argument("--project-page", default="", help="0.8.7: the project's page id(s) for a Project relation")
    ap.add_argument("--project-prop", default="", help="0.8.7: the name of the project property (Settings)")
    ap.add_argument("--workspace-prop", default="", help="0.8.8: the name of the Workspace property (Settings)")
    ap.add_argument("--workspace-value", default="", help="0.8.8: the workspace as an option name or text")
    ap.add_argument("--workspace-page", default="", help="0.8.8: the workspace's page id(s) for a relation, comma-separated")


def apply_link_args(a, ids: dict) -> dict:
    """Sets the chosen property names and fills the page ids the config does not have. Returns ids."""
    PROJECT_PROP["name"] = getattr(a, "project_prop", "") or ""
    WORKSPACE_PROP["name"] = getattr(a, "workspace_prop", "") or ""
    if getattr(a, "project_page", "") and not ids.get("project_page_id"):
        ids["project_page_id"] = ",".join(page_ids(a.project_page))
    if getattr(a, "workspace_page", ""):     # 0.8.8: the value chosen in Settings > Projects wins over the config
        ids["workspace_page_id"] = ",".join(page_ids(a.workspace_page))
    return ids


def link_values(props: dict, ids: dict, project_name: str, workspace_value: str, existing: dict = None) -> dict:
    """Project and Workspace values for a new or existing page of any database (meetings, risks, decisions)."""
    out, existing = {}, existing or {}
    for key, idkey, val in (("Project", "project_page_id", project_name), ("Workspace", "workspace_page_id", workspace_value)):
        lp = link_prop(props, key)
        if not lp or (existing.get(lp[0]) or {}).get(lp[1]):
            continue
        out.update(link_value(lp[0], lp[1], ids.get(idkey), val))
    return out


def page_id_of(ref: str) -> str:
    """The 32 hex characters of a Notion page id or url (dashes allowed), or ""."""
    m = re.findall(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}", ref or "")
    return m[-1].replace("-", "").lower() if m else ""


def find_page(ds: str, start: dt.datetime, date_prop: str | None = "Date") -> dict | None:
    if not date_prop:
        return None
    lo, hi = (start - dt.timedelta(minutes=20)).isoformat(), (start + dt.timedelta(minutes=20)).isoformat()
    r = notion("POST", f"/data_sources/{ds}/query", {"filter": {"and": [
        {"property": date_prop, "date": {"on_or_after": lo}}, {"property": date_prop, "date": {"on_or_before": hi}}]},
        "page_size": 10})
    pages = r.get("results", [])
    if not pages:
        return None

    def gap(p):
        s = ((p.get("properties", {}).get(date_prop) or {}).get("date") or {}).get("start")
        try:
            return abs((dt.datetime.fromisoformat(s.replace("Z", "+00:00")) - start).total_seconds())
        except Exception:  # noqa: BLE001
            return 1e9
    return sorted(pages, key=gap)[0]


def append(page_id: str, blocks: list) -> list | None:
    """Append the blocks; the ids Notion returned for them, or None when a response did not list them."""
    ids = []
    for i in range(0, len(blocks), 90):
        r = notion("PATCH", f"/blocks/{page_id}/children", {"children": blocks[i:i + 90]}) or {}
        got = [b.get("id") for b in r.get("results") or []]
        if len(got) != len(blocks[i:i + 90]) or not all(got):
            ids = None
        elif ids is not None:
            ids += got
    return ids


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, type=Path)
    ap.add_argument("--project-config", type=Path, default=None, help="Control Tower config (meetings_db, page ids)")
    ap.add_argument("--meetings-db", default="", help="data source id of the Meetings database (used without a config)")
    add_link_args(ap)          # project and (0.8.8) workspace properties and values
    ap.add_argument("--title", default="", help="the call's name when it was renamed during the call")
    ap.add_argument("--lt", nargs=2, required=True)
    ap.add_argument("--model", default="", help="not used since PB-127 (no model request here); kept for older callers")
    ap.add_argument("--when", default="", help="ISO date and time of the meeting set in Start call (title date)")
    ap.add_argument("--notes", default="", choices=("", "pending", "failed"),
                    help="PB-127: the call notes are being written (pending) or failed; the page then says so until --fill")
    ap.add_argument("--page", default="", help="PB-32 S6: the call's existing Notion page (id or url): rewrite its generated Summary in place, never look up by date, never create, never upload the transcript")
    ap.add_argument("--fill", action="store_true", help="PB-127: with --page, the notes just got ready: the title gets its topic too")
    ap.add_argument("--actions", default="", help="PB-127: JSON list of the call page's action item lines (its register); "
                                                  "else the action items of the call's lists")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--find-only", action="store_true", help="only look up this meeting's page in Notion")
    return ap.parse_args(argv)


def meeting_ids(a) -> dict:
    ids = config_ids(a.project_config) if a.project_config and a.project_config.exists() else {}
    if a.meetings_db:       # 0.8.7 (F3): one Meetings database for all projects, chosen in Settings, wins over the config
        ids["meetings_db"] = a.meetings_db.replace("collection://", "").strip()
    apply_link_args(a, ids)
    if not ids.get("meetings_db"):
        raise RuntimeError("no Meetings database: choose one in Settings > Integrations > Notion")
    return ids


def call_material(a):
    """(lt wrapup-data numbers, the clean transcript as plain text, the brief with the PM's corrections first)."""
    out = subprocess.run(list(a.lt) + ["wrapup-data", str(a.md)], capture_output=True, text=True, timeout=120).stdout
    stats = json.loads(out[out.find("{"):])
    clean = Path(stats["transcript"]["clean_file"]).read_text(encoding="utf-8")
    plain = re.sub(r"\\([][<>*_#])", r"\1", clean)
    if stats["transcript"].get("lines_real", 0) < 5:
        raise RuntimeError("fewer than 5 transcript lines: nothing to write")
    brief_f = call_layout.sidecar_of(str(a.md), ".brief.md")
    brief = brief_f.read_text(encoding="utf-8") if brief_f.exists() else ""
    brief = (pm_corrections(call_layout.sidecar_of(str(a.md), ".edits.jsonl")).strip() + "\n\n" + brief).strip()
    return stats, plain, brief


def call_info(a) -> dict:
    info = header_info(a.md)
    if a.title.strip():
        info["title"] = a.title.strip()
    if a.when:   # 2026-10-26 (owner): the title's date is the meeting time set in the app, not the recording start
        try:
            info["start"] = dt.datetime.fromisoformat(a.when.replace("Z", "+00:00")).astimezone()
        except ValueError:
            pass
    return info


def link_props(sc: dict, ids: dict, existing: dict, project_name: str, workspace_value: str = "") -> dict:
    """Project and Workspace: a relation from the page ids, or the name as a select, multi-select or text."""
    upd = {}
    for key, idkey, val in (("project", "project_page_id", project_name), ("workspace", "workspace_page_id", workspace_value)):
        if not sc[key]:
            continue
        name, t = sc[key]
        if not (existing.get(name) or {}).get(t):
            upd.update(link_value(name, t, ids.get(idkey), val))
    return upd


def page_ids(v) -> list:
    """0.8.7: one or several page ids ("id1, id2" or a list), in order, without repeats."""
    parts = v if isinstance(v, (list, tuple)) else str(v or "").split(",")
    out = []
    for x in parts:
        x = str(x).strip()
        if x and x not in out:
            out.append(x)
    return out


def link_value(name: str, t: str, page_id, val: str) -> dict:
    if t == "relation" and page_ids(page_id):      # 0.8.7: every page id of the project goes into the relation
        return {name: {"relation": [{"id": x} for x in page_ids(page_id)]}}
    if t == "select" and val:
        return {name: {"select": {"name": val[:100]}}}
    if t == "multi_select" and val:
        return {name: {"multi_select": [{"name": v.strip()[:100]} for v in val.split(",") if v.strip()]}}
    if t == "rich_text" and val:
        return {name: {"rich_text": rt(val)}}
    return {}


def page_name(title: str, topic: str) -> str:
    """2026-10-26 (owner): the page title starts with the meeting's name, then what it was about."""
    topic = (topic or "").strip()
    return title if not topic or topic.lower() in title.lower() else title + ": " + topic


def new_page_props(sc: dict, name: str, start: dt.datetime, summary: str) -> dict:
    props = {sc["title"]: {"title": rt(name + " ") + [
        {"type": "mention", "mention": {"type": "date", "date": {"start": start.isoformat(timespec="minutes")}}}]}}
    if sc["date"]:
        props[sc["date"]] = {"date": {"start": start.isoformat(timespec="seconds")}}
    if sc["summary"]:
        props[sc["summary"]] = {"rich_text": rt(summary)}
    return props


def add_footer(page_id: str, lang: str) -> None:
    try:
        append(page_id, footer_blocks(lang))   # the footer never fails the wrap-up
    except Exception:  # noqa: BLE001
        pass


def write_to_page(page: dict, sc, ids, a, w, blocks, plain, info, lang) -> dict:
    """Append a Pacenote section to the meeting's existing page."""
    upd = link_props(sc, ids, page.get("properties", {}), a.project_name, getattr(a, "workspace_value", ""))
    if upd:
        notion("PATCH", f"/pages/{page['id']}", {"properties": upd})
    hhmm = dt.datetime.now().strftime("%H:%M")
    head = [] if (blocks and blocks[0].get("type") == "paragraph") or not w.get("summary") else [para(w["summary"])]   # Meeting notes start with TL;DR
    got = append(page["id"], [h(2, TXT[lang]["append"].format(hhmm=hhmm))] + head + blocks
                 + [h(3, TXT[lang]["tr_app"])])
    _record_first_write(a.md, page["id"], got or [])      # only the ids Notion returned for these writes; none returned: nothing is archived later
    transcript_page(page["id"], TXT[lang]["tr_app"] + " · " + info["title"], plain)
    add_footer(page["id"], lang)
    return {"ok": True, "url": page.get("url"), "page_id": page.get("id")}


def _children(page_id: str) -> list:
    """All top-level blocks of a page, in order."""
    out, cursor = [], None
    while True:
        r = notion("GET", f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")) or {}
        out += r.get("results", [])
        cursor = r.get("next_cursor")
        if not r.get("has_more") or not cursor:
            return out


def _plain(block: dict) -> str:
    body = block.get(block.get("type"), {}) or {}
    return "".join((t.get("text") or {}).get("content", "") or t.get("plain_text", "") for t in body.get("rich_text", []))


def _generated_section(children: list):
    """The last section Pacenote wrote: (index of its heading, index of the first block after its body). The body ends at
    the transcript heading or sub-page, the footer divider or the end of the page; None when there is none."""
    starts = {T["summary"] for T in TXT.values()}
    append_rx = re.compile("^(?:" + "|".join(re.escape(T["append"]).replace(re.escape("{hhmm}"), r"\d\d:\d\d")
                                              for T in TXT.values()) + ")$")
    ends = {T["transcript"] for T in TXT.values()} | {T["tr_app"] for T in TXT.values()}
    head = None
    for i, b in enumerate(children):
        if b.get("type") == "heading_2" and (_plain(b) in starts or append_rx.match(_plain(b))):
            head = i
    if head is None:
        return None
    end = len(children)
    for j in range(head + 1, len(children)):
        b = children[j]
        if b.get("type") in ("child_page", "divider") or (b.get("type") in ("heading_2", "heading_3") and _plain(b) in ends):
            end = j
            break
    return head, end


def _owned_path(md: Path) -> Path:
    return call_layout.sidecar_of(call_language._named_to_md(md), ".notion-blocks.json")


def read_owned(md: Path, page_id: str):
    """The ids of the blocks Pacenote wrote on this page; None when nothing was recorded (a page of an older version)."""
    try:
        d = json.loads(_owned_path(md).read_text("utf-8"))
        return set(d["ids"]) if d.get("page_id") == page_id else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def read_legacy(md: Path, page_id: str) -> set:
    """The ids of old blocks an earlier Regenerate left in place on a page of an older version (they cannot be told from
    the PM's): the page stays out of date while any of them is still there."""
    try:
        d = json.loads(_owned_path(md).read_text("utf-8"))
        return set(d.get("legacy") or ()) if d.get("page_id") == page_id else set()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return set()


def set_owned(md: Path, page_id: str, ids, legacy=()) -> None:
    """Remember exactly these block ids as Pacenote's (the ones Notion returned for its own writes) and the unresolved
    legacy ids. Atomic (a temp file and a rename); raises OSError when the record cannot be written."""
    p = _owned_path(md)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"page_id": page_id, "ids": list(ids)}
    if legacy:
        body["legacy"] = sorted(legacy)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(body), "utf-8")
    os.replace(tmp, p)


def _record_first_write(md: Path, page_id: str, ids) -> None:
    """The first wrap-up: a record that cannot be saved is dropped, so a later Regenerate treats the page as one without
    a record (keeps and reports the old blocks) instead of trusting stale ids. The wrap-up itself does not fail."""
    try:
        set_owned(md, page_id, ids)
    except OSError as e:
        print(f"wrapup_notion: block record not saved: {e}", file=sys.stderr)
        try:
            _owned_path(md).unlink()
        except OSError:
            pass


def _record_or_undo(md: Path, page_id: str, ids, legacy, written: list) -> None:
    """Regenerate: record the ids, or archive the blocks this run wrote and fail. Blocks that are on the page but not in
    the record would be taken for the PM's by the next run and stay as a second copy."""
    try:
        set_owned(md, page_id, ids, legacy)
    except OSError as e:
        left = 0
        for bid in written:
            try:
                notion("DELETE", f"/blocks/{bid}")
            except Exception:  # noqa: BLE001
                left += 1
        raise RuntimeError(f"the record of Pacenote's Notion blocks could not be saved ({e}); "
                           + (f"{left} new blocks could not be removed again" if left else "the new text was removed again")) from e


def regenerate_page(page: dict, sc, ids, a, w, blocks, lang) -> dict:
    """Regenerate (--page): the generated Summary section of the call's own page and its Summary property are replaced in
    place. Blocks outside the section (the PM's own, the transcript sub-page, the footer) stay; the transcript is never
    uploaded again. New blocks go in first, the old ones are archived after, so a failure in between leaves text, not a hole."""
    pid = page["id"]
    upd = link_props(sc, ids, page.get("properties", {}), a.project_name, getattr(a, "workspace_value", ""))
    if sc["summary"] and w.get("summary"):      # PB-127: a line about notes not ready never blanks the Summary property
        upd[sc["summary"]] = {"rich_text": rt(w["summary"])}
    if upd:
        notion("PATCH", f"/pages/{pid}", {"properties": upd})
    children = _children(pid)
    sec = _generated_section(children)
    head = [] if (blocks and blocks[0].get("type") == "paragraph") or not w.get("summary") else [para(w["summary"])]
    new = head + blocks
    present = {b.get("id") for b in children}
    owned = read_owned(a.md, pid)
    # Old blocks of a page without a record (an older version) cannot be told from the PM's: they stay, their ids are kept
    # apart from Pacenote's, and the page is out of date until every one of them is gone (also on the next Regenerate).
    legacy = read_legacy(a.md, pid) & present
    if sec is None:             # the section was removed by hand: write it again, still without a transcript
        out = {"ok": True, "url": page.get("url"), "page_id": pid}
        items, written = [h(2, TXT[lang]["summary"])] + new, []
        for k in range(0, len(items), 90):
            r = notion("PATCH", f"/blocks/{pid}/children", {"children": items[k:k + 90]}) or {}
            written += [b.get("id") for b in r.get("results") or [] if b.get("id")]
            _record_or_undo(a.md, pid, written, legacy, written)
        if legacy:
            out["legacy_blocks"] = len(legacy)
        return out
    i, end = sec
    start = children[i]
    # Only the blocks Pacenote recorded are archived. Anything else inside the section is the PM's and stays; a page with no
    # record keeps every old block, the new text goes in above it, and the caller is told.
    # Blocks the PM added next to a recorded section are preserved and are not a reason to call the page out of date.
    old = [b for b in children[i + 1:end] if owned is not None and b["id"] in owned]
    if owned is None:
        legacy |= {b["id"] for b in children[i + 1:end]}
    label = TXT[lang]["append"].format(hhmm=dt.datetime.now().strftime("%H:%M")) if start.get("type") == "heading_2" and \
        _plain(start) not in {T["summary"] for T in TXT.values()} else TXT[lang]["summary"]
    notion("PATCH", f"/blocks/{start['id']}", {"heading_2": {"rich_text": rt(label)}})
    chunks = [new[k:k + 90] for k in range(0, len(new), 90)]
    written = []                            # ids Notion returned for our own writes: a block the PM adds meanwhile is not among them
    keep = set(owned or ())                 # the old ids stay recorded until the cleanup below is complete
    for chunk in reversed(chunks):          # each chunk goes right after the heading, so the last one inserted ends up first
        r = notion("PATCH", f"/blocks/{pid}/children", {"children": chunk, "after": start["id"]}) or {}
        written += [b.get("id") for b in r.get("results") or [] if b.get("id")]
        # recorded as each batch succeeds: a failure later cannot leave an untracked copy; a record that cannot be saved
        # takes this run's blocks off the page again and fails the run
        _record_or_undo(a.md, pid, keep | {start["id"]} | set(written), legacy, written)
    for b in old:
        notion("DELETE", f"/blocks/{b['id']}")      # a failure here keeps old and new ids recorded; the next run archives both and writes once
    set_owned(a.md, pid, [start["id"]] + written, legacy)   # cleanup done: only the current section is ours (a failure leaves the superset above: safe, the run fails)
    if end < len(children):     # the transcript heading follows the page language too
        tb = children[end]
        if tb.get("type") in ("heading_2", "heading_3") and _plain(tb) in {T["transcript"] for T in TXT.values()} | {T["tr_app"] for T in TXT.values()}:
            key = "tr_app" if tb["type"] == "heading_3" or _plain(tb) in {T["tr_app"] for T in TXT.values()} else "transcript"
            notion("PATCH", f"/blocks/{tb['id']}", {tb["type"]: {"rich_text": rt(TXT[lang][key])}})
    out = {"ok": True, "url": page.get("url"), "page_id": pid}
    if legacy:
        out["legacy_blocks"] = len(legacy)      # unverified old blocks left in place: the page is not fully in the new language
    return out


def create_page(sc, ids, a, w, blocks, plain, info, lang) -> dict:
    """A new page in the Meetings database."""
    name = page_name(info["title"], w.get("name"))
    props = new_page_props(sc, name, info["start"], w.get("summary", ""))
    props.update(link_props(sc, ids, {}, a.project_name, getattr(a, "workspace_value", "")))
    p = notion("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": ids["meetings_db"]},
                                  "properties": props})
    # The blocks go in by append (also past Notion's 100 with a new page) because only append reports their ids.
    got = append(p["id"], [h(2, TXT[lang]["summary"])] + blocks + [h(2, TXT[lang]["transcript"])])
    _record_first_write(a.md, p["id"], got or [])
    transcript_page(p["id"], TXT[lang]["transcript"] + " · " + name, plain)
    add_footer(p["id"], lang)
    return {"ok": True, "url": p.get("url"), "created": True, "page_id": p.get("id")}


def page_topic(m: dict) -> str:
    """PB-127: what the call was about, for the page title, from the notes (no model): the bold title of the first
    "what happened" point, else its first words, else the first topic's title."""
    first = (m.get("recap") or [""])[0].strip()
    b = re.match(r"^\*\*(.+?)\*\*", first)
    if b:
        return b.group(1).strip().rstrip(".:").strip()
    if first:
        return " ".join(first.replace("**", "").split()[:6]).rstrip(".,:;")
    return ((m.get("topics") or [{}])[0].get("title") or "").strip()


def notes_state_blocks(state: str, md: Path, stats: dict, prep_used: bool, lang: str) -> list:
    """PB-127: notes not ready ("pending") or not written ("failed"): one line that says so, never a model-made
    substitute; the agenda, Sales and Pacenote numbers need no model and stay."""
    T = TXT[lang]
    return [para(T[state])] + extra_blocks(md, stats, prep_used, T, lang)


def add_topic(page: dict, sc: dict, titles, topic: str) -> None:
    """PB-127 (--fill): a page written before its notes were ready gets "<meeting name>: <topic>" once they are. Only a
    title that is still exactly one of the meeting's names (`titles`: the name the app knows, the transcript's own)
    changes; a calendar page or a page renamed in Notion keeps its own."""
    if not topic or not sc["title"]:
        return
    parts = ((page.get("properties") or {}).get(sc["title"]) or {}).get("title") or []
    text = "".join((p.get("text") or {}).get("content", "") or p.get("plain_text", "") for p in parts if p.get("type") == "text")
    title = next((t for t in ([titles] if isinstance(titles, str) else titles) if t and t.strip() == text.strip()), None)
    if not title or page_name(title, topic) == title:
        return
    keep = [{"type": "mention", "mention": p["mention"]} for p in parts if p.get("type") == "mention" and p.get("mention")]
    notion("PATCH", f"/pages/{page['id']}", {"properties": {sc["title"]: {"title": rt(page_name(title, topic) + " ") + keep}}})


def run(a, res: dict) -> int:
    ids = meeting_ids(a)
    sc = schema(ids["meetings_db"])
    if a.find_only:
        p = find_page(ids["meetings_db"], header_info(a.md)["start"], sc["date"])
        print(json.dumps({"ok": True, "found": bool(p), "url": p and p.get("url")}))
        return 0
    stats, plain, brief = call_material(a)
    info = call_info(a)
    # PB-32 (FB2, D56): the page text is in the call's output language; static labels exist for en and uk, other
    # languages get English labels (rules/output_language.yaml).
    out_lang = call_language.output_language(a.md.parent / a.md.name.replace(".named.md", ".md"),
                                             a.md.read_text(encoding="utf-8", errors="replace"))
    lang = out_lang if out_lang in TXT else "en"
    # PB-127 (owner 25.11): no model request. The page is the call's ready Meeting notes and lists; notes still being
    # written or failed give one line that says so (--fill writes the notes in once they are there).
    m = call_meeting(a.md)
    state = "ready" if m else (a.notes or "failed")
    if a.page and not m and not a.fill:     # Regenerate never replaces a written section with a line about missing notes
        raise RuntimeError("the call has no meeting notes to write to its Notion page")
    actions = json.loads(a.actions) if a.actions else None
    w = {"name": page_topic(m), "summary": m.get("tldr", "")} if m else {}
    blocks = meeting_blocks(m, a.md, stats, bool(brief), lang, actions) if m else notes_state_blocks(state, a.md, stats, bool(brief), lang)
    if a.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "name": w.get("name"), "summary": w.get("summary"), "notes": state,
                          "blocks": len(blocks), "ids": ids}, ensure_ascii=False))
        return 0
    res["cost_usd"] = 0.0           # PB-127: nothing here costs a model request
    res["notes"] = state
    if a.page:      # Regenerate or --fill: the page of this call, found by id; a missing page is an error, not a second page
        pid = page_id_of(a.page)
        if not pid:
            raise RuntimeError("--page is not a Notion page id or url")
        page = notion("GET", f"/pages/{pid}")
        if not page.get("id"):
            raise RuntimeError("the call's Notion page was not found")
        res.update(regenerate_page(page, sc, ids, a, w, blocks, lang))
        if a.fill and m:
            add_topic(page, sc, [info["title"], header_info(a.md)["title"]], w.get("name", ""))
        res["created"] = False
    elif (page := find_page(ids["meetings_db"], info["start"], sc["date"])):
        res.update(write_to_page(page, sc, ids, a, w, blocks, plain, info, lang))
    else:
        res.update(create_page(sc, ids, a, w, blocks, plain, info, lang))
    if res.get("legacy_blocks"):    # part of the page is still in the old language: it stays "out of date"
        return None
    if state != "ready":            # only a line about the notes is there: the Notion text is not written in a language yet
        return None
    try:
        call_language.write_sidecar(a.md.parent / a.md.name.replace(".named.md", ".md"), artifact="notion", written_in=out_lang)
    except Exception:  # noqa: BLE001  the page is written; the language note is optional
        pass
    return None


def main(argv=None) -> int:
    a = parse_args(argv)
    res = {"ok": False, "url": None, "created": False, "error": None, "cost_usd": None}
    try:
        done = run(a, res)
        if done is not None:
            return done
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"[:600]
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
