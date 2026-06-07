from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


def clean_html_to_markdown(html: str) -> str:
    raise NotImplementedError


def dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raise NotImplementedError


def chunk(text: str, max_chars: int = 1200, overlap_sentences: int = 2) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    chunks = []
    i = 0

    while i < len(sentences):
        buffer = ""
        j = i

        while j < len(sentences):
            candidate = buffer + " " + sentences[j] if buffer else sentences[j]
            if len(candidate) > max_chars and buffer:
                break
            buffer = candidate
            j += 1

        chunks.append(buffer.strip())
        i = max(i + 1, j - overlap_sentences)

    return chunks


def strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup:
    raise NotImplementedError
