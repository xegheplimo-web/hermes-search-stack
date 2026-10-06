"""Misc / boundary tests for SearchStore v1 (R3-C scope).

Hermetic: every database lives under pytest's tmp_path; stdlib-only; no
network. Covers unicode roundtrips, empty-db stats, large docs, FTS rebuild,
WAL concurrency, url_key edges, empty export, and missing-document lookup.
"""

import sqlite3
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


# ---------- legacy external-content migration (R9-W2B) ----------

# The pre-R9-W2B schema: external-content FTS5 with plain (non-folding)
# triggers. Used to verify the transparent migration in
# db.migrate_fts_standalone() (analysis/r9-interfaces.md §D).
_LEGACY_MIGRATION_1 = """\
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  url_key TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  title TEXT,
  provider TEXT,
  format TEXT NOT NULL DEFAULT 'markdown',
  fetched_at TEXT NOT NULL,
  char_count INTEGER NOT NULL,
  text TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}',
  UNIQUE(url_key, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_documents_url_key ON documents(url_key);
CREATE INDEX IF NOT EXISTS idx_documents_fetched ON documents(fetched_at);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
  title, text, content='documents', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
  INSERT INTO documents_fts(rowid,title,text) VALUES(new.id,new.title,new.text);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
  INSERT INTO documents_fts(documents_fts,rowid,title,text) VALUES('delete',old.id,old.title,old.text);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
  INSERT INTO documents_fts(documents_fts,rowid,title,text) VALUES('delete',old.id,old.title,old.text);
  INSERT INTO documents_fts(rowid,title,text) VALUES(new.id,new.title,new.text);
END;

CREATE TABLE IF NOT EXISTS searches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query TEXT NOT NULL,
  provider TEXT,
  engine TEXT NOT NULL DEFAULT 'web_search',
  ts TEXT NOT NULL,
  latency_ms INTEGER,
  result_count INTEGER NOT NULL DEFAULT 0,
  meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS search_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  title TEXT,
  url TEXT NOT NULL,
  snippet TEXT
);
CREATE INDEX IF NOT EXISTS idx_search_results_search ON search_results(search_id);

CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT,
  path TEXT,
  created_at TEXT NOT NULL,
  word_count INTEGER,
  citation_count INTEGER NOT NULL DEFAULT 0,
  meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS report_sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  title TEXT,
  quote TEXT,
  position INTEGER
);
CREATE INDEX IF NOT EXISTS idx_report_sources_report ON report_sources(report_id);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS embeddings(
  doc_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL,
  created_at TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS documents_current AS
SELECT d.* FROM documents d
WHERE d.id = (SELECT MAX(x.id) FROM documents x WHERE x.url_key = d.url_key);
"""

_LEGACY_DOC_INSERT = (
    "INSERT INTO documents(url, url_key, content_sha256, title, provider, format, fetched_at, char_count, text, meta)"
    " VALUES(?,?,?,?,?,?,?,?,?,?)"
)


def _event_kinds(conn):
    return [r["kind"] for r in conn.execute("SELECT kind FROM events ORDER BY id")]


def _make_legacy_db(path):
    """Build a pre-R9-W2B DB (external-content FTS, no đ fold) with one đ-doc."""
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_MIGRATION_1)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        _LEGACY_DOC_INSERT,
        (
            "https://ex.com/1",
            "https://ex.com/1",
            "a" * 64,
            "Nghị định",
            "test",
            "markdown",
            "2026-10-06T10:00:00+00:00",
            9,
            "Nghị định 123",
            "{}",
        ),
    )
    conn.commit()
    return conn


def test_legacy_external_content_db_migrates_on_open(tmp_path):
    path = tmp_path / "legacy.db"
    conn = _make_legacy_db(path)
    try:
        # sanity: the hand-built DB really is the legacy external-content schema
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='documents_fts'").fetchone()[0]
        assert "content='documents'" in sql
        # sanity: the legacy index does NOT fold đ (the defect being fixed)
        count_sql = "SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?"
        assert conn.execute(count_sql, ("nghi dinh",)).fetchone()[0] == 0
        assert conn.execute(count_sql, ("nghị định",)).fetchone()[0] == 1
    finally:
        conn.close()

    # opening the store migrates transparently
    with SearchStore(path) as store:
        sql = store.conn.execute("SELECT sql FROM sqlite_master WHERE name='documents_fts'").fetchone()[0]
        assert "content='documents'" not in sql
        assert "content_rowid" not in sql
        # backfill copied every document into the standalone index
        assert store.conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 1
        # both spellings now hit the same doc
        assert {h["doc_id"] for h in store.search("nghị định")} == {1}
        assert {h["doc_id"] for h in store.search("nghi dinh")} == {1}
        # migration recorded via the events mechanism
        assert "fts_standalone_migrated" in _event_kinds(store.conn)
        # canonical content untouched
        assert store.get_document(1)["text"] == "Nghị định 123"

    # idempotent reopen: no second migration event, searches still work
    with SearchStore(path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM events WHERE kind='fts_standalone_migrated'").fetchone()[0] == 1
        assert {h["doc_id"] for h in store.search("nghi dinh")} == {1}
