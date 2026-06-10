# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

**Nauda Palisse — Assistant Veille Tech** : assistant RAG qui répond à des questions sur
l'actualité tech en combinant une base vectorielle indexée (Chroma) et des actualités
fraîches récupérées en direct (NewsAPI). Backend FastAPI + ChromaDB + sentence-transformers
+ LangChain/Azure AI (Kimi-K2.6), frontend Next.js 15, orchestration Docker Compose.

## Commandes

```bash
make install      # uv sync (dépendances backend)
make up / down    # docker compose up -d / down (chromadb + backend + frontend)
make logs         # docker compose logs -f --tail=100
make test         # uv run pytest -v
make fmt          # ruff format + ruff check --fix
make lint         # ruff check
make typecheck    # mypy app
make ingest       # ingestion arXiv + TLDR → SQLite (= arxiv-ingest + tldr-ingest)
make index        # indexation SQLite → Chroma (chunk + embedding)
make pipeline-e2e # bout en bout : ingest puis index
make arxiv        # bout en bout arXiv seul (arxiv-ingest + index)
make tldr         # bout en bout TLDR seul (tldr-ingest + index)
make chat-test    # curl POST /chat avec une question d'exemple
```

Lancer un test unique : `uv run pytest tests/acceptance/test_cleaning.py::test_dedupe_removes_duplicate_urls -v`

Setup initial : `cp .env.example .env` puis renseigner `AZURE_AI_INFERENCE_*` et `NEWS_API_KEY`.

- Backend : http://localhost:8000 (`/health`, `/topics`, `/chat`)
- Frontend : http://localhost:3000
- ChromaDB : http://localhost:8002 (host) / `http://chromadb:8000` (inter-services)

Frontend (`web/`) : `npm run dev|build|lint|typecheck` (gérés séparément du backend Python, pas via `make`).

## Architecture

### Flux principal d'un appel `/chat`

```
Frontend (page.tsx) ──POST /chat──▶ main.py ──▶ chat.handle_chat()
                                                     │
                       ┌─────────────────────────────┼──────────────────────────┐
                       ▼                             ▼                          ▼
            retrieval.retrieve(query)     ingest.enrich.enrich_retrieval   runtime.fresh_news.fetch
            (embedding + query Chroma)    (hook d'enrichissement)          (NewsAPI live)
                       │                             │                          │
                       └──────────────┬──────────────┴──────────────────────────┘
                                      ▼
                         rag.llm.compose_answer()
                         (formate le contexte, appelle Azure AI / Kimi-K2.6,
                          construit les ArticleCard, gère le mode "degraded")
                                      ▼
                              ChatResponse {answer, cards, status}
```

`chat.handle_chat` enveloppe les appels à `enrich_retrieval` et `fresh_news.fetch` dans des
`try/except NotImplementedError` : ces deux hooks sont conçus pour être optionnels et
dégradables — le pipeline reste fonctionnel même quand ils ne sont pas (encore) implémentés.

### Modules clés

- `app/main.py` — App FastAPI (CORS ouvert), endpoints `/health`, `/topics` (liste statique
  de 5 sujets), `/chat` (délègue à `handle_chat`).
- `app/config.py` — `Settings` (pydantic-settings, chargé depuis `.env`, mis en cache via
  `lru_cache`) : endpoint/clé Azure AI, URL/collection Chroma, modèle d'embedding, clé
  NewsAPI, ports.
- `app/chat.py` — orchestrateur `handle_chat` : étend la requête (`question | topic1, topic2`),
  récupère le top-8 via `retrieval.retrieve`, tente l'enrichissement et l'actu fraîche,
  compose la réponse finale.
- `app/rag/chroma_client.py` — client HTTP Chroma (`get_or_create_collection("articles",
  hnsw:space=cosine)`), mis en cache.
- `app/rag/retrieval.py` — charge `intfloat/multilingual-e5-small` (sentence-transformers),
  encode la requête, interroge Chroma (`n_results=k`), normalise en
  `{id, content, metadata, distance}`. Toute exception est avalée et loggée → `[]`.
