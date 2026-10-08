"""gateway.providers.exa — Exa MCP provider (search + extract), R17-W2.

Keyless calls to Exa's hosted MCP server (``https://mcp.exa.ai/mcp``): search
via the ``web_search_exa`` tool, extract via ``web_fetch_exa``. Both return a
formatted plain-text payload parsed by :mod:`gateway.providers._http`.
"""

from __future__ import annotations

from gateway.protocols import ExtractItem, SearchItem
from gateway.providers._http import FetchFn, default_fetch, mcp_call, parse_exa_search_text

EXA_MCP_URL = "https://mcp.exa.ai/mcp"


class ExaProvider:
    """Exa search + extract over the hosted MCP endpoint."""

    name = "exa"
    capabilities = frozenset({"search", "extract"})

    def __init__(self, fetch: FetchFn | None = None) -> None:
        self._fetch: FetchFn = fetch or default_fetch

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Return Exa search hits for *query* (``web_search_exa``)."""
        from gateway.providers import ProviderError  # lazy: avoids an import cycle

        try:
            limit = max(1, int(max_results))
            text = mcp_call(self._fetch, EXA_MCP_URL, "web_search_exa", {"query": query, "numResults": limit})
            return parse_exa_search_text(text, max_results)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — contract: provider failures are typed
            raise ProviderError(self.name, str(exc)) from exc

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Extract each URL via ``web_fetch_exa`` (one call per URL)."""
        from gateway.providers import ProviderError  # lazy: avoids an import cycle

        try:
            out: list[ExtractItem] = []
            for url in urls:
                text = mcp_call(self._fetch, EXA_MCP_URL, "web_fetch_exa", {"urls": [url]})
                titles = (
                    (s[len("# ") :] if s.startswith("# ") else s[len("Title:") :]).strip()
                    for s in map(str.strip, text.splitlines())
                    if s.startswith(("# ", "Title:"))
                )
                out.append(ExtractItem(url=url, title=next(titles, ""), content=text[: max(int(char_limit), 0)]))
            return out
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — contract: provider failures are typed
            raise ProviderError(self.name, str(exc)) from exc


__all__ = ["EXA_MCP_URL", "ExaProvider"]
