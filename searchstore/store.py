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


def _fold_d(text: str) -> str:
    """Fold đ/Đ → d for FTS queries. Mirrors the SQL nested ``replace()`` in
    the documents_fts triggers / FTS_BACKFILL_SQL exactly
    (analysis/r9-interfaces.md §D)."""
    return text.replace("đ", "d").replace("Đ", "d")


fold_d = _fold_d


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
        ensure_entity_schema(self._conn)  # R13-A hook: entities view on every DB

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
            rows = self._conn.execute(_FTS_SQL, (snippet_len, _fold_d(query), limit)).fetchall()
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
            self._conn.execute("DELETE FROM documents_fts")
            self._conn.execute(db.FTS_BACKFILL_SQL)
        return self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    # ---------- vector tier (lazy: searchstore.vectors is optional) ----------

    def add_embedding(self, doc_id: int, vector: list[float], *, model: str = "unknown") -> None:
        vectors = _load_vectors()
        with self._conn:
            vectors.add_embedding(self._conn, doc_id, vector, model=model)
            self._record_event("embedding_added", {"doc_id": doc_id, "model": model, "dim": len(vector)})

    def similar(self, vector: list[float], *, k: int = 10) -> list[dict]:
        return _load_vectors().similar(self._conn, vector, k=k)


# ===========================================================================
# Entity helpers — appended for R13-A (analysis/r13-interfaces.md §2).
#
# Entities are ordinary documents: format="entity", provider=entity source,
# title=name, meta=the full entity schema v1 dict (JSON). The ``entities``
# view exposes the schema fields as columns over documents_current. Dedupe
# rules per §2: exact on (source, source_id) / deterministic entity_id, then
# fuzzy name+address token overlap >= 0.8. All FTS goes through fold_d
# (R9-W2B). vn_geo.business wraps these helpers behind db_path signatures.
# ===========================================================================

ENTITY_FORMAT = "entity"
ENTITY_URL_PREFIX = "vn://entity/"
ENTITY_DIFF_WATERMARK_KEY = "entity_diff_watermark"
ENTITY_FUZZY_THRESHOLD = 0.8
ENTITY_DEFAULT_TTL_CLASS = "poi"

# ttl_class -> seconds before checked_at goes stale (freshness gate,
# analysis/r13-interfaces.md §5: stale rows are flagged, never silently served).
ENTITY_TTL_SECONDS = {
    "poi": 7 * 86400,
    "poi_strict": 3 * 86400,
    "registry": 30 * 86400,
    "dynamic": 3600,
}

# Content fields compared on re-upsert to decide "changed". Bookkeeping fields
# (entity_id, first_seen, last_seen, checked_at, raw, sources, geocode_source)
# are excluded — they never make a new version by themselves.
ENTITY_DIFF_FIELDS = (
    "name",
    "kind",
    "category",
    "category_raw",
    "cat_confidence",
    "tax_code",
    "address_text",
    "area_old",
    "province",
    "lat",
    "lng",
    "phone",
    "website",
    "source_url",
    "source_id",
    "status",
    "rating",
    "review_count",
    "geocode_status",
    "ttl_class",
    "confidence",
)

_ENTITIES_VIEW_SQL = """\
CREATE VIEW IF NOT EXISTS entities AS
SELECT
  d.id          AS doc_id,
  d.url         AS url,
  d.title       AS title,
  d.provider    AS provider,
  d.fetched_at  AS fetched_at,
  json_extract(d.meta, '$.entity_id')      AS entity_id,
  json_extract(d.meta, '$.name')           AS name,
  json_extract(d.meta, '$.kind')           AS kind,
  json_extract(d.meta, '$.category')       AS category,
  json_extract(d.meta, '$.category_raw')   AS category_raw,
  json_extract(d.meta, '$.cat_confidence') AS cat_confidence,
  json_extract(d.meta, '$.tax_code')       AS tax_code,
  json_extract(d.meta, '$.address_text')   AS address_text,
  json_extract(d.meta, '$.area_old')       AS area_old,
  json_extract(d.meta, '$.province')       AS province,
  json_extract(d.meta, '$.lat')            AS lat,
  json_extract(d.meta, '$.lng')            AS lng,
  json_extract(d.meta, '$.phone')          AS phone,
  json_extract(d.meta, '$.website')        AS website,
  json_extract(d.meta, '$.source_url')     AS source_url,
  json_extract(d.meta, '$.source_id')      AS source_id,
  json_extract(d.meta, '$.status')         AS status,
  json_extract(d.meta, '$.rating')         AS rating,
  json_extract(d.meta, '$.review_count')   AS review_count,
  json_extract(d.meta, '$.first_seen')     AS first_seen,
  json_extract(d.meta, '$.last_seen')      AS last_seen,
  json_extract(d.meta, '$.checked_at')     AS checked_at,
  json_extract(d.meta, '$.ttl_class')      AS ttl_class,
  json_extract(d.meta, '$.confidence')     AS confidence,
  json_extract(d.meta, '$.geocode_status') AS geocode_status,
  json_extract(d.meta, '$.geocode_source') AS geocode_source,
  d.meta        AS meta
FROM documents_current d
WHERE d.format = 'entity'
"""

