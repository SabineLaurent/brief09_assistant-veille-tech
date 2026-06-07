# Étape 5 — `_index_articles()` dans `ingest_cli.py`

## Contexte

`chunk()` est implémenté (LangChain `RecursiveCharacterTextSplitter`).
Les briques de bas niveau sont prêtes :

| Fonction | Fichier | Rôle |
|---|---|---|
| `chunk(text)` | `app/ingest/cleaning.py` | Découpe un texte en morceaux indexables |
| `embed(text)` | `app/rag/retrieval.py` | Transforme un texte en vecteur numérique |
| `get_collection()` | `app/rag/chroma_client.py` | Connexion à la collection Chroma `articles` |

Il manque le **ciment** : une fonction qui prend des articles (issus de `news_api` ou
`scraper`), les passe dans `chunk` + `embed`, et les pousse dans Chroma. C'est le
rôle de `_index_articles()` dans `scripts/ingest_cli.py`.

---

## Flux cible

```
articles (list[dict])
    │  chaque article : {id, title, source, date, url, content, tags}
    ▼
chunk(article["content"])
    │  → ["chunk 0", "chunk 1", ...]
    ▼
embed(chunk_text)    ← même modèle que retrieval (multilingual-e5-small, normalize=True)
    │  → [vecteur 0, vecteur 1, ...]
    ▼
collection.upsert(ids, documents, embeddings, metadatas)
    │  Chroma ✅
    ▼
retourne le nombre total de chunks indexés (affiché dans le terminal)
```

---

## Décisions de conception

### IDs déterministes

Format : `"{article_id}::0"`, `"{article_id}::1"`, etc.

`article_id` est lui-même dérivé de l'URL de l'article (hash SHA-1), défini côté
`news_api` et `scraper`. Un même article ré-ingéré produit donc les mêmes IDs →
`upsert` écrase l'existant sans créer de doublons. **L'idempotence est garantie.**

### `upsert` plutôt que `add`

`collection.add()` lève une erreur si un ID existe déjà. `collection.upsert()` met
à jour silencieusement. Indispensable pour pouvoir relancer `make ingest` sans vider
Chroma au préalable.

### `tags` : liste → chaîne

Chroma n'accepte que des types primitifs dans les métadonnées (`str`, `int`, `float`,
`bool`). Les `tags` sont donc stockés en chaîne `"python, ai"`.

Côté `/chat`, `_split_tags()` dans `llm.py` gère déjà les deux formats (liste ou
chaîne séparée par des virgules) — pas de changement à faire côté retrieval.

### Granularité : article par article, chunks en batch

```
Article A → [chunk 0, chunk 1, chunk 2] → 1 upsert (3 chunks d'un coup)
Article B → [chunk 0, chunk 1]          → 1 upsert (2 chunks d'un coup)
```

Pas un chunk à la fois, pas tous les articles d'un coup. Ce compromis évite des
requêtes Chroma trop fines ET des payloads trop lourds.

### Tolérance aux pannes

Chaque article est traité dans un `try/except` individuel : un échec (Chroma
inaccessible, embedding raté) est loggué sans interrompre les autres articles.

---

## Implémentation prévue

```python
import logging

import typer

from app.ingest.cleaning import chunk
from app.rag.chroma_client import get_collection
from app.rag.retrieval import embed

log = logging.getLogger(__name__)

app = typer.Typer(help="Ingestion CLI for the veille tech index.")


def _index_articles(articles: list[dict]) -> int:
    collection = get_collection()
    total_chunks = 0

    for article in articles:
        try:
            chunks = chunk(article.get("content", ""))
            if not chunks:
                continue

            article_id = article["id"]
            tags = article.get("tags", [])
            tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)

            metadata = {
                "title":  article.get("title", ""),
                "source": article.get("source", ""),
                "date":   article.get("date") or "",
                "url":    article.get("url", ""),
                "tags":   tags_str,
            }

            ids        = [f"{article_id}::{i}" for i in range(len(chunks))]
            embeddings = [embed(c) for c in chunks]
            metadatas  = [metadata] * len(chunks)

            collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
            total_chunks += len(chunks)

        except Exception:
            log.warning("Échec indexation article %s", article.get("id", "?"))

    return total_chunks


@app.command()
def news(topics: list[str] = typer.Option(..., "--topic", "-t")) -> None:
    # articles = NewsApiIngester().run(topics)  ← à câbler
    raise NotImplementedError


@app.command()
def scrape(urls: list[str] = typer.Option(..., "--url", "-u")) -> None:
    # articles = Scraper().run(urls)  ← à câbler
    raise NotImplementedError
```

---

## État d'implémentation

| Brique | État |
|---|---|
| `cleaning.py::chunk()` via LangChain | ✅ fait |
| `ingest_cli.py::_index_articles()` | ❌ à écrire |
| `ingest_cli.py::news` — câblage `NewsApiIngester` | ❌ bloqué (ingester non implémenté) |
| `ingest_cli.py::scrape` — câblage `Scraper` | ❌ bloqué (scraper non implémenté) |
