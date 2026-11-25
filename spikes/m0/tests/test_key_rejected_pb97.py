"""PB-97 (owner 25.11): a key route out for 401 or 403 carries "rejected": <status> in the shared state file. The
mark stays through any other outage (a timeout on a recheck) and goes only when the route answers again. The app reads
it (LiveTipsCore AIKeyRejected) to disable the route in every AI picker, never assign it and show an alert."""
import time

from livetips_m0.providers import Completion, FakeProvider, Provider
from livetips_m0.routing import RouteState, RoutedProvider, chain_from


class Failing(Provider):
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def complete(self, *, model, system, user, max_tokens, cache_ttl, on_text=None, extra=None):
        self.calls += 1
        return Completion(model=model, t_start=time.monotonic(), t_done=time.monotonic(), error=self.error)


def ok(text="{}"):
    return FakeProvider(lambda m, u: text)


def run(rp):
    return rp.complete(model="m", system=[{"text": "s"}], user="u", max_tokens=10, cache_ttl=None)


def test_status_and_mark_rules():
    from livetips_m0.routing import rejected_mark, rejected_status      # here, so the other tests fail on their own
    assert rejected_status("OpenAI: HTTP 401 Unauthorized") == 401
    assert rejected_status("Gemini: HTTP 403 PERMISSION_DENIED") == 403
    assert rejected_status("HTTP 429 rate limit") is None and rejected_status("took 4011 ms") is None
    assert rejected_status(None) is None
    assert rejected_mark("openai", "auth", "HTTP 401", None) == 401
    assert rejected_mark("api", "auth", "authentication_error: invalid x-api-key", None) == 0
    assert rejected_mark("gemini", "auth", "authentication", 403) == 403        # no status named: the old one stays
    assert rejected_mark("openai", "error", "timed out", 401) == 401             # another outage keeps the mark
    assert rejected_mark("openai", "credit", "insufficient_quota", None) is None
    assert rejected_mark("code", "auth", "HTTP 401", None) is None              # a signed-out account is not a key
    assert rejected_mark("codex", "auth", "not logged in", None) is None


def test_real_run_401_marks_the_key_and_switches(tmp_path):
    st = RouteState(str(tmp_path / "s.json"))
    openai = Failing("OpenAI: HTTP 401 {\"error\": {\"code\": \"invalid_api_key\"}}")
    rp = RoutedProvider("tips", "openai", {"openai": openai, "gemini": ok('{"gist":"x"}')}, auto=True, state=st,
                        probe_every=3600, chain=chain_from(["openai", "gemini"]))
    c = run(rp)
    assert not c.error and c.route == "gemini"
    s = st.read()["openai"]
    assert s["down"] and s["reason"] == "auth" and s["rejected"] == 401


def test_real_run_403_marks_the_gemini_key_and_switches(tmp_path):
    st = RouteState(str(tmp_path / "s.json"))
    gemini = Failing("Gemini: HTTP 403 PERMISSION_DENIED")
    rp = RoutedProvider("tips", "gemini", {"gemini": gemini, "openai": ok('{"gist":"x"}')}, auto=True, state=st,
                        probe_every=3600, chain=chain_from(["gemini", "openai"]))
    c = run(rp)
    assert not c.error and c.route == "openai"
    s = st.read()["gemini"]
    assert s["down"] and s["reason"] == "auth" and s["rejected"] == 403


def test_mark_survives_a_timeout_on_recheck_and_clears_when_the_key_answers(tmp_path):
    st = RouteState(str(tmp_path / "s.json"))
    st.mark_down("openai", "auth", error="HTTP 403")
    # the recheck times out: the reason becomes "error" but the key stays rejected
    rp = RoutedProvider("tips", "openai", {"openai": Failing("timed out"), "gemini": ok()}, auto=True, state=st,
                        probe_every=0)
    assert rp.probe("openai") is False
    s = st.read()["openai"]
    assert s["reason"] == "error" and s["rejected"] == 403
    # a credit outage of the same route keeps it too
    st.mark_down("openai", "credit", error="insufficient_quota")
    assert st.read()["openai"]["rejected"] == 403
    # the key answers (a new key was pasted): the mark goes with the outage
    rp.legs["openai"] = ok()
    assert rp.probe("openai") is True
    assert "rejected" not in st.read()["openai"] and not st.is_down("openai")


def test_accounts_and_other_outages_get_no_mark(tmp_path):
    st = RouteState(str(tmp_path / "s.json"))
    st.mark_down("code", "auth", error="HTTP 401 not logged in")
    st.mark_down("api", "credit", error="Your credit balance is too low")
    st.mark_down("gemini", "auth")                       # no error text: rejected with an unknown status
    d = st.read()
    assert "rejected" not in d["code"] and "rejected" not in d["api"]
    assert d["gemini"]["rejected"] == 0
