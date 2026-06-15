from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from app.config import get_settings

# Colonnes de date autorisées comme watermark. Un nom de colonne ne peut PAS être
# passé en paramètre SQL (`?` ne vaut que pour des valeurs, pas des identifiants) :
# on le valide donc contre cette liste blanche pour écarter tout risque d'injection.
#   - published_date : watermark TLDR (date d'édition)
#   - updated_date   : watermark arXiv (date <updated>) — colonne ajoutée à l'étape
#                      arXiv, cf. docs/steps/11-ingestion-incrementale-watermark.md
_WATERMARK_FIELDS = ("published_date", "updated_date")


def upsert_article(article: dict[str, Any], db_path: str | None = None) -> bool:
    """
    Insère l'article. Retourne True si inséré, False si déjà présent.
    """
    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO article
                (reference, title, source, published_date, updated_date, content, url, tags, keywords, authors, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article["reference"],
                article["title"],
                article["source"],
                article["published_date"].isoformat() if article["published_date"] else None,
                article["updated_date"].isoformat() if article.get("updated_date") else None,
                article["content"],
                article["url"],
                json.dumps(article["tags"], ensure_ascii=False),
                json.dumps(article.get("keywords", []), ensure_ascii=False),
                json.dumps(article["authors"], ensure_ascii=False),
                article["ingested_at"].isoformat() if article["ingested_at"] else None,
            ),
        )
        return cursor.rowcount == 1


def count_articles(db_path: str | None = None) -> int:
    """
    Retourne le nombre total d'articles en base.
    """
    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM article").fetchone()
        return row[0]


def get_watermark(source: str, date_field: str, db_path: str | None = None) -> datetime | None:
    """
    Retourne la date la plus récente connue en base pour une source — le « high
    watermark ». Sert à borner la prochaine ingestion (ne récupérer que ce qui est
    plus récent). Voir docs/steps/11-ingestion-incrementale-watermark.md.

    Entrée :
        source : valeur de la colonne `source`, ex. "tldr.tech" ou "arXiv".
        date_field : colonne de date à comparer — "published_date" (TLDR) ou
            "updated_date" (arXiv). Validée contre _WATERMARK_FIELDS.
        db_path : chemin de la base (défaut : settings.ingest_db_path).

    Sortie :
        datetime du plus récent article de cette source, ou None si la source n'a
        encore aucun article (cas « base vide » → l'appelant choisit une date de
        départ par défaut).

    MAX() sur le TEXT ISO 8601 suffit : ce format se trie alphabétiquement dans le
    même ordre que chronologiquement.
    """
    if date_field not in _WATERMARK_FIELDS:
        raise ValueError(f"date_field invalide : {date_field!r} (attendu : {_WATERMARK_FIELDS})")

    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            f"SELECT MAX({date_field}) FROM article WHERE source = ?",
            (source,),
        ).fetchone()

    value = row[0] if row else None
    return datetime.fromisoformat(value) if value else None


def update_article_status(reference: str, status: str, db_path: str | None = None) -> None:
    """
    Met à jour le status d'un article ('ingested', 'indexed', 'error').
    Renseigne indexed_at automatiquement quand status='indexed'.
    """
    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        if status == "indexed":
            conn.execute(
                "UPDATE article SET status = ?, indexed_at = CURRENT_TIMESTAMP WHERE reference = ?",
                (status, reference),
            )
        else:
            conn.execute(
                "UPDATE article SET status = ? WHERE reference = ?",
                (status, reference),
            )


def read_ingested_articles(db_path: str | None = None) -> list[dict]:
    """
    Retourne tous les articles avec status='ingested' (non encore indexés).
    """
    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM article WHERE status = 'ingested'").fetchall()
        return [dict(row) for row in rows]


def read_unreviewed_articles(db_path: str | None = None) -> list[dict]:
    """
    Retourne les articles que l'agent de review n'a pas encore traités
    (llm_reviewed_at IS NULL), en excluant ceux déjà rejetés définitivement.

    `llm_reviewed_at` est le signal de lecture par l'agent de completion des enregistrements d'articles:
    NULL = pas encore complété. On teste avec IS NULL, car en SQL rien n'est « = NULL », pas même NULL.

    Le filtre `status != 'rejected'` évite de re-traiter en boucle un article rejeté
    (statut terminal) : un reject ne touche pas `llm_reviewed_at`, donc sans ce garde-fou
    l'article ressortirait à chaque passe. COALESCE protège un `status` éventuellement NULL.
    """
    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM article "
            "WHERE llm_reviewed_at IS NULL AND COALESCE(status, '') != 'rejected'"
        ).fetchall()
        return [dict(row) for row in rows]


def update_article_records_with_llm_reviews(
    reference: str,
    keywords: list[str],
    tags: list[str],
    generated_summary: str | None = None,
    title: str | None = None,
    db_path: str | None = None,
) -> None:
    """
    Write the review agent's result and stamp llm_reviewed_at.

    keywords/tags are always written (stored as JSON, like upsert_article). The other
    fields are written ONLY when the review recovered them, so a faithful source value
    is never overwritten by a blank:
      - generated_summary → column `content` (only to fill an empty/thin content)
      - title             → column `title`   (only to replace a junk title recovered
                            from the source page)

    The SET clause is built dynamically from the provided fields: the column names are
    fixed literals (never user input) and every value stays parameterized (`?`), so this
    is injection-safe.
    """
    path = db_path or get_settings().ingest_db_path

    columns = ["keywords = ?", "tags = ?"]
    values: list[Any] = [
        json.dumps(keywords, ensure_ascii=False),
        json.dumps(tags, ensure_ascii=False),
    ]
    if generated_summary is not None:
        columns.append("content = ?")
        values.append(generated_summary)
    if title is not None:
        columns.append("title = ?")
        values.append(title)
    columns.append("llm_reviewed_at = CURRENT_TIMESTAMP")

    values.append(reference)
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"UPDATE article SET {', '.join(columns)} WHERE reference = ?",
            values,
        )
