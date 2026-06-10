from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import get_settings
from app.ingest.cleaning import clean_html_to_markdown
from app.ingest.models import Article

log = logging.getLogger(__name__)


class TldrArticle(Article):
    pass


@dataclass
class TldrScraper:
    base_url: str = get_settings().sources.tldr_base_url
    user_agent: str = "nauda-palisse-veille/0.1"
    timeout: float = 10.0

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
        return [f"{self.base_url}/{edition}/{date}" for edition in editions]

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
                content="<résumé en Markdown>", url="https://...",
                tags=["<edition>", "<catégorie>"], authors=[]
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
                    edition = _extract_edition(url)
                    articles.extend(_parse_newsletter(resp.text, date, edition))
                except Exception as e:
                    log.warning("TldrScraper: failed to fetch %s — %s", url, e)
        return articles


def _extract_date(url: str) -> str:
    """
    Entrée : URL de newsletter, ex. "https://tldr.tech/ai/2026-06-10"
    Sortie : la date au format "YYYY-MM-DD" ("" si introuvable).
    """
    match = re.search(r"(\d{4}-\d{2}-\d{2})", url)
    return match.group(1) if match else ""


def _extract_edition(url: str) -> str:
    """
    Entrée : URL de newsletter, ex. "https://tldr.tech/ai/2026-06-10"
    Sortie : l'édition (avant-dernier segment du chemin), ex. "ai" ("" si introuvable).
    """
    parts = url.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 else ""


def _parse_newsletter(html: str, date: str, edition: str) -> list[TldrArticle]:
    """
    Parse le HTML d'une newsletter TLDR en articles normalisés.

    Entrée :
        html : page HTML complète de la newsletter (structure attendue :
               <section> par catégorie, contenant des <article> avec un lien
               a.font-bold>h3 pour le titre et un div.newsletter-html pour le résumé)
        date : date de l'édition au format "YYYY-MM-DD" (peut être "")
        edition : nom de l'édition, ex. "ai"

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
        category = _extract_category(section)

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

            tags = [edition]
            if category:
                tags.append(category)

            articles.append(TldrArticle(
                reference=reference,
                title=title,
                source="tldr.tech",
                published_date=published_date,
                content=content,
                url=source_url,
                tags=tags,
                authors=[],
            ))

    return articles


def _extract_category(section: Tag) -> str:
    """
    Entrée : un élément <section> de la newsletter (objet BeautifulSoup Tag).
    Sortie : le texte du <header><h3>, ex. "Big Tech & Startups" ("" si absent).
    """
    header = section.find("header")
    if not header:
        return ""
    h3 = header.find("h3")
    return h3.get_text(strip=True) if h3 else ""
