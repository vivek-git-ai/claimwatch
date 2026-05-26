"""Pass 2: resolve open claims using a new transcript (walk-forward)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.agents.claim_extractor import _format_transcript_text
from src.llm.azure import chat_structured, get_extraction_deployment
from src.models.corpus import ClaimMade, CorpusResolutionStatus
from src.models.schema import ParsedTranscript


class ResolutionUpdateItem(BaseModel):
    claim_id: str
    status: CorpusResolutionStatus = Field(
        description="confirmed|revised|failed|partial|open|unresolvable|stale|n/a"
    )
    resolved_by_claim_id: str | None = Field(
        default=None,
        description="Must be one of NEW_CLAIMS_THIS_STEP ids when revision happens in this transcript",
    )
    evidence_quote: str = Field(description="Verbatim quote from new transcript supporting verdict")
    notes: str | None = None


class ResolutionBatch(BaseModel):
    updates: list[ResolutionUpdateItem] = Field(default_factory=list)


RESOLVER_PROMPT = """You judge whether a NEW transcript resolves PRIOR open forward-looking claims.

Walk-forward rules:
- Use ONLY the new transcript text and the claim lists provided. No outside knowledge.
- You do NOT create new claims. You only update status of OPEN_CLAIMS_FROM_BEFORE.

Status meanings:
- confirmed: prior claim materially held or later evidence shows the soft expectation came true
  (e.g. Q1 "suspect share gain" + Q2 past-tense evidence of share gain → confirmed, NOT left open)
- revised: target/timeline changed; set resolved_by_claim_id to the matching id in NEW_CLAIMS_THIS_STEP
- failed: contradicted, abandoned, or clearly not happening
- partial: partly met or materially weakened
- open: no relevant new evidence in this transcript
- unresolvable: was never testable
- stale/n/a: do not assign unless instructed

resolved_by_claim_id:
- REQUIRED when status=revised and the revision utterance appears in NEW_CLAIMS_THIS_STEP
- Pick the exact claim_id from NEW_CLAIMS_THIS_STEP (not invented ids)
- For confirmed without a new claim row, leave resolved_by null

Quote evidence verbatim from the new transcript."""


_MAX_TRANSCRIPT_CHARS = 90_000


def _format_new_claims_block(new_claims: list[ClaimMade]) -> str:
    if not new_claims:
        return "NEW_CLAIMS_THIS_STEP: (none)"
    lines = ["NEW_CLAIMS_THIS_STEP (Pass 1 on this transcript — use these ids for resolved_by):"]
    for c in new_claims:
        lines.append(
            f"- {c.claim_id} | thread={c.thread_id} | {c.subject} | target={c.target_value} | "
            f"hedge={c.hedge_level} | {c.paraphrase[:100]}"
        )
    return "\n".join(lines)


def resolve_open_claims(
    open_claims: list[ClaimMade],
    parsed: ParsedTranscript,
    transcript_id: str,
    *,
    new_claims: list[ClaimMade] | None = None,
) -> tuple[list[ResolutionUpdateItem], dict]:
    """Check open claims from before this transcript against text of this transcript."""
    if not open_claims:
        return [], {"model": get_extraction_deployment(), "tokens_in": 0, "tokens_out": 0}

    new_claims = new_claims or []
    new_ids = {c.claim_id for c in new_claims}

    text = _format_transcript_text(parsed, include_qa=True)
    if len(text) > _MAX_TRANSCRIPT_CHARS:
        text = text[:_MAX_TRANSCRIPT_CHARS] + "\n\n[... transcript truncated for resolver ...]"

    claims_block = "\n".join(
        f"- {c.claim_id} | thread={c.thread_id} | {c.date_made} | {c.subject} | "
        f"target={c.target_value} | hedge={c.hedge_level} | {c.paraphrase[:120]}"
        for c in open_claims
    )

    user_prompt = f"""New transcript: {transcript_id}  date={parsed.metadata.transcript_date}

OPEN_CLAIMS_FROM_BEFORE (only update these):
{claims_block}

{_format_new_claims_block(new_claims)}

NEW TRANSCRIPT TEXT:
{text}
"""

    batch, usage = chat_structured(
        messages=[
            {"role": "system", "content": RESOLVER_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_model=ResolutionBatch,
        deployment=get_extraction_deployment(),
    )

    # Validate resolved_by points to a new claim from this step
    valid_updates: list[ResolutionUpdateItem] = []
    for u in batch.updates:
        if u.resolved_by_claim_id and u.resolved_by_claim_id not in new_ids:
            u = u.model_copy(update={"resolved_by_claim_id": None, "notes": (u.notes or "") + " [resolved_by cleared: id not in NEW_CLAIMS_THIS_STEP]"})
        valid_updates.append(u)

    usage["open_claims_sent"] = len(open_claims)
    usage["new_claims_sent"] = len(new_claims)
    return valid_updates, usage
