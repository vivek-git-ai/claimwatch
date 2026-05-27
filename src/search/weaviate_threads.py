"""Weaviate Cloud storage for Ask v1 thread semantic search."""

from __future__ import annotations

import os
import uuid
import weaviate
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import MetadataQuery

from src.analytics.corpus_loader import CorpusBundle
from src.llm.azure import embed_texts, get_embedding_dimensions
from src.models.corpus import ClaimThread
from src.search.models import ThreadSearchHit, thread_search_text

COLLECTION_NAME = "ClaimThread"

# Legacy collections from earlier experiments — removed on --reset-all
LEGACY_COLLECTIONS = (
    "Claim",
    "TranscriptChunk",
    "Transcript",
    "ResolutionEvent",
    "CostLog",
    "ClaimCorpus",
    "CorpusMeta",
)


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def weaviate_configured() -> bool:
    return bool(os.getenv("WEAVIATE_URL", "").strip() and os.getenv("WEAVIATE_API_KEY", "").strip())


def weaviate_host_label() -> str:
    url = os.getenv("WEAVIATE_URL", "").strip()
    if not url:
        return ""
    return url.replace("https://", "").replace("http://", "").split("/")[0]


def get_status() -> dict:
    """Connection/index snapshot for dashboard (no secrets)."""
    if not weaviate_configured():
        return {
            "configured": False,
            "connected": False,
            "host": "",
            "collections": [],
            "thread_count": 0,
            "error": None,
        }
    try:
        collections = list_collections()
        count = thread_count()
        return {
            "configured": True,
            "connected": True,
            "host": weaviate_host_label(),
            "collections": collections,
            "thread_count": count,
            "error": None,
        }
    except Exception as e:
        return {
            "configured": True,
            "connected": False,
            "host": weaviate_host_label(),
            "collections": [],
            "thread_count": 0,
            "error": str(e),
        }


def get_weaviate_client() -> weaviate.WeaviateClient:
    url = _env("WEAVIATE_URL")
    api_key = _env("WEAVIATE_API_KEY")
    if not url.startswith("http"):
        url = f"https://{url}"
    return weaviate.connect_to_weaviate_cloud(
        cluster_url=url,
        auth_credentials=weaviate.auth.AuthApiKey(api_key),
    )


def list_collections() -> list[str]:
    client = get_weaviate_client()
    try:
        return list(client.collections.list_all().keys())
    finally:
        client.close()


def _thread_uuid(thread_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"claimwatch:{COLLECTION_NAME}:{thread_id}")


def delete_collection(name: str) -> bool:
    client = get_weaviate_client()
    try:
        if not client.collections.exists(name):
            return False
        client.collections.delete(name)
        return True
    finally:
        client.close()


def clean_ask_collections(*, include_legacy: bool = True) -> list[str]:
    """Delete ClaimThread and optionally legacy collections. Returns names deleted."""
    names = [COLLECTION_NAME]
    if include_legacy:
        names = list(dict.fromkeys([*names, *LEGACY_COLLECTIONS]))
    deleted: list[str] = []
    for name in names:
        if delete_collection(name):
            deleted.append(name)
    return deleted


def ensure_thread_collection() -> None:
    client = get_weaviate_client()
    try:
        if client.collections.exists(COLLECTION_NAME):
            return
        _ = get_embedding_dimensions()
        client.collections.create(
            name=COLLECTION_NAME,
            vectorizer_config=Configure.Vectorizer.none(),
            vector_index_config=Configure.VectorIndex.hnsw(
                distance_metric=weaviate.classes.config.VectorDistances.COSINE
            ),
            properties=[
                Property(name="thread_id", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="subject", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="category", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="search_text", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="final_status", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="n_claims", data_type=DataType.INT, skip_vectorization=True),
            ],
        )
    finally:
        client.close()


def thread_count() -> int:
    client = get_weaviate_client()
    try:
        if not client.collections.exists(COLLECTION_NAME):
            return 0
        coll = client.collections.get(COLLECTION_NAME)
        agg = coll.aggregate.over_all(total_count=True)
        return int(agg.total_count or 0)
    finally:
        client.close()


def sync_threads_to_weaviate(
    bundle: CorpusBundle,
    *,
    reset: bool = False,
    include_legacy_clean: bool = False,
) -> int:
    """
    Embed all threads (Azure) and upsert into Weaviate.
    If reset=True, delete ClaimThread (and legacy if include_legacy_clean) first.
  """
    if reset:
        clean_ask_collections(include_legacy=include_legacy_clean)

    ensure_thread_collection()
    threads = sorted(bundle.thread_by_id.values(), key=lambda t: t.thread_id)
    if not threads:
        return 0

    texts = [thread_search_text(t, bundle) for t in threads]
    vectors = embed_texts(texts)

    client = get_weaviate_client()
    try:
        coll = client.collections.get(COLLECTION_NAME)
        with coll.batch.dynamic() as batch:
            for thread, text, vector in zip(threads, texts, vectors, strict=True):
                batch.add_object(
                    properties={
                        "thread_id": thread.thread_id,
                        "subject": thread.subject,
                        "category": thread.category,
                        "search_text": text,
                        "final_status": thread.final_status,
                        "n_claims": thread.n_claims,
                    },
                    vector=vector,
                    uuid=_thread_uuid(thread.thread_id),
                )
    finally:
        client.close()

    return len(threads)


def search_threads_weaviate(
    query: str,
    *,
    k: int = 5,
) -> list[ThreadSearchHit]:
    """Near-vector search on ClaimThread collection."""
    if not weaviate_configured():
        raise RuntimeError("WEAVIATE_URL and WEAVIATE_API_KEY must be set")

    ensure_thread_collection()
    count = thread_count()
    if count == 0:
        raise RuntimeError(
            "Weaviate ClaimThread collection is empty. "
            "Run: uv run python -m src.main index-threads --reset"
        )

    query_vector = embed_texts([query])[0]
    client = get_weaviate_client()
    try:
        coll = client.collections.get(COLLECTION_NAME)
        response = coll.query.near_vector(
            near_vector=query_vector,
            limit=k,
            return_metadata=MetadataQuery(distance=True),
        )
        hits: list[ThreadSearchHit] = []
        for obj in response.objects:
            props = obj.properties or {}
            dist = obj.metadata.distance if obj.metadata else None
            score = 1.0 - float(dist) if dist is not None else 0.0
            hits.append(
                ThreadSearchHit(
                    thread_id=str(props.get("thread_id", "")),
                    subject=str(props.get("subject", "")),
                    score=score,
                )
            )
        return hits
    finally:
        client.close()
