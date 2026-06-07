from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup
from langchain.text_splitter import RecursiveCharacterTextSplitter


def clean_html_to_markdown(html: str) -> str:
    raise NotImplementedError


def dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raise NotImplementedError


def chunk(text: str, max_chars: int = 1200) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=max_chars, chunk_overlap=100)
    return splitter.split_text(text)


def strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup:
    raise NotImplementedError
