.PHONY: up down logs install test fmt lint typecheck pipeline-e2e ingest arxiv-ingest tldr-ingest arxiv tldr index chat-test chromadelete chromareset

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

pipeline-e2e: ingest index

ingest: arxiv-ingest tldr-ingest

arxiv-ingest:
	PYTHONPATH=. uv run python scripts/ingest_cli.py fetch

tldr-ingest:
	PYTHONPATH=. uv run python scripts/ingest_cli.py tldr

index:
	PYTHONPATH=. CHROMA_URL=http://localhost:8002 uv run python scripts/ingest_cli.py index

arxiv: arxiv-ingest index

tldr: tldr-ingest index

chromareset:
	uv run python -c "\
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
