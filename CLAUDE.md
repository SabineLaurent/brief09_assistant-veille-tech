# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

**Nauda Palisse — Assistant Veille Tech** : assistant RAG qui répond à des questions sur
l'actualité tech en combinant une base vectorielle indexée (Chroma) et des actualités
fraîches récupérées en direct. Backend FastAPI + ChromaDB + sentence-transformers +
LangChain/Azure AI (Kimi-K2.6), frontend Next.js 15, orchestration Docker Compose.

L'ingestion passe par un **checkpoint SQLite** (`ingest.db`) entre la collecte et
l'indexation vectorielle (voir Architecture).

## Commandes

```bash
make install      # uv sync (dépendances backend)
make up / down    # docker compose up -d / down (chromadb + backend + frontend)
make logs         # docker compose logs -f --tail=100
make test         # uv run pytest -v
make fmt          # ruff format + ruff check --fix
make lint         # ruff check
make typecheck    # mypy app
make migrate      # crée/maj le schéma SQLite (app/data/article.sql)

# Pipeline d'ingestion (2 temps : collecte → SQLite, puis SQLite → Chroma)
make ingest       # collecte arXiv + TLDR → SQLite  (= arxiv-ingest + tldr-ingest)
make index        # SQLite (status='ingested') → chunk + embed → Chroma, marque 'indexed'
make pipeline-e2e # bout en bout : ingest puis index
make arxiv        # arXiv seul de bout en bout (arxiv-ingest + index)
make tldr         # TLDR seul de bout en bout (tldr-ingest + index)
make chat-test    # curl POST /chat avec une question d'exemple

# Maintenance Chroma
make chromareset  # supprime la collection (recréée au prochain appel)
make chromadelete # down + rm volume chroma + up (reset complet)
```

Lancer un test unique : `uv run pytest tests/acceptance/test_cleaning.py::test_dedupe_removes_duplicate_urls -v`

La CLI sous-jacente est `scripts/ingest_cli.py` (Typer) : commandes `fetch` (arXiv),
`tldr`, `index`. Les commandes `news`/`scrape` y sont encore des stubs `NotImplementedError`.

Setup initial : `cp .env.example .env` puis renseigner `AZURE_AI_INFERENCE_*` (et clés des
sources si besoin). `ARXIV_TOPICS` / `WATCHED_REPOS` sont des JSON chargés par
pydantic-settings.

- Backend : http://localhost:8000 (`/health`, `/topics`, `/chat`)
- Frontend : http://localhost:3000
- ChromaDB : http://localhost:8002 (host) / `http://chromadb:8000` (inter-services)

Frontend (`web/`) : `npm run dev|build|lint|typecheck` (gérés séparément du backend Python).

## Architecture

### Deux pipelines distincts

**1. Ingestion (batch, hors ligne) — collecte vers Chroma en passant par SQLite :**

```
  Sources                  Normalisation         Checkpoint SQLite        Indexation
┌──────────────┐        ┌──────────────────┐    ┌──────────────────┐    ┌────────────────────┐
│ ArXivApi     │        │ Article (pydantic)│    │ table `article`  │    │ indexer.index_     │
│ Ingester.run │──dict─▶│ models.py         │───▶│ status='ingested'│───▶│ articles()         │
│ TldrScraper  │        │ reference/title/  │    │ (upsert idempotent)   │ chunk + embed →    │
│ .run(urls)   │        │ tags/dates/...    │    │ watermark par source  │ Chroma upsert,     │
└──────────────┘        └──────────────────┘    └──────────────────┘    │ status='indexed'   │
                                                                          └────────────────────┘
```

- **Collecte** (`make ingest`) : chaque ingester renvoie des `Article` pydantic, persistés
  via `upsert_article` (`INSERT OR IGNORE` sur `reference` → idempotent). Les nouveaux
  articles ont `status='ingested'`.
