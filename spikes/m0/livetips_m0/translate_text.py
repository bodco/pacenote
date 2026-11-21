"""One line translated on the PM's AI route (PB-30, D56), for the recorder's background translation.

The recorder runs `python -m livetips_m0.translate_text` and sends {"text", "src", "dst"} on stdin; the answer is one
JSON line {"text": "..."} on stdout. Text only, never audio. The route, the auto-switch and the spend ledger are the
app's own (routing.provider_from_env, job "translate"; LT_AI_ROUTE etc. come from the environment the app sets).
The text may go only to the AIs the PM allowed for live tips: the same chain and exclusions (background=True), and
nothing runs unless the recorder was launched with LT_TRANSLATE_ROUTE=1 (the app's per-call permission).
Exit code 1 and no output: no route answered, the recorder keeps the marked line.
"""
import json
import os
import sys

NAMES = {"uk": "Ukrainian", "en": "English", "ru": "Russian", "pl": "Polish", "de": "German", "fr": "French"}
MODEL = "claude-haiku-5-5"      # the cheapest of the Claude models; LT_TRANSLATE_MODEL overrides it


def translate(text: str, src: str, dst: str, provider=None) -> str:
    from .routing import chain_allowed, provider_from_env
    if provider is None and os.environ.get("LT_TRANSLATE_ROUTE") != "1":
        raise RuntimeError("translation through the AI route is not allowed for this call")
    prov = provider or provider_from_env("translate", timeout=12.0, background=True)
    if getattr(prov, "preferred", None) and not chain_allowed(prov.preferred, "tips"):
        raise RuntimeError("the live tips route is not usable for text of a call: " + str(prov.preferred))
    s = "the language it is in" if src == "und" else NAMES.get(src, src)
    system = (f"Translate the user's text from {s} to {NAMES.get(dst, dst)}. Keep names, numbers and "
              "product terms as they are. Answer with the translation only, no quotes, no comments.")
    c = prov.complete(model=os.environ.get("LT_TRANSLATE_MODEL") or MODEL, system=[{"text": system}], user=text,
                      max_tokens=400, cache_ttl=None)
    if c.error:
        raise RuntimeError("translation request failed: " + str(c.error)[:200])
    return c.text.strip()


def main() -> int:
    try:
        req = json.loads(sys.stdin.read())
        out = translate(str(req["text"]), str(req.get("src") or "und"), str(req.get("dst") or "uk"))
    except Exception as e:  # noqa: BLE001
        print(repr(e)[:200], file=sys.stderr)
        return 1
    if not out:
        return 1
    print(json.dumps({"text": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
