# SPECS — Chaîne d'ingestion : spécification d'implémentation

## 0. Périmètre

Ce document spécifie le travail **restant** pour rendre la chaîne d'ingestion opérationnelle.
Le reste du pipeline (`/chat`, retrieval vectoriel, intégration LLM, frontend, orchestration
Docker) est fonctionnel — voir `first-sight.md` (vue d'ensemble) et `deep-dive.md`
(trace fonction par fonction + contrats exacts dérivés des tests d'acceptance, Partie 2).

Modules concernés (tous actuellement en `raise NotImplementedError`) :

| Module | Fonction(s) | Test d'acceptance |
|---|---|---|
| `app/ingest/cleaning.py` | `clean_html_to_markdown`, `dedupe`, `chunk`, `strip_boilerplate` | `test_cleaning.py` |
| `app/ingest/scraper.py` | `Scraper.run(urls)` | `test_scraper.py` |
| `app/ingest/news_api.py` | `NewsApiIngester.run(topics)` | `test_news_api_ingester.py` |
| `app/runtime/fresh_news.py` | `fetch(topics, since)` (async) | `test_fresh_news.py` |
| `app/ingest/enrich.py` | `enrich_retrieval(retrieved)` | — (pas de test dédié) |
| `scripts/ingest_cli.py` | commandes `news`, `scrape` | — (pas de test dédié) |

Pour chaque module : rappel du contrat (détaillé dans `tests/acceptance/` et `deep-dive.md`),
pistes d'implémentation, et décisions de conception encore ouvertes. **Ce document ne fixe
pas le code** — il cadre les choix à faire pour que l'implémentation satisfasse les contrats
existants et s'intègre proprement au reste du pipeline.

## 1. Pipeline cible (vue d'ensemble)

```
                 ┌────────────────────┐        ┌─────────────────────┐
                 │ NewsApiIngester.run│        │   Scraper.run(urls) │
                 │   (topics → news)  │        │ (urls → pages web)  │
                 └─────────┬──────────┘        └──────────┬──────────┘
                           │  list[dict] articles bruts/normalisés
                           └─────────────┬─────────────────┘
                                          ▼
                       cleaning : strip_boilerplate → clean_html_to_markdown
                                  → dedupe → chunk
                                          ▼
                          embed() (multilingual-e5-small, normalisé)
                                          ▼
                     collection.add(ids, documents, embeddings, metadatas)
                                  Chroma — collection "articles"
                                          ▲
                                          │ (alimente)
                                  retrieval.retrieve()  ───▶ /chat (déjà fonctionnel)

  ── indépendant de l'index, appelé en direct au moment du chat ──
                 ┌────────────────────────────┐
                 │  fresh_news.fetch(topics)  │ ──▶ articles "frais" (pas indexés)
                 └────────────────────────────┘

  ── hook optionnel post-retrieval ──
                 ┌────────────────────────────┐
                 │ enrich.enrich_retrieval()  │ ──▶ chunks supplémentaires (même forme)
                 └────────────────────────────┘
```

`scripts/ingest_cli.py` est le point d'orchestration qui relie ingesters → cleaning →
indexation Chroma (déclenché par `make ingest`). `fresh_news` et `enrich` sont des hooks
*runtime*, appelés à chaque `/chat` et **découplés de l'indexation** — ils ne passent pas
par `cleaning`/Chroma.

## 2. Ordre d'implémentation conseillé

1. **`cleaning.py`** — fonctions pures, sans I/O réseau, le plus simple à tester en
   isolation ; `scraper` et le futur orchestrateur CLI en dépendent directement.
2. **`scraper.py`** — utilise `cleaning.strip_boilerplate` + `clean_html_to_markdown`.
3. **`news_api.py`** — indépendant du scraper ; nécessite `httpx` + une clé NewsAPI.
4. **`scripts/ingest_cli.py`** — câble `news_api`/`scraper` → `cleaning` → indexation
   Chroma ; ne peut être finalisé qu'une fois 1-3 prêts.
5. **`fresh_news.py`** — indépendant de l'indexation (appel NewsAPI live, pas de Chroma) ;
   peut être fait en parallèle de 4.
6. **`enrich.py`** — hook optionnel, le pipeline tourne sans lui (`handle_chat` dégrade
   proprement sur `NotImplementedError`) ; à traiter en dernier ou à laisser en stub si
   le périmètre est jugé suffisant sans lui.

## 3. Spec par module

### 3.1 `app/ingest/cleaning.py`

#### `clean_html_to_markdown(html: str) -> str`
- **Contrat** (`test_clean_html_to_markdown_strips_tags`) : pour
  `<article><h1>Titre</h1><p>Para <b>gras</b>.</p></article>`, le résultat ne doit pas
  contenir la balise littérale `<h1>` mais doit conserver les mots `"Titre"` et `"gras"`
  → conversion réelle vers Markdown (pas un simple strip de tags).
- **Piste** : `markdownify` est déjà une dépendance pinnée (`markdownify>=0.13,<0.20`,
  cf. `pyproject.toml`) — `markdownify(html, heading_style="ATX")` transforme `<h1>` en
  `# Titre` et `<b>` en `**gras**`, ce qui satisfait le contrat directement.

#### `dedupe(articles: list[dict]) -> list[dict]`
- **Contrat** (`test_dedupe_removes_duplicate_urls`) : déduplique par clé `url` ; sur 3
  articles dont 2 partagent la même `url`, n'en garde que 2 (peu importe lequel des deux
  doublons est conservé, ni l'ordre du résultat).
- **Piste** : parcours en gardant un `set[str]` des `url` déjà vues, on ne garde que la
  première occurrence de chaque URL. La clé `url` est garantie présente par les contrats
  amont (`scraper.run` et `news_api.run` la fournissent toujours) — pas besoin de
  défense contre une clé manquante.

#### `chunk(text: str, max_chars: int = 1200) -> list[str]`
- **Contrat** (`test_chunk_splits_long_text`) : pour un texte ~4000 caractères et
  `max_chars=600`, produire **≥ 2** chunks, chacun **≤ 700** caractères (tolérance
  ≈ `max_chars + 100`).
- **Piste** : découper sur des frontières de phrases (p. ex. `re.split(r"(?<=[.!?])\s+",
  text)`) et accumuler les phrases dans un buffer jusqu'à ce qu'ajouter la suivante
  dépasserait `max_chars`, puis flush — ça respecte naturellement la marge de tolérance
  tant qu'aucune phrase isolée n'est pathologiquement longue (cas non couvert par le test,
  mais à garder en tête pour du contenu réel : prévoir un repli par découpe brute si une
  "phrase" dépasse `max_chars` à elle seule).

#### `strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup`
- **Contrat** (`test_strip_boilerplate_removes_nav_and_footer`) : retire `<nav>` et
  `<footer>` (leur texte absent de `.get_text()`), conserve `<main>` ; retourne un
  `BeautifulSoup` (objet, pas une string).
- **Piste** : `for tag in soup.find_all(["nav", "footer"]): tag.decompose()` puis
  `return soup`.
- **Décision ouverte** : étendre la liste de tags à retirer (`<script>`, `<style>`,
  `<aside>`, `<header>`…) ? Le test ne couvre que `nav`/`footer`, mais des pages réelles
  scrapées contiendront probablement bien plus de bruit — à arbitrer une fois les sources
  cibles choisies (§5).

### 3.2 `app/ingest/scraper.py` — `Scraper.run(urls: list[str]) -> list[dict]`
- **Contrat** (`test_scraper.py`) : chaque article contient au minimum `title, url,
  content, source` ; **tolérance aux pannes** — `run(["http://127.0.0.1:1/does-not-exist"])`
  doit renvoyer une `list` sans lever, donc chaque URL doit être traitée dans un bloc
  protégé individuellement (un échec n'invalide pas le run entier).
- **Piste** :
  - `httpx.Client(headers={"User-Agent": self.user_agent}, timeout=self.timeout,
    follow_redirects=True)`, un `try/except Exception` par URL (log + `continue`).
  - Parsing : `BeautifulSoup(resp.text, "lxml")` → `strip_boilerplate` → extraction du
    titre (`soup.title.string` ou premier `<h1>`) → `clean_html_to_markdown(str(soup))`
    pour le `content`.
  - `source` : pas fourni par la page elle-même — dériver du domaine
    (`urlparse(url).netloc`).
- **Décision ouverte** : le contrat de `Scraper.run` n'exige ni `id` ni `date`, mais
  l'orchestrateur CLI (§3.6) en aura besoin pour indexer dans Chroma de façon cohérente
  avec `news_api` (IDs uniques, `metadata["date"]` attendu côté `_build_cards`). Deux
  options : (a) générer ces champs ici (même stratégie de hash d'URL que pour `news_api`,
  `date=None` si non extractible de la page), ou (b) laisser le CLI les synthétiser au
  moment de l'indexation. (a) est plus cohérent avec le modèle `Article` de
  `app/schemas.py` (actuellement non utilisé, mais qui modélise exactement la forme
  normalisée `{id, title, source, date, content, url, tags}` attendue en bout de chaîne).

### 3.3 `app/ingest/news_api.py` — `NewsApiIngester.run(topics: list[str]) -> list[dict]`
- **Contrat** (`test_news_api_ingester.py`) : chaque article contient au minimum `id,
  title, source, date, url, content` ; `topics=[]` → `[]` (ou toute liste) ;
  **dédoublonnage inter-sujets** — `run(["python", "python"])` doit renvoyer des `id`
  uniques, donc l'ingester doit dédupliquer lui-même (pas seulement compter sur
  `cleaning.dedupe` en aval).
- **Piste** :
  - Requêter `{self.settings.news_api_base_url}/everything?q=<topic>&apiKey=
    {self.settings.news_api_key}` via `httpx.Client` (un appel par topic).
  - L'API NewsAPI **ne fournit pas d'identifiant stable** par article (champs : `source,
    author, title, description, url, publishedAt, content`). Pour satisfaire à la fois
    "chaque article a un `id`" et "dédoublonnage par `id` inter-sujets", **dériver l'`id`
    de façon déterministe à partir de `url`** (p. ex. `hashlib.sha1(url.encode()).
    hexdigest()`) — un même article ressorti pour "python" et "python" produit alors le
    même `id`, et le dédoublonnage devient un simple regroupement par clé `id`.
  - Normalisation : `title=art["title"]`, `source=art["source"]["name"]`,
    `date=art["publishedAt"]`, `url=art["url"]`,
    `content=art.get("content") or art.get("description") or ""`.
- **Décision ouverte** : langue de recherche (`language=fr`/`en`/aucun filtre ?), fenêtre
  temporelle (`from=`/`sortBy=publishedAt` ?), et — point d'attention important — gestion
  des erreurs réseau / clé API absente (voir §5, "tolérance aux pannes").

### 3.4 `app/runtime/fresh_news.py` — `fetch(topics, since) -> list[dict]` (async)
- **Contrat** (`test_fresh_news.py`) : coroutine (`async def`, appelée avec `await` dans
  `chat.py:24`) ; chaque article contient au minimum `title, url, source` ; `since`
  (un `datetime` ou `None`) doit être accepté sans erreur (le test ne vérifie pas que le
  filtrage est effectif, juste l'absence d'exception) ; `topics=[]` → `[]`/liste.
- **Piste** : `httpx.AsyncClient`, requête `/everything` avec `from=since.isoformat()`
  si `since` est fourni. Cohérent avec `news_api.py` pour la normalisation des champs.
- ⚠️ **Point d'intégration critique** (noté dans `deep-dive.md` §7, table `_build_cards`) :
  contrairement aux chunks indexés, **les `tags` des articles frais ne passent PAS par
  `_split_tags`** côté `llm._build_cards` — ils doivent donc déjà arriver sous forme de
  `list[str]` directement exploitable (`art["tags"]`, défaut `[]`). Pour bien s'intégrer
  à `_build_cards`/`_format_context`, fournir aussi `content` et/ou `description`, et
  `date`.
- **Décision ouverte** : NewsAPI ne fournit pas de tags par article — les dériver du
  `topic` recherché (p. ex. `tags=[topic]`) est l'option la plus directe.

### 3.5 `app/ingest/enrich.py` — `enrich_retrieval(retrieved: list[dict]) -> list[dict]`
- **Contrat** : pas de test d'acceptance dédié — uniquement utilisé via
  `chat.py:20-25`. Reçoit la liste déjà normalisée par `retrieval.retrieve` (forme
  `{id, content, metadata, distance}`) et doit renvoyer une liste de chunks
  **supplémentaires de la même forme**, concaténée par `handle_chat`
  (`retrieved + enriched`, seulement si `enriched` est non vide).
- **Piste / pistes d'enrichissement possibles** : ré-interroger Chroma avec une requête
  reformulée (synonymes, expansion de sujet), récupérer des chunks voisins par métadonnée
  (même `source`/`tags`), ou croiser avec les résultats de `fresh_news`. Le pipeline
  reste pleinement fonctionnel sans cette brique (`handle_chat` dégrade sur
  `NotImplementedError` → `enriched = []`) : **son implémentation est un "nice-to-have"**,
  à prioriser après les hooks testés (3.1-3.4) si le temps le permet.

### 3.6 `scripts/ingest_cli.py` — CLI Typer
- **Contrat** : pas de test d'acceptance dédié. Deux commandes à câbler :
  - `news --topic/-t <topic> [...]` → `NewsApiIngester.run(topics)` → nettoyage
    (`dedupe`, `chunk`) → indexation Chroma.
  - `scrape --url/-u <url> [...]` → `Scraper.run(urls)` → même chaîne de nettoyage →
    indexation.
- **Piste** : factoriser une fonction commune `_index_articles(articles: list[dict]) ->
  int` (nombre de chunks indexés) partagée par les deux commandes — voir le contrat
  d'indexation transversal en §4. Utiliser `get_collection()` (`app.rag.chroma_client`)
  et `embed()`/`get_embedder()` (`app.rag.retrieval`) pour rester strictement cohérent
  avec le pipeline de retrieval (même modèle d'embedding, même normalisation, même
  métrique cosinus).
- Chaque commande doit logguer un résumé exploitable (nb d'articles récupérés / chunks
  indexés) — c'est l'unique retour visible de `make ingest` (CLI sans test, donc la
  validation manuelle via `/chat` après ingestion est le filet de sécurité, voir §7).

## 4. Contrat transversal : indexation dans Chroma

L'étape d'indexation (`collection.add(...)` ou `.upsert(...)`, appelée depuis le CLI)
doit produire des entrées que `retrieval.retrieve` pourra ensuite normaliser, et que
`llm._build_cards`/`_format_context` sauront afficher. Les contraintes à respecter :

| Élément Chroma | Contrainte | Origine |
|---|---|---|
| `ids` | chaînes **uniques** (un par chunk, pas par article — p. ex. `f"{article_id}::{i}"`) | API Chroma |
| `embeddings` | `embed(chunk_text)` — **même modèle** (`multilingual-e5-small`) et **même normalisation** (`normalize_embeddings=True`) que côté retrieval, pour rester cohérent avec la métrique `hnsw:space=cosine` de la collection | `retrieval.py:21-24`, `chroma_client.py:31` |
| `documents` | le texte du chunk (post-`clean_html_to_markdown` + `chunk`) | `retrieval.py` normalise ensuite en `content` |
| `metadatas` | dict avec au minimum `title`, `source`, `date`, `url`, `tags` | attendu par `_build_cards` (`llm.py`, table en §7 de `deep-dive.md`) — `tags` peut être une liste **ou** une chaîne `"a, b"` (`_split_tags` gère les deux côté retrieval) |

Le modèle `Article` (`app/schemas.py:7-14`, `{id, title, source, date, content, url,
tags}`) — actuellement défini mais non utilisé ailleurs — modélise exactement la forme
normalisée que `news_api`/`scraper` doivent produire avant nettoyage/chunking. L'utiliser
pour valider/typer la sortie des ingesters serait un bon point d'ancrage de cohérence.

## 5. Point d'attention transversal : tolérance aux pannes et absence de clés API

`.env.example` livre `NEWS_API_KEY=` **vide**, et les tests d'acceptance
(`test_news_api_ingester.py`, `test_fresh_news.py`) appellent `run`/`fetch` **sans
mocker `httpx`** (contrairement à ce que suggère la présence de `respx` en dépendance
dev — listé "pour le moment venu" selon `CLAUDE.md`, mais pas encore câblé). Concrètement :

- Dans un environnement de test sans clé NewsAPI valide, `NewsApiIngester.run` et
  `fresh_news.fetch` **doivent renvoyer une liste (vide en pratique) plutôt que lever**
  — sans quoi `make test` échoue dès que `NEWS_API_KEY` n'est pas configurée. C'est
  exactement le pattern déjà en place dans `retrieval.retrieve` (`retrieval.py:32-34` :
  `try/except Exception` global → `log.warning` + `return []`) et testé explicitement
  pour `Scraper.run` (`test_run_handles_unreachable_url_gracefully`).
- **Recommandation** : envelopper les appels HTTP de `news_api.py` et `fresh_news.py`
  dans un `try/except` qui logue et renvoie `[]`/`list` sur toute erreur (réponse non-2xx,
  timeout, JSON invalide, clé absente) — cohérent avec la philosophie "pipeline
  dégradable" déjà en place dans `chat.handle_chat` et `retrieval.retrieve`.
- **Piste d'amélioration future** (hors périmètre des tests d'acceptance actuels) :
  ajouter dans `tests/` (pas `tests/acceptance/`, [[Emplacement des tests]] réservé à la
  spec des stubs) des tests `respx`-mockés qui fixent le comportement de normalisation
  sur des réponses NewsAPI déterministes — l'acceptance test resterait un simple test de
  fumée ("ne plante pas"), les tests `respx` couvriraient la logique de normalisation.

## 6. Décisions ouvertes à trancher avant/pendant l'implémentation

- **Sources de scraping ciblées** : le `README.md` (section "Sources potentielles")
  liste des pistes (Hacker News, DEV.to, changelogs Vercel/OpenAI/GitHub/Anthropic,
  release notes Next.js/FastAPI/LangChain…) — le choix exact reste à arbitrer en
  fonction des 5 sujets exposés par `/topics` et de la fraîcheur attendue. Ce choix
  conditionne directement la robustesse requise de `strip_boilerplate`/
  `clean_html_to_markdown` (structure HTML très variable d'un site à l'autre).
- **Stratégie de chunking exacte** : découpage par phrases (proposé en §3.1) vs.
  paragraphes vs. tokens — à valider sur du contenu réel une fois les sources choisies.
- **Génération des `tags`** : ni NewsAPI ni le scraping ne fournissent de tags
  structurés. Dériver du `topic` recherché est la voie la plus simple (`tags=[topic]`),
  une extraction de mots-clés serait plus riche mais hors contrat testé.
- **Filtres NewsAPI** (`language`, `sortBy`, fenêtre `from`/`to`) : à fixer selon la
  fraîcheur et la couverture linguistique souhaitées (le projet est en français mais
  vise l'actu tech, majoritairement anglophone).
- **Politique d'upsert vs. add côté Chroma** : ré-ingestions répétées (cron / relances
  manuelles de `make ingest`) vont-elles dupliquer des chunks ? `collection.upsert`
  avec des `ids` déterministes (dérivés de `url` + index de chunk, cf. §4) évite le
  problème — à choisir explicitement plutôt que `add`.
- **Déclenchement de l'ingestion** : pour l'instant manuel (`make ingest`/CLI) — une
  automatisation (cron, tâche planifiée) est envisageable mais hors périmètre actuel.

## 7. Définition du "done"

- `make test` passe : tous les tests dans `tests/acceptance/` (et tout nouveau test
  ajouté à la racine `tests/`, cf. [[Emplacement des tests]]) sont au vert, y compris
  en l'absence de `NEWS_API_KEY` dans l'environnement (§5).
- `make lint` et `make typecheck` passent sans nouvelle alerte sur les modules
  `app/ingest/`, `app/runtime/fresh_news.py`, `scripts/ingest_cli.py`.
- `make ingest` (ou les sous-commandes `news`/`scrape`) peuple effectivement la
  collection Chroma `articles` — vérifiable via le compte de documents
  (`get_collection().count()`) ou directement via `/chat`.
- Validation de bout en bout : après une ingestion non vide, un appel `/chat` (p. ex.
  `make chat-test`) renvoie `status="ok"` avec des `cards` correctement formées
  (`title`, `source`, `date`, `snippet`, `tags`, `url` exploitables côté frontend) —
  signe que le format d'indexation (§4) est cohérent avec `_build_cards`/`retrieval`.
- `fresh_news.fetch` retourne des articles dont les `tags` sont déjà des `list[str]`
  exploitables sans `_split_tags` (point d'intégration noté en §3.4).