- **Indexation** (`make index`) : `read_ingested_articles()` lit les `status='ingested'`,
  `indexer.index_articles` les découpe (`cleaning.chunk`), embede (même modèle que le
  retrieval), `collection.upsert` dans Chroma, puis passe chaque article à `status='indexed'`
  (ou `'error'` en cas d'échec). Boucle **article par article** : un échec n'arrête pas le run.
- **SQLite** sert de **checkpoint** : il découple collecte et indexation, permet de rejouer
  l'indexation sans re-télécharger, et garde une trace (`status`, `ingested_at`, `indexed_at`).

**2. Chat (en ligne) — RAG au moment de la requête :**

```
Frontend (page.tsx) ──POST /chat──▶ main.py ──▶ chat.handle_chat()
                                                     │
                       ┌─────────────────────────────┼──────────────────────────┐
                       ▼                             ▼                          ▼
            retrieval.retrieve(query)     ingest.enrich.enrich_retrieval   runtime.fresh_news.fetch
            (embedding + query Chroma)    (hook, stub)                     (live, stub)
                       └──────────────┬──────────────┴──────────────────────────┘
                                      ▼
                         rag.llm.compose_answer()  ──▶ ChatResponse {answer, cards, status}
```

`handle_chat` enveloppe `enrich_retrieval` et `fresh_news.fetch` dans des
`try/except NotImplementedError` : ces hooks sont optionnels et dégradables — le chat reste
fonctionnel sans eux.

### Ingestion incrémentale (watermark)

Pour ne pas re-télécharger ce qui est déjà en base, chaque source utilise un **high
watermark** : la date la plus récente déjà connue (`article_store.get_watermark(source,
date_field)`, `MAX()` sur une colonne de date TEXT ISO 8601).

- **arXiv** : watermark sur `updated_date` (`<updated>` Atom). Le flux est trié par
  `lastUpdatedDate` décroissant : la pagination s'arrête dès qu'un article ≤ watermark est
  atteint (rattrapage terminé), avec un plafond `arxiv_max_pages` au run à froid.
- **TLDR** : watermark sur `published_date`. `missing_edition_dates()` calcule les dates
  d'édition restant à scraper (dernière connue + 1 jour → aujourd'hui).

Voir `docs/steps/12-ingestion-incrementale-watermark.md`.

### Modèles `Article` — attention, il y en a deux

