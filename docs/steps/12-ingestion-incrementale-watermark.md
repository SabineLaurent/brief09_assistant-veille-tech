# Ingestion incrémentale et watermark

> **Statut : implémenté** (2026-06-11) pour TLDR et arXiv, pagination arXiv comprise. Ce
> document garde la trace du raisonnement et des décisions de conception.

## Problème de départ

En l'état, le scraping (TLDR) et l'appel API (arXiv) récupèrent **à chaque run tous les
documents qui matchent la requête**, puis ne gardent que ceux absents de la base
(`INSERT OR IGNORE` sur la clé `reference`).

Ça marche, mais c'est coûteux : on re-télécharge et re-parse à chaque fois des documents
déjà connus, juste pour les jeter en bout de chaîne.

**Objectif visé** : arrêter de re-télécharger ce qui est déjà connu, *sans rien manquer*,
tout en rattrapant les nouveaux articles.

---

## Le vocabulaire

### Full ingest (ingestion « pleine »)

On récupère **tout le périmètre** de la requête à chaque exécution, sans tenir compte de
ce qu'on a déjà. Le dédoublonnage se fait *après coup* (ici via `INSERT OR IGNORE`).

- ✅ Simple, sans état à mémoriser.
- ❌ Gaspille de la bande passante et du temps CPU : on retravaille l'historique entier
  à chaque run. Et pour une source plafonnée (arXiv renvoie au plus `max_results`
  résultats), on peut même **manquer** ce qui dépasse le plafond.

C'est le comportement **actuel** du projet.

### Incremental ingest (ingestion « incrémentale »)

On ne récupère que ce qui est **nouveau depuis la dernière fois**. Pour ça, il faut
mémoriser « jusqu'où je suis allé » au run précédent — c'est le rôle du *watermark*.

- ✅ Chaque run ne traite que le delta (les nouveautés). Rapide, économe.
- ❌ Demande de garder une trace du point de reprise et de bien la mettre à jour.

C'est le comportement **cible**.

### Watermark (« ligne de flottaison »)

Une **valeur-repère** qui matérialise la frontière entre « déjà traité » et « pas encore
traité ». Ici, c'est une **date** : la date du document le plus récent déjà en base pour
une source donnée.

La métaphore vient des cours d'eau : la trace que l'eau laisse sur la berge indique
jusqu'où elle est montée. De la même façon, le watermark indique jusqu'où l'ingestion
est « montée » dans le temps.

### High watermark (« plus hautes eaux »)

C'est le **watermark le plus haut atteint**, c'est-à-dire la valeur **maximale** observée :
`MAX(date)`. On parle de *high watermark* pour insister sur le fait qu'on retient le
**point le plus avancé**, pas un point quelconque.

Concrètement pour nous :

```sql
SELECT MAX(published_date) FROM article WHERE source = ?
```

Au run suivant :

- tout ce qui est **strictement au-dessus** du high watermark → c'est nouveau, on récupère
  (« rattraper les nouveaux ») ;
- tout ce qui est **≤** au high watermark → déjà connu, on s'arrête / on saute
  (« ne pas re-télécharger ») ;
- comme on remonte le flux **dans l'ordre** jusqu'au watermark sans laisser de trou, on
  **ne manque rien**.

> « Ne rien manquer » et « ne pas re-télécharger » sont les deux faces d'une même
> mécanique : la même frontière sert à la fois de point d'arrêt et de garantie de
> complétude.

---

## Watermark sur quelle date ? (`published_date` vs `ingested_at`)

Le modèle `Article` porte déjà **deux** dates — à ne pas confondre :

| Date | Sens | Côté |
|---|---|---|
| `published_date` | quand **la source** a publié (date d'édition TLDR, publication arXiv) | source |
| `ingested_at` | quand **nous** l'avons récupéré en base | nous |

**Le watermark doit être `published_date`, pas `ingested_at`.** Raison : le watermark
borne la requête envoyée à la **source distante**, et ni TLDR ni arXiv ne savent quand
*nous* avons ingéré. On ne peut interroger une source que sur **ses propres dates** (date
d'édition dans l'URL TLDR, `submittedDate` côté API arXiv). « Donne-moi ce qui est publié
depuis le moment où j'ai ingéré » n'est pas une question qu'on peut leur poser.

`ingested_at` reste utile, mais pour **l'audit / le debug** (« qu'a ramené le dernier
run ? » → `WHERE ingested_at > ...`), pas pour la navigation. Inutile donc d'ajouter un
`ingested_date` : ce serait un doublon de `ingested_at`.