- `app/rag/llm.py` — cœur de la génération : `get_llm()` instancie
  `AzureAIChatCompletionsModel` (LangChain) si les credentials Azure sont présents, sinon
  `None` (mode dégradé) ; `compose_answer` gère 3 cas → rien trouvé (`status="empty"`),
  LLM absent (cartes brutes, `status="degraded"`), LLM dispo (appel Azure AI, parsing JSON
  `{answer, cards}`, `status="ok"`, avec repli si erreur LLM).
- `app/runtime/fresh_news.py` — `fetch(topics, since)` : doit interroger NewsAPI en direct
  au moment du chat pour couvrir l'actu chaude.
- `app/ingest/` — chaîne d'ingestion : `news_api.NewsApiIngester.run` (récupère/normalise
  des articles NewsAPI par sujet), `scraper.Scraper.run` (scrape une liste d'URLs),
  `cleaning.*` (`clean_html_to_markdown`, `dedupe`, `chunk`, `strip_boilerplate` — pipeline
  de nettoyage avant indexation), `enrich.enrich_retrieval` (hook d'enrichissement
  post-retrieval).
- `scripts/ingest_cli.py` — CLI Typer (`news`, `scrape`), appelée par `make ingest`.
- `web/` — frontend Next.js : une seule page (`app/page.tsx`) avec sélection de sujets,
  champ question libre, appel `postChat`/`fetchTopics` (`lib/api.ts`), affichage de la
  synthèse + grille de cartes d'articles avec tags colorés (hash déterministe → palette).

### État du projet

Le squelette (FastAPI, RAG retrieval, intégration LLM, frontend, orchestration Docker) est
fonctionnel. Les briques d'ingestion (`scraper`, `news_api`, `cleaning`, `enrich`,
`fresh_news`, et le CLI `ingest_cli.py`) sont en `raise NotImplementedError` — c'est le
travail principal restant. `tests/acceptance/` définit le contrat attendu de chaque stub
(champs requis dans les dicts retournés, dédoublonnage, gestion des erreurs réseau,
chunking, etc.) : c'est la spec exécutable à suivre pour l'implémentation.

### Tests

- `tests/conftest.py` ajoute la racine du dépôt à `sys.path`.
- `tests/acceptance/` contient la spec exécutable des stubs d'ingestion (`respx` est listé
  en dépendance dev pour mocker `httpx` côté scraper/NewsAPI le moment venu).
- Nouveaux tests à placer à la racine `tests/`, pas dans `tests/acceptance/` (réservé à la
  spec des stubs d'ingestion).

### Docker Compose

3 services : `chromadb` (port hôte 8002, healthcheck sur `/api/v1/heartbeat`), `backend`
(8000, monte `./app` et `./scripts` en volumes — rechargement à chaud via `--reload`),
`frontend` (3000, dépend du backend).

## Règles de sécurité

- Ne jamais lire, afficher ou citer le contenu de `.env` ou tout fichier contenant des secrets.
- Si tu as besoin de connaître une variable d'environnement, demande son nom uniquement, pas sa valeur.

## Autres règles

- YAGNI: "You Ain't Gonna Need It"
- SOC: "Separation of Concerns"
- DRY: "Don't Repeat Yourself"
- KISS: "Keep It Simple, Stupid"
- NTUI: "Never Trust User Inputs"

Je suis novice en python. J'ai besoin de consignes et explications claires pour comprendre ce que je code et d'une progression pas à pas.
Pour un premier jet, on peut coder en spaghetti code.
Mais ensuite, tu proposes étape par étape des modifications pour respecter les principes précédemment cités.

## Informations supplémentaires

Fichiers à lire en complément:

- @README.md
- @docs/SPECS.md
- @docs/TODO.md
- @docs/SOURCES.md
- @docs/repo-initial-state/
