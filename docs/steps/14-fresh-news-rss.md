# fresh_news — agrégateur RSS générique

> **Statut : implémenté** (2026-06-13). Couvre le point 4 (« fresh news ») :
> `app/runtime/fresh_news.py`, le hook d'actu *chaude* injectée au moment du chat (non
> indexée). 3 fichiers écrits et validés (`make lint`, `mypy`, `pytest test_fresh_news`
> 3/3 au vert) — voir « Réalisé » en bas.

## Le besoin

`fresh_news.fetch(topics, since)` est appelé à **chaque `/chat`** (`app/chat.py:27`) pour
injecter de l'actu fraîche **qui n'est pas dans l'index**. Contrat (cf.
`tests/acceptance/test_fresh_news.py` + SPECS §3.4) :

- `async def fetch(topics, since) -> list[dict]` ;
- chaque dict a au minimum `title, url, source` ;
- **ne lève jamais** (sinon le chat casse) → dégrade en `[]` ;
- `tags` déjà en `list[str]` (les cartes fraîches ne passent pas par `_split_tags`).

## Le choix : flux RSS, pas Perplexity / Gmail / NewsAPI

Options explorées et écartées :

| Option | Verdict |
|---|---|
| Perplexity (recherche live) | ❌ API payante, clé requise |
| Gmail « mail du jour » | ❌ parsing HTML par expéditeur (fragile), pas d'URL unique par carte, latence IMAP/chat. Mieux adapté à une *ingestion* future qu'à du jetable. |
| NewsAPI palier gratuit | ⚠️ clé requise + délai 24 h + dev-only |
| Hacker News (Algolia) | ✅ gratuit sans clé, mais liens communautaires bruts |
| **Flux RSS** | ✅ **retenu** — gratuit, sans clé, vraies cartes de presse (titre/URL/résumé/tags), et **générique** |

### Pourquoi RSS est le bon choix DRY

Contrairement au scraping TLDR (sélecteurs HTML sur-mesure **par site**), **RSS est un
format standardisé**. Un **seul** parser (`feedparser`) lit n'importe quel flux et expose
les mêmes champs (`entry.title`, `entry.link`, `entry.published_parsed`, `entry.tags`…),
que le flux vienne d'OpenAI, de Hugging Face ou de MIT. → **zéro code par-source**, un seul
chemin de code pour N flux. C'est ça, le générique/DRY.

Flux de départ (cf. `docs/conception/rss-feed-urls.md`) :

- OpenAI News — `https://openai.com/news/rss.xml`
- Hugging Face Blog — `https://huggingface.co/blog/feed.xml`
- MIT Technology Review (AI) — `https://www.technologyreview.com/topic/artificial-intelligence/feed/`

## Architecture cible

```
config: rss_feeds = [url1, url2, url3]        ← liste de feeds (.env, JSON)
        │
        ▼  pour chaque url (try/except : un flux KO n'arrête pas les autres)
   httpx.AsyncClient.get(url)                  ← télécharge le XML (timeout court)
        ▼
   feedparser.parse(contenu)                   ← LE parser générique (uniformise tout)
        ▼  pour chaque entry
   {title, url, source, date, content, tags}   ← normalisation commune
        ▼
   fusion + filtre `since` + cap N items/flux   ← fetch() assemble
        ▼
   list[dict]  →  compose_answer → cartes
```

## Décisions retenues (KISS / YAGNI)

- **Parser : `feedparser`** (et pas `lxml` comme arXiv) : gère seul les dates RFC-822
  (`Tue, 10 Jun 2026 …`), les namespaces et les variantes RSS/Atom. Beaucoup plus doux à
  écrire et à lire.
- **Feeds en `list[str]`** (URLs seules) en config ; `source` **dérivée du titre du flux**
  (fallback domaine) → pas de nom à répéter en config. Plus DRY que `list[{name, url}]`.
