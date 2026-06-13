# TODO

## Développement

- Je suis novice en python.
- Je veux du code simple, kiss, yagni.
- Faire en sorte que ça marche dans un premier temps, rendre le code plus propre, maintenanble, stable et défensif dans un second temps.

- Avant d'implémenter la solution, faire une proposition avec avancée step by step en mode professeur (explication claire, précise, logique, etc).

1. Concevoir et écrire le code pour requeter l'API dont les données finiront dans la base de connaissance (app/ingest/news_api.py ; pagination, normalisation des articles, validation pydantic, upsert dans Chroma)

2. Concevoir et écrire le scraping de 2 à 3 sources tech (app/ingest/scraper.py ; 1 blog technique, 1 changelog produit, 1 page de doc / annonce).

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
  
2. ✅ **Fait** (2026-06-13, cf. `docs/steps/13-attribut-keywords.md`) — ajouter un attribut keywords à la table article. Et répercuter cette modification en adaptant le code de la suite du pipeline.

3. mettre en place un agent pour déterminer la/les catégorie(s) de l'article,  générer les mot clés adaptés.
   - dans un premier temps l'agent sera activable manuellement.
   - choisir un modele adapté à la tâche.
   - les catégories disponibles pour arXiv sont les suivantes et uniquement les suivantes:
     - AI
     - Sécurité
     - Agentique
     - Embarqué
   - les mots clés seront générés par l'agent selon le contenu de l'article source, puis enregistré en db sqlite sous l'attribut keywords de la table article.
