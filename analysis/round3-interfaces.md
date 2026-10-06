# Round 3 — Frozen interfaces: `searchstore` v1 (SearchStore)

Owner: orchestrator. Status: **FROZEN 2026-10-06**. Agents build exactly to this contract.
Companion research/design: `analysis/storage-modern.md`.

## 0. Ground rules (all R3 agents)

- Write ONLY the files listed in your task scope. Everything else is READ-ONLY.
- English deliverables. No commits, no pushes, no config edits, no installs, no network in tests.
- **stdlib-only** (sqlite3, argparse, json, hashlib, os, pathlib, datetime, re, math, sys, contextlib).
  `vectors.py` may `import sqlite_vec` / `import numpy` ONLY inside `try/except ImportError`.
- ruff clean BOTH modes on your files: `ruff check .` AND `ruff format --check .` (repo venv:
  `.venv/Scripts/python.exe -m ruff ...`). CI enforces both.
- Tests hermetic, use `tmp_path`; never create DB files inside the repo tree.
- Python 3.11+ syntax OK (`str | None`). Full repo suite must stay green.

## 1. Package layout (files are disjoint per agent)

```
searchstore/__init__.py     R3-A   exports: SearchStore, SearchStoreError, __version__ = "0.1.0"
searchstore/db.py           R3-A   connect(), SCHEMA_VERSION=1, migrate()
searchstore/store.py        R3-A   SearchStore class + SearchStoreError
searchstore/cli.py          R3-B   argparse CLI, main(argv=None) -> int
searchstore/__main__.py     R3-B   `from .cli import main; raise SystemExit(main())`
searchstore/adapters.py     R3-B   load_battery_json(), load_keyless_json(), load_report_md()
searchstore/vectors.py      R3-B   tier_available(), add_embedding(), similar(), embedding_count()
searchstore/README.md       R3-C   usage + schema + extension guide
tests/test_searchstore_core.py      R3-A
tests/test_searchstore_cli.py       R3-B
tests/test_searchstore_adapters.py  R3-B
tests/test_searchstore_vectors.py   R3-B
tests/test_searchstore_misc.py      R3-C
tests/fixtures/searchstore/         R3-B (sanitized samples copied from results/)
```

## 2. Schema v1 — EXACT SQL (verbatim; R3-A puts this in db.py as migration 1)

```sql
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
```

Invariants: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`.
Migrations: `_MIGRATIONS` list; apply each where `len(applied) < idx`; idempotent; record in events
(`kind="migration_applied"`); `user_version` = highest applied.

## 3. Python API — EXACT signatures (R3-A implements; R3-B/C import)

```python
class SearchStoreError(Exception):
    """All library errors. Message must be actionable."""

def url_key(url: str) -> str:  # normalize: lowercase scheme+host, strip fragment, strip trailing '/', keep query
def content_sha256(text: str) -> str:  # hex sha256 of utf-8

class SearchStore:
    def __init__(self, db_path: str | os.PathLike, *, create: bool = True): ...
    # create=True: mkdir parents, connect, migrate. create=False: raise FileNotFoundError if missing.
    @property
    def conn(self) -> sqlite3.Connection: ...
    def close(self) -> None: ...
    def __enter__(self) / __exit__(self, *exc): ...

    def ingest_document(self, url: str, text: str, *, title: str | None = None,
                        provider: str | None = None, fetched_at: str | None = None,
                        format: str = "markdown", meta: dict | None = None) -> int:
        # dedup: if (url_key, sha256) exists -> return existing id, NO new row, NO event.
        # new content for same URL -> new row (versioning). fetched_at default: now ISO-8601 local.
        # records event "document_ingested". returns documents.id.

    def ingest_search(self, query: str, results: list[dict], *, provider: str | None = None,
                      engine: str = "web_search", ts: str | None = None,
                      latency_ms: int | None = None, meta: dict | None = None) -> int:
        # results: [{"url": str, "title": str|None, "snippet": str|None}, ...] — position = index (0-based).
        # result_count = len(results). records event "search_ingested". returns searches.id.

    def ingest_report(self, slug: str, *, path: str | None = None, title: str | None = None,
                      text: str | None = None, sources: list[dict] | None = None,
                      created_at: str | None = None, meta: dict | None = None) -> int:
        # sources: [{"url": str, "title": str|None, "quote": str|None}, ...]
        # word_count = len(text.split()) if text else None; citation_count = len(sources or []).
        # re-ingest same slug: replace sources; update report row (reports has UNIQUE slug).
        # records event "report_ingested". returns reports.id.

    def search(self, query: str, *, limit: int = 10, mode: str = "fts",
               query_vector: list[float] | None = None, snippet_len: int = 200) -> list[dict]:
        # mode="fts": FTS5 MATCH, ORDER BY rank. mode="hybrid": requires query_vector else SearchStoreError.
        # hybrid = RRF fuse(f FTS top-50, vector top-50): score = sum(1/(60+rank_1based)).
        # returns [{"doc_id", "url", "title", "provider", "fetched_at", "snippet", "score", "mode_used"}]
        # score: higher = better. fts score = -bm25_rank. snippet via FTS5 snippet() with <b>…</b>.
        # no matches -> []. Malformed MATCH query -> SearchStoreError (do not crash).

    def get_document(self, doc_id: int) -> dict | None: ...
    def record_event(self, kind: str, payload: dict | None = None) -> int: ...

    def stats(self) -> dict:
        # keys: db_path, db_bytes, schema_version, documents, urls (distinct url_key),
        # searches, search_results, reports, report_sources, events, embeddings,
        # vector_tier, fts (bool), wal (bool), first_fetched_at, last_fetched_at

    def export(self, table: str, out_path: str, *, format: str = "jsonl") -> int:
        # table in {documents, searches, search_results, reports, report_sources, events}
        # jsonl: one compact JSON per row (ensure_ascii=False). returns rows written.
        # format="md" valid ONLY for table="reports" (one <slug>.md per report into out_path dir).

    def rebuild_fts(self) -> int:  # INSERT INTO documents_fts(documents_fts) VALUES('rebuild'); returns count

    def add_embedding(self, doc_id: int, vector: list[float], *, model: str = "unknown") -> None:
        # via vectors.add_embedding; INSERT OR REPLACE by doc_id; event "embedding_added".
    def similar(self, vector: list[float], *, k: int = 10) -> list[dict]:
        # [{"doc_id", "score"}] cosine similarity desc, via vectors.similar.
