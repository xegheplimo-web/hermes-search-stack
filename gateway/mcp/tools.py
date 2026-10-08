"""MCP tool implementations for the Hermes universal gateway.

Thin, SDK-free wrappers over the engine and repo modules, per
``analysis/r8-interfaces.md`` section 7. Tools never raise raw exceptions:
every failure is returned as a structured ``{"error": ...}`` payload.

Binding: :func:`make_tools` captures an engine (plus an optional backend and
store-db override) and returns eight plain callables with the frozen names and
signatures. ``gateway/mcp/server.py`` registers them on the MCP server.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

TOOL_NAMES: tuple[str, ...] = (
    "hermes_search",
    "hermes_extract",
    "hermes_research",
    "hermes_fact_check",
    "hermes_store_query",
    "hermes_vn",
    "hermes_places",
    "hermes_social",
)

#: Frozen parameter contracts from r8-interfaces.md section 7.
#: ``required`` lists params without defaults; ``defaults`` maps the rest.
FROZEN_TOOL_PARAMS: dict[str, dict[str, Any]] = {
    "hermes_search": {"required": ("query",), "defaults": {"max_results": 10}},
    "hermes_extract": {"required": ("urls",), "defaults": {"char_limit": 15000}},
    "hermes_research": {"required": ("query",), "defaults": {"depth": "auto", "context": None}},
    "hermes_fact_check": {"required": ("claims_text", "sources"), "defaults": {}},
    "hermes_store_query": {
        "required": ("query",),
        "defaults": {"limit": 10, "mode": "auto"},
    },
    "hermes_vn": {
        "required": ("kind", "query"),
        "defaults": {"area": None, "days": None},
    },
    "hermes_places": {
        "required": ("query",),
        "defaults": {"area": None, "category": None, "min_rating": None, "count": 8},
    },
    "hermes_social": {"required": ("query", "platform"), "defaults": {}},
}

_VN_KINDS: tuple[str, ...] = ("admin", "places", "enterprises", "news", "business")

_SOCIAL_PLATFORMS: tuple[str, ...] = ("v2ex", "bilibili", "youtube", "rss")

_DEFAULT_STORE_DB = "data/searchstore.db"


# ---------------------------------------------------------------------------
# Binding helpers (public frozen API only — no engine internals touched)
# ---------------------------------------------------------------------------


def _resolve_backend(engine: Any, backend: Any | None) -> Any | None:
    """Use the explicit backend, else the engine's public ``backend`` accessor.

    ``Engine.backend`` is a lazy property (first access builds the backend
    from config); a backend that fails to construct resolves to ``None`` so
    the tools degrade to a structured ``{"error": ...}`` instead of taking
    down the whole MCP bind (r9 §B).
    """
    if backend is not None:
        return backend
    try:
        return getattr(engine, "backend", None)
    except Exception:  # noqa: BLE001 — resolution failure means "unavailable"
        return None


def _resolve_store_path(engine: Any, store_db: str | None) -> str:
    """Store DB path: explicit override, else engine config, else default."""
    if store_db:
        return str(store_db)
    config = getattr(engine, "config", None)
    path = getattr(config, "store_db", None) if config is not None else None
    return str(path) if path else _DEFAULT_STORE_DB


def _resolve_repo_root(engine: Any) -> Path:
    """Repo root for ``data/tmp/`` temp files (fact_check), else cwd."""
    config = getattr(engine, "config", None)
    root = getattr(config, "repo_root", None) if config is not None else None
    try:
        return Path(root) if root else Path.cwd()
    except Exception:  # noqa: BLE001 — never fail path resolution
        return Path.cwd()


def _field(item: Any, name: str, default: Any = "") -> Any:
    """Read a field from a dataclass instance or a plain dict."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_tools(
    engine: Any,
    *,
    backend: Any | None = None,
    store_db: str | None = None,
    places_db: str | None = None,
) -> dict[str, Callable[..., dict]]:
    """Build the eight frozen tools bound to *engine*.

    *backend* overrides ``engine.backend`` when given (the engine exposes
    its backend; the override exists for tests and embedding). *store_db*
    overrides the engine config's ``store_db`` path. *places_db* overrides
    the default ``<repo_root>/data/places.db`` path for ``hermes_places``.
    """
    active_backend = _resolve_backend(engine, backend)
    store_path = _resolve_store_path(engine, store_db)
    repo_root = _resolve_repo_root(engine)
    places_path = str(places_db) if places_db else str(repo_root / "data" / "places.db")

    def hermes_search(query: str, max_results: int = 10) -> dict:
        """Search the web via the engine backend.

        Returns ``{results: [{title, url, description, position}]}``.
        """
        if active_backend is None:
            return {"results": [], "error": "no search backend available"}
        try:
            items = active_backend.search(query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 — structured error, never raise
            return {"results": [], "error": f"search failed: {exc}"}
        results = []
        for i, item in enumerate(items or []):
            results.append(
                {
                    "title": _field(item, "title", ""),
                    "url": _field(item, "url", ""),
                    "description": _field(item, "description", ""),
                    "position": _field(item, "position", i),
                }
            )
        return {"results": results}

    def hermes_extract(urls: list[str], char_limit: int = 15000) -> dict:
        """Extract page text for *urls* via the engine backend.

        Returns ``{results: [{url, title, content, error?}]}``; per-page
        failures are reported on the item, backend failures top-level.
        """
        if active_backend is None:
            return {"results": [], "error": "no search backend available"}
        try:
            items = active_backend.extract(list(urls), char_limit=char_limit)
        except Exception as exc:  # noqa: BLE001 — structured error, never raise
            return {"results": [], "error": f"extract failed: {exc}"}
        results = []
        for item in items or []:
            entry: dict[str, Any] = {
                "url": _field(item, "url", ""),
                "title": _field(item, "title", ""),
                "content": _field(item, "content", ""),
            }
            err = _field(item, "error", None)
            if err:
                entry["error"] = err
            results.append(entry)
        return {"results": results}

    def hermes_research(query: str, depth: str = "auto", context: list[dict] | None = None) -> dict:
        """Run a full grounded research pass via ``engine.run()``.

        *context* is an optional list of ``{title, url, content}`` dicts the
        engine appends as ordinary ``origin="caller"`` evidence after the
        fetched sources (r17 §4-5). Returns ``{answer_markdown, sources,
        depth, cached, elapsed_ms, warnings}``.
        """
        started = time.perf_counter()
        if context is not None and not isinstance(context, list):
            return {
                "answer_markdown": "",
                "sources": [],
                "depth": depth,
                "cached": False,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "warnings": ["context must be a list of {title,url,content} dicts"],
                "error": "context must be a list of {title,url,content} dicts",
            }
        try:
            result = engine.run(query, depth=depth, context=context)
        except Exception as exc:  # noqa: BLE001 — structured error, never raise
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return {
                "answer_markdown": "",
                "sources": [],
                "depth": depth,
                "cached": False,
                "elapsed_ms": elapsed_ms,
                "warnings": [f"research failed: {exc}"],
                "error": f"research failed: {exc}",
            }
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        sources = []
        for i, s in enumerate(_field(result, "sources", []) or []):
            entry: dict[str, Any] = {
                "id": _field(s, "id", i),
                "title": _field(s, "title", ""),
                "url": _field(s, "url", ""),
            }
            origin = _field(s, "origin", None)
            if origin:
                entry["origin"] = origin
            sources.append(entry)
        return {
            "answer_markdown": _field(result, "answer_markdown", ""),
            "sources": sources,
            "depth": _field(result, "depth", depth),
            "cached": bool(_field(result, "cached", False)),
            "elapsed_ms": elapsed_ms,
            "warnings": list(_field(result, "warnings", []) or []),
        }

    def hermes_fact_check(claims_text: str, sources: list[dict]) -> dict:
        """Verify *claims_text* against *sources* via repo ``fact_check.py``.

        Returns ``{verdicts: [{claim, verdict, quote}], summary}``. The LLM
        judge runs only when ``HERMES_GATEWAY_FACT_JUDGE=aux``; otherwise
        (and whenever the judge is unreachable) the run degrades to
        mechanical-only verdicts with a warning. Hermetic by default.
        """
        if not isinstance(claims_text, str) or not claims_text.strip():
            return {
                "verdicts": [],
                "summary": {"pass": False, "flags_n": 0},
                "warnings": ["claims_text must be a non-empty string"],
                "error": "claims_text must be a non-empty string",
            }
        if not isinstance(sources, list):
            return {
                "verdicts": [],
                "summary": {"pass": False, "flags_n": 0},
                "warnings": ["sources must be a list of {title, url, quote?}"],
                "error": "sources must be a list of {title, url, quote?}",
            }
        try:
            from fact_check import run_check
        except ImportError as exc:
            return {
                "verdicts": [],
                "summary": {"pass": False, "flags_n": 0},
                "warnings": [f"fact_check module not available: {exc}"],
                "error": f"fact_check module not available: {exc}",
            }

        tmp_dir = repo_root / "data" / "tmp"
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {
                "verdicts": [],
                "summary": {"pass": False, "flags_n": 0},
                "warnings": [f"cannot create temp dir {tmp_dir}: {exc}"],
                "error": f"cannot create temp dir {tmp_dir}: {exc}",
            }

        claim_lines = [ln.strip() for ln in claims_text.splitlines() if ln.strip()]
        ledger_sources = []
        source_lines = []
        for i, src in enumerate(sources):
            sid = i + 1
            url = _field(src, "url", "")
            title = _field(src, "title", "")
            quote = _field(src, "quote", "")
            quote_text = quote.get("text", "") if isinstance(quote, dict) else quote
            entry: dict[str, Any] = {"id": sid, "url": url, "title": title}
            if quote_text:
                entry["quotes"] = [{"text": str(quote_text)}]
            ledger_sources.append(entry)
            source_lines.append(f"[{sid}] {url or title or f'source-{sid}'}")
        draft_text = claims_text.rstrip() + "\n\nSources:\n" + "\n".join(source_lines) + "\n"
        ledger = {"sources": ledger_sources}

        draft_path = ledger_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                prefix="mcp-fact-draft-",
                dir=str(tmp_dir),
                delete=False,
                encoding="utf-8",
            ) as fh:
                fh.write(draft_text)
                draft_path = Path(fh.name)
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                prefix="mcp-fact-ledger-",
                dir=str(tmp_dir),
                delete=False,
                encoding="utf-8",
            ) as fh:
                json.dump(ledger, fh, ensure_ascii=False)
                ledger_path = Path(fh.name)
            # The aux LLM judge is opt-in via HERMES_GATEWAY_FACT_JUDGE=aux:
            # attempting it by default could block on live network calls.
            # judge="aux" still degrades internally to mechanical-only
            # verdicts with a notice when the judge is unreachable.
            judge = "aux" if os.environ.get("HERMES_GATEWAY_FACT_JUDGE", "off").strip().lower() == "aux" else "off"
            report, _code, notices = run_check(draft_path, ledger_path, judge=judge)
        except Exception as exc:  # noqa: BLE001 — structured error, never raise
            return {
                "verdicts": [],
                "summary": {"pass": False, "flags_n": 0},
                "warnings": [f"fact_check run failed: {exc}"],
                "error": f"fact_check run failed: {exc}",
            }
        finally:
            for path in (draft_path, ledger_path):
                try:
                    if path is not None:
                        path.unlink()
                except OSError:
                    pass

        warnings = list(notices)
        judge = report.get("judge", {}) if isinstance(report, dict) else {}
        if judge.get("status") != "ok":
            warnings.append("LLM judge unavailable; mechanical-only verdicts")
        verdicts = []
        for claim in report.get("claims", []) or []:
            idx = claim.get("sentence", -1)
            text = claim_lines[idx] if 0 <= idx < len(claim_lines) else f"sentence {idx}"
            verdicts.append(
                {
                    "claim": text,
                    "verdict": claim.get("verdict", "not_judged"),
                    "quote": claim.get("quote", "") or "",
                }
            )
        if not verdicts:
            # No cited sentences (e.g. claims carry no [n] markers):
            # report each line as a mechanical-only verdict.
            verdicts = [{"claim": line, "verdict": "not_judged", "quote": ""} for line in claim_lines]
            warnings.append("no cited sentences found; verdicts are mechanical-only")
        stats = report.get("stats", {}) if isinstance(report, dict) else {}
        summary_in = report.get("summary", {}) if isinstance(report, dict) else {}
        summary = {
            "pass": bool(summary_in.get("pass", False)),
            "flags_n": int(summary_in.get("flags_n", 0)),
            "coverage": stats.get("coverage", 0.0),
            "unsupported_n": int(summary_in.get("unsupported_n", 0)),
            "conflicting_n": int(summary_in.get("conflicting_n", 0)),
        }
        return {"verdicts": verdicts, "summary": summary, "warnings": warnings}

    def hermes_store_query(query: str, limit: int = 10, mode: str = "auto") -> dict:
        """Query the local SearchStore (FTS). *mode*: ``auto``/``fts``.

        ``hybrid``/``vector`` need a query vector the tool cannot supply, so
        they return a structured error directing the caller to ``fts``.
        """
        norm = (mode or "auto").strip().lower()
        if norm == "auto":
            norm = "fts"
        if norm in ("hybrid", "vector"):
            return {
                "results": [],
                "mode": mode,
                "error": f"mode {mode!r} requires a query vector; use 'fts' or 'auto'",
            }
        if norm != "fts":
            return {
                "results": [],
                "mode": mode,
                "error": f"unknown search mode {mode!r}; use 'fts', 'hybrid', 'vector' or 'auto'",
            }
        try:
            from searchstore import SearchStore
        except ImportError as exc:
            return {
                "results": [],
                "mode": norm,
                "error": f"searchstore not available: {exc}",
            }
        try:
            with SearchStore(store_path) as store:
                hits = store.search(query, limit=limit, mode="fts")
        except Exception as exc:  # noqa: BLE001 — structured error, never raise
            return {
                "results": [],
                "mode": norm,
                "error": f"store query failed: {exc}",
            }
        return {"results": list(hits or []), "mode": norm}

    def hermes_vn(
        kind: str,
        query: str,
        area: str | None = None,
        days: int | None = None,
    ) -> dict:
        """Vietnam data lookup by *kind* (admin/places/enterprises/news/business).

        ``news`` delegates to the ``vn_news`` module when present, ``business``
        queries the local vn-geo.db entity store read-only (R15-C), and the
        other kinds query the SearchStore filtered by kind tag.
        """
        norm_kind = (kind or "").strip().lower()
        if norm_kind not in _VN_KINDS:
            return {
                "results": [],
                "kind": kind,
                "error": f"unknown kind {kind!r}; use one of {list(_VN_KINDS)}",
            }
        if norm_kind == "news":
            return _vn_news_query(query, days=days, store_db=store_path or None)
        if norm_kind == "business":
            return _vn_business_query(query, area=area)
        return _vn_store_query(store_path, norm_kind, query, area=area, days=days)

    def hermes_places(
        query: str,
        area: str | None = None,
        category: str | None = None,
        min_rating: float | None = None,
        count: int = 8,
    ) -> dict:
        """Query the local places db via ``vn_geo.places`` (r16 §1).

        Returns ``{ok, kind: "places", query, area, count, places,
        viewport}``: a blank *query* browses (``text=None``), *count* is
        clamped to 1..50, and rows map 1:1 onto the frozen Place schema.
        Never raises — a missing db or query failure returns ``ok=False``
        with an ``error`` string.
        """
        text = query.strip() if isinstance(query, str) else ""
        text = text or None
        try:
            limit = max(1, min(50, int(count)))
        except (TypeError, ValueError):
            limit = 8
        error_base = {
            "ok": False,
            "kind": "places",
            "query": query,
            "area": area,
            "count": 0,
            "places": [],
            "viewport": None,
        }
        path = Path(places_path)
        if not path.exists():
            return {**error_base, "error": f"places db not found at {path}"}
        try:
            from searchstore import SearchStore
        except ImportError as exc:
            return {**error_base, "error": f"searchstore not available: {exc}"}
        try:
            from vn_geo import places as vn_places
        except Exception as exc:  # noqa: BLE001 — module absent/broken/mid-edit
            return {**error_base, "error": f"vn_geo.places not available: {exc}"}
        try:
            with SearchStore(str(path)) as store:
                rows = vn_places.query_places(
                    store,
                    text=text,
                    area=area,
                    category=category,
                    min_rating=min_rating,
                    limit=limit,
                )
            places = [_place_row(row) for row in rows or [] if isinstance(row, dict)]
            viewport = _places_viewport(places)
        except Exception as exc:  # noqa: BLE001 — structured error, never raise
            return {**error_base, "error": f"places query failed: {exc}"}
        return {
            "ok": True,
            "kind": "places",
            "query": query,
            "area": area,
            "count": len(places),
            "places": places,
            "viewport": viewport,
        }

    def hermes_social(query: str, platform: str) -> dict:
        """Search ONE keyless social provider (r17 §4: v2ex|bilibili|youtube|rss).

        Returns ``{platform, results: [{title, url, description, position}]}``.
        Same isolation policy as ``hermes_search``: every failure — unknown
        platform, provider build, provider search — is a structured
        ``{"error": ...}``, never a raise.
        """
        key = str(platform or "").strip().lower()
        if key not in _SOCIAL_PLATFORMS:
            return {
                "platform": platform,
                "results": [],
                "error": f"unsupported platform {platform!r} (use v2ex|bilibili|youtube|rss)",
            }
        try:
            import gateway.providers as providers

            provider = providers.get_provider(key)
        except Exception as exc:  # noqa: BLE001 — structured error, never raise
            return {"platform": platform, "results": [], "error": f"provider {key!r} unavailable: {exc}"}
        try:
            items = provider.search(query, max_results=10)
        except Exception as exc:  # noqa: BLE001 — same isolation as hermes_search
            return {"platform": platform, "results": [], "error": f"search failed: {exc}"}
        results = []
        for i, item in enumerate(items or []):
            results.append(
                {
                    "title": _field(item, "title", ""),
                    "url": _field(item, "url", ""),
                    "description": _field(item, "description", ""),
                    "position": _field(item, "position", i),
                }
            )
        return {"platform": platform, "results": results}

    return {
        "hermes_search": hermes_search,
        "hermes_extract": hermes_extract,
        "hermes_research": hermes_research,
        "hermes_fact_check": hermes_fact_check,
        "hermes_store_query": hermes_store_query,
        "hermes_vn": hermes_vn,
        "hermes_places": hermes_places,
        "hermes_social": hermes_social,
    }


