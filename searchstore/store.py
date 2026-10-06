"""SearchStore: single-file storage layer for the search stack.

SQLite FTS5 (BM25) + append-only versioned documents + events audit log +
optional vector tier (searchstore.vectors, imported lazily). See
analysis/round3-interfaces.md section 3 for the frozen API contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from types import ModuleType

from . import db


class SearchStoreError(Exception):
    """All library errors. Message must be actionable."""


_URL_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)(.*)$", re.DOTALL)


def url_key(url: str) -> str:
    """Normalize a URL for dedup: lowercase scheme+host, strip fragment,
    strip trailing '/', keep query."""
    u = str(url).strip().split("#", 1)[0]
    m = _URL_RE.match(u)
    if m:
        scheme, authority, rest = m.groups()
        u = f"{scheme.lower()}://{authority.lower()}{rest}"
    if "?" in u:
        base, query = u.split("?", 1)
        return base.rstrip("/") + "?" + query
    return u.rstrip("/")


def content_sha256(text: str) -> str:
    """Hex SHA-256 of the UTF-8 encoding of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(obj: dict | None) -> str:
    return json.dumps(obj if obj is not None else {}, ensure_ascii=False)


_FTS_SQL = """
SELECT d.id AS doc_id, d.url, d.title, d.provider, d.fetched_at,
       snippet(documents_fts, 1, '<b>', '</b>', '…', ?) AS snippet,
       bm25(documents_fts) AS rank
FROM documents_fts
JOIN documents d ON d.id = documents_fts.rowid
WHERE documents_fts MATCH ?
ORDER BY bm25(documents_fts)
LIMIT ?
"""

_EXPORT_TABLES = frozenset({"documents", "searches", "search_results", "reports", "report_sources", "events"})

_STATS_TABLES = ("documents", "searches", "search_results", "reports", "report_sources", "events", "embeddings")

# Literal SQL maps — table names never interpolate into SQL strings (bandit B608 clean).
_STATS_COUNT_SQL = {
    "documents": "SELECT COUNT(*) FROM documents",
    "searches": "SELECT COUNT(*) FROM searches",
    "search_results": "SELECT COUNT(*) FROM search_results",
    "reports": "SELECT COUNT(*) FROM reports",
    "report_sources": "SELECT COUNT(*) FROM report_sources",
    "events": "SELECT COUNT(*) FROM events",
    "embeddings": "SELECT COUNT(*) FROM embeddings",
}

_EXPORT_SELECT_SQL = {
    "documents": "SELECT * FROM documents ORDER BY id",
    "searches": "SELECT * FROM searches ORDER BY id",
    "search_results": "SELECT * FROM search_results ORDER BY id",
    "reports": "SELECT * FROM reports ORDER BY id",
    "report_sources": "SELECT * FROM report_sources ORDER BY id",
    "events": "SELECT * FROM events ORDER BY id",
}


def _load_vectors() -> ModuleType:
    try:
        from . import vectors
    except ImportError as e:
        raise SearchStoreError("vectors module not available") from e
    return vectors


