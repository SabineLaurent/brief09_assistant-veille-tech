from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from app.config import get_settings
from app.ingest.cleaning import clean_html_to_markdown

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0
_USER_AGENT = "nauda-palisse-veille/0.1"


def _entry_datetime(entry: Any) -> datetime | None:

    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    
    return datetime(*parsed[:6])


def _normalize_entry(entry: Any, feed_title: str, dt: datetime | None) -> dict[str, Any]:

    url = entry.get("link", "")
    # source : titre du flux (ex. "OpenAI News"), repli sur le domaine de l'URL.
    source = feed_title or urlparse(url).netloc
    summary = entry.get("summary", "")

    # tags volontairement vide pour les articles fresh news : la distinction "frais" et
    # la catégorie (feed.topic) seront gérées côté front (affichage), pas dans les tags.
    return {
        "title": entry.get("title", "Sans titre"),
        "url": url,
        "source": source,
        "date": dt.isoformat() if dt else None,
        "content": clean_html_to_markdown(summary) if summary else "",
        "tags": [],
    }


async def fetch(
    topics: list[str],
    since: datetime | None = None,
) -> list[dict[str, Any]]:

    settings = get_settings()
    feeds = settings.sources.rss_feeds
    cap = settings.sources.rss_max_items_per_feed
    articles: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
            follow_redirects=True,
        ) as client:
            for feed in feeds:
                # Tolérance aux pannes : un flux KO (réseau, XML pourri) ne doit pas
                # priver des autres --> on logue et on passe au suivant.
                try:
                    resp = await client.get(feed.url)
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.content)
                    feed_title = parsed.feed.get("title", "")

                    kept = 0
                    for entry in parsed.entries:
                        if kept >= cap:
                            break
                        dt = _entry_datetime(entry)
                        if since is not None and dt is not None and dt < since:
                            continue
                        articles.append(_normalize_entry(entry, feed_title, dt))
                        kept += 1

                    # 0 article gardé sur un flux joignable = anomalie (flux vide, tout
                    # filtré par `since`...) : on le signale au lieu de l'avaler.
                    if kept == 0:
                        logger.warning(
                            "fresh_news: flux %s joignable mais 0 article gardé", feed.url
                        )
                    else:
                        logger.info("fresh_news: flux %s — %d article(s)", feed.url, kept)

                except Exception as exc:
                    logger.warning("fresh_news: flux %s ignoré — %s", feed.url, exc)

    except Exception as exc:
        logger.warning("fresh_news: échec global — %s", exc)
        return []

    # Bilan global : 0 article tous flux confondus ne doit pas passer inaperçu.
    if not articles:
        logger.warning(
            "fresh_news: AUCUN article frais récupéré (%d flux configuré(s))", len(feeds)
        )
    else:
        logger.info(
            "fresh_news: %d article(s) frais au total (%d flux)", len(articles), len(feeds)
        )

    return articles


if __name__ == "__main__":
    # Lancement manuel : `uv run python -m app.runtime.fresh_news`
    # Affiche les logs (INFO) + un récap lisible des articles récupérés.
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    results = asyncio.run(fetch(topics=[], since=None))

    print(f"\n=== {len(results)} article(s) frais ===")
    for art in results:
        print(f"\n[{', '.join(art['tags'])}] {art['source']} — {art['date']}")
        print(f"  {art['title']}")
        print(f"  {art['url']}")
