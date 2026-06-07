from __future__ import annotations

import logging

import typer

from app.ingest.cleaning import chunk
from app.rag.chroma_client import get_collection
from app.rag.retrieval import embed

log = logging.getLogger(__name__)

app = typer.Typer(help="Ingestion CLI for the veille tech index.")


def _index_articles(articles: list[dict]) -> int:
    collection = get_collection()
    total_chunks = 0

    for article in articles:
        try:
            chunks = chunk(article.get("content", ""))
            if not chunks:
                continue

            article_id = article["id"]
            tags = article.get("tags", [])
            tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)

            metadata = {
                "title": article.get("title", ""),
                "source": article.get("source", ""),
                "date": article.get("date") or "",
                "url": article.get("url", ""),
                "tags": tags_str,
            }

            ids = [f"{article_id}::{i}" for i in range(len(chunks))]
            embeddings = [embed(c) for c in chunks]
            metadatas = [metadata] * len(chunks)

            collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
            total_chunks += len(chunks)

        except Exception:
            log.warning("Échec indexation article %s", article.get("id", "?"))

    return total_chunks


@app.command()
def news(topics: list[str] = typer.Option(..., "--topic", "-t", help="Topic to query.")) -> None:
    raise NotImplementedError


@app.command()
def scrape(urls: list[str] = typer.Option(..., "--url", "-u", help="URL to scrape.")) -> None:
    raise NotImplementedError


if __name__ == "__main__":
    app()
