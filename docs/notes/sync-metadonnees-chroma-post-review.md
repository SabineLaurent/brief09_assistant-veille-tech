# Synchronisation des métadonnées Chroma après review

## Le problème

L'agent de review recalcule les `tags` (vocabulaire contrôlé `available_topics` :
`AI, Security, Agentic, Embedded`) et les `keywords` d'un article. Mais un article peut
**déjà être indexé** dans Chroma au moment où la review le traite. Ses chunks portent alors
une métadonnée périmée (ex. le tag brut arXiv `cs.AI`), désynchronisée du SQLite reviewé.

Il faut donc rafraîchir la métadonnée Chroma **sans re-vectoriser** : seuls `tags`/`keywords`
changent, le texte embedé (titre + contenu) est inchangé — re-embedder produirait exactement
les mêmes vecteurs pour rien.

## Deux passes de review, séparées par l'indexation

La ligne de partage n'est pas « urgent / pas urgent » mais **« est-ce que ça touche
l'embedding ? »** :

| Passe | Ce qu'elle modifie | Embedding impacté ? | Quand | Cible Make |
|---|---|---|---|---|
| **blocking** | récupère le **titre** (scrape) + complète un **contenu** maigre | oui (titre préfixé au vecteur, contenu = chunks) | **avant** `index` | `make review-blocking` |
| **classique** | seulement `tags` + `keywords` | non (métadonnée pure) | **après** `index` | `make review` |

`pipeline-e2e` enchaîne `ingest → review-blocking → index`. La passe classique se lance
ensuite, calmement, hors chemin critique de fraîcheur : Chroma est déjà joignable et peuplé,
on raffine la métadonnée par-dessus.

## Le sync : `patch_article_metadata` (indexer.py)

C'est l'outil de mise à jour métadonnée-seule. Il s'appuie sur deux méthodes Chroma prêtes :

1. `collection.get(where={"reference": ref})` → récupère les **ids** de tous les chunks de
   l'article (Chroma ne sait pas mettre à jour « par filtre », il lui faut les ids).
2. `collection.update(ids=..., metadatas=...)` **sans** passer `embeddings` → Chroma conserve
   les vecteurs existants. Pas de ré-embedding.

### Pourquoi on réécrit la métadonnée *entière*

Le comportement de `collection.update` sur la métadonnée — **fusion** (ne change que les
clés fournies) vs **remplacement** (écrase tout le dict) — **n'est pas garanti d'une version
de Chroma à l'autre** (projet en 0.5). Pour ne dépendre d'aucune hypothèse,
`patch_article_metadata` **relit la métadonnée existante** (`found["metadatas"]`), en fait une
copie, n'y remplace que `tags`/`keywords`, et renvoie le **dict complet**. Ainsi `title`,
`source`, `date`, `url`, `reference` sont préservés quel que soit le comportement de `update`.

## Traçabilité : `chroma_synced_at`

La colonne `chroma_synced_at` (table `article`) est un **horodatage d'audit** : elle marque
que la métadonnée reviewée a bien été poussée dans Chroma. Elle est posée par
`mark_chroma_synced(reference)` (article_store.py), appelée depuis `_review_and_persist`
**seulement** quand `patch_article_metadata` a effectivement patché des chunks (retour > 0).

Elle est **orthogonale à `llm_reviewed_at`** :

- `llm_reviewed_at` = l'agent a annoté l'article (SQLite à jour).
- `chroma_synced_at` = la métadonnée Chroma reflète cette annotation.

Les deux peuvent diverger : un article reviewé dont Chroma était injoignable pendant la passe
garde `llm_reviewed_at` rempli mais `chroma_synced_at` NULL. Le chaînage (review → patch)
décide *quoi* synchroniser ; la colonne ne fait qu'enregistrer *que* ça a eu lieu — elle ne
pilote aucune sélection.

### Cas non couvert (volontaire)

Un blocker récupéré par la passe blocking reste `ingested` et sera **indexé à neuf** au
prochain `index`, avec ses `tags`/`keywords` déjà reviewés en SQLite : sa métadonnée Chroma
naît correcte, sans passer par `patch_article_metadata`. Son `chroma_synced_at` reste donc
NULL alors que Chroma est à jour. C'est cohérent avec la sémantique de la colonne, qui ne
trace que le chemin *patch post-index*, pas l'indexation initiale.
