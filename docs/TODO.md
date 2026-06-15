# TODO

- Je suis novice en python.
- Je veux du code simple, kiss, yagni.
- Faire en sorte que ça marche dans un premier temps, rendre le code plus propre, maintenanble, stable et défensif dans un second temps.

- Avant d'implémenter la solution, faire une proposition avec avancée step by step en mode professeur (explication claire, précise, logique, etc).

## Développement

1. Concevoir et écrire le code pour requeter l'API dont les données finiront dans la base de connaissance (app/ingest/news_api.py ; pagination, normalisation des articles, validation pydantic, upsert dans Chroma)

2. Concevoir et écrire le scraping de 2 à 3 sources tech (app/ingest/scraper.py ; 1 blog technique, 1 changelog produit, 1 page de doc / annonce).
3. Écrire le nettoyage HTML→Markdown + déduplication + chunking + suppression boilerplate (app/ingest/cleaning.py)
4. Concevoir et écrire le code pour requeter l'API qui sera appelée en temps réel (online) (app/runtime/fresh_news.py)
5. Composer les requêtes vers la collection Chroma (paramètres n_results, filtres metadata) et vers l'API fresh news
6. Brancher l'ingestion + le runtime sur les hooks déjà en place dans app/chat.py : faire en sorte que enrich_retrieval(...) et fresh_news.fetch(...) ne renvoient plus NotImplementedError mais des listes utilisables par le LLM
7. documenter le flux de bout en bout (Markdown) à destination de l'équipe.

## Avancement du projet

Suivi des étapes réalisées et décisions prises dans @docs/steps/

## Amélioration et corrections

1. arXiv: nom de catégorie pour la création d'url de recherche à modifier pour l'enregistrement du tag de l'article en db sqlite.
   - dans l'arXiv ingester, ajouter les query suivantes:
     - cs.AI
     - cs.CR + mots "AI" et "agentic"
     - cs.LG
     - cs.CL + "AI", "RAG", "retrieval augmented generation", "reranking", "embeddings".
     - cs.SE + "AI", "agentic"
     - cs.MA
     - cs.AR + "AI", "on-device", "edge", "tinyML".
     - cs.SY + "AI", "inference", "edge", "embedded".
  
- [x] 2. **Done** (2026-06-13, cf. `docs/steps/13-attribut-keywords.md`) — ajouter un attribut keywords à la table article. Et répercuter cette modification en adaptant le code de la suite du pipeline.

3. mettre en place un agent pour déterminer la/les catégorie(s) de l'article,  générer les mot clés adaptés.
   - dans un premier temps l'agent sera activable manuellement.
   - choisir un modele adapté à la tâche.
   - les catégories disponibles pour arXiv sont les suivantes et uniquement les suivantes:
     - AI
     - Sécurité
     - Agentique
     - Embarqué
     TODO: A modifié et/ou compléter celon les sources futures ajoutées.
   - les mots clés seront générés par l'agent selon le contenu de l'article source, puis enregistré en db sqlite sous l'attribut keywords de la table article.

