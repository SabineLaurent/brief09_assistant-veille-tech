# Fonctionnement de l'ingestion arXiv - 2026-06-10

Parcours complet du flux : de la requête à l'API arXiv jusqu'à l'indexation dans Chroma.

Note jumelle de [fonctionnement-scraping-tldr.md](fonctionnement-scraping-tldr.md) — le
**Temps 2** (indexation) est strictement le même pour les deux sources, c'est tout
l'intérêt du modèle `Article` commun.

```
   TEMPS 1 : INGESTION (make arxiv-ingest)
   API arXiv (XML Atom) ──parsing + normalisation──▶ ArXivArticle (Pydantic)
                                                   ──▶ SQLite (status='ingested')

   TEMPS 2 : INDEXATION (make index)
   SQLite (status='ingested') ──chunk + embedding──▶ Chroma ──▶ status='indexed'
```

---

## Temps 1 — L'ingestion : de l'API arXiv vers SQLite

### 1. Le point d'entrée : la commande CLI

`make arxiv-ingest` lance `scripts/ingest_cli.py fetch` (`Makefile:35-36`). La commande
`fetch` (`ingest_cli.py:16-21`) est minimaliste :

1. `ArXivApiIngester().run()` → liste d'`ArXivArticle` normalisés.
2. `upsert_article(a.model_dump())` pour chaque article → SQLite.
3. Affichage du résumé (récupérés / nouveaux insérés / total en base).

Le module a aussi un mode autonome (`python app/ingest/arXiv_api.py`, bloc `__main__`)
qui fait la même chose plus un **export CSV horodaté** de la session — utile en
développement pour inspecter ce qui a été récupéré.

### 2. La configuration : quels sujets surveiller ?

Tout est piloté par `Sources` dans `app/config.py` (chargé depuis `.env`) :