_ENTITY_FTS_SQL = """\
SELECT d.meta AS meta
FROM documents_fts
JOIN documents_current d ON d.id = documents_fts.rowid
WHERE documents_fts MATCH ? AND d.format = 'entity'
ORDER BY bm25(documents_fts)
"""

_ENTITY_ALL_SQL = "SELECT meta FROM documents_current WHERE format = 'entity' ORDER BY title"

_TOKEN_RE = re.compile(r"[0-9a-z]+")


def ensure_entity_schema(conn: sqlite3.Connection) -> None:
    """Create the ``entities`` view when absent. Idempotent; called from
    ``SearchStore.__init__`` (import-safe hook) and again inside the entity
    helpers so direct-connection callers are covered too."""
    conn.execute(_ENTITIES_VIEW_SQL)


def _entity_fold(text: str) -> str:
    """Unaccent fold for dedupe tokens: đ/Đ -> d, diacritic strip, casefold,
    whitespace collapse (superset of _fold_d)."""
    import unicodedata

    s = _fold_d(str(text))
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).casefold().strip()


def entity_id_for(
    source: str,
    source_id: str | None = None,
    name: str | None = None,
    address: str | None = None,
) -> str:
    """Deterministic ``e_<sha1_12>`` id (§2): ``source|source_id`` when a
    source id exists, else ``source|folded name|folded address``."""
    src = _entity_fold(source)
    sid = _entity_fold(source_id or "")
    if sid:
        basis = f"{src}|{sid}"
    else:
        basis = f"{src}|{_entity_fold(name or '')}|{_entity_fold(address or '')}"
    return "e_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def entity_token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of folded token sets; §2 fuzzy-dedupe metric."""
    ta = set(_TOKEN_RE.findall(_entity_fold(a)))
    tb = set(_TOKEN_RE.findall(_entity_fold(b)))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _entity_doc_url(entity_id: str) -> str:
    return ENTITY_URL_PREFIX + entity_id


def _entity_doc_text(entity: dict) -> str:
    """Searchable text for an entity doc: name/categories/address/contact."""
    parts = [
        str(entity.get(k) or "").strip()
        for k in ("name", "category_raw", "category", "address_text", "area_old", "province", "phone", "tax_code")
    ]
    parts = [p for p in parts if p]
    return (". ".join(parts) + ".") if parts else ""


def _entity_meta(row) -> dict | None:
    try:
        meta = json.loads(row["meta"] or "{}")
    except json.JSONDecodeError:
        return None
    return meta if isinstance(meta, dict) else None


def _iter_entity_docs(conn: sqlite3.Connection):
    for row in conn.execute("SELECT id, meta FROM documents_current WHERE format = ?", (ENTITY_FORMAT,)):
        meta = _entity_meta(row)
        if meta is not None:
            yield row["id"], meta


