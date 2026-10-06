"""Deterministic canned backend used by tests and offline development.

``StubBackend`` returns configurable ``SearchItem`` lists and echoes
deterministic extract content derived from each URL. It performs no network
calls and records its invocations so tests can assert call shapes.
"""

from __future__ import annotations

from dataclasses import replace

from gateway.protocols import ExtractItem, SearchItem


def default_items(n: int = 12) -> list[SearchItem]:
    """Canned search hits (stable titles/urls, ``position`` assigned per call)."""
    return [
        SearchItem(
            title=f"Stub Result {i}",
            url=f"https://stub.example/article-{i}",
            description=f"Deterministic stub snippet {i}.",
        )
        for i in range(1, n + 1)
    ]


class StubBackend:
    """A ``SearchBackend`` that never leaves the process."""

    name = "stub"

    def __init__(
        self,
        *,
        items: list[SearchItem] | None = None,
        content_len: int = 600,
        extract_errors: dict[str, str] | None = None,
        healthy: bool = True,
        fail_search: bool = False,
        fail_extract: bool = False,
    ):
        self.items = list(items) if items is not None else default_items()
        self.content_len = content_len
        self.extract_errors = dict(extract_errors or {})
        self.healthy = healthy
        self.fail_search = fail_search
        self.fail_extract = fail_extract
        self.search_calls: list[tuple[str, int]] = []
        self.extract_calls: list[tuple[list[str], int]] = []

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        self.search_calls.append((query, max_results))
        if self.fail_search:
            raise RuntimeError("stub backend search failure (configured)")
        limit = max(int(max_results), 0)
        return [replace(item, position=i + 1) for i, item in enumerate(self.items[:limit])]

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        self.extract_calls.append((list(urls), char_limit))
        if self.fail_extract:
            raise RuntimeError("stub backend extract failure (configured)")
        out: list[ExtractItem] = []
        for url in urls:
            if url in self.extract_errors:
                out.append(ExtractItem(url=url, error=self.extract_errors[url]))
                continue
            content = self._content_for(url)[: max(int(char_limit), 0)]
            out.append(ExtractItem(url=url, title=self._title_for(url), content=content))
        return out

    def ping(self) -> dict:
        return {"ok": self.healthy, "detail": "stub backend (deterministic, offline)"}

    # ---------- helpers ----------

    def _title_for(self, url: str) -> str:
        for item in self.items:
            if item.url == url:
                return item.title
        return f"Stub page {url}"

    def _content_for(self, url: str) -> str:
        title = self._title_for(url)
        seed = f"Stub extract content for {url} ({title}). "
        repeats = self.content_len // len(seed) + 1
        return (seed * repeats)[: self.content_len]
