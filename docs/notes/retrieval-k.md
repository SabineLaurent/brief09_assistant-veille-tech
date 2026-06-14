# Note — le paramètre `k` du retrieval (RAG)

`k` = **nombre de chunks** que Chroma renvoie et qu'on **injecte dans le prompt** du LLM.

## Où c'est fixé (aujourd'hui : `k=8`, en dur)

- `app/rag/retrieval.py` → `def retrieve(query, k=8)` (défaut)
- `app/chat.py` → `retrieval.retrieve(query, k=8)` (l'appel passe `k=8`)
- `app/rag/retrieval.py` → `collection.query(..., n_results=k)` (c'est ce `k` qui limite)
- Les chunks sont tronqués à 600 caractères chacun dans `_format_context` (`llm.py`).

## ⚠️ `k` = chunks, PAS articles

Un article est découpé en plusieurs chunks à l'indexation. 8 chunks peuvent donc venir
de **moins de 8 articles** (plusieurs chunks d'un même article) → après dédup, on peut
avoir **moins de 8 cards**.

## Impacts, du moins au plus important

### 1. Latence de recherche (Chroma) — quasi nulle
Recherche approximative (index HNSW) : récupérer 8 ou 50 voisins coûte presque pareil à
notre échelle. Monter `k` ne ralentit (quasi) pas Chroma.

### 2. Latence + coût du LLM — l'impact n°1
Chaque chunk = du texte en plus dans le prompt envoyé au LLM. Donc `k` ↑ → tokens en
entrée ↑ → **latence ↑** (plus à lire/traiter) et **coût ↑** (facturation au token).
Relation ~**linéaire** : doubler `k` ≈ doubler le contexte injecté.

### 3. Qualité de la réponse — NON-monotone (contre-intuitif)
Plus de contexte n'est pas toujours mieux. Courbe en cloche :
- `k` trop petit → on rate des passages pertinents (rappel faible) → réponse incomplète.
- `k` trop grand → on noie le modèle. Les chunks sont triés par similarité décroissante :
  au-delà d'un rang, ils sont de moins en moins pertinents → bruit qui **dilue** le signal
  (« lost in the middle ») et **distrait** le modèle.
- → optimum **au milieu**. `k=8` est un défaut classique et raisonnable.

### 4. Nombre de cards (UX)
`k` chunks → jusqu'à `k` cards après dédup. Plus de `k` = plus de sources, mais risque de
**redondance** (même article) et de cards peu pertinentes en bas de liste.

### 5. Limite de contexte
À `k` très élevé on approche la fenêtre de contexte du modèle. Atténué ici par la
troncature à 600 car./chunk (8 chunks ≈ 4 800 car.), mais à surveiller si on monte fort.

## En pratique
- Réponses incomplètes ? → monter vers `k≈12-15` et tester.
- Réponses lentes / floues / hors-sujet ? → baisser `k`.
- **Next step qualité : reranking** — récupérer large (`k≈30`), re-trier avec un modèle de
  reranking, n'injecter que les 5-6 meilleurs. Gain de rappel **sans** noyer le LLM.
- Option maintenabilité : sortir `k` en réglage de config (`retrieval_k`) plutôt qu'en dur.
