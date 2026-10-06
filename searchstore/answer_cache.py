"""Verified-answer cache (R6-A / P6) — ``research_pack.v1`` over its own SQLite file.

Caches research packs that passed the ``fact_check.py`` verification gate, so
repeated queries are served without re-running the web stack. The module is
stdlib-only and self-contained: it owns its SQLite file (default
``data/answers.db``) and never opens the SearchStore schema; the only optional
integration is a best-effort ``SearchStore.record_event`` via ``--store``.

Contract: ``analysis/r6-interfaces.md`` §3 (frozen 2026-10-06).

CLI::

    python -m searchstore.answer_cache put --pack f.json [--db P] [--force] [--store S] [--json]
    python -m searchstore.answer_cache get --query Q [--scope S] [--db P] [--store S] [--json]
    python -m searchstore.answer_cache list [--db P] [--json]
    python -m searchstore.answer_cache stats [--db P] [--json]
    python -m searchstore.answer_cache invalidate (--query Q | --url U | --older-than-days N)
                                                 [--scope S] [--db P] [--json]

Exit codes: 0 ok (``get`` = hit) · 1 rejected pack / ``get`` miss · 2 usage/IO.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

try:  # frozen public API (analysis/round3-interfaces.md §3); optional at runtime
    from .store import content_sha256 as _store_sha256
except Exception:  # standalone use without the package context
    _store_sha256 = None

SCHEMA = "research_pack.v1"
DEFAULT_DB = "data/answers.db"
_MODES = ("fast", "deep")

_REQUIRED_KEYS = ("schema", "query", "sources", "created_at", "ttl_days")
_KNOWN_KEYS = frozenset(
    {
        "schema",
        "query",
        "scope",
        "mode",
        "answer_markdown",
        "sources",
        "verification",
        "created_at",
        "ttl_days",
    }
)
_SOURCE_COLUMNS = ("url", "title", "quote", "provider", "served_by", "fetched_at", "trust_score")

_SCHEMA_SQL = """\
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS packs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query_key TEXT NOT NULL UNIQUE,
  query TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT 'fast',
  answer_md TEXT,
  verification TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  ttl_days REAL NOT NULL DEFAULT 14,
  meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS pack_sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  url TEXT,
  title TEXT,
  quote TEXT,
  provider TEXT,
  served_by TEXT,
  fetched_at TEXT,
  trust_score REAL
);
CREATE INDEX IF NOT EXISTS idx_pack_sources_pack ON pack_sources(pack_id);
CREATE INDEX IF NOT EXISTS idx_pack_sources_url ON pack_sources(url);

CREATE TABLE IF NOT EXISTS hits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query_key TEXT NOT NULL,
  ts TEXT NOT NULL,
  source TEXT
);
CREATE INDEX IF NOT EXISTS idx_hits_query_key ON hits(query_key);
"""


class PackError(Exception):
    """Invalid or unverified ``research_pack.v1`` payload."""


def content_sha256(text: str) -> str:
    """Hex SHA-256 of the UTF-8 encoding of *text*.

    Delegates to ``searchstore.store.content_sha256`` (frozen public API) when
    the package is importable; the local fallback is byte-identical so this
    module also works standalone.
    """
    if _store_sha256 is not None:
        return _store_sha256(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_query(q: str) -> str:
    """Casefold + collapse all whitespace + strip; diacritics are PRESERVED."""
    return " ".join(("" if q is None else str(q)).split()).casefold()


def query_key(query: str, scope: str = "") -> str:
    """Stable cache key for (query, scope): sha256 of normalized query + scope."""
    return content_sha256(normalize_query(query) + "\n" + str(scope or "").strip())


# --------------------------------------------------------------------------- helpers


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are assumed UTC. None on failure."""
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _age_days(created_at: str, now: datetime | None = None) -> float:
    """Whole+fractional days since *created_at*; ``math.inf`` when unparseable."""
    dt = _parse_ts(created_at)
    if dt is None:
        return math.inf
    now = now or datetime.now(UTC)
    return (now - dt).total_seconds() / 86400.0


