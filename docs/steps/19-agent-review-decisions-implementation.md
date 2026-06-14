# 19 — Agent de review : décisions d'implémentation (session du matin)

> Daté du 2026-06-14. Branche `missing-contents-completion-in-article-records`.
> Consolidation des décisions prises en discutant l'implémentation de l'agent de
> review (completion des enregistrements d'articles). Prolonge les steps 16
> (colonne `llm_reviewed_at`), 17 (structured output vs tool-calling), 18 (quoi
> indexer) et `docs/conception/agent-enrichissement.md`.

## État du code à la fin de la session

**Écrit et validé :**

- `app/data/article_store.py` — deux fonctions :
  - `read_unreviewed_articles()` : `SELECT * FROM article WHERE llm_reviewed_at IS NULL`
    (la file d'attente de l'agent).
  - `update_article_records_with_llm_reviews(reference, keywords, tags, generated_summary=None)` :
    écrit `keywords`/`tags` (JSON) + horodate `llm_reviewed_at` ; n'écrit dans
    `content` que si `generated_summary` est fourni.
- `app/config.py` — champ `available_topics: list[str]` (valeurs provisoires
  `["AI", "Sécurité", "Agentique", "Embarqué"]`).

**Conçu, pas encore écrit (chantier de l'après-midi) :**

- `app/review/reviewer.py` (cœur de l'agent).
- `app/indexing/indexer.py` : ajouter `reference` à la métadonnée Chroma (1 ligne).
- une fonction de patch métadonnée Chroma (`collection.update`).
- `app/review/runner.py` (boucle + persistance + patch Chroma).
- commande CLI `review` + cible Makefile.
- rebranchement de `/topics` sur `available_topics` (petite étape différée).

## Décisions prises (et ce qu'elles impliquent)

### 1. `available_topics` = source de vérité UNIQUE, en config

- **Décision** : une seule liste de topics, en config (`Settings.available_topics`,
  surchargeable en JSON via `.env` comme `ARXIV_TOPICS`), partagée par (1) l'agent
  de review (cible de classification) et (2) les filtres cliquables du frontend.
- **Pourquoi** : la liste est **provisoire** (taxonomie pas encore figée) → on doit
  pouvoir l'éditer sans toucher au code. Et un utilisateur s'attend à ce que les
  filtres de l'UI correspondent aux topics que l'agent pose sur les articles.
- **Implications** :
  - On **abandonne l'enum statique** envisagé au step 17 : pas d'`Enum` Python en dur.
  - `/topics` devra renvoyer des `Topic{slug, label}` dérivés d'`available_topics`
    (label = string brut, slug = slugifié), au lieu des 5 `POPULAR_TOPICS` codés en
    dur (python/javascript/ai-ml/devops/web). → **les filtres du frontend changeront.**
  - Le tag stocké sur l'article reste le string brut (`"Sécurité"`) pour que le
    filtrage retombe sur ses pieds.

### 2. NTUI par validation code (puisqu'on perd l'enum)

- **Décision** : le prompt injecte `available_topics` (« choisis uniquement parmi : … »)
  ET le code **filtre** la sortie du LLM contre l'ensemble autorisé.
- **Pourquoi** : sans enum statique, la garantie « pas de topic inventé » (NTUI) ne
  peut plus venir du JSON schema → on la replace au niveau code. La sortie d'un LLM
  est une **entrée non fiable** (Never Trust User Inputs), même contrainte par prompt.
- **Implication** : `topics = [t for t in review.topics if t in set(available_topics)]`.

### 3. Vocabulaire : « topic », pas « catégorie »

- **Décision** : on parle de **topics** côté agent, pas de catégories.
- **Pourquoi** : « topic » est le mot **réel du projet** (`schemas.Topic`, endpoint
  `/topics`, `ArXivTopic`, commentaire « tags = topics du frontend »). « Catégorie »
  était une invention.
- **Implication** : collision de nom avec `schemas.Topic` (BaseModel slug/label) — non
  bloquante puisqu'on est passé en config (pas d'enum à nommer).

### 4. `@dataclass` vs `BaseModel` — convention clarifiée

- **Décision** : `@dataclass` pour les objets de **travail/config** (scrapers,
  ingesters) ; `BaseModel` (pydantic) pour les **modèles de données** qui ont besoin de
  validation / `model_dump()` (`Article`, futur schéma de review). **Ne jamais mélanger**
  les deux dans une chaîne d'héritage.
- **Pourquoi** : pydantic valide/coerce à l'instanciation (utile pour des données
  externes), inutile pour de la config interne (NTUI ne s'applique pas à nos propres
  constantes). C'est déjà la convention du repo.

### 5. Refactor « classe parent Scraper » → ABANDONNÉ pour l'instant

- **Décision** : on **ne fait pas** le refactor (extraire la plomberie HTTP commune de
  `TldrScraper`/`Scraper`/`RssFeedIngester` dans un parent). `reviewer.py` utilise le
  `Scraper` existant **tel quel**.
- **Pourquoi** : dérive de périmètre. Le reviewer n'en a pas besoin (il lui faut juste
  le texte d'une page). La version vraiment propre (générique `BaseScraper[T]`) traîne
  `Generic[T]` + l'**invariance de `list`** (`list[TldrArticle]` n'est pas sous-type de
  `list[Article]`) + la modif d'un test d'acceptance, pour une précision de type
  **consommée nulle part** (les appelants ne font que `model_dump()`) → anti-YAGNI.
