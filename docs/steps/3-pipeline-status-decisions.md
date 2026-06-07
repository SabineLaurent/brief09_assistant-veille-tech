# Étape 3 — Décisions sur le suivi de statut et les sources de vérité

## Contexte

Avant de câbler SQLite → Chroma, trois questions de design ont été discutées :
sources de vérité, rôle du CSV, et suivi de progression via un champ `status`.

---

## Question 1 — Le CSV comme source de vérité ?

### Proposition initiale
Les fichiers CSV produits en fin d'ingest seraient la source de vérité,
SQLite étant un cache brut de la phase fetch.

### Discussion

Les CSV sont append-only : on ne peut pas mettre à jour une ligne existante.
Impossible d'y stocker un statut de progression. Pour reprendre une pipeline
interrompue, il faut quelque chose de requêtable et de mutable → SQLite.

Le CSV reste utile comme **journal d'audit** : humainement lisible, archivable,
montrable à un jury, versionnable dans git si on le souhaite.

Si SQLite venait à être corrompu, le CSV permettrait de réimporter les données
brutes sans reconsommer de quota API (une fonction `import_from_csv` serait
alors nécessaire). Décision : **prévoir ce cas en YAGNI** — on implémentera
`import_from_csv` si le besoin se présente concrètement.

### Décision retenue

- **CSV** = archive / journal d'audit (production en fin d'ingest, inchangée)
- **SQLite** = source opérationnelle de la pipeline (avec statut, voir ci-dessous)

---

## Question 2 — SQLite comme cache brut ? (confirmation)

Décision déjà prise à l'étape 2, confirmée ici :

- **SQLite** = source de vérité des données brutes, horodatées, telles que reçues de l'API
- **Chroma** = index dérivé, reconstruisible depuis SQLite à tout moment
- Intérêt : découple la fréquence de fetch (quota API) de la fréquence de réindexation (à la demande)

---

## Question 3 — Suivi de progression via un champ `status`

### Proposition

Ajouter une colonne `status` sur la table `article` de SQLite.
Elle suit la progression de chaque article dans la chaîne d'indexation.

### Granularité des états

Chunking et embedding se passent **en mémoire en millisecondes** :
si ça plante là, ça replante de la même façon au retry — pas besoin de
les tracer individuellement.

Le seul point de défaillance réel est l'**upsert Chroma** (réseau, service
pas démarré, collection inaccessible, etc.).

### Décision retenue : 3 états

```
ingested  →  indexed
             error
```

| Status     | Signification |
|------------|---------------|
| `ingested` | Article sauvegardé dans SQLite (sorti de l'API). État initial. |
| `indexed`  | Article chunké, embeddé, upsert Chroma réussi. |
| `error`    | Échec lors de l'indexation (Chroma inaccessible, embedding raté…). |

### Comportement de la commande `index`

```sql
SELECT * FROM article WHERE status = 'ingested'
```

- Traite uniquement les articles non encore indexés
- Relancer `make ingest` (commande `index`) est **idempotent** : reprend là où ça s'est arrêté
- En cas d'échec Chroma : passe à `error` → l'article sera retenté au prochain run
  (alternative : laisser en `ingested` pour retry automatique — à décider à l'implémentation)

### Impact sur le schéma SQLite

Migration nécessaire : ajouter la colonne `status` à la table existante.

```sql
ALTER TABLE article ADD COLUMN status TEXT NOT NULL DEFAULT 'ingested';
```

Les articles déjà en base héritent automatiquement du statut `ingested`
(valeur par défaut) — pas de perte de données.

---

## Résumé des décisions

| Décision | Retenu |
|---|---|
| Source de vérité opérationnelle | SQLite + `status` |
| CSV | Archive / audit uniquement |
| Résilience contre corruption SQLite | YAGNI (`import_from_csv` à prévoir si besoin) |
| Nombre d'états `status` | 3 : `ingested`, `indexed`, `error` |
| Idempotence de la commande `index` | Oui (filtre sur `status = 'ingested'`) |
| Migration SQLite | `ALTER TABLE article ADD COLUMN status TEXT NOT NULL DEFAULT 'ingested'` |

---

## État d'implémentation au moment de ces décisions

| Brique | État |
|---|---|
| `arXiv_api.py` — fetch + SQLite | ✅ fait |
| `articles_recorder.py` — upsert + count | ✅ fait |
| Migration `status` dans SQLite | ❌ à faire (`app/data/migrate.py`) |
| `articles_recorder.py` — `read_all_articles()` | ❌ à ajouter |
| `cleaning.py` — `chunk`, `dedupe` | ❌ stubs |
| `ingest_cli.py` — commande `index` | ❌ stub |
| SQLite → Chroma (pont d'indexation) | ❌ manquant |
