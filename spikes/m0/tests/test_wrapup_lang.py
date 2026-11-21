"""PB-32 S2 (FB2, D56): the Notion meeting page is written in the call's output language."""
import types

from livetips_m0 import call_language as CL
from livetips_m0 import wrapup_notion as W
from test_wrapup_main import call, out  # noqa: F401  (the fixture and the helper of the end-to-end tests)


def _retag(call, tag, header="Me = en, allowed = en"):
    call.md.write_text("# Live transcript: Weekly sync\n- Start: 2026-10-01 10:00:00\n- Languages: " + header + "\n\n"
                       + "".join("[10:00:0%d] Me (%s): line\n" % (i, tag) for i in range(5)))


def test_language_follows_the_call_not_the_header_first_language(call, capsys):
    _retag(call, "uk")                      # header says en, the lines are Ukrainian
    assert W.main(call.base + ["--when", "2026-10-01T09:30:00+00:00"]) == 0
    assert call.seen["lang"] == "uk"
    assert CL.read_sidecar(call.md)["artifacts"] == {"notion": "uk"}


def test_explicit_choice_for_the_call_wins(call, capsys):
    _retag(call, "uk")
    CL.write_sidecar(call.md, output_language="en")
    assert W.main(call.base + ["--when", "2026-10-01T09:30:00+00:00"]) == 0
    assert call.seen["lang"] == "en"


def test_language_without_static_labels_gets_text_in_it_and_english_labels(call, capsys):
    _retag(call, "pl")
    assert W.main(call.base + ["--when", "2026-10-01T09:30:00+00:00"]) == 0
    assert call.seen["lang"] == "pl"
    page = [c for c in call.calls if c[0] == "POST" and c[1] == "/pages"][0][2]
    heads = [b["heading_2"]["rich_text"][0]["text"]["content"] for b in page["children"] if b["type"] == "heading_2"]
    assert heads == ["Summary", "Transcript"]


def test_the_prompt_names_the_language():
    seen = {}

    class P:
        def complete(self, model, system, user, max_tokens, cache_ttl=None):
            seen["system"] = system[0]["text"]
            return types.SimpleNamespace(text='{"name": "n", "summary": "s"}', error=None, model=model, input_tokens=1,
                                         cache_write_5m=0, cache_write_1h=0, cache_read=0, output_tokens=1)
    W.claude_wrapup("t", "", "m", "pl", provider=P())
    assert "Write in Polish" in seen["system"]


def _with_page(call):
    _retag(call, "uk")
    call.state["pages"] = [{"id": "near", "url": "u-near", "properties": {"Date": {"date": {"start": "2026-10-01T10:05:00"}}}}]


def test_notion_language_is_recorded_only_after_the_page_write(call, capsys, monkeypatch):
    """PB-32 S6: Regenerate re-runs wrapup_notion for a call with a page; Notion counts as current only once it took the write."""
    _with_page(call)
    seen = []

    def spy(method, path, body=None):
        if method in ("PATCH", "POST"):
            seen.append(CL.read_sidecar(call.md)["artifacts"].get("notion"))
        return call.notion(method, path, body)
    monkeypatch.setattr(W, "notion", spy)
    assert W.main(call.base) == 0
    assert seen and all(v is None for v in seen)
    assert CL.read_sidecar(call.md)["artifacts"] == {"notion": "uk"}


def test_notion_language_is_not_recorded_when_the_page_write_fails(call, capsys, monkeypatch):
    _with_page(call)

    def failing(method, path, body=None):
        if method == "PATCH" and path.startswith("/blocks/"):
            raise RuntimeError("PATCH -> 500")
        return call.notion(method, path, body)
    monkeypatch.setattr(W, "notion", failing)
    assert W.main(call.base) == 1
    assert "notion" not in CL.read_sidecar(call.md)["artifacts"]