def _is_verified(verification) -> bool:
    """Verification gate: ``fact_check_exit == 0`` OR ``verified is True``."""
    if not isinstance(verification, dict):
        return False
    return verification.get("fact_check_exit") == 0 or verification.get("verified") is True


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _validate_pack(pack: object) -> dict:
    """Validate a ``research_pack.v1`` payload (§2.1); raise PackError on violation.

    ``verification`` is intentionally NOT a required key here — a missing or
    malformed block is handled by the verification gate in ``put`` so that
    ``force=True`` can still store the pack.
    """
    if not isinstance(pack, dict):
        raise PackError(f"pack must be a JSON object, got {type(pack).__name__}")
    missing = [k for k in _REQUIRED_KEYS if k not in pack]
    if missing:
        raise PackError(f"pack missing required key(s): {', '.join(missing)}")
    if pack["schema"] != SCHEMA:
        raise PackError(f"unsupported schema {pack['schema']!r}; expected {SCHEMA!r}")
    if not isinstance(pack["query"], str) or not normalize_query(pack["query"]):
        raise PackError("pack 'query' must be a non-empty string")
    scope = pack.get("scope", "")
    if not isinstance(scope, str):
        raise PackError("pack 'scope' must be a string")
    mode = pack.get("mode", "fast")
    if mode not in _MODES:
        raise PackError(f"pack 'mode' must be one of {_MODES}")
    answer_md = pack.get("answer_markdown")
    if answer_md is not None and not isinstance(answer_md, str):
        raise PackError("pack 'answer_markdown' must be a string or null")
    sources = pack["sources"]
    if not isinstance(sources, list):
        raise PackError("pack 'sources' must be a list")
    norm_sources = []
    for i, s in enumerate(sources):
        if not isinstance(s, dict) or not s.get("url"):
            raise PackError(f"pack source at position {i} is missing required 'url'")
        trust = s.get("trust_score")
        norm_sources.append(
            {
                "url": s["url"],
                "title": s.get("title"),
                "quote": s.get("quote"),
                "provider": s.get("provider"),
                "served_by": s.get("served_by"),
                "fetched_at": s.get("fetched_at"),
                "trust_score": float(trust)
                if isinstance(trust, (int, float)) and not isinstance(trust, bool)
                else None,
            }
        )
    verification = pack.get("verification")
    if verification is not None and not isinstance(verification, dict):
        raise PackError("pack 'verification' must be an object")
    if _parse_ts(pack["created_at"]) is None:
        raise PackError(f"pack 'created_at' is not ISO-8601: {pack['created_at']!r}")
    ttl = pack["ttl_days"]
    if not isinstance(ttl, (int, float)) or isinstance(ttl, bool) or ttl < 0:
        raise PackError("pack 'ttl_days' must be a non-negative number")
    return {
        "query": pack["query"],
        "scope": scope,
        "mode": mode,
        "answer_markdown": answer_md,
        "sources": norm_sources,
        "verification": verification,
        "created_at": str(pack["created_at"]),
        "ttl_days": float(ttl),
        "meta": {k: v for k, v in pack.items() if k not in _KNOWN_KEYS},
    }


def _record_store_event(store_db: str | None, kind: str, payload: dict) -> None:
    """Best-effort event into a real SearchStore db via the public API; never raises."""
    if not store_db:
        return
    try:
        from .store import SearchStore  # lazy: optional integration

        with SearchStore(store_db) as store:
            store.record_event(kind, payload)
    except Exception:
        pass  # recording must never fail the main operation


# --------------------------------------------------------------------------- AnswerCache


