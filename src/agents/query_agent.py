"""Ask v1 — semantic thread search + structured answer with citations."""

from __future__ import annotations

import json
from pydantic import BaseModel, Field

from src.analytics.corpus_loader import CorpusBundle
from src.llm.azure import chat_structured, get_extraction_deployment
from src.models.corpus import ClaimThread
from src.search.thread_embeddings import ThreadEmbeddingIndex, ThreadSearchHit, semantic_search_threads

SYSTEM_PROMPT = """You answer questions about how a management guidance thread evolved over time.

You receive structured thread data: subject, trace events (uttered / status_changed), and claim quotes.

Rules:
- Write a concise chronological narrative (3–8 sentences).
- Populate citations only for claim_ids present in the thread data.
- Use verbatim quote text from the data for each citation.quote.
- Use evidence_quote when a status_changed event provides it.
- Do not invent claim_ids, dates, or quotes.
- If the thread data cannot answer the question, say so briefly in the narrative and return fewer citations.
"""


class CitationRecord(BaseModel):
    claim_id: str
    thread_id: str
    date: str
    speaker: str
    status: str
    quote: str
    evidence_quote: str | None = None


class AskAnswer(BaseModel):
    narrative: str
    citations: list[CitationRecord] = Field(default_factory=list)
    thread_id: str


def get_thread(bundle: CorpusBundle, thread_id: str) -> ClaimThread | None:
    return bundle.thread_by_id.get(thread_id)


def get_claim_summary(bundle: CorpusBundle, claim_id: str) -> dict | None:
    ec = bundle.claim_by_id.get(claim_id)
    if not ec:
        return None
    c = ec.claim
    return {
        "claim_id": c.claim_id,
        "thread_id": c.thread_id,
        "date_made": str(c.date_made),
        "speaker": c.speaker,
        "status": c.resolution.status,
        "quote": c.quote,
        "paraphrase": c.paraphrase,
        "evidence_quote": c.resolution.evidence_quote,
        "resolution_notes": c.resolution.resolution_notes,
    }


def format_thread_for_llm(thread: ClaimThread, bundle: CorpusBundle) -> str:
    """Serialize thread trace + claims for the synthesis prompt."""
    claims_block = []
    for cid in thread.claim_ids:
        row = get_claim_summary(bundle, cid)
        if row:
            claims_block.append(row)

    trace_block = []
    for ev in thread.trace or []:
        trace_block.append(
            {
                "event": ev.event,
                "claim_id": ev.claim_id,
                "date": str(ev.date),
                "transcript_id": ev.transcript_id,
                "speaker": ev.speaker,
                "status": ev.status,
                "target_value": ev.target_value,
                "evidence_quote": ev.evidence_quote,
                "resolution_notes": ev.resolution_notes,
                "resolved_by_claim_ids": ev.resolved_by_claim_ids,
            }
        )

    payload = {
        "thread_id": thread.thread_id,
        "subject": thread.subject,
        "category": thread.category,
        "final_status": thread.final_status,
        "first_date": str(thread.first_date) if thread.first_date else None,
        "last_date": str(thread.last_date) if thread.last_date else None,
        "claim_ids": thread.claim_ids,
        "claims": claims_block,
        "trace": trace_block,
    }
    return json.dumps(payload, indent=2)


def _enrich_citations(
    citations: list[CitationRecord],
    thread: ClaimThread,
    bundle: CorpusBundle,
) -> list[CitationRecord]:
    valid = set(thread.claim_ids)
    out: list[CitationRecord] = []
    seen: set[str] = set()
    for cit in citations:
        if cit.claim_id not in valid or cit.claim_id in seen:
            continue
        seen.add(cit.claim_id)
        ec = bundle.claim_by_id.get(cit.claim_id)
        if not ec:
            continue
        c = ec.claim
        out.append(
            CitationRecord(
                claim_id=c.claim_id,
                thread_id=thread.thread_id,
                date=str(c.date_made),
                speaker=c.speaker,
                status=c.resolution.status,
                quote=c.quote,
                evidence_quote=c.resolution.evidence_quote or cit.evidence_quote,
            )
        )
    return out


def answer_question(
    question: str,
    bundle: CorpusBundle,
    *,
    thread_id: str | None = None,
    index: ThreadEmbeddingIndex | None = None,
    search_k: int = 5,
) -> tuple[AskAnswer, list[ThreadSearchHit], dict]:
    """
    Answer a natural-language question about a guidance thread.

    Returns (answer, search_hits, usage_dict).
    """
    hits: list[ThreadSearchHit] = []
    if thread_id:
        thread = get_thread(bundle, thread_id)
        if not thread:
            raise ValueError(f"Unknown thread_id: {thread_id}")
    else:
        hits = semantic_search_threads(bundle, question, k=search_k, index=index)
        if not hits:
            raise ValueError("No matching threads found for this question.")
        thread_id = hits[0].thread_id
        thread = get_thread(bundle, thread_id)
        if not thread:
            raise ValueError(f"Unknown thread_id: {thread_id}")

    context = format_thread_for_llm(thread, bundle)
    user_prompt = f"""Question: {question}

Thread data (JSON):
{context}
"""

    parsed, usage = chat_structured(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_model=AskAnswer,
        deployment=get_extraction_deployment(),
    )

    parsed.thread_id = thread.thread_id
    parsed.citations = _enrich_citations(parsed.citations, thread, bundle)
    usage["thread_id"] = thread.thread_id
    return parsed, hits, usage
