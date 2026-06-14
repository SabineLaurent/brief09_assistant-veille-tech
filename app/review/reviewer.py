from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.ingest.scraper import Scraper

logger = logging.getLogger(__name__)

# En dessous de ce seuil (en caractères), le content est jugé trop pauvre pour servir
# de base : on scrape la page source et on fait générer un résumé. À ~150 caractères
# (≈ 2-3 phrases), on conserve les brèves déjà exploitables et on ne scrape que les
# articles vraiment maigres (titre + lien, ou extrait d'une phrase).
MIN_CONTENT_CHARS = 150


# ── Schémas de sortie structurée ──────────────────────────────────────────────
# Le LLM est contraint de répondre au format de l'un de ces modèles (JSON schema).
# Deux schémas distincts pour ne payer des tokens de résumé que lorsque le content
# est vide ou trop court.


class _Review(BaseModel):
    """
    Sortie lorsque le content est suffisant : on annote sans résumer.
    """

    keywords: list[str] = Field(description="content keywords (the article's actual subject)")
    topics: list[str] = Field(description="topics chosen from the allowed list")


class _ReviewWithSummary(BaseModel):
    """
    Sortie lorsque le content est vide ou trop court : on résume d'abord, puis on annote.

    `summary` est volontairement le premier champ : en sortie structurée, le modèle
    remplit les champs dans l'ordre du schéma. Rédiger le résumé avant les keywords
    fait découler ces derniers du résumé. C'est pourquoi ce modèle n'hérite pas de
    `_Review` (deux champs sont dupliqués, à dessein).
    """

    summary: str = Field(description="summary of the article, abstract level, a few sentences")
    keywords: list[str] = Field(description="keywords reflecting the central subject of the summary")
    topics: list[str] = Field(description="topics chosen from the allowed list")


@dataclass
class ReviewResult:
    """
    Résultat de travail d'un article, consommé par le runner pour la persistance.
    """

    keywords: list[str]
    tags: list[str]  # topics retenus, filtrés contre available_topics
    generated_summary: str | None = None  # renseigné uniquement si le content était vide


@lru_cache(maxsize=1)
def get_mini_agent() -> ChatOpenAI | None:
    """
    Client LLM de l'agent de review (déploiement "mini" dédié, distinct du chat).

    Renvoie None si l'agent n'est pas configuré, ce qui permet une dégradation propre :
    le pipeline reste fonctionnel sans agent.
    """
    settings = get_settings()
    if not settings.azure_ai_mini_agent_endpoint or not settings.azure_ai_mini_agent_api_key:
        logger.info("Mini agent not configured — review skipped")
        return None
    return ChatOpenAI(
        base_url=settings.azure_ai_mini_agent_endpoint,
        api_key=settings.azure_ai_mini_agent_api_key,
        model=settings.azure_ai_mini_agent_model,
        temperature=0.1,  # fidèle, n'invente rien
    )


def _build_system_prompt(available_topics: list[str]) -> str:
    return (
        "You are an English-speaking technology-watch annotation agent for Nauda Palisse.\n"
        "For the given article, produce keywords describing its actual subject, and "
        "one or more topics.\n"
        "Choose topics ONLY from this list: "
        f"{', '.join(available_topics)}.\n"
        "Write the summary (if requested) and the keywords STRICTLY IN ENGLISH, "
        "staying faithful to the article's tone, style and vocabulary.\n"
        "If a summary is requested, write it first (a few sentences, abstract level), "
        "then keywords reflecting its central subject. Stay factual, do not invent anything."
    )


def _resolve_text(content: str, url: str, title: str) -> tuple[str, bool]:
    """
    Détermine le texte à annoter et s'il faut générer un résumé.

    C'est le code, et non le LLM, qui décide quand scraper la page source.
    Retourne (text, needs_summary) :
      - content suffisant     → (content, False)
      - sinon page scrapée OK → (texte de la page, True)
      - sinon                 → (titre, True)
    """
    if len(content) >= MIN_CONTENT_CHARS:
        return content, False

    if url:
        try:
            scraped = Scraper().run([url])
            if scraped and scraped[0].get("content"):
                return scraped[0]["content"], True
        except Exception as exc:  # un échec de scrape ne doit pas interrompre la review
            logger.warning("Scrape failed for %s — %s", url, exc)

    return title, True


def review_article(article: dict) -> ReviewResult | None:
    """
    Annote un article via un appel LLM structuré: keywords + topics (+ résumé si besoin).

    Renvoie None si l'agent n'est pas configuré ou si l'appel LLM échoue. Dans ce cas,
    l'appelant ne marque pas l'article comme traité et celui-ci sera repris lors d'une
    prochaine passe (les échecs réseau ou de quota sont le plus souvent transitoires).
    """
    agent = get_mini_agent()
    if agent is None:
        return None

    settings = get_settings()
    content = article.get("content") or ""
    title = article.get("title") or ""
    url = article.get("url") or ""

    text, needs_summary = _resolve_text(content, url, title)
    schema = _ReviewWithSummary if needs_summary else _Review

    try:
        review = agent.with_structured_output(schema).invoke(
            [
                SystemMessage(content=_build_system_prompt(settings.available_topics)),
                HumanMessage(content=f"Title: {title}\n\nContent:\n{text}"),
            ]
        )
    except Exception as exc:
        logger.warning("Review LLM call failed for %r — %s", article.get("reference"), exc)
        return None

    # La sortie du LLM est une entrée non fiable: on filtre les topics contre le
    # vocabulaire autorisé, même si le prompt les a déjà contraints.
    allowed = set(settings.available_topics)
    tags = [t for t in review.topics if t in allowed]

    return ReviewResult(
        keywords=review.keywords,
        tags=tags,
        generated_summary=review.summary if needs_summary else None,
    )
