"""Misc / boundary tests for SearchStore v1 (R3-C scope).

Hermetic: every database lives under pytest's tmp_path; stdlib-only; no
network. Covers unicode roundtrips, empty-db stats, large docs, FTS rebuild,
WAL concurrency, url_key edges, empty export, and missing-document lookup.
"""

import threading

from searchstore import SearchStore, url_key

UNICODE_TEXT = "Quận Yên Dũng chào bạn 🎉 héllo 世界 中文テスト café naïve résumé 🚀🔥"


def _open(tmp_path, name="misc.db"):
    return SearchStore(tmp_path / name)


def test_unicode_emoji_cjk_roundtrip(tmp_path):
    store = _open(tmp_path)
    try:
        url = "https://example.com/unicode"
        title = "Tiêu đề 🎉 日本語タイトル"
        doc_id = store.ingest_document(url, UNICODE_TEXT, title=title, provider="test")
        doc = store.get_document(doc_id)
        assert doc is not None
        assert doc["text"] == UNICODE_TEXT
        assert doc["title"] == title
        assert doc["text"].encode("utf-8").decode("utf-8") == UNICODE_TEXT
        assert len(doc["content_sha256"]) == 64
    finally:
        store.close()


def test_empty_db_stats_keys(tmp_path):
    store = _open(tmp_path)
    try:
        stats = store.stats()
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
        assert required <= set(stats)
        assert stats["schema_version"] == 1
        assert stats["documents"] == 0
        assert stats["urls"] == 0
        assert stats["searches"] == 0
        assert stats["search_results"] == 0
        assert stats["reports"] == 0
        assert stats["report_sources"] == 0
        assert stats["embeddings"] == 0
        assert stats["fts"] is True
        assert stats["wal"] is True
        assert stats["first_fetched_at"] is None
        assert stats["last_fetched_at"] is None
    finally:
        store.close()


def test_large_100kb_text_insert_and_fts_find(tmp_path):
    store = _open(tmp_path)
    try:
        marker = "needlewordxyz"
        filler = "lorem ipsum dolor sit amet "
        repeats = (100 * 1024) // len(filler) + 10
        text = (filler * repeats) + " " + marker + " " + (filler * 10)
        assert len(text.encode("utf-8")) >= 100 * 1024
        doc_id = store.ingest_document("https://example.com/big", text, title="big doc")
        doc = store.get_document(doc_id)
        assert doc is not None
        assert doc["char_count"] == len(text)
        assert marker in doc["text"]
        hits = store.search(marker)
        assert [h["doc_id"] for h in hits] == [doc_id]
    finally:
        store.close()


def test_rebuild_fts_after_manual_fts_wipe(tmp_path):
    store = _open(tmp_path)
    try:
        d1 = store.ingest_document("https://example.com/a", "rebuildme alpha")
        d2 = store.ingest_document("https://example.com/b", "rebuildme beta")
        assert {h["doc_id"] for h in store.search("rebuildme")} == {d1, d2}
        store.conn.execute("DELETE FROM documents_fts")
        store.conn.commit()
        assert store.search("rebuildme") == []
        count = store.rebuild_fts()
        assert count == 2
        assert {h["doc_id"] for h in store.search("rebuildme")} == {d1, d2}
    finally:
        store.close()


def test_two_concurrent_readers_on_one_db(tmp_path):
    path = tmp_path / "shared.db"
    writer = SearchStore(path)
    try:
        doc_id = writer.ingest_document("https://example.com/shared", "concurrent hello world", title="shared")
        assert writer.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        errors: list = []

        def reader_job():
            try:
                with SearchStore(path, create=False) as reader:
                    for _ in range(20):
                        doc = reader.get_document(doc_id)
                        assert doc is not None
                        assert doc["text"] == "concurrent hello world"
                        reader.search("concurrent")
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=reader_job) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
    finally:
        writer.close()


def test_url_key_edge_cases():
    assert url_key("https://a.com/x#frag") == "https://a.com/x"
    assert url_key("https://a.com/x/") == "https://a.com/x"
    assert url_key("https://a.com/x/#frag") == "https://a.com/x"
    assert url_key("https://a.com/x?y=1#frag") == "https://a.com/x?y=1"
    assert url_key("https://a.com/x/?y=1#frag") == "https://a.com/x?y=1"
    assert url_key("HTTPS://A.COM/X/") == "https://a.com/X"
    assert url_key("https://a.com") == "https://a.com"
    assert url_key("https://a.com/") == "https://a.com"


def test_export_empty_table_returns_zero(tmp_path):
    store = _open(tmp_path)
    try:
        out = tmp_path / "empty" / "docs.jsonl"
        written = store.export("documents", str(out))
        assert written == 0
        assert out.exists()
        assert out.read_text(encoding="utf-8") == ""
        out2 = tmp_path / "empty" / "searches.jsonl"
        assert store.export("searches", str(out2)) == 0
    finally:
        store.close()


def test_get_document_missing_returns_none(tmp_path):
    store = _open(tmp_path)
    try:
        assert store.get_document(999999) is None
        assert store.get_document(-1) is None
    finally:
        store.close()


def test_unicode_export_jsonl_roundtrip(tmp_path):
    store = _open(tmp_path)
    try:
        store.ingest_document("https://example.com/u", UNICODE_TEXT, title="uni")
        out = tmp_path / "uni.jsonl"
        assert store.export("documents", str(out)) == 1
        raw = out.read_text(encoding="utf-8")
        assert "🎉" in raw and "世界" in raw
        assert "Yên" in raw
    finally:
        store.close()
