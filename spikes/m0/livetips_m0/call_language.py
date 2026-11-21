"""One output language (PB-32, FB2, owner decision D56): the language of everything a call writes is the call language.
The rules are in rules/output_language.yaml; LiveTipsCore/CallLanguage.swift is the same resolver in Swift (both are
checked against Fixtures/output_language_cases.json). The interface language is never an input.

Resolution order: explicit choice for this call > dominant language of the call > first language of Start call >
project client_language (hint) > Settings > "en".

  resolve(explicit=..., dominant=..., start_langs=[...], client_language="...", settings="...") -> "uk"
  spoken(transcript_text, meta)          the adapter: the one place that reads the recorder (PB-30)
  toggle_languages(sp, start_langs, after_call)   the languages the Output language control lists (>= 2: show it)
  read_sidecar / write_sidecar / stale_artifacts  the per-call choice and the language of each artifact
"""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path
from typing import Iterable, Optional

from . import call_layout

DEFAULT = "en"
SPOKEN_MIN_SHARE = 0.10
MAX_LANGUAGES = 3
ARTIFACTS = ("notes", "meeting_notes", "followup", "notion", "sheet")
SIDECAR_SUFFIX = ".outlang.json"

NAMES = {"uk": "Ukrainian", "en": "English", "pl": "Polish", "de": "German", "es": "Spanish", "fr": "French",
         "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "cs": "Czech", "sk": "Slovak", "hu": "Hungarian"}
CLIENT_HINTS = (("ukrain", "uk"), ("україн", "uk"), ("english", "en"), ("англій", "en"), ("spanish", "es"),
                ("español", "es"), ("german", "de"), ("deutsch", "de"), ("french", "fr"), ("polish", "pl"),
                ("polsk", "pl"))

_TAG = re.compile(r"^\[\d\d:\d\d:\d\d\][^\n(]*\(([a-z]{2})\):", re.M)
_HEADER = re.compile(r"^- (?:Languages|Мови):[^\n=]*=\s*([a-z]{2})", re.M)


def normalize(code) -> Optional[str]:
    """'uk', 'UK', 'uk-UA', 'uk_UA' -> 'uk'; '', 'auto', None, 'ukr', 'Ukrainian' -> None."""
    if not isinstance(code, str):
        return None
    c = code.strip().lower().replace("_", "-").split("-")[0]
    return c if re.fullmatch(r"[a-z]{2}", c) else None


def language_name(code) -> str:
    """The name given to a model ('Ukrainian'); a code without a name is written as the code."""
    c = normalize(code) or DEFAULT
    return NAMES.get(c, c)


def hint_from_client_language(text) -> Optional[str]:
    """The project's client_language free text ('English', 'Ukrainian (formal)') -> code, None when not recognised."""
    t = (text or "").strip().lower()
    for key, code in CLIENT_HINTS:
        if t.startswith(key):
            return code
    return normalize(t)


def _codes(items: Optional[Iterable]) -> list:
    out: list = []
    for x in items or []:
        c = normalize(x)
        if c and c not in out:
            out.append(c)
    return out


class Spoken:
    """What the recorder says was spoken: `dominant` (code or None) and `shares` {code: share of the call}."""

    def __init__(self, dominant: Optional[str] = None, shares: Optional[dict] = None):
        self.dominant = dominant
        self.shares = shares or {}

    def languages(self) -> list:
        """The dominant language first, then those at or above the spoken threshold, the biggest share first
        (ties: first seen)."""
        ranked = sorted(self.shares.items(), key=lambda kv: -kv[1])
        out = [c for c, s in ranked if s >= SPOKEN_MIN_SHARE - 1e-9 and c != self.dominant]
        return (([self.dominant] if self.dominant else []) + out)[:MAX_LANGUAGES]



def spoken(transcript_text: str = "", meta: Optional[dict] = None) -> Spoken:
    """The adapter (the only function that knows the recorder's shape; PB-30 may change it, nothing else does).
    1. `meta` of the call, when PB-30 writes it: `dominant_language` ("uk" or empty) and `languages` ({"uk": 0.71, ...}
       or ["uk", "en"]).
    2. Otherwise the transcript: the majority of the line tags "[13:01:04] Name (uk): ..." (the same value as
       callnotes.call_lang) and the share of each tag; no tags: the header's first language, no shares."""
    meta = meta or {}
    dom = normalize(meta.get("dominant_language"))
    shares: dict = {}
    langs = meta.get("languages")
    if isinstance(langs, dict):
        for k, v in langs.items():
            c = normalize(k)
            if c and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                shares[c] = float(v)
    elif isinstance(langs, (list, tuple)):
        c_list = _codes(langs)
        shares = {c: 1.0 / len(c_list) for c in c_list}
    if not shares or not dom:
        tags = _TAG.findall(transcript_text or "")
        if tags:
            counts = collections.Counter(tags)
            if not shares:
                shares = {c: n / len(tags) for c, n in counts.items()}
            if not dom:
                dom = counts.most_common(1)[0][0]
        elif not dom:
            m = _HEADER.search(transcript_text or "")
            dom = m.group(1) if m else None
    return Spoken(dom, shares)


def resolve(explicit=None, dominant=None, start_langs=None, client_language="", settings="") -> str:
    """The output language of a call. Pass `dominant` from spoken(...).dominant after the call, None before it."""
    for c in (normalize(explicit), normalize(dominant), next(iter(_codes(start_langs)), None),
              hint_from_client_language(client_language), normalize(settings)):
        if c:
            return c
    return DEFAULT


def toggle_languages(sp: Optional[Spoken], start_langs=None, after_call: bool = True) -> list:
    """The languages the Output language control lists: after the call those spoken from 10% of it, before the call
    the ones chosen in Start call (up to 3). The control is shown only when there are two or more."""
    if after_call and sp is not None and sp.languages():
        return sp.languages()
    return _codes(start_langs)[:MAX_LANGUAGES]


def is_multi(languages: list) -> bool:
    return len(languages) >= 2


def sidecar_path(transcript) -> Path:
    return call_layout.sidecar_of(transcript, SIDECAR_SUFFIX)


def read_sidecar(transcript) -> dict:
    """{'output_language': code|None, 'set_by': str|None, 'artifacts': {name: code}}; never raises."""
    out = {"output_language": None, "set_by": None, "artifacts": {}}
    try:
        d = json.loads(sidecar_path(transcript).read_text("utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(d, dict):
        return out
    out["output_language"] = normalize(d.get("output_language"))
    out["set_by"] = d.get("set_by") if isinstance(d.get("set_by"), str) else None
    arts = d.get("artifacts")
    if isinstance(arts, dict):
        out["artifacts"] = {k: c for k, v in arts.items() if k in ARTIFACTS and (c := normalize(v))}
    return out


def write_sidecar(transcript, output_language=None, artifact=None, written_in=None) -> dict:
    """Set the PM's choice (`output_language`) and/or record the language an artifact was written in. Atomic; keeps
    what is already there; returns what was written."""
    cur = read_sidecar(transcript)
    if output_language is not None:
        c = normalize(output_language)
        if c:
            cur["output_language"], cur["set_by"] = c, "user"
    if artifact in ARTIFACTS and normalize(written_in):
        cur["artifacts"][artifact] = normalize(written_in)
    body = {k: v for k, v in (("output_language", cur["output_language"]), ("set_by", cur["set_by"])) if v}
    body["artifacts"] = cur["artifacts"]
    p = sidecar_path(transcript)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)
    return cur


def stale_artifacts(artifacts: dict, output_language) -> list:
    """Artifacts written in another language than the output language ('out of date'), in the fixed artifact order."""
    out = normalize(output_language)
    return [a for a in ARTIFACTS if a in artifacts and out and normalize(artifacts[a]) != out]


def output_language(transcript, transcript_text: str = "", meta: Optional[dict] = None, start_langs=None,
                    client_language: str = "", settings: str = "") -> str:
    """The whole chain for a call with a transcript: sidecar choice > dominant (adapter) > the rest."""
    return resolve(read_sidecar(transcript)["output_language"], spoken(transcript_text, meta).dominant, start_langs,
                   client_language, settings)
