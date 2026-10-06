"""Core tests for SearchStore v1 (R3-A scope).

Hermetic: all databases live under pytest's tmp_path; no network. These tests
must pass with only R3-A files present — the optional vector tier
(searchstore.vectors, owned by R3-B) is exercised only via importorskip /
monkeypatched absence.
"""

import json
import sqlite3
import sys

import pytest

import searchstore
from searchstore import SearchStore, SearchStoreError, content_sha256, url_key
from searchstore import db as db_module

VN_TEXT = "Quận Yên Dũng, tỉnh Bắc Giang, Việt Nam"


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


_COUNT_SQL = {
    "documents": "SELECT COUNT(*) FROM documents",
    "searches": "SELECT COUNT(*) FROM searches",
    "search_results": "SELECT COUNT(*) FROM search_results",
    "reports": "SELECT COUNT(*) FROM reports",
    "report_sources": "SELECT COUNT(*) FROM report_sources",
    "events": "SELECT COUNT(*) FROM events",
    "embeddings": "SELECT COUNT(*) FROM embeddings",
}


def _count(conn, table):
    return conn.execute(_COUNT_SQL[table]).fetchone()[0]


def _event_kinds(conn):
    return [r["kind"] for r in conn.execute("SELECT kind FROM events ORDER BY id")]


# ---------- db / connection / migrations ----------


def test_connection_invariants(store):
    c = store.conn
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert c.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_schema_version(store):
    assert db_module.SCHEMA_VERSION == 1
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert len(db_module._MIGRATIONS) >= 1


def test_migrate_idempotent(store):
    conn = store.conn
    objects = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='migration_applied'").fetchone()[0] == 1
    assert db_module.migrate(conn) == 1  # second run is a no-op
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == objects
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='migration_applied'").fetchone()[0] == 1


def test_schema_objects_exist(store):
    names = {r["name"] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    expected = {
        "documents",
        "documents_fts",
        "searches",
        "search_results",
        "reports",
        "report_sources",
        "events",
        "kv",
        "embeddings",
        "documents_current",
    }
    assert expected <= names


def test_create_false_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        SearchStore(tmp_path / "nope.db", create=False)


def test_create_makes_parent_dirs(tmp_path):
    s = SearchStore(tmp_path / "deep" / "nested" / "d.db")
    try:
        assert (tmp_path / "deep" / "nested" / "d.db").exists()
    finally:
        s.close()


# ---------- url_key / content_sha256 ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP://Example.COM/Path", "http://example.com/Path"),
        ("https://a.com/x#frag", "https://a.com/x"),
        ("https://a.com/x/", "https://a.com/x"),
        ("https://a.com/x?y=1&z=2", "https://a.com/x?y=1&z=2"),
        ("https://a.com/?q=1", "https://a.com?q=1"),
        ("https://a.com", "https://a.com"),
        ("HTTPS://A.COM/X/#f", "https://a.com/X"),
    ],
)
def test_url_key_normalization(raw, expected):
    assert url_key(raw) == expected


def test_content_sha256():
    assert content_sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert len(content_sha256("x")) == 64
    assert content_sha256("Yên Dũng") != content_sha256("Yen Dung")


def test_package_exports():
    assert searchstore.__version__ == "0.1.0"
    assert searchstore.SearchStore is SearchStore
    assert issubclass(SearchStoreError, Exception)


# ---------- ingest: dedup + versioning ----------


def test_dedup_returns_same_id(store):
    id1 = store.ingest_document("https://a.com/x", "hello world", title="t")
    id2 = store.ingest_document("https://a.com/x", "hello world", title="ignored")
    assert id1 == id2
    assert _count(store.conn, "documents") == 1
    # dedup hit records no new event
    assert _event_kinds(store.conn).count("document_ingested") == 1


def test_dedup_uses_normalized_url(store):
    id1 = store.ingest_document("https://a.com/x/", "same body")
    id2 = store.ingest_document("HTTPS://A.COM/x#frag", "same body")
    assert id1 == id2
    assert _count(store.conn, "documents") == 1


def test_same_url_new_content_versions(store):
    id1 = store.ingest_document("https://a.com/p", "version one alpha")
    id2 = store.ingest_document("https://a.com/p", "version two beta")
    assert id2 != id1
    assert _count(store.conn, "documents") == 2
    cur = store.conn.execute("SELECT * FROM documents_current WHERE url_key = 'https://a.com/p'").fetchall()
    assert len(cur) == 1
    assert cur[0]["id"] == id2
    assert "beta" in cur[0]["text"]


def test_ingest_document_fields(store):
    did = store.ingest_document(
        "https://e.com/doc",
        "some text here",
        title="Doc",
        provider="tavily",
        fetched_at="2026-10-06T10:00:00+07:00",
        format="markdown",
        meta={"k": "v"},
    )
    d = store.get_document(did)
    assert d["title"] == "Doc"
    assert d["provider"] == "tavily"
    assert d["fetched_at"] == "2026-10-06T10:00:00+07:00"
    assert d["char_count"] == len("some text here")
    assert json.loads(d["meta"]) == {"k": "v"}
    assert len(d["content_sha256"]) == 64


