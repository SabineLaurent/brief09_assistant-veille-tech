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
    - à optimiser pour le cas:
     -->  où un même article de newsletter est publié sur plusieurs sites, en utilisant un hash du contenu de l'article plutôt que l'URL.
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