- `arXiv_base_url` : `https://export.arxiv.org/api/query` (l'API publique, sans clé).
- `arXiv_topics` : une liste d'objets `ArXivTopic {category, keywords}` — par exemple
  `{"category": "cs.AI", "keywords": ["deep learning", "transformer"]}`. C'est le
  "quoi surveiller", défini en JSON dans le `.env`.
- `arxiv_max_results` : 25 résultats max par topic.
- `arxiv_min_year` : 2025 — les articles plus anciens sont écartés (voir §5).

### 3. La requête API : `fetch_articles()`

`fetch_articles(category, keywords)` (`arXiv_api.py:38-70`) construit la requête de
recherche dans la syntaxe propre à arXiv :

```
search_query = "cat:cs.AI AND (all:deep learning OR all:transformer)"
```

C'est-à-dire : *dans la catégorie `cs.AI`, les articles qui matchent au moins un des
mots-clés* (les keywords sont combinés en `OR`, le `all:` cherche dans tous les champs).
Les autres paramètres : tri par `lastUpdatedDate` décroissant (les plus récents
d'abord), `start=0`, `max_results=25`.

L'appel HTTP est un simple `httpx.get(...)` avec timeout de 15 s et suivi des
redirections, puis `raise_for_status()` — une réponse non-2xx lève une exception.

⚠️ Contrairement au scraper TLDR, **il n'y a pas de `try/except` par topic** : une
panne réseau sur un topic fait échouer tout le `run()`. C'est un écart avec la
philosophie "pipeline dégradable" du projet (cf. remarques en fin de note).

### 4. Le parsing XML Atom : `_xml_to_raw_entries()` + `_entry_to_dict()`

L'API arXiv ne renvoie pas du JSON mais du **XML au format Atom** (le format des flux
RSS modernes). Deux subtilités :

- **Les espaces de noms XML** : chaque balise est préfixée par
  `{http://www.w3.org/2005/Atom}`. Le helper `_tag(name)` (`arXiv_api.py:24-27`) évite
  de répéter ce préfixe verbeux partout — `_tag("title")` →
  `"{http://www.w3.org/2005/Atom}title"`.
- **Un article = un élément `<entry>`** : `_xml_to_raw_entries` parse le document avec
  `lxml.etree` et retourne la liste des `<entry>`.

`_entry_to_dict` (`arXiv_api.py:81-114`) extrait ensuite de chaque `<entry>` :

| Champ extrait | Provenance dans le XML |
|---|---|
| `id` | `<id>` — l'URL canonique `http://arxiv.org/abs/2411.18583v1` |
| `title`, `summary`, `published` | balises directes (texte strippé) |
| `authors` | tous les `<author><name>` |
| `link` | le `<link rel="alternate">` (la page web de l'article) |
| `category`, `keywords` | **injectés** depuis la requête (pas dans le XML) — ils serviront de tags |

### 5. La normalisation : `normalize_article()`

`normalize_article` (`arXiv_api.py:117-138`) convertit le dict brut en `ArXivArticle`
(qui hérite du modèle commun `Article`, `app/ingest/models.py`) :

- **`reference`** : l'ID arXiv extrait de l'URL — `"2411.18583v1"` (split sur
  `"/abs/"`). Contrairement à TLDR (hash SHA-1 d'URL), arXiv fournit un **identifiant
  natif stable** — pas besoin de le fabriquer. Même rôle : clé d'idempotence pour la
  déduplication en aval.
- **`published_date`** : la chaîne ISO `"2025-11-27T18:59:59Z"` → objet `datetime`
  (le `Z` est remplacé par `+00:00` car `fromisoformat` de Python < 3.11 ne le
  comprenait pas).
- **`content`** : le `summary`, c'est-à-dire **l'abstract** du papier. L'API arXiv ne
  fournit pas le texte intégral — c'est l'abstract qui sera chunké et vectorisé.
- **`tags`** : `[category] + keywords`, par exemple
  `["cs.AI", "deep learning", "transformer"]`.
- **`authors`** : la liste des auteurs (vide côté TLDR, remplie ici).

### 6. L'orchestration : `run()` et le filtre par année

`run()` (`arXiv_api.py:140-169`) boucle sur les topics configurés : pour chaque topic,
`fetch_articles` → `normalize_article` → **filtre par année**.

Le filtre `published_date.year < arxiv_min_year` → article ignoré est appliqué **côté
client, après réception**, et c'est un choix documenté dans la docstring : l'endpoint
Atom d'arXiv n'a pas de paramètre de filtre sur la date de *publication* ; le seul
filtre disponible (`submittedDate`) porte sur la date de *dépôt initial*, qui peut
diverger de `published` quand un papier est révisé tardivement. Filtrer après réception
garantit un comportement cohérent quel que soit l'historique de la soumission.

### 7. La persistance SQLite : `upsert_article()`

Identique à TLDR (`article_store.py:10-32`) :

- **`INSERT OR IGNORE`** sur la colonne `reference UNIQUE` : un article déjà en base
  (même ID arXiv) est silencieusement ignoré → relancer `make arxiv-ingest` ne crée
  aucun doublon.
- **`status` par défaut `'ingested'`** (posé par le schéma `article.sql:11`) : file
  d'attente pour le temps 2.
- `tags` et `authors` sérialisés en JSON (SQLite ne connaît pas les listes).

---

## Temps 2 — L'indexation : de SQLite vers Chroma

Strictement identique au flux TLDR — voir
[fonctionnement-scraping-tldr.md](fonctionnement-scraping-tldr.md) §7-8 pour le détail.
En résumé, `make index` → commande `index` du CLI → `index_articles()`
(`app/indexing/indexer.py`) :

1. `read_ingested_articles()` : `SELECT * FROM article WHERE status = 'ingested'`.
2. Pour chaque article (chacun dans son `try/except`) :
   - **chunking** de l'abstract (`RecursiveCharacterTextSplitter`, 1200 caractères,
     chevauchement 100 — un abstract arXiv tient souvent en 1-2 chunks) ;
   - **métadonnées** `{title, source: "arXiv", date, url, tags}` (tags joints en
     `"cs.AI|deep learning|…"`) ;
   - **ids déterministes** `f"{reference}::{i}"` (ex. `"2411.18583v1::0"`) ;
   - **embeddings** `multilingual-e5-small` normalisés (cohérents avec le retrieval
     et la métrique cosinus de la collection) ;
   - **`collection.upsert(...)`** → ré-indexation sans doublon ;
   - statut → `'indexed'` (+ `indexed_at`) ou `'error'`.

Les chunks sont alors interrogeables par `retrieval.retrieve()` à chaque `/chat`.

---

## Différences notables avec le flux TLDR

| | arXiv | TLDR |
|---|---|---|
| Source des données | API officielle (XML Atom) | Scraping HTML |
| `reference` | ID natif arXiv (`2411.18583v1`) | SHA-1 de l'URL nettoyée |
| `content` | Abstract complet du papier | Résumé rédigé par TLDR |
| `authors` | Liste des auteurs | `[]` |
| `tags` | catégorie + keywords configurés | édition + catégorie de section |
| Tolérance aux pannes | ❌ pas de `try/except` par topic | ✅ `try/except` par URL |
| Filtre qualité | année ≥ `arxiv_min_year` | exclusion des `(Sponsor)` |

## Remarques en passant (rien de bloquant)

- **Tolérance aux pannes** : `run()` ne protège pas chaque topic individuellement — une
  erreur HTTP sur le premier topic fait perdre tous les suivants. Envelopper l'appel à
  `fetch_articles` dans un `try/except` par topic (comme le fait `TldrScraper.run` par
  URL) alignerait le module sur la philosophie "dégradable" du projet.
- **La `reference` inclut la version** (`v1`, `v2`…) : un papier révisé sur arXiv
  ressortira avec une nouvelle référence → nouvelle ligne en base au lieu d'une mise à
  jour. Acceptable pour de la veille, mais à garder en tête.
- L'import `Path` (`arXiv_api.py:6`) semble inutilisé — un `make lint` le confirmera.

---