# ---------- FTS search ----------


def test_fts_vietnamese_diacritics(store):
    did = store.ingest_document("https://ex.com/vn", VN_TEXT, title="Địa danh")
    hits = store.search('"Yên Dũng"')  # exact phrase with diacritics
    assert [h["doc_id"] for h in hits] == [did]
    folded = store.search("dung")  # no diacritics -> folds to match Dũng
    assert [h["doc_id"] for h in folded] == [did]
    assert [h["doc_id"] for h in store.search("Yên")] == [did]


def test_fts_phrase_and_boolean(store):
    d1 = store.ingest_document("https://x.com/1", "the quick brown fox jumps")
    d2 = store.ingest_document("https://x.com/2", "quick fox only")
    assert {h["doc_id"] for h in store.search('"quick brown"')} == {d1}
    assert {h["doc_id"] for h in store.search('"brown quick"')} == set()  # phrase order matters
    assert {h["doc_id"] for h in store.search("quick AND brown")} == {d1}
    assert {h["doc_id"] for h in store.search("quick OR brown")} == {d1, d2}
    assert {h["doc_id"] for h in store.search("quick NOT brown")} == {d2}


def test_search_result_shape_and_snippet(store):
    store.ingest_document("https://x.com/a", "alpha beta gamma delta", title="T", provider="p")
    hits = store.search("beta")
    assert len(hits) == 1
    h = hits[0]
    assert {
        "doc_id",
        "url",
        "title",
        "provider",
        "fetched_at",
        "snippet",
        "score",
        "mode_used",
    } <= set(h)
    assert h["mode_used"] == "fts"
    assert h["url"] == "https://x.com/a"
    assert "<b>" in h["snippet"] and "</b>" in h["snippet"]
    assert isinstance(h["score"], float)


def test_search_no_match_returns_empty(store):
    store.ingest_document("https://x.com/a", "some words")
    assert store.search("zzzqqq") == []


def test_search_malformed_query_raises(store):
    store.ingest_document("https://x.com/a", "some words")
    with pytest.raises(SearchStoreError):
        store.search('say "hi')  # unterminated string -> fts5 syntax error
    with pytest.raises(SearchStoreError):
        store.search("foo AND")  # dangling operator


def test_search_unknown_mode_raises(store):
    with pytest.raises(SearchStoreError):
        store.search("x", mode="bogus")


def test_hybrid_requires_query_vector(store):
    with pytest.raises(SearchStoreError):
        store.search("x", mode="hybrid")


# ---------- ingest_search / ingest_report ----------


def test_ingest_search_positions_and_count(store):
    results = [
        {"url": "https://a.com/1", "title": "t1", "snippet": "s1"},
        {"url": "https://a.com/2"},
        {"url": "https://a.com/3", "title": "t3", "snippet": "s3"},
    ]
    sid = store.ingest_search("my query", results, provider="tavily", latency_ms=42)
    srow = store.conn.execute("SELECT * FROM searches WHERE id = ?", (sid,)).fetchone()
    assert srow["query"] == "my query"
    assert srow["result_count"] == 3
    assert srow["provider"] == "tavily"
    assert srow["engine"] == "web_search"
    assert srow["latency_ms"] == 42
    rows = store.conn.execute("SELECT * FROM search_results WHERE search_id = ? ORDER BY position", (sid,)).fetchall()
    assert [r["position"] for r in rows] == [0, 1, 2]
    assert [r["url"] for r in rows] == ["https://a.com/1", "https://a.com/2", "https://a.com/3"]
    assert rows[1]["title"] is None


def test_ingest_report_and_replace_sources(store):
    rid = store.ingest_report(
        "rep-1",
        title="Report",
        text="one two three",
        sources=[{"url": "https://s1", "title": "S1"}, {"url": "https://s2", "quote": "q2"}],
    )
    rep = store.conn.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
    assert rep["word_count"] == 3
    assert rep["citation_count"] == 2
    srcs = store.conn.execute("SELECT * FROM report_sources WHERE report_id = ? ORDER BY position", (rid,)).fetchall()
    assert [s["url"] for s in srcs] == ["https://s1", "https://s2"]
    assert [s["position"] for s in srcs] == [0, 1]

    # re-ingest same slug -> same report row updated, sources replaced
    rid2 = store.ingest_report("rep-1", title="Report v2", text="four", sources=[{"url": "https://s3"}])
    assert rid2 == rid
    rep = store.conn.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
    assert rep["title"] == "Report v2"
    assert rep["word_count"] == 1
    assert rep["citation_count"] == 1
    srcs = store.conn.execute("SELECT * FROM report_sources WHERE report_id = ?", (rid,)).fetchall()
    assert len(srcs) == 1 and srcs[0]["url"] == "https://s3"


# ---------- events / kv / get_document ----------


