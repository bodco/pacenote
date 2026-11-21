"""PB-30: the tips engine's one-line translation command, and the readers that must skip its "translated" events."""
import json

import pytest

from livetips_m0 import transcript, translate_text
from livetips_m0.providers import FakeProvider


def test_translate_uses_the_provider_and_sends_text_only():
    seen = []

    def responder(model, user):
        seen.append(user)
        return " Це дуже добре "
    out = translate_text.translate("Это очень хорошо", "ru", "uk", provider=FakeProvider(responder))
    assert out == "Це дуже добре" and seen == ["Это очень хорошо"]


def test_translate_raises_on_a_provider_error():
    class Bad(FakeProvider):
        def complete(self, **kw):
            c = super().complete(**kw)
            c.error = "no credit"
            return c
    with pytest.raises(RuntimeError, match="no credit"):
        translate_text.translate("x", "ru", "uk", provider=Bad(lambda m, u: "y"))


def test_translated_events_in_the_timing_sidecar_do_not_shift_the_line_pairing(tmp_path):
    md = tmp_path / "c.md"
    md.write_text("[10:00:00] Співрозмовник (uk): Це дуже добре\n[10:00:00] Співрозмовник (uk): Друга\n", encoding="utf-8")
    rows = [{"t": "10:00:00", "ch": "them", "seg_end": "10:00:02.000", "written": "10:00:03.000"},
            {"event": "translated", "t": "10:00:00", "ch": "them", "translated_from": "ru", "original": "Это", "text": "Це"},
            {"t": "10:00:00", "ch": "them", "seg_end": "10:00:09.000", "written": "10:00:10.000"}]
    (tmp_path / "c.md.timing.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    lines = transcript.load_call(md, "Я")
    assert [round(l.seg_end) for l in lines] == [36002, 36009]
