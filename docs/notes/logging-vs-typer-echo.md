# Note — `logging` vs `typer.echo`

Deux façons d'écrire dans le terminal, **deux rôles différents**. À ne pas confondre.

## En une phrase

- `typer.echo(...)` → parler à **l'utilisateur** (le résultat de la commande).
- `log.info(...)` → tracer ce que fait **le programme** (journalisation pour le dev/l'exploitant).

## `typer.echo` — l'interface utilisateur

Version améliorée de `print()` (c'est `click.echo` en dessous). Message destiné à la
personne qui a tapé la commande.

```python
typer.echo(f"{len(articles)} articles indexés → {total_chunks} chunks.")
```

- Sort sur la sortie standard, **toujours**, tel quel.
- Pas de niveau, pas de filtrage, pas d'horodatage.

## `logging` — la journalisation

Chaque message a un **niveau** : `DEBUG < INFO < WARNING < ERROR < CRITICAL`.

```python
log.info("  → %d articles indexés", len(articles))
log.warning("Échec indexation article %s", reference)
```

### Le piège n°1 : rien ne s'affiche sans `basicConfig`

Un `log.info(...)` **n'affiche rien** tant que personne n'a configuré le logging.
C'est pour ça que chaque bloc `__main__` commence par :

```python
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
```

→ « affiche les logs de niveau INFO et plus, préfixés par leur niveau ».

### Le piège n°2 : `%s`/`%d` + argument, PAS de f-string

```python
log.info("  → %d articles indexés", len(articles))   # ✅ idiome logging
log.info(f"  → {len(articles)} articles indexés")     # ❌ à éviter
```

Avec `%d` + argument séparé, si le niveau INFO est désactivé Python ne construit même
pas la chaîne (gain de perf, et c'est la convention standard de `logging`).

## Tableau récap

| | `typer.echo` | `logging` |
|---|---|---|
| Destinataire | l'utilisateur | le dev / l'exploitant |
| Niveau de gravité | aucun | DEBUG → CRITICAL |
| Filtrable | non | oui (`level=...` masque les niveaux bas) |
| Format (date, niveau…) | non | oui (`basicConfig(format=...)`) |
| Destination | terminal | terminal, **fichier**, service distant… |
| S'affiche par défaut ? | oui | **non** — besoin d'un `basicConfig` |

## Le choix dans ce projet

- **Cœur métier** (`index_articles`, ingesters) → `logging`. Ces fonctions peuvent être
  appelées depuis la CLI, un `__main__`, un test ou un cron : c'est **l'appelant** qui
  décide où et à quel niveau les logs partent. La fonction ne force pas l'affichage.
- **CLI Typer** (`scripts/ingest_cli.py`) → `typer.echo` pour le résumé final, car là le
  destinataire est clairement l'humain qui a tapé `make index`.
- **Blocs `__main__`** (ex. `app/indexing/indexer.py`) → `logging`, pour rester homogène
  avec les `log.info` à l'intérieur des fonctions appelées.
