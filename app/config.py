from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Source(BaseModel):
    """
    Base commune d'une source à surveiller.
    """

    name: str
    fresh_news: bool = False  # si True, ajoute le tag "New" aux articles

class ArXivTopic(Source):
    """
    Représente un sujet de recherche à surveiller sur arXiv.
    """

    keywords: list[str]

class WatchedRepo(Source):
    """
    Représente un dépôt GitHub à surveiller pour les nouvelles releases.
    """

    owner: str

class RSSFeed(Source):
    """
    Représente un flux RSS à surveiller.
    """

    url: str

class TldrEdition(Source):
    """
    Représente une édition de la newsletter TLDR.tech à surveiller.
    """

class ControlledTopic(BaseModel):
    """
    Topic du vocabulaire contrôlé : son nom et sa définition (gloss).
    Le gloss est injecté dans le prompt de l'agent de review pour guider la classification.
    """

    name: str
    gloss: str | None = None  # définition courte ; None → seul le nom est utilisé

class Sources(BaseSettings):
    """
    Configuration des sources d'information à surveiller (arXiv, GitHub, etc.).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    news_api_key: str = ""
    news_api_base_url: str = "https://newsapi.org/v2"

    # ====== arXiv ======
    arXiv_base_url: str = "https://export.arxiv.org/api/query"
    arXiv_topics: list[ArXivTopic] = Field(default_factory=list)
    arxiv_max_results: int = 2             # taille d'une page (paramètre max_results de l'API)
    arxiv_max_pages: int = 1               # plafond de pages paginées par topic (borne le run à froid)
    arxiv_min_year: int = 2026

    # ====== Flux RSS ======
    rss_feeds: list[RSSFeed] = Field(default_factory=list)
    rss_max_items_per_feed: int = 2
    rss_start_date: str = "2026-06-18"

    # ====== TLDR.tech ======
    tldr_base_url: str = "https://tldr.tech"
    tldr_editions: list[TldrEdition] = Field(default_factory=list)
    # Date de départ de l'ingestion (Cas « base vide » (aucune édition TLDR encore ingérée). Sinon on repart de la dernière date connue + 1 jour.
    tldr_start_date: str = "2026-06-18"

    # ====== GitHub ======
    github_api_url: str = "https://api.github.com"
    github_releases_token: str = ""
    github_watched_repos: list[WatchedRepo] = Field(default_factory=list)


class Settings(BaseSettings):
    """
    Configuration globale de l'application.
     - Chargée à partir du fichier .env (grâce à pydantic-settings).
     - Accessible partout via la fonction get_settings().
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    azure_ai_inference_endpoint: str = ""
    azure_ai_inference_api_key: str = ""
    azure_ai_inference_model: str = "Kimi-K2.6"

    # -------Agent d'enrichissement (résumé + mots-clés + sujet), distinct du chat:
    # endpoint/clé séparés de Kimi (déploiement Foundry dédié, modèle "mini"). Défauts vides → mode dégradé possible (l'agent ne plante pas sans config), comme get_llm().
    azure_ai_mini_agent_endpoint: str = ""
    azure_ai_mini_agent_api_key: str = ""
    azure_ai_mini_agent_model: str = "gpt-5.4-mini"

    # -------Topics disponibles : vocabulaire contrôlé, source de vérité UNIQUE partagée par
    # (1) l'agent de review (cible de classification);
    # (2) l'endpoint /topics (filtres du frontend).
    available_topics: list[ControlledTopic] = Field(
        default_factory=lambda: [
            ControlledTopic(name="AI", gloss="machine learning, models, LLMs, training/inference, AI research and products."),
            ControlledTopic(name="Security", gloss="vulnerabilities, attacks, defense, cryptography, privacy."),
            ControlledTopic(name="Agentic", gloss="autonomous agents, tool-use, multi-agent systems, agent orchestration."),
            ControlledTopic(name="Embedded", gloss="on-device/edge computing, hardware, IoT, tinyML, firmware."),
            ControlledTopic(name="Web", gloss="web development, front-end, back-end, frameworks, web standards."),
            ControlledTopic(name="DevOps", gloss="CI/CD, deployment, monitoring, observability, cloud-native."),
        ]
    )

    chroma_url: str = "http://chromadb:8000"
    chroma_collection: str = "articles"
    embedding_model: str = "intfloat/multilingual-e5-small"

    sources: Sources = Field(default_factory=Sources)
    ingest_db_path: str = "ingest.db"

    backend_port: int = 8000
    frontend_port: int = 3000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
