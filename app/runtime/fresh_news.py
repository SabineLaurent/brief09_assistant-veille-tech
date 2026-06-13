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
    """Date de publication d'une entrée en datetime naïf (UTC), ou None.

    feedparser pré-parse la date en time.struct_time sous `published_parsed`
    (repli `updated_parsed`). On garde les 6 premiers champs (année…seconde).
    """
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6])


def _normalize_entry(entry: Any, feed_title: str, dt: datetime | None) -> dict[str, Any]:
    """Mappe une entrée feedparser → carte fraîche {title, url, source, date, content,
    tags}. Forme commune à tous les flux : c'est ce qui rend le module générique."""
    url = entry.get("link", "")
    # source : titre du flux (ex. "OpenAI News"), repli sur le domaine de l'URL.
    source = feed_title or urlparse(url).netloc
    # tags : les <category> du flux → list[str] directement exploitable par _build_cards
    # (les cartes fraîches ne passent pas par _split_tags, cf. SPECS §3.4).
    tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
    summary = entry.get("summary", "")
    return {
        "title": entry.get("title", "Sans titre"),
        "url": url,
        "source": source,
        "date": dt.isoformat() if dt else None,
        "content": clean_html_to_markdown(summary) if summary else "",
        "tags": tags,
    }


async def fetch(
    topics: list[str],
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Agrège l'actu fraîche depuis les flux RSS configurés (settings.sources.rss_feeds).

    - Appelée à chaque /chat ; **ne lève jamais** → renvoie [] sur toute erreur.
    - `since` : ne garde que les entrées plus récentes (quand la date est connue).
    - `topics` : ignoré pour l'instant (flux déjà curés) — présent pour le contrat.
    Voir docs/steps/14-fresh-news-rss.md.
    """
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
            for url in feeds:
                # Tolérance aux pannes : un flux KO (réseau, XML pourri) ne doit pas
                # priver des autres → on logue et on passe au suivant.
                try:
                    resp = await client.get(url)
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
                except Exception as exc:
                    logger.warning("fresh_news: flux %s ignoré — %s", url, exc)
    except Exception as exc:
        logger.warning("fresh_news: échec global — %s", exc)
        return []

    return articles
