"""PB-32 round 5: a failed archive followed by a retry leaves one copy of the Notion section and the PM's block."""
from test_wrapup_main import _stateful_notion, call  # noqa: F401  (call is a fixture)

from livetips_m0 import wrapup_notion as W


def test_regenerate_retry_after_a_failed_archive_leaves_no_duplicate(call, capsys, monkeypatch):
    pages, log = _stateful_notion(call, monkeypatch)
    assert W.main(call.base + ["--when", "2026-10-01T09:30:00+00:00"]) == 0
    pid = next(iter(pages))
    kids = pages[pid]
    heading = next(i for i, b in enumerate(kids) if b["type"] == "heading_2")
    kids.insert(heading + 2, {"id": "pm1", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "PM note"}}]}})
    assert W.main(call.base + ["--page", pid]) == 0                      # a clean Regenerate sets the size of one copy
    clean = len(pages[pid])
    good, deletes = W.notion, [0]

    def flaky(method, path, body=None):
        if method == "DELETE":
            deletes[0] += 1
            if deletes[0] == 2:
                raise RuntimeError("Notion 502")
        return good(method, path, body)
    monkeypatch.setattr(W, "notion", flaky)
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 1                      # the archive failed halfway
    assert len(pages[pid]) > clean                                       # old and new text are both on the page: nothing lost
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0                      # the retry
    assert len(pages[pid]) == clean and "pm1" in {b["id"] for b in pages[pid]}
