"""Extract forward-looking management claims (mgmt + Q&A)."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field

from src.agents.speaker_filter import is_management_turn
from src.llm.azure import chat_structured, get_extraction_deployment
from src.models.schema import (
    Claim,
    ClaimStatus,
    ClaimSubtype,
    ClaimType,
    HedgingLevel,
    ParsedTranscript,
    QuantitativeFields,
    Section,
)


class ExtractedClaimItem(BaseModel):
    original_text: str = Field(description="Verbatim quote from management speech")
    normalized_claim: str = Field(description="One clear sentence restating the forward-looking claim")
    claim_type: ClaimType
    claim_subtype: ClaimSubtype
    hedging_level: HedgingLevel
    time_horizon: str
    speaker_name: str
    source_section: Section = Field(description="mgmt_discussion or qa")
    subject: str = Field(description="Short label, e.g. FY2021 revenue growth")
    target_value: str = Field(description="Numeric or directional target as stated, e.g. 10-12%, ~€730m")
    falsifiable: str = Field(description="Y, partial, or N")
    thread_subject_hint: str | None = Field(
        default=None,
        description="If this restates prior guidance, same subject label as before",
    )
    metric: str | None = None
    value: float | None = None
    unit: str | None = None
    direction: str | None = None
    notes: str | None = None


class ExtractionBatch(BaseModel):
    claims: list[ExtractedClaimItem] = Field(default_factory=list)


SYSTEM_PROMPT = """You extract forward-looking claims made by ROCKWOOL management from earnings transcripts.

SCOPE: Management prepared remarks AND management answers in Q&A (exclude analyst questions).
Input includes section tags [speaker | role | mgmt_discussion|qa].

INCLUSION (each becomes its own atomic claim):
- Guidance, targets, expectations, commitments, plans, dated milestones
- Partially falsifiable outlooks (tag falsifiable=partial)
- Keep aspirational-but-untestable only if clearly flagged falsifiable=N

EXCLUDE:
- Pure past results / achievements with no forward test
- Generic macro color with no falsifiable hook
- "Thanks", handoffs, non-substantive filler
- Analyst questions (only extract management answers)

ATOMICITY: One utterance per claim. If one sentence has three numbers (revenue %, EBIT %, CapEx €), emit THREE claims.
If management reaffirms prior guidance in Q&A, still emit a new claim (dedup happens later via threads).

Q&A: Use analyst question as context but quote management words only. Tag source_section=qa when from Q&A.

Fields:
- subject: stable short label for threading (e.g. "FY2021 revenue growth", "Ranson WV factory startup")
- target_value: as stated ("10-12%", "~€730m", "production start Q3 2021")
- falsifiable: Y | partial | N
- hedging_level: hard=firm commitment/will; soft=expect/believe/suspect; conditional=assuming; aspirational=aim; unfalsifiable

Do not upgrade hedged language into firm commitments in normalized_claim.
original_text must be a verbatim substring from the provided text."""


def _format_transcript_text(parsed: ParsedTranscript, *, include_qa: bool = True) -> str:
    blocks = []
    for turn in parsed.speaker_turns:
        if not is_management_turn(turn):
            continue
        if not include_qa and turn.section != Section.MGMT_DISCUSSION:
            continue
        blocks.append(
            f"[{turn.speaker_name} | {turn.speaker_role} | {turn.section.value}]\n{turn.text}"
        )
    if blocks:
        return "\n\n---\n\n".join(blocks)
    if include_qa:
        return (parsed.mgmt_discussion_text + "\n\n" + parsed.qa_text).strip()
    return parsed.mgmt_discussion_text


def extract_claims_from_transcript(
    parsed: ParsedTranscript,
    transcript_id: str,
    *,
    include_qa: bool = True,
    open_threads: list[dict] | None = None,
) -> tuple[list[ExtractedClaimItem], dict]:
    """
    Pass 1 extraction for one transcript.
    open_threads: optional [{"thread_id","subject","last_target_value"}] for thread hints.
    """
    meta = parsed.metadata
    text = _format_transcript_text(parsed, include_qa=include_qa)

    thread_ctx = ""
    if open_threads:
        lines = [
            f"- {t.get('thread_id')}: {t.get('subject')} (last: {t.get('last_target_value', '')})"
            for t in open_threads[:40]
        ]
        thread_ctx = "\n\nOpen claim threads (reuse subject label when same topic):\n" + "\n".join(lines)

    user_prompt = f"""Transcript: {transcript_id}
Date: {meta.transcript_date}
Event: {meta.event_type.value}  Quarter: {meta.quarter or 'N/A'}  Year: {meta.year}
{thread_ctx}

Management speech (mgmt_discussion + qa):

{text}
"""

    batch, usage = chat_structured(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_model=ExtractionBatch,
    )

    usage["deployment"] = get_extraction_deployment()
    usage["claims_count"] = len(batch.claims)
    return batch.claims, usage


def extracted_items_to_claims(
    items: list[ExtractedClaimItem],
    transcript_id: str,
    transcript_date: date,
) -> list[Claim]:
    """Map extraction batch to schema Claim objects for data/stats/extractions export."""
    claims: list[Claim] = []
    for item in items:
        status = (
            ClaimStatus.UNRESOLVABLE
            if item.hedging_level == HedgingLevel.UNFALSIFIABLE
            else ClaimStatus.PENDING
        )
        quantitative = None
        if item.claim_subtype == ClaimSubtype.QUANTITATIVE and item.metric and item.value is not None:
            quantitative = QuantitativeFields(
                metric=item.metric,
                value=item.value,
                unit=item.unit or "",
                direction=item.direction or "target",
            )
        claims.append(
            Claim(
                id=str(uuid.uuid4()),
                transcript_id=transcript_id,
                transcript_date=transcript_date,
                speaker_name=item.speaker_name,
                speaker_role="",
                original_text=item.original_text,
                normalized_claim=item.normalized_claim,
                claim_type=item.claim_type,
                claim_subtype=item.claim_subtype,
                hedging_level=item.hedging_level,
                time_horizon=item.time_horizon,
                status=status,
                quantitative=quantitative,
            )
        )
    return claims
