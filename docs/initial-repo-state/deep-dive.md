# Deep dive — flux `/chat` et chaîne d'ingestion, fichier par fichier

Complément à `first-sight.md` (qui donne la vue d'ensemble). Ici : trace fonction par
fonction, avec la forme exacte des données à chaque étape, plus le détail des contrats
attendus par les stubs d'ingestion (tels que définis par `tests/acceptance/`).

---

## Partie 1 — Trace microscopique du flux `/chat`

### 1. Frontend — saisie utilisateur (`web/app/page.tsx`)

- État local : `selected: Set<string>` (sujets cliqués), `customTopic: string` (champ libre),
  `question: string`.
- `effectiveTopics` (`page.tsx:44-49`, `useMemo`) = `[...selected, customTopic.trim()]`
  (le custom topic n'est ajouté que s'il est non vide).
- Clic sur "Lancer la veille" → `launch()` (`page.tsx:60-73`) :
  ```ts
  postChat(question.trim(), effectiveTopics)
  ```

### 2. Client HTTP — `web/lib/api.ts:27-38`

```ts
postChat(question, topics)
  → fetch POST `${API_URL}/chat`
    body = JSON.stringify({ question, topics })
  → ChatResponse = { answer: string, cards: ArticleCard[], status: "ok"|"empty"|"degraded" }
```
`API_URL` vient de `NEXT_PUBLIC_API_URL` (slash final retiré), fallback `http://localhost:8000`.

### 3. Endpoint FastAPI — `app/main.py:39-41`

```python
@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    return await handle_chat(req)
```
`ChatRequest` (`app/schemas.py:32-34`) = `{question: str, topics: list[str] = []}`, validé par
Pydantic à l'entrée (FastAPI rejette automatiquement un body malformé en 422).

### 4. Orchestrateur — `app/chat.py:14-31` (`handle_chat`)

Séquence exacte :

1. **`query = _expand_query(req.question, req.topics)`** (`chat.py:36-39`)
   - Si `topics` est vide → `query = question`
   - Sinon → `query = f"{question} | {', '.join(topics)}"`
   - Exemple : `"Quelles tendances ?" + ["Python","AI/ML"]` → `"Quelles tendances ? | Python, AI/ML"`

2. **`retrieved = retrieval.retrieve(query, k=8)`** → `list[dict]` (détail §5)

3. **Enrichissement (optionnel)** :
   ```python
   try:
       enriched = ingest_enrich.enrich_retrieval(retrieved)
   except NotImplementedError:
       enriched = []
   if enriched:
       retrieved = retrieved + enriched
   ```
   `enrich_retrieval` reçoit la liste normalisée `{id, content, metadata, distance}` et doit
   renvoyer une liste de chunks de même forme à concaténer. Stub actuel → `NotImplementedError`
   → `enriched = []` → `retrieved` inchangé.

4. **Actu fraîche (optionnelle)** :
   ```python
   try:
       fresh = await fresh_news.fetch(topics=req.topics, since=None)
   except NotImplementedError:
       fresh = []
   ```
   Stub actuel → `fresh = []`.

5. **`return await compose_answer(question=req.question, topics=req.topics, retrieved_chunks=retrieved, fresh_articles=fresh)`**
   → `ChatResponse` (détail §7)

> Le double `try/except NotImplementedError` est ce qui permet au pipeline de tourner de
> bout en bout *avant même* que l'ingestion soit implémentée — `retrieved` peut rester `[]`
> et `compose_answer` bascule alors en `status="empty"`.

### 5. Retrieval vectoriel — `app/rag/retrieval.py:27-51` (`retrieve`)

```python
def retrieve(query: str, k: int = 8) -> list[dict[str, Any]]:
```

1. `collection = get_collection()` → voir §6 (Chroma)
2. `query_vec = embed(query)` (`retrieval.py:21-24`)
   - `get_embedder()` (`:15-18`, `@lru_cache`) charge
     `SentenceTransformer(settings.embedding_model)` = `intfloat/multilingual-e5-small`
   - `embedder.encode([query], normalize_embeddings=True)[0].tolist()` → `list[float]`
     (vecteur normalisé, donc compatible avec la métrique `cosine` de la collection)
3. `result = collection.query(query_embeddings=[query_vec], n_results=k)`
   - Le client Chroma renvoie un dict avec des listes-de-listes (une sous-liste par
     embedding de requête, ici une seule) : `{"ids": [[...]], "documents": [[...]],
     "metadatas": [[...]], "distances": [[...]]}`
4. **Garde-fou global** : tout le bloc requête est dans un `try/except Exception` →
   en cas d'erreur (Chroma down, modèle introuvable, etc.), log `logger.warning` et
   `return []`. Aucune exception ne remonte à `handle_chat`.
5. Normalisation (`retrieval.py:36-50`) : `zip(ids[0], docs[0], metas[0], distances[0],
   strict=False)` → liste de dicts :
   ```python
   {"id": doc_id, "content": doc, "metadata": meta or {}, "distance": dist}
   ```
   C'est **cette forme exacte** (`id/content/metadata/distance`) qui circule ensuite dans
   tout le pipeline (`enrich_retrieval`, `_format_context`, `_build_cards`).

### 6. Client Chroma — `app/rag/chroma_client.py`

- `get_client()` (`:13-23`, `@lru_cache(maxsize=1)`) : parse `settings.chroma_url`
  (`http://chromadb:8000` en docker, `http://localhost:8002` en accès host) avec
  `urlparse`, instancie `chromadb.HttpClient(host, port, settings=ChromaSettings(anonymized_telemetry=False))`.
- `get_collection()` (`:26-32`, **non cachée** — rappelle `get_client()` à chaque fois,
  mais `get_client` lui est mis en cache donc pas de coût de reconnexion) :
  `client.get_or_create_collection(name="articles", metadata={"hnsw:space": "cosine"})`.
  La métrique cosinus est cohérente avec `normalize_embeddings=True` côté retrieval.

### 7. Composition de la réponse — `app/rag/llm.py:101-150` (`compose_answer`)

Signature : `compose_answer(*, question, topics, retrieved_chunks, fresh_articles) -> ChatResponse`

**Étape commune à tous les cas** : `cards = _build_cards(retrieved_chunks, fresh_articles)`
(`llm.py:60-88`) — transforme les deux listes hétérogènes en `list[ArticleCard]` homogène :

| Source            | `title`                  | `source`                | `date`            | `snippet`                                  | `url`             | `tags`                          |
|-------------------|--------------------------|-------------------------|-------------------|--------------------------------------------|-------------------|---------------------------------|
| chunk indexé      | `metadata["title"]` (déf. "Sans titre") | `metadata["source"]` (déf. "interne") | `metadata["date"]` | `content[:280]`                             | `metadata["url"]` (déf. "") | `_split_tags(metadata["tags"])` |
| article frais     | `art["title"]` (déf. "Sans titre")      | `art["source"]` (déf. "newsapi")      | `art["date"]`      | `art["content"]` ou `art["description"]`, `[:280]` | `art["url"]` (déf. "")      | `art["tags"]` (déf. `[]`, **pas** de split) |

`_split_tags` (`llm.py:91-98`) gère 3 formats côté metadata Chroma : `list` → cast en `str`,
`"a, b"` (string) → split sur virgule + `strip`, sinon → `[]`. *(Asymétrie à noter : les
tags des articles frais ne passent pas par `_split_tags`, donc ils doivent déjà être une
`list[str]` côté `fresh_news.fetch`.)*

Puis **3 branches mutuellement exclusives** :

**(a) Rien à montrer** — `not retrieved_chunks and not fresh_articles` (`:110-118`)
→ `ChatResponse(answer="Aucun article ne couvre encore ce sujet…", cards=[], status="empty")`
(message statique invitant à lancer une ingestion).

**(b) LLM non configuré** — `get_llm()` retourne `None` (`:120-129`)
`get_llm()` (`llm.py:26-37`, `@lru_cache`) renvoie `None` si
`azure_ai_inference_endpoint` ou `azure_ai_inference_api_key` est vide (`""` par défaut
dans `Settings`). → `ChatResponse(answer=f"{len(cards)} article(s) trouvé(s)… LLM non
configuré — voici les sources brutes.", cards=cards, status="degraded")`.

**(c) LLM disponible** (`:131-150`)
1. `_format_context(retrieved, fresh)` (`llm.py:40-57`) construit un texte avec deux
   sections optionnelles :
   - `## Index interne` → pour chaque chunk : `f"[{i}] {title} — {source} ({date})\n{content[:600]}"`
   - `## Actualité fraîche` → pour chaque article frais : `f"[F{i}] {title} — {source} ({date})\n{content[:600]}\n{url}"`
   - Si aucune des deux listes n'est non-vide → `"(aucune source disponible)"`
2. `user_payload = {"question": ..., "topics": ..., "context": <texte ci-dessus>}`,
   sérialisé en JSON (`ensure_ascii=False`)
3. `llm.ainvoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(json.dumps(user_payload))])`
   — `SYSTEM_PROMPT` (`llm.py:17-23`) impose une sortie JSON stricte `{answer, cards}`
   en français
4. `_extract_answer(raw)` (`llm.py:153-160`) : tente `json.loads(raw)["answer"]`, sinon
   renvoie `raw.strip()` tel quel (tolère un LLM qui ne respecte pas le format JSON)
5. **Si l'appel LLM lève une exception** (réseau, auth, parsing…) : pas de propagation —
   message de repli `f"Synthèse indisponible (erreur LLM). {len(cards)} article(s) référencé(s)."`
6. Dans tous les sous-cas de la branche (c) → `status="ok"` (même en cas d'erreur LLM —
   seul le texte de `answer` change ; les `cards`, elles, sont toujours présentes)

> Note : les `cards` retournées par le LLM lui-même (champ `cards` du JSON `{answer, cards}`
> attendu par `SYSTEM_PROMPT`) sont **ignorées** — `compose_answer` reconstruit toujours
> `cards` localement via `_build_cards`. Seul `answer` est extrait de la réponse du modèle.

### 8. Retour au frontend — affichage (`web/app/page.tsx:161-195`)

- `resp.answer` → bloc "Synthèse" (texte brut, `whitespace-pre-wrap`)
- `resp.status !== "ok"` → ligne `statut : {status}` affichée sous la synthèse
- `resp.cards` → grille de `<Card>` (`page.tsx:201-248`) ; si `cards.length === 0` →
  message "Aucun résultat pour le moment."
- Chaque `Card` affiche `title`, `source` + date formatée `fr-FR`, `snippet` (clampé à
  3 lignes via CSS `WebkitLineClamp`), badges `tags` colorés via `tagColor()`
  (`page.tsx:21-25` — hash polynomial `h = h*31 + charCode`, modulo `TAG_COLORS.length = 6`,
  donc couleur déterministe par libellé de tag), et lien externe `url` si non vide.

---

## Partie 2 — Contrats des stubs d'ingestion (spec exécutable = `tests/acceptance/`)

Ces fonctions lèvent toutes `NotImplementedError` aujourd'hui. Voici la forme exacte que
chaque test attend en sortie — c'est le contrat à respecter à l'implémentation.

### `app/ingest/news_api.py` — `NewsApiIngester.run(topics: list[str]) -> list[dict]`
*(`tests/acceptance/test_news_api_ingester.py`)*

- Dataclass : `settings: Settings | None = None`, auto-rempli via `get_settings()` dans
  `__post_init__` si `None` — donc `run` doit utiliser `self.settings` (déjà non-`None` à
  ce stade) pour lire `news_api_key` / `news_api_base_url`.
- Chaque article retourné doit contenir au minimum les clés :
  `id, title, source, date, url, content`.
- `topics=[]` → doit retourner `[]` (ou toute liste, le test est permissif :
  `articles == [] or isinstance(articles, list)`).
- **Dédoublonnage inter-sujets** : `run(["python", "python"])` doit renvoyer des `id`
  uniques (`len(ids) == len(set(ids))`) — donc l'ingester doit dédupliquer les articles
  qui ressortent pour plusieurs topics avant de les renvoyer (pas seulement laisser
  `cleaning.dedupe` s'en charger en aval).

### `app/ingest/scraper.py` — `Scraper.run(urls: list[str]) -> list[dict]`
*(`tests/acceptance/test_scraper.py`)*

- Dataclass : `user_agent: str = "nauda-palisse-veille/0.1"`, `timeout: float = 10.0`
  (probablement à passer à un client `httpx`).
- Chaque article retourné doit contenir au minimum : `title, url, content, source`.
- **Tolérance aux pannes réseau** : `run(["http://127.0.0.1:1/does-not-exist"])` doit
  renvoyer une `list` (pas lever d'exception) — donc chaque URL doit être scrapée dans un
  bloc protégé individuellement (une URL en échec ne doit pas faire échouer tout le run).

### `app/ingest/cleaning.py` — pipeline de nettoyage
*(`tests/acceptance/test_cleaning.py`)*

| Fonction | Contrat testé |
|---|---|
| `clean_html_to_markdown(html: str) -> str` | Le HTML `<article><h1>Titre</h1><p>Para <b>gras</b>.</p></article>` doit donner un texte **sans** la balise littérale `<h1>`, mais conservant les mots `"Titre"` et `"gras"` — conversion vers du Markdown (cohérent avec la dépendance `markdownify` listée dans `pyproject.toml`), pas un simple strip de tags. |
| `dedupe(articles: list[dict]) -> list[dict]` | Déduplique par `url` : sur 3 articles dont 2 partagent la même `url` (`a` et `b` → `https://x.example/a`), doit n'en garder que 2 (et toutes les `url` du résultat doivent être uniques). L'ordre / lequel des deux doublons est gardé n'est pas testé. |
| `chunk(text: str, max_chars: int = 1200) -> list[str]` | Pour un texte de ~4000 caractères et `max_chars=600` : doit produire **au moins 2** chunks, chacun **≤ 700** caractères (~ `max_chars + 100` de marge — laisse une tolérance pour ne pas couper au milieu d'une phrase/mot). |
| `strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup` | Doit retirer les éléments `<nav>` et `<footer>` (texte "menu"/"copyright" absent du `.get_text()` final) tout en conservant le contenu de `<main>` ("contenu" présent) — retourne un objet `BeautifulSoup` modifié (pas une string). |

### `app/ingest/enrich.py` — `enrich_retrieval(retrieved: list[dict]) -> list[dict]`

Pas de test d'acceptance dédié (seulement utilisé via `chat.py:20-25`). Reçoit la liste
*déjà normalisée* par `retrieval.retrieve` (forme `{id, content, metadata, distance}`) et
doit renvoyer une liste de chunks **supplémentaires** de la même forme à concaténer —
`handle_chat` fait `retrieved + enriched` seulement si `enriched` est non-vide.

### `app/runtime/fresh_news.py` — `fetch(topics: list[str], since: datetime | None = None) -> list[dict]` (async)
*(`tests/acceptance/test_fresh_news.py`)*

- Coroutine (`async def`, appelée avec `await` dans `chat.py:24`).
- Chaque article retourné doit contenir au minimum : `title, url, source`. *(Pour bien
  s'intégrer à `_build_cards`/`_format_context` côté `llm.py`, il faudra aussi fournir
  `content` et/ou `description`, `date`, et idéalement `tags: list[str]` déjà normalisés
  — voir l'asymétrie notée en §7, ces tags ne repassent **pas** par `_split_tags`.)*
- `since` : un `datetime` (ex. `utcnow() - timedelta(days=2)`) doit être accepté sans
  erreur — sert à filtrer les articles trop anciens (le test ne vérifie que l'absence
  d'erreur, pas le filtrage effectif).
- `topics=[]` → doit retourner `[]` ou toute `list`.

### `scripts/ingest_cli.py` — CLI Typer

- `news --topic/-t <topic> [--topic ...]` et `scrape --url/-u <url> [--url ...]` : deux
  commandes encore en `raise NotImplementedError`, censées orchestrer respectivement
  `NewsApiIngester.run` → nettoyage → indexation Chroma, et `Scraper.run` → idem. Pas de
  test d'acceptance dédié — à câbler une fois les briques du dessus prêtes.
