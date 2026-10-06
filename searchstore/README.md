# SearchStore v1 — single-file SQLite storage for the search stack

What it is: one SQLite file holding the search stack's documents (versioned,
FTS5-indexed), searches + results, reports + sources, an append-only event
audit log, a kv table, and an optional vector tier. Stdlib-only core.
Contract: `analysis/round3-interfaces.md` (frozen 2026-10-06).

Quickstart (3 lines):

```sh
python -m searchstore init
python -m searchstore ingest-battery results/battery-2026-10-06.json
python -m searchstore search "Yên Dũng"
```

## Requirements

- Python 3.11+, stdlib-only core (`sqlite3`, `argparse`, `json`, `hashlib`,
  `os`, `pathlib`, `datetime`, `re`, `math`, `sys`, `contextlib`).
- Optional vector tiers (`sqlite-vec` / `numpy`) are imported lazily inside
  `try/except ImportError` and installable later via `uv` — the zero-dependency
  default (pure-Python cosine fallback) works out of the box.
- No network, no server, no config edits. Tests are hermetic (`tmp_path`).

## Schema overview (v1, `SCHEMA_VERSION = 1`)

- `documents` — versioned docs: `url`, `url_key` (normalized), `content_sha256`, `title`, `provider`, `format`, `fetched_at`, `char_count`, `text`, `meta`; `UNIQUE(url_key, content_sha256)`.
- `documents_fts` — FTS5 virtual table (`title`, `text`, `tokenize='unicode61 remove_diacritics 2'`) kept in sync by `documents_ai/ad/au` triggers.
- `searches` — one row per query: `query`, `provider`, `engine` (default `'web_search'`), `ts`, `latency_ms`, `result_count`, `meta`.
- `search_results` — rows per hit: `search_id → searches(id)`, `position` (0-based), `title`, `url`, `snippet`.
- `reports` — one row per slug: `slug UNIQUE`, `title`, `path`, `created_at`, `word_count`, `citation_count`, `meta`.
- `report_sources` — citations per report: `report_id → reports(id)`, `url`, `title`, `quote`, `position`.
- `events` — append-only audit log: `ts`, `kind`, `payload` (JSON); migrations record `kind="migration_applied"`.
- `kv` — string key-value store: `key PRIMARY KEY`, `value`.
- `embeddings` — optional vectors: `doc_id PRIMARY KEY → documents(id)`, `model`, `dim`, `vector BLOB`, `created_at`.
- `documents_current` view — latest version per URL: `WHERE id = (SELECT MAX(id) ... WHERE url_key = d.url_key)`.

Invariants: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`,
`synchronous=NORMAL`. Migrations are idempotent; `PRAGMA user_version` tracks
the highest applied version.

## Python API quickref

```python
from searchstore import SearchStore