4. Dans fresh_news.api, je veux récupérer uniquement les articles du jour et s'il n'y en a pas, ceux du jours précédents.
5. Je veux transformer rss_feed en ingester d'article.
6. Articles sans contenu coincés en `ingested` à l'indexation.
   - Constat (2026-06-13) : à l'indexation, les articles dont `content` est vide
     produisent `chunk("") == []` → `index_articles` les saute (`continue`) sans
     changer leur status. Ils restent donc en `status='ingested'` et sont relus puis
     re-sautés à chaque `make index`. Cas observé : 12 articles (11 du blog Hugging
     Face, 1 TLDR) dont le flux RSS ne fournit que titre + lien, sans corps.
   - Pistes (par effort croissant) :
     - **(privilégiée)** quand `content` est vide, replier sur le titre comme texte
       indexable : l'article devient retrouvable (embedding sur le titre, card =
       titre + lien). Signal faible mais mieux que rien. À décider : repli à
       l'indexation (`text = content or title`, ne touche pas au `content` stocké)
       vs à l'ingestion (écrit `content = title` en base, salit le contenu source).
     - leur donner un status distinct (`skipped`) quand `chunks` est vide, pour qu'ils
       sortent de la file `ingested`.
     - enrichir le contenu RSS : pour les flux sans corps, scraper la page (`url`) afin
       de récupérer le texte avant indexation.
     - faire générer un résumé par l'agent du point 3, en même temps qu'il génère les
       mots-clés (même passe sur l'article) : ce résumé peuplerait `content`. Riche,
       mais coûteux et dépendant de la mise en place de l'agent.
   - **En cours (2026-06-15)** : la passe `app.review.runner missing` complète le
     `content` vide (scrape + résumé) puis `make index` les indexe. **Validation e2e
     cadrée dans `docs/notes/validation-e2e-point-6.md`** — à dérouler une fois la base
     repeuplée.
   - **Validé e2e (2026-06-15)** : déroulé complet réussi (bug reproduit à l'index #1,
     fix prouvé à l'index #2, 0 `ingested` résiduel, `/chat` `status="ok"`). Détails et
     résultats dans `docs/notes/validation-e2e-point-6.md` (section « Résultat »).

- [x] 7. **Fait (2026-06-15)** — préfixer le titre au chunk avant embedding.
   - Constat : le titre porte souvent le sujet réel de l'article ; l'inclure dans le
     texte embedé (et non plus seulement en métadonnée) améliore le rappel à moindre
     coût, surtout pour les chunks « du milieu » d'un article qui ne le mentionnent pas.
   - Réalisé dans `index_articles` : `embed_texts = [f"{title}\n\n{c}" for c in chunks]`
     pour l'embedding, mais `documents=chunks` (chunk brut) conservé → snippet non pollué
     par le titre. Même modèle, même normalisation. Test : `tests/test_indexer_title_prefix.py`.
   - ⚠️ Forward-only : ne re-vectorise pas l'existant. Les articles déjà indexés gardent
     leur embedding sans titre tant qu'ils ne sont pas ré-indexés (re-ingest / chromareset).

- [x] 8. **Fait (2026-06-15)** — déduplication des résultats par article.
   - Constat : un même article ressort plusieurs fois dans la réponse à une requête
     quand plusieurs de ses chunks vectorisés matchent (top-k ramène N chunks du même
     article) → cards en double.
   - Réalisé dans `retrieval.retrieve` : sur-échantillonnage (`n_results = k * oversample`,
     oversample=3) puis dédup par `reference` en gardant le meilleur chunk (Chroma trie
     par distance croissante → 1ʳᵉ occurrence = plus proche). Nettoie cards ET contexte LLM.
     Test : `tests/test_retrieval_dedupe.py`. Validé live (0 doublon).

9. Détecter les `content` trop courts (pas seulement vides) avant indexation.
   - Constat (2026-06-15) : la passe `runner missing` ne capte que les articles
     **strictement vides** (`read_articles_missing_content` filtre
     `content IS NULL OR trim(content)=''`). Les articles avec un `content` non vide
     mais maigre (< `MIN_CONTENT_CHARS`, 150 car. dans `reviewer.py`) passent entre les
     mailles : ils s'indexent sur un signal pauvre au lieu d'être complétés.
   - Incohérence à lever : `reviewer._resolve_text` utilise déjà le seuil
     `MIN_CONTENT_CHARS` pour décider de scraper, mais la **sélection SQL** amont ne
     l'utilise pas. Aligner la détection sur le seuil.
   - Piste : fonction de détection `content` sous seuil, exécutée **avant indexation**
     (pour que l'embedding porte sur le contenu complété, pas le contenu maigre — on ne
     peut re-générer ni chunks ni embeddings dans Chroma, seulement la métadonnée).
   - **Confirmé e2e (2026-06-15)** : sur 513 articles ingérés, **89** ont un `content`
     non vide mais < 150 car. — ils échappent à la passe `missing` et se sont indexés
     sur un signal pauvre (cf. `docs/notes/validation-e2e-point-6.md`).

10. Rejeter les articles à titre déchet (`status='rejected'`).
    - Constat (2026-06-15) : un titre sans substance (observé : un titre réduit à
      `")"`, zéro caractère alphanumérique) est inexploitable, même en repli sur le
      titre. C'est du déchet à ne pas indexer.
    - **Décision (validée 2026-06-15)** : rejeter **à l'indexation** avec un statut
      **terminal `status='rejected'`** (pas `ingested`, qui le ferait reboucler à chaque
      `make index`). Centralise la décision « est-ce indexable ? » là où elle se prend
      déjà (`error`/`indexed`/skip), et garde une trace auditable.
    - Piste : fonction pure `is_usable_title(title)` dans `cleaning.py` (testable sans
      I/O, test à la racine `tests/`) — seuil ex. ≥ 3 caractères alphanumériques après
      nettoyage. Appliquée dans `index_articles` : titre inexploitable **et** pas de
      contenu → `status='rejected'`.
    - **Confirmé e2e (2026-06-15)** : 1 cas réel dans la base — réf `6eb0fc303bd2`,
      titre `)`. C'est un vrai article CNBC/DeepSeek mal parsé par TLDR (titre + content
      perdus). La review a complété le `content` mais **pas le titre** → il s'est indexé
      avec le titre `)`. Conséquence : la règle « titre inexploitable ET pas de contenu »
      ne suffit pas si la review a entre-temps rempli le contenu ; à arbitrer (rejeter
      sur titre seul ? corriger le parsing TLDR en amont ?).
    - **Fait (2026-06-15)** : la récupération de titre (review lit `scraped["title"]`)
      résout le cas observé — réf `6eb0fc303bd2` a été indexé avec son vrai titre
      « DeepSeek slated to draw $7 billion… » au lieu de `)`. Reste l'arbitrage du
      point 12 (bon titre mais contenu inscrapable).

- [x] 9. **Fait (2026-06-15)** — détection `content < MIN_CONTENT_CHARS` avant index
  (garde `is_usable_title ET has_enough_content` dans `index_articles`, helper
  `has_enough_content` dans `cleaning.py`, sélection `is_blocker` dans `runner.py`).
  Validé e2e (cf. `docs/notes/statuts-et-cas-indexation.md`).
- [x] 10. **Fait (2026-06-15)** — rejet déchet via `status='rejected'` terminal posé par
  la passe review (`runner`), avec récupération préalable du titre depuis la source.
  Schéma `article.sql` mis à jour (CHECK autorise `'rejected'`). Validé e2e.

## Améliorations identifiées pendant le chantier review (2026-06-15)

11. Aligner les vocabulaires de topics (front / review / arXiv).
    - Constat : **3 vocabulaires disjoints** —
      `/topics` front (`python, javascript, ai-ml, devops, web`),
      `available_topics` review/tags (`AI, Security, Agentic, Embedded`),
      tags bruts arXiv (`cs.CR, cs.AI, cs.LG…`).
    - Pas de crash aujourd'hui : les topics ne servent qu'à **augmenter le texte** de la
      requête (`chat._expand_query` → `f"{question} | {topics}"`), il n'y a **pas de
      filtre `where`** dans `retrieval.retrieve`. Mais dès qu'on filtrera par topic
      (`where={"tags": ...}`), un topic front sans correspondance (ex. « DevOps ») → 0
      résultat.
    - À trancher : taxonomie unique partagée front ↔ review + mapping des catégories arXiv.

12. Bon titre + contenu inscrapable → rejeté à tort.
    - Constat : la règle « source atteinte ET pas (titre OK ET contenu OK) → `rejected` »
      jette des articles au **titre parfait** dont le contenu n'est pas récupérable (page
      JS/SPA). Exemples réels (2026-06-15) :
      « MiMo Code is now released and open-source » (`mimo.xiaomi.com`),
      « SpaceX's president is floating a Tesla merger… » (`qz.com`).
    - À reconsidérer : indexer sur le **titre seul** (signal faible mais réel, cf. point 7)
      au lieu de rejeter ? autre seuil ? garder en `ingested` ?

13. Paywall / 403 traité comme transitoire → retry infini.
    - Constat : le `Scraper` renvoie `[]` aussi bien pour un échec **transitoire**
      (timeout, 5xx) que **permanent** (403 paywall, 404). `review_article` traite `[]`
      comme transitoire → `skipped` → re-tenté **à chaque run**, sans jamais résoudre.
      Exemples (2026-06-15) : 7 articles `held` de `bloomberg.com`, `nytimes.com`,
      `wsj.com` (403 bot-block).
    - Piste : distinguer permanent (4xx hors 429) de transitoire (timeout/5xx/429) — il
      faudrait que le `Scraper` remonte le statut HTTP au lieu d'avaler en `[]` ; ou un
      compteur de tentatives → `rejected` après N passes.
