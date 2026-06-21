# Repo state — 2026-06-21

Working tree **propre**. Branche `missing-contents-completion-in-article-records`
**22 commits en avance sur `main`** (jamais mergée — tout le chantier review/qualité y vit).

## Où en est le projet

Assistant RAG (Chroma + FastAPI + LLM Foundry). Le pipeline cœur (`/chat`, retrieval,
indexation, ingestion arXiv/TLDR via checkpoint SQLite) est fonctionnel. Le chantier en cours
est la **chaîne de qualité à l'indexation** autour d'un **agent de review** qui complète et
annote les articles.

```
collecte → SQLite(ingested) → [review blocking] → index(quality gate) → Chroma → [review classique → sync métadonnées]
```

## Ce qui est fait (appuyé sur les commits)

| Brique | État |
|---|---|
| Agent de review (`app/review/`) — topics + keywords + contenu manquant | ✅ |
| Vocabulaire contrôlé `available_topics` (AI/Security/Agentic/Embedded) | ✅ |
| Passe `missing` : scrape + résumé pour `content` vide (#6) | ✅ |
| Quality gate à l'index : `is_usable_title` ET `has_enough_content` (#9) | ✅ |
| Rejet déchet `status='rejected'` terminal (#10) | ✅ |
| Titre préfixé au chunk avant embedding (#7) | ✅ |
| Dédup des résultats par `reference` au retrieval (#8) | ✅ |
| `chroma_synced_at` stampé par la sync post-review | ✅ |
| Review classique câblée **après** l'index ; prompt resserré | ✅ |
| Tags/keywords retirés de l'ingestion → laissés à la review (dernier commit) | ✅ |
| Sortie LLM forcée en anglais (structured output) | ✅ |

## Ce qui reste

- 🟡 **Matrice review-blocker & scraping (#12 + #13)** — **décidée, figée dans
  `docs/notes/matrice-review-blocker-et-scraping.md`, mais NON implémentée.** Prochain gros
  morceau. Trois manques précis :
  1. `Scraper` : distinguer 3 issues (transitoire / 200-vide / permanent) au lieu d'avaler
     toute panne en `[]`.
  2. Détection `content` : séparer `blurb` (court mais indexable) de `absent`.
  3. Gate de l'indexer : accepter un blocker « reviewé + approuvé pour repli » (titre seul /
     blurb seul) sans rouvrir la porte aux contenus maigres non-reviewés.
- ⬜ **#11** Aligner les 3 vocabulaires de topics (front `python/js/…` ↔ review
  `AI/Security/…` ↔ tags arXiv `cs.*`).
- ⬜ Stubs SPECS encore ouverts : `fresh_news.fetch` (#4 : articles du jour + repli veille),
  `enrich_retrieval`, RSS → ingester (#5).

## Décisions en suspens

- **#13** : paywall/403 traité comme transitoire → retry infini (Bloomberg/NYT/WSJ). Tranché
  dans la matrice, reste à coder.
- **#12** : bons titres + contenu inscrapable (SPA JS) rejetés à tort → décidé d'indexer sur
  titre seul.

## Pour repartir

1. **Implémenter la matrice (#12/#13)** — commencer par faire remonter le statut HTTP par le
   `Scraper` (prérequis commun aux deux), puis la distinction `blurb`/`absent`, puis le repli
   dans le gate de l'indexer.
2. Sinon, **merger cette branche dans `main`** : 22 commits de travail validé e2e jamais
   intégrés.
