.PHONY: install \
	up down logs \
	test fmt lint typecheck \
	migrate \
	ingest arxiv-ingest tldr-ingest rss-ingest index review-blocking review \
	pipeline-e2e arxiv-e2e tldr-e2e \
	fresh-news \
	chromareset chromadelete \
	chat-test


# ──────────────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────────────
install:
	uv sync


# ──────────────────────────────────────────────────────────────────────────────
# Docker
# ──────────────────────────────────────────────────────────────────────────────
up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100


# ──────────────────────────────────────────────────────────────────────────────
# Qualité de code
# ──────────────────────────────────────────────────────────────────────────────
test:
	uv run pytest -v

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy app


# ──────────────────────────────────────────────────────────────────────────────
# Base SQLite
# ──────────────────────────────────────────────────────────────────────────────
migrate:
	uv run python -m app.data.migrate


# ──────────────────────────────────────────────────────────────────────────────
# Ingestion — collecte par source (→ SQLite)
# ──────────────────────────────────────────────────────────────────────────────
ingest: arxiv-ingest tldr-ingest rss-ingest

arxiv-ingest:
	PYTHONPATH=. uv run python scripts/ingest_cli.py fetch

tldr-ingest:
	PYTHONPATH=. uv run python scripts/ingest_cli.py tldr

rss-ingest:
	PYTHONPATH=. uv run python scripts/ingest_cli.py rss


# ──────────────────────────────────────────────────────────────────────────────
# Ingestion — index & review
# ──────────────────────────────────────────────────────────────────────────────
index:
	PYTHONPATH=. CHROMA_URL=http://localhost:8002 uv run python scripts/ingest_cli.py index

# Récupère les bloquants (titre déchet et/ou contenu maigre) AVANT l'index : scrape la
# source pour relire le titre / résumer le contenu, rejette ce qui n'est pas récupérable.
# Dégrade proprement si l'agent mini n'est pas configuré (tout est skippé, retenté plus tard).
review-blocking:
	PYTHONPATH=. CHROMA_URL=http://localhost:8002 uv run python -m app.review.review_orchestrator blocking

# Passe d'annotation complète (keywords/tags de tous les non-reviewés), APRÈS l'index :
# patche la métadonnée des articles déjà indexés. Hors chemin critique de fraîcheur.
review:
	PYTHONPATH=. CHROMA_URL=http://localhost:8002 uv run python -m app.review.review_orchestrator


# ──────────────────────────────────────────────────────────────────────────────
# Pipelines bout en bout
# ──────────────────────────────────────────────────────────────────────────────
# Full chain: collect → recover blockers → index → classic review (annotate + sync
# Chroma metadata). The classic review runs last, after the index is reachable.
pipeline-e2e: ingest review-blocking index review

arxiv-e2e: arxiv-ingest review-blocking index

tldr-e2e: tldr-ingest review-blocking index

# ──────────────────────────────────────────────────────────────────────────────
# Runtime — aperçu live (hors index Chroma)
# ──────────────────────────────────────────────────────────────────────────────
# Aperçu live des news fraîches (GitHub releases + TLDR du jour) telles que /chat les
# verrait, hors index Chroma : tables rich récap par source + détail. Lecture seule.
fresh-news:
	PYTHONPATH=. uv run python -m app.runtime.fresh_news


# ──────────────────────────────────────────────────────────────────────────────
# Maintenance Chroma
# ──────────────────────────────────────────────────────────────────────────────
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


# ──────────────────────────────────────────────────────────────────────────────
# Chat
# ──────────────────────────────────────────────────────────────────────────────
chat-test:
	curl -s -X POST http://localhost:8000/chat \
		-H 'Content-Type: application/json' \
		-d '{"question":"Quelles tendances reviennent cette semaine ?","topics":["Python","AI/ML"]}' \
		| python -m json.tool
