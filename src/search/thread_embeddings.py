"""Semantic search over claim threads — Weaviate Cloud (Ask v1)."""

from __future__ import annotations

import re

from src.analytics.corpus_loader import CorpusBundle
from src.models.corpus import ClaimThread
from src.search import weaviate_threads
from src.search.models import ThreadSearchHit, thread_search_text


def _tokenize_query(query: str) -> list[str]:
    return [t for t in re.split(r"\W+", query.lower()) if len(t) >= 3]


def _substring_search_threads(
    bundle: CorpusBundle,
    query: str,
    *,
    k: int = 5,
) -> list[ThreadSearchHit]:
    """Fallback when Weaviate is not configured."""
    tokens = _tokenize_query(query)
    if not tokens:
        return []
    scored: list[tuple[float, ClaimThread]] = []
    for thread in bundle.thread_by_id.values():
        hay = thread_search_text(thread, bundle).lower()
        hits = sum(1 for t in tokens if t in hay)
        if hits:
            scored.append((hits / len(tokens), thread))
    scored.sort(key=lambda x: (-x[0], x[1].subject.lower()))
    return [
        ThreadSearchHit(thread_id=t.thread_id, subject=t.subject, score=score)
        for score, t in scored[:k]
    ]


class ThreadEmbeddingIndex:
    """Weaviate-backed thread index (no in-memory vectors)."""

    def __init__(self, bundle: CorpusBundle) -> None:
        self.bundle = bundle

    def ensure_built(self) -> None:
        pass

    def search(self, query: str, *, k: int = 5) -> list[ThreadSearchHit]:
        if weaviate_threads.weaviate_configured():
            return weaviate_threads.search_threads_weaviate(query, k=k)
        return _substring_search_threads(self.bundle, query, k=k)


def semantic_search_threads(
    bundle: CorpusBundle,
    query: str,
    *,
    k: int = 5,
    index: ThreadEmbeddingIndex | None = None,
) -> list[ThreadSearchHit]:
    idx = index or ThreadEmbeddingIndex(bundle)
    return idx.search(query, k=k)
