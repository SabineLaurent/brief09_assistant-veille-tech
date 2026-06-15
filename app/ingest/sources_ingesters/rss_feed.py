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
    """Date d'une entrée RSS/Atom : published si présent, sinon updated, sinon None.

    feedparser expose les dates parsées sous forme de struct_time
    (`published_parsed` / `updated_parsed`) : on en garde les 6 premiers champs
    (année…seconde) pour construire un datetime.
    """
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6])


@dataclass
class RssFeedIngester:
    """Ingester « froid » des flux RSS/Atom configurés (blogs officiels : OpenAI,
    HuggingFace, MIT Tech Review…).

    Même mécanique de collecte que `runtime/fresh_news.py` (feedparser), mais :
      - sortie en `Article` pydantic (persistée en SQLite, puis indexée dans Chroma),
      - PAS de tag « New » : ces articles vont dans la base de connaissance froide.

    Calqué sur `TldrScraper` : httpx synchrone (l'ingestion CLI est synchrone) et
    boucle tolérante aux pannes (un flux KO n'invalide pas les autres).
    """

    # default_factory=get_settings : get_settings() est lru_cached → on récupère le
    # singleton de config, sans rendre le champ Optional (mypy reste content).
    settings: Settings = field(default_factory=get_settings)
    user_agent: str = "nauda-palisse-veille/0.1"
    timeout: float = 8.0

    def run(self) -> list[RssArticle]:
        """Récupère et normalise les articles de tous les flux RSS configurés.

        Entrée :
            aucune — les flux viennent de la config
            (settings.sources.rss_feeds, liste de RSSFeed {url, topic, fresh_news}).

        Sortie :
            liste de RssArticle (voir _normalize_entry pour la forme), tous flux
            confondus. Collecte froide incrémentale : par flux, on prend les
            articles publiés depuis le watermark (dernière date connue en base) ;
            au run à froid (watermark None), on part de `rss_start_date`.
            Liste vide si tous les flux ont échoué.
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
        """Convertit une entrée RSS en RssArticle normalisé.

        Sortie :
            RssArticle de la forme :
                reference="<sha1 de l'url sans query>", title="...",
                source="<titre du flux>", published_date=datetime|None,
                content="<résumé en Markdown>", url="https://...", authors=[]
            tags et keywords restent vides : c'est l'agent de review qui les renseigne.
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
    # Lancement manuel : `uv run python -m app.ingest.rss_feed`
    #  - récupère les articles des flux RSS configurés
    #  - sauvegarde en base via upsert_article (idempotent)
    #  - exporte un CSV horodaté de la session
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
