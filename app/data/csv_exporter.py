from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

_OUTPUT_DIR = Path("logs/ingest")


def _serialize(value: object) -> str:
    if isinstance(value, list):
        return "|".join(str(v) for v in value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def export_to_csv(articles: list[dict], source_name: str) -> str:
    """
    Exporte une liste d'articles dans un CSV horodaté sous logs/ingest/.
    Retourne le chemin du fichier créé.
    """
    if not articles:
        return ""

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = _OUTPUT_DIR / f"articles_{source_name}_{timestamp}.csv"
    fields = list(articles[0].keys())

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for article in articles:
            writer.writerow({key: _serialize(val) for key, val in article.items()})

    return str(csv_path)
