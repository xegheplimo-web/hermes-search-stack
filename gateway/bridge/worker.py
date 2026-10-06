"""Hermes sidecar worker — JSON-lines over stdio (r8-interfaces.md section 4).

Launched as ``<hermes_python> gateway/bridge/worker.py`` by
``HermesBridge`` with ``cwd`` = repo root and ``PYTHONPATH`` covering the repo
plus ``<HERMES_HOME>/hermes-agent``. Each stdin line is a request
``{"id": int, "op": "ping"|"search"|"extract", "params": {...}}``; each stdout
line is ``{"id": int, "ok": bool, "result": ..., "error": str|null}``. Hermes
internals are imported lazily inside the op handlers only — ``--echo``
round-trips every op without touching Hermes so hermetic tests can exercise the
protocol via ``sys.executable``. No exception ever escapes the read loop.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def _write(obj: dict) -> None:
    """Emit one UTF-8 JSON-lines response on stdout."""
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _respond(req_id: object, result: object = None, error: str | None = None) -> None:
    _write({"id": req_id, "ok": error is None, "result": result, "error": error})


# ---------------------------------------------------------------------------
# Payload mapping (managed + keyless shapes -> SearchItem/ExtractItem dicts)
# ---------------------------------------------------------------------------


def _search_item(row: dict, position: int) -> dict | None:
    url = str(row.get("url") or "").strip()
    if not url:
        return None
    return {
        "title": str(row.get("title") or ""),
        "url": url,
        "description": str(row.get("description") or ""),
        "position": int(row.get("position") or position),
    }


def _extract_item(entry: dict, url: str) -> dict:
    if not isinstance(entry, dict):
        return {"url": url, "title": "", "content": "", "error": "empty vendor response"}
    content = str(entry.get("content") or entry.get("raw_content") or "")
    error = entry.get("error")
    if error is not None:
        error = str(error)
    return {
        "url": str(entry.get("url") or url),
        "title": str(entry.get("title") or ""),
        "content": content,
        "error": error,
    }


def _web_rows(payload: object) -> list | None:
    """Managed/keyless search envelope -> the ``data.web`` (or lookalike) list."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    items = data.get("web")
    if isinstance(items, list):
        return items
    for key in ("results", "items"):
        alt = payload.get(key) or data.get(key)
        if isinstance(alt, list):
            return alt
    return None


# ---------------------------------------------------------------------------
# Hermes call paths (lazy imports — proven by verify_web_stack.py /
# test_keyless_fallback.py: managed search, keyless extract ring, rescue)
# ---------------------------------------------------------------------------


def _hermes_search(query: str, max_results: int) -> dict:
    """Managed search via ``tools.web_tools.web_search_tool`` (sync, JSON str)."""
    from tools.web_tools import web_search_tool

    raw = web_search_tool(query, limit=max_results)
    return json.loads(raw)


def _keyless_search(query: str, max_results: int) -> dict:
    """Keyless search ring via ``plugins.web.keyless_mcp.search_with_failover``."""
    from plugins.web import keyless_mcp

    return keyless_mcp.search_with_failover("parallel", query, max_results)


def _keyless_extract(url: str) -> dict:
    """Keyless extract ring for ONE url (parallel -> firecrawl -> keenable -> exa)."""
    from plugins.web import keyless_mcp

    rows = keyless_mcp.extract_with_failover("parallel", [url])
    entry = rows[0] if isinstance(rows, list) and rows else {}
    return _extract_item(entry, url)


def _op_search(params: dict) -> list[dict]:
    query = str(params.get("query") or "")
    if not query.strip():
        raise ValueError("search op requires non-empty 'query'")
    max_results = int(params.get("max_results") or 10)
    managed_error = None
    try:
        payload = _hermes_search(query, max_results)
        if payload.get("success"):
            rows = _web_rows(payload)
            if rows is not None:
                items = [item for i, r in enumerate(rows[:max_results]) if (item := _search_item(r, i + 1))]
                if items:
                    return items
                managed_error = "managed search returned no usable results"
            else:
                managed_error = "managed search payload missing data.web"
        else:
            managed_error = str(payload.get("error") or "managed search reported success=false")
    except Exception as exc:  # noqa: BLE001 — fall through to the keyless ring
        managed_error = f"{type(exc).__name__}: {exc}"
    try:
        payload = _keyless_search(query, max_results)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"managed search failed ({managed_error}); keyless ring also failed: {exc}") from None
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(f"managed search failed ({managed_error}); keyless ring: {payload.get('error')}")
    rows = _web_rows(payload)
    if rows is None:
        raise RuntimeError(f"managed search failed ({managed_error}); keyless payload unrecognized")
    items = [item for i, r in enumerate(rows[:max_results]) if (item := _search_item(r, i + 1))]
    if not items:
        raise RuntimeError(f"managed search failed ({managed_error}); keyless ring returned no results")
    return items


