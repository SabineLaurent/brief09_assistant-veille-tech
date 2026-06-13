"""
Orchestrateur des sources FROIDES → SQLite.

Un seul point d'entrée : `python -m app.ingest.ingester`.
Quand on le lance, il collecte TOUTES les sources froides (arXiv, TLDR, RSS) et
les envoie en base SQLite via `upsert_article` (idempotent).

Pourquoi ce fichier :
  - Chaque ingester (arXiv, TLDR, RSS) sait déjà se lancer seul (bloc __main__),
    mais cela oblige à enchaîner 3 commandes pour une collecte complète.
  - Ici on factorise le « rituel » commun (upsert + export CSV + log) dans une
    seule fonction `_persist`, appelée par chaque source (DRY).
  - `run_all` est TOLÉRANT AUX PANNES : si une source échoue (réseau, parsing…),
    on logue l'erreur et on passe à la suivante — même philosophie que
    `chat.handle_chat` / `retrieval.retrieve` (« pipeline dégradable »).

Ce fichier n'enlève rien : les blocs __main__ de chaque ingester continuent de
fonctionner pour lancer UNE source isolée. `ingester.py` les chapeaute.
"""

from __future__ import annotations

import logging

from app.data.article_store import count_articles, upsert_article
from app.data.csv_exporter import export_to_csv
from app.data.migrate import init_db
from app.ingest.arXiv_api import ArXivApiIngester
from app.ingest.article_models import Article
from app.ingest.rss_feed import RssFeedIngester
from app.ingest.tldr_scraper import TldrScraper

log = logging.getLogger(__name__)


def _persist(articles: list[Article], source_name: str) -> int:
    """Le rituel commun à toutes les sources : persiste en base + log CSV.

    Entrée :
        articles    : liste d'Article pydantic produite par un ingester.
        source_name : nom court de la source ("arxiv", "tldr", "rss"),
                      utilisé pour nommer le CSV horodaté.

    Sortie :
        nombre d'articles RÉELLEMENT insérés (upsert idempotent : un article
        déjà connu via sa `reference` n'est pas recompté).
    """
    if not articles:
        log.info("[%s] aucun article récupéré.", source_name)
        return 0

    inserted = sum(upsert_article(a.model_dump()) for a in articles)
    csv_path = export_to_csv([a.model_dump() for a in articles], source_name=source_name)
    log.info(
        "[%s] %d récupéré(s), %d nouveau(x) inséré(s) — CSV : %s",
        source_name,
        len(articles),
        inserted,
        csv_path,
    )
    return inserted


def _ingest_arxiv() -> int:
    """arXiv : l'ingester gère son watermark en interne, on appelle juste run()."""
    articles = ArXivApiIngester().run()
    return _persist(articles, "arxiv")


def _ingest_tldr() -> int:
    """TLDR : run_incremental() encapsule le calcul du watermark (cf. tldr_scraper)."""
    articles = TldrScraper().run_incremental()
    return _persist(articles, "tldr")


def _ingest_rss() -> int:
    """RSS : l'ingester cape par flux en interne, on appelle juste run()."""
    articles = RssFeedIngester().run()
    return _persist(articles, "rss")


def run_all() -> None:
    """Collecte TOUTES les sources froides → SQLite, en mode tolérant aux pannes.

    Chaque source est isolée dans un try/except : une source qui plante est
    loggée puis ignorée, les autres continuent. À la fin, on affiche le total
    en base.
    """
    # S'assure que le schéma SQLite existe (idempotent : CREATE TABLE IF NOT
    # EXISTS). Rend l'orchestrateur autonome — pas besoin de `make migrate` avant.
    init_db()

    sources = [
        ("arxiv", _ingest_arxiv),
        ("tldr", _ingest_tldr),
        ("rss", _ingest_rss),
    ]

    total_inserted = 0
    for name, fn in sources:
        try:
            total_inserted += fn()
        except Exception as e:  # noqa: BLE001 — on veut vraiment tout attraper ici
            log.error("[%s] source en échec — %s", name, e)

    log.info("Ingestion froide terminée : %d nouveau(x) article(s) au total.", total_inserted)
    log.info("  → La base contient maintenant %d entrées.", count_articles())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_all()
