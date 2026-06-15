import json

import numpy as np

from app.indexing import indexer

TITLE = "Real Article Title"
CONTENT = "x" * 400  # above MIN_CONTENT_CHARS → a valid record that gets indexed


class _FakeEmbedder:
    """Capture the texts passed to encode and return one dummy vector per text."""

    def __init__(self):
        self.encoded = None

    def encode(self, texts, normalize_embeddings=False):
        self.encoded = list(texts)
        # Mirror sentence-transformers: a numpy array (the code calls v.tolist()).
        return np.zeros((len(self.encoded), 2))


class _FakeCollection:
    def __init__(self):
        self.upsert_kwargs = None

    def upsert(self, **kwargs):
        self.upsert_kwargs = kwargs


def test_title_is_embedded_but_document_stays_raw(monkeypatch):
    embedder = _FakeEmbedder()
    collection = _FakeCollection()
    monkeypatch.setattr(indexer, "get_embedder", lambda: embedder)
    monkeypatch.setattr(indexer, "get_collection", lambda: collection)
    monkeypatch.setattr(indexer, "update_article_status", lambda *a, **k: None)

    article = {
        "reference": "ref1",
        "title": TITLE,
        "content": CONTENT,
        "tags": json.dumps(["AI"]),
        "keywords": json.dumps(["kw"]),
    }
    result = indexer.index_articles([article])

    assert result.indexed == 1
    # Every embedded text carries the title prefix...
    assert embedder.encoded
    assert all(text.startswith(f"{TITLE}\n\n") for text in embedder.encoded)
    # ...but the stored documents stay the raw chunks (no title pollution in snippets).
    assert all(not doc.startswith(TITLE) for doc in collection.upsert_kwargs["documents"])