class AnswerCache:
    """Verified-answer cache over its own SQLite file (contract §3)."""

    def __init__(self, db_path: str | os.PathLike, *, create: bool = True):
        self._db_path = os.fspath(db_path)
        if not create and not os.path.exists(self._db_path):
            raise FileNotFoundError(f"database not found: {self._db_path} (pass create=True to create it)")
        if create:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = _connect(self._db_path)
        self._conn.executescript(_SCHEMA_SQL)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AnswerCache:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- write ----------

    def put(self, pack: dict, *, force: bool = False) -> dict:
        """Validate + store a ``research_pack.v1`` pack keyed by (query, scope).

        Gate: requires ``verification.fact_check_exit == 0`` or
        ``verification.verified is True`` unless *force*. Returns
        ``{"query_key", "replaced"}``; an existing entry for the same key is
        replaced (sources swapped atomically).
        """
        norm = _validate_pack(pack)
        if not force and not _is_verified(norm["verification"]):
            raise PackError(
                "pack is not verified (verification.fact_check_exit != 0 and "
                "verified is not true); pass force=True to override"
            )
        key = query_key(norm["query"], norm["scope"])
        vals = (
            norm["query"],
            norm["scope"],
            norm["mode"],
            norm["answer_markdown"],
            json.dumps(norm["verification"] or {}, ensure_ascii=False),
            norm["created_at"],
            norm["ttl_days"],
            json.dumps(norm["meta"], ensure_ascii=False),
        )
        with self._conn:
            row = self._conn.execute("SELECT id FROM packs WHERE query_key = ?", (key,)).fetchone()
            if row is None:
                cur = self._conn.execute(
                    "INSERT INTO packs(query_key, query, scope, mode, answer_md, verification,"
                    " created_at, ttl_days, meta) VALUES(?,?,?,?,?,?,?,?,?)",
                    (key, *vals),
                )
                pack_id = cur.lastrowid
                replaced = False
            else:
                pack_id = row["id"]
                self._conn.execute(
                    "UPDATE packs SET query=?, scope=?, mode=?, answer_md=?, verification=?,"
                    " created_at=?, ttl_days=?, meta=? WHERE id=?",
                    (*vals, pack_id),
                )
                self._conn.execute("DELETE FROM pack_sources WHERE pack_id = ?", (pack_id,))
                replaced = True
            for i, s in enumerate(norm["sources"]):
                self._conn.execute(
                    "INSERT INTO pack_sources(pack_id, position, url, title, quote, provider,"
                    " served_by, fetched_at, trust_score) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        pack_id,
                        i,
                        s["url"],
                        s["title"],
                        s["quote"],
                        s["provider"],
                        s["served_by"],
                        s["fetched_at"],
                        s["trust_score"],
                    ),
                )
        return {"query_key": key, "replaced": replaced}

    # ---------- read ----------

    def get(self, query: str, *, scope: str = "", record_hit: bool = True, source: str = "api") -> dict | None:
        """Look up a cached pack; None on miss.

        Returns ``{"pack": {...}, "fresh": bool, "age_days": float}`` where
        ``fresh = age_days <= ttl_days``. A hit (fresh or stale) appends one row
        to ``hits`` unless ``record_hit=False``.
        """
        key = query_key(query, scope)
        row = self._conn.execute("SELECT * FROM packs WHERE query_key = ?", (key,)).fetchone()
        if row is None:
            return None
        sources = [
            {k: r[k] for k in _SOURCE_COLUMNS}
            for r in self._conn.execute(
                "SELECT url, title, quote, provider, served_by, fetched_at, trust_score"
                " FROM pack_sources WHERE pack_id = ? ORDER BY position",
                (row["id"],),
            )
        ]
        pack = {
            "schema": SCHEMA,
            "query": row["query"],
            "scope": row["scope"],
            "mode": row["mode"],
            "answer_markdown": row["answer_md"],
            "sources": sources,
            "verification": json.loads(row["verification"]),
            "created_at": row["created_at"],
            "ttl_days": row["ttl_days"],
        }
        pack.update(json.loads(row["meta"]))
        age = _age_days(row["created_at"])
        fresh = bool(age <= float(row["ttl_days"]))
        if record_hit:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO hits(query_key, ts, source) VALUES(?,?,?)",
                    (key, _iso_now(), source),
                )
        return {"pack": pack, "fresh": fresh, "age_days": age}

    # ---------- maintenance ----------

    def invalidate(
        self,
        *,
        query: str | None = None,
        scope: str | None = None,
        url: str | None = None,
        older_than_days: float | None = None,
    ) -> int:
        """Delete packs matching ALL given filters (AND); returns rows removed.

        No filters → 0 (refuses to mass-delete).
        """
        if query is None and url is None and older_than_days is None:
            return 0
        now = datetime.now(UTC)
        target_key = query_key(query, scope or "") if query is not None else None
        doomed = []
        for row in self._conn.execute("SELECT id, query_key, created_at FROM packs").fetchall():
            if target_key is not None and row["query_key"] != target_key:
                continue
            if url is not None and not self._pack_has_url(row["id"], url):
                continue
            if older_than_days is not None and _age_days(row["created_at"], now) <= older_than_days:
                continue
            doomed.append(row["id"])
        with self._conn:
            for pack_id in doomed:
                self._conn.execute("DELETE FROM packs WHERE id = ?", (pack_id,))
        return len(doomed)

    def _pack_has_url(self, pack_id: int, url: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM pack_sources WHERE pack_id = ? AND url = ? LIMIT 1", (pack_id, url)
        ).fetchone()
        return row is not None

    def stats(self) -> dict:
        """Aggregate counts + oldest/newest pack timestamps."""
        c = self._conn
        return {
            "packs": c.execute("SELECT COUNT(*) FROM packs").fetchone()[0],
            "sources": c.execute("SELECT COUNT(*) FROM pack_sources").fetchone()[0],
            "hits": c.execute("SELECT COUNT(*) FROM hits").fetchone()[0],
            "oldest": c.execute("SELECT MIN(created_at) FROM packs").fetchone()[0],
            "newest": c.execute("SELECT MAX(created_at) FROM packs").fetchone()[0],
        }

    def list_packs(self) -> list[dict]:
        """One summary dict per pack, newest first."""
        out = []
        for row in self._conn.execute("SELECT * FROM packs ORDER BY created_at DESC").fetchall():
            age = _age_days(row["created_at"])
            out.append(
                {
                    "query_key": row["query_key"],
                    "query": row["query"],
                    "scope": row["scope"],
                    "mode": row["mode"],
                    "created_at": row["created_at"],
                    "ttl_days": row["ttl_days"],
                    "sources": self._conn.execute(
                        "SELECT COUNT(*) FROM pack_sources WHERE pack_id = ?", (row["id"],)
                    ).fetchone()[0],
                    "verified": _is_verified(json.loads(row["verification"])),
                    "age_days": age,
                    "fresh": bool(age <= float(row["ttl_days"])),
                }
            )
        return out


