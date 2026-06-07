from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import get_settings


def upsert_article(article: dict[str, Any], db_path: str | None = None) -> None:
    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO article
                (reference, title, source, published_date, content, url, tags, authors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article["reference"],
                article["title"],
                article["source"],
                article["published_date"].isoformat() if article["published_date"] else None,
                article["content"],
                article["url"],
                json.dumps(article["tags"]),
                json.dumps(article["authors"]),
            ),
        )
