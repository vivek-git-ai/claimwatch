"""Claude-style claim corpus models (claims_made, threads, resolutions)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from src.models.schema import Section

Falsifiability = Literal["Y", "partial", "N"]
CorpusResolutionStatus = Literal[
    "open",
    "confirmed",
    "revised",
    "failed",
    "partial",
    "unresolvable",
    "stale",
    "n/a",
]
CorpusHedgeLevel = Literal["firm", "moderate", "soft"]


class ClaimMade(BaseModel):
    """One atomic forward-looking utterance (Pass 1)."""

    claim_id: str
    thread_id: str | None = None
    source_doc: str
    source_section: Section
    date_made: date
    speaker: str
    quote: str
    paraphrase: str
    category: str
    subject: str
    timeframe: str
    target_value: str
    hedge_level: CorpusHedgeLevel
    falsifiable: Falsifiability
    notes: str | None = None


class ClaimResolution(BaseModel):
    status: CorpusResolutionStatus = "open"
    resolved_by: list[str] = Field(
        default_factory=list,
        description="Successor claim IDs when status=revised (not a person)",
    )
    evidence_quote: str | None = None
    resolution_notes: str | None = None
    resolved_at_date: date | None = Field(
        default=None,
        description="Transcript date when status first left open",
    )
    resolved_at_transcript: str | None = Field(
        default=None,
        description="Transcript id where claim was resolved",
    )


class ClaimWithResolution(ClaimMade):
    resolution: ClaimResolution = Field(default_factory=ClaimResolution)


class ThreadEvolutionStep(BaseModel):
    claim_id: str
    date: date
    target_value: str
    hedge_level: CorpusHedgeLevel
    status_after_this_utterance: CorpusResolutionStatus


class ThreadTraceEvent(BaseModel):
    """Full audit trail: when said, when status changed, by which transcript."""

    event: Literal["uttered", "status_changed"]
    claim_id: str
    date: date
    transcript_id: str
    speaker: str | None = None
    status: CorpusResolutionStatus
    target_value: str = ""
    hedge_level: CorpusHedgeLevel = "soft"
    evidence_quote: str | None = None
    resolution_notes: str | None = None
    resolved_by_claim_ids: list[str] = Field(default_factory=list)


class ClaimThread(BaseModel):
    thread_id: str
    subject: str
    category: str
    n_claims: int = 0
    first_date: date | None = None
    last_date: date | None = None
    first_said_date: date | None = Field(
        default=None,
        description="Earliest claim utterance in thread",
    )
    resolved_at_date: date | None = Field(
        default=None,
        description="When thread reached final non-open status (last trace event)",
    )
    resolved_at_transcript: str | None = None
    final_status: CorpusResolutionStatus = "open"
    claim_ids: list[str] = Field(default_factory=list)
    evolution: list[ThreadEvolutionStep] = Field(default_factory=list)
    trace: list[ThreadTraceEvent] = Field(
        default_factory=list,
        description="Chronological uttered + resolution events for all claims in thread",
    )


class ClaimsMadeFile(BaseModel):
    schema_version: str = "1.0"
    corpus: str = ""
    total_claims: int = 0
    claims: list[ClaimMade] = Field(default_factory=list)


class ClaimsThreadsFile(BaseModel):
    schema_version: str = "1.0"
    corpus: str = ""
    total_threads: int = 0
    threads: list[ClaimThread] = Field(default_factory=list)


class ClaimsWithResolutionsFile(BaseModel):
    schema_version: str = "1.0"
    corpus: str = ""
    resolution_scope: str = "in-corpus only"
    total_claims: int = 0
    claims: list[ClaimWithResolution] = Field(default_factory=list)
