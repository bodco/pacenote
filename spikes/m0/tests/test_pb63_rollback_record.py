"""PB-63: when Regenerate cannot save the record of its Notion blocks and cannot take them off the page again either,
the blocks left behind are not taken for the PM's: a recovery record written before each append names them, Regenerate
stays failed until they are archived, and then it writes one copy."""
from test_pb32_round6 import _last, _page_with_pm_note
from test_wrapup_main import _stateful_notion, call  # noqa: F401  (call is a fixture)

from livetips_m0 import wrapup_notion as W


def _broken_record(*a, **k):
    raise OSError("disk full")


def test_a_failed_record_and_a_failed_rollback_in_one_run_leave_no_duplicate(call, capsys, monkeypatch):
    pages, pid, clean = _page_with_pm_note(call, capsys, monkeypatch)
    good_notion, good_set = W.notion, W.set_owned

    def no_delete(method, path, body=None):
        if method == "DELETE":
            raise RuntimeError("Notion 502")
        return good_notion(method, path, body)
    monkeypatch.setattr(W, "set_owned", _broken_record)
    monkeypatch.setattr(W, "notion", no_delete)
    assert W.main(call.base + ["--page", pid]) == 1                      # the record and the rollback both failed
    assert "could not be removed" in _last(capsys)["error"]
    stranded = len(pages[pid])
    assert stranded > clean                                              # this run's blocks are still on the page
    assert W._pending_path(call.md).exists()                             # and the recovery record names them

    monkeypatch.setattr(W, "set_owned", good_set)                        # the disk is back, Notion still refuses DELETE
    assert W.main(call.base + ["--page", pid]) == 1                      # Regenerate stays failed
    assert "not reconciled" in _last(capsys)["error"]
    assert len(pages[pid]) == stranded                                   # and writes no further copy

    monkeypatch.setattr(W, "notion", good_notion)
    assert W.main(call.base + ["--page", pid]) == 0                      # the retry archives them first, then writes once
    assert len(pages[pid]) == clean and "pm1" in {b["id"] for b in pages[pid]}
    assert not W._pending_path(call.md).exists()


def test_a_recovery_record_that_cannot_be_written_stops_the_append(call, capsys, monkeypatch):
    pages, pid, clean = _page_with_pm_note(call, capsys, monkeypatch)
    before = [b["id"] for b in pages[pid]]
    monkeypatch.setattr(W, "_write_pending", _broken_record)
    assert W.main(call.base + ["--page", pid]) == 1
    assert "recovery record" in _last(capsys)["error"]
    assert [b["id"] for b in pages[pid]] == before                       # nothing was appended without a record


def _append_fails_once(monkeypatch):
    good = W.notion

    def fail(method, path, body=None):
        if method == "PATCH" and path.endswith("/children"):
            raise RuntimeError("Notion 502")                             # nothing landed
        return good(method, path, body)
    monkeypatch.setattr(W, "notion", fail)
    return good


def test_a_failed_append_never_archives_matching_text_that_is_not_pacenotes(call, capsys, monkeypatch):
    pages, pid, clean = _page_with_pm_note(call, capsys, monkeypatch)
    heading = next(i for i, b in enumerate(pages[pid]) if b["type"] == "heading_2")
    pages[pid].append(dict(pages[pid][heading + 1], id="pm2"))           # the PM copied a generated block
    good = _append_fails_once(monkeypatch)
    assert W.main(call.base + ["--page", pid]) == 1
    assert W._pending_path(call.md).exists()
    monkeypatch.setattr(W, "notion", good)
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0
    ids = {b["id"] for b in pages[pid]}
    assert {"pm1", "pm2"} <= ids and len(pages[pid]) == clean + 1


def test_a_failed_append_on_a_page_without_a_record_keeps_the_old_blocks(call, capsys, monkeypatch):
    pages, _ = _stateful_notion(call, monkeypatch)
    assert W.main(call.base + ["--when", "2026-10-01T09:30:00+00:00"]) == 0
    pid = next(iter(pages))
    W._owned_path(call.md).unlink()                                      # a page written by an older version
    heading, end = W._generated_section(pages[pid])
    old = {b["id"] for b in pages[pid][heading + 1:end]}
    good = _append_fails_once(monkeypatch)
    assert W.main(call.base + ["--page", pid]) == 1
    monkeypatch.setattr(W, "notion", good)
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0
    assert old <= {b["id"] for b in pages[pid]}                          # the old blocks are kept, as before PB-63


def _strand_without_ids(monkeypatch):
    """One run in which the record, the rollback and the ids after the append all fail; returns a restore function."""
    good_notion, good_set, good_pending, n = W.notion, W.set_owned, W._write_pending, [0]

    def before_append_only(*a, **k):
        n[0] += 1
        if n[0] % 2 == 0:
            raise OSError("disk full")
        return good_pending(*a, **k)

    def no_delete(method, path, body=None):
        if method == "DELETE":
            raise RuntimeError("Notion 502")
        return good_notion(method, path, body)
    monkeypatch.setattr(W, "_write_pending", before_append_only)
    monkeypatch.setattr(W, "set_owned", _broken_record)
    monkeypatch.setattr(W, "notion", no_delete)

    def restore():
        for name, fn in (("_write_pending", good_pending), ("set_owned", good_set), ("notion", good_notion)):
            monkeypatch.setattr(W, name, fn)
    return restore


def test_stranded_blocks_are_found_by_content_past_a_block_the_pm_typed_in_between(call, capsys, monkeypatch):
    pages, pid, clean = _page_with_pm_note(call, capsys, monkeypatch)
    restore = _strand_without_ids(monkeypatch)
    assert W.main(call.base + ["--page", pid]) == 1
    heading = next(i for i, b in enumerate(pages[pid]) if b["type"] == "heading_2")
    pages[pid].insert(heading + 2, {"id": "pm3", "type": "paragraph",
                                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": "typed later"}}]}})
    restore()
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0
    ids = {b["id"] for b in pages[pid]}
    assert {"pm1", "pm3"} <= ids and len(pages[pid]) == clean + 1


def test_stranded_blocks_of_a_section_removed_by_hand_are_found_by_content(call, capsys, monkeypatch):
    pages, pid, clean = _page_with_pm_note(call, capsys, monkeypatch)
    heading, end = W._generated_section(pages[pid])
    pages[pid][heading:end] = [b for b in pages[pid][heading:end] if b["id"] == "pm1"]    # the PM deletes the section
    before = len(pages[pid])
    restore = _strand_without_ids(monkeypatch)
    assert W.main(call.base + ["--page", pid]) == 1
    stranded = len(pages[pid]) - before
    assert stranded > 0
    restore()
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0
    assert len(pages[pid]) == before + stranded and "pm1" in {b["id"] for b in pages[pid]}   # one section, not two


def test_stranded_blocks_without_their_ids_are_found_by_content(call, capsys, monkeypatch):
    pages, pid, clean = _page_with_pm_note(call, capsys, monkeypatch)
    restore = _strand_without_ids(monkeypatch)
    assert W.main(call.base + ["--page", pid]) == 1
    assert len(pages[pid]) > clean
    restore()
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0
    assert len(pages[pid]) == clean and "pm1" in {b["id"] for b in pages[pid]}
