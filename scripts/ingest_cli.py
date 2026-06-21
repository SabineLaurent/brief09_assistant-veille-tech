from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from app.data.article_store import (
    count_articles,
    read_ingested_articles,
    upsert_article,
)
from app.data.csv_exporter import export_to_csv
from app.indexing.indexer import index_articles
from app.ingest.article_models import Article
from app.ingest.sources_ingesters.arXiv_api import ArXivApiIngester
from app.ingest.sources_ingesters.rss_feed import RssFeedIngester
from app.ingest.sources_ingesters.tldr_scraper import TldrScraper

console = Console()

# Route program traces (e.g. the scraper's per-edition lines) through rich so the
# source currently being ingested shows up, colored, during the command.
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, show_path=False, show_time=False)],
)
# httpx/httpcore log every HTTP request at INFO; silence them so the CLI shows
# only our own per-source lines and the summary table.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = typer.Typer(help="Ingestion CLI for the veille tech index.")


def _persist_and_report(articles: Sequence[Article], source_name: str) -> None:
    """Upsert each article, log a session CSV, and print a per-source summary table.

    source_name is the session label used for the CSV filename (e.g. "arxiv",
    "tldr", "rss"), not the per-article source.
    """
    dumps = [a.model_dump() for a in articles]
    fetched: Counter[str] = Counter(d["source"] for d in dumps)
    inserted: Counter[str] = Counter()
    for d in dumps:
        if upsert_article(d):
            inserted[d["source"]] += 1

    table = Table(title="Articles récupérés par source")
    table.add_column("Source", style="cyan", no_wrap=True)
    table.add_column("Récupérés", justify="right")
    table.add_column("Nouveaux", justify="right", style="green")
    for source in sorted(fetched):
        table.add_row(source, str(fetched[source]), str(inserted[source]))
    table.add_section()
    table.add_row("Total", str(sum(fetched.values())), str(sum(inserted.values())))
    console.print(table)

    csv_path = export_to_csv(dumps, source_name)
    if csv_path:
        console.print(f"Log CSV de la session : {csv_path}")
    console.print(f"Base : {count_articles()} articles au total.")


@app.command()
def fetch() -> None:
    """Récupère les articles arXiv et les sauvegarde en base (SQLite)."""
    _persist_and_report(ArXivApiIngester().run(), "arxiv")


@app.command()
def index() -> None:
    """Indexe dans Chroma les articles SQLite avec status='ingested'."""
    articles = read_ingested_articles()
    if not articles:
        typer.echo("Aucun article à indexer (status='ingested' introuvable).")
        raise typer.Exit()
    result = index_articles(articles)
    typer.echo(
        f"{result.indexed} indexés, {result.held} bloqués (titre/contenu insuffisant), "
        f"{result.errors} en erreur → {result.chunks} chunks dans Chroma."
    )


@app.command()
def tldr() -> None:
    """Scrappe les newsletters TLDR manquantes (depuis la dernière ingestion) et sauvegarde en base (SQLite)."""
    _persist_and_report(TldrScraper().run_incremental(), "tldr")


@app.command()
def rss() -> None:
    """Ingère les flux RSS configurés (blogs officiels) et sauvegarde en base (SQLite)."""
    _persist_and_report(RssFeedIngester().run(), "rss")


@app.command()
def news(topics: list[str] = typer.Option(..., "--topic", "-t", help="Topic to query.")) -> None:
    raise NotImplementedError


@app.command()
def scrape(urls: list[str] = typer.Option(..., "--url", "-u", help="URL to scrape.")) -> None:
    raise NotImplementedError


if __name__ == "__main__":
    app()