```

## 4. CLI — EXACT (R3-B implements)

```
python -m searchstore [--db PATH] <command> [...]
--db default: ./searchstore.db      global --json accepted per command below.

init [--json]                                  -> create+migrate; JSON: {"ok":true,"db":...,"schema_version":1}
ingest-doc --url U (--file F | --text T) [--title T] [--provider P] [--fetched-at ISO] [--json]
ingest-battery PATH [--json]                   -> adapter -> searches+events
ingest-keyless PATH [--json]                   -> adapter -> events only
ingest-report --slug S (--file F | --text T) [--title T] [--sources JSONFILE] [--json]
search QUERY [--limit N] [--mode {fts,hybrid}] [--query-vector JSONFILE] [--json]
stats [--json]
export TABLE --out PATH [--format {jsonl,md}]
rebuild-fts [--json]

exit codes: 0 ok · 1 SearchStoreError/data error · 2 usage/IO (missing file, argparse, bad args)
human output: compact lines; --json output: single JSON object on stdout.
```

## 5. Adapters — EXACT (R3-B implements; shapes frozen from real files in `results/`)

```python
def load_battery_json(path) -> dict:
    # {"kind":"battery", "generated":str, "searches":[{"query","provider","ts","latency_ms",
    #   "result_count","meta":{"case_id","pass","backends"}}],
    #  "events":[{"kind":"battery_case","payload":{case fields}}], "meta":{...}}
    # searches from cases where kind=="search": query=case["input"]; provider parsed from evidence
    #   line `Web search via <name>:` if present else None; ts=file generated; latency_ms=round(latency_s*1000).
    # events: one per case (id, kind, input, pass, latency_s, n_results, notes, error).

def load_keyless_json(path) -> dict:
    # {"kind":"keyless", "generated":str, "searches":[], "events":[{"kind":"keyless_case",...}]}
    # (queries not recoverable from keyless files -> events only)

def load_report_md(path, sources=None) -> dict:
    # {"kind":"report","slug":<stem>,"title":first "# " line or stem,"text":full md,
    #  "sources": sources or [], "created_at": file mtime ISO, "meta":{"path":...}}
    # sources: list of {"url","title"?,"quote"?} (caller-loaded JSON array).
```

`ingest-battery` CLI flow: load → `store.ingest_search` per search (if any) → `record_event` per event.

## 6. Vector tier — EXACT (R3-B implements in vectors.py)

```python
def tier_available() -> str   # "sqlite-vec" | "numpy" | "python"   (python = pure-stdlib cosine, ALWAYS available)
def add_embedding(conn, doc_id, vector, model="unknown") -> None
def similar(conn, vector, k=10) -> list[dict]   # cosine desc; empty table -> []
def embedding_count(conn) -> int
```
Priority: sqlite_vec (if importable AND loadable on this connection) → numpy → pure python.
Vector stored as BLOB (struct.pack('<f'*n) / numpy tobytes). dimension from len(vector); validate
finite, non-empty else SearchStoreError. Same doc_id replaces.

## 7. Acceptance per agent

- R3-A: `tests/test_searchstore_core.py` — ≥18 behaviors: migrate idempotent (2× same result), WAL on,
  dedup returns same id, same URL new content = 2 rows + documents_current returns latest,
  FTS finds VN with/without diacritics (`Yên Dũng` & `dung`), phrase + boolean, snippet has `<b>`,
  ingest_search stores positions+count, ingest_report sources replace + citation_count,
  search no-match [], malformed query -> SearchStoreError, export jsonl rows == count,
  stats keys present, events recorded for ingest, kv works, rebuild_fts, close/context manager,
  url_key normalization cases, get_document None.
- R3-B: cli tests (init/search/stats/export/rebuild via `main(argv)` in-process — no subprocess needed;
  exit codes 0/1/2 for: ok, SearchStoreError (e.g. bad db path is 2; malformed fts query is 1), missing file 2);
  adapters tests against fixtures copied from `results/` (battery: ≥3 searches parsed + events;
  keyless: events only; report: title/word_count/sources); vectors: python tier always works,
  roundtrip cosine ordering, replace doc_id, empty -> [].
- R3-C: README (quickstart CLI + API, schema table, 3 extension recipes: new table via migration,
  new adapter, vector/hybrid + LanceDB upgrade path); misc tests: unicode emoji + CJK text roundtrip,
  empty db stats, 100KB text insert+FTS, rebuild-fts after manual FTS delete, concurrent 2 readers WAL.

## 8. Agent report format (append to your log; also keep files)

```
DONE / PARTIAL / BLOCKED
Files: <created/changed>
Tests: <command> -> <X passed, exit code>
ruff: check=clean format=clean
Evidence: <2-3 concrete lines: command + observed output>
Notes: <deviations, if any>
```
