# TODO

## Développement

- Je suis novice en python.
- Je veux du code simple, kiss, yagni.
- Faire en sorte que ça marche dans un premier temps, rendre le code plus propre, maintenanble, stable et défensif dans un second temps.

- Avant d'implémenter la solution, faire une proposition avec avancée step by step en mode professeur (explication claire, précise, logique, etc).

1. Concevoir et écrire le code pour requeter l'API dont les données finiront dans la base de connaissance (app/ingest/news_api.py ; pagination, normalisation des articles, validation pydantic, upsert dans Chroma)

Où en est-on?
--> @docs/steps/1-get-arXiv-articles.md
--> @docs/steps/2-indexation-decisions.md
--> etc

2. Concevoir et écrire le scraping de 2 à 3 sources tech (app/ingest/scraper.py ; 1 blog technique, 1 changelog produit, 1 page de doc / annonce).
