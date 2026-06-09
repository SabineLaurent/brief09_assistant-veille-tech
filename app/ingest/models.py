"""
Modèle de base partagé par toutes les sources d'ingestion.

Pourquoi Pydantic BaseModel et pas @dataclass :
  - Le CLI appelle `a.model_dump()` pour persister les articles en base SQLite.
    Cette méthode est propre à Pydantic — un @dataclass nécessiterait
    `dataclasses.asdict()` et des modifications dans le CLI.
  - Pydantic valide les types à l'instanciation (ex: published_date: datetime | None).
  - Tout le reste du pipeline utilise déjà Pydantic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Article(BaseModel):
    reference: str
    title: str
    source: str
    published_date: datetime | None
    content: str
    url: str
    tags: list[str]
    authors: list[str]
    # Field(default_factory=datetime.now) et pas = datetime.now() :
    # avec = datetime.now(), la date serait calculée une seule fois au chargement
    # du module — tous les articles auraient la même date. default_factory
    # recalcule datetime.now() à chaque nouvel Article créé.
    ingested_at: datetime = Field(default_factory=datetime.now)
    indexed_at: datetime | None = None

    def to_chroma_metadata(self) -> dict[str, str]:
        return {
            "title": self.title,
            "source": self.source,
            "date": self.published_date.isoformat() if self.published_date else "",
            "url": self.url,
            "tags": "|".join(self.tags),
            "authors": "|".join(self.authors),
        }

    def to_indexable(self) -> dict[str, Any]:
        return {
            "id": self.reference,
            "content": self.content,
            "metadata": self.to_chroma_metadata(),
        }
