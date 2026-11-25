"""PB-52 GraphRAG G2: the local evidence store of a project (docs/specs/PB-52-g2-evidence-store.md).

Behind the flag `evidence_store` (config `features`, off by default; LT_EVIDENCE=1 for tests). Nothing in the app
imports this package while the flag is off. One SQLite file per project under <support>/evidence/<uid>.sqlite, never
inside the project folder. Ingest reads the project folder and the lists the app passes, vectors come from the
bundled loopback embed server or stay NULL. Only `pass --route <route>` (S3) sends text out: segments of the project's
sources to the AI the PM chose for the evidence job, or to the model on this Mac.

    store.py    schema, open, locks, the state-request log, apply_extraction
    ingest.py   deterministic ingest per source type, change detection with moves, the state-only pass
    search.py   search and entity_card with access and time applied in SQL before ranking
    extract.py  S3: prompt, catalog, validator, cut detection (pure)
    worker.py   S3: the extraction pass, segment cache, ceilings, the call-active abort, estimate
    request.py  S3: one model request in its own process group
    topics.py   S4: topic detection per client, summary requests and their validation (ask_global is in search.py)
    __main__    the CLI: python -m livetips_m0.evidence status|ingest|pass|estimate|exclude|override|search|card|
                topics|global|rebuild|vacuum|delete
"""
import json
import os

FLAG = "evidence_store"


def enabled(config=None) -> bool:
    """The flag: LT_EVIDENCE=1, or config {"features": {"evidence_store": true}} (a dict or a JSON file path)."""
    if os.environ.get("LT_EVIDENCE", "") == "1":
        return True
    cfg = config
    if isinstance(cfg, str) and cfg:
        try:
            with open(os.path.expanduser(cfg), encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return False
    feats = cfg.get("features") if isinstance(cfg, dict) else None
    return bool(isinstance(feats, dict) and feats.get(FLAG) is True)