def _op_extract(params: dict) -> list[dict]:
    urls = [str(u) for u in (params.get("urls") or []) if str(u).strip()]
    if not urls:
        raise ValueError("extract op requires a non-empty 'urls' list")
    raw_limit = params.get("char_limit")
    char_limit = int(raw_limit) if raw_limit is not None else None

    managed: dict[str, dict] = {}
    managed_error = None
    try:
        from tools.web_tools import web_extract_tool

        raw = asyncio.run(web_extract_tool(list(urls), char_limit=char_limit))
        payload = json.loads(raw)
        for entry in payload.get("results") or []:
            if isinstance(entry, dict) and entry.get("url"):
                managed[str(entry["url"])] = _extract_item(entry, str(entry["url"]))
        if not managed:
            managed_error = str(payload.get("error") or "managed extract returned no results")
    except Exception as exc:  # noqa: BLE001 — every URL then walks the keyless ring
        managed_error = f"{type(exc).__name__}: {exc}"

    out: list[dict] = []
    for url in urls:
        entry = managed.get(url)
        if entry is not None and entry["content"].strip() and not entry["error"]:
            out.append(entry)
            continue
        try:
            out.append(_keyless_extract(url))
        except Exception as exc:  # noqa: BLE001 — per-URL error entry, never fatal
            detail = entry["error"] if entry is not None else managed_error
            out.append({"url": url, "title": "", "content": "", "error": f"managed ({detail}); keyless ring: {exc}"})
    if char_limit:
        for item in out:
            item["content"] = item["content"][:char_limit]
    return out


# ---------------------------------------------------------------------------
# Echo mode (hermetic protocol tests — no Hermes imports)
# ---------------------------------------------------------------------------


def _echo_search(params: dict) -> list[dict]:
    query = str(params.get("query") or "")
    max_results = int(params.get("max_results") or 10)
    n = max(1, min(3, max_results))
    return [
        {
            "title": f"echo result {i} for {query!r}",
            "url": f"https://echo.local/{i}",
            "description": f"echo snippet {i}",
            "position": i,
        }
        for i in range(1, n + 1)
    ]


def _echo_extract(params: dict) -> list[dict]:
    urls = [str(u) for u in (params.get("urls") or [])]
    raw_limit = params.get("char_limit")
    char_limit = int(raw_limit) if raw_limit is not None else None
    out = []
    for url in urls:
        content = f"echo content for {url}"
        if char_limit:
            content = content[:char_limit]
        out.append({"url": url, "title": f"echo {url}", "content": content, "error": None})
    return out


# ---------------------------------------------------------------------------
# Dispatch + read loop
# ---------------------------------------------------------------------------


def _dispatch(op: object, params: dict, *, echo: bool) -> object:
    if op == "ping":
        result = {"pong": True, "python": sys.executable}
        if echo:
            result["echo"] = True
        return result
    if op == "search":
        return _echo_search(params) if echo else _op_search(params)
    if op == "extract":
        return _echo_extract(params) if echo else _op_extract(params)
    raise ValueError(f"unknown op {op!r} (expected ping|search|extract)")


def serve(echo: bool = False) -> int:
    """Read JSON-lines requests from stdin until EOF; never raise."""
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            _respond(None, error=f"invalid JSON request line: {exc}")
            continue
        req_id = req.get("id") if isinstance(req, dict) else None
        try:
            if not isinstance(req, dict):
                raise ValueError("request must be a JSON object")
            params = req.get("params")
            _respond(req_id, result=_dispatch(req.get("op"), params if isinstance(params, dict) else {}, echo=echo))
        except Exception as exc:  # noqa: BLE001 — every failure is a wire error, never a crash
            _respond(req_id, error=f"{type(exc).__name__}: {exc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gateway.bridge.worker",
        description="Hermes sidecar worker: JSON-lines search/extract over stdio.",
    )
    parser.add_argument("--echo", action="store_true", help="canned responses; no Hermes imports (tests)")
    args = parser.parse_args(argv)
    return serve(echo=args.echo)


if __name__ == "__main__":
    sys.exit(main())
