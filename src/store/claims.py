"""Export per-transcript extraction runs for review and comparison."""

from __future__ import annotations

import json
from pathlib import Path

from src.models.schema import Claim

CLAIMS_EXPORT_DIR = Path("data/stats/extractions")
RUNS_DIR = Path("data/stats/runs")


def snapshot_extractions_dir() -> Path | None:
    """Copy current data/stats/extractions to data/stats/runs/<timestamp>/."""
    import shutil
    from datetime import datetime, timezone

    src = CLAIMS_EXPORT_DIR
    if not src.exists() or not any(src.glob("*.json")):
        return None

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = RUNS_DIR / ts / "extractions"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def export_claims_json(claims: list[Claim], transcript_id: str) -> Path:
    """Write claims to data/stats/extractions/ for manual review."""
    CLAIMS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = CLAIMS_EXPORT_DIR / f"{transcript_id}.json"
    payload = {
        "transcript_id": transcript_id,
        "claim_count": len(claims),
        "claims": [c.model_dump(mode="json") for c in claims],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
