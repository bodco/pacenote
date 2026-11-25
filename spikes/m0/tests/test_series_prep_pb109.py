"""PB-109 (owner 25.11, review r1 B1/B2): the series the PM put together, and the calls taken out of a series, reach
the brief on the packed ways (Codex, OpenAI, Gemini, local, API) the same as on the Claude ways."""
import os

from livetips_m0 import localprep


def live_folder(tmp_path):
    root = tmp_path / "proj"
    live = root / "live"
    live.mkdir(parents=True)
    for name, text in [("2026-11-03-1000-weekly-sync.app.md", "Weekly sync of Nov 3: Mike owes the budget."),
                       ("2026-11-10-1000-weekly-sync.app.md", "Weekly sync of Nov 10: Sarah demoed the build."),
                       ("2026-11-17-1000-weekly-sync.app.md", "Weekly sync of Nov 17: taken out by the PM."),
                       ("2026-11-12-1000-kickoff-with-emily.app.md", "Kickoff with Emily: scope and dates."),
                       ("2026-10-27-1000-weekly-sync.md", "Weekly sync of Oct 27 (skill transcript).")]:
        (live / name).write_text(text)
    return root, live


def req(root, live, **kw):
    r = {"title": "Weekly sync", "context": "", "root": str(root), "live": str(live), "use_project_folder": True,
         "folders": [], "files": [], "today": "2026-11-24", "call_type": "auto"}
    r.update(kw)
    return r


def names(paths):
    return [os.path.basename(p) for p in paths]


def test_without_series_fields_the_pick_by_title_is_unchanged(tmp_path):
    root, live = live_folder(tmp_path)
    r = req(root, live)
    assert localprep.series_previous(r) == localprep.previous_calls(str(live), "Weekly sync")
    assert names(localprep.series_previous(r)) == ["2026-11-17-1000-weekly-sync.app.md", "2026-11-10-1000-weekly-sync.app.md",
                                                   "2026-11-03-1000-weekly-sync.app.md"]


def test_a_call_taken_out_is_skipped_and_does_not_take_a_slot(tmp_path):
    root, live = live_folder(tmp_path)
    r = req(root, live, series_exclude=["2026-11-17-1000-weekly-sync"])
    assert names(localprep.series_previous(r)) == ["2026-11-10-1000-weekly-sync.app.md", "2026-11-03-1000-weekly-sync.app.md",
                                                   "2026-10-27-1000-weekly-sync.md"]
    pack, used, _, _ = localprep.build_pack_ex(r)
    assert "taken out by the PM" not in pack
    assert "Oct 27" in pack


def test_a_manual_series_brings_its_own_calls_and_open_commitments_first(tmp_path):
    root, live = live_folder(tmp_path)
    picked = [str(live / "2026-11-12-1000-kickoff-with-emily.app.md"), str(live / "2026-11-10-1000-weekly-sync.app.md")]
    r = req(root, live, series_name="Orbit weekly", series_calls=picked,
            series_open=["- Mike: send the budget (due 2026-11-28) [2026-11-03-1000-weekly-sync]"])
    assert localprep.series_previous(r) == picked
    pack, used, _, _ = localprep.build_pack_ex(r)
    assert "Kickoff with Emily" in pack                       # a call of another title, picked by the PM
    assert "taken out by the PM" not in pack and "Nov 3" not in pack.split("Open commitments")[0]
    assert pack.index("Mike: send the budget") < pack.index("Kickoff with Emily")
    assert 'series "Orbit weekly"' in pack


def test_open_commitments_reach_the_pack_when_the_notes_stand_for_the_transcripts(tmp_path):
    root, live = live_folder(tmp_path)
    r = req(root, live, series_name="Orbit weekly", series_calls=[], series_open=["- me: share the deck [2026-11-10-1000-weekly-sync]"],
            include_prev=False)
    pack, used, _, _ = localprep.build_pack_ex(r)
    assert "me: share the deck" in pack
    assert localprep.series_previous(r) == []                   # an empty manual series: no fallback to the title


def test_with_the_project_folder_excluded_the_series_is_not_read(tmp_path):
    root, live = live_folder(tmp_path)
    r = req(root, live, use_project_folder=False, series_name="Orbit weekly", series_calls=[str(live / "2026-11-10-1000-weekly-sync.app.md")],
            series_open=["- me: share the deck [x]"])
    pack, used, _, _ = localprep.build_pack_ex(r)
    assert "share the deck" not in pack and "Sarah demoed" not in pack
