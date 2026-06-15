from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from sentence_transformers import SentenceTransformer

from app.config import get_settings
from app.rag.chroma_client import get_collection

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    settings = get_settings()
    return SentenceTransformer(settings.embedding_model)


def embed(text: str) -> list[float]:
    embedder = get_embedder()
    vec = embedder.encode([text], normalize_embeddings=True)
    return vec[0].tolist()


def retrieve(query: str, k: int = 8, oversample: int = 3) -> list[dict[str, Any]]:
    """
    Return up to `k` chunks for the query, at most one per article (by `reference`).

    Several chunks of the same article can rank in the top results and would otherwise
    produce duplicate cards. We oversample (`k * oversample` raw hits), then keep only the
    best chunk per article: Chroma returns hits by ascending distance, so the first
    occurrence of a reference is its closest chunk. Chunks without a `reference` in their
    metadata (legacy, pre-dedup index) are never merged.
    """
    try:
        collection = get_collection()
        query_vec = embed(query)
        result = collection.query(query_embeddings=[query_vec], n_results=k * oversample)
    except Exception as exc:
        logger.warning("retrieval failed: %s", exc)
        return []

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    ids = (result.get("ids") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    chunks: list[dict[str, Any]] = []
    seen_references: set[str] = set()
    for doc_id, doc, meta, dist in zip(ids, docs, metas, distances, strict=False):
        meta = meta or {}
        reference = meta.get("reference")
        if reference is not None:
            if reference in seen_references:
                continue
            seen_references.add(reference)
        chunks.append(
            {
                "id": doc_id,
                "content": doc,
                "metadata": meta,
                "distance": dist,
            }
        )
        if len(chunks) >= k:
            break
    return chunks