class SearchStore:
    def __init__(self, db_path: str | os.PathLike, *, create: bool = True):
        self._db_path = os.fspath(db_path)
        if not create and not os.path.exists(self._db_path):
            raise FileNotFoundError(f"database not found: {self._db_path} (pass create=True to create it)")
        if create:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = db.connect(self._db_path)
        db.migrate(self._conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SearchStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- ingest ----------

    def ingest_document(
        self,
        url: str,
        text: str,
        *,
        title: str | None = None,
        provider: str | None = None,
        fetched_at: str | None = None,
        format: str = "markdown",
        meta: dict | None = None,
    ) -> int:
        key = url_key(url)
        sha = content_sha256(text)
        row = self._conn.execute(
            "SELECT id FROM documents WHERE url_key = ? AND content_sha256 = ?", (key, sha)
        ).fetchone()
        if row is not None:
            return row["id"]
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO documents(url, url_key, content_sha256, title, provider, format,"
                " fetched_at, char_count, text, meta) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (url, key, sha, title, provider, format, fetched_at or _iso_now(), len(text), text, _json(meta)),
            )
            self._record_event(
                "document_ingested",
                {"doc_id": cur.lastrowid, "url": url, "url_key": key, "content_sha256": sha},
            )
            return cur.lastrowid

    def ingest_search(
        self,
        query: str,
        results: list[dict],
        *,
        provider: str | None = None,
        engine: str = "web_search",
        ts: str | None = None,
        latency_ms: int | None = None,
        meta: dict | None = None,
    ) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO searches(query, provider, engine, ts, latency_ms, result_count, meta)"
                " VALUES(?,?,?,?,?,?,?)",
                (query, provider, engine, ts or _iso_now(), latency_ms, len(results), _json(meta)),
            )
            sid = cur.lastrowid
            for i, r in enumerate(results):
                if not r.get("url"):
                    raise SearchStoreError(f"search result at position {i} is missing required 'url'")
                self._conn.execute(
                    "INSERT INTO search_results(search_id, position, title, url, snippet) VALUES(?,?,?,?,?)",
                    (sid, i, r.get("title"), r["url"], r.get("snippet")),
                )
            self._record_event("search_ingested", {"search_id": sid, "query": query, "result_count": len(results)})
            return sid

    def ingest_report(
        self,
        slug: str,
        *,
        path: str | None = None,
        title: str | None = None,
        text: str | None = None,
        sources: list[dict] | None = None,
        created_at: str | None = None,
        meta: dict | None = None,
    ) -> int:
        sources = sources or []
        vals = (
            title,
            path,
            created_at or _iso_now(),
            len(text.split()) if text else None,
            len(sources),
            _json(meta),
        )
        with self._conn:
            row = self._conn.execute("SELECT id FROM reports WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                cur = self._conn.execute(
                    "INSERT INTO reports(slug, title, path, created_at, word_count, citation_count, meta)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (slug, *vals),
                )
                rid = cur.lastrowid
            else:
                rid = row["id"]
                self._conn.execute(
                    "UPDATE reports SET title=?, path=?, created_at=?, word_count=?, citation_count=?, meta=?"
                    " WHERE id=?",
                    (*vals, rid),
                )
                self._conn.execute("DELETE FROM report_sources WHERE report_id = ?", (rid,))
            for i, s in enumerate(sources):
                if not s.get("url"):
                    raise SearchStoreError(f"report source at position {i} is missing required 'url'")
                self._conn.execute(
                    "INSERT INTO report_sources(report_id, url, title, quote, position) VALUES(?,?,?,?,?)",
                    (rid, s["url"], s.get("title"), s.get("quote"), i),
                )
            self._record_event("report_ingested", {"report_id": rid, "slug": slug, "citation_count": len(sources)})
            return rid

    # ---------- search ----------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: str = "fts",
        query_vector: list[float] | None = None,
        snippet_len: int = 200,
    ) -> list[dict]:
        if mode == "fts":
            return self._fts_search(query, limit=limit, snippet_len=snippet_len, mode_used="fts")
        if mode == "hybrid":
            if query_vector is None:
                raise SearchStoreError("mode='hybrid' requires query_vector (list of floats)")
            vectors = _load_vectors()
            fts_hits = self._fts_search(query, limit=50, snippet_len=snippet_len, mode_used="hybrid")
            vec_hits = vectors.similar(self._conn, query_vector, k=50)
            rrf: dict[int, float] = {}
            for rank, hit in enumerate(fts_hits, start=1):
                rrf[hit["doc_id"]] = rrf.get(hit["doc_id"], 0.0) + 1.0 / (60 + rank)
            for rank, hit in enumerate(vec_hits, start=1):
                rrf[hit["doc_id"]] = rrf.get(hit["doc_id"], 0.0) + 1.0 / (60 + rank)
            by_id = {h["doc_id"]: h for h in fts_hits}
            out = []
            for doc_id, score in sorted(rrf.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]:
                hit = by_id.get(doc_id)
                if hit is None:
                    doc = self.get_document(doc_id)
                    if doc is None:
                        continue
                    hit = {
                        "doc_id": doc_id,
                        "url": doc["url"],
                        "title": doc["title"],
                        "provider": doc["provider"],
                        "fetched_at": doc["fetched_at"],
                        "snippet": doc["text"][:snippet_len],
                    }
                out.append(dict(hit, score=score, mode_used="hybrid"))
            return out
        raise SearchStoreError(f"unknown search mode {mode!r}; use 'fts' or 'hybrid'")

    def _fts_search(self, query: str, *, limit: int, snippet_len: int, mode_used: str) -> list[dict]:
        try:
            rows = self._conn.execute(_FTS_SQL, (snippet_len, query, limit)).fetchall()
        except sqlite3.Error as e:
            raise SearchStoreError(f"invalid FTS query {query!r}: {e}") from e
        return [
            {
                "doc_id": r["doc_id"],
                "url": r["url"],
                "title": r["title"],
                "provider": r["provider"],
                "fetched_at": r["fetched_at"],
                "snippet": r["snippet"],
                "score": -r["rank"],
                "mode_used": mode_used,
            }
            for r in rows
        ]

    # ---------- misc ----------

    def get_document(self, doc_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row is not None else None

    def record_event(self, kind: str, payload: dict | None = None) -> int:
        with self._conn:
            return self._record_event(kind, payload)

    def _record_event(self, kind: str, payload: dict | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO events(ts, kind, payload) VALUES(?,?,?)", (_iso_now(), kind, _json(payload))
        )
        return cur.lastrowid

    def stats(self) -> dict:
        c = self._conn
        out = {t: c.execute(_STATS_COUNT_SQL[t]).fetchone()[0] for t in _STATS_TABLES}
        try:
            tier = _load_vectors().tier_available()
        except SearchStoreError:
            tier = "unavailable"
        out.update(
            {
                "db_path": self._db_path,
                "db_bytes": os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0,
                "schema_version": c.execute("PRAGMA user_version").fetchone()[0],
                "urls": c.execute("SELECT COUNT(DISTINCT url_key) FROM documents").fetchone()[0],
                "vector_tier": tier,
                "fts": bool(
                    c.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='documents_fts'"
                    ).fetchone()[0]
                ),
                "wal": c.execute("PRAGMA journal_mode").fetchone()[0] == "wal",
                "first_fetched_at": c.execute("SELECT MIN(fetched_at) FROM documents").fetchone()[0],
                "last_fetched_at": c.execute("SELECT MAX(fetched_at) FROM documents").fetchone()[0],
            }
        )
        return out

    def export(self, table: str, out_path: str, *, format: str = "jsonl") -> int:
        if table not in _EXPORT_TABLES:
            raise SearchStoreError(f"cannot export {table!r}; allowed tables: {sorted(_EXPORT_TABLES)}")
        if format == "jsonl":
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            rows = self._conn.execute(_EXPORT_SELECT_SQL[table]).fetchall()
            with open(out_path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
            return len(rows)
        if format == "md":
            if table != "reports":
                raise SearchStoreError("format='md' is only valid for table='reports'")
            os.makedirs(out_path, exist_ok=True)
            rows = self._conn.execute("SELECT * FROM reports ORDER BY id").fetchall()
            for r in rows:
                sources = self._conn.execute(
                    "SELECT url, title, quote FROM report_sources WHERE report_id = ? ORDER BY position",
                    (r["id"],),
                ).fetchall()
                lines = [f"# {r['title'] or r['slug']}", ""]
                for s in sources:
                    line = f"- [{s['title'] or s['url']}]({s['url']})"
                    if s["quote"]:
                        line += f" — {s['quote']}"
                    lines.append(line)
                Path(out_path, f"{r['slug']}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return len(rows)
        raise SearchStoreError(f"unknown export format {format!r}; use 'jsonl' or 'md'")

    def rebuild_fts(self) -> int:
        with self._conn:
            self._conn.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
        return self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    # ---------- vector tier (lazy: searchstore.vectors is optional) ----------

    def add_embedding(self, doc_id: int, vector: list[float], *, model: str = "unknown") -> None:
        vectors = _load_vectors()
        with self._conn:
            vectors.add_embedding(self._conn, doc_id, vector, model=model)
            self._record_event("embedding_added", {"doc_id": doc_id, "model": model, "dim": len(vector)})

    def similar(self, vector: list[float], *, k: int = 10) -> list[dict]:
        return _load_vectors().similar(self._conn, vector, k=k)
