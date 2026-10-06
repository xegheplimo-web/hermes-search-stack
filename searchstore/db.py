"""SQLite connection and schema migrations for SearchStore.

Contract: analysis/round3-interfaces.md section 2 (schema v1) and the
connection invariants WAL / foreign_keys=ON / busy_timeout=5000 /
synchronous=NORMAL.

R9-W2B (analysis/r9-interfaces.md §D): ``documents_fts`` is a STANDALONE
fts5 table — the index keeps its own copy of title/text with đ/Đ folded to
``d`` (the ``unicode61 remove_diacritics 2`` tokenizer folds vowel marks but
not ``đ``→``d``; U+0111 is non-decomposable). The fold is applied by the
triggers, the backfill, and — query side — ``SearchStore._fts_search``.
"""

import json
import os
import sqlite3
from datetime import datetime

SCHEMA_VERSION = 1

# Standalone FTS5 DDL (R9-W2B). Same table name/columns as the legacy
# external-content table, but without the ``content=``/``content_rowid=``
# wiring, and the triggers fold đ/Đ→d via nested replace() so unaccented VN
# queries hit đ-words. Shared by _MIGRATION_1 and migrate_fts_standalone()
# so fresh and migrated databases end up with identical schemas.
_FTS_DDL = """\
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
  title, text,
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
  INSERT INTO documents_fts(rowid,title,text) VALUES(new.id,
    replace(replace(new.title,'đ','d'),'Đ','d'),
    replace(replace(new.text,'đ','d'),'Đ','d'));
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
  DELETE FROM documents_fts WHERE rowid=old.id;
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
  DELETE FROM documents_fts WHERE rowid=old.id;
  INSERT INTO documents_fts(rowid,title,text) VALUES(new.id,
    replace(replace(new.title,'đ','d'),'Đ','d'),
    replace(replace(new.text,'đ','d'),'Đ','d'));
END;
"""

# Re-index every document into the standalone FTS with the đ/Đ fold. Used by
# migrate_fts_standalone() (legacy backfill) and SearchStore.rebuild_fts().
FTS_BACKFILL_SQL = (
    "INSERT INTO documents_fts(rowid,title,text) "
    "SELECT id, replace(replace(title,'đ','d'),'Đ','d'), "
    "replace(replace(text,'đ','d'),'Đ','d') FROM documents"
)

_MIGRATION_1 = (
    """\
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

"""
    + _FTS_DDL
    + """

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
)

_MIGRATIONS = [_MIGRATION_1]


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(path: str | os.PathLike) -> sqlite3.Connection:
    """Open a SQLite connection with the v1 invariants (WAL, FK on, etc.)."""
    conn = sqlite3.connect(os.fspath(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _fts_is_legacy_external_content(conn: sqlite3.Connection) -> bool:
    """True when ``documents_fts`` is the pre-R9-W2B external-content table."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='documents_fts'").fetchone()
    return row is not None and "content='documents'" in (row[0] or "")


def migrate_fts_standalone(conn: sqlite3.Connection) -> bool:
    """Migrate a legacy external-content ``documents_fts`` to the standalone
    đ-folding schema (R9-W2B).

    Drops the old triggers + table, recreates the standalone schema, and
    backfills from ``documents`` with the same đ/Đ→d fold. Idempotent: a
    no-op (returns False) when the table is already standalone. Records one
    ``fts_standalone_migrated`` event when a migration is applied.
    """
    if not _fts_is_legacy_external_content(conn):
        return False
    conn.executescript(
        "DROP TRIGGER IF EXISTS documents_ai;\n"
        "DROP TRIGGER IF EXISTS documents_ad;\n"
        "DROP TRIGGER IF EXISTS documents_au;\n"
        "DROP TABLE IF EXISTS documents_fts;\n" + _FTS_DDL
    )
    conn.execute(FTS_BACKFILL_SQL)
    payload = json.dumps(
        {
            "table": "documents_fts",
            "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        }
    )
    conn.execute(
        "INSERT INTO events(ts, kind, payload) VALUES(?,?,?)",
        (_iso_now(), "fts_standalone_migrated", payload),
    )
    conn.commit()
    return True


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations in order; idempotent.

    Records one ``migration_applied`` event per migration actually applied and
    returns the highest applied schema version (``PRAGMA user_version``).

    After the versioned migrations, a legacy external-content
    ``documents_fts`` (pre-R9-W2B) is transparently migrated to the
    standalone đ-folding schema — see ``migrate_fts_standalone``.
    """
    applied = conn.execute("PRAGMA user_version").fetchone()[0]
    for idx, sql in enumerate(_MIGRATIONS, start=1):
        if applied >= idx:
            continue
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {idx}")
        conn.execute(
            "INSERT INTO events(ts, kind, payload) VALUES(?,?,?)",
            (_iso_now(), "migration_applied", json.dumps({"version": idx})),
        )
        conn.commit()
        applied = idx
    migrate_fts_standalone(conn)
    return applied
