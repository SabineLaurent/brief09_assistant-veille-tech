# Matrice de décision — review d'un blocker & issues de scraping

> **Statut : décidé, NON implémenté (2026-06-15).** Spec figée à l'issue d'une discussion
> de design. Le code actuel ne la reflète pas encore (cf. « Écarts avec le code actuel » en
> bas). Recoupe les améliorations #12 et #13 du `docs/TODO.md`.

## Contexte

À l'indexation, un article n'est indexé que s'il a un **titre exploitable ET un contenu
suffisant**. Tout le reste est un **blocker** : la passe de review tente de le récupérer
depuis la source (scrape) avant l'index. Le problème à trancher : que faire quand le scrape
ne récupère pas le contenu — selon la **raison** de l'échec et selon ce dont on dispose déjà.

## Deux dimensions d'état

- **titre** : `valide` / `inexploitable`
  - `inexploitable` = déchet (ex. `)`) **ou** absent. Les deux ne portent aucun signal
    utile → traités identiquement (correspond à `is_usable_title()`).
- **content** : `valide` / `blurb` / `absent`
  - `valide` : `len ≥ seuil` (`MIN_CONTENT_CHARS`, 150).
  - `blurb` : `0 < len < seuil` → **court mais exploitable** (on peut embeder titre+blurb).
  - `absent` : vide → **rien à embeder** (`chunk("") == []`).
  - ⚠️ Distinction nouvelle : aujourd'hui `has_enough_content()` met `blurb` et `absent`
    dans le même sac (les deux < seuil → False). Or le `blurb` s'indexe, l'`absent` non.

## Issues de scraping

Pour tout blocker on tente le scrape. **L'issue commande la suite :**

| Issue scrape | Traitement |
|---|---|
| **succès** (titre/contenu extrait) | enrichir → indexer normalement |
| **transitoire** (429 / timeout / 5xx) | rester `ingested` → retenté au prochain run |
| **200-vide** (page atteinte, rien d'extractible — SPA/JS) | **held + logging** « scraping intelligent requis » — ni indexé, ni rejeté ; attend une future passe de scraping JS (navigateur headless) |
| **permanent** (403 / 401 / 404) | repli selon la matrice ci-dessous (le contenu ne viendra jamais) |

Le **200-vide** n'est PAS un cul-de-sac comme le 403 : la page répond, c'est notre scraper
(httpx + BeautifulSoup, sans rendu JS) qui ne sait pas extraire. Le contenu est récupérable
avec un meilleur outil → on ne se contente pas d'un repli faible qu'il faudrait remplacer,
on garde l'article en attente et on le signale.

## Repli sur échec permanent

Le contenu ne viendra jamais : on indexe au mieux avec ce qu'on a.

| | content `valide` | content `blurb` | content `absent` |
|---|---|---|---|
| **titre valide** | *(n'est pas un blocker)* | index **titre + blurb** | index **titre seul** |
| **titre inexploitable** | index **content seul**, card = **source/domaine** | index **blurb seul**, card = **source/domaine** | **reject** |

**Règles transverses :**
- Un **titre inexploitable est toujours écarté** : ni préfixé à l'embedding (ce serait du
  bruit), ni affiché. La card retombe alors sur **source/domaine**.
- Le titre `valide`, lui, reste préfixé à l'embedding (signal fort, déjà en place) et sert
  de titre de card.

## Conséquences d'implémentation (à faire, pas encore codé)

1. **`Scraper`** : remonter **3 issues distinctes** (transitoire / 200-vide / permanent) au
   lieu d'avaler toute panne en `[]`.
2. **Détection content** : distinguer `blurb` vs `absent` (au-delà du booléen
   `has_enough_content`).
3. **Gate de l'indexer** : accepter un blocker « reviewé + approuvé pour repli »
   (titre seul / blurb seul) sans ouvrir la porte à tous les contenus maigres non-reviewés.
4. **200-vide** : ressort dans le **résumé de run** (logging), reste `ingested` — donc
   re-détecté (et re-scrapé) à chaque passe. Léger surcoût accepté.
5. **Aucun changement de schéma** : pas de flag/colonne. Ce que la matrice ne sait pas
   résoudre reste en attente et ressort par logging.

## Écarts avec le code actuel (2026-06-15)

- `reviewer._scrape` renvoie `None` pour **toutes** les pannes (403 comme timeout) et un
  `dict` si la page est atteinte → ne distingue pas permanent / transitoire / 200-vide.
- `review_article` traite tout `None` comme transitoire (`skipped`, retenté indéfiniment —
  c'est le bug #13) et tout « page atteinte mais rien d'usable » comme `rejected`
  (jette des articles au bon titre — c'est #12).
- `has_enough_content` ne distingue pas `blurb` de `absent`.
- Pas de repli « index sur titre/blurb » ni de titre de card source/domaine.