# --------------------------------------------------------------------------- CLI


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def _cmd_put(args: argparse.Namespace) -> tuple[int, dict, list[str]]:
    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    with AnswerCache(args.db) as cache:
        res = cache.put(pack, force=args.force)
    _record_store_event(
        args.store,
        "answer_cache_put",
        {"query_key": res["query_key"], "replaced": res["replaced"], "query": pack.get("query")},
    )
    payload = {"ok": True, **res}
    return 0, payload, [f"stored pack {res['query_key'][:12]}… replaced={res['replaced']}"]


def _cmd_get(args: argparse.Namespace) -> tuple[int, dict, list[str]]:
    key = query_key(args.query, args.scope)
    with AnswerCache(args.db) as cache:
        hit = cache.get(args.query, scope=args.scope, source="cli")
    if hit is None:
        return 1, {"ok": True, "found": False, "query_key": key}, [f"cache miss: {args.query!r}"]
    _record_store_event(args.store, "answer_cache_hit", {"query_key": key, "fresh": hit["fresh"], "query": args.query})
    pack = hit["pack"]
    human = [
        f"{'fresh' if hit['fresh'] else 'STALE'}  age={hit['age_days']:.2f}d  ttl={pack['ttl_days']}d  {pack['query']}",
    ]
    if pack.get("answer_markdown"):
        human += ["", pack["answer_markdown"]]
    return 0, {"ok": True, "found": True, **hit}, human


