"""gateway.providers.parallel — Parallel.ai MCP provider (search + extract), R17-W2.

Keyless calls to Parallel's hosted MCP server (``https://search.parallel.ai/mcp``):
search via the ``web_search`` tool, extract via ``web_fetch``. Both tools return a
JSON *text* payload (a JSON-RPC envelope whose ``result.content[0].text`` is itself
a JSON document), so the flow is ``mcp_call`` -> ``json.loads``.
"""

from __future__ import annotations

import json
import uuid

from gateway.protocols import ExtractItem, SearchItem
from gateway.providers._http import FetchFn, default_fetch, mcp_call

PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"


class ParallelProvider:
    """Parallel.ai search + extract over the hosted MCP endpoint."""

    name = "parallel"
    capabilities = frozenset({"search", "extract"})

    def __init__(self, fetch: FetchFn | None = None) -> None:
        self._fetch: FetchFn = fetch or default_fetch

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Return Parallel search hits for *query* (``web_search``)."""
        args = {"objective": query, "search_queries": [query], "session_id": uuid.uuid4().hex}
        data = json.loads(mcp_call(self._fetch, PARALLEL_MCP_URL, "web_search", args))
        items: list[SearchItem] = []
        for r in data.get("results") or []:
            if not isinstance(r, dict) or not r.get("url"):
                continue
            items.append(
                SearchItem(
                    title=r.get("title") or "",
                    url=r.get("url") or "",
                    description=" ".join(r.get("excerpts") or []),
                    position=len(items) + 1,
                )
            )
            if max_results and len(items) >= max_results:
                break
        return items

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Extract each URL via ``web_fetch`` (one call per URL)."""
        out: list[ExtractItem] = []
        for url in urls:
            args = {"urls": [url], "objective": "Full page content", "session_id": uuid.uuid4().hex}
            data = json.loads(mcp_call(self._fetch, PARALLEL_MCP_URL, "web_fetch", args))
            item = self._pick_result(url, data)
            item.content = item.content[: max(int(char_limit), 0)]
            out.append(item)
        return out

    @staticmethod
    def _pick_result(url: str, data: dict) -> ExtractItem:
        """Map one ``web_fetch`` payload to an :class:`ExtractItem` for *url*."""
        results = [r for r in data.get("results") or [] if isinstance(r, dict) and r.get("url")]
        match = next((r for r in results if r["url"] == url), results[0] if results else None)
        if match is not None:
            content = match.get("full_content") or match.get("content") or "\n\n".join(match.get("excerpts") or [])
            return ExtractItem(url=url, title=match.get("title") or "", content=content or "")
        for error in data.get("errors") or []:
            err = error or {}
            if err.get("url") == url:
                return ExtractItem(url=url, error=str(err.get("content") or "extraction failed"))
        return ExtractItem(url=url, error="url absent from parallel response")


__all__ = ["PARALLEL_MCP_URL", "ParallelProvider"]
