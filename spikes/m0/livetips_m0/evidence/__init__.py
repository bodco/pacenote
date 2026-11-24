"""PB-52 GraphRAG G2: the local evidence store of a project (docs/specs/PB-52-g2-evidence-store.md).

Behind the flag `evidence_store` (config `features`, off by default; LT_EVIDENCE=1 for tests). Nothing in the app
imports this package while the flag is off. One SQLite file per project under <support>/evidence/<uid>.sqlite, never
inside the project folder. Nothing leaves the Mac: ingest reads the project folder and the lists the app passes,
vectors come from the bundled loopback embed server or stay NULL.

    store.py   schema, open, locks, the state-request log, apply_extraction
    ingest.py  deterministic ingest per source type, change detection with moves, the state-only pass
    search.py  search and entity_card with access and time applied in SQL before ranking
    __main__   the CLI: python -m livetips_m0.evidence status|ingest|pass|exclude|override|search|card|rebuild|delete
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