def find_entity(conn: sqlite3.Connection, entity: dict) -> tuple[int, dict] | None:
    """Locate the current doc matching *entity* per §2 dedupe rules.

    Exact wins: identical entity_id, or identical (source, source_id) with a
    non-empty source_id. Otherwise fuzzy name+address token overlap >= 0.8,
    best score wins. Returns ``(doc_id, meta)`` or None.
    """
    entity_id = str(entity.get("entity_id") or "")
    source = str(entity.get("source") or "")
    source_id = str(entity.get("source_id") or "")
    docs = list(_iter_entity_docs(conn))
    for doc_id, meta in docs:
        if entity_id and str(meta.get("entity_id") or "") == entity_id:
            return doc_id, meta
        if source_id and meta.get("source") == source and str(meta.get("source_id") or "") == source_id:
            return doc_id, meta
    target = f"{entity.get('name') or ''} {entity.get('address_text') or ''}"
    best: tuple[int, dict] | None = None
    best_score = 0.0
    for doc_id, meta in docs:
        score = entity_token_overlap(target, f"{meta.get('name') or ''} {meta.get('address_text') or ''}")
        if score >= ENTITY_FUZZY_THRESHOLD and (best is None or score > best_score):
            best = (doc_id, meta)
            best_score = score
    return best


def _merge_entity_sources(meta: dict, entity: dict) -> list[str]:
    seen: list[str] = []
    for s in (meta.get("source"), entity.get("source"), *(meta.get("sources") or [])):
        if s and s not in seen:
            seen.append(str(s))
    return seen


def entity_upsert(store: SearchStore, entity: dict) -> str:
    """Dedupe-upsert an entity schema v1 dict into *store*; return entity_id.

    - No match  -> insert doc (``vn://entity/<id>``) + ``entity_new`` event.
    - Match + changed content fields -> new document version (append-only) +
      ``entity_changed`` event, or ``entity_closed`` when status transitioned
      to closed. first_seen is kept; last_seen/checked_at are bumped.
    - Match + no content change -> last_seen/checked_at bumped in place on the
      current row (+0 duplicate rows, no event).
    Incoming ``None``/``""`` values never downgrade existing fields.
    """
    conn = store.conn
    ensure_entity_schema(conn)
    ent = dict(entity)
    source = str(ent.get("source") or "").strip()
    if not source:
        raise SearchStoreError("entity_upsert: entity['source'] is required")
    name = str(ent.get("name") or "").strip()
    if not name:
        raise SearchStoreError("entity_upsert: entity['name'] is required")
    if not str(ent.get("entity_id") or "").strip():
        ent["entity_id"] = entity_id_for(
            source, str(ent.get("source_id") or ""), name, str(ent.get("address_text") or "")
        )
    now = _iso_now()
    for field in ("first_seen", "last_seen", "checked_at"):
        if not ent.get(field):
            ent[field] = now
    hit = find_entity(conn, ent)
    if hit is None:
        ent.setdefault("sources", [source])
        store.ingest_document(
            _entity_doc_url(ent["entity_id"]),
            _entity_doc_text(ent),
            title=name,
            provider=source,
            format=ENTITY_FORMAT,
            meta=ent,
        )
        store.record_event("entity_new", {"entity_id": ent["entity_id"], "name": name, "source": source})
        return str(ent["entity_id"])

    doc_id, meta = hit
    entity_id = str(meta.get("entity_id") or ent["entity_id"])
    merged = dict(meta)
    changes: dict[str, list] = {}
    for field in ENTITY_DIFF_FIELDS:
        new_v = ent.get(field)
        if new_v is None or new_v == "":
            continue
        if new_v != meta.get(field):
            changes[field] = [meta.get(field), new_v]
            merged[field] = new_v
    if ent.get("raw") is not None:
        merged["raw"] = ent["raw"]
    merged["entity_id"] = entity_id
    merged["first_seen"] = meta.get("first_seen") or ent.get("first_seen") or now
    merged["last_seen"] = now
    merged["checked_at"] = now
    merged["sources"] = _merge_entity_sources(meta, ent)
    if not changes:
        with conn:
            conn.execute("UPDATE documents SET meta = ? WHERE id = ?", (_json(merged), doc_id))
        return entity_id
    store.ingest_document(
        _entity_doc_url(entity_id),
        _entity_doc_text(merged),
        title=str(merged.get("name") or name),
        provider=str(meta.get("source") or source),
        format=ENTITY_FORMAT,
        meta=merged,
    )
    kind = "entity_closed" if merged.get("status") == "closed" and meta.get("status") != "closed" else "entity_changed"
    store.record_event(
        kind,
        {"entity_id": entity_id, "name": merged.get("name"), "source": merged.get("source"), "changes": changes},
    )
    return entity_id


