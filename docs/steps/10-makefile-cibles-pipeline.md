# Étape 10 — Restructuration des cibles Makefile du pipeline

## Contexte

Avec l'arrivée du scraper TLDR (étape 8), le pipeline compte désormais deux
sources d'ingestion (arXiv via API, TLDR via scraping). L'ancien `make ingest`
mélangeait deux responsabilités : il enchaînait le fetch arXiv **et**
l'indexation Chroma, sans inclure TLDR. Une commande `make` dédiée au scraping
TLDR venait d'être ajoutée (`ingest-tldr`), il fallait donc repenser la
structure d'ensemble des cibles.

---

## Question — Comment découper les cibles `make` ?

### Proposition

Un `make ingest` qui regroupe toutes les sources (API + scraping) jusqu'à
SQLite, un `make index` qui reprend les entrées `status='ingested'` de SQLite
jusqu'à Chroma (chunking + embedding + upsert), et un `make pipeline-e2e` pour
le bout en bout.

### Discussion

**La frontière ingest / index existe déjà dans l'architecture.** SQLite sert
de tampon entre les deux phases (étapes 2 et 3) :

```
sources (arXiv, TLDR) ──ingestion──▶ SQLite (status='ingested') ──indexation──▶ Chroma (status='indexed')
```

Les cibles `make` doivent refléter cette frontière (SOC). Bénéfice concret :
ré-ingérer sans ré-indexer, indexer sans re-scraper, ou relancer une seule
source. Grâce au statut en SQLite, chaque étape est rejouable indépendamment
sans dupliquer de données.

**Séquentiel plutôt que parallèle pour les sources.** Les deux ingesters
écrivent dans la même base SQLite, qui gère mal les écritures concurrentes
(verrous). `make -j` apporterait du risque pour gagner quelques secondes —
KISS, on garde l'exécution séquentielle par défaut des prérequis Make.

Limite acceptée : si `ingest-arxiv` échoue (exception non rattrapée),
`ingest-tldr` ne tournera pas. Cas marginal : les ingesters avalent déjà les
erreurs réseau et renvoient une liste vide.

**Nommage.**

- `ingest-fetch` → `ingest-arxiv` : avec deux sources, « fetch » ne disait
  plus laquelle. Chaque sous-cible nomme sa source.
- `ingest-index` → `index` : reflète le nom de la commande CLI
  (`ingest_cli.py index`), une seule convention à retenir.
- `pipeline-e2e` : nom explicite pour le bout en bout, qui lève l'ambiguïté
  de l'ancien `make ingest` entre « ingérer » et « tout faire ».
- Pas de cible séparée pour chunk / embedding / upsert : tout est déjà dans
  `index_articles()` appelé par la commande CLI `index`, une seule cible
  `make` suffit (YAGNI).

### Décision retenue

```makefile
pipeline-e2e: ingest index          # bout en bout

ingest: ingest-arxiv ingest-tldr    # toutes les sources → SQLite

ingest-arxiv:
	PYTHONPATH=. uv run python scripts/ingest_cli.py fetch

ingest-tldr:
	PYTHONPATH=. uv run python scripts/ingest_cli.py tldr

index:                              # SQLite → Chroma (chunk + embedding + upsert)
	PYTHONPATH=. CHROMA_URL=http://localhost:8002 uv run python scripts/ingest_cli.py index
```

Cinq niveaux de granularité : une source seule (`ingest-arxiv`,
`ingest-tldr`), toutes les sources (`ingest`), l'indexation seule (`index`),
ou tout d'un coup (`pipeline-e2e`).

CLAUDE.md et README mis à jour en conséquence (l'ancien `make ingest`
documenté ne correspondait plus au comportement).

### Pour personnaliser le scraping TLDR

`make` ne transmet pas facilement d'arguments — passer directement par le
CLI :

```bash
PYTHONPATH=. uv run python scripts/ingest_cli.py tldr -e ai -d 2026-06-09
```

Par défaut, `make ingest-tldr` scrape les éditions `tech`, `webdev` et `ai`
à la date du jour.

---

## Révision 2026-06-10 — Renommage source-d'abord + cibles bout en bout par source

Les cibles par source sont renommées au format « source d'abord », plus
naturel à l'autocomplétion (`make arxiv<TAB>` liste tout ce qui concerne
arXiv), et deux cibles bout en bout par source sont ajoutées :

```makefile
ingest: arxiv-ingest tldr-ingest    # (ex ingest-arxiv / ingest-tldr)

arxiv: arxiv-ingest index           # bout en bout arXiv seul
tldr: tldr-ingest index             # bout en bout TLDR seul
```

**Pas de `arxiv-index` / `tldr-index`** : la commande CLI `index` n'a pas de
filtre par source (`read_ingested_articles` reprend tous les
`status='ingested'`). Un filtre `--source` serait nécessaire côté CLI et
SQLite — jugé non nécessaire pour l'instant (YAGNI), décision abandonnée.
Conséquence assumée : `make tldr` indexe aussi les éventuels articles arXiv
en attente (et inversement) — sans gravité, l'indexation est idempotente.
