# Fonctionnement de fresh_news — actu fraîche au moment du chat — 2026-06-21

Parcours complet du hook `fresh_news` : ce qu'il fait, d'où viennent les articles, et
comment ils arrivent jusqu'à la réponse du LLM.

> **Note historique** — Ce hook s'appuyait initialement sur des flux RSS
> (cf. `docs/steps/14-fresh-news-rss.md`, désormais **obsolète**). Depuis le 2026-06-21,
> RSS est devenu un **ingester froid** (`app/ingest/sources_ingesters/rss_feed.py`,
> SQLite → Chroma) et `fresh_news` s'appuie sur **deux nouvelles sources live** :
> les **releases GitHub** des dépôts suivis et le **scraper TLDR** sur les jours récents.

---

## Le rôle de fresh_news dans le pipeline

Il faut distinguer **deux mondes** dans le projet :

- l'**index** (Chroma) — la base de connaissance, alimentée *hors ligne* par l'ingestion
  (arXiv, TLDR, RSS) puis interrogée à chaque chat par `retrieval.retrieve()` ;
- le **frais** (`fresh_news`) — de l'actu *chaude* récupérée **en direct au moment du
  chat**, qui n'est **pas** dans l'index et n'y entre jamais.

```
                          POST /chat
                              │
                   chat.handle_chat()
                              │
        ┌─────────────────────┴─────────────────────┐
        ▼                                            ▼
  retrieval.retrieve(query)              fresh_news.fetch(topics, since=None)
  (index Chroma, hors ligne)             (GitHub + TLDR, EN DIRECT)
        │                                            │
        └─────────────────────┬─────────────────────┘
                              ▼
                    llm.compose_answer()
              (retrieved_chunks + fresh_articles → cartes)
```

`fresh_news.fetch` est appelé à **chaque `/chat`** (`app/chat.py:27`,
`await fresh_news.fetch(topics=req.topics, since=None)`). Son rôle : compléter les chunks
indexés (parfois un peu datés) par ce qui vient de sortir.

---

## Le contrat à respecter

Imposé par `tests/acceptance/test_fresh_news.py` et `llm._build_cards` :

- `async def fetch(topics, since) -> list[dict]` ;
- chaque dict a au minimum `title, url, source` (+ `date`, `content`, `tags` en pratique) ;
- `tags` est déjà une **`list[str]`** : les cartes fraîches ne passent **pas** par
  `_split_tags` côté LLM (contrairement aux chunks indexés) ;
- **ne lève jamais** : toute panne (réseau, token absent, page 404…) dégrade en `[]`, sinon
  le chat casse.

`topics` et `since` sont **acceptés mais non utilisés** : les deux sources sont déjà bornées
en fraîcheur (dernière release / TLDR sur les jours récents), donc pas de filtrage
supplémentaire. Les paramètres restent dans la signature pour honorer le contrat de
l'appelant.

---

## Les deux sources

### Source 1 — GitHub releases (`_fetch_github`)

L'« API » du besoin : suivre les **nouvelles versions** des dépôts qui comptent pour la
veille.

1. Lit la liste `settings.sources.github_watched_repos` (config `WATCHED_REPOS` dans
   `.env`, JSON `[{"owner": "...", "name": "..."}]`). Liste vide → `[]` immédiat.
2. Ouvre **un** `httpx.AsyncClient` (async, timeout 8 s). Le token
   `GITHUB_RELEASES_TOKEN`, **s'il est présent**, part en header `Authorization: Bearer …`
   (relève la limite de 60 → 5000 req/h) ; sinon l'appel est **anonyme** et fonctionne
   quand même.
3. Pour chaque dépôt : `GET /repos/{owner}/{name}/releases/latest`. **Un dépôt en erreur**
   (pas de release → 404, réseau, rate-limit) est loggé et **sauté** — il n'invalide pas
   les autres.
4. `_normalize_release` mappe la réponse JSON GitHub → la forme commune :

   | Champ carte | Source GitHub |
   |---|---|
   | `title` | `"{name} {tag_name}"` (ex. `langgraph 1.2.6`) |
   | `url` | `html_url` (page de la release) |
   | `source` | `"github.com/{owner}/{name}"` |
   | `date` | `published_at` (ISO 8601) |
   | `content` | `body` (déjà en Markdown — pas de conversion) |
   | `tags` | `[]` |

On prend **la dernière release de chaque dépôt, toujours** (sans filtre de date) : simple et
déterministe. Le LLM jugera la pertinence selon la question.

### Source 2 — TLDR.tech en direct (`_fetch_tldr`)