def _entity_area_match(meta: dict, area_q: str) -> bool:
    if not area_q:
        return True
    hay = _entity_fold(" ".join(str(meta.get(k) or "") for k in ("address_text", "area_old", "province")))
    return _entity_fold(area_q) in hay


def _entity_category_match(meta: dict, cat_q: str) -> bool:
    if not cat_q:
        return True
    q = _entity_fold(cat_q)
    cat = _entity_fold(meta.get("category") or "")
    kind = _entity_fold(meta.get("kind") or "")
    raw = _entity_fold(meta.get("category_raw") or "")
    return q == cat or (q and cat.startswith(q)) or q == kind or (q and q in raw)


def _entity_is_stale(meta: dict, *, now: datetime | None = None) -> bool:
    """Freshness gate (§5): checked_at older than ttl_class -> stale."""
    ttl = ENTITY_TTL_SECONDS.get(
        str(meta.get("ttl_class") or ENTITY_DEFAULT_TTL_CLASS), ENTITY_TTL_SECONDS[ENTITY_DEFAULT_TTL_CLASS]
    )
    checked = str(meta.get("checked_at") or "").strip()
    try:
        checked_dt = datetime.fromisoformat(checked)
    except ValueError:
        return True
    if checked_dt.tzinfo is None:
        checked_dt = checked_dt.astimezone()
    now = now or datetime.now().astimezone()
    return (now - checked_dt).total_seconds() > ttl


def entity_query(
    conn: sqlite3.Connection,
    text: str = "",
    *,
    area: str = "",
    category: str = "",
    limit: int = 20,
) -> list[dict]:
    """FTS + meta-filter entity query; returns entity meta dicts.

    *text* is folded through ``fold_d`` before MATCH (R9-W2B hard rule: the
    index pre-folds đ/Đ -> d, so an unfolded accented query can return 0
    hits). *area* is an unaccent-folded substring match over
    address_text/area_old/province; *category* matches the canonical cat,
    its kind, or the raw category. Every result carries a ``stale`` flag
    (freshness gate — stale rows are flagged, never silently dropped).
    """
    ensure_entity_schema(conn)
    text = str(text or "").strip()
    if text:
        # Standalone đ-folding index (analysis/r9-interfaces.md §D): fold query text via fold_d.
        try:
            rows = conn.execute(_ENTITY_FTS_SQL, (_fold_d(text),)).fetchall()
        except sqlite3.Error as e:
            raise SearchStoreError(f"invalid entity query text {text!r}: {e}") from e
    else:
        rows = conn.execute(_ENTITY_ALL_SQL).fetchall()
    out: list[dict] = []
    for row in rows:
        meta = _entity_meta(row)
        if meta is None:
            continue
        if not _entity_area_match(meta, str(area or "")):
            continue
        if not _entity_category_match(meta, str(category or "")):
            continue
        meta["stale"] = _entity_is_stale(meta)
        out.append(meta)
        if len(out) >= limit:
            break
    return out


def entity_events(conn: sqlite3.Connection) -> list[dict]:
    """Entity diff events (``entity_*`` kinds) since the previous call.

    A ``kv`` watermark tracks consumption: each call returns the
    new/closed/changed events recorded since the last one and advances the
    watermark, so an identical re-run reports zero new events (§3
    ``diff_events``).
    """
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (ENTITY_DIFF_WATERMARK_KEY,)).fetchone()
    watermark = 0
    if row is not None:
        try:
            watermark = int(row["value"])
        except (TypeError, ValueError):
            watermark = 0
    rows = conn.execute(
        "SELECT id, ts, kind, payload FROM events WHERE kind GLOB 'entity_*' AND id > ? ORDER BY id",
        (watermark,),
    ).fetchall()
    events: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        events.append({"id": r["id"], "ts": r["ts"], "kind": r["kind"], **payload})
    if rows:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)",
                (ENTITY_DIFF_WATERMARK_KEY, str(rows[-1]["id"])),
            )
    return events
