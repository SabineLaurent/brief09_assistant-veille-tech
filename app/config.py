from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ArXivTopic(BaseModel):
    """
    Représente un sujet de recherche à surveiller sur arXiv.
    """

    category: str
    keywords: list[str]


class WatchedRepo(BaseModel):
    """
    Représente un dépôt GitHub à surveiller pour les nouvelles releases.
    """

    owner: str
    repo: str
    topic: str | None = None


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
    arxiv_max_results: int = 25
    arxiv_min_year: int = 2025

    # ====== TLDR.tech ======
    tldr_base_url: str = "https://tldr.tech"
    # Cas « base vide » (aucune édition TLDR encore ingérée) : date de départ de
    # l'ingestion. Sinon on repart de la dernière date connue + 1 jour.
    tldr_start_date: str = "2026-06-01"

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
