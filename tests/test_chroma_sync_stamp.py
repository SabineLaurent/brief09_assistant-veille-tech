import sqlite3
from datetime import datetime
from types import SimpleNamespace

from app.data.article_store import mark_chroma_synced, upsert_article
from app.data.migrate import init_db
from app.review import review_orchestrator as runner


def _make_db(tmp_path):
    db = str(tmp_path / "ingest.db")
    init_db(db)
    return db


def _insert(db, reference):
    upsert_article(
        {
            "reference": reference,
            "title": "A Title",
            "source": "s",
            "published_date": None,
            "updated_date": None,
            "content": "content",
            "url": "http://x",
            "tags": ["cs.AI"],
            "keywords": [],
            "authors": [],
            "ingested_at": datetime.now(),
        },
        db_path=db,
    )


# ── mark_chroma_synced (real SQLite) ──────────────────────────────────────────


def test_mark_chroma_synced_stamps_column(tmp_path):
    db = _make_db(tmp_path)
    _insert(db, "r1")
    # fresh record: the audit stamp starts NULL
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT chroma_synced_at FROM article WHERE reference = ?", ("r1",)
        ).fetchone()[0] is None

    mark_chroma_synced("r1", db_path=db)

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT chroma_synced_at FROM article WHERE reference = ?", ("r1",)
        ).fetchone()[0] is not None


# ── runner: stamp only when chunks were actually patched ──────────────────────


def _annotated_result():
    # A valid record merely being annotated (no recovery) → eligible for a Chroma patch.
    return SimpleNamespace(
        rejected=False,
        keywords=["kw"],
        tags=["AI"],
        generated_summary=None,
        recovered_title=None,
    )


def _wire_runner(monkeypatch, patched_chunks, calls):
    monkeypatch.setattr(runner, "review_article", lambda a: _annotated_result())
    monkeypatch.setattr(runner, "update_article_records_with_llm_reviews", lambda **k: None)
    monkeypatch.setattr(runner, "patch_article_metadata", lambda ref, tags, kw: patched_chunks)
    monkeypatch.setattr(runner, "mark_chroma_synced", lambda ref, **k: calls.append(ref))


def test_runner_stamps_synced_when_chunks_patched(monkeypatch):
    calls: list[str] = []
    _wire_runner(monkeypatch, patched_chunks=3, calls=calls)

    result = runner._review_and_persist([{"reference": "r1"}])

    assert calls == ["r1"]
    assert result.patched_chunks == 3


def test_runner_does_not_stamp_when_no_chunks(monkeypatch):
    # Not-yet-indexed record: patch returns 0 → nothing synced, nothing to record.
    calls: list[str] = []
    _wire_runner(monkeypatch, patched_chunks=0, calls=calls)

    result = runner._review_and_persist([{"reference": "r1"}])

    assert calls == []
    assert result.patched_chunks == 0
