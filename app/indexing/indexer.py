from __future__ import annotations

import json
import logging

from app.data.article_store import update_article_status
from app.ingest.cleaning import chunk
from app.rag.chroma_client import get_collection
from app.rag.retrieval import get_embedder

log = logging.getLogger(__name__)


def index_articles(articles: list[dict]) -> int:
    collection = get_collection()
    total_chunks = 0

    for article in articles:
        reference = article.get("reference", "?")
        try:
            chunks = chunk(article.get("content", ""))
            if not chunks:
                continue

            tags_str = "|".join(json.loads(article.get("tags", "[]")))
            metadata = {
                "title": article.get("title", ""),
                "source": article.get("source", ""),
                "date": article.get("published_date") or "",
                "url": article.get("url", ""),
                "tags": tags_str,
            }

            ids = [f"{reference}::{i}" for i in range(len(chunks))]
            vecs = get_embedder().encode(chunks, normalize_embeddings=True)
            embeddings = [v.tolist() for v in vecs]
            metadatas = [metadata] * len(chunks)

            collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
            update_article_status(reference, "indexed")
            total_chunks += len(chunks)

        except Exception:
            log.warning("Échec indexation article %s", reference)
            update_article_status(reference, "error")

    return total_chunks
