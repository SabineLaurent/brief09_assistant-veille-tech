# Bug : backend en crash-loop — `feedparser` absent de l'image Docker périmée

## Symptôme

Le frontend ne fonctionne plus, **les topics par défaut n'apparaissent plus**.
Côté réseau, `http://localhost:8000/health` et `/topics` ne répondent pas (connexion
refusée), alors que le conteneur backend est listé comme « Up » par `docker compose ps`.

Logs backend (`docker compose logs backend`) :

```
File "/srv/app/main.py", line 4, in <module>
    from app.chat import handle_chat
File "/srv/app/chat.py", line 8, in <module>
    from app.runtime import fresh_news
File "/srv/app/runtime/fresh_news.py", line 8, in <module>
    import feedparser
ModuleNotFoundError: No module named 'feedparser'
```

## Cause

`feedparser` a été ajouté comme dépendance (pour l'ingester RSS) dans `pyproject.toml`
et `uv.lock`, **mais l'image Docker du backend n'a pas été reconstruite depuis**.
`make up` (= `docker compose up -d`) **ne reconstruit pas** l'image : le conteneur a
continué de tourner sur l'ancienne image, sans `feedparser`.

Comme `app/runtime/fresh_news.py` fait `import feedparser` **en haut du module**, et que
la chaîne d'import `main.py → chat.py → fresh_news.py` est suivie au démarrage, l'app
FastAPI refuse de se charger → uvicorn meurt en boucle → aucun endpoint ne répond → le
frontend, qui peuple ses topics via `/topics`, se retrouve vide.

Note : en local (`uv run ...`) tout marchait (`make index`, ingester RSS), car le venv
local avait bien `feedparser`. Le bug n'apparaît que côté **Docker**.

## Solution

Reconstruire l'image backend :

```bash
docker compose up -d --build backend
```

## Prévention

Après **tout ajout / mise à jour de dépendance Python**, reconstruire l'image avant de
tester via Docker (`docker compose up -d --build`). Pas nécessaire pour un lancement
local (`uv run`), où le venv a déjà la lib. Même famille de piège que la dichotomie
local vs Docker du `CHROMA_URL`.
