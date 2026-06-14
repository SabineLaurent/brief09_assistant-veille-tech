# 16 — Ajouter une colonne à une base SQLite contenant déjà des données

> Daté du 2026-06-14. Contexte : ajout de la colonne `llm_reviewed_at` à la table
> `article` pour l'agent d'enrichissement LLM (cf. TODO pt.3 et pt.6,
> `docs/conception/agent-enrichissement.md`). La base dev contenait déjà ~555
> articles qu'on voulait **conserver** → migration plutôt que recréation.

## Décision

Jusqu'ici la règle était « base jetable → on édite `article.sql` et on recrée la
base à neuf » (YAGNI). Elle est **révisée** dès qu'il y a de la donnée accumulée à
garder : on **migre** la base existante au lieu de la recréer.

`llm_reviewed_at` : `TEXT`, `NULL` par défaut. NULL = l'agent LLM n'a pas encore
traité l'article. Sert de signal de lecture (`WHERE llm_reviewed_at IS NULL`).
**Orthogonal à `status`** (un article peut être enrichi indépendamment d'être
indexé) → surtout *ne pas* ajouter une valeur d'enum à `status`, ça casserait la
machine à états ingest→index (SoC).

## Le pattern : migration additive idempotente (à recopier au besoin)

SQLite n'a **pas** de `ADD COLUMN IF NOT EXISTS`. Pour qu'un `make migrate` rejouable
ne lève pas `duplicate column name`, on garde l'`ALTER` derrière une introspection
`PRAGMA table_info`. Forme employée le temps de la migration, dans `app/data/migrate.py` :

```python
# (nom_colonne, définition SQL) — chaque entrée = un ALTER ADD COLUMN idempotent
_ADD_COLUMNS = [
    ("llm_reviewed_at", "TEXT"),
]

def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}  # row = (cid, name, type, notnull, default, pk)

def init_db(db_path: str | None = None) -> None:
    path = db_path or get_settings().ingest_db_path
    with sqlite3.connect(path) as conn:
        conn.executescript(_SQL)            # crée la table si absente (base neuve)
        columns = _existing_columns(conn, "article")
        for name, definition in _ADD_COLUMNS:   # met à niveau une base existante
            if name not in columns:
                conn.execute(f"ALTER TABLE article ADD COLUMN {name} {definition}")
```

Deux gestes obligatoires, à faire ensemble :
1. ajouter la colonne en **dernière position** du `CREATE TABLE` dans `article.sql`
   (pour coïncider avec l'endroit où `ALTER ADD COLUMN` la met → base neuve et base
   migrée ont le même ordre de colonnes) ;
2. ajouter la ligne `(nom, "TYPE")` dans `_ADD_COLUMNS`.

Vérifié le 2026-06-14 : colonne ajoutée, **555 articles conservés**, `make migrate`
relancé sans erreur (idempotent).

## Pourquoi on a ensuite SUPPRIMÉ le code d'ALTER

Une fois la migration passée, `migrate.py` a été **reverté à sa forme minimale**
(`executescript(_SQL)` seul). C'est correct — et même plus propre — **dans notre
modèle**, à condition de comprendre lequel :

- **Notre modèle = « fichier de schéma + patchs de rattrapage ».** `article.sql` est
  la **source de vérité complète**. L'`ALTER` n'est qu'un patch *transitoire* pour
  rattraper les bases nées avant la colonne.
- À l'instant où (a) la seule base vivante est déjà migrée et (b) `article.sql`
  déclare la colonne, le patch n'a **plus aucun travail** : code mort. Le supprimer
  **résout la duplication DRY** (la colonne n'est plus déclarée qu'à un seul endroit,
  `article.sql`). Le pattern fait ~10 lignes → on le réintroduit le jour de la
  prochaine migration sur base vivante (YAGNI : pas de scaffolding vide entre-temps).

⚠️ Ceci ne serait **pas** vrai dans un modèle à **registre de versions** (Alembic /
yoyo) : là on ne supprime *jamais* une migration, c'est l'historique rejoué depuis
zéro qui construit le schéma. Notre liberté de supprimer vient justement d'avoir fait
simple.

Risque résiduel (faible en solo dev) : une base **ancienne, antérieure à la colonne
et jamais migrée** (vieux backup, autre machine, volume Docker oublié) n'aura pas la
colonne et fera planter le code. Ici : une seule base, déjà migrée → négligeable.

## Est-ce « la » façon propre en 2026 ?

- **Le mécanisme** `ALTER TABLE ADD COLUMN` + garde `PRAGMA` est le mécanisme natif
  SQLite **correct** pour une colonne nullable. Rien de mieux côté SQL pur.
- **Mais** ce n'est pas la façon standard de l'industrie pour gérer des migrations en
  général. Le standard = un **outil de migration versionnée** qui tient un *registre*
  (`schema_migrations`) des migrations déjà appliquées, et les rejoue une fois, dans
  l'ordre :

  | Outil | Pour qui |
  |---|---|
  | **Alembic** | standard, couplé à SQLAlchemy (lourd sans l'ORM) |
  | **yoyo-migrations** | léger, migrations en **SQL brut**, marche avec `sqlite3` nu |
  | dbmate / sqitch | agnostiques du langage |

- **Différence de fond** : nous = idempotence *par introspection* (« la colonne
  existe-t-elle ? »), qui marche pour **ajouter une colonne** mais **pas** pour un
  *backfill* de données, un *rename*, un transform, ni une migration « exactement une
  fois ». Un outil versionné gère tout ça via son registre.

## Signal de bascule vers un vrai outil de migration

Tant qu'on n'ajoute que des **colonnes nullables**, l'introspection suffit (KISS/YAGNI,
solo dev, SQLite). **Basculer sur `yoyo-migrations` le jour où** une migration devra
**transformer la donnée existante** (ex. recalculer une valeur sur les 555 articles,
renommer/scinder une colonne) ou garantir un **ordre / un passage unique** —
l'introspection ne suffit plus à ce moment-là.
