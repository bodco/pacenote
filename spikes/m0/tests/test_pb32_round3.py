"""PB-32 round 3: exact Notion ownership across two Regenerates, and the sidecar's cross-process lock."""
import threading

from livetips_m0 import call_language as CL
from livetips_m0 import wrapup_notion as W
from test_wrapup_main import _stateful_notion, call, out  # noqa: F401  (fixtures and the stateful fake Notion)


def test_regenerate_twice_with_a_pm_edit_between_keeps_the_pm_block(call, capsys, monkeypatch):
    """The ids come from Notion's write responses, so a paragraph the PM adds after a Regenerate is not recorded as
    Pacenote's and survives the next one."""
    pages, log = _stateful_notion(call, monkeypatch)
    assert W.main(call.base + ["--when", "2026-10-01T09:30:00+00:00"]) == 0
    pid = next(iter(pages))
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0
    kids = pages[pid]
    heading = next(i for i, b in enumerate(kids) if b["type"] == "heading_2")
    kids.insert(heading + 1, {"id": "pm-late", "type": "paragraph",
                              "paragraph": {"rich_text": [{"type": "text", "text": {"content": "added after"}}]}})
    log.clear()
    capsys.readouterr()
    assert W.main(call.base + ["--page", pid]) == 0
    assert "pm-late" in {b["id"] for b in pages[pid]}
    assert "pm-late" not in {p.split("/")[2] for m, p in log if m == "DELETE"}
    assert any(m == "DELETE" for m, p in log)                      # the generated body was still replaced


def test_concurrent_sidecar_writers_do_not_lose_each_other(tmp_path, monkeypatch):
    """An artifact write overlapping the PM's language choice keeps both (flock around read, merge and replace)."""
    md = tmp_path / "c.md"
    md.write_text("x")
    real = CL.read_sidecar
    inside, go = threading.Event(), threading.Event()

    def slow_read(t):
        r = real(t)
        if not inside.is_set():            # the first writer pauses after reading; the second must wait for the lock
            inside.set()
            go.wait(2)
        return r
    monkeypatch.setattr(CL, "read_sidecar", slow_read)
    a = threading.Thread(target=CL.write_sidecar, args=(md,), kwargs={"artifact": "notion", "written_in": "uk"})
    b = threading.Thread(target=CL.write_sidecar, args=(md,), kwargs={"output_language": "en"})
    a.start()
    inside.wait(2)
    b.start()
    go.set()
    a.join()
    b.join()
    monkeypatch.setattr(CL, "read_sidecar", real)
    s = CL.read_sidecar(md)
    assert s["output_language"] == "en" and s["artifacts"] == {"notion": "uk"}