def test_events_recorded(store):
    store.ingest_document("https://a.com/x", "body")
    store.ingest_search("q", [{"url": "https://r.com"}])
    store.ingest_report("slug-1")
    eid = store.record_event("custom", {"a": 1})
    kinds = _event_kinds(store.conn)
    assert kinds[0] == "migration_applied"
    for k in ("document_ingested", "search_ingested", "report_ingested", "custom"):
        assert k in kinds
    row = store.conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    assert json.loads(row["payload"]) == {"a": 1}
    doc_ev = store.conn.execute("SELECT payload FROM events WHERE kind = 'document_ingested'").fetchone()
    assert json.loads(doc_ev["payload"])["url"] == "https://a.com/x"


def test_kv_table(store):
    store.conn.execute("INSERT INTO kv(key, value) VALUES('k1', 'v1')")
    store.conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('k1', 'v2')")
    store.conn.commit()
    assert store.conn.execute("SELECT value FROM kv WHERE key='k1'").fetchone()[0] == "v2"


def test_get_document(store):
    did = store.ingest_document("https://a.com/doc", "text body")
    d = store.get_document(did)
    assert d["id"] == did
    assert d["url"] == "https://a.com/doc"
    assert d["text"] == "text body"
    assert store.get_document(999999) is None


# ---------- export / rebuild / stats ----------


def test_export_jsonl(store, tmp_path):
    for i in range(3):
        store.ingest_document(f"https://e.com/{i}", f"body {i}")
    out = tmp_path / "export" / "docs.jsonl"
    n = store.export("documents", str(out))
    assert n == 3
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == _count(store.conn, "documents")
    first = json.loads(lines[0])
    assert {"id", "url", "url_key", "content_sha256", "text"} <= set(first)


def test_export_md_reports_only(store, tmp_path):
    store.ingest_report("my-report", title="My Report", sources=[{"url": "https://s", "title": "S", "quote": "qt"}])
    outdir = tmp_path / "md_out"
    n = store.export("reports", str(outdir), format="md")
    assert n == 1
    content = (outdir / "my-report.md").read_text(encoding="utf-8")
    assert "# My Report" in content
    assert "https://s" in content
    with pytest.raises(SearchStoreError):
        store.export("documents", str(tmp_path / "x"), format="md")
    with pytest.raises(SearchStoreError):
        store.export("not_a_table", str(tmp_path / "y.jsonl"))


def test_rebuild_fts(store):
    store.ingest_document("https://a.com/1", "rebuild alpha")
    store.ingest_document("https://a.com/2", "rebuild beta")
    assert store.rebuild_fts() == 2
    assert {h["doc_id"] for h in store.search("rebuild")} == {1, 2}


def test_stats_keys_and_values(store):
    store.ingest_document("https://a.com/p", "v1")
    store.ingest_document("https://a.com/p", "v2")  # second version, same url_key
    s = store.stats()
    required = {
        "db_path",
        "db_bytes",
        "schema_version",
        "documents",
        "urls",
        "searches",
        "search_results",
        "reports",
        "report_sources",
        "events",
        "embeddings",
        "vector_tier",
        "fts",
        "wal",
        "first_fetched_at",
        "last_fetched_at",
    }
    assert required <= set(s)
    assert s["schema_version"] == 1
    assert s["documents"] == 2
    assert s["urls"] == 1
    assert s["fts"] is True
    assert s["wal"] is True
    assert s["db_bytes"] > 0
    assert s["first_fetched_at"] and s["last_fetched_at"]
    assert s["vector_tier"] in {"unavailable", "sqlite-vec", "numpy", "python"}


# ---------- lifecycle ----------


def test_close_and_context_manager(tmp_path):
    path = tmp_path / "ctx.db"
    with SearchStore(path) as s:
        did = s.ingest_document("https://a.com/x", "ctx body")
        assert s.get_document(did)["url"] == "https://a.com/x"
    with pytest.raises(sqlite3.ProgrammingError):
        s.conn.execute("SELECT 1")


# ---------- vector tier decoupling ----------


def test_vectors_unavailable_path(store, monkeypatch):
    """With searchstore.vectors absent, vector ops raise SearchStoreError and
    stats reports 'unavailable' — deterministic regardless of R3-B's files."""
    monkeypatch.delattr(sys.modules["searchstore"], "vectors", raising=False)
    monkeypatch.setitem(sys.modules, "searchstore.vectors", None)
    assert store.stats()["vector_tier"] == "unavailable"
    with pytest.raises(SearchStoreError):
        store.add_embedding(1, [0.1, 0.2])
    with pytest.raises(SearchStoreError):
        store.similar([0.1, 0.2])
    with pytest.raises(SearchStoreError):
        store.search("x", mode="hybrid", query_vector=[0.1, 0.2])


def test_embeddings_roundtrip_if_vectors_present(store):
    """Integration check: only runs once R3-B's vectors.py exists."""
    pytest.importorskip("searchstore.vectors")
    d1 = store.ingest_document("https://v.com/1", "vec one")
    d2 = store.ingest_document("https://v.com/2", "vec two")
    store.add_embedding(d1, [1.0, 0.0])
    store.add_embedding(d2, [0.0, 1.0])
    sims = store.similar([1.0, 0.0], k=2)
    assert sims[0]["doc_id"] == d1
    assert sims[0]["score"] >= sims[-1]["score"]
    assert "embedding_added" in _event_kinds(store.conn)
    assert store.stats()["embeddings"] == 2
