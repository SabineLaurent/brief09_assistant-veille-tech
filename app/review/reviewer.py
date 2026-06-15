from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.ingest.cleaning import has_enough_content, is_usable_title
from app.ingest.scraper import Scraper

logger = logging.getLogger(__name__)


# ── Schémas de sortie structurée ──────────────────────────────────────────────
# Le LLM est contraint de répondre au format de l'un de ces modèles (JSON schema).
# Deux schémas distincts pour ne payer des tokens de résumé que lorsque le content
# est vide ou trop court.


class _Review(BaseModel):
    """
    Sortie lorsque le content est suffisant : on annote sans résumer.
    """

    keywords: list[str] = Field(
        description=(
            "3 to 6 concise keywords (1-3 words each) naming the subject of the provided "
            "article text; prefer the canonical term over the article's full phrasing"
        )
    )
    topics: list[str] = Field(description="topics from the allowed list; empty if none apply")


class _ReviewWithSummary(BaseModel):
    """
    Output when content is empty or too short: summarize first, then annotate.

    `summary` is deliberately the first field: in structured output the model fills
    fields in schema order. Writing the summary first forces the model to digest the
    text before emitting the keywords, which still derive from the article itself.
    This is why this model does not inherit from `_Review` (two fields are duplicated,
    on purpose).
    """

    summary: str = Field(description="summary of the article, abstract level, a few sentences")
    keywords: list[str] = Field(
        description=(
            "3 to 6 concise keywords (1-3 words each) naming the subject of the provided "
            "article text; prefer the canonical term over a full phrase"
        )
    )
    topics: list[str] = Field(description="topics from the allowed list; empty if none apply")


@dataclass
class ReviewResult:
    """
    Outcome of reviewing one article, consumed by the runner for persistence.

    `rejected` is the terminal verdict: the record can never become indexable, so the
    runner sets status='rejected' and ignores the other fields. Otherwise the runner
    persists keywords/tags plus, when set, the recovered title and/or generated summary.
    """

    keywords: list[str]
    tags: list[str]                       # topics kept, filtered against available_topics
    generated_summary: str | None = None  # set only when content was empty/thin and recovered
    recovered_title: str | None = None    # set only when a junk title was read from the source
    rejected: bool = False                # True → terminal reject (record can never be indexed)


@lru_cache(maxsize=1)
def get_mini_agent() -> ChatOpenAI | None:
    """
    Client LLM de l'agent de review (déploiement "mini" dédié, distinct du chat).

    Renvoie None si l'agent n'est pas configuré, ce qui permet une dégradation propre :
    le pipeline reste fonctionnel sans agent.
    """
    settings = get_settings()
    if not settings.azure_ai_mini_agent_endpoint or not settings.azure_ai_mini_agent_api_key:
        logger.info("Mini agent not configured — review skipped")
        return None
    return ChatOpenAI(
        base_url=settings.azure_ai_mini_agent_endpoint,
        api_key=settings.azure_ai_mini_agent_api_key,
        model=settings.azure_ai_mini_agent_model,
        temperature=0.1,  # fidèle, n'invente rien
    )


_TOPIC_GLOSSES = {
    "AI": "machine learning, models, LLMs, training/inference, AI research and products.",
    "Security": "vulnerabilities, attacks, defense, cryptography, privacy.",
    "Agentic": "autonomous agents, tool-use, multi-agent systems, agent orchestration.",
    "Embedded": "on-device/edge computing, hardware, IoT, tinyML, firmware.",
}


