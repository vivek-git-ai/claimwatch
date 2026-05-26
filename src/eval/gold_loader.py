"""Load and validate golden-set files for extraction and resolution eval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class GoldExtractionClaim(BaseModel):
    """One row in an extraction gold file."""

    claim_id: str = Field(description="Stable gold id, e.g. GOLD-00-001")
    thread_id: str | None = None
    source_doc: str
    source_section: str
    date_made: str
    speaker: str
    quote: str
    paraphrase: str
    category: str | None = None
    subject: str | None = None
    timeframe: str | None = None
    target_value: str | None = None
    hedge_level: str | None = None
    falsifiable: str | None = None
    notes: str | None = None


class GoldExtractionFile(BaseModel):
    schema_version: str = "1.0"
    transcript_id: str
    transcript_date: str
    labelled_by: str = ""
    reviewed: bool = False
    review_notes: str | None = None
    notes: str | None = None
    total_claims: int = 0
    claims: list[GoldExtractionClaim] = Field(default_factory=list)

    @property
    def is_user_reviewed(self) -> bool:
        return bool(self.reviewed)


class GoldResolutionClaim(BaseModel):
    """One row in the resolution checkpoint gold file."""

    claim_id: str
    source_doc: str
    quote: str
    paraphrase: str
    expected_status: str
    expected_resolved_at_transcript: str | None = None
    expected_evidence_quote: str | None = None
    expected_resolved_by_gold_id: str | None = None
    notes: str | None = None


class GoldResolutionFile(BaseModel):
    schema_version: str = "1.0"
    eval_type: str = "resolution_checkpoint"
    checkpoint_transcript_id: str
    checkpoint_index: int
    transcripts_in_scope: list[str] = Field(default_factory=list)
    labelled_by: str = ""
    reviewed: bool = False
    review_notes: str | None = None
    notes: str | None = None
    total_claims: int = 0
    claims: list[GoldResolutionClaim] = Field(default_factory=list)

    @property
    def is_user_reviewed(self) -> bool:
        return bool(self.reviewed)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_extraction_gold_file(path: Path) -> GoldExtractionFile:
    data = _load_json(path)
    if data.get("claims") is None:
        data["claims"] = []
    return GoldExtractionFile.model_validate(data)


def load_extraction_gold_dir(gold_dir: Path) -> list[GoldExtractionFile]:
    """Load every extraction gold file in `gold_dir`, ignoring `_template.json`."""
    files: list[GoldExtractionFile] = []
    for path in sorted(gold_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        files.append(load_extraction_gold_file(path))
    return files


def load_resolution_checkpoint(path: Path) -> GoldResolutionFile:
    data = _load_json(path)
    if data.get("claims") is None:
        data["claims"] = []
    return GoldResolutionFile.model_validate(data)
