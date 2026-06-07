from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import get_settings

_SQL = (Path(__file__).parent / "article.sql").read_text()


def init_db(db_path: str | None = None) -> None:
    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        conn.executescript(_SQL)


if __name__ == "__main__":
    init_db()
