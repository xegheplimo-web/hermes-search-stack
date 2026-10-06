"""SQLite connection and schema migrations for SearchStore.

Contract: analysis/round3-interfaces.md section 2 (schema v1) and the
connection invariants WAL / foreign_keys=ON / busy_timeout=5000 /
synchronous=NORMAL.
"""

import json
import os
import sqlite3
from datetime import datetime

SCHEMA_VERSION = 1

_MIGRATION_1 = """\
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


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations in order; idempotent.

    Records one ``migration_applied`` event per migration actually applied and
    returns the highest applied schema version (``PRAGMA user_version``).
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
    return applied
