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

    # Date de dernière révision côté source (arXiv <updated>). None pour les sources
    # sans notion de révision (TLDR). Sert de watermark à l'ingestion arXiv (Option A) :
    # voir docs/steps/12-ingestion-incrementale-watermark.md.
    updated_date: datetime | None = None

    content: str

    url: str

    # Catégorie(s) thématique(s) de l'article, choisie(s) dans le vocabulaire contrôlé.
    # Vides à l'ingestion ; remplies ensuite par l'agent de review.
    tags: list[str] = Field(default_factory=list)

    # Mots-clés de contenu (sujet réel de l'article), distincts des tags (classification).
    # Vides à l'ingestion ; remplis ensuite par l'agent de review.
    keywords: list[str] = Field(default_factory=list)

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
            # Chroma n'accepte que des scalaires en métadonnée (pas de liste) : on
            # encode en chaîne séparée par ", " — relue côté llm._split_tags.
            "tags": ", ".join(self.tags),
            "keywords": ", ".join(self.keywords),
            "authors": ", ".join(self.authors),
        }

    def to_indexable(self) -> dict[str, Any]:
        return {
            "id": self.reference,
            "content": self.content,
            "metadata": self.to_chroma_metadata(),
        }


class ArXivArticle(Article):
    pass


class TldrArticle(Article):
    pass


class RssArticle(Article):
    pass
