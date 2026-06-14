from __future__ import annotations

import logging
from typing import NamedTuple

from app.data.article_store import (
    read_unreviewed_articles,
    update_article_records_with_llm_reviews,
)
from app.indexing.indexer import patch_article_metadata
from app.review.reviewer import review_article

log = logging.getLogger(__name__)


class ReviewRunResult(NamedTuple):
    """Résumé d'une passe de review.

    reviewed + skipped == nombre d'articles non encore traités au début de la passe.
    """

    reviewed: int        # articles annotés et persistés (llm_reviewed_at renseigné)
    skipped: int         # articles laissés NULL (agent non configuré / appel en échec) → repris plus tard
    patched_chunks: int  # chunks dont la métadonnée Chroma a été rafraîchie


def run_review(limit: int | None = None) -> ReviewRunResult:
    """
    Annote les articles non encore traités : un appel LLM par article, persistance
    SQLite, et rafraîchissement de la métadonnée Chroma quand c'est pertinent.

    `limit` borne le nombre d'articles traités dans la passe (None = tous) — utile
    pour lancer un lot de validation avant la passe complète.

    Stratégie de reprise : un article dont la review échoue n'est pas marqué
    (llm_reviewed_at reste NULL) et sera retenté à la passe suivante. La passe ne
    s'interrompt jamais sur l'échec d'un article.
    """
    articles = read_unreviewed_articles()
    if limit is not None:
        articles = articles[:limit]
    reviewed = 0
    skipped = 0
    patched_chunks = 0

    for article in articles:
        reference = article["reference"]
        result = review_article(article)
        if result is None:
            skipped += 1
            continue

        update_article_records_with_llm_reviews(
            reference=reference,
            keywords=result.keywords,
            tags=result.tags,
            generated_summary=result.generated_summary,
        )
        reviewed += 1

        # Si un résumé a été généré, le content a changé : l'article était sauté à
        # l'indexation (encore en status='ingested') et sera indexé à neuf au prochain
        # `make index` — inutile de patcher. Sinon le content est inchangé et déjà
        # indexé : on rafraîchit seulement sa métadonnée (tags/keywords).
        if result.generated_summary is None:
            try:
                patched_chunks += patch_article_metadata(
                    reference, result.tags, result.keywords
                )
            except Exception as exc:  # Chroma indisponible : ne pas avorter la passe
                log.warning("Metadata patch failed for %s — %s", reference, exc)

    return ReviewRunResult(reviewed=reviewed, skipped=skipped, patched_chunks=patched_chunks)


if __name__ == "__main__":
    """
    Point d'entrée autonome pour lancer une passe de review :
        CHROMA_URL=http://localhost:8002 uv run python -m app.review.runner

     - lit les articles non encore traités (llm_reviewed_at IS NULL)
     - annote chacun via l'agent (keywords + topics, + résumé si content vide)
     - persiste en SQLite et rafraîchit la métadonnée Chroma des articles déjà indexés

    Nécessite la config de l'agent (azure_ai_mini_agent_*) ; sinon tous les articles
    sont « sautés » et repris plus tard.

    Le CHROMA_URL est nécessaire depuis l'hôte : le .env pointe sur "chromadb:8000"
    (nom de service Docker), injoignable depuis le terminal où Chroma est publié sur
    localhost:8002.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    result = run_review()
    log.info(
        "  → %d annotés, %d sautés (repris plus tard) → %d chunks patchés dans Chroma.",
        result.reviewed,
        result.skipped,
        result.patched_chunks,
    )
