# État du projet — 2026-06-13

Récap de reprise après une pause.

## 📍 Où en est le projet

Le projet a bien dépassé le squelette initial. **Il a pivoté** par rapport à la spec
d'origine : au lieu de NewsAPI direct → Chroma, l'architecture est désormais **à deux
étages avec un store SQLite intermédiaire**.

### L'architecture réelle aujourd'hui

```
SOURCES                    INGEST → SQLite (ingest.db)        INDEX → Chroma
─────────                  ──────────────────────────        ──────────────
arXiv API   ─┐
             ├─▶  app/data/article_store.py  ──▶  index  ──▶  collection "articles"
TLDR.tech   ─┘   (table article, statut)         (chunk+embed)
```

Le SQLite sert de **zone tampon** : on ingère d'abord (statut brut), puis on indexe vers
Chroma dans une seconde passe. Ça découple récupération et vectorisation.

### ✅ Ce qui est fait (commits récents)

| Brique | État |
|---|---|
| **arXiv API** (`app/ingest/arXiv_api.py`) | ✅ ingestion incrémentale + **pagination** (arrêt sur watermark ou plafond), tolérance aux pannes |
| **TLDR scraper** (`app/ingest/tldr_scraper.py`) | ✅ ingestion incrémentale par dates manquantes |
| **Ingestion incrémentale (watermark)** | ✅ `MAX(published_date)` / `MAX(updated_date)`, dérivé de la base — voir step 12 |
| **Store SQLite** (`app/data/`) | ✅ `article_store.py`, migrations, modèle `Article`, export CSV |
| **Indexation Chroma** (`make index`) | ✅ chunk (LangChain `RecursiveCharacterTextSplitter`) + embed + upsert |
| **`cleaning.py`** | ✅ `clean_html_to_markdown`, `strip_boilerplate`, `dedupe`, `chunk` |
| **Makefile** | ✅ cibles `ingest` / `index` / `arxiv` / `tldr` / `pipeline-e2e` |
| **Doc** | ✅ 14 fichiers `docs/steps/` qui tracent toutes les décisions |

### ⏳ Ce qui reste en `NotImplementedError`

- **`app/ingest/news_api.py`** → `NewsApiIngester.run` (stub). C'est le **point 1 du
  TODO** (« requêter l'API »), mais en pratique il a été résolu autrement via arXiv.
  À décider : on garde NewsAPI ou on l'abandonne ?
- **`app/runtime/fresh_news.py`** → `fetch` (stub) — l'injection d'actu fraîche au moment
  du `/chat`.
- **`app/ingest/enrich.py`** → hook optionnel (nice-to-have).
- **`scripts/ingest_cli.py`** : a des commandes `fetch`/`index`/`tldr` câblées, mais
  `news`/`scrape` sont probablement encore des stubs.

### 📋 Selon le TODO

1. **Requêter l'API → base de connaissance** : ✅ fait (via arXiv, pas NewsAPI)
2. **Scraping 2-3 sources tech** : 🟡 en cours — TLDR fait (1/3). Restent un changelog
   produit + une page de doc/annonce.

### Décision en suspens

`docs/SOURCES.md` mentionne **GitHub Releases API** comme prochaine source « core data »
prévue, mais pas encore implémentée.

---

**Pour repartir** : deux pistes ouvertes —

- vérifier l'état runtime (lancer `make test` + compter les docs dans `ingest.db` / Chroma
  pour voir si la base est peuplée),
- ou attaquer directement la suite (point 2 du TODO : 2ᵉ source de scraping, type GitHub
  Releases).
