# Étape 6 — HNSW et indexation dans Chroma

## C'est quoi HNSW ?

**HNSW = Hierarchical Navigable Small World**

C'est l'algorithme de recherche vectorielle utilisé par Chroma (et la plupart des bases
vectorielles : Pinecone, Weaviate, Qdrant…).

### Le problème qu'il résout

Quand on cherche les articles les plus proches d'une requête, la méthode naïve serait de
comparer le vecteur de la requête à **tous** les vecteurs stockés. Avec des milliers
d'articles découpés en chunks, c'est trop lent.

### L'idée : un réseau de voisins à plusieurs niveaux

HNSW organise les vecteurs comme un réseau hiérarchique. Les niveaux hauts font des grands
sauts (recherche grossière), les niveaux bas affinent. Comme chercher une adresse :

```
Niveau 2 (grossier) :  A ──── B ──── C
Niveau 1 :             A ─ D ─ B ─ E ─ C
Niveau 0 (fin) :       A─F─D─G─B─H─E─I─C
```

On entre par le haut, on descend vers les voisins les plus proches à chaque niveau.
Résultat : la recherche est très rapide même avec des millions de vecteurs.

---

## Mesure de similarité : distance cosinus

Dans notre config Chroma :

```python
client.get_or_create_collection(
    name="articles",
    metadata={"hnsw:space": "cosine"},
)
```

La **distance cosinus** mesure l'angle entre deux vecteurs — pas leur magnitude.
Deux textes sémantiquement proches auront des vecteurs qui "pointent dans la même
direction", même si leur longueur diffère.

C'est pourquoi `embed()` utilise `normalize_embeddings=True` :

```python
embedder.encode([text], normalize_embeddings=True)
```

Normaliser les vecteurs (longueur = 1) garantit que la distance cosinus est cohérente
entre l'ingestion et le retrieval. **Même modèle, même normalisation des deux côtés —
c'est une règle à ne jamais briser.**

---

## Flux d'indexation dans notre pipeline

```
Article A
  ↓ chunk(content)
["chunk 0", "chunk 1", "chunk 2"]
  ↓ embed(chunk)  — même modèle (multilingual-e5-small), normalize=True
[vecteur 0, vecteur 1, vecteur 2]
  ↓ collection.upsert(ids, documents, embeddings, metadatas)
Chroma met à jour l'index HNSW avec les 3 chunks d'un coup

Article B
  ↓ même processus → 1 upsert pour tous ses chunks
...
```

L'index HNSW se met à jour à chaque `upsert` — **pas d'étape "indexation globale"
à la fin**. C'est incrémental, article par article.

### Pourquoi `upsert` et pas `add` ?

`add()` lève une erreur si un ID existe déjà.
`upsert()` écrase silencieusement — ce qui permet de relancer `make ingest` sans
vider Chroma au préalable. Avec des IDs déterministes (`"{article_id}::{i}"`),
la ré-ingestion est **idempotente** : pas de doublons.

---

## Batch embedding ✅

Dans la première implémentation :

```python
embeddings = [embed(c) for c in chunks]  # N appels au modèle
```

Chaque appel à `embed()` était une inférence séparée. Le modèle est conçu pour traiter
des batches — passer tous les chunks d'un article en un seul appel est bien plus rapide.

**Implémentation corrigée dans `scripts/ingest_cli.py` :**

```python
vecs = get_embedder().encode(chunks, normalize_embeddings=True)  # 1 seul appel
embeddings = [v.tolist() for v in vecs]
```

- `get_embedder()` est mis en cache (`@lru_cache`) : le modèle n'est chargé qu'une seule fois.
- `.encode(chunks, …)` traite la liste entière en une seule passe GPU/CPU.
- `.tolist()` convertit chaque vecteur numpy en `list[float]` attendu par Chroma.
