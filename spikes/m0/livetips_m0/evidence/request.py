"""PB-52 G2 (S3): one model request of the evidence worker in its own process (spec section 8.5).

    python -m livetips_m0.evidence.request < {"route", "model", "system", "user", "max_tokens", "cache"}
    -> one JSON line: text, tokens, stop reason, error, account usage (never the prompt)

The worker starts it in a new session (its own process group). When `call-active.json` appears during the request,
the worker kills the whole group at once: the HTTP connection of api, openai and gemini closes with the process (Ollama
stops generating when its client goes away), and a `codex` or `claude` CLI child dies with it. The keys come from
the environment the app gives this child only (CLAUDE_API, OPENAI_API_KEY, GEMINI_API_KEY), as for every job.

Per route the request carries the output cap as each provider allows (spec sections 8.6 and 9.1): max_tokens on api;
max_output_tokens on openai and maxOutputTokens (plus thinking room) on gemini, both with the provider's second
request turned off (single_request); a sentence in the prompt on codex; nothing on code (claude -p has no limit);
num_predict exactly the cap on local.
"""
from __future__ import annotations

import json
import os
import sys


def leg(route: str):
    from .. import routing as R
    legs = R.make_legs(os.environ.get("CLAUDE_API", ""), os.environ.get("CLAUDE_WORKSPACE", ""),
                       R.find_claude_cli() if route == "code" else None, 120.0, 600.0)
    return legs.get(route)


def extra_for(route: str, max_tokens: int):
    """The per-route opt-ins of the evidence job. Never anything on api: AnthropicProvider passes extra to the SDK."""
    if route in ("openai", "gemini"):
        return {"single_request": True}
    if route == "local":
        return {"num_predict": int(max_tokens)}
    return None


def run(req: dict) -> dict:
    route = req["route"]
    p = leg(route)
    if p is None:
        missing = {"codex": "Codex CLI is not installed", "code": "Claude Code is not installed"}
        return {"error": "NoRoute: " + missing.get(route, "no API key"), "route": route}
    system = [{"text": req["system"], "cache": "5m" if req.get("cache") else None}]
    system = [{k: v for k, v in b.items() if v is not None} for b in system]
    c = p.complete(model=req["model"], system=system, user=req["user"], max_tokens=int(req["max_tokens"]),
                   cache_ttl="5m" if req.get("cache") and route in ("api", "openai") else None,
                   extra=extra_for(route, int(req["max_tokens"])))
    return {"route": route, "model": c.model, "text": c.text, "in_tokens": c.input_tokens, "cached": c.cache_read,
            "cache_write": c.cache_write_5m + c.cache_write_1h, "out_tokens": c.output_tokens,
            "stop_reason": c.stop_reason, "incomplete": c.incomplete, "error": c.error, "retried": c.retried,
            "account_usage": c.account_usage, "equiv_usd": c.equiv_usd}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
        out = run(req)
    except Exception as e:  # noqa: BLE001  never the prompt or a key, only the kind of failure
        out = {"error": "request: " + type(e).__name__}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
