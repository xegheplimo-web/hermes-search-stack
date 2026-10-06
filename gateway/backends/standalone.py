"""Standalone keyless backend — direct HTTP to public free-tier endpoints.

Experimental ``SearchBackend`` that re-implements the keyless call paths proven
by ``test_keyless_fallback.py`` / ``plugins.web.keyless_mcp`` without the Hermes
runtime: search via Parallel -> Exa, extract via Parallel -> Exa -> Keenable.
All calls are best-effort with clear errors; ``httpx`` is imported lazily so
the gateway package stays importable without optional dependencies. Never used
in tests (no live calls allowed).
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from gateway.protocols import ExtractItem, SearchItem

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
KEENABLE_API_URL = "https://api.keenable.ai"
_KEENABLE_TITLE = "hermes-search-stack-gateway"

_TIMEOUT_S = 30.0
_EXA_LABELS = ("Title:", "URL:", "Highlights:", "Published:", "Author:")


class StandaloneError(RuntimeError):
    """All standalone vendors failed (or transport is unusable)."""


def _http_client():
    """Lazy httpx import — standalone needs it, unit tests never construct it."""
    try:
        import httpx
    except ImportError as exc:
        raise StandaloneError(
            "standalone backend requires httpx (install requirements-gateway.txt) "
            "— use backend='hermes' or 'stub' instead"
        ) from exc
    return httpx.Client(timeout=_TIMEOUT_S, headers={"User-Agent": "hermes-search-stack-gateway/0.1"})


def _parse_mcp_body(body: str) -> str:
    """First text item of an MCP ``tools/call`` response (JSON or SSE data lines)."""
    stripped = body.strip()
    candidates = [stripped] if stripped.startswith("{") else []
    candidates += [line[len("data: ") :] for line in re.split(r"\r\n|\r|\n", body) if line.startswith("data: ")]
    envelope_seen = False
    for candidate in candidates:
        try:
            data = json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
        err = data.get("error")
        if err:
            raise StandaloneError(str(err.get("message") or err))
        result = data.get("result") or {}
        texts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)]
        if result.get("isError"):
            raise StandaloneError(" ".join(t for t in texts if t) or "MCP tool call failed")
        text = next((str(t) for t in texts if t), "")
        if text:
            return text
        envelope_seen = True
    raise StandaloneError("MCP response contained no text content" if envelope_seen else "unrecognized MCP response")


def _mcp_call(client, url: str, tool: str, arguments: dict[str, Any]) -> str:
    """POST a JSON-RPC ``tools/call``; return the text payload."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    try:
        response = client.post(url, json=payload, headers=headers)
    except Exception as exc:
        raise StandaloneError(f"request to {url} failed: {exc}") from exc
    body = response.content.decode("utf-8", errors="replace")
    if response.status_code >= 400:
        raise StandaloneError(f"HTTP {response.status_code} from {url}: {body[:200]}")
    return _parse_mcp_body(body)


# --- Parallel (search.parallel.ai) — JSON text payloads ----------------------


def _parallel_search(client, query: str, limit: int) -> list[SearchItem]:
    args = {"objective": query, "search_queries": [query], "session_id": uuid.uuid4().hex}
    data = json.loads(_mcp_call(client, PARALLEL_MCP_URL, "web_search", args))
    results = data.get("results") or []
    return [
        SearchItem(
            title=r.get("title") or "",
            url=r.get("url") or "",
            description=" ".join(r.get("excerpts") or []),
            position=i + 1,
        )
        for i, r in enumerate(results[:limit] if limit else results)
        if isinstance(r, dict) and r.get("url")
    ]


def _parallel_extract(client, urls: list[str]) -> dict[str, ExtractItem]:
    args = {"urls": list(urls), "objective": "Full page content", "session_id": uuid.uuid4().hex}
    data = json.loads(_mcp_call(client, PARALLEL_MCP_URL, "web_fetch", args))
    out: dict[str, ExtractItem] = {}
    for r in data.get("results") or []:
        if not isinstance(r, dict) or not r.get("url"):
            continue
        content = r.get("full_content") or r.get("content") or "\n\n".join(r.get("excerpts") or [])
        out[r["url"]] = ExtractItem(url=r["url"], title=r.get("title") or "", content=content or "")
    for error in data.get("errors") or []:
        url = (error or {}).get("url") or ""
        if url:
            out.setdefault(url, ExtractItem(url=url, error=str(error.get("content") or "extraction failed")))
    return out


# --- Exa (mcp.exa.ai) — formatted plain-text payloads ------------------------


