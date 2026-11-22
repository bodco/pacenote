"""PB-32 round 6: a block record that cannot be saved never leaves a second copy on the Notion page, and old blocks of
a page without a record keep the page out of date on every Regenerate until they are gone."""
import json

from test_wrapup_main import _stateful_notion, call  # noqa: F401  (call is a fixture)

from livetips_m0 import call_language as CL
from livetips_m0 import wrapup_notion as W


def _page_with_pm_note(call, capsys, monkeypatch):
    pages, _ = _stateful_notion(call, monkeypatch)
    assert W.main(call.base + ["--when", "2026-10-01T09:30:00+00:00"]) == 0
    pid = next(iter(pages))
    kids = pages[pid]
    heading = next(i for i, b in enumerate(kids) if b["type"] == "heading_2")
    kids.insert(heading + 2, {"id": "pm1", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "PM note"}}]}})
    assert W.main(call.base + ["--page", pid]) == 0                      # a clean Regenerate sets the size of one copy
    capsys.readouterr()
    return pages, pid, len(pages[pid])


def _last(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_a_record_that_cannot_be_saved_takes_the_new_blocks_off_and_a_retry_leaves_one_copy(call, capsys, monkeypatch):
    pages, pid, clean = _page_with_pm_note(call, capsys, monkeypatch)
    good_set = W.set_owned

    def broken_record(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(W, "set_owned", broken_record)
    assert W.main(call.base + ["--page", pid]) == 1                      # the record failed: the run says so
    assert "could not be saved" in _last(capsys)["error"]
    assert len(pages[pid]) == clean                                      # this run's blocks went off again
    monkeypatch.setattr(W, "set_owned", good_set)
    assert W.main(call.base + ["--page", pid]) == 0                      # the retry
    assert len(pages[pid]) == clean and "pm1" in {b["id"] for b in pages[pid]}


def test_a_failed_archive_and_a_failed_final_record_still_retry_to_one_copy(call, capsys, monkeypatch):
    pages, pid, clean = _page_with_pm_note(call, capsys, monkeypatch)
    good_notion, good_set, calls = W.notion, W.set_owned, [0]

    def flaky_notion(method, path, body=None):
        if method == "DELETE":
            raise RuntimeError("Notion 502")
        return good_notion(method, path, body)
    monkeypatch.setattr(W, "notion", flaky_notion)
    assert W.main(call.base + ["--page", pid]) == 1                      # the archive failed: old and new are recorded

    def fail_last(*a, **k):                                              # the archive works now, the final record does not
        calls[0] += 1
        if calls[0] > 1:
            raise OSError("disk full")
        return good_set(*a, **k)
    monkeypatch.setattr(W, "notion", good_notion)
    monkeypatch.setattr(W, "set_owned", fail_last)
    assert W.main(call.base + ["--page", pid]) == 1
    monkeypatch.setattr(W, "set_owned", good_set)
    assert W.main(call.base + ["--page", pid]) == 0
    assert len(pages[pid]) == clean and "pm1" in {b["id"] for b in pages[pid]}


def test_old_blocks_of_a_page_without_a_record_keep_it_out_of_date_on_every_run(call, capsys, monkeypatch):
    pages, _ = _stateful_notion(call, monkeypatch)
    assert W.main(call.base + ["--when", "2026-10-01T09:30:00+00:00"]) == 0
    pid = next(iter(pages))
    W._owned_path(call.md).unlink()                                      # a page written by an older version: no record
    CL.write_sidecar(call.md, output_language="uk")                      # the PM chose another language
    kids = pages[pid]
    heading, end = W._generated_section(kids)
    old = {b["id"] for b in kids[heading + 1:end]}
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0
    assert _last(capsys)["legacy_blocks"] == len(old)
    assert W.main(call.base + ["--page", pid]) == 0                      # again, nothing cleaned by hand
    assert _last(capsys)["legacy_blocks"] == len(old)
    assert old <= {b["id"] for b in pages[pid]}                          # the old blocks were never archived
    assert CL.read_sidecar(call.md)["artifacts"]["notion"] == "en"       # still out of date
    pages[pid][:] = [b for b in pages[pid] if b["id"] not in old]        # the PM deletes them
    assert W.main(call.base + ["--page", pid]) == 0
    assert "legacy_blocks" not in _last(capsys)
    assert CL.read_sidecar(call.md)["artifacts"]["notion"] == "uk"
