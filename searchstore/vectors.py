"""Vector similarity for SearchStore — 3-tier backend (contract §6).

Tier priority: ``sqlite-vec`` -> ``numpy`` -> pure python (``struct`` + ``math``).
The pure-python tier is always available (the fallback floor).
"""

from __future__ import annotations

import math
import sqlite3
import struct
from datetime import datetime
from typing import Any

from .store import SearchStoreError

try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

_TIER_SQLITE_VEC = "sqlite-vec"
_TIER_NUMPY = "numpy"
_TIER_PYTHON = "python"

# id()s of connections on which the sqlite_vec extension has already been loaded.
_SQLITE_VEC_LOADED: set[int] = set()


def _validate_vector(vector: Any) -> list[float]:
    """Return *vector* as a list of finite floats or raise SearchStoreError."""
    if isinstance(vector, (str, bytes)):
        raise SearchStoreError("vector must be a sequence of numbers")
    try:
        values = [float(v) for v in vector]
    except (TypeError, ValueError) as exc:
        raise SearchStoreError(f"vector must be a sequence of numbers: {exc}") from exc
    if not values:
        raise SearchStoreError("vector must not be empty")
    if not all(math.isfinite(v) for v in values):
        raise SearchStoreError("vector must contain only finite values")
    return values


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _clamp(score: float) -> float:
    return max(-1.0, min(1.0, score))


def _sqlite_vec_loadable() -> bool:
    """Probe whether the sqlite_vec extension can be loaded at all."""
    try:
        probe = sqlite3.connect(":memory:")
    except sqlite3.Error:
        return False
    try:
        probe.enable_load_extension(True)
        sqlite_vec.load(probe)
        return True
    except Exception:
        return False
    finally:
        probe.close()


def tier_available() -> str:
    """Return the best available vector tier: 'sqlite-vec' | 'numpy' | 'python'."""
    if sqlite_vec is not None and _sqlite_vec_loadable():
        return _TIER_SQLITE_VEC
    if np is not None:
        return _TIER_NUMPY
    return _TIER_PYTHON


def _sqlite_vec_ready(conn: sqlite3.Connection) -> bool:
    """Load the sqlite_vec extension on *conn* if possible (at most once per conn)."""
    if sqlite_vec is None:
        return False
    if id(conn) in _SQLITE_VEC_LOADED:
        return True
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception:
        return False


def add_embedding(conn: sqlite3.Connection, doc_id: int, vector: Any, model: str = "unknown") -> None:
    """Store *vector* for *doc_id* (INSERT OR REPLACE by doc_id)."""
    values = _validate_vector(vector)
    conn.execute(
        "INSERT OR REPLACE INTO embeddings(doc_id, model, dim, vector, created_at) VALUES(?, ?, ?, ?, ?)",
        (int(doc_id), str(model), len(values), _pack(values), datetime.now().isoformat()),
    )
    conn.commit()


def _similar_sqlite_vec(conn: sqlite3.Connection, query: list[float], k: int = 10) -> list[dict] | None:
    """Cosine search via sqlite_vec's vec_distance_cosine; None = tier unavailable."""
    if not _sqlite_vec_ready(conn):
        return None
    try:
        rows = conn.execute(
            "SELECT doc_id, vec_distance_cosine(vector, ?) AS dist FROM embeddings ORDER BY dist ASC LIMIT ?",
            (_pack(query), k),
        ).fetchall()
    except Exception:
        return None
    return [{"doc_id": doc_id, "score": _clamp(1.0 - dist)} for doc_id, dist in rows]


def _similar_numpy(conn: sqlite3.Connection, query: list[float], k: int = 10) -> list[dict]:
    q = np.asarray(query, dtype=np.float32)
    q_norm = float(np.linalg.norm(q))
    scored: list[tuple[int, float]] = []
    for doc_id, dim, blob in conn.execute("SELECT doc_id, dim, vector FROM embeddings").fetchall():
        if dim != len(query) or len(blob) != dim * 4:
            continue
        vec = np.frombuffer(blob, dtype="<f4", count=dim)
        v_norm = float(np.linalg.norm(vec))
        score = 0.0 if q_norm == 0.0 or v_norm == 0.0 else float(np.dot(q, vec) / (q_norm * v_norm))
        scored.append((doc_id, _clamp(score)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [{"doc_id": doc_id, "score": score} for doc_id, score in scored[:k]]


def _similar_python(conn: sqlite3.Connection, query: list[float], k: int = 10) -> list[dict]:
    q_norm = math.sqrt(math.fsum(x * x for x in query))
    scored: list[tuple[int, float]] = []
    for doc_id, dim, blob in conn.execute("SELECT doc_id, dim, vector FROM embeddings").fetchall():
        if dim != len(query) or len(blob) != dim * 4:
            continue
        vec = list(struct.unpack(f"<{dim}f", blob))
        v_norm = math.sqrt(math.fsum(x * x for x in vec))
        if q_norm == 0.0 or v_norm == 0.0:
            score = 0.0
        else:
            score = math.fsum(a * b for a, b in zip(query, vec, strict=True)) / (q_norm * v_norm)
        scored.append((doc_id, _clamp(score)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [{"doc_id": doc_id, "score": score} for doc_id, score in scored[:k]]


def similar(conn: sqlite3.Connection, vector: Any, k: int = 10) -> list[dict]:
    """Return up to *k* embeddings most similar to *vector* (cosine similarity, desc)."""
    query = _validate_vector(vector)
    if k <= 0:
        return []
    if sqlite_vec is not None and _sqlite_vec_ready(conn):
        rows = _similar_sqlite_vec(conn, query, k)
        if rows is not None:
            return rows
    if np is not None:
        return _similar_numpy(conn, query, k)
    return _similar_python(conn, query, k)


def embedding_count(conn: sqlite3.Connection) -> int:
    """Return the number of rows in the embeddings table."""
    return int(conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