def _parse_exa_search_text(text: str, limit: int) -> list[SearchItem]:
    """Parse Exa's ``---``-separated ``Title:/URL:/Highlights:`` blocks."""
    items: list[SearchItem] = []
    for block in text.split("\n---\n"):
        title = url = ""
        highlights: list[str] = []
        in_highlights = False
        for stripped in map(str.strip, block.splitlines()):
            if stripped.startswith("Title:"):
                title = stripped[len("Title:") :].strip()
            elif stripped.startswith("URL:"):
                url = stripped[len("URL:") :].strip()
            elif in_highlights and stripped and not stripped.startswith(_EXA_LABELS):
                highlights.append(stripped)
            if stripped.startswith(_EXA_LABELS):
                in_highlights = stripped.startswith("Highlights:")
        if url:
            items.append(SearchItem(title=title, url=url, description=" ".join(highlights), position=len(items) + 1))
        if limit and len(items) >= limit:
            break
    return items


def _exa_search(client, query: str, limit: int) -> list[SearchItem]:
    text = _mcp_call(client, EXA_MCP_URL, "web_search_exa", {"query": query, "numResults": max(1, int(limit))})
    return _parse_exa_search_text(text, limit)


def _exa_extract(client, url: str) -> ExtractItem:
    """Per-URL Exa fetch; the tool returns one combined text payload."""
    text = _mcp_call(client, EXA_MCP_URL, "web_fetch_exa", {"urls": [url]})
    titles = (
        (s[len("# ") :] if s.startswith("# ") else s[len("Title:") :]).strip()
        for s in map(str.strip, text.splitlines())
        if s.startswith(("# ", "Title:"))
    )
    return ExtractItem(url=url, title=next(titles, ""), content=text)


# --- Keenable (api.keenable.ai public endpoints) -----------------------------


def _keenable_extract(client, url: str) -> ExtractItem:
    response = client.get(
        f"{KEENABLE_API_URL}/v1/fetch/public",
        params={"url": url},
        headers={"X-Keenable-Title": _KEENABLE_TITLE},
    )
    if response.status_code >= 400:
        raise StandaloneError(f"keenable fetch HTTP {response.status_code}")
    data = response.json()
    return ExtractItem(
        url=data.get("url") or url,
        title=data.get("title") or "",
        content=data.get("content") or "",
    )


# --- Backend -----------------------------------------------------------------


class StandaloneBackend:
    """``SearchBackend`` over direct keyless HTTP (experimental, best-effort)."""

    name = "standalone"
    _EXTRACT_RING = ("parallel", "exa", "keenable")

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = _http_client()
        return self._client

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Search via the keyless ring (parallel -> exa); first success wins."""
        client = self._get_client()
        errors: list[str] = []
        for vendor in ("parallel", "exa"):
            fn = _parallel_search if vendor == "parallel" else _exa_search
            try:
                items = fn(client, query, max_results)
            except (StandaloneError, json.JSONDecodeError, TypeError, KeyError) as exc:
                errors.append(f"{vendor}: {exc}")
                continue
            if items:
                for i, item in enumerate(items):
                    item.position = i + 1
                return items
            errors.append(f"{vendor}: no results")
        raise StandaloneError(
            "all keyless search vendors failed (" + "; ".join(errors) + "). "
            "Configure the hermes backend or set vendor API keys for reliable service."
        )

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Extract each URL via the parallel -> exa -> keenable ring."""
        client = self._get_client()
        out: list[ExtractItem] = []
        for url in urls:
            errors: list[str] = []
            item: ExtractItem | None = None
            for vendor in self._EXTRACT_RING:
                try:
                    if vendor == "parallel":
                        results = _parallel_extract(client, [url])
                        item = results.get(url)
                        if item is None:
                            raise StandaloneError("url absent from parallel response")
                    elif vendor == "exa":
                        item = _exa_extract(client, url)
                    else:
                        item = _keenable_extract(client, url)
                except (StandaloneError, json.JSONDecodeError, TypeError, KeyError) as exc:
                    errors.append(f"{vendor}: {exc}")
                    item = None
                    continue
                if item is not None and item.content.strip() and not item.error:
                    break
                why = "empty content" if item is not None and not item.error else (item.error if item else "no entry")
                errors.append(f"{vendor}: {why}")
                item = None
            if item is None:
                item = ExtractItem(url=url, error="keyless extract ring exhausted: " + "; ".join(errors))
            item.content = item.content[: max(int(char_limit), 0)]
            out.append(item)
        return out

    def ping(self) -> dict:
        """Cheap health probe: httpx availability only (no live calls)."""
        try:
            import httpx  # noqa: F401
        except ImportError:
            return {"ok": False, "detail": "httpx not installed — standalone backend unavailable"}
        return {"ok": True, "detail": "standalone keyless backend (experimental; first live call may be slow)"}

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def __enter__(self) -> StandaloneBackend:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
