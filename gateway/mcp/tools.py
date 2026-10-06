"""MCP tool implementations for the Hermes universal gateway.

Thin, SDK-free wrappers over the engine and repo modules, per
``analysis/r8-interfaces.md`` section 7. Tools never raise raw exceptions:
every failure is returned as a structured ``{"error": ...}`` payload.

Binding: :func:`make_tools` captures an engine (plus an optional backend and
store-db override) and returns six plain callables with the frozen names and
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
)

#: Frozen parameter contracts from r8-interfaces.md section 7.
#: ``required`` lists params without defaults; ``defaults`` maps the rest.
FROZEN_TOOL_PARAMS: dict[str, dict[str, Any]] = {
    "hermes_search": {"required": ("query",), "defaults": {"max_results": 10}},
    "hermes_extract": {"required": ("urls",), "defaults": {"char_limit": 15000}},
    "hermes_research": {"required": ("query",), "defaults": {"depth": "auto"}},
    "hermes_fact_check": {"required": ("claims_text", "sources"), "defaults": {}},
    "hermes_store_query": {
        "required": ("query",),
        "defaults": {"limit": 10, "mode": "auto"},
    },
    "hermes_vn": {
        "required": ("kind", "query"),
        "defaults": {"area": None, "days": None},
    },
}

_VN_KINDS: tuple[str, ...] = ("admin", "places", "enterprises", "news")

_DEFAULT_STORE_DB = "data/searchstore.db"


# ---------------------------------------------------------------------------
# Binding helpers (public frozen API only — no engine internals touched)
# ---------------------------------------------------------------------------


def _resolve_backend(engine: Any, backend: Any | None) -> Any | None:
    """Use the explicit backend, else the engine's own backend attribute."""
    if backend is not None:
        return backend
    return getattr(engine, "backend", None)


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
) -> dict[str, Callable[..., dict]]:
    """Build the six frozen tools bound to *engine*.

    *backend* overrides ``engine.backend`` when given (the engine exposes
    its backend; the override exists for tests and embedding). *store_db*
    overrides the engine config's ``store_db`` path.
    """
    active_backend = _resolve_backend(engine, backend)
    store_path = _resolve_store_path(engine, store_db)
    repo_root = _resolve_repo_root(engine)

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

    def hermes_research(query: str, depth: str = "auto") -> dict:
        """Run a full grounded research pass via ``engine.run()``.

        Returns ``{answer_markdown, sources, depth, cached, elapsed_ms,
        warnings}``.
        """
        started = time.perf_counter()
        try:
            result = engine.run(query, depth=depth)
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
        sources = [
            {
                "id": _field(s, "id", i),
                "title": _field(s, "title", ""),
                "url": _field(s, "url", ""),
            }
            for i, s in enumerate(_field(result, "sources", []) or [])
        ]
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
        """Vietnam data lookup by *kind* (admin/places/enterprises/news).

        ``news`` delegates to the ``vn_news`` module when present; the other
        kinds query the SearchStore filtered by kind tag.
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
        return _vn_store_query(store_path, norm_kind, query, area=area, days=days)

    return {
        "hermes_search": hermes_search,
        "hermes_extract": hermes_extract,
        "hermes_research": hermes_research,
        "hermes_fact_check": hermes_fact_check,
        "hermes_store_query": hermes_store_query,
        "hermes_vn": hermes_vn,
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
