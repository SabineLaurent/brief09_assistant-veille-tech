from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.ingest.cleaning import clean_html_to_markdown, strip_boilerplate

log = logging.getLogger(__name__)


@dataclass
class Scraper:
    user_agent: str = "nauda-palisse-veille/0.1"
    timeout: float = 10.0

    def run(self, urls: list[str]) -> list[dict[str, Any]]:
        articles = []
        with httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            for url in urls:
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, "lxml")
                    strip_boilerplate(soup)
                    title = _extract_title(soup)
                    content = clean_html_to_markdown(str(soup))
                    source = urlparse(url).netloc
                    article_id = hashlib.sha1(url.encode()).hexdigest()
                    articles.append({
                        "id": article_id,
                        "title": title,
                        "url": url,
                        "content": content,
                        "source": source,
                        "date": None,
                        "tags": [],
                    })
                except Exception as e:
                    log.warning("Scraper: failed to fetch %s — %s", url, e)
        return articles


def _extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return ""
