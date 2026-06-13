from __future__ import annotations

import json
import logging
from typing import NamedTuple

from app.data.article_store import update_article_status
from app.ingest.cleaning import chunk
from app.rag.chroma_client import get_collection
from app.rag.retrieval import get_embedder

log = logging.getLogger(__name__)


class IndexResult(NamedTuple):
    """Résumé d'un run d'indexation (les 3 compteurs d'articles + le total de chunks).

    indexed + skipped + errors == nombre d'articles reçus : aucun n'est « perdu ».
    """

    indexed: int  # articles réellement indexés dans Chroma
    skipped: int  # articles sautés faute de contenu (chunk vide) → restent 'ingested'
    errors: int   # articles passés en status='error' (exception pendant l'indexation)
    chunks: int   # total de chunks upsertés dans Chroma


def index_articles(articles: list[dict]) -> IndexResult:
    collection = get_collection()
    total_chunks = 0
    indexed = 0
    skipped = 0
    errors = 0

    for article in articles:
        reference = article.get("reference", "?")
        try:
            chunks = chunk(article.get("content", ""))
            if not chunks:
                skipped += 1
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
            indexed += 1

        except Exception:
            log.warning("Échec indexation article %s", reference)
            update_article_status(reference, "error")
            errors += 1

    return IndexResult(indexed=indexed, skipped=skipped, errors=errors, chunks=total_chunks)


if __name__ == "__main__":
    """
    Point d'entrée autonome pour lancer l'indexation directement :
        CHROMA_URL=http://localhost:8002 uv run python -m app.indexing.indexer

     - lit les articles SQLite en status='ingested'
     - les découpe + embede + upsert dans Chroma (index_articles)
     - passe chaque article à status='indexed' (ou 'error' en cas d'échec)

    Même traitement que `make index`, sans passer par la CLI Typer.

    Le CHROMA_URL est nécessaire depuis l'hôte : le .env pointe sur "chromadb:8000"
    (nom de service Docker, valable entre conteneurs), injoignable depuis le terminal —
    où Chroma est publié sur localhost:8002. `make index` applique déjà cette surcharge.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.data.article_store import read_ingested_articles

    articles = read_ingested_articles()
    if not articles:
        log.info("Aucun article à indexer (status='ingested' introuvable).")
    else:
        result = index_articles(articles)
        log.info(
            "  → %d indexés, %d sautés (contenu vide), %d en erreur → %d chunks dans Chroma.",
            result.indexed,
            result.skipped,
            result.errors,
            result.chunks,
        )
