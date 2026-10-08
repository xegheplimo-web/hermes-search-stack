"""gateway.providers.youtube — YouTube results-page search (search-only), R17-W2.

Pure HTTP (no ``yt-dlp``): fetch the public results page, pull the embedded
``var ytInitialData = {...};`` JSON, then walk the tree for ``videoRenderer``
nodes. A browser ``User-Agent`` is sent so the server returns the JSON island.
"""

from __future__ import annotations

import json
import re
from typing import Any

from gateway.protocols import ExtractItem, SearchItem
from gateway.providers._http import FetchFn, default_fetch

YOUTUBE_RESULTS_URL = "https://www.youtube.com/results"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_YT_INITIAL_DATA = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.DOTALL)


class YouTubeProvider:
    """Search-only provider over the YouTube results page."""

    name = "youtube"
    capabilities = frozenset({"search"})

    def __init__(self, fetch: FetchFn | None = None) -> None:
        self._fetch: FetchFn = fetch or default_fetch

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Return video hits for *query* parsed from ``ytInitialData``."""
        body = self._fetch(YOUTUBE_RESULTS_URL, params={"search_query": query}, headers={"User-Agent": BROWSER_UA})
        match = _YT_INITIAL_DATA.search(body)
        if not match:
            raise RuntimeError("youtube: ytInitialData not found in results page")
        renderers: list[dict[str, Any]] = []
        _walk_video_renderers(json.loads(match.group(1)), renderers)
        items: list[SearchItem] = []
        for renderer in renderers:
            video_id = renderer.get("videoId")
            title = _first_run_text((renderer.get("title") or {}).get("runs"))
            if not video_id or not title:
                continue
            items.append(
                SearchItem(
                    title=title,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    description=_description(renderer),
                    position=len(items) + 1,
                )
            )
            if max_results and len(items) >= max_results:
                break
        return items

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Not supported — YouTube is a search-only provider."""
        from gateway.providers import ProviderError  # lazy: avoids an import cycle

        raise ProviderError("youtube", "extract not supported (search-only provider)")


def _walk_video_renderers(node: Any, out: list[dict[str, Any]]) -> None:
    """Collect every ``videoRenderer`` dict found anywhere in *node*."""
    if isinstance(node, dict):
        renderer = node.get("videoRenderer")
        if isinstance(renderer, dict):
            out.append(renderer)
        for value in node.values():
            _walk_video_renderers(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_video_renderers(value, out)


def _first_run_text(runs: Any) -> str:
    """First non-empty ``text`` of a ``runs`` list (else ``""``)."""
    if isinstance(runs, list):
        for run in runs:
            if isinstance(run, dict) and run.get("text"):
                return str(run["text"])
    return ""


def _description(renderer: dict[str, Any]) -> str:
    """Best-effort description: channel byline, else duration, else ``""``."""
    byline = _first_run_text((renderer.get("shortBylineText") or {}).get("runs"))
    if byline:
        return byline
    return str((renderer.get("lengthText") or {}).get("simpleText") or "")


__all__ = ["BROWSER_UA", "YOUTUBE_RESULTS_URL", "YouTubeProvider"]
