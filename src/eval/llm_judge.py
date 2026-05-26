"""LLM-as-judge for extraction and resolution eval (Azure GPT-4o).

Compares pipeline output in data/claims/ against your gold labels in data/eval/.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.llm.azure import chat_structured, get_extraction_deployment


class ExtractionVerdict(BaseModel):
    reproduces_gold: bool = Field(
        description="True if the prediction describes the same atomic claim as gold."
    )
    contextually_relevant: bool = Field(
        description="True if the prediction is a valid forward-looking ROCKWOOL management claim in context."
    )
    quote_supported: bool = Field(
        description="True if the predicted quote materially supports the predicted paraphrase."
    )
    reasoning: str = Field(description="One short sentence.")


EXTRACTION_JUDGE_SYSTEM = """You judge whether an extraction agent reproduced a gold-standard claim.

Gold was labelled independently from the pipeline. Judge the PREDICTED claim:

1. reproduces_gold — same atomic forward-looking claim as gold (subject, direction, timeframe).
   Numbers need not match exactly if the underlying claim is the same.
2. contextually_relevant — real forward-looking ROCKWOOL management guidance (not history,
   not analyst speech, not vague filler).
3. quote_supported — predicted quote supports the predicted paraphrase (no hallucination)."""


class ResolutionVerdict(BaseModel):
    reproduces_gold_status: bool = Field(
        description="True if predicted status matches gold or is a defensible alternative."
    )
    evidence_relevant: bool = Field(
        description="True if predicted evidence_quote supports the predicted status in context."
    )
    resolution_contextually_sound: bool = Field(
        description="True if the overall resolution verdict is reasonable for this claim."
    )
    reasoning: str = Field(description="One short sentence.")


RESOLUTION_JUDGE_SYSTEM = """You judge whether a resolution agent reproduced the gold resolution.

Gold expected status and evidence were labelled independently. Judge the PREDICTED resolution:

1. reproduces_gold_status — predicted status matches gold (confirmed/revised/failed/partial/open/
   unresolvable) or is defensible (e.g. partial vs confirmed when evidence is mixed).
2. evidence_relevant — predicted evidence_quote (if any) is real transcript text that supports
   the predicted status.
3. resolution_contextually_sound — taken together, the predicted status + evidence + timing
   is a reasonable resolution of the gold claim at the checkpoint."""


def judge_extraction_pair(
    gold: dict[str, Any],
    pred: dict[str, Any],
    *,
    deployment: str | None = None,
) -> ExtractionVerdict:
    user_prompt = f"""GOLD
quote: {gold.get('quote')}
paraphrase: {gold.get('paraphrase')}
speaker: {gold.get('speaker')}
subject: {gold.get('subject')}

PREDICTED
quote: {pred.get('quote')}
paraphrase: {pred.get('paraphrase')}
speaker: {pred.get('speaker')}
subject: {pred.get('subject')}"""
    verdict, _ = chat_structured(
        messages=[
            {"role": "system", "content": EXTRACTION_JUDGE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        response_model=ExtractionVerdict,
        deployment=deployment or get_extraction_deployment(),
    )
    return verdict


def judge_resolution_pair(
    gold_row: dict[str, Any],
    pred_row: dict[str, Any],
    *,
    deployment: str | None = None,
) -> ResolutionVerdict:
    pred_resolution = pred_row.get("resolution") or {}
    user_prompt = f"""GOLD CLAIM
paraphrase: {gold_row.get('paraphrase')}

GOLD RESOLUTION
status: {gold_row.get('expected_status')}
resolved_at: {gold_row.get('expected_resolved_at_transcript')}
evidence: {gold_row.get('expected_evidence_quote')}

PREDICTED RESOLUTION
status: {pred_resolution.get('status')}
resolved_at: {pred_resolution.get('resolved_at_transcript')}
evidence: {pred_resolution.get('evidence_quote')}"""
    verdict, _ = chat_structured(
        messages=[
            {"role": "system", "content": RESOLUTION_JUDGE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        response_model=ResolutionVerdict,
        deployment=deployment or get_extraction_deployment(),
    )
    return verdict
