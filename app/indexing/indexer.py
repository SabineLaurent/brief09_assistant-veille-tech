from __future__ import annotations

import json
import logging

from app.data.article_store import update_article_status
from app.ingest.cleaning import chunk
from app.rag.chroma_client import get_collection
from app.rag.retrieval import embed

log = logging.getLogger(__name__)


def index_articles(articles: list[dict]) -> int:
    collection = get_collection()
    total_chunks = 0

    for article in articles:
        try:
            chunks = chunk(article["content"])
            ids, docs, embeddings, metas = [], [], [], []

            for i, chunk_text in enumerate(chunks):
                ids.append(f"{article['reference']}::{i}")
                docs.append(chunk_text)
                embeddings.append(embed(chunk_text))
                metas.append({
                    "title": article["title"],
                    "source": article["source"],
                    "date": article["published_date"] or "",
                    "url": article["url"],
                    "tags": "|".join(json.loads(article["tags"])),
                })

            collection.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
            update_article_status(article["reference"], "indexed")
            total_chunks += len(chunks)

        except Exception:
            log.warning("Échec indexation article %s", article["reference"])
            update_article_status(article["reference"], "error")

    return total_chunks
