from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.data.article_store import get_watermark
from app.ingest.cleaning import clean_html_to_markdown
from app.ingest.article_models import TldrArticle

log = logging.getLogger(__name__)


@dataclass
class TldrScraper:
    base_url: str = get_settings().sources.tldr_base_url
    user_agent: str = "nauda-palisse-veille/0.1"
    timeout: float = 10.0
    editions: list[str] = field(default_factory=lambda: ["tech", "webdev", "ai"])

    def build_urls(self, editions: list[str], date: str) -> list[str]:
        """
        Construit les URLs des newsletters TLDR à scraper.

        Entrée :
            editions : noms d'éditions TLDR, ex. ["tech", "webdev", "ai"]
            date : date de l'édition au format "YYYY-MM-DD", ex. "2026-06-10"

        Sortie :
            liste d'URLs, une par édition,
            ex. ["https://tldr.tech/tech/2026-06-10", "https://tldr.tech/ai/2026-06-10"]
        """
        # rstrip("/") : le base_url peut finir par un "/" (selon le .env) ; sans ça
        # on obtiendrait un double slash "https://tldr.tech//tech/..." (redirect 308).
        base = self.base_url.rstrip("/")
        return [f"{base}/{edition}/{date}" for edition in editions]

    def run(self, urls: list[str]) -> list[TldrArticle]:
        """
        Télécharge et parse chaque newsletter ; une URL en échec est loggée et ignorée.

        Entrée :
            urls : URLs de newsletters TLDR (telles que produites par build_urls),
            ex. ["https://tldr.tech/ai/2026-06-10"]

        Sortie :
            liste de TldrArticle (toutes éditions confondues), chacun de la forme :
                reference="<sha1 de l'url sans tracking>", title="...",
                source="tldr.tech", published_date=datetime|None,
                content="<résumé en Markdown>", url="https://...", authors=[]
            tags et keywords restent vides : c'est l'agent de review qui les renseigne.
            Liste vide si toutes les URLs ont échoué.
        """
        articles: list[TldrArticle] = []
        with httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            for url in urls:
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    date = _extract_date(url)
                    articles.extend(_parse_newsletter(resp.text, date))
                except Exception as e:
                    log.warning("TldrScraper: failed to fetch %s — %s", url, e)
        return articles

    def run_incremental(self) -> list[TldrArticle]:
        """Collecte incrémentale : calcule les éditions manquantes puis scrape.

        Encapsule l'enchaînement qui vivait jusqu'ici dans le CLI `tldr` :
            get_watermark (article_store) → missing_edition_dates → build_urls → run

        Sortie :
            liste de TldrArticle des éditions manquantes (watermark+1 →
            aujourd'hui). Liste vide si la base est déjà à jour.

        Pendant : aligne TLDR sur arXiv — l'appelant n'a plus qu'à faire
        `.run_incremental()` sans connaître la mécanique du watermark.
        """
        settings = get_settings()
        watermark = get_watermark("tldr.tech", "published_date")
        start_date = date.fromisoformat(settings.sources.tldr_start_date)
        dates = missing_edition_dates(watermark, date.today(), start_date)
        if not dates:
            log.info("TLDR déjà à jour, rien à scraper.")
            return []

        urls = [u for d in dates for u in self.build_urls(self.editions, d)]
        log.info(
            "TLDR : %d date(s) à scraper (%s → %s), %d URL(s).",
            len(dates),
            dates[0],
            dates[-1],
            len(urls),
        )
        return self.run(urls)


def missing_edition_dates(watermark: datetime | None, today: date, start_date: date) -> list[str]:
    """Calcule les dates d'édition TLDR restant à scraper — ingestion incrémentale.

    Entrée :
        watermark : date de la dernière édition déjà en base (None si base vide).
        today : date du jour, borne haute incluse.
        start_date : date de départ quand la base est vide (watermark None).

    Sortie :
        liste de dates "YYYY-MM-DD" (format attendu par build_urls), de la dernière
        connue + 1 jour jusqu'à aujourd'hui inclus. Liste vide si déjà à jour
        (watermark >= today) → le 2ᵉ run ne re-scrape rien.

    Voir docs/steps/11-ingestion-incrementale-watermark.md.
    """
    start = start_date if watermark is None else watermark.date() + timedelta(days=1)
    dates: list[str] = []
    day = start
    while day <= today:
        dates.append(day.isoformat())
        day += timedelta(days=1)
    return dates


def _extract_date(url: str) -> str:
    """
    Entrée : URL de newsletter, ex. "https://tldr.tech/ai/2026-06-10"
    Sortie : la date au format "YYYY-MM-DD" ("" si introuvable).
    """
    match = re.search(r"(\d{4}-\d{2}-\d{2})", url)
    return match.group(1) if match else ""


def _parse_newsletter(html: str, date: str) -> list[TldrArticle]:
    """
    Parse le HTML d'une newsletter TLDR en articles normalisés.

    Entrée :
        html : page HTML complète de la newsletter (structure attendue :
               <section> par catégorie, contenant des <article> avec un lien
               a.font-bold>h3 pour le titre et un div.newsletter-html pour le résumé)
        date : date de l'édition au format "YYYY-MM-DD" (peut être "")

    Sortie :
        liste de TldrArticle (voir TldrScraper.run pour la forme exacte).
        Les articles sponsorisés ("(Sponsor)" dans le titre) et les entrées
        sans lien sont exclus.
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []

    published_date: datetime | None = None
    if date:
        try:
            published_date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            pass

    for section in soup.find_all("section"):
        for article_tag in section.find_all("article"):
            link = article_tag.find("a", class_="font-bold")
            if not link:
                continue

            h3 = link.find("h3")
            title = h3.get_text(strip=True) if h3 else ""

            if "(Sponsor)" in title:
                continue

            href = link.get("href", "")
            source_url = href if isinstance(href, str) else ""
            if not source_url:
                continue

            summary_div = article_tag.find("div", class_="newsletter-html")
            content = clean_html_to_markdown(str(summary_div)) if summary_div else ""

            clean_url = source_url.split("?utm_source=")[0]
            reference = hashlib.sha1(clean_url.encode()).hexdigest()

            articles.append(
                TldrArticle(
                    reference=reference,
                    title=title,
                    source="tldr.tech",
                    published_date=published_date,
                    content=content,
                    url=source_url,
                    authors=[],
                )
            )

    return articles
