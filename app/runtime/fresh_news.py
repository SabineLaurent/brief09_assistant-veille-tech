from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings, TldrEdition, get_settings
from app.ingest.article_models import TldrArticle
from app.ingest.sources_ingesters.tldr_scraper import TldrScraper

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0
_USER_AGENT = "nauda-palisse-veille/0.1"
_TLDR_LOOKBACK_DAYS = 3  # today, J-1, J-2


# ====== GitHub releases ======


async def _fetch_github(settings: Settings) -> list[dict[str, Any]]:
    """Latest release of each watched repo, as fresh articles.

    One request per repo (`/repos/{owner}/{name}/releases/latest`); a repo
    without release (404), unreachable or rate-limited is logged and skipped.
    """
    repos = settings.sources.github_watched_repos
    if not repos:
        return []

    headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    token = settings.sources.github_releases_token
    if token:
        headers["Authorization"] = f"Bearer {token}"

    base = settings.sources.github_api_url.rstrip("/")
    articles: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        headers=headers, timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        for repo in repos:
            try:
                resp = await client.get(f"{base}/repos/{repo.owner}/{repo.name}/releases/latest")
                resp.raise_for_status()
                articles.append(_normalize_release(repo.owner, repo.name, resp.json()))
            except Exception as exc:
                logger.warning("fresh_news[github]: %s/%s skipped — %s", repo.owner, repo.name, exc)

    logger.info("fresh_news[github]: %d release(s) (%d repo(s))", len(articles), len(repos))
    return articles


def _normalize_release(owner: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
    """GitHub release payload → fresh article dict. `body` is already Markdown."""
    tag = data.get("tag_name", "")
    return {
        "title": f"{name} {tag}".strip(),
        "url": data.get("html_url", ""),
        "source": f"github.com/{owner}/{name}",
        "date": data.get("published_at"),
        "content": data.get("body") or "",
        "tags": [],
    }


# ====== TLDR.tech (live) ======


async def _fetch_tldr(settings: Settings) -> list[dict[str, Any]]:
    """Today's TLDR editions (cascading to previous days if empty)."""
    scraper = TldrScraper()
    editions = settings.sources.tldr_editions
    # TldrScraper is synchronous (httpx.Client): run it off the event loop so the
    # live chat path is not blocked.
    articles = await asyncio.to_thread(_scrape_tldr_cascade, scraper, editions)
    return [_normalize_tldr(a) for a in articles]


def _scrape_tldr_cascade(scraper: TldrScraper, editions: list[TldrEdition]) -> list[TldrArticle]:
    """Cascade today → J-1 → J-2: return the first non-empty day.

    A TLDR edition may not exist yet (published later in the day) or be missing
    (weekend), so we walk back day by day and stop as soon as one yields articles.
    """
    today = date.today()
    for delta in range(_TLDR_LOOKBACK_DAYS):
        day = (today - timedelta(days=delta)).isoformat()
        urls = scraper.build_urls(editions, day)
        articles = scraper.run(urls)
        if articles:
            logger.info("fresh_news[tldr]: %d article(s) for %s", len(articles), day)
            return articles
        logger.info("fresh_news[tldr]: 0 article for %s, trying previous day…", day)

    logger.warning("fresh_news[tldr]: no article over the last %d day(s)", _TLDR_LOOKBACK_DAYS)
    return []


def _normalize_tldr(article: TldrArticle) -> dict[str, Any]:
    """TldrArticle → fresh article dict (same shape as _normalize_release)."""
    return {
        "title": article.title,
        "url": article.url,
        "source": article.source,
        "date": article.published_date.isoformat() if article.published_date else None,
        "content": article.content,
        "tags": [],
    }


# ====== Public entrypoint ======


async def fetch(
    topics: list[str],
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Live fresh articles: GitHub releases + TLDR (today, fallback to previous days).

    `topics` and `since` are accepted to honor the caller's contract
    (chat.handle_chat), but the sources are already freshness-bounded (latest
    release per repo, TLDR over the last 3 days) so no extra filtering is applied.

    Degradable: each source is isolated; any failure logs and yields [] so /chat
    keeps working without network / GitHub token.
    """
    settings = get_settings()

    try:
        github = await _fetch_github(settings)
    except Exception as exc:
        logger.warning("fresh_news[github]: global failure — %s", exc)
        github = []

    try:
        tldr = await _fetch_tldr(settings)
    except Exception as exc:
        logger.warning("fresh_news[tldr]: global failure — %s", exc)
        tldr = []

    articles = github + tldr
    if not articles:
        logger.warning("fresh_news: no fresh article retrieved")
    else:
        logger.info(
            "fresh_news: %d fresh article(s) (github=%d, tldr=%d)",
            len(articles),
            len(github),
            len(tldr),
        )
    return articles


if __name__ == "__main__":
    # Manual launch: `uv run python -m app.runtime.fresh_news`
    from collections import Counter

    from rich.console import Console
    from rich.logging import RichHandler
    from rich.table import Table

    console = Console()

    # Route our per-source traces ([github]/[tldr]) through rich so they show up
    # colored during the fetch; silence httpx/httpcore's per-request INFO noise.
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, show_time=False)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    results = asyncio.run(fetch(topics=[], since=None))

    # Recap table: count of fresh articles per real source.
    per_source: Counter[str] = Counter(art["source"] for art in results)
    recap = Table(title="Articles frais par source")
    recap.add_column("Source", style="cyan", no_wrap=True)
    recap.add_column("Nombre", justify="right", style="green")
    for source in sorted(per_source):
        recap.add_row(source, str(per_source[source]))
    recap.add_section()
    recap.add_row("Total", str(len(results)))
    console.print(recap)

    # Detail table: one row per fresh article. Title/URL kept on a single line
    # (ellipsis) so the table stays readable even with ~100 articles.
    detail = Table(title="Détail", expand=True)
    detail.add_column("Source", style="cyan", no_wrap=True)
    detail.add_column("Date", no_wrap=True)
    detail.add_column("Titre", ratio=2, no_wrap=True, overflow="ellipsis")
    detail.add_column("URL", ratio=1, no_wrap=True, overflow="ellipsis", style="dim")
    for art in results:
        detail.add_row(art["source"], (art.get("date") or "—")[:10], art["title"], art["url"])
    console.print(detail)
