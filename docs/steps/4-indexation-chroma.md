# Étape 4 — Indexation dans Chroma

## Contexte

Les articles arXiv sont déjà fetchés et stockés dans SQLite (status `ingested`).
Cette étape câble le pont manquant : SQLite → chunk → embed → Chroma.

---

## Étape 4.1 — `chunk()` avec chevauchement

### Pourquoi découper en chunks ?

Les modèles d'embedding ont une limite de tokens. Un abstract arXiv (~300 mots) tient
en un seul chunk, mais un article complet scrapé (blog, documentation) peut dépasser
cette limite. `chunk()` découpe le texte en morceaux indexables.

### Pourquoi le chevauchement (overlap) ?

Sans overlap, une idée qui tombe à cheval sur deux chunks peut être mal récupérée :
chaque chunk n'en a qu'une moitié, et le retrieval peut rater les deux.

```
Sans overlap :
  Chunk 1 : [S1  S2  S3  S4]
  Chunk 2 :                 [S5  S6  S7  S8]
  → Si l'idée clé est entre S4 et S5 : aucun chunk ne la contient entièrement.

Avec overlap (overlap_sentences=2) :
  Chunk 1 : [S1  S2  S3  S4]
  Chunk 2 :         [S3  S4  S5  S6]
  Chunk 3 :                 [S5  S6  S7  S8]
  → S3/S4 apparaissent dans chunk 1 et 2 : le contexte est préservé.
```

### Implémentation

```python
def chunk(text: str, max_chars: int = 1200, overlap_sentences: int = 2) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    chunks = []
    i = 0  # index de la première phrase du chunk en cours

    while i < len(sentences):
        buffer = ""
        j = i  # on avance j jusqu'à remplir le chunk

        while j < len(sentences):
            candidate = buffer + " " + sentences[j] if buffer else sentences[j]
            if len(candidate) > max_chars and buffer:
                break  # buffer plein → on sauvegarde
            buffer = candidate
            j += 1

        chunks.append(buffer.strip())

        # Prochain chunk repart overlap_sentences phrases EN ARRIÈRE
        # max(i+1, ...) évite une boucle infinie si une phrase seule > max_chars
        i = max(i + 1, j - overlap_sentences)

    return chunks
```

**Paramètres retenus :** `max_chars=1200`, `overlap_sentences=2`.

**Cas limite :** si une phrase isolée dépasse `max_chars` (ex. un bloc de code),
elle forme un chunk à elle seule — le `max(i + 1, ...)` garantit qu'on avance quand même.

### Fichier modifié

`app/ingest/cleaning.py` — signature étendue avec `overlap_sentences=2`.

---

## Étape 4.2 — `index_articles()` dans `app/indexing/__init__.py`

### Rôle

Lire les articles SQLite avec `status='ingested'`, les indexer dans Chroma,
mettre à jour leur statut.

### Flux

```
article (SQLite, status='ingested')
    ↓ chunk(article["content"])
["chunk 0", "chunk 1", ...]
    ↓ embed(chunk_text)    ← même modèle que retrieval (multilingual-e5-small)
[vecteur 0, vecteur 1, ...]
    ↓ collection.upsert(ids, documents, embeddings, metadatas)
Chroma ✅
    ↓ update_article_status(reference, "indexed")
SQLite ✅
```

### IDs déterministes

Format : `"{reference}::0"`, `"{reference}::1"`, etc.

Avantage : relancer l'indexation n'entraîne pas de doublons — Chroma écrase l'existant
à l'upsert. L'idempotence est garantie.

### Métadonnées Chroma

Chroma n'accepte que des valeurs `str` dans les métadonnées.
`tags` est stocké en JSON dans SQLite (`'["ai", "ml"]'`) → re-sérialisé en `"ai|ml"`
via `"|".join(json.loads(article["tags"]))`, cohérent avec `arXiv_api.to_chroma_metadata()`.

| Champ SQLite     | Clé Chroma | Transformation            |
|------------------|------------|---------------------------|
| `title`          | `title`    | aucune                    |
| `source`         | `source`   | aucune                    |
| `published_date` | `date`     | `or ""` si None           |
| `url`            | `url`      | aucune                    |
| `tags`           | `tags`     | `json.loads` → `"\|".join"` |

### Tolérance aux pannes

Chaque article est traité dans un `try/except` individuel : un échec Chroma
(service inaccessible, embedding raté) passe le statut à `error` sans interrompre
les autres. Relancer `index` reprend depuis `status='ingested'` — les articles
`error` peuvent être remis à `ingested` manuellement si besoin.

### Implémentation prévue

```python
def index_articles(articles: list[dict]) -> int:
    collection = get_collection()
    total_chunks = 0

    for article in articles:
        try:
            chunks = chunk(article["content"])
            ids, docs, embeddings, metas = [], [], [], []

            for i, chunk_text in enumerate(chunks):
                ids.append(f"{article['reference']}::{i}")
                docs.append(chunk_text)
                embeddings.append(embed(chunk_text))
                metas.append({
                    "title":  article["title"],
                    "source": article["source"],
                    "date":   article["published_date"] or "",
                    "url":    article["url"],
                    "tags":   "|".join(json.loads(article["tags"])),
                })

            collection.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
            update_article_status(article["reference"], "indexed")
            total_chunks += len(chunks)

        except Exception:
            log.warning("Échec indexation article %s", article["reference"])
            update_article_status(article["reference"], "error")

    return total_chunks
```

---

## État d'implémentation

| Brique | État |
|--------|------|
| `cleaning.py::chunk()` avec overlap | ✅ fait |
| `app/indexing/__init__.py::index_articles()` | ❌ à écrire |
| `ingest_cli.py` — commande `index` | ❌ à câbler |
