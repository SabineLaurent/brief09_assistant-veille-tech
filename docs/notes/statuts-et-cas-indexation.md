# Statuts d'article & cas d'indexation — vocabulaire validé

Note de référence sur le vocabulaire de dénomination mis en place et validé le
2026-06-15, pour le placement de la review avant indexation et le fallback LLM.

## Principe directeur

Dans Chroma, **l'embedding est figé** une fois écrit ; seule la **métadonnée est
patchable** (`indexer.patch_article_metadata`). On classe donc tout ce que produit la
review selon ce qu'elle touche :

| Production de la review | Touche | Doit précéder l'index ? |
|---|---|---|
| `content` complété (vide / trop court) | le **texte embedé** | ✅ oui — sinon vecteur faux définitivement |
| `keywords` / `tags` | la **métadonnée** seule | ❌ non — patchable après index |
| rejet titre déchet | l'indexabilité | ✅ oui, mais **pure fonction, sans LLM** |

→ Le LLM n'est dans le chemin critique que pour **compléter le `content` des bloquants**.

## Le LLM n'est invoqué que pour les bloquants

Un record **valide** ne passe jamais par le LLM : il s'indexe quoi que fasse l'agent.
« Indexer les valides même si le LLM est down » n'est donc pas un fallback à coder —
c'est le comportement naturel dès qu'on sépare valides et bloquants.

## Vocabulaire des statuts (colonne `status` de la table `article`)

| Statut | Sens | Terminal ? |
|---|---|---|
| `ingested` | collecté, en attente de traitement — **inclut les bloquants en attente de review/retry** | non |
| `indexed` | présent dans Chroma | oui (jusqu'à ré-ingestion) |
| `rejected` | **déchet définitif**, jamais indexable (ex. titre réduit à `)`) | ✅ oui |
| `error` | échec d'indexation (existant) | non (rejouable) |

`llm_reviewed_at IS NULL` = pas encore passé par la review (distinct du `status`).

**Règle d'or : `rejected` est réservé au déchet définitif.** Un LLM injoignable est
**transitoire** → on ne rejette PAS le bloquant (on jetterait un article récupérable),
il **reste `ingested`** et sera relu au prochain run quand l'agent répond.

## Les cas, à l'indexation

| Cas | Détection | Sort | Récupérable ? |
|---|---|---|---|
| **Record valide** | `content ≥ MIN_CONTENT_CHARS` ET titre exploitable | `indexed` — passe quoi qu'il arrive | — |
| **Bloquant complété** | review a rempli le `content` | `indexed` | — |
| **Bloquant non complété** | `content < seuil`, LLM down / scrape KO | **reste `ingested`** | ✅ retry au prochain run |
| **Titre déchet** | pure fonction `is_usable_title()` (sans LLM) | `rejected` | ❌ terminal |

Définition de **« valide pour indexation »** : `content ≥ MIN_CONTENT_CHARS`
**ET** titre exploitable.

## Garde à corriger dans l'indexer (phase 2)

Aujourd'hui le « held back » ne marche que pour le `content` **strictement vide**
(`chunk("") == []` → sauté → reste `ingested`). Un `content` **court mais non vide**
(ex. 50 car.) s'indexe quand même sur un signal pauvre. Il faut une **garde explicite**
sur le seuil `MIN_CONTENT_CHARS` pour retenir aussi les bloquants courts.

## Flux cible

```
ingest
  → review BLOQUANTS (LLM, content < seuil)     # complète ce qu'il peut ; LLM down → laisse ingested
  → index :
       titre déchet        → rejected (terminal, pur, sans LLM)
       content < seuil     → laissé ingested (bloquant, retry plus tard)
       valide              → indexed            # passe quoi qu'il arrive
  → annotation keywords/tags du reste (LLM, patch métadonnée)   # hors chemin critique
```

Fallback LLM : agent down → review skippe tout → l'index passe les **valides**, **garde**
les bloquants en `ingested`, **rejette** le déchet. Aucune perte.

## Renvois

- Validation e2e du point 6 : `docs/notes/validation-e2e-point-6.md`
- Chantiers : `docs/TODO.md` points 6 (validé), 9 (phase 2 — seuil), 10 (phase 3 — `rejected`)
