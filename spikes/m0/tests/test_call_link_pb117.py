"""PB-117 (SX8): the call's Notion page carries the link to its Pacenote call page, pacenote://call/<id>.
The vectors are the same as in CoreChecks callLinkPB117 (the app's CallLink.url), so both sides build one string."""
from pathlib import Path

from livetips_m0 import wrapup_notion as W

VECTORS = [
    ("/p/live/2026-11-25-1455-acme-demo.md", "pacenote://call/2026-11-25-1455-acme-demo"),
    ("/p/live/2026-11-25-1455/2026-11-25-1455-weekly.app.md", "pacenote://call/2026-11-25-1455-weekly"),
    ("/p/live/2026-11-25-1455-демо.md", "pacenote://call/2026-11-25-1455-%D0%B4%D0%B5%D0%BC%D0%BE"),
    ("/p/live/a b&c?d#e.md", "pacenote://call/a%20b%26c%3Fd%23e"),
]


def test_call_link_matches_the_app():
    for md, url in VECTORS:
        assert W.call_link(Path(md)) == url, md
    assert W.call_link(Path("/p/live/.md")) == ""


def _texts(blocks):
    out = []
    for b in blocks:
        body = b.get(b["type"], {})
        out.append("".join(r["text"]["content"] for r in body.get("rich_text", [])))
    return out


def test_pacenote_section_has_the_link_in_both_languages(tmp_path):
    md = tmp_path / "2026-11-25-1455-acme-demo.md"
    for lang, label in (("en", "Pacenote call page"), ("uk", "Сторінка дзвінка в Pacenote")):
        b = W.pacenote_blocks({}, md, False, W.TXT[lang])
        last = b[-1]["bulleted_list_item"]["rich_text"]
        assert _texts(b)[-1].startswith(label), lang
        assert last[-1]["text"]["content"] == "pacenote://call/2026-11-25-1455-acme-demo"
        assert last[-1]["annotations"] == {"code": True}
        # plain text only: Notion is never sent a pacenote:// href, so the page write cannot fail on it
        assert all("link" not in r["text"] for r in last)


def test_meeting_and_wrapup_pages_carry_the_link(tmp_path):
    md = tmp_path / "2026-11-25-1455-acme-demo.md"
    md.write_text("# Live transcript: Demo\n")
    url = "pacenote://call/2026-11-25-1455-acme-demo"
    m = {"tldr": "Mike and Emily agreed on the pilot."}
    assert any(url in t for t in _texts(W.meeting_blocks(m, md, {}, False, "en")))
    # the wrap-up page of a call is written by PB-127 (ready notes by plain API); its link is the Pacenote section above