### Et « caler le run suivant sur le dernier run » ?

Bon réflexe, mais attention à la **version naïve** : stocker l'heure murale du run
(`now()`) et demander « tout ce qui est publié depuis ». Il y a toujours un **trou** entre
« le plus récent que j'ai attrapé » et « l'heure où je tournais » → un article publié
juste avant le run mais visible seulement après serait sauté la fois suivante.

La **version correcte** ne stocke pas `now()` mais retient le **plus haut repère vu dans
les données** = `MAX(date_source)` = le high watermark, qui **se dérive de la base**. C'est
plus simple (aucun journal de runs) et plus sûr.

---

## Décliné par source

Les deux sources ne se parcourent pas pareil, mais partagent le même watermark.

### TLDR — organisé par date d'édition

Chaque URL correspond à **une date** (`https://tldr.tech/ai/2026-06-10`).

1. watermark = `MAX(published_date)` pour `source = 'tldr.tech'` ;
2. on génère les dates de `watermark + 1 jour` → aujourd'hui ;
3. on scrape chaque date manquante (une date sans édition renvoie un 404, déjà géré
   proprement par `TldrScraper.run`).

→ Aucune date re-scrapée, aucune date sautée.

### arXiv — flux trié par date décroissante, plafonné

`fetch_articles(category, keywords, start)` demande une page de `max_results` résultats à
partir de l'offset `start`.

