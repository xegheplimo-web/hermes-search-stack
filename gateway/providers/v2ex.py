"""gateway.providers.v2ex — V2EX search via sov2ex (search-only), R17-W2.

V2EX has no keyless search API; the community ``sov2ex`` service
(``https://www.sov2ex.com/api/search``) exposes a JSON Elasticsearch-style
result set (``hits[]._source``).
"""

from __future__ import annotations

import json

from gateway.protocols import ExtractItem, SearchItem
from gateway.providers._http import FetchFn, default_fetch

SOV2EX_URL = "https://www.sov2ex.com/api/search"


class V2EXProvider:
    """Search-only provider over the sov2ex API."""

    name = "v2ex"
    capabilities = frozenset({"search"})

    def __init__(self, fetch: FetchFn | None = None) -> None:
        self._fetch: FetchFn = fetch or default_fetch

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Return V2EX topic hits for *query* via sov2ex."""
        size = max(1, int(max_results))
        data = json.loads(self._fetch(SOV2EX_URL, params={"q": query, "size": size}))
        items: list[SearchItem] = []
        for hit in data.get("hits") or []:
            src = (hit or {}).get("_source") or {}
            topic_id = src.get("id")
            if topic_id is None:
                continue
            items.append(
                SearchItem(
                    title=str(src.get("title") or ""),
                    url=f"https://www.v2ex.com/t/{topic_id}",
                    description=(src.get("content") or "")[:200],
                    position=len(items) + 1,
                )
            )
            if len(items) >= size:
                break
        return items

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Not supported — V2EX is a search-only provider."""
        from gateway.providers import ProviderError  # lazy: avoids an import cycle

        raise ProviderError("v2ex", "extract not supported (search-only provider)")


__all__ = ["SOV2EX_URL", "V2EXProvider"]
