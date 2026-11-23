"""PB-51 x PB-70: the commitments register takes the call's client from the client scope the preparation decided, so
G1's "two known clients never join" holds in the app, and with no scope the register keeps G1's series rule."""
import pytest

from livetips_m0 import commitments as C
from livetips_m0.engine import Engine


def _engine(monkeypatch, tmp_path, scope):
    e = Engine.__new__(Engine)
    e.cfg = {"commitments": True, "call_file": str(tmp_path / "2026-11-17-1000-weekly-sync.md"), "title": "Weekly sync"}
    e.register_dir = str(tmp_path)
    e.situation, e.onme_ticked, e.onme_mine = {"actions": [{"what": "fix item 13"}]}, {}, []
    monkeypatch.setattr(Engine, "fix_onme", lambda self, sit: sit)
    monkeypatch.setattr(Engine, "meta", lambda self: {"project": "p"})
    monkeypatch.setattr(Engine, "_client_scope", lambda self, sheet=None: scope)
    seen = {}
    monkeypatch.setattr(C, "record_call", lambda folder, **kw: seen.update(kw) or [])
    return e, seen


@pytest.mark.parametrize("scope,client", [({"client": "acme", "others": ["globex"]}, "acme"), (None, "")])
def test_register_client_is_the_calls_client_scope(monkeypatch, tmp_path, scope, client):
    e, seen = _engine(monkeypatch, tmp_path, scope)
    e.write_register()
    assert seen["client"] == client


def test_a_failing_scope_never_stops_the_register(monkeypatch, tmp_path):
    e, seen = _engine(monkeypatch, tmp_path, None)
    monkeypatch.setattr(Engine, "_client_scope", lambda self, sheet=None: 1 / 0)
    e.write_register()
    assert seen["client"] == ""


def test_item_13_of_two_scoped_clients_with_one_meeting_title_is_never_suggested(tmp_path, monkeypatch):
    """Both calls are "Weekly sync"; PB-70 scoped one to acme and one to globex: no link and no suggestion."""
    for day, client in (("2026-11-10", "globex"), ("2026-11-17", "acme")):
        monkeypatch.setattr(C, "now_iso", lambda d=day: d + "T10:00:00+00:00")
        C.record_call(str(tmp_path), call="live/{}-1000-weekly-sync.md".format(day), project="p", title="Weekly sync",
                      actions=[{"what": "fix item 13"}], theirs=[], client=client, people={})
    items = [i for i in C.load(str(tmp_path)) if i.get("status") in C.LIVE]
    assert len(items) == 2
    assert C.suggestions(str(tmp_path)) == []
