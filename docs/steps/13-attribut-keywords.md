# Attribut `keywords` sur la table article

> **Statut : implémenté** (2026-06-13). Couvre le point 2 des « Amélioration et
> corrections » du TODO : *ajouter un attribut `keywords` à la table article et répercuter
> dans la suite du pipeline*. Prépare le terrain pour le point 3 (agent qui générera les
> mots-clés).

## Problème de départ

Le modèle `Article` n'avait qu'un champ `tags`. Pour arXiv, ce champ mélangeait deux
choses de nature différente :

```python
tags = [category] + keywords   # ex. ["cs.AI", "AI", "agentic"]
```

- la **catégorie** arXiv (`cs.AI`) → c'est de la *provenance* : ce qui décrit d'où vient
  l'article / quelle requête l'a trouvé ;
- les **mots-clés de recherche** (`AI`, `agentic`) → ce sont les termes de la requête.

Le point 3 du TODO prévoit un agent qui générera des **mots-clés de contenu** (le sujet
réel de l'article, déduit de son texte). Il faut donc une case distincte pour les
accueillir. D'où la séparation :

| Champ | Rôle | Qui le remplit |
|---|---|---|
| `tags` | provenance / requête (catégorie arXiv, édition + section TLDR) | les ingesters, aujourd'hui |
| `keywords` | mots-clés de **contenu** (sujet réel) | l'agent (point 3), plus tard |

## Décisions retenues

- **`keywords` séparé de `tags`** (pas de fusion). `tags` = provenance, `keywords` =
  contenu.
- **Pour arXiv : déplacer les mots-clés de recherche** dans `keywords` →
  `tags=[category]`, `keywords=[mots-clés de recherche]`. Pour TLDR : pas de notion de
  mots-clés de recherche → `keywords=[]` (l'agent remplira).
- **`keywords` vide par défaut** (`[]`) côté ingestion : ce point ne génère encore aucun
  mot-clé, il pose seulement la plomberie.
- **Pas de migration SQLite** : la base de dev est recréée à neuf, on édite directement
  `article.sql` (KISS / YAGNI).
- **Encodage des listes en métadonnée Chroma : virgule `", "` partout** (voir ci-dessous).

## Sous-décision : encodage des listes pour Chroma (virgule au lieu de `|`)

Chroma **n'accepte que des scalaires** en métadonnée (`str`, `int`, `float`, `bool`) — pas
de liste. Il faut donc encoder `tags`/`keywords`/`authors` en chaîne.

En implémentant `keywords`, on a repéré une **incohérence pré-existante** : l'indexer
joignait les tags avec `|` (`"cs.AI|deep learning"`), mais `llm._split_tags` (qui relit les
tags pour les cartes) **découpe sur la virgule `,`**. Résultat : un tag multi-valeurs
revenait en **un seul** morceau, jamais redécoupé. Le `|` ne servait donc à rien.

→ **Décision : virgule `", "` partout**, ce que `_split_tags` sait déjà relire. Une seule
convention, et le bug est corrigé au passage.

Réserve assumée (YAGNI) : si un tag/keyword contenait lui-même une virgule, il serait
coupé à tort. N'arrive pas avec les sources actuelles (catégories arXiv `cs.AI`, mots-clés
courts, sections TLDR type `"Big Tech & Startups"`).

## Ce qui a été fait (6 fichiers)

1. **`app/data/article.sql`** — colonne `keywords TEXT NOT NULL DEFAULT '[]'` (JSON, comme
   `tags`/`authors`). Le `DEFAULT` permettrait à une base existante d'absorber la colonne ;
   ici surtout pour la cohérence du schéma.
2. **`app/data/migrate.py`** — *aucun changement* finalement. On avait esquissé un
   `ALTER TABLE` idempotent, retiré au nom de YAGNI (base recréée à neuf).
3. **`app/ingest/models.py`** — champ `keywords: list[str] = Field(default_factory=list)`
   sur `Article` ; ajouté à `to_chroma_metadata()` (encodage virgule). Les trois listes
   (`tags`, `keywords`, `authors`) passent de `"|".join` à `", ".join`.
4. **`app/data/article_store.py`** — `upsert_article` : `keywords` ajouté aux colonnes et
   valeurs de l'`INSERT` (`json.dumps(article.get("keywords", []))`). Stockage SQLite = JSON
   (différent de l'encodage virgule réservé à Chroma — les deux cohabitent sans souci).
5. **`app/ingest/arXiv_api.py`** — `normalize_article` : `tags=[category]`,
   `keywords=keywords` (au lieu de `tags=[category] + keywords`). Docstring mise à jour.
6. **`app/indexing/indexer.py`** — metadata Chroma : ajout de `keywords`, et bascule du
   `"|".join` vers `", ".join` (relecture JSON depuis SQLite puis ré-encodage virgule).

**Sans changement, car dynamiques** : `read_ingested_articles` (`SELECT *`), `csv_exporter`
(`articles[0].keys()`), `TldrScraper` (`keywords` prend le défaut `[]` du modèle).

## Validation

- `make lint` : **OK**.
- `make typecheck` / `make test` : les erreurs/échecs restants sont **tous pré-existants**
  et hors périmètre de ce point (stubs `news_api`/`fresh_news` en `NotImplementedError`,
  typing `chromadb`/`Settings | None` dans `fetch_articles`/`run`, etc.). Aucune nouvelle
  alerte introduite par ces 6 fichiers.

## Suite

- **Point 3 du TODO** : agent (activable manuellement) qui lit le contenu de l'article,
  détermine la/les catégorie(s) (`AI` / `Sécurité`) et génère les `keywords`, écrits en
  base sous la colonne créée ici.
- **Affichage** : `keywords` est désormais porté jusqu'à la métadonnée Chroma, donc
  disponible côté retrieval/`llm`. Le faire apparaître sur les `ArticleCard` (UI) n'a pas
  été fait ici — inutile tant que `keywords` est vide ; à brancher quand l'agent les
  remplira.
