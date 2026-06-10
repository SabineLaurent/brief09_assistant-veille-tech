# Modèle de base `Article` et héritage par source

## Contexte

`ArXivArticle` et `TldrArticle` avaient les mêmes 8 champs et les mêmes méthodes
`to_chroma_metadata()` / `to_indexable()`. Violation du principe DRY.

## Solution : classe de base dans `app/ingest/models.py`

```python
class Article(BaseModel):       # classe de base — app/ingest/models.py
    reference: str
    title: str
    source: str
    published_date: datetime | None
    content: str
    url: str
    tags: list[str]
    authors: list[str]
    ingested_at: datetime = Field(default_factory=datetime.now)
    indexed_at: datetime | None = None

    def to_chroma_metadata(self) -> dict[str, str]: ...
    def to_indexable(self) -> dict[str, Any]: ...


class ArXivArticle(Article):    # app/ingest/arXiv_api.py
    pass


class TldrArticle(Article):     # app/ingest/tldr_scraper.py
    authors: list[str] = Field(default_factory=list)
```

## Pourquoi `pass` dans `ArXivArticle` ?

`pass` est le mot-clé Python pour "corps de classe vide". Il n'y a rien à ajouter :
`ArXivArticle` hérite de tout sans modification.

Avec Pydantic, **le constructeur (`__init__`) est généré automatiquement** à partir des
champs de la classe parent. On peut donc instancier `ArXivArticle(reference=..., title=...,
...)` sans écrire de `__init__`.

Comparaison avec Java/C# : dans ces langages une classe vide sans constructeur serait
inutile. En Python + Pydantic, c'est valide et courant.

## Pourquoi garder `ArXivArticle` plutôt qu'utiliser `Article` directement ?

- **Lisibilité** : `list[ArXivArticle]` indique l'origine des articles sans ambiguïté.
- **Extensibilité** : si un champ propre à arXiv est ajouté plus tard (ex: `arxiv_category`),
  la classe existe déjà au bon endroit.

## Pourquoi `TldrArticle` surcharge `authors` ?

TLDR ne fournit pas d'auteurs individuels. `authors: list[str] = Field(default_factory=list)`
rend le champ optionnel à l'instanciation — pas besoin de passer `authors=[]` à chaque fois.

## Pourquoi Pydantic `BaseModel` et pas `@dataclass` ?

Le CLI appelle `a.model_dump()` pour persister les articles en SQLite — méthode propre à
Pydantic. Un `@dataclass` nécessiterait `dataclasses.asdict()` et des modifications en
cascade. Voir aussi le docstring de `app/ingest/models.py`.
