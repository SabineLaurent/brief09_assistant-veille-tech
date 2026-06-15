from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup
from langchain.text_splitter import RecursiveCharacterTextSplitter


def clean_html_to_markdown(html: str) -> str:
    """
    Convertir le HTML en Markdown, en supprimant les balises html et en conservant la structure du texte.

    - Convertit le HTML restant en Markdown, en utilisant des titres ATX pour les titres HTML.
    """
    from markdownify import markdownify

    return markdownify(html, heading_style="ATX")


def dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Supprimer les articles en double.
    
    - en fonction de leur URL.

    TODO:
    - à optimiser pour les cas où:
     --> un même article de newsletter est publié sur plusieurs sites, en utilisant un hash du contenu de l'article plutôt que l'URL.
     --> articles republiés avec un suffixe de version (cas dans arXiv)
    """
    seen: set[str] = set()
    result = []
    for art in articles:
        url = art["url"]
        if url not in seen:
            seen.add(url)
            result.append(art)
    return result


def chunk(text: str, max_chars: int = 1200) -> list[str]:
    """
    Découper le texte en morceaux d’une longueur de caractères maximale spécifiée.
    """
    splitter = RecursiveCharacterTextSplitter(chunk_size=max_chars, chunk_overlap=100)
    return splitter.split_text(text)


def strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Supprimer les balises d'un objet BeautifulSoup.

    - Retire les balises de navigation, de pied de page, de script et de style.
    """
    for tag in soup.find_all(["nav", "footer", "script", "style"]):
        tag.decompose()
    return soup


# A usable title carries a minimum of alphanumeric substance. A title reduced to stray
# punctuation (observed: a lone ")") is a parsing artifact, not a real title — indexing
# it produces a junk card and a meaningless embedding.
_MIN_TITLE_ALNUM = 3

# Below this length (in characters) the content is too thin to embed meaningfully: the
# article is held back as a "blocker" for the review pass to complete (scrape + summary)
# before indexing. ~150 chars (≈ 2-3 sentences) keeps usable briefs and only flags
# genuinely thin records (title + link, or a one-sentence excerpt).
MIN_CONTENT_CHARS = 150


def is_usable_title(title: str) -> bool:
    """
    Return True if the title carries enough substance to be worth indexing.

    Counts alphanumeric characters with ``str.isalnum`` (Unicode-aware, so accented or
    non-Latin titles are kept) and compares to a small threshold. A lone ")" scores 0
    and is rejected as parsing junk.
    """
    alnum = sum(1 for char in (title or "") if char.isalnum())
    return alnum >= _MIN_TITLE_ALNUM


def has_enough_content(content: str) -> bool:
    """
    Return True if the content is substantial enough to embed and index as-is.

    Below the threshold the article is a "blocker": held back from indexing so the
    review pass can complete its content first (the embedding is computed once and
    cannot be regenerated in the vector store).
    """
    return len((content or "").strip()) >= MIN_CONTENT_CHARS