- **Implication** : idée **différée** (signal de bascule = un 3ᵉ vrai consommateur, ou
  des types précis réellement exploités). Reste notée en mémoire.

### 6. `reviewer.py` — zéro gaspillage par DEUX schémas + résumé conditionnel

- **Décision** : deux schémas pydantic de sortie. `_Review` (keywords + topics) quand
  le content est suffisant ; `_ReviewWithSummary` (+ summary) quand il est vide/court.
  On n'envoie le schéma avec résumé **que** dans le 2ᵉ cas.
- **Pourquoi** : « il n'y a pas de petites économies » — le LLM ne génère des tokens de
  résumé que quand c'est nécessaire (≈ 12 articles sur 555, pas les 543 autres).
- **Implication** : un seul appel LLM dans tous les cas (pas de 2ᵉ appel).

### 7. Keywords « niveau abstract » uniforme (option B), summary-first

- **Décision** : les keywords proviennent toujours d'un texte **niveau abstract** —
  du `content` quand il existe, du **résumé généré** quand le content était vide.
- **Pourquoi** : grain uniforme, et keywords **plus propres sur les pages bruitées**
  (extraire d'un résumé nettoyé > d'un dump web brut).
- **Implication technique critique** : en structured output, le modèle remplit les
  champs **dans l'ordre du schéma**. Pour que les keywords soient « tirés du résumé »,
  `summary` doit être le **premier champ** → `_ReviewWithSummary` devient **autonome**
  (n'hérite plus de `_Review` ; on duplique ~3 champs, prix honnête). + nudge prompt :
  « rédige d'abord le résumé, puis des keywords reflétant son sujet central ».

### 8. Repli de contenu déterministe, scrape seulement si besoin

- **Décision** : `text = content` si `len(content) >= MIN_CONTENT_CHARS` (= **200**),
  sinon page scrapée (`Scraper().run([url])`), sinon titre. Le code (pas le LLM) décide
  quand scraper.
- **Pourquoi** : on évite de re-télécharger les ~543 articles qui ont déjà un bon
  content (coût/fragilité). Repli du step 17.
- **Implication / divergence assumée** : ça **diverge du step 18** (« l'agent scrape
  toujours ») — on ne scrape que le cas vide. Conséquence cohérente avec la décision 7.

### 9. Échec d'un article → `llm_reviewed_at` reste NULL (retry)

- **Décision** : si `review_article` renvoie `None` (LLM non configuré, ou appel qui
  lève), le runner ne stampe pas `llm_reviewed_at` → l'article est **repris** à la
  passe suivante.
- **Pourquoi** : les échecs sont presque toujours **transitoires** (429, réseau) ; la
  passe à froid est « lente mais patiente » → laisser NULL = retry automatique. Aligné
  avec la stratégie rate limits (conception §5). Un échec de **scrape** ne déclenche pas
  ce `None` (repli sur titre, l'appel LLM a quand même lieu).
- **Implication** : ⚠️ risque théorique de boucle infinie sur un échec **permanent**
  (rare). Garde-fou (compteur de tentatives) à ajouter **seulement si** ça arrive
  (YAGNI). Décision vit dans le **runner**, pas dans `reviewer.py`.

### 10. Indexation et review DÉCOUPLÉES (pas de gate DB en dur)

- **Décision** : on **n'ajoute pas** de condition `WHERE ... AND llm_reviewed_at IS NOT
  NULL` à l'indexation. On ordonne les étapes dans le pipeline (`ingest → review →
  index`) sans dépendance dure (design « A »).
- **Pourquoi** : un gate en dur **casserait la dégradabilité** — agent non configuré →
  `llm_reviewed_at` jamais rempli → **rien ne s'indexe** → index vide → chat cassé. Ça
  couplerait l'indexation auto à une étape manuelle, LLM-dépendante, rate-limitée, et
  contredirait l'orthogonalité actée au step 16.
- **Implication** : les articles **avec content** s'indexent immédiatement même sans
  agent (métadonnée topics/keywords provisoirement vide).

### 11. Ré-enrichissement de l'index quand le LLM revient (patch métadonnée)

- **Décision** : quand l'agent passe **après** l'indexation, on **patche la métadonnée
  Chroma** des articles déjà indexés via `collection.update(ids, metadatas=...)` —
  **sans** re-chunker ni re-vectoriser.
- **Pourquoi** : pour un article **avec content déjà indexé**, la review ne change que
  `topics`/`keywords` (le `content` n'est pas touché → `generated_summary=None`). Le
  texte embedé est identique → re-vectoriser produirait les **mêmes vecteurs** = calcul
  jeté. `update` (métadonnée seule) est l'outil juste ; `upsert` (re-embed) serait du
  gaspillage.
- **Implications** :
  - **Prérequis** : ajouter `reference` à la métadonnée Chroma (`indexer.py`, 1 ligne)
    pour pouvoir retrouver les chunks d'un article (`collection.get(where={"reference":
    ref})`).
  - Deux cas distincts :

    | Cas article | Action de l'agent | Action Chroma |
    |---|---|---|
    | avec content, déjà indexé | remplit topics/keywords SQLite | `update` métadonnée (pas de re-embed) |
    | sans content (sauté à l'index) | remplit content + topics/keywords SQLite | rien — réindexé à neuf au prochain `make index` |

### 12. Keywords/topics : métadonnée, pas vectorisés (pour l'instant)

- **Décision** : les **topics** servent de **filtre** (métadonnée, jamais embedés) ; les
  **keywords** restent en **métadonnée** (affichage/filtrage), pas dans le vecteur.
- **Pourquoi** : un topic est une valeur **catégorielle** → filtrage exact (`where`),
  pas un match sémantique flou. Bourrer keywords + titre + contenu dans un seul vecteur
  **dilue** le signal (capacité fixe). En prod, la sensibilité aux mots-clés se gagne
  plutôt par **recherche hybride** (dense + BM25) ou en **préfixant le titre** au chunk.
- **Implication / pistes rappel futures** (YAGNI tant que le rappel suffit) : si manque
  de rappel → (1) préfixer le titre au chunk (gain cheap), puis (2) recherche hybride.
  Cohérent avec `docs/notes/retrieval-k.md`.

## Récap des constantes / valeurs fixées

- `MIN_CONTENT_CHARS = 200` (seuil « content trop court → scrape + résumé »).
- `available_topics` (provisoire) = `["AI", "Sécurité", "Agentique", "Embarqué"]`.
- `temperature = 0.1` pour l'agent (fidèle, n'invente rien).

## Ordre d'attaque proposé pour l'après-midi

1. `reviewer.py` (avec `_ReviewWithSummary` autonome summary-first, décisions 6/7/8).
2. `indexer.py` : ajouter `reference` à la métadonnée (décision 11).
3. fonction de patch métadonnée Chroma (décision 11).
4. `runner.py` : boucle + persistance + patch Chroma (décisions 9/10/11).
5. CLI `review` + Makefile.
6. (différé) rebranchement `/topics` sur `available_topics` (décision 1).
