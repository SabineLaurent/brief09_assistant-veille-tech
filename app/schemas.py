from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class Article(BaseModel):
    id: str
    title: str
    source: str
    date: datetime | None = None
    content: str
    url: HttpUrl | str
    tags: list[str] = Field(default_factory=list)


class ArticleCard(BaseModel):
    title: str
    source: str
    date: str | None = None
    snippet: str
    url: str
    tags: list[str] = Field(default_factory=list)
    # True if the card comes from the live runtime feed (fresh_news), False if it
    # comes from the Chroma index. Lets the frontend distinguish fresh from cold.
    is_fresh_news: bool = False


class Topic(BaseModel):
    slug: str
    label: str


class ChatRequest(BaseModel):
    question: str
    topics: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    cards: list[ArticleCard]
    status: Literal["ok", "empty", "degraded"] = "ok"
