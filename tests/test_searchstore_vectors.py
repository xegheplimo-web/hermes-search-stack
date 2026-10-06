"""Vector tier tests (contract §6 / §7-R3-B)."""

from __future__ import annotations

import pytest

from searchstore.store import SearchStore, SearchStoreError
from searchstore.vectors import (
    _similar_python,
    add_embedding,
    embedding_count,
    similar,
    tier_available,
)


@pytest.fixture
def store(tmp_path):
    instance = SearchStore(str(tmp_path / "vectors.db"))
    yield instance
    instance.close()


@pytest.fixture
def doc(store):
    return store.ingest_document("https://example.com/doc", "hello world")


class TestTier:
    def test_tier_available_valid(self):
        assert tier_available() in ("sqlite-vec", "numpy", "python")

    def test_python_tier_is_always_available(self, store):
        # The pure-python backend must work even when it is not the active tier.
        doc_id = store.ingest_document("https://example.com/floor", "floor")
        add_embedding(store.conn, doc_id, [1.0, 0.0, 0.0], model="floor")
        rows = _similar_python(store.conn, [1.0, 0.0, 0.0], k=10)
        assert rows == [{"doc_id": doc_id, "score": pytest.approx(1.0)}]


class TestAddEmbedding:
    def test_roundtrip(self, store, doc):
        add_embedding(store.conn, doc, [1.0, 0.0, 0.0], model="test")
        assert embedding_count(store.conn) == 1
        rows = similar(store.conn, [1.0, 0.0, 0.0])
        assert len(rows) == 1
        assert rows[0]["doc_id"] == doc
        assert rows[0]["score"] == pytest.approx(1.0)

    def test_replace_same_doc_id(self, store, doc):
        add_embedding(store.conn, doc, [1.0, 0.0, 0.0], model="test")
        add_embedding(store.conn, doc, [0.0, 1.0, 0.0], model="test")
        assert embedding_count(store.conn) == 1
        rows = similar(store.conn, [0.0, 1.0, 0.0])
        assert rows[0]["doc_id"] == doc
        assert rows[0]["score"] == pytest.approx(1.0)

    def test_empty_vector_rejected(self, store, doc):
        with pytest.raises(SearchStoreError):
            add_embedding(store.conn, doc, [])

    @pytest.mark.parametrize("bad", [[float("nan")], [float("inf")], [float("-inf")], [1.0, float("nan")]])
    def test_non_finite_rejected(self, store, doc, bad):
        with pytest.raises(SearchStoreError):
            add_embedding(store.conn, doc, bad)

    def test_model_and_dim_stored(self, store, doc):
        add_embedding(store.conn, doc, [0.5, 0.5, 0.5, 0.5], model="my-model")
        row = store.conn.execute(
            "SELECT model, dim, length(vector) FROM embeddings WHERE doc_id = ?", (doc,)
        ).fetchone()
        assert tuple(row) == ("my-model", 4, 16)


class TestSimilar:
    def test_empty_table(self, store):
        assert similar(store.conn, [1.0, 2.0]) == []

    def test_cosine_ordering(self, store):
        a = store.ingest_document("https://example.com/a", "alpha")
        b = store.ingest_document("https://example.com/b", "beta")
        c = store.ingest_document("https://example.com/c", "gamma")
        add_embedding(store.conn, a, [1.0, 0.0, 0.0])
        add_embedding(store.conn, b, [0.0, 1.0, 0.0])
        add_embedding(store.conn, c, [0.6, 0.8, 0.0])
        rows = similar(store.conn, [1.0, 0.0, 0.0])
        assert [r["doc_id"] for r in rows] == [a, c, b]
        assert rows[0]["score"] == pytest.approx(1.0)
        assert rows[1]["score"] == pytest.approx(0.6, abs=1e-6)
        assert rows[2]["score"] == pytest.approx(0.0)

    def test_k_limit(self, store):
        for i in range(3):
            doc_id = store.ingest_document(f"https://example.com/{i}", f"doc {i}")
            add_embedding(store.conn, doc_id, [1.0, 0.0])
        assert len(similar(store.conn, [1.0, 0.0], k=2)) == 2

    def test_dim_mismatch_returns_empty(self, store, doc):
        add_embedding(store.conn, doc, [1.0, 0.0])
        assert similar(store.conn, [1.0]) == []

    def test_zero_vector_scores_zero(self, store, doc):
        add_embedding(store.conn, doc, [0.0, 0.0])
        rows = similar(store.conn, [1.0, 0.0])
        assert rows[0]["score"] == pytest.approx(0.0)

    def test_invalid_query_vector(self, store, doc):
        add_embedding(store.conn, doc, [1.0, 0.0])
        with pytest.raises(SearchStoreError):
            similar(store.conn, [])
        with pytest.raises(SearchStoreError):
            similar(store.conn, [float("nan")])

    def test_scores_within_range(self, store):
        a = store.ingest_document("https://example.com/a", "alpha")
        b = store.ingest_document("https://example.com/b", "beta")
        add_embedding(store.conn, a, [1.0, 2.0, 3.0])
        add_embedding(store.conn, b, [-1.0, 0.5, 0.25])
        for row in similar(store.conn, [0.5, 0.5, 0.5]):
            assert -1.0 <= row["score"] <= 1.0


class TestCount:
    def test_embedding_count(self, store):
        assert embedding_count(store.conn) == 0
        doc_id = store.ingest_document("https://example.com/a", "alpha")
        add_embedding(store.conn, doc_id, [1.0])
        assert embedding_count(store.conn) == 1
        other = store.ingest_document("https://example.com/b", "beta")
        add_embedding(store.conn, other, [0.0, 1.0])
        assert embedding_count(store.conn) == 2
