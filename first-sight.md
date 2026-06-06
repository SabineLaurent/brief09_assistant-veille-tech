# Premier regard sur le dépôt — Assistant Veille Tech (Nauda Palisse)

Notes prises lors d'une première exploration du code en place.

## Vue d'ensemble du projet

**Nauda Palisse — Veille Tech** : assistant RAG qui répond à des questions sur l'actu
tech en combinant une base vectorielle indexée (Chroma) et des actualités fraîches
récupérées en direct (NewsAPI). Stack : FastAPI + ChromaDB + sentence-transformers +
LangChain/Azure AI (Kimi-K2.6) côté backend, Next.js 15 côté frontend, le tout
orchestré par Docker Compose.

### Flux principal d'un appel `/chat`

```
Frontend (page.tsx) ──POST /chat──▶ main.py ──▶ chat.handle_chat()
                                                     │
                       ┌─────────────────────────────┼──────────────────────────┐
                       ▼                             ▼                          ▼
            retrieval.retrieve(query)     ingest.enrich.enrich_retrieval   runtime.fresh_news.fetch
            (embedding + query Chroma)    (hook d'enrichissement, stub)    (NewsAPI live, stub)
                       │                             │                          │
                       └──────────────┬──────────────┴──────────────────────────┘
                                      ▼
                         rag.llm.compose_answer()
                         (formate le contexte, appelle Azure AI / Kimi-K2.6,
                          construit les ArticleCard, gère le mode "degraded")
                                      ▼
                              ChatResponse {answer, cards, status}
```

### Détail module par module

- **`app/main.py`** — App FastAPI, CORS ouvert, 3 endpoints : `/health`, `/topics`
  (liste statique de 5 sujets populaires), `/chat` (délègue à `handle_chat`).

- **`app/config.py`** — `Settings` (pydantic-settings) chargé depuis `.env` :
  endpoint/clé Azure AI, URL/collection Chroma, modèle d'embedding, clé NewsAPI,
  ports. Mis en cache via `lru_cache`.

- **`app/chat.py`** (`handle_chat`) — Orchestrateur :
  1. Étend la requête (`question | topic1, topic2`) ;
  2. Récupère le top-8 des chunks via `retrieval.retrieve` ;
  3. Tente d'enrichir via `ingest.enrich.enrich_retrieval` (catch `NotImplementedError`) ;
  4. Tente de récupérer de l'actu fraîche via `fresh_news.fetch`
     (catch `NotImplementedError`) ;
  5. Compose la réponse finale via `compose_answer`.
  → Le `try/except NotImplementedError` montre que ces deux hooks sont conçus pour
  être optionnels/dégradables pendant le développement.

- **`app/rag/chroma_client.py`** — Client HTTP Chroma
  (`get_or_create_collection("articles", hnsw:space=cosine)`), mis en cache.

- **`app/rag/retrieval.py`** — Charge le modèle d'embedding
  `intfloat/multilingual-e5-small` (sentence-transformers), encode la requête,
  interroge Chroma (`n_results=k`), normalise le résultat en liste de
  `{id, content, metadata, distance}`. Toute exception est avalée et loggée
  (retourne `[]`).

- **`app/rag/llm.py`** — Cœur de la génération :
  - `get_llm()` : instancie `AzureAIChatCompletionsModel` (Kimi-K2.6 via LangChain)
    si les credentials Azure sont présents, sinon `None` (mode dégradé) ;
  - `_format_context` / `_build_cards` : transforment chunks indexés + articles
    frais en contexte texte et en `ArticleCard` pour l'UI ;
  - `compose_answer` : 3 cas → (a) rien trouvé → `status="empty"`, (b) LLM non
    configuré → cartes brutes avec `status="degraded"`, (c) LLM dispo → appelle
    Azure AI, parse la réponse JSON attendue (`{answer, cards}`), extrait `answer`,
    renvoie `status="ok"`. Les erreurs LLM sont catchées et donnent une réponse de
    repli.

- **`app/runtime/fresh_news.py`** — `fetch(topics, since)` : **stub**
  (`raise NotImplementedError`), censé interroger NewsAPI en direct au moment du
  chat pour couvrir l'actu chaude.

- **`app/ingest/`** — Chaîne d'ingestion, **entièrement stubbée** :
  - `news_api.NewsApiIngester.run` : récupère/normalise des articles NewsAPI par sujet ;
  - `scraper.Scraper.run` : scrape une liste d'URLs ;
  - `cleaning.*` : `clean_html_to_markdown`, `dedupe`, `chunk`, `strip_boilerplate`
    — pipeline de nettoyage avant indexation ;
  - `enrich.enrich_retrieval` : hook optionnel d'enrichissement post-retrieval.

- **`scripts/ingest_cli.py`** — CLI Typer (`news`, `scrape`), également en stub,
  appelée par `make ingest`.

- **`tests/acceptance/`** — Définit le contrat attendu de chaque stub (champs requis
  dans les dicts retournés, dédoublonnage, gestion des erreurs réseau, chunking,
  etc.) — la spec exécutable de ce qui reste à implémenter.

- **`web/`** — Frontend Next.js : une seule page (`page.tsx`) avec sélection de
  sujets, champ question libre, appel `postChat`/`fetchTopics` (`lib/api.ts`),
  affichage de la synthèse + grille de cartes d'articles avec tags colorés (hash
  déterministe → palette).

- **`docker-compose.yml`** — 3 services : `chromadb` (port hôte 8002), `backend`
  (8000, monte `./app` et `./scripts`), `frontend` (3000, dépend du backend).

### État du projet

Le squelette (FastAPI, RAG retrieval, intégration LLM, frontend, orchestration
Docker) est **fonctionnel**. Ce qui manque, ce sont précisément les briques
d'ingestion (`scraper`, `news_api`, `cleaning`, `enrich`, `fresh_news`, et le CLI) —
toutes en `raise NotImplementedError`, avec des tests d'acceptance déjà rédigés pour
guider l'implémentation.
