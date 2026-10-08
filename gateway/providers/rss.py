"""gateway.providers.rss — Google News RSS search (search-only), R17-W2.

Uses defusedxml's hardened ElementTree (no ``feedparser`` dependency) to read
Google News' RSS search feed (``https://news.google.com/rss/search``). Parsing
untrusted XML with the raw stdlib parser trips Bandit B314 — defusedxml is the
drop-in fix and ships in ``requirements-gateway.txt``.
"""

from __future__ import annotations

from gateway.protocols import ExtractItem, SearchItem
from gateway.providers._http import FetchFn, default_fetch

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"


class RSSProvider:
    """Search-only provider over the Google News RSS feed."""

    name = "rss"
    capabilities = frozenset({"search"})

    def __init__(self, fetch: FetchFn | None = None) -> None:
        self._fetch: FetchFn = fetch or default_fetch

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Return news hits for *query* from the Google News RSS feed."""
        try:
            from defusedxml.ElementTree import fromstring as _xml_fromstring
        except ImportError as exc:  # pragma: no cover - defusedxml ships in requirements-gateway.txt
            raise RuntimeError("rss provider requires defusedxml (install requirements-gateway.txt)") from exc
        body = self._fetch(
            GOOGLE_NEWS_RSS_URL,
            params={"q": query, "hl": "vi", "gl": "VN", "ceid": "VN:vi"},
        )
        items: list[SearchItem] = []
        for item in _xml_fromstring(body).iter("item"):
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            if not title and not url:
                continue
            source = (item.findtext("source") or "").strip()
            published = (item.findtext("pubDate") or "").strip()
            items.append(
                SearchItem(
                    title=title,
                    url=url,
                    description=" ".join(part for part in (source, published) if part),
                    position=len(items) + 1,
                )
            )
            if max_results and len(items) >= max_results:
                break
        return items

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Not supported — RSS is a search-only provider."""
        from gateway.providers import ProviderError  # lazy: avoids an import cycle

        raise ProviderError("rss", "extract not supported (search-only provider)")


__all__ = ["GOOGLE_NEWS_RSS_URL", "RSSProvider"]
