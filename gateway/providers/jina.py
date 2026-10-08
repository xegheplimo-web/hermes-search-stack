"""gateway.providers.jina — Jina Reader provider (extract-only), R17-W2.

Keyless page extraction via Jina's public Reader (``https://r.jina.ai/<url>``).
The response is Markdown-with-header plain text: a ``Title:`` line followed by a
``Markdown Content:`` section.

CRITICAL (verified live): do **not** send a browser ``User-Agent`` — Cloudflare
challenges it; ``Accept: text/plain`` is required to get the Reader output.
"""

from __future__ import annotations

from gateway.protocols import ExtractItem, SearchItem
from gateway.providers._http import FetchFn, default_fetch

JINA_READER = "https://r.jina.ai/"
_MARKER = "Markdown Content:"


class JinaProvider:
    """Extract-only provider over the Jina Reader endpoint."""

    name = "jina"
    capabilities = frozenset({"extract"})

    def __init__(self, fetch: FetchFn | None = None) -> None:
        self._fetch: FetchFn = fetch or default_fetch

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Jina Reader cannot search — always raises :class:`ProviderError`."""
        from gateway.providers import ProviderError  # lazy: avoids an import cycle

        raise ProviderError("jina", "search not supported (extract-only provider)")

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Read each URL through the Jina Reader and split header/content."""
        out: list[ExtractItem] = []
        for url in urls:
            text = self._fetch(JINA_READER + url, headers={"Accept": "text/plain"})
            title = ""
            for line in text.splitlines():
                if line.strip().startswith("Title:"):
                    title = line.strip()[len("Title:") :].strip()
                    break
            content = text.split(_MARKER, 1)[1].lstrip("\n") if _MARKER in text else text
            out.append(ExtractItem(url=url, title=title, content=content[: max(int(char_limit), 0)]))
        return out


__all__ = ["JINA_READER", "JinaProvider"]
