from app.rag import retrieval


class _FakeCollection:
    """Return a fixed Chroma-shaped result; record the n_results it was asked for."""

    def __init__(self, result):
        self._result = result
        self.asked_n_results = None

    def query(self, query_embeddings, n_results):
        self.asked_n_results = n_results
        return self._result


# Two chunks of article A (A is the closest), then B and C. Distances ascending, as Chroma returns them.
_RESULT = {
    "ids": [["A::0", "A::1", "B::0", "C::0"]],
    "documents": [["a-best", "a-second", "b", "c"]],
    "metadatas": [[{"reference": "A"}, {"reference": "A"}, {"reference": "B"}, {"reference": "C"}]],
    "distances": [[0.1, 0.2, 0.3, 0.4]],
}


def _wire(monkeypatch, result):
    collection = _FakeCollection(result)
    monkeypatch.setattr(retrieval, "get_collection", lambda: collection)
    monkeypatch.setattr(retrieval, "embed", lambda q: [0.0, 0.0])
    return collection


def test_dedupes_by_reference_keeping_best_chunk(monkeypatch):
    _wire(monkeypatch, _RESULT)
    out = retrieval.retrieve("q", k=8)
    references = [c["metadata"]["reference"] for c in out]
    assert references == ["A", "B", "C"]  # A appears once
    # The kept A chunk is the closest one (smallest distance).
    a = next(c for c in out if c["metadata"]["reference"] == "A")
    assert a["content"] == "a-best"


def test_respects_k_after_dedupe(monkeypatch):
    _wire(monkeypatch, _RESULT)
    out = retrieval.retrieve("q", k=2)
    assert [c["metadata"]["reference"] for c in out] == ["A", "B"]


def test_oversamples_raw_hits(monkeypatch):
    collection = _wire(monkeypatch, _RESULT)
    retrieval.retrieve("q", k=8, oversample=3)
    assert collection.asked_n_results == 24  # k * oversample


def test_chunks_without_reference_are_not_merged(monkeypatch):
    result = {
        "ids": [["x::0", "y::0"]],
        "documents": [["x", "y"]],
        "metadatas": [[{}, {}]],  # no reference key
        "distances": [[0.1, 0.2]],
    }
    _wire(monkeypatch, result)
    out = retrieval.retrieve("q", k=8)
    assert len(out) == 2
