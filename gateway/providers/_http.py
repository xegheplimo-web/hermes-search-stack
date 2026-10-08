"""gateway.providers._http — shared fetch seam + MCP helpers (R17-W2).

Small, dependency-light helpers reused by every keyless provider. The fetch
callable is the *only* network seam: providers accept an injectable
:data:`FetchFn` so tests stay hermetic (fixtures in ``tests/fixtures/r17/``).

- :data:`FetchFn` / :func:`default_fetch` — one request -> response text
  (``httpx`` imported lazily so the gateway package imports without the extra).
- :func:`parse_mcp_body` / :func:`mcp_call` — JSON-RPC ``tools/call`` over the
  MCP HTTP transport (JSON *or* SSE ``data:`` bodies).
- :func:`parse_exa_search_text` — Exa's ``---``-separated ``Title:/URL:/…``
  plain-text blocks.

The MCP call+parse logic mirrors ``gateway/backends/standalone.py`` (copied on
purpose — the standalone backend is untouched this round; unification is
deferred).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from gateway.protocols import SearchItem

#: Injectable fetch seam: ``fetch(url, *, method, headers, params, json, timeout)``
#: -> response text. Providers default to :func:`default_fetch`.
FetchFn = Callable[..., str]

_TIMEOUT_S = 30.0
_EXA_LABELS = ("Title:", "URL:", "Highlights:", "Published:", "Author:")


def default_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,
    timeout: float = _TIMEOUT_S,
) -> str:
    """Default :data:`FetchFn`: one ``httpx`` request, decoded to text.

    ``httpx`` is imported lazily (it is an optional gateway extra); non-2xx
    responses raise :class:`RuntimeError` with the status and a body preview.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx ships in requirements-gateway.txt
        raise RuntimeError("provider HTTP fetch requires httpx (install requirements-gateway.txt)") from exc
    try:
        response = httpx.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001 — transport errors surface as RuntimeError
        raise RuntimeError(f"request to {url} failed: {exc}") from exc
    body = response.content.decode("utf-8", errors="replace")
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code} from {url}: {body[:200]}")
    return body


def parse_mcp_body(body: str) -> str:
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
            raise RuntimeError(str(err.get("message") or err))
        result = data.get("result") or {}
        texts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)]
        if result.get("isError"):
            raise RuntimeError(" ".join(t for t in texts if t) or "MCP tool call failed")
        text = next((str(t) for t in texts if t), "")
        if text:
            return text
        envelope_seen = True
    raise RuntimeError("MCP response contained no text content" if envelope_seen else "unrecognized MCP response")


def mcp_call(fetch: FetchFn, url: str, tool: str, arguments: dict[str, Any]) -> str:
    """POST a JSON-RPC ``tools/call`` to *url*; return the text payload."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    body = fetch(url, method="POST", headers=headers, json=payload)
    return parse_mcp_body(body)


def parse_exa_search_text(text: str, limit: int) -> list[SearchItem]:
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


__all__ = ["FetchFn", "default_fetch", "mcp_call", "parse_exa_search_text", "parse_mcp_body"]
