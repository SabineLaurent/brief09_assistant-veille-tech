from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from lxml import etree
from app.config import Settings, get_settings
from app.ingest.models import Article


class ArXivArticle(Article):
    pass


log = logging.getLogger(__name__)

_ATOM = "http://www.w3.org/2005/Atom"


def _tag(name: str) -> str:
    # L'API arXiv renvoie du XML Atom : chaque balise est préfixée par l'espace
    # de noms. Cette fonction évite de répéter "{http://...}title" partout.
    return f"{{{_ATOM}}}{name}"


@dataclass
class ArXivApiIngester:
    settings: Settings | None = None

    def __post_init__(self) -> None:
        if self.settings is None:
            self.settings = get_settings()

    def fetch_articles(self, category: str, keywords: list[str]) -> list[dict[str, Any]]:
        """
        Interroge l'API arXiv pour une catégorie et une liste de mots-clés (combinés en OR).

        Entrée :
            category : catégorie arXiv, ex. "cs.AI"
            keywords : mots-clés de recherche, ex. ["deep learning", "transformer"]

        Sortie :
            liste de dicts bruts (un par <entry> du flux Atom), de la forme :
                {
                    "id": "http://arxiv.org/abs/2411.18583v1",
                    "title": "...",
                    "summary": "<abstract du papier>",
                    "published": "2025-11-27T18:59:59Z",
                    "authors": ["Alice Martin", "Bob Chen"],
                    "link": "http://arxiv.org/abs/2411.18583v1",
                    "category": "cs.AI",          # recopié depuis l'entrée
                    "keywords": ["deep learning"]  # recopié depuis l'entrée
                }
        """
        # ======= Construction de la requête de recherche ========
        # ex. "cat:cs.AI AND (all:deep learning OR all:transformer)"
        kw_query = " OR ".join(f"all:{kw}" for kw in keywords)
        log.info("[arXiv] topic %s — %d keywords", category, len(keywords))

        params = {
            "search_query": f"cat:{category} AND ({kw_query})",
            "sortBy": "lastUpdatedDate",
            "sortOrder": "descending",
            "start": 0,
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

        return [self._entry_to_dict(entry, category, keywords) for entry in raw_entries]


    def _xml_to_raw_entries(self, content: bytes) -> list[etree._Element]:
        """
        Parse le XML Atom renvoyé par arXiv et retourne une liste d'éléments <entry>.

        Entrée :
            content : corps brut de la réponse HTTP (bytes), un document XML Atom
            dont la racine <feed> contient un élément <entry> par article.

        Sortie :
            liste d'éléments lxml <entry> (vide si le flux ne contient aucun article).
        """
        root = etree.fromstring(content)
        return root.findall(_tag("entry"))


    def _entry_to_dict(
        self, entry: etree._Element, category: str, keywords: list[str]
    ) -> dict[str, Any]:
        """
        Convertit un élément <entry> en dictionnaire.

        Entrée :
            entry : élément lxml <entry> du flux Atom arXiv
            category : catégorie arXiv de la requête d'origine, ex. "cs.AI"
            keywords : mots-clés de la requête d'origine, ex. ["deep learning"]

        Sortie :
            dict brut {id, title, summary, published, authors, link, category,
            keywords} — voir fetch_articles pour un exemple complet. Les champs
            texte absents du XML valent "" ; authors/link sont extraits des
            sous-éléments <author><name> et <link rel="alternate">.
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
            "authors": authors,
            "link": link,
            "category": category,
            "keywords": keywords,
        }


    def normalize_article(self, article: dict[str, Any]) -> ArXivArticle:
        """
        Normalise les données d'un article arXiv.
         - Extrait l'ID arXiv de l'URL (ex: "2411.18583v1")
         - Convertit la date de publication en objet datetime
         - Construit une liste de tags à partir de la catégorie + les mots-clés

        Entrée :
            article : dict brut produit par _entry_to_dict (voir fetch_articles
            pour la forme exacte).

        Sortie :
            ArXivArticle de la forme :
                reference="2411.18583v1", title="...", source="arXiv",
                published_date=datetime|None, content="<abstract>",
                url="http://arxiv.org/abs/2411.18583v1",
                tags=["cs.AI", "deep learning", ...], authors=["Alice Martin", ...]
        """
        arxiv_id = article["id"].split("/abs/")[-1]  # ex: "2411.18583v1"

        published = article.get("published", "")
        date = datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None

        return ArXivArticle(
            reference=arxiv_id,
            title=article["title"],
            source="arXiv",
            published_date=date,
            content=article["summary"],
            url=article["link"],
            tags=[article["category"]] + article["keywords"],
            authors=article["authors"],
        )

    def run(self) -> list[ArXivArticle]:
        """
        Récupère et normalise les articles arXiv pour tous les topics configurés,
        en excluant ceux publiés avant `arxiv_min_year`.

        Entrée :
            aucune — les topics viennent de la config
            (settings.sources.arXiv_topics, liste d'ArXivTopic {category, keywords}).

        Sortie :
            liste d'ArXivArticle normalisés (voir normalize_article pour la forme),
            tous topics confondus, filtrés par année de publication.

        Remarque sur le filtrage par date :
        Le filtre est appliqué ici plutôt que dans la requête API car l'endpoint
        arXiv Atom n'expose pas de paramètre de filtre par date de publication.
        Le paramètre `submittedDate` filtre sur la date de dépôt initiale, qui peut
        diverger de `published` (révisions, mises à jour tardives). Filtrer après
        réception garantit un comportement cohérent quelle que soit l'historique
        de la soumission.
        """
        log.info("Début ingestion arXiv — %d topic(s)", len(self.settings.sources.arXiv_topics))
        min_year = self.settings.sources.arxiv_min_year
        results = []
        for topic in self.settings.sources.arXiv_topics:
            for raw in self.fetch_articles(topic.category, topic.keywords):
                article = self.normalize_article(raw)
                if article.published_date is not None and article.published_date.year < min_year:
                    log.debug(
                        "Article ignoré (année %d < %d) : %s",
                        article.published_date.year,
                        min_year,
                        article.url,
                    )
                    continue
                results.append(article)
        log.info("  → %d articles retenus (filtre année ≥ %d)", len(results), min_year)
        return results


if __name__ == "__main__":
    """
    Script d'ingestion autonome pour arXiv, à lancer ponctuellement pendant le développement.
     - Récupère les articles correspondant aux topics configurés
     - Normalise les données
     - Sauvegarde en base via la fonction upsert_article (idempotent)
     - Exporte les articles de cette session dans un CSV horodaté
     - Affiche un résumé dans la console
     - À terme, ce script sera remplacé par une tâche planifiée (cron)
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.data.article_store import count_articles, upsert_article
    from app.data.csv_exporter import export_to_csv

    ingester = ArXivApiIngester()
    articles = ingester.run()

    inserted = sum(upsert_article(a.model_dump()) for a in articles)
    csv_path = export_to_csv([a.model_dump() for a in articles], source_name="arxiv")

    log.info("  → %d nouveaux articles ajoutés à la base de données", inserted)
    log.info("  → Log des entrées de cette session dans le csv : %s", csv_path)
    log.info("Ingestion arXiv terminée.")
    log.info("  → La base de données de veille contient maintenant %d entrées", count_articles())