# ---------------------------------------------------------------------------
# hermes_places helpers
# ---------------------------------------------------------------------------


def _place_row(row: dict) -> dict:
    """Map a ``query_places`` row onto the frozen §1 Place object.

    Fields pass through 1:1; the tool computes only ``id`` (the doc url =
    stable identity) and ``url`` (clickable ``source_url`` when the r16-b
    enrichment provides it, else ``id``). All keys are read via ``.get()``
    so the mapping works before and after r16-b lands.
    """
    doc_url = row.get("url")
    return {
        "id": doc_url,
        "name": row.get("name"),
        "source": row.get("source"),
        "address": row.get("address"),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "rating": row.get("rating"),
        "review_count": row.get("review_count"),
        "category": row.get("category"),
        "phone": row.get("phone"),
        "website": row.get("website"),
        "hours": row.get("hours"),
        "thumbnail": row.get("thumbnail"),
        "url": row.get("source_url") or doc_url,
        "scanned_at": row.get("scanned_at"),
    }


def _is_num(value: Any) -> bool:
    """True for real numeric lat/lon values (bool excluded)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _places_viewport(places: list[dict]) -> dict | None:
    """Map viewport over places with numeric lat+lon (§1).

    ``bbox`` is ``[min_lon, min_lat, max_lon, max_lat]`` (MapLibre
    LngLatBounds order); ``center`` is the bbox midpoint ``[lon, lat]``.
    No coordinates at all -> ``None``; a single point -> degenerate bbox.
    """
    points = [(float(p["lat"]), float(p["lon"])) for p in places if _is_num(p.get("lat")) and _is_num(p.get("lon"))]
    if not points:
        return None
    lats = [lat for lat, _ in points]
    lons = [lon for _, lon in points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    return {
        "center": [(min_lon + max_lon) / 2, (min_lat + max_lat) / 2],
        "bbox": [min_lon, min_lat, max_lon, max_lat],
    }


# ---------------------------------------------------------------------------
# hermes_vn backends
# ---------------------------------------------------------------------------


def _vn_news_query(query: str, *, days: int | None, store_db: str | None = None) -> dict:
    """News path: lazy ``vn_news`` import with graceful degradation."""
    try:
        import vn_news  # type: ignore[import-not-found]
    except Exception:  # module absent, broken, or mid-edit — stay graceful
        return {"results": [], "kind": "news", "error": "vn_news not available"}
    query_fn = getattr(vn_news, "query", None)
    if not callable(query_fn):
        return {
            "results": [],
            "kind": "news",
            "error": "vn_news not available (no query API)",
        }
    kwargs: dict[str, Any] = {}
    if days is not None:
        kwargs["days"] = days
    if store_db:
        kwargs["db_path"] = store_db
    try:
        try:
            records = query_fn(query, **kwargs)
        except TypeError:
            records = query_fn(query)
    except Exception as exc:  # noqa: BLE001 — structured error, never raise
        return {"results": [], "kind": "news", "error": f"vn_news query failed: {exc}"}
    results = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        results.append(
            {
                "title": rec.get("title", ""),
                "url": rec.get("url", ""),
                "source": rec.get("source", ""),
                "published": rec.get("published", ""),
                "summary": rec.get("summary", ""),
            }
        )
    return {"results": results, "kind": "news"}


def _vn_business_query(query: str, *, area: str | None) -> dict:
    """Business path: read-only vn-geo.db entity lookup via core.local_context.

    Returns ``{"ok", "kind", "count", "items"}`` where each item carries the
    entity fields (the ``vn_geo.business.query_entities`` meta shape) plus
    ``confidence`` and ``ambiguous``; a missing database degrades to
    ``{"ok": False, "error": "vn-geo.db not found at <path>"}``.
    """
    try:
        from gateway.core import local_context
    except Exception as exc:  # module absent, broken, or mid-edit — stay graceful
        return {"ok": False, "kind": "business", "error": f"local_context not available: {exc}"}
    db_path = local_context.resolve_db_path(None)
    if not db_path.exists():
        return {"ok": False, "kind": "business", "error": f"vn-geo.db not found at {db_path}"}
    text = query if not area else f"{query} {area}"
    try:
        # Shared matching core from the same task's module (R15-C).
        scored = local_context._scored_entities(text, db_path)
    except Exception as exc:  # noqa: BLE001 — structured error, never raise
        return {"ok": False, "kind": "business", "error": f"business query failed: {exc}"}
    if area:
        # Same area filter semantics as vn_geo.business.query_entities:
        # folded substring over address_text/area_old/province.
        from vn_geo.categories import fold_text

        area_key = fold_text(area)
        scored = [
            item
            for item in scored
            if area_key
            in fold_text(" ".join(str(item.meta.get(k) or "") for k in ("address_text", "area_old", "province")))
        ]
    items = [dict(item.meta, confidence=item.confidence, ambiguous=item.ambiguous) for item in scored]
    return {"ok": True, "kind": "business", "count": len(items), "items": items}


def _vn_store_query(
    store_path: str,
    kind: str,
    query: str,
    *,
    area: str | None,
    days: int | None,
) -> dict:
    """Admin/places/enterprises path: SearchStore FTS filtered by kind tag."""
    try:
        from searchstore import SearchStore
    except ImportError as exc:
        return {"results": [], "kind": kind, "error": f"searchstore not available: {exc}"}
    text = query if not area else f"{query} {area}"
    try:
        with SearchStore(store_path) as store:
            hits = store.search(text, limit=20, mode="fts")
            kept = []
            for hit in hits or []:
                if _hit_matches_kind(store, hit, kind) and _hit_matches_days(hit, days):
                    kept.append(hit)
    except Exception as exc:  # noqa: BLE001 — structured error, never raise
        return {"results": [], "kind": kind, "error": f"store query failed: {exc}"}
    return {"results": kept, "kind": kind, "query": query}


def _hit_matches_kind(store: Any, hit: dict, kind: str) -> bool:
    """True when the hit's provider/meta carries the *kind* tag."""
    provider = str(hit.get("provider") or "").lower()
    if provider:
        return kind in provider
    try:
        doc = store.get_document(hit.get("doc_id"))
    except Exception:  # noqa: BLE001 — treat lookup failure as non-match
        return False
    if not doc:
        return False
    meta = doc.get("meta")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {"_raw": meta}
    blob = json.dumps({"provider": doc.get("provider"), "meta": meta}).lower()
    return kind in blob


def _hit_matches_days(hit: dict, days: int | None) -> bool:
    """Freshness filter on ``fetched_at``/``published`` when *days* given."""
    if days is None:
        return True
    stamp = hit.get("fetched_at") or hit.get("published") or ""
    try:
        moment = datetime.fromisoformat(str(stamp))
    except ValueError:
        return True  # unparseable timestamp: do not exclude
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cutoff = datetime.now(UTC) - timedelta(days=int(days))
    return moment >= cutoff
