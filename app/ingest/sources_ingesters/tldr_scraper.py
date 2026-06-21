from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from app.config import TldrEdition, get_settings
from app.data.article_store import get_watermark
from app.ingest.cleaning import clean_html_to_markdown
from app.ingest.article_models import TldrArticle

log = logging.getLogger(__name__)


@dataclass
class TldrScraper:
    base_url: str = get_settings().sources.tldr_base_url
    user_agent: str = "nauda-palisse-veille/0.1"
    timeout: float = 10.0
    editions: list[TldrEdition] = field(default_factory=lambda: get_settings().sources.tldr_editions)

    def build_urls(self, editions: list[TldrEdition], date: str) -> list[str]:
        """
        Constructs TLDR newsletter URLs to scrape.

        Entrance:
            editions: TLDR editions to scrape; each edition's `name` is the URL
            slug, e.g. TldrEdition(name="tech"), TldrEdition(name="ai")
            date: edition date in “YYYY-MM-DD” format, e.g. "2026-06-10"

        Output:
            list of URLs, one per edition (those without a `name` are skipped),
            ex. ["https://tldr.tech/tech/2026-06-10", "https://tldr.tech/ai/2026-06-10"]
        """
        # rstrip("/"): the base_url can end with a "/" (depending on the .env); without that
        # we would obtain a double slash "https://tldr.tech//tech/..." (redirect 308).
        base = self.base_url.rstrip("/")
        return [f"{base}/{edition.name}/{date}" for edition in editions if edition.name]

    def run(self, urls: list[str]) -> list[TldrArticle]:
        """
        Download and parse each newsletter; a failed URL is logged and ignored.

        Entrance:
            urls: TLDR newsletter URLs (as produced by build_urls),
            e.g. ["https://tldr.tech/ai/2026-06-10"]

        Output:
            list of TldrArticle (all editions combined), each of the form:
                reference="<sha1 of url without tracking>", title="...",
                source="tldr.tech-<edition>", published_date=datetime|None,
                content="<Markdown summary>", url="https://...", authors=[]
            tags and keywords remain empty: it is the review agent who enters them.
            Empty list if all URLs failed.
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

    def run_incremental(self) -> list[TldrArticle]:
        """Incremental collection: per-edition catch-up, then scrape.

        Each edition (ai, tech, dev…) has its own watermark, keyed on the stored
        source "tldr.tech-<edition>", so editions catch up independently:
            get_watermark(per edition) → missing_edition_dates → build_urls → run

        Output:
            TldrArticle list of missing editions (each edition's watermark+1 →
            today). Empty list if every edition is already up to date.

        Caller just needs to do `.run_incremental()` without knowing the
        watermark mechanics.
        """
        settings = get_settings()
        start_date = date.fromisoformat(settings.sources.tldr_start_date)
        today = date.today()

        # Scrape edition by edition so the source currently being ingested is
        # visible live (one log line per source), and each edition uses its own
        # watermark to catch up independently.
        articles: list[TldrArticle] = []
        for edition in self.editions:
            if not edition.name:
                continue
            source = f"tldr.tech-{edition.name}"
            watermark = get_watermark(source, "published_date")
            dates = missing_edition_dates(watermark, today, start_date)
            if not dates:
                continue
            urls = [u for d in dates for u in self.build_urls([edition], d)]
            edition_articles = self.run(urls)
            log.info("[tldr] %s … %d article(s)", source, len(edition_articles))
            articles.extend(edition_articles)

        if not articles:
            log.info("TLDR déjà à jour, rien à scraper.")
        return articles


def missing_edition_dates(watermark: datetime | None, today: date, start_date: date) -> list[str]:
    """Calculates TLDR edition dates remaining to scrape — incremental ingestion.

    Entrance:
        watermark: date of the last edition already in the base (None if the base is empty).
        today: today's date, upper limit included.
        start_date: start date when the database is empty (watermark None).

    Output:
        list of dates "YYYY-MM-DD" (format expected by build_urls), from the last
        known + 1 day up to and including today. Empty list if already up to date
        (watermark >= today) → the 2nd run does not re-scrape anything.

    See docs/steps/11-ingestion-incrementale-watermark.md.
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
    Input: Newsletter URL, e.g. "https://tldr.tech/ai/2026-06-10"
    Output: the date in "YYYY-MM-DD" format ("" if not found).
    """
    match = re.search(r"(\d{4}-\d{2}-\d{2})", url)
    return match.group(1) if match else ""


def _extract_edition(url: str) -> str:
    """
    Input: newsletter URL, e.g. "https://tldr.tech/ai/2026-06-10"
    Output: the edition slug ("ai"); "" if not found.
    """
    match = re.search(r"/([^/]+)/\d{4}-\d{2}-\d{2}", url)
    return match.group(1) if match else ""


def _parse_newsletter(html: str, date: str, edition: str) -> list[TldrArticle]:
    """
    Parses the HTML of a TLDR newsletter into normalized articles.

    Entrance:
        html: complete HTML page of the newsletter (expected structure:
               <section> by category, containing <article> with a link
               a.font-bold>h3 for the title and a div.newsletter-html for the summary)
        date: date of edition in "YYYY-MM-DD" format (can be "")
        edition: edition slug (e.g. "ai") → stored as source "tldr.tech-<edition>"
            (falls back to "tldr.tech" when empty)

    Output:
        liste de TldrArticle (voir TldrScraper.run pour la forme exacte).
        Sponsored articles ("(Sponsor)" in the title) and entries
        unrelated are excluded.
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []

    source = f"tldr.tech-{edition}" if edition else "tldr.tech"

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
                    source=source,
                    published_date=published_date,
                    content=content,
                    url=source_url,
                    authors=[],
                )
            )

    return articles
