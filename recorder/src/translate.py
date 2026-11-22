"""PB-30 (D56): translate a line that Whisper wrote in another language than the one spoken, off the live path.

The command and its folder come only from the app's own config.json, never from a call request (transcript text goes
there). The recorder writes the marked line at once (translated_from set). A worker thread then asks, in this order:
1. the app's local model (Ollama), when it is running and has the model;
2. the PM's configured AI route, through a small command in the tips engine (livetips_m0.translate_text, text only,
   never audio; the route settings, the auto-switch and the spend ledger are the engine's own).
The permission is per launch: the app sets LT_TRANSLATE_ROUTE=1 in the recorder's environment only for a call that may
use the AI; without it the command is ignored whatever config.json says, and only the local model is used.
No route, a timeout, a bad answer or an answer that only repeats the text: the marked line stays as it is (the route and
a failure category are logged, never the text). A model that finds the text already in the target language answers
SAME: the line stays and no further route is asked. `submit()` never blocks; each request has a
deadline of its own; the queue is bounded (the oldest waiting line is dropped, so it stays marked).
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from typing import Callable

LANG_NAMES = {"uk": "Ukrainian", "en": "English", "ru": "Russian", "pl": "Polish", "de": "German", "fr": "French",
              "es": "Spanish", "it": "Italian", "pt": "Portuguese", "be": "Belarusian", "bg": "Bulgarian"}
DEFAULT_LOCAL_MODEL = "gemma4:26b"      # rules/ai.yaml > local: the app's default local model


def prompt_for(src: str, dst: str) -> str:
    s = "the language it is in" if src == "und" else LANG_NAMES.get(src, src)
    d = LANG_NAMES.get(dst, dst)
    return (f"Translate the user's text from {s} to {d}. Keep names, numbers and product terms as they are. "
            f"Answer with the translation only, no quotes, no comments. If the text is already in {d}, answer exactly "
            f"{SAME}.")


SAME = "SAME"


def _key(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(text).casefold()) if c.isalnum())   # "café" = "cafe"


def unchanged(new: str, text: str) -> bool:
    """The answer only repeats the text: nothing was translated."""
    return _key(new) == _key(text)


def is_same(out: str) -> bool:
    """The model says the text is already in the target language: "SAME", also with a comment after it. Case matters:
    "Same" is a real one-word translation."""
    words = str(out).strip().strip("\"'").split()
    return bool(words) and words[0].strip(".,:;!\"'()") == SAME


def is_loopback(url: str) -> bool:
    try:
        return urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1")
    except ValueError:
        return False


class _LoopbackOnly(urllib.request.HTTPRedirectHandler):
    """A redirect never takes the transcript text off this Mac."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_loopback(newurl):
            raise urllib.error.URLError("redirect to a remote host refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Translator:
    """text -> translation, or None when no route answers in time. `translate(text, src, dst)` is blocking and is
    only called from the worker thread."""

    def __init__(self, cfg: dict, accept: Callable[[str, str], bool] = lambda text, dst: True,
                 log: Callable[[str], None] = lambda m: None):
        self.log = log              # the route and a failure category only: never the text, stderr or a key
        self.accept = accept        # a local answer that fails it is not returned: the configured route is tried next
        self.timeout = float(cfg.get("translate_timeout_s", 15.0))
        self.url = str(cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
        self.model = str(cfg.get("translate_model") or os.environ.get("LT_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL)
        # the command runs only when the app launched this recorder with the call's permission (LT_TRANSLATE_ROUTE=1);
        # config.json alone is never enough (a stale command from an earlier call), and no flag means local only
        self.cmd = [str(c) for c in (cfg.get("translate_cmd") or [])] if os.environ.get("LT_TRANSLATE_ROUTE") == "1" else []
        self.cmd_dir = str(cfg.get("translate_dir") or "")
        # a model server that is not on this Mac gets transcript text only with the call's permission (the same flag)
        self.ollama_allowed = is_loopback(self.url) or os.environ.get("LT_TRANSLATE_ROUTE") == "1"
        # no proxy: one from the environment or the system settings would take the text off this Mac
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _LoopbackOnly)
        self._up_until = 0.0
        self._up = False

    def _ollama_up(self) -> bool:
        """Ollama answers and has the model; asked at most once a minute."""
        if not self.ollama_allowed:
            return False
        if time.monotonic() < self._up_until:
            return self._up
        try:
            with self._opener.open(self.url + "/api/tags", timeout=min(2.0, self.timeout)) as r:
                names = {m.get("name") for m in json.load(r).get("models", [])}
            self._up = self.model in names or f"{self.model}:latest" in names
            if not self._up:
                self.log("translate: local: model not found")
        except Exception as e:  # noqa: BLE001
            self.log(f"translate: local: not reachable ({type(e).__name__})")
            self._up = False
        self._up_until = time.monotonic() + 60.0
        return self._up

    def _ollama(self, text: str, src: str, dst: str) -> str | None:
        body = {"model": self.model, "stream": False, "options": {"temperature": 0, "num_predict": 400},
                "messages": [{"role": "system", "content": prompt_for(src, dst)}, {"role": "user", "content": text}]}
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with self._opener.open(req, timeout=self.timeout) as r:
            return str(json.load(r).get("message", {}).get("content", "")).strip() or None

    def _route(self, text: str, src: str, dst: str) -> str | None:
        env = dict(os.environ)
        if self.cmd_dir:
            env["PYTHONPATH"] = self.cmd_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        p = subprocess.run(self.cmd, input=json.dumps({"text": text, "src": src, "dst": dst}), capture_output=True,
                           text=True, timeout=self.timeout, env=env, cwd=self.cmd_dir or None)
        if p.returncode != 0:
            raise RouteFailed(f"exit {p.returncode}")
        return str(json.loads(p.stdout.strip().splitlines()[-1]).get("text", "")).strip() or None

    def _check(self, out: str | None, text: str, dst: str) -> str:
        """-> "ok", "same" (the model says the text is already in the target language) or why the answer is not used."""
        if not out:
            return "empty answer"
        if is_same(out):
            return "same"
        if unchanged(out, text):
            return "answer repeats the text"
        if not self.accept(out, dst):
            return "answer not in the target language"
        return "ok"

    def translate(self, text: str, src: str, dst: str) -> str | None:
        if self._ollama_up():
            try:
                out = self._ollama(text, src, dst)
                why = self._check(out, text, dst)
                if why == "ok":
                    return out
                if why == "same":
                    self.log(f"translate: local: already {dst}, kept")
                    return None
                self.log(f"translate: local: {why}")
            except Exception as e:  # noqa: BLE001
                self.log(f"translate: local: {type(e).__name__}")
                self._up_until = time.monotonic() + 60.0
                self._up = False
        if self.cmd:
            try:
                out = self._route(text, src, dst)
            except Exception as e:  # noqa: BLE001
                self.log(f"translate: route: {e if isinstance(e, RouteFailed) else type(e).__name__}")
                return None
            why = self._check(out, text, dst)
            if why == "ok":
                return out
            self.log(f"translate: route: {f'already {dst}, kept' if why == 'same' else why}")
        return None


class RouteFailed(Exception):
    """The route command exited with an error (its stderr is not logged: it may hold the text)."""


class TranslateWorker(threading.Thread):
    """A bounded queue and one thread. `apply(job, new_text)` is called for every accepted translation; `accept(text,
    dst)` says whether a translation is good enough (it must not be Russian or in a wrong script)."""

    def __init__(self, translate: Callable[[str, str, str], "str | None"], apply: Callable[[dict, str], None],
                 accept: Callable[[str, str], bool], maxlen: int = 16, log: Callable[[str], None] = lambda m: None):
        super().__init__(daemon=True, name="translate")
        self.translate, self.apply, self.accept, self.log = translate, apply, accept, log
        self.jobs: collections.deque = collections.deque()
        self.maxlen = maxlen
        self.cv = threading.Condition()
        self.busy = False
        self.stopping = False
        self.done = self.kept = 0

    def submit(self, job: dict) -> None:
        """O(1). `job` = {"text", "src", "dst", ...}. When the queue is full the oldest line stays marked."""
        with self.cv:
            if len(self.jobs) >= self.maxlen:
                self.jobs.popleft()
                self.kept += 1
            self.jobs.append(job)
            self.cv.notify()

    def run(self) -> None:
        while True:
            with self.cv:
                while not self.jobs and not self.stopping:
                    self.cv.wait(0.5)
                if not self.jobs:
                    return
                job = self.jobs.popleft()
                self.busy = True
            try:
                new = self.translate(job["text"], job["src"], job["dst"])
                if new and not unchanged(new, job["text"]) and self.accept(new, job["dst"]):
                    self.apply(job, new)
                    self.done += 1
                else:
                    self.kept += 1
            except Exception as e:  # noqa: BLE001
                self.kept += 1
                self.log(f"translate: {type(e).__name__}")      # the message may hold the line's text
            finally:
                with self.cv:
                    self.busy = False
                    self.cv.notify_all()

    def stop(self, drain_s: float = 20.0) -> None:
        """Wait up to `drain_s` for the queued lines, then leave the rest marked."""
        end = time.monotonic() + max(0.0, drain_s)
        with self.cv:
            while (self.jobs or self.busy) and time.monotonic() < end:
                self.cv.wait(0.2)
            self.stopping = True
            left = len(self.jobs)
            self.jobs.clear()
            self.kept += left
            self.cv.notify_all()
