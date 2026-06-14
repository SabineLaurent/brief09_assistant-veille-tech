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
