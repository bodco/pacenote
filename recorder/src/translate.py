"""PB-30 (D56): translate a line that Whisper wrote in another language than the one spoken, off the live path.

The recorder writes the marked line at once (translated_from set). A worker thread then asks, in this order:
1. the app's local model (Ollama), when it is running and has the model;
2. the PM's configured AI route, through a small command in the tips engine (livetips_m0.translate_text, text only,
   never audio; the route settings, the auto-switch and the spend ledger are the engine's own).
No route, a timeout or a bad answer: the marked line stays as it is. `submit()` never blocks; each request has a
deadline of its own; the queue is bounded (the oldest waiting line is dropped, so it stays marked).
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import threading
import time
import urllib.request
from typing import Callable

LANG_NAMES = {"uk": "Ukrainian", "en": "English", "ru": "Russian", "pl": "Polish", "de": "German", "fr": "French",
              "es": "Spanish", "it": "Italian", "pt": "Portuguese", "be": "Belarusian", "bg": "Bulgarian"}
DEFAULT_LOCAL_MODEL = "gemma4:26b"      # rules/ai.yaml > local: the app's default local model


def prompt_for(src: str, dst: str) -> str:
    s = "the language it is in" if src == "und" else LANG_NAMES.get(src, src)
    d = LANG_NAMES.get(dst, dst)
    return (f"Translate the user's text from {s} to {d}. Keep names, numbers and product terms as they are. "
            "Answer with the translation only, no quotes, no comments.")


class Translator:
    """text -> translation, or None when no route answers in time. `translate(text, src, dst)` is blocking and is
    only called from the worker thread."""

    def __init__(self, cfg: dict):
        self.timeout = float(cfg.get("translate_timeout_s", 15.0))
        self.url = str(cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
        self.model = str(cfg.get("translate_model") or os.environ.get("LT_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL)
        self.cmd = [str(c) for c in (cfg.get("translate_cmd") or [])]
        self.cmd_dir = str(cfg.get("translate_dir") or "")
        self._up_until = 0.0
        self._up = False

    def _ollama_up(self) -> bool:
        """Ollama answers and has the model; asked at most once a minute."""
        if time.monotonic() < self._up_until:
            return self._up
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=min(2.0, self.timeout)) as r:
                names = {m.get("name") for m in json.load(r).get("models", [])}
            self._up = self.model in names or f"{self.model}:latest" in names
        except Exception:  # noqa: BLE001
            self._up = False
        self._up_until = time.monotonic() + 60.0
        return self._up

    def _ollama(self, text: str, src: str, dst: str) -> str | None:
        body = {"model": self.model, "stream": False, "options": {"temperature": 0, "num_predict": 400},
                "messages": [{"role": "system", "content": prompt_for(src, dst)}, {"role": "user", "content": text}]}
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return str(json.load(r).get("message", {}).get("content", "")).strip() or None

    def _route(self, text: str, src: str, dst: str) -> str | None:
        env = dict(os.environ)
        if self.cmd_dir:
            env["PYTHONPATH"] = self.cmd_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        p = subprocess.run(self.cmd, input=json.dumps({"text": text, "src": src, "dst": dst}), capture_output=True,
                           text=True, timeout=self.timeout, env=env)
        if p.returncode != 0:
            return None
        return str(json.loads(p.stdout.strip().splitlines()[-1]).get("text", "")).strip() or None

    def translate(self, text: str, src: str, dst: str) -> str | None:
        if self._ollama_up():
            try:
                out = self._ollama(text, src, dst)
                if out:
                    return out
            except Exception:  # noqa: BLE001
                self._up_until = time.monotonic() + 60.0
                self._up = False
        if self.cmd:
            try:
                return self._route(text, src, dst)
            except Exception:  # noqa: BLE001
                return None
        return None


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
                if new and self.accept(new, job["dst"]):
                    self.apply(job, new)
                    self.done += 1
                else:
                    self.kept += 1
            except Exception as e:  # noqa: BLE001
                self.kept += 1
                self.log(f"translate: {e!r}")
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
