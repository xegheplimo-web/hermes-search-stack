"""gateway.providers.bilibili — Bilibili web search (search-only), R17-W2.

Keyless Bilibili search (``/x/web-interface/search/all/v2``); the endpoint
requires a browser ``User-Agent``. Titles carry ``<em>`` highlight tags that
are stripped (and entities unescaped) before building the hit.
"""

from __future__ import annotations

import html
import json
import re

from gateway.protocols import ExtractItem, SearchItem
from gateway.providers._http import FetchFn, default_fetch

BILIBILI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/all/v2"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_EM_TAG = re.compile(r"</?em[^>]*>")
_ARCURL_ID = re.compile(r"/video/([^/?#]+)")


class BilibiliProvider:
    """Search-only provider over the Bilibili web search API."""

    name = "bilibili"
    capabilities = frozenset({"search"})

    def __init__(self, fetch: FetchFn | None = None) -> None:
        self._fetch: FetchFn = fetch or default_fetch

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Return video hits for *query* from the Bilibili ``video`` group."""
        from gateway.providers import ProviderError  # lazy: avoids an import cycle

        try:
            body = self._fetch(
                BILIBILI_SEARCH_URL,
                params={"keyword": query, "page": 1},
                headers={"User-Agent": BROWSER_UA},
            )
            payload = json.loads(body)
            if payload.get("code"):
                raise ProviderError(self.name, f"bilibili api code {payload.get('code')}: {payload.get('message')}")
            groups = (payload.get("data") or {}).get("result") or []
            video_group = next((g for g in groups if isinstance(g, dict) and g.get("result_type") == "video"), None)
            items: list[SearchItem] = []
            for entry in (video_group or {}).get("data") or []:
                if not isinstance(entry, dict):
                    continue
                url = _video_url(entry)
                if not url:
                    continue
                title = html.unescape(_EM_TAG.sub("", str(entry.get("title") or ""))).strip()
                items.append(
                    SearchItem(
                        title=title,
                        url=url,
                        description=str(entry.get("description") or entry.get("desc") or entry.get("author") or ""),
                        position=len(items) + 1,
                    )
                )
                if max_results and len(items) >= max_results:
                    break
            return items
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — contract: provider failures are typed
            raise ProviderError(self.name, str(exc)) from exc

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Not supported — Bilibili is a search-only provider."""
        from gateway.providers import ProviderError  # lazy: avoids an import cycle

        try:
            raise ProviderError("bilibili", "extract not supported (search-only provider)")
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — contract: provider failures are typed
            raise ProviderError(self.name, str(exc)) from exc


def _video_url(entry: dict) -> str:
    """``https://www.bilibili.com/video/<bvid>`` (fallback: parse ``arcurl``)."""
    bvid = str(entry.get("bvid") or "").strip()
    if bvid:
        return "https://www.bilibili.com/video/" + bvid
    match = _ARCURL_ID.search(str(entry.get("arcurl") or ""))
    return "https://www.bilibili.com/video/" + match.group(1) if match else ""


__all__ = ["BILIBILI_SEARCH_URL", "BROWSER_UA", "BilibiliProvider"]
