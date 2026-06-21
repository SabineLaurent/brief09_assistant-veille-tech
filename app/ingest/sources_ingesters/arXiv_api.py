from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from lxml import etree
from app.config import Settings, get_settings
from app.data.article_store import get_watermark
from app.ingest.article_models import ArXivArticle


log = logging.getLogger(__name__)

_ATOM = "http://www.w3.org/2005/Atom"


def _tag(name: str) -> str:
    # L'API arXiv renvoie du XML Atom : chaque balise est préfixée par l'espace
    # de noms. Cette fonction évite de répéter "{http://...}title" partout.
    return f"{{{_ATOM}}}{name}"


@dataclass
class ArXivApiIngester:
    settings: Settings | None = None
    page_delay: float = 3.0  # pause (s) entre deux pages paginées — "politesse" envers arXiv

    def __post_init__(self) -> None:
        if self.settings is None:
            self.settings = get_settings()

    def fetch_articles(
        self, category: str, keywords: list[str], start: int = 0
    ) -> list[dict[str, Any]]:
        """
        Queries the arXiv API for a category and a list of keywords (combined in OR).

        Entrance:
            category: arXiv category, e.g. "cs.AI"
            keywords: search keywords, e.g. ["deep learning", "transform"]

        Output:
            list of raw dicts (one per <entry> of the Atom flow), of the form:
                {
                    "id": "http://arxiv.org/abs/2411.18583v1",
                    "title": "...",
                    "summary": "<paper abstract>",
                    "published": "2025-11-27T18:59:59Z",
                    "authors": ["Alice Martin", "Bob Chen"],
                    "link": "http://arxiv.org/abs/2411.18583v1",
                }
        """
        # ======= Construction of the search query ========
        # e.g. "cat:cs.AI AND (all:deep learning OR all:transformer)"
        kw_query = " OR ".join(f"all:{kw}" for kw in keywords)
        log.info("[arXiv] topic %s — %d keywords (start=%d)", category, len(keywords), start)

        params = {
            "search_query": f"cat:{category} AND ({kw_query})",
            "sortBy": "lastUpdatedDate",
            "sortOrder": "descending",
            "start": start,
            "max_results": self.settings.sources.arxiv_max_results,
        }

        # ======= Requête HTTP vers arXiv ========
        response = httpx.get(
            self.settings.sources.arXiv_base_url,
            params=params,
            timeout=15.0,
            follow_redirects=True,
        )

        response.raise_for_status()
        log.debug("HTTP %s — %d octets reçus", response.status_code, len(response.content))

        # ======= Parsing du XML ========
        raw_entries = self._xml_to_raw_entries(response.content)
        log.info("  → %d articles récupérés", len(raw_entries))

        return [self._entry_to_dict(entry) for entry in raw_entries]


    def _xml_to_raw_entries(self, content: bytes) -> list[etree._Element]:
        """
        Parses the Atom XML returned by arXiv and returns a list of <entry> elements.

        Entrance:
            content: raw body of the HTTP response (bytes), an Atom XML document
            whose root <feed> contains one <entry> element per article.

        Output:
            lxml <entry> element list (empty if the feed contains no articles).
        """
        root = etree.fromstring(content)
        return root.findall(_tag("entry"))


    def _entry_to_dict(self, entry: etree._Element) -> dict[str, Any]:
        """
        Converts an <entry> element to a dictionary.

        Entrance:
            entry: lxml <entry> element of the Atom arXiv feed

        Output:
            raw dict {id, title, summary, published, updated, authors, link} —
            see fetch_articles for a complete example. Missing text fields
            of XML are ""; authors/link are extracted from sub-elements
            <author><name> and <link rel="alternate">.
        """

        def text(name: str) -> str:
            
            el = entry.find(_tag(name))
            return el.text.strip() if el is not None and el.text else ""

        authors = []
        for author in entry.findall(_tag("author")):
            name_el = author.find(_tag("name"))
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        link = ""
        for el in entry.findall(_tag("link")):
            if el.get("rel") == "alternate":
                link = el.get("href", "")
                break

        return {
            "id": text("id"),
            "title": text("title"),
            "summary": text("summary"),
            "published": text("published"),
            "updated": text("updated"),
            "authors": authors,
            "link": link,
        }


    def normalize_article(self, article: dict[str, Any]) -> ArXivArticle:
        """
        Normalizes data from an arXiv article.
         -Extract the arXiv ID from the URL (ex: "2411.18583v1")
         -Converts publish date to datetime object

        Entrance:
            article: raw dict produced by _entry_to_dict (see fetch_articles
            for the exact form).

        Output:
            ArXivArticle of the form:
                reference="2411.18583v1", title="...", source="arXiv",
                published_date=datetime|None, content="<abstract>",
                url="http://arxiv.org/abs/2411.18583v1",
                authors=["Alice Martin", ...]

        tags and keywords are left empty: it is the review agent who
        provides information based on the content.
        """
        arxiv_id = article["id"].split("/abs/")[-1]  # ex: "2411.18583v1"

        published = article.get("published", "")
        date = datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None

        updated = article.get("updated", "")
        updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None

        return ArXivArticle(
            reference=arxiv_id,
            title=article["title"],
            source="arXiv",
            published_date=date,
            updated_date=updated_dt,
            content=article["summary"],
            url=article["link"],
            authors=article["authors"],
        )

    def run(self) -> list[ArXivArticle]:
        """
        Retrieves and normalizes arXiv articles for all configured topics,
        excluding those published before `arxiv_min_year`.

        Entrance:
            none — the topics come from the config
            (settings.sources.arXiv_topics, ArXivTopic list {name, keywords}).

        Output:
            list of normalized ArXivArticles (see normalize_article for the form),
            all topics combined, filtered by year of publication.

        Note on filtering by date:
        The filter is applied here rather than in the API request because the endpoint
        arXiv Atom does not expose a filter parameter by publication date.
        The `submittedDate` parameter filters on the initial submission date, which can
        diverge from `published` (revisions, late updates). Filter after
        reception guarantees consistent behavior regardless of history
        of submission.
        """
        log.info("Début ingestion arXiv — %d topic(s)", len(self.settings.sources.arXiv_topics))
        min_year = self.settings.sources.arxiv_min_year
        page_size = self.settings.sources.arxiv_max_results
        max_pages = self.settings.sources.arxiv_max_pages
        # Incremental watermark: the most recent <updated> date already in the database.
        # None at the very first run (empty base) → we paginate up to the max_pages ceiling.
        watermark = get_watermark("arXiv", "updated_date")
        results: list[ArXivArticle] = []

        for topic in self.settings.sources.arXiv_topics:
            for page in range(max_pages):
                # Fault tolerance: a network failure (frequent arXiv timeout on the
                # pagination) should not lose everything. We keep the pages already obtained
                # and we stop this topic properly.
                try:
                    raw_entries = self.fetch_articles(
                        topic.name, topic.keywords, start=page * page_size
                    )
                except Exception as e:
                    log.warning(
                        "[arXiv] page start=%d échouée (%s) — arrêt du topic %s",
                        page * page_size,
                        e,
                        topic.name,
                    )
                    break
                if not raw_entries:
                    break  # plus de résultats pour ce topic

                reached_watermark = False
                for raw in raw_entries:
                    article = self.normalize_article(raw)
                    # Flow sorted by lastUpdatedDate descending: as soon as we reach a
                    # article ≤ watermark, everything else is already known → we stop.
                    if (
                        watermark is not None
                        and article.updated_date is not None
                        and article.updated_date <= watermark
                    ):
                        reached_watermark = True
                        break
                    if article.published_date is not None and article.published_date.year < min_year:
                        continue
                    results.append(article)

                if reached_watermark:
                    break  # catch-up finished, no need to page further
                if page + 1 < max_pages:
                    time.sleep(self.page_delay)  # courtesy to the arXiv API

        log.info("  → %d articles retenus (filtre année ≥ %d)", len(results), min_year)
        return results