- `app/ingest/models.py:Article` — **forme canonique d'ingestion** (`reference, title,
  source, published_date, updated_date, content, url, tags, authors, status, ingested_at,
  indexed_at`). Sous-classée par `ArXivArticle` / `TldrArticle`. C'est ce modèle qui est
  `model_dump()` → SQLite. `to_chroma_metadata()` / `to_indexable()` produisent la forme
  attendue par l'indexation.
- `app/schemas.py:Article` — modèle **côté API** (legacy, peu utilisé). Ne pas confondre.
  Les schémas réellement servis par l'API sont `ChatRequest`, `ChatResponse`, `ArticleCard`,
  `Topic`.

### Modules clés

- `app/main.py` — App FastAPI (CORS ouvert). Appelle `init_db()` au démarrage. Endpoints
  `/health`, `/topics` (5 sujets statiques), `/chat`.
- `app/config.py` — `Settings` (pydantic-settings, `.env`, `lru_cache`). Contient un
  sous-objet `Sources` (arXiv topics/limites, TLDR, GitHub, NewsAPI). Modèles
  `ArXivTopic {category, keywords}` et `WatchedRepo`.
- `app/data/` — couche SQLite : `article.sql` (schéma), `migrate.py` (`init_db`),
  `article_store.py` (`upsert_article`, `get_watermark`, `read_ingested_articles`,
  `update_article_status`, `count_articles`), `csv_exporter.py` (log CSV horodaté des
  sessions d'ingestion sous `logs/ingest/`).
- `app/ingest/arXiv_api.py` — `ArXivApiIngester` : interroge l'API Atom arXiv (httpx + lxml),
  pagine avec délai de politesse, normalise, applique watermark + filtre `arxiv_min_year`.
- `app/ingest/tldr_scraper.py` — `TldrScraper` : construit les URLs d'édition, scrape (httpx
  + BeautifulSoup), parse les newsletters, exclut les sponsors, dérive `reference` d'un hash
  d'URL nettoyée.
- `app/ingest/cleaning.py` — fonctions pures : `clean_html_to_markdown` (markdownify),
  `dedupe` (par URL), `chunk` (LangChain `RecursiveCharacterTextSplitter`),
  `strip_boilerplate` (retire nav/footer/script/style).
- `app/indexing/indexer.py` — `index_articles(articles)` : chunk + embed + `collection.upsert`
  + maj du status SQLite.
- `app/rag/chroma_client.py` — client HTTP Chroma, collection `articles` (`hnsw:space=cosine`),
  caché.
- `app/rag/retrieval.py` — `get_embedder()` (sentence-transformers `multilingual-e5-small`),
  `embed()` (normalisé), `retrieve(query, k)` (query Chroma, normalise en `{id, content,
  metadata, distance}`, avale les exceptions → `[]`).
- `app/rag/llm.py` — génération : `get_llm()` (Azure AI / LangChain, `None` si non
  configuré → mode dégradé), `compose_answer` (3 cas : `empty` / `degraded` / `ok`, parsing
  JSON `{answer, cards}` avec repli), `_build_cards`, `_split_tags` (gère tags `list` **ou**
  string).
- `app/runtime/fresh_news.py`, `app/ingest/enrich.py`, CLI `news`/`scrape` — **encore en
  stub** (`NotImplementedError`).
- `web/` — frontend Next.js : une page (`app/page.tsx`), client REST (`lib/api.ts`).

### Indexation Chroma — contraintes de cohérence

`index_articles` doit produire des entrées que `retrieval.retrieve` saura relire :

- `ids` uniques **par chunk** : `f"{reference}::{i}"`.
- `embeddings` : **même modèle** (`multilingual-e5-small`) et **même normalisation**
  (`normalize_embeddings=True`) que le retrieval — sinon la métrique cosinus est faussée.
- `metadatas` : au minimum `title, source, date, url, tags`. `tags` peut être une string
  `"a|b"` ou une liste (`_split_tags` gère les deux côté retrieval). Pour rester idempotent
  sur les ré-ingestions, on utilise `upsert` (pas `add`).

### Tests

- `tests/conftest.py` ajoute la racine du dépôt à `sys.path`.
- `tests/acceptance/` = spec exécutable des contrats d'ingestion. **Ne pas y ajouter de
  nouveaux tests** — les nouveaux tests vont à la racine `tests/` (`respx` dispo pour mocker
  httpx).
- Les tests doivent passer **sans clés API** : les appels réseau optionnels (NewsAPI, etc.)
  dégradent vers `[]`/liste vide plutôt que de lever (cf. `retrieval.retrieve`).

### Docker Compose

3 services : `chromadb` (port hôte 8002, healthcheck `/api/v1/heartbeat`), `backend` (8000,
monte `./app` et `./scripts` — `--reload`), `frontend` (3000, dépend du backend).

## Règles de sécurité

- Ne jamais lire, afficher ou citer le contenu de `.env` ou tout fichier contenant des secrets.
- Si tu as besoin de connaître une variable d'environnement, demande son nom uniquement, pas
  sa valeur.

## Autres règles

- YAGNI, SOC, DRY, KISS, NTUI ("Never Trust User Inputs").

Je suis novice en python. J'ai besoin de consignes et explications claires pour comprendre ce
que je code, et d'une progression pas à pas. **Avant d'implémenter, fais une proposition
step-by-step en mode professeur** (explication claire, précise, logique). Un premier jet peut
être en spaghetti code ; ensuite tu proposes étape par étape les refactors vers les principes
ci-dessus. Écris **un fichier à la fois** et attends validation avant de continuer.

## Informations supplémentaires

Fichiers à lire en complément :

- @README.md
- @docs/SPECS.md
- @docs/TODO.md
- @docs/SOURCES.md
- @docs/steps/        — journal daté des décisions et étapes réalisées
- @docs/repo-initial-state/