def _cmd_list(args: argparse.Namespace) -> tuple[int, dict, list[str]]:
    with AnswerCache(args.db) as cache:
        packs = cache.list_packs()
    human = [
        f"{p['query_key'][:12]}  {'fresh' if p['fresh'] else 'stale'}  srcs={p['sources']}  {p['query']}" for p in packs
    ]
    return 0, {"ok": True, "packs": packs}, human or ["(empty)"]


def _cmd_stats(args: argparse.Namespace) -> tuple[int, dict, list[str]]:
    with AnswerCache(args.db) as cache:
        stats = cache.stats()
    return 0, {"ok": True, **stats}, [f"{k}: {v}" for k, v in stats.items()]


def _cmd_invalidate(args: argparse.Namespace) -> tuple[int, dict, list[str]]:
    with AnswerCache(args.db) as cache:
        removed = cache.invalidate(
            query=args.query, scope=args.scope, url=args.url, older_than_days=args.older_than_days
        )
    return 0, {"ok": True, "removed": removed}, [f"removed {removed} pack(s)"]


def _add_json(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="print one JSON object to stdout")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="searchstore.answer_cache",
        description="Verified-answer cache (research_pack.v1) over its own SQLite file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("put", help="validate + store a research_pack.v1 JSON file")
    p.add_argument("--pack", required=True, metavar="F", help="path to the pack JSON file")
    p.add_argument("--db", default=DEFAULT_DB, metavar="P", help=f"cache db path (default: {DEFAULT_DB})")
    p.add_argument("--force", action="store_true", help="store even when verification fails/missing")
    p.add_argument("--store", metavar="S", help="optional SearchStore db to record an answer_cache_put event")
    _add_json(p)
    p.set_defaults(func=_cmd_put)

    p = sub.add_parser("get", help="look up a cached pack (exit 0 hit / 1 miss)")
    p.add_argument("--query", required=True, metavar="Q")
    p.add_argument("--scope", default="", metavar="S")
    p.add_argument("--db", default=DEFAULT_DB, metavar="P")
    p.add_argument("--store", metavar="S", help="optional SearchStore db to record an answer_cache_hit event")
    _add_json(p)
    p.set_defaults(func=_cmd_get)

    p = sub.add_parser("list", help="list cached packs, newest first")
    p.add_argument("--db", default=DEFAULT_DB, metavar="P")
    _add_json(p)
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("stats", help="print cache statistics")
    p.add_argument("--db", default=DEFAULT_DB, metavar="P")
    _add_json(p)
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("invalidate", help="delete cached packs by query, url or age")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", metavar="Q")
    group.add_argument("--url", metavar="U")
    group.add_argument("--older-than-days", type=float, metavar="N")
    p.add_argument("--scope", default="", metavar="S", help="scope for --query")
    p.add_argument("--db", default=DEFAULT_DB, metavar="P")
    _add_json(p)
    p.set_defaults(func=_cmd_invalidate)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return the process exit code (0 ok · 1 miss/rejected · 2 usage/IO)."""
    args = _build_parser().parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        code, payload, human = args.func(args)
    except PackError as exc:
        _emit_error(exc, json_mode)
        return 1
    except (FileNotFoundError, ValueError, OSError, sqlite3.Error) as exc:
        _emit_error(exc, json_mode)
        return 2
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in human:
            print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
