"""Shared types for thread search."""

from __future__ import annotations

from dataclasses import dataclass

from src.analytics.corpus_loader import CorpusBundle
from src.models.corpus import ClaimThread


@dataclass(frozen=True)
class ThreadSearchHit:
    thread_id: str
    subject: str
    score: float


def thread_search_text(thread: ClaimThread, bundle: CorpusBundle) -> str:
    parts = [thread.subject, thread.category]
    for cid in thread.claim_ids:
        ec = bundle.claim_by_id.get(cid)
        if ec:
            parts.append(ec.claim.paraphrase)
            parts.append(ec.claim.quote[:300])
    return "\n".join(parts)
