from __future__ import annotations

import json
import logging
from typing import NamedTuple

from app.data.article_store import update_article_status
from app.ingest.cleaning import chunk, has_enough_content, is_usable_title
from app.rag.chroma_client import get_collection
from app.rag.retrieval import get_embedder

log = logging.getLogger(__name__)


class IndexResult(NamedTuple):
    """Résumé d'un run d'indexation (les 3 compteurs d'articles + le total de chunks).

    indexed + held + errors == nombre d'articles reçus : aucun n'est « perdu ».
    """

    indexed: int  # articles réellement indexés dans Chroma
    held: int     # blockers left as 'ingested' (unusable title and/or thin content)
    errors: int   # articles passés en status='error' (exception pendant l'indexation)
    chunks: int   # total de chunks upsertés dans Chroma


def index_articles(articles: list[dict]) -> IndexResult:
    collection = get_collection()
    total_chunks = 0
    indexed = 0
    held = 0
    errors = 0

    for article in articles:
        reference = article.get("reference", "?")
        try:
            title = article.get("title", "") or ""
            content = article.get("content", "") or ""

            # Route, don't reject: only fully valid records (usable title AND enough
            # content) get indexed. Anything else is held as 'ingested' — a blocker for
            # the review pass to recover (real title via scrape, content via summary) or
            # to terminally reject. The indexer never scrapes, calls the LLM, or rejects.
            if not (is_usable_title(title) and has_enough_content(content)):
                held += 1
                continue

            chunks = chunk(content)

            # tags/keywords sont stockés en JSON dans SQLite → on les relit puis on
            # ré-encode en chaîne ", " (Chroma n'accepte pas de liste en métadonnée;
            # llm._split_tags redécoupe sur la virgule).
            tags_str = ", ".join(json.loads(article.get("tags", "[]")))
            keywords_str = ", ".join(json.loads(article.get("keywords", "[]")))
            metadata = {
                # `reference` permet de retrouver tous les chunks d'un article
                # (collection.get(where={"reference": ...})) pour patcher leur
                # métadonnée sans re-vectoriser.
                "reference": reference,
                "title": article.get("title", ""),
                "source": article.get("source", ""),
                "date": article.get("published_date") or "",
                "url": article.get("url", ""),
                "tags": tags_str,
                "keywords": keywords_str,
            }

            ids = [f"{reference}::{i}" for i in range(len(chunks))]

            # Prefix the title to each chunk before embedding so the article's subject is
            # part of every vector — including middle chunks that never restate it. Only
            # the EMBEDDED text carries the title; the stored document stays the raw chunk,
            # so the retrieval snippet is not polluted by a repeated title.
            embed_texts = [f"{title}\n\n{c}" for c in chunks]
            vecs = get_embedder().encode(embed_texts, normalize_embeddings=True)
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

    return IndexResult(indexed=indexed, held=held, errors=errors, chunks=total_chunks)


def patch_article_metadata(reference: str, tags: list[str], keywords: list[str]) -> int:
    """
    Rafraîchit tags/keywords dans la métadonnée des chunks déjà indexés d'un article.

    Sert quand l'agent de review annote un article DÉJÀ indexé : seuls tags/keywords
    changent, le texte embedé est inchangé. On met donc à jour la seule métadonnée
    (collection.update) sans re-découper ni re-vectoriser — re-embed produirait les
    mêmes vecteurs.

    Les chunks de l'article sont retrouvés via la métadonnée `reference`. tags/keywords
    sont ré-encodés en chaîne ", " comme à l'indexation (Chroma n'accepte pas de liste
    en métadonnée). Les autres champs (title, source, date, url, reference) sont
    préservés.

    Retourne le nombre de chunks patchés (0 si l'article n'est pas encore indexé).
    """
    collection = get_collection()
    found = collection.get(where={"reference": reference})
    ids = found["ids"]
    if not ids:
        return 0

    tags_str = ", ".join(tags)
    keywords_str = ", ".join(keywords)
    metadatas = []
    for meta in found["metadatas"]:
        updated = dict(meta)
        updated["tags"] = tags_str
        updated["keywords"] = keywords_str
        metadatas.append(updated)

    collection.update(ids=ids, metadatas=metadatas)
    return len(ids)


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
            "  → %d indexés, %d bloqués (titre/contenu insuffisant), %d en erreur → %d chunks dans Chroma.",
            result.indexed,
            result.held,
            result.errors,
            result.chunks,
        )
