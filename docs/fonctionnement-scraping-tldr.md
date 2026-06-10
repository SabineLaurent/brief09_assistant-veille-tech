# Fonctionnement du scraping de TLDR.tech - 2026-06-10


Parcours complet du flux : du scraping du site TLDR jusqu'à l'indexation dans Chroma.

Le point clé : **le pipeline se fait en deux temps découplés**, avec SQLite comme zone
tampon entre les deux.

```
   TEMPS 1 : INGESTION (make tldr-ingest)
   tldr.tech ──scraping──▶ TldrArticle (Pydantic) ──▶ SQLite (status='ingested')

   TEMPS 2 : INDEXATION (make index)
   SQLite (status='ingested') ──chunk + embedding──▶ Chroma ──▶ status='indexed'
```

---

## Temps 1 — Le scraping : du site TLDR vers SQLite

### 1. Le point d'entrée : la commande CLI

`make tldr-ingest` lance `scripts/ingest_cli.py tldr` (`Makefile:38-39`). La commande
`tldr` (`ingest_cli.py:36-47`) fait quatre choses :

1. Elle reçoit les options `--edition` (par défaut `["tech", "webdev", "ai"]`) et
   `--date` (par défaut aujourd'hui).
2. Elle construit les URLs via `build_urls()`.
3. Elle lance le scraping via `scraper.run(urls)`.
4. Elle sauvegarde chaque article en SQLite via `upsert_article(a.model_dump())`.

### 2. La construction des URLs

`build_urls` (`tldr_scraper.py:31-32`) est trivial mais malin : TLDR.tech a des URLs
prévisibles de la forme `https://tldr.tech/{edition}/{date}`. Par exemple
`https://tldr.tech/ai/2026-06-10`. Pas besoin de naviguer sur le site, on fabrique
directement les adresses des 3 newsletters du jour.

### 3. Le téléchargement HTTP : `run()`

`run()` (`tldr_scraper.py:34-50`) ouvre **un seul client httpx** (réutilisé pour toutes
les URLs, plus efficace que d'ouvrir une connexion par requête), avec un `User-Agent`
identifiable, un timeout de 10 s et le suivi des redirections.

Point d'architecture important : **chaque URL est traitée dans son propre `try/except`**.
Si la newsletter "webdev" du jour n'existe pas (404), on logue un warning et on continue
avec "ai". Un échec n'invalide jamais le run entier — c'est la philosophie "pipeline
dégradable" qu'on retrouve partout dans le projet.

Avant de parser, on ré-extrait `date` et `edition` **depuis l'URL elle-même** (regex
`\d{4}-\d{2}-\d{2}` pour la date, avant-dernier segment du chemin pour l'édition) — ces
deux infos serviront de métadonnées.

### 4. Le parsing HTML : `_parse_newsletter()`

C'est le cœur du scraper (`tldr_scraper.py:63-114`). Une newsletter TLDR a une structure
HTML régulière dont on tire parti :

```html
<section>                          <!-- une catégorie ("Big Tech & Startups"…) -->
  <header><h3>Catégorie</h3></header>
  <article>                        <!-- un article de la newsletter -->
    <a class="font-bold" href="https://source-originale...">
      <h3>Titre de l'article (3 minute read)</h3>
    </a>
    <div class="newsletter-html">Résumé en HTML…</div>
  </article>
  <article>…</article>
</section>
```

Le code parcourt deux boucles imbriquées : pour chaque `<section>`, extraire la catégorie
depuis le `<header><h3>`, puis pour chaque `<article>` :

- **Titre** : le `<h3>` à l'intérieur du lien `a.font-bold`. Si le titre contient
  `(Sponsor)`, on **saute l'article** — pas de pub dans la base de connaissance.
- **URL** : le `href` du lien — c'est l'URL de l'**article original** (pas celle de TLDR).
- **Contenu** : le `<div class="newsletter-html">` (le résumé rédigé par TLDR), converti
  en Markdown via `clean_html_to_markdown()` (qui utilise `markdownify` — les balises
  disparaissent, la structure du texte reste).
- **`reference`** : un hash SHA-1 de l'URL, **après avoir retiré le tracking
  `?utm_source=...`**. C'est crucial : ce hash est l'identifiant déterministe de
  l'article. Un même article vu deux fois (deux scrapes, deux éditions) produit le même
  hash → la déduplication devient automatique en aval.
- **Tags** : `[edition, catégorie]`, par exemple `["ai", "Big Tech & Startups"]`.

### 5. Le modèle Pydantic `Article`

Chaque article devient un `TldrArticle`, qui hérite du modèle commun `Article`
(`app/ingest/models.py`). Pydantic **valide les types à l'instanciation** (par exemple
`published_date` doit être un `datetime` ou `None`), et `ingested_at` est rempli
automatiquement via `default_factory=datetime.now`. Toutes les sources (arXiv, TLDR)
produisent donc exactement la même forme — c'est ce qui permet au reste du pipeline
d'être agnostique de la source.

### 6. La persistance SQLite : `upsert_article()`

De retour dans le CLI, chaque article est passé à `upsert_article()`
(`article_store.py:10-32`). Deux mécanismes à comprendre :

- **`INSERT OR IGNORE`** : la colonne `reference` est déclarée `UNIQUE` dans le schéma
  (`article.sql:3`). Si l'article existe déjà (même hash), SQLite ignore silencieusement
  l'insertion et `rowcount` vaut 0. Relancer `make tldr-ingest` deux fois ne crée donc
  **aucun doublon** — le pipeline est *idempotent*.
- **`status` par défaut `'ingested'`** (`article.sql:11`) : on n'insère pas le statut
  explicitement, le schéma le pose. C'est ce statut qui sert de "file d'attente" pour
  le temps 2.

Les listes (`tags`, `authors`) sont sérialisées en JSON (`json.dumps`) car SQLite ne
connaît pas les listes.

---

## Temps 2 — L'indexation : de SQLite vers Chroma

### 7. La sélection : `read_ingested_articles()`

`make index` lance la commande `index` du CLI, qui appelle `read_ingested_articles()` :
un simple `SELECT * FROM article WHERE status = 'ingested'`. Seuls les articles **pas
encore indexés** sont repris — le statut fait office de curseur. Rien à indexer →
message et sortie propre.

### 8. Le cœur : `index_articles()` (`app/indexing/indexer.py`)

La fonction boucle **article par article**, chacun dans son `try/except` (même
philosophie de tolérance aux pannes). Pour chaque article :

1. **Chunking** : `chunk(content)` découpe le texte en morceaux de ≤ 1200 caractères
   avec un chevauchement de 100 (via `RecursiveCharacterTextSplitter` de LangChain).
   Pourquoi ? Les embeddings fonctionnent mal sur des textes trop longs, et le
   chevauchement évite de couper une idée pile à la frontière entre deux chunks. (Pour
   des résumés TLDR courts, ça donne souvent 1 seul chunk.)

2. **Métadonnées** : on reconstruit le dict `{title, source, date, url, tags}` attendu
   par le retrieval et `_build_cards` côté LLM. Les `tags` JSON de SQLite sont
   re-désérialisés puis joints en chaîne `"ai|Big Tech & Startups"` (Chroma n'accepte
   que des scalaires en métadonnées).

3. **IDs déterministes** : `f"{reference}::{i}"` — un id **par chunk**, pas par article
   (exigence Chroma : ids uniques). Comme `reference` est déjà déterministe (hash
   d'URL), ré-indexer le même article régénère les mêmes ids.

4. **Embeddings** : `get_embedder().encode(chunks, normalize_embeddings=True)` — le
   **même modèle** (`intfloat/multilingual-e5-small`) et la **même normalisation** que
   côté retrieval. Indispensable : la collection Chroma est configurée en distance
   cosinus, et comparer des vecteurs encodés différemment donnerait des résultats
   incohérents.

5. **`collection.upsert(...)`** (et pas `add`) : combiné aux ids déterministes, un
   ré-import **écrase** les chunks existants au lieu de les dupliquer. Deuxième étage
   d'idempotence.

6. **Mise à jour du statut** : succès → `update_article_status(reference, "indexed")`
   (qui pose aussi `indexed_at = CURRENT_TIMESTAMP`) ; échec → statut `"error"`.
   L'article sort de la "file d'attente" dans les deux cas, et la base SQLite garde la
   trace de ce qui s'est mal passé.

---

## La boucle est bouclée

Une fois dans Chroma, ces chunks sont exactement ce que `retrieval.retrieve()` interroge
à chaque `/chat` : la question de l'utilisateur est encodée avec le même modèle e5-small,
Chroma renvoie les 8 chunks les plus proches en cosinus, et `llm._build_cards`
reconstruit les cartes d'articles à partir des métadonnées posées à l'étape 8.2 (c'est
`_split_tags` côté retrieval qui re-découpe la chaîne de tags).

## Trois idées de conception à retenir

1. **Idempotence à chaque étage** : hash SHA-1 → `INSERT OR IGNORE` → `upsert` Chroma.
   On peut relancer `make pipeline-e2e` autant de fois qu'on veut sans polluer la base.
2. **SQLite comme tampon avec machine à états** (`ingested` → `indexed`/`error`) : le
   scraping et l'indexation sont découplés, rejouables indépendamment, et auditables
   (timestamps `ingested_at`/`indexed_at`).
3. **Tolérance aux pannes locale** : un `try/except` par URL au scraping, par article à
   l'indexation — jamais de "tout ou rien".

## Remarques en passant (rien de bloquant)

- L'URL stockée en base garde son `?utm_source=...` (seul le hash utilise l'URL
  nettoyée) : deux liens identiques avec des trackings différents seraient dédupliqués
  par `reference`, mais l'URL affichée contiendrait le tracking.
- Les imports `from pydantic import Field` et `Any` dans `tldr_scraper.py` semblent
  inutilisés — un `make lint` le confirmera.

---