store = SearchStore("searchstore.db")
doc_id = store.ingest_document("https://example.com", "hello world", title="Hi")
hits = store.search("hello")
print(store.stats()["documents"])
```

Signatures (frozen, see contract §3): `url_key(url)`, `content_sha256(text)`,
`SearchStore(db_path, *, create=True)` (+ `conn`, `close()`, context manager),
`ingest_document(url, text, *, title, provider, fetched_at, format, meta)`,
`ingest_search(query, results, *, provider, engine, ts, latency_ms, meta)`,
`ingest_report(slug, *, path, title, text, sources, created_at, meta)`,
`search(query, *, limit=10, mode="fts", query_vector, snippet_len=200)`,
`get_document(doc_id)`, `record_event(kind, payload)`, `stats()`,
`export(table, out_path, *, format="jsonl")`, `rebuild_fts()`,
`add_embedding(doc_id, vector, *, model)`, `similar(vector, *, k=10)`.

Notes: dedup on `(url_key, sha256)` returns the existing id with no new row or
event; new content for the same URL appends a new row (see
`documents_current`); `mode="hybrid"` requires `query_vector` and fuses
FTS top-50 + vector top-50 with RRF (`1/(60+rank)`); malformed `MATCH` queries
raise `SearchStoreError`; `export(format="md")` is valid only for `reports`.

## CLI reference

```sh
python -m searchstore [--db PATH] <command> [...]
```

`--db` default: `./searchstore.db`. `--json` prints a single JSON object on stdout;
otherwise output is compact human-readable lines.

| Command | Purpose |
|---|---|
| `init [--json]` | Create + migrate the DB (JSON: `{"ok":true,"db":...,"schema_version":1}`). |
| `ingest-doc --url U (--file F \| --text T) [--title T] [--provider P] [--fetched-at ISO] [--json]` | Ingest one document. |
| `ingest-battery PATH [--json]` | Load battery JSON (adapter) → `ingest_search` per search + `record_event` per event. |
| `ingest-keyless PATH [--json]` | Load keyless JSON (adapter) → events only (queries not recoverable). |
| `ingest-report --slug S (--file F \| --text T) [--title T] [--sources JSONFILE] [--json]` | Ingest (or replace) a report + its sources. |
| `search QUERY [--limit N] [--mode {fts,hybrid}] [--query-vector JSONFILE] [--json]` | FTS5 (BM25) search; hybrid needs `--query-vector`. |
| `stats [--json]` | Counts + `schema_version`, `urls`, `vector_tier`, `fts`, `wal`, `first/last_fetched_at`. |
| `export TABLE --out PATH [--format {jsonl,md}]` | `TABLE` in `{documents, searches, search_results, reports, report_sources, events}`; `md` only for `reports`. |
| `rebuild-fts [--json]` | Rebuild the FTS index; returns the document count. |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | `SearchStoreError` / data error (e.g. malformed FTS query, bad hybrid usage) |
| 2 | Usage / IO error (argparse, missing file, bad args, missing DB with `create=False`) |

## Extension guide — 3 recipes

### (a) New table or column → append migration N+1 in `db.py`

1. Append a `_MIGRATION_2` SQL string (must be idempotent: `CREATE TABLE IF NOT EXISTS ...`, `CREATE INDEX IF NOT EXISTS ...`).
2. Append it to `_MIGRATIONS`; `migrate()` applies each entry where `len(applied) < idx`, sets `PRAGMA user_version`, and records `kind="migration_applied"`.
3. Bump `SCHEMA_VERSION`. Never edit migration 1 — history is append-only.

### (b) New ingest source → adapter fn returning the §5 shape + CLI subcommand

1. Add `load_<source>_json(path)` in `adapters.py` returning the frozen envelope: `{"kind": ..., "generated": ..., "searches": [...], "events": [...], "meta": {...}}` with searches as `{"query", "provider", "ts", "latency_ms", "result_count", "meta"}` and events as `{"kind", "payload"}` (see contract §5).
2. Add a CLI subcommand (e.g. `ingest-<source> PATH [--json]`) following the `ingest-battery` flow: load → `store.ingest_search` per search (if any) → `store.record_event` per event.
3. Keep parsing pure-stdlib and hermetic; add fixtures under `tests/fixtures/searchstore/`.

### (c) Scale path → >50k vectors or typo-search: LanceDB / Meilisearch sidecar; API stays the same

When the corpus passes ~50k vectors (where `sqlite-vec` brute-force degrades) or
you need typo-tolerant instant search, add a sidecar per
`analysis/storage-modern.md` §2–§3: **LanceDB** (embedded ANN + native
BM25+vector+RRF hybrid, Tantivy) for vectors, **Meilisearch** for the search-bar
typo-tolerant tier. Keep the `SearchStore` API unchanged — implement the new
backend behind the existing adapter seam (`add_embedding` / `similar` /
`search(mode="hybrid")`) and sync via `export(..., format="jsonl")`.

## Design principles

- **Local-first single-file**: one `.db`, zero servers, zero ops; upgrades never change the API.
- **Content-addressed dedup**: `(url_key, content_sha256)` — same bytes ingested twice return the same id.
- **Append-only versions**: new content for the same URL is a new row; `documents_current` exposes the latest per URL.
- **Events audit**: every ingest/migration/embedding appends one row to `events`.
- **WAL**: `journal_mode=WAL` (+ `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`) so readers never block writers.
