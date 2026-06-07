# Étape 2 — Décisions d'architecture pour l'indexation

## Contexte

On cherche à brancher la chaîne complète : fetch articles → nettoyer → chunker → embedder → indexer dans Chroma.
Deux questions de design se sont posées avant d'écrire le moindre code.

---

## Question 1 — Chunking classique ou sémantique ?

### Chunking classique
Découpe par taille : on coupe aux frontières de phrases dès qu'on dépasse `max_chars`.
Rapide, sans dépendance supplémentaire.

### Chunking sémantique
Découpe par sens : on embède chaque phrase, on mesure la similarité cosinus entre phrases
consécutives, et on coupe quand la similarité chute sous un seuil (ex. `0.5`) ou que
`max_chars` est dépassé. Plus pertinent pour le retrieval, mais plus lent (embed par phrase).

### Décision retenue : condition sur la taille

```
len(text) ≤ seuil  →  chunking classique  (pas d'embed, rapide)
len(text) > seuil  →  chunking sémantique (embed par phrase, coupe aux cassures de sens)
```

Seuil par défaut : `2 × max_chars` (si le texte tient en ≤ 2 chunks, le classique suffit).

**Pourquoi :** les résumés arXiv font ~150-300 mots (~1000-1500 caractères) → classique suffit.
Le sémantique devient utile quand on scrappe des articles complets (blogs, pages web longues).
La condition évite de charger le modèle d'embedding inutilement pour les textes courts.

**Note technique :** `embed()` utilise `normalize_embeddings=True`, donc les vecteurs sont
déjà de longueur 1 → similarité cosinus = produit scalaire. Pas besoin de diviser par les normes.

---

## Question 2 — SQLite est-il utile ou redondant avec Chroma ?

### Arguments pour SQLite

- Sépare le fetch (lent, limité par quota API) du chunking/embedding (local, relançable à volonté)
- Checkpoint lisible par un humain (debug, audit)
- Résilience : si l'embed plante à mi-chemin, les données brutes sont préservées

### Arguments contre SQLite

- Chroma stocke déjà le texte des documents + métadonnées + embeddings
- Avec `collection.upsert()` et des IDs déterministes (hash de l'URL), Chroma est déjà idempotent
- Deux bases = risque de désynchronisation + question : laquelle est la source de vérité ?

### Décision retenue : SQLite comme source de vérité

**Raison explicite :** SQLite sert de cache brut pour découpler la fréquence de fetch
(cron quotidien) de la fréquence de réindexation (à la demande). En cas de corruption
ou de purge de Chroma, on reconstruit l'index sans reconsommer de quota API.

**Architecture cible :**

```
Cron ──▶ fetch (API) ──▶ SQLite  (source de vérité, données brutes)
                              │
                    ingest index ──▶ chunk ──▶ embed ──▶ upsert Chroma
                                                              │
                                                     /chat ──▶ retrieval
```

- **SQLite** = source de vérité (articles bruts, horodatés, tels que reçus de l'API)
- **Chroma** = index dérivé, reconstruisible depuis SQLite à tout moment

### Formulation pour un jury

> *"SQLite sert de cache brut des articles récupérés pour découpler la fréquence de fetch
> (cron quotidien) de la fréquence de réindexation (à la demande). En cas de corruption
> ou de purge de Chroma, on reconstruit l'index sans reconsommer de quota API. SQLite est
> la source de vérité des données brutes, Chroma est un index dérivé et reconstruisible."*

---

## État d'implémentation au moment de ces décisions

| Brique | État |
|---|---|
| `arXiv_api.py` — fetch + normalisation | ✅ fait |
| `articles_recorder.py` — upsert SQLite | ✅ fait (utilisé dans `__main__` uniquement) |
| `cleaning.py` — `chunk()` | ❌ à implémenter (logique classique + sémantique) |
| `ingest_cli.py` — commandes `fetch` / `index` | ❌ à câbler |
| SQLite → Chroma (pont d'indexation) | ❌ manquant |

Le `__main__` block de `arXiv_api.py` utilise SQLite comme outil de dev.
Le CLI ne lit pas encore SQLite — c'est le prochain maillon à implémenter.
