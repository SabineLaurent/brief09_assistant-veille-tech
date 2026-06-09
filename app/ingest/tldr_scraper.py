from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

from app.config import get_settings
from app.ingest.cleaning import clean_html_to_markdown

log = logging.getLogger(__name__)


class TldrArticle(BaseModel):
    reference: str
    title: str
    source: str
    published_date: datetime | None
    content: str
    url: str
    tags: list[str]
    authors: list[str]

    def to_chroma_metadata(self) -> dict[str, str]:
        return {
            "title": self.title,
            "source": self.source,
            "date": self.published_date.isoformat() if self.published_date else "",
            "url": self.url,
            "tags": "|".join(self.tags),
            "authors": "|".join(self.authors),
        }

    def to_indexable(self) -> dict[str, Any]:
        return {
            "id": self.reference,
            "content": self.content,
            "metadata": self.to_chroma_metadata(),
        }


@dataclass
class TldrScraper:
    base_url: str = get_settings().sources.tldr_base_url
    user_agent: str = "nauda-palisse-veille/0.1"
    timeout: float = 10.0

    def build_urls(self, editions: list[str], date: str) -> list[str]:
        return [f"{self.base_url}/{edition}/{date}" for edition in editions]

    def run(self, urls: list[str]) -> list[TldrArticle]:
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
    match = re.search(r"(\d{4}-\d{2}-\d{2})", url)
    return match.group(1) if match else ""


def _extract_edition(url: str) -> str:
    parts = url.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 else ""


def _parse_newsletter(html: str, date: str, edition: str) -> list[TldrArticle]:
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
    header = section.find("header")
    if not header:
        return ""
    h3 = header.find("h3")
    return h3.get_text(strip=True) if h3 else ""