1. watermark = `MAX(updated_date)` pour `source = 'arXiv'` ;
2. **tri du flux sur `lastUpdatedDate descending`** (voir règle d'or ci-dessous) ;
3. on **pagine** (`start` = 0, 25, 50…) du plus récent au plus ancien ;
4. on **s'arrête dès qu'un article est `≤ watermark`** (déjà connu) — ou au plafond de
   pages (voir « Pagination » ci-dessous).

#### Règle d'or : aligner le champ de tri et le champ de watermark

> Le champ sur lequel on **trie** le flux distant doit être le champ sur lequel on
> **calcule** le watermark.

Le code initial mélangeait : tri sur `lastUpdatedDate` mais comparaison sur
`published_date`. Deux résolutions cohérentes étaient possibles :

| | **Option A — `lastUpdatedDate` (retenue)** | Option B — `submittedDate` |
|---|---|---|
| Tri du flux | `lastUpdatedDate` desc | `submittedDate` desc |
| Watermark | `MAX(updated_date)` | `MAX(published_date)` |
| Capte les **révisions** ? | ✅ oui | ❌ non |
| Champ à stocker en plus | ⚠️ `updated_date` (date `<updated>` Atom) | rien |
| `INSERT OR IGNORE` suffit ? | ✅ (en mode coexistence, voir ci-dessous) | ✅ |
| Complexité | KISS+ (un champ en plus) | KISS |

**Décision : Option A, en mode coexistence `v1`/`v2`.** On veut capter les révisions
d'articles (contenu frais pour la veille), mais **sans remplacer** l'ancienne version :
`v1` et `v2` cohabitent en base et dans l'index. Ce n'est pas DRY (contenu dupliqué entre
versions proches), mais c'est un choix assumé qui garde l'implémentation simple.

**Sans objet pour TLDR** (pas de tri/plafond : on itère les dates).

#### Conséquences de l'Option A en mode coexistence

Comme une révision est traitée comme un **nouvel** article (et non un remplacement), la
plupart des complications disparaissent :

| Sujet | Mode remplacement (écarté) | **Mode coexistence (retenu)** |
|---|---|---|
| `reference` | id de base sans version | **inchangée** (garde `v1`/`v2`, c'est ce qui les distingue) |
| `upsert_article` | vrai upsert `ON CONFLICT DO UPDATE` | **`INSERT OR IGNORE` inchangé** (`v2` = nouvelle `reference` → insérée) |
| Ré-indexation | supprimer les anciens chunks avant ré-ajout | **rien** (`v2` indexée comme un nouvel article) |

Il ne reste donc qu'**une seule addition** par rapport à l'Option B :

- **Nouveau champ `updated_date`** (date `<updated>` du flux Atom) sur le modèle `Article`
  + colonne SQLite + extraction dans `arXiv_api.py`. C'est lui qu'on compare pour le
  watermark arXiv (`MAX(updated_date)`), pour rester aligné avec le tri `lastUpdatedDate`.

Conséquence assumée : `v1` et `v2` apparaîtront tous deux dans les résultats de retrieval.
Acceptable pour de la veille ; à revisiter (passage en mode remplacement) seulement si la
redondance devient gênante.

#### Pagination — deux conditions d'arrêt

L'API arXiv renvoie les résultats par **pages** (`start` = offset, `max_results` = taille).
`run()` boucle sur les pages d'un topic et s'arrête à la **première** de ces deux
conditions :

1. **watermark atteint** — un article `≤ watermark` apparaît. Le flux étant trié par
   `lastUpdatedDate` décroissant, tout le reste est forcément déjà connu → on arrête (cas
   normal d'un run incrémental, souvent dès la page 1).
2. **plafond `arxiv_max_pages`** — borne le **run à froid**. Base vide ⇒ watermark `None`
   ⇒ la condition 1 ne se déclenche jamais ⇒ sans plafond, on paginerait sur tout
   l'historique du topic. Le plafond limite à `max_pages × max_results` articles
   (défaut 5 × 25 = 125/topic).

**Tolérance aux pannes** : chaque page est récupérée dans un `try/except`. L'API arXiv
renvoie fréquemment un timeout sur la pagination ; en cas d'échec, on **conserve les pages
déjà obtenues** et on arrête le topic, au lieu de tout perdre — cohérent avec la
philosophie « pipeline dégradable » du reste du code.

**Politesse** : une pause (`page_delay`, défaut 3 s) sépare deux requêtes paginées.

---

## Décisions retenues (KISS / YAGNI)

- **Stockage du watermark** : on le **dérive de la base existante**
  (`SELECT MAX(published_date) FROM article WHERE source = ?`). Zéro nouvel état, rien à
  synchroniser. Une table `ingest_checkpoint` dédiée serait plus extensible (id / offset
  et pas seulement une date) mais c'est un état de plus à maintenir → on ne l'ajoutera que
  si un besoin réel apparaît.
- **Le `INSERT OR IGNORE` reste** comme filet de sécurité (idempotence) : il ne sera
  presque plus sollicité, mais protège contre les chevauchements à la marge.
- **Watermark = `published_date`** (date source), jamais `ingested_at` (date à nous).
- **arXiv : Option A, coexistence `v1`/`v2`** — tri `lastUpdatedDate`, watermark
  `MAX(updated_date)`, révisions captées comme de nouveaux articles. Seule addition vs
  Option B : le champ `updated_date`. `reference` et `INSERT OR IGNORE` inchangés.
- **Pagination arXiv bornée** : boucle `start` arrêtée au watermark **ou** au plafond
  `arxiv_max_pages` (borne le run à froid) ; chaque page en `try/except` (tolérance aux
  timeouts arXiv) ; pause `page_delay` entre pages.
- **On commence par TLDR** (logique de dates plus lisible, et pas de piège de champ comme
  arXiv).

---

## Plan d'implémentation (réalisé)

1. **`app/data/article_store.py`** : ajouter une requête watermark. TLDR compare
   `published_date`, arXiv compare `updated_date` (Option A) → prévoir un paramètre de
   colonne, p. ex. `get_watermark(source, date_field) -> datetime | None` (colonne validée
   contre une liste blanche, un nom de colonne ne pouvant pas être un `?` SQL). Lecture
   pure, testable isolément.
2. **Orchestration TLDR** : une fonction qui, à partir du watermark (`published_date`),
   calcule les dates manquantes jusqu'à aujourd'hui, puis appelle `build_urls` + `run`.
   Cas base vide → date de départ par défaut à définir.
3. **Validation TLDR** : lancer deux fois et vérifier que le 2ᵉ run ne re-scrape rien.
4. **arXiv (Option A, coexistence)** : champ `updated_date` (`models.py` + colonne SQLite
   + extraction `<updated>` dans `arXiv_api.py`) ; `upsert_article` le persiste ; `run()`
   pagine (`fetch_articles(..., start)`) avec arrêt sur watermark **ou** plafond
   `arxiv_max_pages`, chaque page en `try/except`. `reference` et `INSERT OR IGNORE`
   restent inchangés.
