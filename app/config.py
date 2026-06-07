from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ArXivTopic(BaseModel):
    category: str
    keywords: list[str]

class WatchedRepo(BaseModel):
    owner: str
    repo: str
    topic: str | None = None


class Sources(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    news_api_key: str = ""
    news_api_base_url: str = "https://newsapi.org/v2"

    arXiv_base_url: str = "https://export.arxiv.org/api/query"
    arXiv_topics: list[ArXivTopic] = Field(default_factory=list)
    arxiv_max_results: int
    arxiv_min_year: int

    github_api_url: str = "https://api.github.com"
    github_releases_token: str = ""
    github_watched_repos: list[WatchedRepo] = Field(default_factory=list)


class Settings(BaseSettings):
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
