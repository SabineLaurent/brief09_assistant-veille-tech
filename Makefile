.PHONY: up down logs install test fmt lint typecheck pipeline-e2e ingest arxiv-ingest tldr-ingest arxiv-e2e tldr-e2e review-blocking review index chat-test chromadelete chromareset

install:
	uv sync

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

test:
	uv run pytest -v

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy app

migrate:
	uv run python -m app.data.migrate

# Full chain: collect → recover blockers → index → classic review (annotate + sync
# Chroma metadata). The classic review runs last, after the index is reachable.
pipeline-e2e: ingest review-blocking index review

ingest: arxiv-ingest tldr-ingest

arxiv-ingest:
	PYTHONPATH=. uv run python scripts/ingest_cli.py fetch

tldr-ingest:
	PYTHONPATH=. uv run python scripts/ingest_cli.py tldr

# Récupère les bloquants (titre déchet et/ou contenu maigre) AVANT l'index : scrape la
# source pour relire le titre / résumer le contenu, rejette ce qui n'est pas récupérable.
# Dégrade proprement si l'agent mini n'est pas configuré (tout est skippé, retenté plus tard).
review-blocking:
	PYTHONPATH=. CHROMA_URL=http://localhost:8002 uv run python -m app.review.runner blocking

# Passe d'annotation complète (keywords/tags de tous les non-reviewés), APRÈS l'index :
# patche la métadonnée des articles déjà indexés. Hors chemin critique de fraîcheur.
review:
	PYTHONPATH=. CHROMA_URL=http://localhost:8002 uv run python -m app.review.runner

index:
	PYTHONPATH=. CHROMA_URL=http://localhost:8002 uv run python scripts/ingest_cli.py index

arxiv-e2e: arxiv-ingest review-blocking index

tldr-e2e: tldr-ingest review-blocking index

chromareset:
	CHROMA_URL=http://localhost:8002 uv run python -c "\
from app.rag.chroma_client import get_client; \
from app.config import get_settings; \
s = get_settings(); \
c = get_client(); \
c.delete_collection(s.chroma_collection); \
print('Collection', s.chroma_collection, 'supprimée — sera recréée au prochain appel.')"

chromadelete:
	make down
	rm -rf .docker-data/chroma
	make up

chat-test:
	curl -s -X POST http://localhost:8000/chat \
		-H 'Content-Type: application/json' \
		-d '{"question":"Quelles tendances reviennent cette semaine ?","topics":["Python","AI/ML"]}' \
		| python -m json.tool
