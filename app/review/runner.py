from __future__ import annotations

import logging
import sys
from typing import NamedTuple

from app.data.article_store import (
    read_articles_missing_content,
    read_unreviewed_articles,
    update_article_records_with_llm_reviews,
)
from app.indexing.indexer import patch_article_metadata
from app.review.reviewer import review_article

log = logging.getLogger(__name__)


class ReviewRunResult(NamedTuple):
    """Summary of a review pass.

    reviewed + skipped == number of articles fed to the pass.
    """

    reviewed: int        # articles annotated and persisted (llm_reviewed_at set)
    skipped: int         # articles left NULL (agent not configured / call failed) → retried later
    patched_chunks: int  # chunks whose Chroma metadata was refreshed


def _review_and_persist(articles: list[dict]) -> ReviewRunResult:
    """
    Run the review agent over the given articles and persist the results.

    For each article: one LLM call, SQLite persistence, and a Chroma metadata refresh
    when relevant. A failed review leaves llm_reviewed_at NULL so the article is retried
    on a later pass; the loop never aborts on a single failure.
    """
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

        # When a summary was generated, the content changed: the article was skipped at
        # indexing (still status='ingested') and will be indexed fresh on the next
        # `make index` — no patch needed. Otherwise the content is unchanged and already
        # indexed: only refresh its metadata (tags/keywords).
        if result.generated_summary is None:
            try:
                patched_chunks += patch_article_metadata(
                    reference, result.tags, result.keywords
                )
            except Exception as exc:  # Chroma unreachable: do not abort the pass
                log.warning("Metadata patch failed for %s — %s", reference, exc)

    return ReviewRunResult(reviewed=reviewed, skipped=skipped, patched_chunks=patched_chunks)


def run_review(limit: int | None = None) -> ReviewRunResult:
    """
    Review every not-yet-processed article (llm_reviewed_at IS NULL).

    `limit` caps the number of articles processed (None = all) — handy to run a
    validation batch before the full pass.
    """
    articles = read_unreviewed_articles()
    if limit is not None:
        articles = articles[:limit]
    return _review_and_persist(articles)


def run_missing_content_review(limit: int | None = None) -> ReviewRunResult:
    """
    Review only the records that have no content yet (the most harmful ones).

    With empty content, chunk("") returns [] and the indexer skips the article, so it
    never reaches the vector store. This pass scrapes the source URL, generates a
    summary that populates `content`, and annotates keywords/topics. These articles stay
    status='ingested' and are indexed fresh on the next `make index`.

    `limit` caps the number of articles processed (None = all).
    """
    articles = read_articles_missing_content()
    if limit is not None:
        articles = articles[:limit]
    return _review_and_persist(articles)


if __name__ == "__main__":
    """
    Standalone entry point for a review pass:
        CHROMA_URL=http://localhost:8002 uv run python -m app.review.runner
        CHROMA_URL=http://localhost:8002 uv run python -m app.review.runner missing

     - default: review every unreviewed article (llm_reviewed_at IS NULL)
     - `missing`: review only the content-less records (scrape + summary)

    Requires the agent config (azure_ai_mini_agent_*); otherwise every article is
    "skipped" and retried later.

    CHROMA_URL is needed from the host: .env points at "chromadb:8000" (Docker service
    name), unreachable from the terminal where Chroma is published on localhost:8002.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if "missing" in sys.argv[1:]:
        result = run_missing_content_review()
        log.info(
            "  → %d content-less records completed, %d skipped (retried later).",
            result.reviewed,
            result.skipped,
        )
    else:
        result = run_review()
        log.info(
            "  → %d annotated, %d skipped (retried later) → %d chunks patched in Chroma.",
            result.reviewed,
            result.skipped,
            result.patched_chunks,
        )
