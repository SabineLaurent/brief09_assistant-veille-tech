from __future__ import annotations

import logging
import sys
from typing import NamedTuple

from app.data.article_store import (
    mark_chroma_synced,
    read_unreviewed_articles,
    update_article_records_with_llm_reviews,
    update_article_status,
)
from app.indexing.indexer import patch_article_metadata
from app.ingest.cleaning import has_enough_content, is_usable_title
from app.review.article_reviewer import review_article

log = logging.getLogger(__name__)


class ReviewRunResult(NamedTuple):
    """Summary of a review pass.

    reviewed + rejected + skipped == number of articles fed to the pass.
    """

    reviewed: int        # articles annotated and persisted (llm_reviewed_at set)
    rejected: int        # articles terminally rejected (status='rejected')
    skipped: int         # left NULL (agent off / call failed / source unreachable) → retried later
    patched_chunks: int  # chunks whose Chroma metadata was refreshed


def _review_and_persist(articles: list[dict]) -> ReviewRunResult:
    """
    Run the review agent over the given articles and persist the results.

    For each article: one review (LLM call + optional scrape), then persistence. A
    rejected record gets the terminal status='rejected'; a transient failure (None) is
    left untouched so it is retried on a later pass. The loop never aborts on a single
    failure.
    """
    reviewed = 0
    rejected = 0
    skipped = 0
    patched_chunks = 0

    for article in articles:
        reference = article["reference"]
        result = review_article(article)
        if result is None:
            skipped += 1
            continue
        if result.rejected:
            update_article_status(reference, "rejected")
            rejected += 1
            continue

        update_article_records_with_llm_reviews(
            reference=reference,
            keywords=result.keywords,
            tags=result.tags,
            generated_summary=result.generated_summary,
            title=result.recovered_title,
        )
        reviewed += 1

        # Patch Chroma metadata only for a record that is ALREADY indexed — i.e. a valid
        # article merely being annotated (title and content unchanged). When a summary or
        # a title was recovered, the record was a held blocker (not yet indexed): it stays
        # 'ingested' and is indexed fresh on the next `make index`, so there is nothing to
        # patch.
        if result.generated_summary is None and result.recovered_title is None:
            try:
                n = patch_article_metadata(reference, result.tags, result.keywords)
                patched_chunks += n
                # Stamp the audit trail only when chunks were actually refreshed: a
                # not-yet-indexed record returns 0, so there is nothing synced to record.
                if n > 0:
                    mark_chroma_synced(reference)
            except Exception as exc:  # Chroma unreachable: do not abort the pass
                log.warning("Metadata patch failed for %s — %s", reference, exc)

    return ReviewRunResult(
        reviewed=reviewed, rejected=rejected, skipped=skipped, patched_chunks=patched_chunks
    )


def is_blocker(article: dict) -> bool:
    """
    Return True if the indexer cannot index this record as-is.

    A blocker has a junk title and/or thin content (the same gate the indexer applies).
    The blocking review pass tries to recover it from the source before indexing.
    """
    return not is_usable_title(article.get("title") or "") or not has_enough_content(
        article.get("content") or ""
    )


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


def run_blocking_review(limit: int | None = None) -> ReviewRunResult:
    """
    Review only the blockers: records the indexer holds back (junk title and/or thin
    content), so they can be indexed afterwards. Run BEFORE `make index`.

    The blocker test (is_blocker) is a Python filter — SQL cannot express the title
    check — over the unreviewed records. For each blocker the pass scrapes the source to
    recover the real title and/or a summary; records it cannot recover are terminally
    rejected. Recovered records stay status='ingested' and are indexed fresh next run.

    `limit` caps the number of articles processed (None = all).
    """
    articles = [a for a in read_unreviewed_articles() if is_blocker(a)]
    if limit is not None:
        articles = articles[:limit]
    return _review_and_persist(articles)


if __name__ == "__main__":
    """
    Standalone entry point for a review pass:
        CHROMA_URL=http://localhost:8002 uv run python -m app.review.review_orchestrator
        CHROMA_URL=http://localhost:8002 uv run python -m app.review.review_orchestrator blocking

     - default: review every unreviewed article (llm_reviewed_at IS NULL)
     - `blocking`: review only the blockers (junk title and/or thin content), to run
       BEFORE `make index`

    Requires the agent config (azure_ai_mini_agent_*); otherwise every article is
    "skipped" and retried later.

    CHROMA_URL is needed from the host: .env points at "chromadb:8000" (Docker service
    name), unreachable from the terminal where Chroma is published on localhost:8002.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if "blocking" in sys.argv[1:]:
        result = run_blocking_review()
        log.info(
            "  → %d blockers recovered, %d rejected, %d skipped (retried later).",
            result.reviewed,
            result.rejected,
            result.skipped,
        )
    else:
        result = run_review()
        log.info(
            "  → %d annotated, %d rejected, %d skipped (retried later) → %d chunks patched in Chroma.",
            result.reviewed,
            result.rejected,
            result.skipped,
            result.patched_chunks,
        )
