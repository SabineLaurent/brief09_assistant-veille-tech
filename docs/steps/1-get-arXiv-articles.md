État du premier point du TODO

Le TODO vise : API → base de connaissance (Chroma)

Ce qui est fait :
- arXiv API → fetch + parsing XML + normalisation ✅
- Stockage SQLite avec dédoublonnage (INSERT OR IGNORE) ✅

Ce qui manque:

- SQLite → Chroma : le pont entre le checkpoint et la base vectorielle est absent. Les articles récupérés ne sont jamais indexés dans Chroma, donc /chat ne peut pas encore les trouver.
- ingest_cli.py est toujours un stub — c'est lui qui devra orchestrer la chaîne complète.

Sur news_api.py :
Tu as raison, c'est un squelette du projet initial. SOURCES.md indique que la source retenue est GitHub Releases (+ arXiv). news_api.py n'est pas à implémenter pour ce point du TODO — c'est juste un fichier de départ qui reste en veille.

---
Ce qu'il reste à faire pour "cocher" le point 1

Le maillon manquant est : SQLite → clean → chunk → embed → Chroma

Concrètement :
1. Lire les articles depuis SQLite
2. Nettoyer/chunker le contenu (cleaning.py, encore en stub)
3. Générer les embeddings avec embed() (déjà prêt dans retrieval.py)
4. Faire un upsert dans la collection Chroma (via get_collection())
5. Câbler tout ça dans ingest_cli.py

---

Ce que Pydantic apporte concrètement ici :

- Si arXiv renvoie un title vide ou un url absent, Pydantic lève une ValidationError immédiatement dans normalize_article(), plutôt que de laisser passer une donnée corrompue jusqu'à SQLite ou Chroma.
- Le type de published_date est garanti datetime | None — plus de chaîne mal formée qui passerait silencieusement.

---

Voici le problème concret : ChromaDB n'accepte dans ses metadatas que des valeurs de type str, int, float ou bool. Pas de list, pas de datetime, pas de None.

Donc avec le modèle actuel, ces 3 champs posent problème pour Chroma :

┌────────────────────┬─────────────────┬──────────────────────────────┐
│ Champ ArXivArticle │   Type actuel   │       Problème Chroma        │
├────────────────────┼─────────────────┼──────────────────────────────┤
│ published_date     │ datetime | None │ ni datetime ni None acceptés │
├────────────────────┼─────────────────┼──────────────────────────────┤
│ tags               │ list[str]       │ list refusé                  │
├────────────────────┼─────────────────┼──────────────────────────────┤
│ authors            │ list[str]       │ list refusé                  │
└────────────────────┴─────────────────┴──────────────────────────────┘

Option A — Un seul modèle calé sur Chroma : on perd les types riches Python. Par exemple, article.published_date.year dans run() ne fonctionnerait plus, il faudrait parser une string à la place.

Option B — Deux modèles séparés (SOC) : ArXivArticle garde ses types Python utiles, et un modèle dédié ChromaMetadata représente exactement ce qui part dans Chroma — avec une méthode de conversion entre les deux.

# Sur ArXivArticle, une méthode de conversion
def to_chroma_metadata(self) -> dict[str, str | int | float | bool]:
    return {
        "title": self.title,
        "source": self.source,
        "date": self.published_date.isoformat() if self.published_date else "",
        "url": self.url,
        "tags": "|".join(self.tags),      # list → string
        "authors": "|".join(self.authors), # list → string
    }

Je recommande l'option B : ArXivArticle reste utile pour le filtrage Python (ex. .year), et to_chroma_metadata() isole proprement la sérialisation pour Chroma. On en aura besoin au moment de câbler le CLI → Chroma.

---