- **Filtrage par `topics` : ignoré** pour ce jet. Les flux sont déjà curés (AI/tech) ; on
  renvoie les items récents de tous les flux et on laisse le LLM trier. Filtrer par mot-clé
  risquerait de tout vider. À raffiner si besoin.
- **Cap à 5 items récents par flux** : évite d'inonder le contexte du LLM. Ajustable.
- **Tolérance aux pannes** : `try/except` par flux **et** global → un flux down ou un XML
  pourri ne casse rien, `fetch` renvoie au pire `[]` (cohérent avec `Scraper.run` /
  l'ingester arXiv).
- **Hors périmètre (YAGNI)** : pas de cache, pas de `asyncio.gather` concurrent (boucle
  séquentielle d'abord) ; on optimisera si la latence gêne.

## Plan d'implémentation (3 fichiers, un à la fois)

1. **`pyproject.toml`** — ajouter la dépendance `feedparser`, puis `uv sync`.
   *C'est lui qui rend le module générique.*
2. **`app/config.py` + `.env.example`** — ajouter `rss_feeds: list[str]` dans `Sources`
   (mêmes mécaniques que `arXiv_topics` / `github_watched_repos`, chargé en JSON depuis
   `.env`), défaut = les 3 flux ci-dessus.
   *Ajouter un flux = éditer une ligne `.env`, jamais le code.*
3. **`app/runtime/fresh_news.py`** — le cœur, deux fonctions :
   - `_normalize_entry(entry, feed_title) -> dict` : mappe une entrée feedparser →
     `{title, url, source, date, content, tags}`. `source` = titre du flux (fallback
     domaine), `tags` = `entry.tags` → liste, `content` =
     `clean_html_to_markdown(entry.summary)` (réutilise l'existant).
   - `async def fetch(topics, since)` : boucle sur `settings.sources.rss_feeds`,
     télécharge (httpx async, timeout court), `feedparser.parse`, normalise, filtre
     `since`, cap N items/flux, fusionne. `try/except` par flux + global → `[]`.

## Validation prévue

- `test_fresh_news.py` repasse au vert (renvoie une liste, ne lève pas) ; timeout court
  pour ne pas pendre sur le réseau.
- `make lint` / `make typecheck` sans nouvelle alerte sur `fresh_news.py` / `config.py`.
- Vérif manuelle : `/chat` renvoie des cartes « actu fraîche » avec `title`/`url`/`source`
  exploitables.

## Réalisé

- **`pyproject.toml`** : dépendance `feedparser>=6.0,<7` (+ `uv sync` → `feedparser 6.0.12`).
- **`app/config.py` + `.env.example`** : `rss_feeds: list[str]` (3 flux par défaut) et
  `rss_max_items_per_feed: int = 5` dans `Sources`.
- **`app/runtime/fresh_news.py`** : `_entry_datetime` (struct_time → datetime),
  `_normalize_entry` (entrée feedparser → carte commune), `fetch` (boucle **séquentielle**
  sur les flux, `httpx.AsyncClient` timeout 8 s, filtre `since`, cap par flux,
  `try/except` par flux + global → `[]`).

**Note async :** `fetch` reste `async def` car le contrat l'impose (`await
fresh_news.fetch(...)` dans `chat.py` et le test). La boucle est **séquentielle** — pas de
`asyncio.gather` (concurrence reportée, cf. décisions ci-dessus).

**Validation :** `make lint` ✅, `mypy app/runtime/fresh_news.py app/config.py` ✅, les 3
tests d'acceptance `test_fresh_news.py` passent (ils appellent réellement les flux).

## Suite possible

- Élargir la liste de flux (`.env`) — c'est tout l'intérêt du générique.
- Réintroduire un filtrage par `topics` (match titre/tags) si le bruit gêne.
- Gmail → tâche d'ingestion séparée (stockée/indexée), distincte de ce hook runtime.
