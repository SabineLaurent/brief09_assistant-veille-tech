# Rôle des métadonnées dans le RAG

> Note de cadrage (2026-06-14). Clarifie ce que les métadonnées Chroma apportent —
> et n'apportent pas — à la pertinence et à la qualité des réponses. Recoupe la
> décision « keywords/topics en métadonnée, pas vectorisés » (`docs/steps/19`) et
> `docs/notes/retrieval-k.md`.

## Le point contre-intuitif : les métadonnées ne classent rien

Le classement des chunks est décidé **uniquement** par la distance cosinus entre
l'embedding de la **requête** et l'embedding du **texte du chunk** (`documents`). Les
métadonnées **ne sont pas vectorisées** : elles ne passent jamais dans le modèle
d'embedding.

Conséquence : ajouter `keywords` ou `topics` en métadonnée **ne change pas** quels
chunks sont retrouvés, ni leur ordre. Le ranking dépend du seul contenu.

## Les trois rôles réels des métadonnées

| Rôle | Champs | Impact |
|---|---|---|
| **Filtrage** (`where`) | `topics`, `source`, `date` | Restreint l'ensemble candidat avant/pendant la recherche (filtres topics du frontend). Gain de **précision** (écarte le hors-sujet) — **seulement si câblé**. |
| **Grounding / citation** | `title`, `source`, `date`, `url` | Injectés dans le contexte LLM (`_format_context`) et les cartes (`_build_cards`). Meilleure attribution des sources, fraîcheur/crédibilité jugeables. |
| **Affichage** | `title`, `url`, `tags`, `snippet` | UI seule (cartes). Pas d'effet sur la réponse. |

## État dans ce projet

- **`topics`** : utiles comme **filtre catégoriel** (match exact via `where`), pas comme
  match sémantique. Aujourd'hui `retrieval.retrieve()` ne passe aucun filtre `where` →
  valeur **latente** tant que retrieval/frontend ne les exploitent pas.
- **`keywords`** : actuellement **inertes** dans le RAG (ni embedés, ni filtrés, ni
  injectés au contexte). Générés par l'agent de review, ils servent l'affichage et un
  filtrage futur, pas encore la qualité des réponses.
- **`title` / `source` / `date` / `url`** : impact **immédiat** sur la qualité perçue
  (citations, fraîcheur, cartes).

## Si l'objectif est la pertinence du retrieval

La métadonnée n'est pas le bon levier. Les leviers efficaces, par effort croissant :

1. **Préfixer le titre au chunk** avant embedding (gain cheap : le titre porte souvent
   le sujet).
2. **Recherche hybride** (dense + BM25 sur le texte) pour la sensibilité aux mots-clés.

## À retenir

Les métadonnées améliorent le **scoping (filtrage)** et le **grounding (citations /
cartes)**, pas la **pertinence sémantique brute**. Les poser proprement (et les patcher
sans re-vectoriser) prépare le filtrage et l'affichage ; le gain de pertinence, lui, se
joue sur le texte embedé.
