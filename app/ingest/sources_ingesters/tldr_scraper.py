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
    editions: list[str] = field(default_factory=lambda: ["ai", "infosec", "it", "design", "dev", "devops", "tech", "data", "hardware"])

    def build_urls(self, editions: list[str], date: str) -> list[str]:
        """
        Constructs TLDR newsletter URLs to scrape.

        Entrance:
            editions: TLDR edition names, e.g. ["tech", "webdev", "ai"]
            date: edition date in “YYYY-MM-DD” format, e.g. "2026-06-10"

        Output:
            list of URLs, one per edition,
            ex. ["https://tldr.tech/tech/2026-06-10", "https://tldr.tech/ai/2026-06-10"]
        """
        # rstrip("/"): the base_url can end with a "/" (depending on the .env); without that
        # we would obtain a double slash "https://tldr.tech//tech/..." (redirect 308).
        base = self.base_url.rstrip("/")
        return [f"{base}/{edition}/{date}" for edition in editions]

    def run(self, urls: list[str]) -> list[TldrArticle]:
        """
        Download and parse each newsletter; a failed URL is logged and ignored.

        Entrance:
            urls: TLDR newsletter URLs (as produced by build_urls),
            e.g. ["https://tldr.tech/ai/2026-06-10"]

        Output:
            list of TldrArticle (all editions combined), each of the form:
                reference="<sha1 of url without tracking>", title="...",
                source="tldr.tech", published_date=datetime|None,
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
                    articles.extend(_parse_newsletter(resp.text, date))
                except Exception as e:
                    log.warning("TldrScraper: failed to fetch %s — %s", url, e)
        return articles

    def run_incremental(self) -> list[TldrArticle]:
        """Incremental collection: calculates the missing editions then scrapes.

        Encapsulates the sequence that lived until now in the `tldr` CLI:
            get_watermark(article_store) → missing_edition_dates → build_urls → run

        Output:
            TldrArticle list of missing editions (watermark+1 →
            today). Empty list if the database is already up to date.

        During: aligns TLDR with arXiv — caller just needs to do
        `.run_incremental()` without knowing the watermark mechanics.
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


def _parse_newsletter(html: str, date: str) -> list[TldrArticle]:
    """
    Parses the HTML of a TLDR newsletter into normalized articles.

    Entrance:
        html: complete HTML page of the newsletter (expected structure:
               <section> by category, containing <article> with a link
               a.font-bold>h3 for the title and a div.newsletter-html for the summary)
        date: date of edition in "YYYY-MM-DD" format (can be "")

    Output:
        liste de TldrArticle (voir TldrScraper.run pour la forme exacte).
        Sponsored articles ("(Sponsor)" in the title) and entries
        unrelated are excluded.
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
