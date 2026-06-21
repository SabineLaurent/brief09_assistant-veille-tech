"""
Ingestion of configured RSS feeds.
    --> Official blogs: OpenAI, HuggingFace. 



"""


from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from app.config import Settings, get_settings
from app.data.article_store import get_watermark
from app.ingest.cleaning import clean_html_to_markdown
from app.ingest.article_models import RssArticle

log = logging.getLogger(__name__)


def _entry_datetime(entry: Any) -> datetime | None:
    """
    Date of an RSS entry: published if present, otherwise updated, otherwise None.

    feedparser exposes parsed dates as struct_time
    (`published_parsed` /`updated_parsed`): we keep the first 6 fields
    (year…second) to construct a datetime.
    """
    
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6])


@dataclass
class RssFeedIngester:
    """
    "Cold" ingest configured RSS feeds (official blogs: OpenAI,
    HuggingFace, MIT Tech Review…).

    Same collection mechanics as `runtime/fresh_news.py` (feedparser), but:
      -output in pydantic `Article` (persisted in SQLite, then indexed in Chroma),
      -NO “New” tag: these articles go into the cold knowledge base.

    Modeled after `TldrScraper`: synchronous httpx (CLI ingestion is synchronous) and
    fault-tolerant loop (a KO flow does not invalidate the others).
    """

    # default_factory=get_settings : get_settings() est lru_cached → on récupère le
    # singleton de config, sans rendre le champ Optional (mypy reste content).
    settings: Settings = field(default_factory=get_settings)
    user_agent: str = "nauda-palisse-veille/0.1"
    timeout: float = 8.0


    def run(self) -> list[RssArticle]:
        """
        Retrieves and normalizes articles from all configured RSS feeds.

        Entrance:
            none — the flows come from the config
            (settings.sources.rss_feeds, RSSFeed list {url, topic, fresh_news}).

        Output:
            list of RssArticle (see _normalize_entry for form), all feeds
            confused. Incremental cold collection: per flow, we take the
            articles published since the watermark (last date known in database);
            when running cold (watermark None), we start from `rss_start_date`.
            Empty list if all flows failed.
        """

        feeds = self.settings.sources.rss_feeds
        # Plancher du run à froid (watermark None) : uniforme avec tldr_start_date.
        start = date.fromisoformat(self.settings.sources.rss_start_date)
        articles: list[RssArticle] = []

        with httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            for feed in feeds:
                # Tolérance aux pannes : un flux injoignable (réseau, XML pourri) est
                # loggé puis ignoré — on passe au flux suivant.
                try:
                    resp = client.get(feed.url)
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.content)
                    feed_title = parsed.feed.get("title", "")

                    # Watermark PAR FLUX : la source stockée en base est le titre du
                    # flux (cf. _normalize_entry). floor = dernière date connue, ou
                    # rss_start_date au premier run. L'upsert idempotent gère les
                    # doublons du jour-frontière, donc un simple ">=" suffit.
                    source = feed_title or urlparse(feed.url).netloc
                    watermark = get_watermark(source, "published_date")
                    floor = watermark.date() if watermark else start

                    kept = 0
                    for entry in parsed.entries:
                        # On saute les articles publiés avant le plancher. Ceux sans
                        # date détectable (None) sont conservés.
                        published = _entry_datetime(entry)
                        if published is not None and published.date() < floor:
                            continue
                        articles.append(self._normalize_entry(entry, feed_title))
                        kept += 1
                    log.info("[rss] %s — %d article(s)", feed.url, kept)
                except Exception as e:
                    log.warning("[rss] flux %s ignoré — %s", feed.url, e)

        log.info("  → %d articles RSS récupérés (%d flux)", len(articles), len(feeds))
        return articles


    def _normalize_entry(self, entry: Any, feed_title: str) -> RssArticle:
        """
        Converts an RSS entry to a normalized RssArticle.

        Output:
            RssArticle of the form:
                reference="<sha1 of url without query>", title="...",
                source="<stream title>", published_date=datetime|None,
                content="<Markdown summary>", url="https://...", authors=[]
            tags and keywords remain empty: it is the review agent who enters them.
        """

        url = entry.get("link", "")
        # reference = clé de dédup : on retire la query (tracking utm…) pour qu'un
        # même article ressorti avec des paramètres différents donne la même clé.
        clean_url = url.split("?")[0]
        reference = hashlib.sha1(clean_url.encode()).hexdigest()
        summary = entry.get("summary", "")

        return RssArticle(
            reference=reference,
            title=entry.get("title", "Sans titre"),
            # source : titre du flux (ex. "OpenAI News"), repli sur le domaine de l'URL.
            source=feed_title or urlparse(url).netloc,
            published_date=_entry_datetime(entry),
            content=clean_html_to_markdown(summary) if summary else "",
            url=url,
            authors=[],
        )


if __name__ == "__main__":
    # Manual launch: `uv run python -m app.ingest.rss_feed`
    #  -retrieves articles from configured RSS feeds
    #  -database backup via upsert_article (idempotent)
    #  -exports a timestamped CSV of the session
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.data.article_store import count_articles, upsert_article
    from app.data.csv_exporter import export_to_csv

    ingester = RssFeedIngester()
    articles = ingester.run()

    inserted = sum(upsert_article(a.model_dump()) for a in articles)
    csv_path = export_to_csv([a.model_dump() for a in articles], source_name="rss")

    log.info("  → %d nouveaux articles ajoutés à la base de données", inserted)
    log.info("  → Log CSV de la session : %s", csv_path)
    log.info("Ingestion RSS terminée.")
    log.info("  → La base contient maintenant %d entrées", count_articles())