def _build_system_prompt(available_topics: list[str]) -> str:
    # Gloss each allowed topic when we have a definition; fall back to the bare name
    # so an added topic still appears in the list even before it gets a gloss.
    topic_lines = "\n".join(
        f"  - {t}: {_TOPIC_GLOSSES[t]}" if t in _TOPIC_GLOSSES else f"  - {t}"
        for t in available_topics
    )
    return (
        "You are an English-speaking technology-watch annotation agent for Nauda Palisse.\n"
        "Annotate the given article with topics and keywords.\n\n"
        "TOPICS — choose ONLY from this controlled list, picking every one that genuinely "
        "applies (or none if none fit; never force a match):\n"
        f"{topic_lines}\n\n"
        "KEYWORDS — 3 to 6, concise (1-3 words each), naming the subject of the provided "
        "article text (its title and content). Prefer the canonical term over the article's "
        "full phrasing (e.g. 'prompt injection', not 'brain-prompt injection attacks on "
        "BCI-to-agent pipelines'). Favour specific named technologies, methods or products "
        "over generic words. No sentences, no duplicates, do not repeat the topic names.\n\n"
        "SUMMARY (only when requested) — write it FIRST, a few sentences at abstract level.\n\n"
        "Write everything STRICTLY IN ENGLISH. Stay factual and faithful to the article's "
        "tone and vocabulary; do not invent anything."
    )


def _scrape(url: str) -> dict | None:
    """
    Fetch the source page once and return the scraped record, or None if unreachable.

    None means the Scraper yielded nothing (HTTP error, timeout, network issue) — an
    ambiguous, possibly transient failure the caller treats as "retry later". A returned
    dict means the page was reached (HTTP 200); its `content` may still be empty (e.g. a
    JS-rendered page), which the caller treats as "reached but nothing usable".
    """
    try:
        scraped = Scraper().run([url])
    except Exception as exc:  # a scrape failure must not abort the review
        logger.warning("Scrape failed for %s — %s", url, exc)
        return None
    return scraped[0] if scraped else None


def review_article(article: dict) -> ReviewResult | None:
    """
    Review one article with a structured LLM call, recovering a junk title or thin
    content from the source page when the record is a blocker.

    Returns one of three outcomes:
      - None                        → skip and retry later (agent not configured, LLM
                                      call failed, or source page unreachable — all
                                      likely transient).
      - ReviewResult(rejected=True) → terminal reject: the record can never become
                                      indexable (no source URL, or source reached but
                                      still no usable title and/or content).
      - ReviewResult(...)           → annotation to persist, carrying the recovered title
                                      and/or generated summary when the record was fixed.

    It is the code (not the LLM) that decides to scrape. A recovered title is READ from
    the page (never invented); thin content is summarized by the LLM.
    """
    agent = get_mini_agent()
    if agent is None:
        return None

    settings = get_settings()
    content = article.get("content") or ""
    title = article.get("title") or ""
    url = article.get("url") or ""

    title_ok = is_usable_title(title)
    content_ok = has_enough_content(content)
    text, needs_summary = content, False
    recovered_title: str | None = None

    if not (title_ok and content_ok):
        # Blocker: try to recover the missing pieces from the source page.
        scraped = _scrape(url) if url else None
        if scraped is None:
            if not url:
                # No source to recover from → can never become indexable → reject.
                return ReviewResult(keywords=[], tags=[], rejected=True)
            # Page unreachable, possibly transient → leave 'ingested', retry later.
            return None

        page_title = scraped.get("title") or ""
        page_content = (scraped.get("content") or "").strip()
        if not title_ok and is_usable_title(page_title):
            recovered_title, title_ok = page_title, True
        if not content_ok and has_enough_content(page_content):
            text, needs_summary, content_ok = page_content, True, True

        if not (title_ok and content_ok):
            # Source reached but still not indexable: nothing recoverable → terminal
            # reject (also breaks the blocker loop — it would never pass the indexer).
            return ReviewResult(keywords=[], tags=[], rejected=True)

    schema = _ReviewWithSummary if needs_summary else _Review

    try:
        review = agent.with_structured_output(schema).invoke(
            [
                SystemMessage(content=_build_system_prompt(settings.available_topics)),
                HumanMessage(content=f"Title: {recovered_title or title}\n\nContent:\n{text}"),
            ]
        )
    except Exception as exc:
        logger.warning("Review LLM call failed for %r — %s", article.get("reference"), exc)
        return None

    # The LLM output is untrusted input: filter topics against the allowed vocabulary,
    # even though the prompt already constrained them.
    allowed = set(settings.available_topics)
    tags = [t for t in review.topics if t in allowed]

    return ReviewResult(
        keywords=review.keywords,
        tags=tags,
        generated_summary=review.summary if needs_summary else None,
        recovered_title=recovered_title,
    )
