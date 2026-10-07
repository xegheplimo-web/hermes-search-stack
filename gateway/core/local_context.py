"""gateway/core/local_context.py — local-first VN business evidence (R15-C).

Frozen contract: ``analysis/r15-interfaces.md`` §3. Lets the answer path
consult the local ``data/vn-geo.db`` business entities before the web; the
engine hook that actually calls this is wave-2 C2 (engine.py untouched here).

Hard rules implemented:

- READ-ONLY: the database is opened through a ``file:<path>?mode=ro`` URI.
  No write, no migrate, no ``SearchStore`` open (its ``__init__`` runs DDL).
  A missing or unreadable database yields ``[]`` — never an exception.
- Folded text match per ``searchstore.store.fold_d`` semantics, applied via
  ``vn_geo.categories.fold_text`` — the repo's fold_d superset (đ/Đ -> d plus
  NFD diacritic strip, casefold, whitespace collapse), the same fold family
  used for entity_ids and dedupe tokens.
- Confidence = +0.4 name match + +0.2 area match + +0.2 address match +
  +0.2 coords-present, clamped to [0, 1]. Entities scoring below
  ``min_confidence`` are excluded.
- Ambiguity guard: entities sharing the folded name but with different
  normalized addresses are emitted as SEPARATE items flagged
  ``ambiguous=True`` — never merged, never deduped across addresses.
- Deterministic order: confidence descending, then entity_id ascending.

A field (name/area/address) "matches" the query when its folded token set
(``[0-9a-z]+`` runs — the same alphabet the folded entity index produces)
is fully covered by the query tokens, or the query tokens are fully covered
by the field's, or they share >= 2 tokens (partial overlap covers long
addresses matched by a couple of street tokens). Area tokens are the union
of ``area_old`` and ``province``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from vn_geo.categories import fold_text

#: Repo root = the directory containing the ``gateway`` package.
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Default entity database: ``data/vn-geo.db`` under the repo root.
DEFAULT_VN_GEO_DB = _REPO_ROOT / "data" / "vn-geo.db"

#: Env override for the entity database path.
VN_GEO_DB_ENV = "HERMES_GATEWAY_VN_GEO_DB"

_ENTITY_FORMAT = "entity"  # mirrors searchstore.store.ENTITY_FORMAT
_TOKEN_RE = re.compile(r"[0-9a-z]+")  # mirrors searchstore.store._TOKEN_RE

#: Entity ``source``/``sources`` values that denote registry-grade data
#: (masothue tax registry, CKAN open-data portals) per §3 authority rule.
_REGISTRY_SOURCE = "masothue"
_REGISTRY_PREFIX = "ckan"


@dataclass(slots=True)
class LocalEvidence:
    """One local vn-geo entity as gateway evidence (frozen §3 fields)."""

    id: str  # "local:vn-geo:<entity_id>"
    title: str  # entity name
    url: str  # "local://vn-geo/<entity_id>"
    content: str  # "name · area · address · category · status · sources"
    confidence: float  # additive 0.4/0.2/0.2/0.2 score, clamped to [0, 1]
    freshness: str  # entity updated_at: checked_at, falling back to last_seen
    authority: str  # "registry" (masothue/CKAN-sourced) else "aggregator"
    ambiguous: bool  # same folded name, different normalized address


@dataclass(slots=True)
class _ScoredEntity:
    """Internal match record shared with the MCP business tool."""

    meta: dict
    confidence: float
    ambiguous: bool = False


def resolve_db_path(db_path: str | os.PathLike | None = None) -> Path:
    """Resolve the vn-geo entity db: explicit arg -> ``HERMES_GATEWAY_VN_GEO_DB``
    -> ``<repo>/data/vn-geo.db`` (frozen §3 default resolution)."""
    if db_path:
        return Path(db_path).expanduser()
    env = os.environ.get(VN_GEO_DB_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return DEFAULT_VN_GEO_DB


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open *path* strictly read-only via a ``file:...?mode=ro`` URI.

    Never creates the file, never writes, never migrates; WAL siblings are
    left untouched (acceptance checks db mtime + no -wal growth). On a clean
    WAL database a plain ``mode=ro`` open would CREATE ``-shm``/``-wal`` to
    build the wal-index, so ``immutable=1`` is added when no sibling exists —
    the main file is fully checkpointed and reads leave zero trace. When the
    siblings already exist (a live writer holds the db) plain ``mode=ro``
    attaches to the existing wal-index and sees current commits.
    """
    resolved = path.resolve()
    uri = resolved.as_uri() + "?mode=ro"
    wal = Path(f"{resolved}-wal")
    shm = Path(f"{resolved}-shm")
    if not (wal.exists() or shm.exists()):
        uri += "&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_entity_metas(path: Path) -> list[dict]:
    """Entity meta dicts from ``documents_current``.

    ``sqlite3.Error`` propagates to the caller's boundary
    (``build_local_evidence`` / the MCP handler), which maps it to a
    graceful empty result.
    """
    conn = _connect_ro(path)
    try:
        rows = conn.execute(
            "SELECT meta FROM documents_current WHERE format = ?",
            (_ENTITY_FORMAT,),
        ).fetchall()
    finally:
        conn.close()
    metas = []
    for row in rows:
        try:
            meta = json.loads(row["meta"] or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(meta, dict):
            metas.append(meta)
    return metas


def _tokens(text: object) -> set[str]:
    """Folded token set: fold_d-family unaccent fold + ``[0-9a-z]+`` runs."""
    return set(_TOKEN_RE.findall(fold_text(str(text or ""))))


def _field_match(field_tokens: set[str], query_tokens: set[str]) -> bool:
    """True when field and query cover each other or overlap >= 2 tokens."""
    if not field_tokens or not query_tokens:
        return False
    inter = field_tokens & query_tokens
    return inter == field_tokens or inter == query_tokens or len(inter) >= 2


def _confidence(meta: dict, query_tokens: set[str]) -> float:
    """§3 additive score: +0.4 name, +0.2 area, +0.2 address, +0.2 coords."""
    score = 0.0
    if _field_match(_tokens(meta.get("name")), query_tokens):
        score += 0.4
    area_tokens = _tokens(meta.get("area_old")) | _tokens(meta.get("province"))
    if _field_match(area_tokens, query_tokens):
        score += 0.2
    if _field_match(_tokens(meta.get("address_text")), query_tokens):
        score += 0.2
    if meta.get("lat") is not None and meta.get("lng") is not None:
        score += 0.2
    return min(1.0, max(0.0, score))


def _entity_id(meta: dict) -> str:
    return str(meta.get("entity_id") or "")


def _mark_ambiguous(items: list[_ScoredEntity]) -> None:
    """Flag same-folded-name groups whose normalized addresses differ.

    Flagged items stay separate rows — the guard forbids merging/aliasing,
    it never collapses entities across different addresses.
    """
    by_name: dict[str, list[_ScoredEntity]] = {}
    for item in items:
        by_name.setdefault(fold_text(str(item.meta.get("name") or "")), []).append(item)
    for group in by_name.values():
        if len(group) < 2:
            continue
        addresses = {fold_text(str(item.meta.get("address_text") or "")) for item in group}
        if len(addresses) > 1:
            for item in group:
                item.ambiguous = True


def _scored_entities(
    query: str,
    db_path: Path,
    *,
    min_confidence: float = 0.5,
    limit: int = 8,
) -> list[_ScoredEntity]:
    """Score -> threshold -> ambiguity-flag -> deterministic order -> limit.

    Shared matching core for ``build_local_evidence`` and the MCP
    ``hermes_vn kind="business"`` handler (same task's surface).
    """
    query_tokens = _tokens(query)
    items = []
    for meta in _load_entity_metas(db_path):
        score = _confidence(meta, query_tokens)
        if score >= min_confidence:
            items.append(_ScoredEntity(meta=meta, confidence=score))
    _mark_ambiguous(items)
    items.sort(key=lambda item: (-item.confidence, _entity_id(item.meta)))
    return items[: max(0, int(limit))]


def _authority(meta: dict) -> str:
    """``registry`` for masothue/CKAN-sourced entities, else ``aggregator``."""
    sources = {str(meta.get("source") or "")}
    sources.update(str(s) for s in (meta.get("sources") or []))
    for source in sources:
        if source == _REGISTRY_SOURCE or source.startswith(_REGISTRY_PREFIX):
            return "registry"
    return "aggregator"


def _content(meta: dict) -> str:
    """Compact summary: name · area · address · category · status · sources."""
    sources = meta.get("sources") or ([meta["source"]] if meta.get("source") else [])
    parts = [
        str(meta.get("name") or ""),
        str(meta.get("area_old") or meta.get("province") or ""),
        str(meta.get("address_text") or ""),
        str(meta.get("category") or ""),
        str(meta.get("status") or ""),
        ", ".join(str(s) for s in sources),
    ]
    return " · ".join(part for part in parts if part)


def build_local_evidence(
    query: str,
    *,
    db_path: str | os.PathLike | None = None,
    limit: int = 8,
    min_confidence: float = 0.5,
) -> list[LocalEvidence]:
    """Local-first evidence list from the vn-geo entity store (frozen §3).

    Returns ``[]`` when the resolved database does not exist or cannot be
    read — the local path must never break the answer pipeline.
    """
    path = resolve_db_path(db_path)
    if not path.exists():
        return []
    try:
        scored = _scored_entities(query, path, min_confidence=min_confidence, limit=limit)
    except (sqlite3.Error, OSError, ValueError):
        return []
    out = []
    for item in scored:
        meta = item.meta
        entity_id = _entity_id(meta)
        out.append(
            LocalEvidence(
                id=f"local:vn-geo:{entity_id}",
                title=str(meta.get("name") or entity_id),
                url=f"local://vn-geo/{entity_id}",
                content=_content(meta),
                confidence=item.confidence,
                freshness=str(meta.get("checked_at") or meta.get("last_seen") or ""),
                authority=_authority(meta),
                ambiguous=item.ambiguous,
            )
        )
    return out
