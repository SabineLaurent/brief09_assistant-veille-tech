# Scrapping

## Étape 1 — strip_boilerplate

Ce que ça fait : reçoit un objet BeautifulSoup (page HTML parsée), retire les balises <nav> et <footer> qui contiennent du bruit (menus, liens légaux, copyright), et retourne le BeautifulSoup nettoyé.

Pourquoi decompose() : cette méthode BeautifulSoup supprime la balise et tout son contenu de l'arbre HTML — c'est exactement ce qu'on veut, pas juste masquer, mais vraiment retirer.

⏺ Update(app/ingest/cleaning.py)
Added 3 lines, removed 1 line


def strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup:
    raise NotImplementedError
    for tag in soup.find_all(["nav", "footer", "script", "style"]):
        tag.decompose()
    return soup

On vérifie que le test passe

---

## Étape 2 — clean_html_to_markdown

Ce que ça fait : reçoit une chaîne HTML brute et la convertit en Markdown lisible. Par exemple <h1>Titre</h1> devient # Titre, <b>gras</b> devient **gras**.

Pourquoi markdownify : c'est déjà une dépendance du projet, conçue exactement pour ça. heading_style="ATX" force le style # Titre (au lieu de soulignements ===).

⏺ Update(app/ingest/cleaning.py)
Added 3 lines, removed 1 line


def clean_html_to_markdown(html: str) -> str:
    raise NotImplementedError
    from markdownify import markdownify

    return markdownify(html, heading_style="ATX")


def dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raise NotImplementedError

---

## Étape 3 — dedupe

Ce que ça fait : reçoit une liste d'articles (chaque article est un dictionnaire Python avec des clés comme url, title, etc.) et retire les doublons — deux articles avec la même URL ne comptent que pour un.

Pourquoi un set : un set Python ne peut pas contenir deux fois la même valeur. On s'en sert comme "mémoire" des URLs déjà vues. Pour chaque article, si son URL est déjà dans le set → on l'ignore. Sinon → on l'ajoute à la liste de résultats et on note son URL dans le set.

⏺ Update(app/ingest/cleaning.py)

─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
 Edit file
 app/ingest/cleaning.py
╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌


def dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raise NotImplementedError
    seen: set[str] = set()
    result = []
    for art in articles:
        url = art["url"]
        if url not in seen:
            seen.add(url)
            result.append(art)
    return result


def chunk(text: str, max_chars: int = 1200) -> list[str]:
╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌

---
