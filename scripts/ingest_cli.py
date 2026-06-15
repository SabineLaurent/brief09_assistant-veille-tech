from __future__ import annotations

from datetime import date

import typer

from app.config import get_settings
from app.data.article_store import (
    count_articles,
    get_watermark,
    read_ingested_articles,
    upsert_article,
)
from app.indexing.indexer import index_articles
from app.ingest.sources_ingesters.arXiv_api import ArXivApiIngester
from app.ingest.sources_ingesters.tldr_scraper import TldrScraper, missing_edition_dates

app = typer.Typer(help="Ingestion CLI for the veille tech index.")


@app.command()
def fetch() -> None:
    """Récupère les articles arXiv et les sauvegarde en base (SQLite)."""
    articles = ArXivApiIngester().run()
    inserted = sum(upsert_article(a.model_dump()) for a in articles)
    typer.echo(f"{len(articles)} articles récupérés, {inserted} nouveaux insérés.")
    typer.echo(f"Base : {count_articles()} articles au total.")


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
def tldr(
    editions: list[str] = typer.Option(["tech", "webdev", "ai"], "--edition", "-e", help="TLDR edition (tech, webdev, ai…)"),
) -> None:
    """Scrappe les newsletters TLDR manquantes (depuis la dernière ingestion) et sauvegarde en base (SQLite)."""
    settings = get_settings()
    watermark = get_watermark("tldr.tech", "published_date")
    start_date = date.fromisoformat(settings.sources.tldr_start_date)
    dates = missing_edition_dates(watermark, date.today(), start_date)
    if not dates:
        typer.echo("TLDR déjà à jour, rien à scraper.")
        raise typer.Exit()

    scraper = TldrScraper()
    urls = [url for d in dates for url in scraper.build_urls(editions, d)]
    typer.echo(f"{len(dates)} date(s) à scraper ({dates[0]} → {dates[-1]}), {len(urls)} URL(s).")
    articles = scraper.run(urls)
    inserted = sum(upsert_article(a.model_dump()) for a in articles)
    typer.echo(f"{len(articles)} articles récupérés, {inserted} nouveaux insérés.")
    typer.echo(f"Base : {count_articles()} articles au total.")


@app.command()
def news(topics: list[str] = typer.Option(..., "--topic", "-t", help="Topic to query.")) -> None:
    raise NotImplementedError


@app.command()
def scrape(urls: list[str] = typer.Option(..., "--url", "-u", help="URL to scrape.")) -> None:
    raise NotImplementedError


if __name__ == "__main__":
    app()
