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

            # tags/keywords sont stockés en JSON dans SQLite → on les relit puis on
            # ré-encode en chaîne ", " (Chroma n'accepte pas de liste en métadonnée ;
            # llm._split_tags redécoupe sur la virgule).
            tags_str = ", ".join(json.loads(article.get("tags", "[]")))
            keywords_str = ", ".join(json.loads(article.get("keywords", "[]")))
            metadata = {
                "title": article.get("title", ""),
                "source": article.get("source", ""),
                "date": article.get("published_date") or "",
                "url": article.get("url", ""),
                "tags": tags_str,
                "keywords": keywords_str,
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


if __name__ == "__main__":
    """
    Point d'entrée autonome pour lancer l'indexation directement :
        uv run python -m app.indexing.indexer

     - lit les articles SQLite en status='ingested'
     - les découpe + embede + upsert dans Chroma (index_articles)
     - passe chaque article à status='indexed' (ou 'error' en cas d'échec)

    Même traitement que `make index`, sans passer par la CLI Typer.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.data.article_store import read_ingested_articles

    articles = read_ingested_articles()
    if not articles:
        log.info("Aucun article à indexer (status='ingested' introuvable).")
    else:
        total_chunks = index_articles(articles)
        log.info("  → %d articles indexés → %d chunks dans Chroma.", len(articles), total_chunks)