L'actu généraliste multitopics. **On ne réimplémente rien** : on **réutilise la classe
`TldrScraper`** de l'ingestion (`app/ingest/sources_ingesters/tldr_scraper.py`) — mêmes
`build_urls` + `run`, donc même parsing, même exclusion des sponsors `(Sponsor)`, même
dérivation de `reference`.

La seule chose propre à fresh_news, c'est **le choix des dates** : la **cascade**
`_scrape_tldr_cascade`.

```
today (J)   → scrape les 9 éditions → des articles ?  ── oui ─▶ on s'arrête, on renvoie
   │ non
J-1         → scrape les 9 éditions → des articles ?  ── oui ─▶ on s'arrête, on renvoie
   │ non
J-2         → scrape les 9 éditions → des articles ?  ── oui ─▶ on s'arrête, on renvoie
   │ non
   └─▶ []  (3 jours vides : dégrade proprement)
```

- On **s'arrête au premier jour qui renvoie des articles** (pas de cumul des 3 jours).
- Borne dure : `_TLDR_LOOKBACK_DAYS = 3` (J, J-1, J-2 maximum).
- **Pourquoi une cascade ?** L'édition TLDR du jour peut ne pas être encore publiée (tôt
  dans la journée) ou manquante (week-end). Reculer jour par jour garantit qu'on récupère
  *quelque chose* de récent au lieu de revenir bredouille.

**Détail async important :** `TldrScraper.run` est **synchrone** (`httpx.Client`). Pour ne
pas bloquer la boucle async du chat, toute la cascade est déportée dans un thread via
`asyncio.to_thread(_scrape_tldr_cascade, …)`.

`_normalize_tldr` convertit chaque `TldrArticle` (Pydantic) en dict commun : `title`,
`url`, `source` (`"tldr.tech"`), `date` (`published_date.isoformat()` ou `None`),
`content`, `tags=[]`.

---

## L'assemblage : `fetch()`

```
settings = get_settings()
github = await _fetch_github(settings)   # try/except → []
tldr   = await _fetch_tldr(settings)     # try/except → []
return github + tldr
```

Chaque source est dans **son propre `try/except`** : si GitHub tombe, on garde TLDR, et
inversement. Au pire, `fetch` renvoie `[]` et le chat fonctionne quand même (avec les seuls
chunks de l'index). Un log de bilan indique le compte par source
(`fresh_news: N fresh article(s) (github=…, tldr=…)`).

---

## Ce qu'il faut retenir (idées de conception)

1. **Frais ≠ index.** fresh_news vit en parallèle de l'index, n'écrit nulle part, n'est
   jamais persisté. C'est du jetable recalculé à chaque requête.
2. **Réutilisation, pas duplication.** TLDR live réutilise le `TldrScraper` de
   l'ingestion ; seule la stratégie de dates change (cascade vs watermark incrémental).
3. **Tolérance aux pannes partout.** `try/except` par dépôt GitHub, par URL TLDR (dans le
   scraper), et par source dans `fetch` — jamais de « tout ou rien ».
4. **Forme commune.** GitHub et TLDR produisent exactement le même dict
   `{title, url, source, date, content, tags}`, directement consommable par
   `llm._build_cards` sans traitement par-source.

---

## Configuration (`.env`)

| Clé | Rôle |
|---|---|
| `WATCHED_REPOS` | dépôts GitHub suivis, JSON `[{"owner": "...", "name": "..."}]` |
| `GITHUB_RELEASES_TOKEN` | token GitHub *optionnel* (relève la limite de requêtes) |
| `GITHUB_API_URL` | base de l'API GitHub (`https://api.github.com` par défaut) |
| `TLDR_EDITIONS` | éditions TLDR scrapées, JSON `[{"name": "ai"}, …]` (partagé avec l'ingestion) |
| `TLDR_BASE_URL` | base des URLs TLDR (`https://tldr.tech` par défaut) |

> ⚠️ Les éditions TLDR du *live* sont les **mêmes** que celles de l'ingestion froide
> (`TLDR_EDITIONS`). Avec les 9 éditions par défaut, un `/chat` peut récupérer ~100 articles
> frais sur un jour complet. Si la latence ou le bruit gênent, deux leviers simples :
> plafonner le nombre d'articles renvoyés, ou réduire les éditions utilisées en live.

---

## Lancement manuel

```bash
uv run python -m app.runtime.fresh_news
```

Affiche les logs (INFO) — dont la cascade TLDR et le compte par source — puis un récap
lisible des articles récupérés (`source — date`, `title`, `url`).
