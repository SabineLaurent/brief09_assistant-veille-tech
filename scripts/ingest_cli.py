from __future__ import annotations

import typer

from app.data.article_store import count_articles, read_ingested_articles, upsert_article
from app.indexing.indexer import index_articles
from app.ingest.arXiv_api import ArXivApiIngester

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
    total_chunks = index_articles(articles)
    typer.echo(f"{len(articles)} articles indexés → {total_chunks} chunks dans Chroma.")


@app.command()
def news(topics: list[str] = typer.Option(..., "--topic", "-t", help="Topic to query.")) -> None:
    raise NotImplementedError


@app.command()
def scrape(urls: list[str] = typer.Option(..., "--url", "-u", help="URL to scrape.")) -> None:
    raise NotImplementedError


if __name__ == "__main__":
    app()
