"""Hermetic tests for the R17-W2 keyless provider implementations.

Fixtures only — zero network. Each provider is built with an injected fake
``fetch`` that asserts the expected request and returns a live-captured fixture
from ``tests/fixtures/r17/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gateway.providers import PROVIDERS, ProviderError, get_provider
from gateway.providers.bilibili import BilibiliProvider
from gateway.providers.exa import ExaProvider
from gateway.providers.jina import JinaProvider
from gateway.providers.parallel import ParallelProvider
from gateway.providers.rss import RSSProvider
from gateway.providers.v2ex import V2EXProvider
from gateway.providers.youtube import YouTubeProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "r17"


def fixture_text(name: str) -> str:
    """Read a captured R17 fixture as text."""
    return (FIXTURES / name).read_text(encoding="utf-8")


class FixtureFetch:
    """Injectable ``FetchFn``: assert the request, then return fixture text."""

    def __init__(
        self,
        url_contains: str,
        fixture: str | None = None,
        *,
        params: dict[str, Any] | None = None,
        body: str | None = None,
    ) -> None:
        self.url_contains = url_contains
        self.fixture = fixture
        self.params = dict(params or {})
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, *, method="GET", headers=None, params=None, json=None, timeout=None):
        self.calls.append(
            {"url": url, "method": method, "headers": dict(headers or {}), "params": dict(params or {}), "json": json}
        )
        assert self.url_contains in url, f"unexpected url: {url!r}"
        for key, value in self.params.items():
            assert (params or {}).get(key) == value, f"missing query param {key}={value!r} (got {params!r})"
        if self.body is not None:
            return self.body
        return fixture_text(self.fixture)

    @property
    def last(self) -> dict[str, Any]:
        """The most recent call record."""
        return self.calls[-1]

    @property
    def tool(self) -> str:
        """MCP tool name of the last call (``params.name`` of the JSON-RPC body)."""
        return str(((self.last["json"] or {}).get("params") or {}).get("name") or "")


class BoomFetch:
    """A fetch that always fails — proves provider errors are not swallowed."""

    def __call__(self, url, *, method="GET", headers=None, params=None, json=None, timeout=None):
        raise RuntimeError("boom")


def parallel_search_envelope() -> str:
    """Return a valid JSON-RPC envelope for the parallel search fixture.

    NOTE: the committed ``tests/fixtures/r17/parallel_search.txt`` is truncated
    at capture time (it ends mid-string, so it is *not* valid JSON — see the
    R17-W2 report). To still exercise the real wire shape we recover the
    complete result objects from the captured prefix; a re-captured intact
    fixture takes the verbatim path unchanged.
    """
    raw = fixture_text("parallel_search.txt")
    try:
        json.loads(raw)
        return raw  # intact fixture — nothing to repair
    except json.JSONDecodeError:
        pass
    inner = json.loads('"' + _mcp_text_body(raw) + '"')
    results = [obj for obj in _complete_json_objects(inner) if isinstance(obj, dict) and obj.get("url")]
    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps({"results": results})}]},
    }
    return json.dumps(envelope)


def _mcp_text_body(raw: str) -> str:
    """Raw (still-escaped) ``result.content[0].text`` slice of a truncated envelope."""
    marker = '"text":"'
    return raw[raw.index(marker) + len(marker) :]


def _complete_json_objects(text: str) -> list[Any]:
    """Every complete ``{...}`` object embedded in *text* (brace/string aware)."""
    objects: list[Any] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = escaped = False
        j = i
        while j < n:
            ch = text[j]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth == 0 and j < n:
            try:
                objects.append(json.loads(text[i : j + 1]))
            except json.JSONDecodeError:
                pass
            i = j + 1
        else:
            i += 1
    return objects


# --- registry -----------------------------------------------------------------


def test_registry_lists_seven_providers():
    assert sorted(PROVIDERS) == ["bilibili", "exa", "jina", "parallel", "rss", "v2ex", "youtube"]


def test_get_provider_builds_exa():
    provider = get_provider("exa")
    assert isinstance(provider, ExaProvider)
    assert provider.name == "exa"


def test_provider_capabilities():
    assert ExaProvider().capabilities == {"search", "extract"}
    assert ParallelProvider().capabilities == {"search", "extract"}
    assert JinaProvider().capabilities == {"extract"}
    assert V2EXProvider().capabilities == {"search"}
    assert BilibiliProvider().capabilities == {"search"}
    assert YouTubeProvider().capabilities == {"search"}
    assert RSSProvider().capabilities == {"search"}


# --- exa ----------------------------------------------------------------------


def test_exa_search_parses_wire_blocks():
    fetch = FixtureFetch("mcp.exa.ai", "exa_search.txt")
    items = ExaProvider(fetch).search("vietnam")
    assert fetch.tool == "web_search_exa"
    assert fetch.last["method"] == "POST"
    assert fetch.last["json"]["params"]["arguments"] == {"query": "vietnam", "numResults": 10}
    assert len(items) >= 2
    assert items[0].title and items[0].url
    assert "wikipedia.org" in items[0].url
    assert [item.position for item in items] == list(range(1, len(items) + 1))


def test_exa_extract_returns_content():
    fetch = FixtureFetch("mcp.exa.ai", "exa_fetch.txt")
    items = ExaProvider(fetch).extract(["https://example.com"])
    assert fetch.tool == "web_fetch_exa"
    assert len(items) == 1
    assert items[0].url == "https://example.com"
    assert items[0].title == "Example Domain"
    assert items[0].content.strip()


# --- parallel -----------------------------------------------------------------


def test_parallel_search_parses_results():
    fetch = FixtureFetch("search.parallel.ai", body=parallel_search_envelope())
    items = ParallelProvider(fetch).search("vietnam")
    assert fetch.tool == "web_search"
    assert len(items) >= 2
    assert all(item.url and item.title for item in items)
    assert all(item.description for item in items)
    assert [item.position for item in items] == list(range(1, len(items) + 1))


def test_parallel_extract_returns_content():
    fetch = FixtureFetch("search.parallel.ai", "parallel_fetch.txt")
    items = ParallelProvider(fetch).extract(["https://example.com"])
    assert fetch.tool == "web_fetch"
    assert len(items) == 1
    assert items[0].url == "https://example.com"
    assert items[0].error is None
    assert items[0].content.strip()


# --- jina ---------------------------------------------------------------------


def test_jina_search_raises_provider_error():
    assert JinaProvider().capabilities == {"extract"}
    with pytest.raises(ProviderError) as excinfo:
        JinaProvider().search("vietnam")
    assert excinfo.value.name == "jina"
    assert "extract-only" in str(excinfo.value)


def test_jina_extract_splits_header_and_content():
    fetch = FixtureFetch("r.jina.ai", "jina_example.md")
    items = JinaProvider(fetch).extract(["https://example.com"])
    assert fetch.last["headers"].get("Accept") == "text/plain"
    assert "User-Agent" not in fetch.last["headers"]
    assert len(items) == 1
    assert items[0].title == "Example Domain"
    assert "This domain is for use" in items[0].content


# --- v2ex ---------------------------------------------------------------------


def test_v2ex_search_maps_topics():
    fetch = FixtureFetch("sov2ex.com", "sov2ex_search.json", params={"q": "vietnam", "size": 10})
    items = V2EXProvider(fetch).search("vietnam")
    assert len(items) == 3
    assert all(item.title for item in items)
    assert all(item.url.startswith("https://www.v2ex.com/t/") for item in items)
    assert items[0].url == "https://www.v2ex.com/t/6235"


# --- bilibili -----------------------------------------------------------------


def test_bilibili_search_strips_em_tags():
    fetch = FixtureFetch("api.bilibili.com", "bilibili_search.json", params={"keyword": "vietnam", "page": 1})
    items = BilibiliProvider(fetch).search("vietnam")
    assert fetch.last["headers"].get("User-Agent")
    assert len(items) == 3
    assert all(item.url.startswith("https://www.bilibili.com/video/") for item in items)
    assert all("<em>" not in item.title and "</em>" not in item.title for item in items)
    assert items[0].title == "Thông Tin Việt Nam Tháng 10 Năm 2026"
    assert items[0].description
    assert "转载" in items[0].description


# --- youtube ------------------------------------------------------------------


def test_youtube_search_parses_video_renderers():
    fetch = FixtureFetch("youtube.com/results", "youtube_results.html", params={"search_query": "vietnam"})
    items = YouTubeProvider(fetch).search("vietnam")
    assert fetch.last["headers"].get("User-Agent")
    assert len(items) == 3
    assert all(item.url.startswith("https://www.youtube.com/watch?v=") for item in items)
    assert all(item.title for item in items)


# --- rss ----------------------------------------------------------------------


def test_rss_search_parses_feed():
    fetch = FixtureFetch(
        "news.google.com/rss/search",
        "google_news_rss.xml",
        params={"q": "vietnam", "hl": "vi", "gl": "VN", "ceid": "VN:vi"},
    )
    items = RSSProvider(fetch).search("vietnam")
    assert len(items) == 5
    assert all(item.title and item.url for item in items)


# --- error isolation ----------------------------------------------------------


def test_fetch_errors_propagate():
    """Transport failures surface as typed ProviderError (contract §1)."""
    boom = BoomFetch()
    calls = [
        (lambda: ExaProvider(boom).search("q"), "exa"),
        (lambda: ExaProvider(boom).extract(["https://example.com"]), "exa"),
        (lambda: ParallelProvider(boom).search("q"), "parallel"),
        (lambda: ParallelProvider(boom).extract(["https://example.com"]), "parallel"),
        (lambda: V2EXProvider(boom).search("q"), "v2ex"),
        (lambda: JinaProvider(boom).extract(["https://example.com"]), "jina"),
        (lambda: BilibiliProvider(boom).search("q"), "bilibili"),
        (lambda: YouTubeProvider(boom).search("q"), "youtube"),
        (lambda: RSSProvider(boom).search("q"), "rss"),
    ]
    for call, name in calls:
        with pytest.raises(ProviderError) as excinfo:
            call()
        assert excinfo.value.name == name
        assert "boom" in str(excinfo.value)
