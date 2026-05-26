from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class EventType(str, Enum):
    EARNINGS_CALL = "earnings_call"
    ANALYST_MEETING = "analyst_meeting"
    AGM = "agm"
    ESG_MEETING = "esg_meeting"
    EXTRAORDINARY_MEETING = "extraordinary_meeting"
    UNKNOWN = "unknown"


class Section(str, Enum):
    MGMT_DISCUSSION = "mgmt_discussion"
    QA = "qa"
    UNKNOWN = "unknown"


class ClaimType(str, Enum):
    FINANCIAL_GUIDANCE = "financial_guidance"
    CAPEX_CAPACITY = "capex_capacity"
    MARKET_OUTLOOK = "market_outlook"
    STRATEGIC_INTENT = "strategic_intent"
    SUSTAINABILITY = "sustainability"
    OPERATIONAL = "operational"


class ClaimSubtype(str, Enum):
    QUANTITATIVE = "quantitative"
    QUALITATIVE = "qualitative"


class HedgingLevel(str, Enum):
    HARD = "hard"                   # "We will / We commit to"
    SOFT = "soft"                   # "We expect / We anticipate / We target"
    CONDITIONAL = "conditional"     # "Assuming X, we will..."
    ASPIRATIONAL = "aspirational"   # "We aim / We hope"
    UNFALSIFIABLE = "unfalsifiable" # "We believe in our strategy"


class ClaimStatus(str, Enum):
    PENDING = "pending"
    MATERIALIZED = "materialized"
    PARTIAL = "partial"
    NOT_MATERIALIZED = "not_materialized"
    REVISED = "revised"
    UNRESOLVABLE = "unresolvable"


# ---------------------------------------------------------------------------
# Ingestion models
# ---------------------------------------------------------------------------

class TranscriptMetadata(BaseModel):
    filename: str
    transcript_date: date
    event_type: EventType
    quarter: Optional[str] = None       # "Q1", "Q2", "Q3", "Q4"
    year: int
    company: str = "ROCKWOOL"


class SpeakerTurn(BaseModel):
    speaker_name: str
    speaker_role: str
    is_management: bool
    text: str
    section: Section
    chunk_index: int


class ParsedTranscript(BaseModel):
    metadata: TranscriptMetadata
    speaker_turns: list[SpeakerTurn]
    mgmt_discussion_text: str           # full concatenated management section
    qa_text: str                        # full concatenated Q&A section
    total_pages: Optional[int] = None


# ---------------------------------------------------------------------------
# Claim models
# ---------------------------------------------------------------------------

class QuantitativeFields(BaseModel):
    metric: str                         # "EBIT margin", "revenue growth"
    value: float
    unit: str                           # "%", "EUR million", "tonnes"
    direction: str                      # "target", "above", "below", "around"


class Claim(BaseModel):
    id: str                             # UUID
    transcript_id: str
    transcript_date: date
    speaker_name: str
    speaker_role: str
    original_text: str                  # verbatim quote
    normalized_claim: str              # clean one-sentence form
    claim_type: ClaimType
    claim_subtype: ClaimSubtype
    hedging_level: HedgingLevel
    time_horizon: str                   # "FY2025", "H2 2022", "2030", "near-term"
    status: ClaimStatus = ClaimStatus.PENDING
    quantitative: Optional[QuantitativeFields] = None
    successor_claim_id: Optional[str] = None  # set when status=REVISED


