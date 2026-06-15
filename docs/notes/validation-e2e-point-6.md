# Validation e2e — point 6 (articles sans contenu)

Process de validation cadré pour le point 6 du TODO : prouver que les articles
sans contenu, jadis coincés en `status='ingested'` à l'indexation, deviennent
indexables après la passe de review.

## Pré-requis

- Bases vides ou non : le process fonctionne sur une base existante comme sur une
  table rase (`make chromadelete` + suppression de `ingest.db` pour repartir de zéro).
- Agent mini configuré dans `.env` (`AZURE_AI_MINI_AGENT_ENDPOINT`,
  `AZURE_AI_MINI_AGENT_API_KEY`, `AZURE_AI_MINI_AGENT_MODEL`) avec des valeurs
  **valides** — sinon la passe review « skippe » tout (dégradation propre, mais pas
  de démonstration).
- Depuis le terminal hôte, forcer `CHROMA_URL=http://localhost:8002` (le `.env` vise
  `chromadb:8000`, le nom de service Docker, injoignable hors conteneur).

## Étapes

| # | Étape | Commande | Ce qu'on observe |
|---|-------|----------|------------------|
| 0 | Démarrer Chroma (+ backend) | `make up` | services healthy |
| 1 | Collecte → SQLite | `make ingest` | articles `status='ingested'`, dont certains à `content` vide (HuggingFace RSS, TLDR) |
| 2 | Index #1 (montre le bug) | `make index` | les articles vides sont **sautés** → restent `ingested` |
| 3 | Passe review « blocking » | `CHROMA_URL=http://localhost:8002 uv run python -m app.review.runner blocking` | scrape + récup titre/résumé → `content`/`title` remplis, `llm_reviewed_at` posé ; non récupérables → `rejected` |
| 4 | Index #2 (montre le fix) | `make index` | les ex-vides s'indexent enfin → `status='indexed'` |
| 5 | Vérif | requêtes SQLite + `make chat-test` | plus aucun `ingested` résiduel, cards OK |

> **MàJ 2026-06-15 (étape 3 du chantier)** : la commande a été renommée `missing` →
> `blocking` et la passe couvre maintenant **tous les bloquants** (titre déchet **et/ou**
> `content < MIN_CONTENT_CHARS`, pas seulement le `content` strictement vide). Elle
> récupère le titre réel par scrape, résume le contenu, et **rejette** (`status='rejected'`)
> ce qui n'est pas récupérable (Plan D). Voir `docs/notes/statuts-et-cas-indexation.md`.
> La passe « blocking » est désormais à lancer **avant** `make index` (étape 2 → 3 → 4).

## Requêtes de vérification (étapes 2, 4, 5)

```bash
# répartition des statuts
sqlite3 ingest.db "SELECT status, count(*) FROM article GROUP BY status;"

# articles à content vide encore en file ingested (doit décroître entre #1 et #2)
sqlite3 ingest.db "SELECT count(*) FROM article \
  WHERE (content IS NULL OR trim(content)='') AND status='ingested';"

# articles complétés par la review (content écrit par l'agent)
sqlite3 ingest.db "SELECT count(*) FROM article WHERE llm_reviewed_at IS NOT NULL;"
```

## Critère de réussite ("done")

- Après l'étape 4, aucun article à `content` vide ne reste en `status='ingested'`
  (soit `indexed`, soit `rejected` une fois la phase 3 en place).
- `make chat-test` renvoie `status="ok"` avec des cards correctement formées.

## Portée — ce que ce process ne couvre PAS encore

- **Phase 2** : la passe « missing » ne capte que les articles **strictement vides**
  (`content IS NULL OR trim=''`). Les `content` courts mais non vides
  (`< MIN_CONTENT_CHARS`, 150 car.) ne sont pas détectés ici → détection sur le seuil
  à traiter *avant* indexation.
- **Phase 3** : titres déchet (ex. un titre réduit à `")"`, sans substance
  alphanumérique) → à rejeter (`status='rejected'`, statut terminal) plutôt que laisser
  boucler en `ingested`.

## Résultat — passe du 2026-06-15 (✅ validé)

Déroulé sur base repeuplée à neuf (513 articles ingérés : 1 `content` strictement vide,
89 `content` < 150 car.).

| Étape | Résultat observé |
|-------|------------------|
| 0 `make up` | services healthy, Chroma vide au départ |
| 1 `make ingest` | 513 articles `status='ingested'` |
| 2 `make index` #1 | 512 indexés, **1 sauté (content vide) → reste `ingested`** (bug reproduit) |
| 3 review `missing` | 1 complété (scrape CNBC + résumé : content 0→286 car., keywords+tags posés, `llm_reviewed_at` daté), 0 skipped |
| 4 `make index` #2 | 1 indexé → **513 `indexed`, 0 `ingested` résiduel** (fix prouvé) |
| 5 `/chat` | `status="ok"`, 8 cards bien formées |

Observations relevées pour les chantiers suivants :

- **Phase 2** confirmée : 89 articles à `content` court (non vide, < 150 car.) échappent
  à la passe `missing` et s'indexent sur un signal pauvre.
- **Phase 3** confirmée : l'article au titre déchet `)` (réf `6eb0fc303bd2`) est **le même**
  que le `content` vide — un vrai article CNBC/DeepSeek mal parsé par TLDR. La review a
  complété son contenu, donc il s'est indexé **avec son titre `)`** : une card titrée `)`
  est passée en base. Note : la review ne corrige pas le titre → phase 3 nécessaire même
  après complétion du contenu.
- **Point 8** (dédup) reconfirmé : la card « The Range Shrinks… » est ressortie **2 fois**
  (deux chunks du même article matchent).

## Résultat — run câblé du 2026-06-15 (✅ flux complet)

Déroulé après câblage de `review-blocking` dans le flux (étapes 9/10/4 du chantier), base
repeuplée à neuf (541 articles ; 98 bloquants : 1 titre déchet + contenus maigres).

| Étape | Résultat |
|-------|----------|
| `make ingest` | 541 `ingested`, 98 bloquants détectés (`is_blocker`) |
| `make review-blocking` | titre `)` CNBC **récupéré** (→ « DeepSeek slated to draw $7 billion… ») ; ~récupérés + **2 rejetés** + skippés transitoires |
| `make index` | **532 indexés, 7 bloqués (held), 2 rejetés**, 627 chunks |
| `/chat` | `status="ok"`, 8 cards, **0 card à titre déchet** |

Temps review : ~1,3 s/article (scrape + LLM ; mesuré sur un échantillon de 18).

Bug corrigé en cours de route : le schéma `article.sql` avait un CHECK
`status IN ('ingested','indexed','error')` qui refusait `'rejected'` → ajouté (et rebuild
de table sur la base existante pour préserver les données).

Trois améliorations identifiées (alignement des topics, bon-titre/contenu-inscrapable,
paywall→retry-infini) consignées dans `docs/TODO.md` (points 11-13).
