"""R17-W3A tests: caller context evidence + ``hermes_social`` (r17 §4-5).

Engine: ``context=[{title,url,content}]`` entries append AFTER fetched
evidence as ordinary ``origin="caller"`` items (ids renumbered 1..N, host
trust-scored, local prepend still leads). Tools: ``hermes_research``
forwards context and echoes ``origin``; ``hermes_social`` routes to one
keyless provider with the same never-raise isolation. Hermetic — stub
backend/synth, monkeypatched provider registry, tmp dbs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import gateway.providers as providers
from gateway.backends.stub import StubBackend
from gateway.config import GatewayConfig
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine
from gateway.core.local_context import VN_GEO_DB_ENV
from gateway.mcp.tools import FROZEN_TOOL_PARAMS, TOOL_NAMES, make_tools
from gateway.protocols import SearchItem
from gateway.providers import ProviderError
from tests.gateway.conftest import FakeSynthesizer
from tests.gateway.test_deadline import SlowSearchBackend
from tests.gateway.test_engine import _verified_pack
from vn_geo import business

_CTX = [
    {"title": "Caller Doc A", "url": "https://caller.example/a", "content": "caller alpha"},
    {"title": "Caller Doc B", "url": "https://caller.example/b", "content": "caller beta"},
]


def _sequential_ids(result) -> bool:
    return [s.id for s in result.sources] == list(range(1, len(result.sources) + 1))


# ---------------------------------------------------------------------------
# Engine — context append / renumber / origin
# ---------------------------------------------------------------------------


def test_context_appended_after_fetched_and_renumbered(stub_engine):
    result = stub_engine.run("weather in Hanoi today", context=list(_CTX))
    urls = [s.url for s in result.sources]
    assert urls[-2:] == ["https://caller.example/a", "https://caller.example/b"]
    assert all(u.startswith("https://stub.example/") for u in urls[:-2])
    assert _sequential_ids(result)


def test_context_sources_carry_caller_origin(stub_engine):
    result = stub_engine.run("weather in Hanoi today", context=list(_CTX))
    caller = [s for s in result.sources if s.origin == "caller"]
    assert [s.url for s in caller] == ["https://caller.example/a", "https://caller.example/b"]
    assert all(s.origin is None for s in result.sources[:-2])


def test_context_urls_get_host_trust_scores(stub_engine):
    """§5: caller entries are ordinary evidence — scored by host, no new tier."""
    result = stub_engine.run("weather in Hanoi today", context=list(_CTX))
    caller = [s for s in result.sources if s.origin == "caller"]
    assert caller and all(s.trust_score is not None for s in caller)


def test_context_order_local_fetched_context(tmp_path, monkeypatch):
    """Final order is [local...][fetched...][caller-context...] (r17 §4)."""
    db = tmp_path / "vn-geo.db"
    eid = business.upsert_entity(
        str(db),
        {
            "name": "Nhà nghỉ Bảo An",
            "address_text": "xã Nham Biền, huyện Yên Dũng, Bắc Giang",
            "area_old": "Yên Dũng",
            "province": "Bắc Giang",
            "lat": 21.2,
            "lng": 106.2,
            "source": "gmaps",
            "category": "food",
            "kind": "food",
            "status": "open",
            "geocode_status": "exact",
        },
    )
    monkeypatch.setenv(VN_GEO_DB_ENV, str(db))
    cfg = GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
    )
    engine = Engine(cfg, backend=StubBackend(), synth=FakeSynthesizer(), cache=GatewayCache(cfg.cache_db_path))
    result = engine.run("nha nghi bao an yen dung", depth="fast", context=list(_CTX))
    urls = [s.url for s in result.sources]
    assert urls[0] == f"local://vn-geo/{eid}"
    assert urls[-2:] == ["https://caller.example/a", "https://caller.example/b"]
    assert all(u.startswith("https://stub.example/") for u in urls[1:-2])
    assert _sequential_ids(result)
    assert result.sources[-1].origin == "caller"


def test_run_iter_accepts_context(stub_engine):
    done = [
        e["result"] for e in stub_engine.run_iter("weather in Hanoi today", context=list(_CTX)) if e["type"] == "done"
    ]
    assert done and done[0].sources[-1].origin == "caller"


# ---------------------------------------------------------------------------
# Engine — malformed context degrades to warnings, never raises
# ---------------------------------------------------------------------------


def test_context_invalid_entries_skipped_with_warnings(stub_engine):
    context = [
        "not-a-dict",
        {"title": "", "url": "https://caller.example/x", "content": "c"},
        {"title": "No URL", "content": "c"},
        {"title": "Good", "url": "https://caller.example/good", "content": "ok"},
        {"title": "No Content", "url": "https://caller.example/nc"},
    ]
    result = stub_engine.run("weather in Hanoi today", context=context)
    for i in (0, 1, 2, 4):
        assert any(f"context entry {i} ignored" in w for w in result.warnings), i
    caller = [s for s in result.sources if s.origin == "caller"]
    assert [s.url for s in caller] == ["https://caller.example/good"]
    assert _sequential_ids(result)


def test_context_non_list_warns_and_noops(stub_engine):
    context = {"title": "T", "url": "https://caller.example/x", "content": "c"}
    result = stub_engine.run("weather in Hanoi today", context=context)  # type: ignore[arg-type]
    assert any("context ignored (not a list)" in w for w in result.warnings)
    assert all(s.origin != "caller" for s in result.sources)


@pytest.mark.parametrize("context", [None, []])
def test_context_none_and_empty_are_silent_noops(stub_engine, context):
    result = stub_engine.run("weather in Hanoi today", context=context)
    assert not any("context" in w for w in result.warnings)
    assert all(s.origin != "caller" for s in result.sources)


def test_context_never_breaks_query_on_trust_failure(stub_engine, monkeypatch):
    """A broken ``trust`` import warns; the caller evidence still appends."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "trust":
            raise ImportError("simulated absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = stub_engine.run("weather in Hanoi today", context=list(_CTX))
    assert any("context trust scoring unavailable" in w for w in result.warnings)
    assert [s.url for s in result.sources if s.origin == "caller"] == [
        "https://caller.example/a",
        "https://caller.example/b",
    ]


# ---------------------------------------------------------------------------
# Tools — hermes_research context forwarding + origin passthrough
# ---------------------------------------------------------------------------


class _RecordingEngine:
    """Stub engine that records ``run()`` kwargs and returns canned sources."""

    def __init__(self, tmp_path):
        self.backend = None
        self.config = SimpleNamespace(store_db=str(tmp_path / "s.db"), repo_root=str(tmp_path))
        self.calls: list[dict] = []

    def run(self, query, *, depth="auto", allow_cache=True, context=None):
        self.calls.append({"query": query, "depth": depth, "context": context})
        return SimpleNamespace(
            answer_markdown="a",
            sources=[
                SimpleNamespace(id=1, title="Web", url="https://web.example/1", origin=None),
                SimpleNamespace(id=2, title="Caller", url="https://caller.example/a", origin="caller"),
            ],
            depth="fast",
            cached=False,
            warnings=[],
        )


def test_hermes_research_forwards_context_and_origin(tmp_path):
    engine = _RecordingEngine(tmp_path)
    tools = make_tools(engine)
    out = tools["hermes_research"]("q", context=list(_CTX))
    assert engine.calls == [{"query": "q", "depth": "auto", "context": _CTX}]
    assert out["sources"][0] == {"id": 1, "title": "Web", "url": "https://web.example/1"}
    assert out["sources"][1]["origin"] == "caller"
    assert "origin" not in out["sources"][0]


def test_hermes_research_context_default_is_none(tmp_path):
    engine = _RecordingEngine(tmp_path)
    tools = make_tools(engine)
    tools["hermes_research"]("q")
    assert engine.calls[0]["context"] is None


def test_hermes_research_context_must_be_list(tmp_path):
    tools = make_tools(_RecordingEngine(tmp_path))
    out = tools["hermes_research"]("q", context={"title": "x"})  # type: ignore[arg-type]
    assert out["error"] == "context must be a list of {title,url,content} dicts"
    assert out["sources"] == [] and out["answer_markdown"] == ""


# ---------------------------------------------------------------------------
# Tools — hermes_social provider routing + isolation
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, items=None, error: Exception | None = None):
        self._items = items or []
        self._error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query, *, max_results=10):
        self.calls.append((query, max_results))
        if self._error is not None:
            raise self._error
        return self._items


def _patch_registry(monkeypatch, provider, seen: dict):
    def fake_get_provider(name):
        seen["name"] = name
        return provider

    monkeypatch.setattr(providers, "get_provider", fake_get_provider)


def test_hermes_social_routes_and_formats(tmp_path, monkeypatch):
    provider = _FakeProvider(
        items=[
            SimpleNamespace(title="Thread", url="https://v2ex.test/t/1", description="d", position=1),
            {"title": "Clip", "url": "https://v2ex.test/t/2", "description": "d2", "position": 2},
        ]
    )
    seen: dict = {}
    _patch_registry(monkeypatch, provider, seen)
    tools = make_tools(_RecordingEngine(tmp_path))
    out = tools["hermes_social"]("vietnam coffee", "v2ex")
    assert seen["name"] == "v2ex"
    assert provider.calls == [("vietnam coffee", 10)]
    assert out == {
        "platform": "v2ex",
        "results": [
            {"title": "Thread", "url": "https://v2ex.test/t/1", "description": "d", "position": 1},
            {"title": "Clip", "url": "https://v2ex.test/t/2", "description": "d2", "position": 2},
        ],
    }


def test_hermes_social_unsupported_platform(tmp_path):
    tools = make_tools(_RecordingEngine(tmp_path))
    out = tools["hermes_social"]("q", "tiktok")
    assert out == {
        "platform": "tiktok",
        "results": [],
        "error": "unsupported platform 'tiktok' (use v2ex|bilibili|youtube|rss)",
    }


def test_hermes_social_provider_error_is_structured(tmp_path, monkeypatch):
    provider = _FakeProvider(error=ProviderError("v2ex", "HTTP 503"))
    _patch_registry(monkeypatch, provider, {})
    tools = make_tools(_RecordingEngine(tmp_path))
    out = tools["hermes_social"]("q", "v2ex")
    assert out["results"] == []
    assert "provider 'v2ex': HTTP 503" in out["error"]


def test_hermes_social_get_provider_error_is_structured(tmp_path, monkeypatch):
    def raise_provider(name):
        raise ProviderError(name, "unknown provider")

    monkeypatch.setattr(providers, "get_provider", raise_provider)
    tools = make_tools(_RecordingEngine(tmp_path))
    out = tools["hermes_social"]("q", "rss")
    assert out["results"] == [] and out["platform"] == "rss"
    assert "error" in out


# ---------------------------------------------------------------------------
# Registry — additive 8th tool
# ---------------------------------------------------------------------------


def test_tool_names_and_params_have_eight_entries(tmp_path):
    assert TOOL_NAMES[-1] == "hermes_social"
    assert len(TOOL_NAMES) == 8
    assert FROZEN_TOOL_PARAMS["hermes_social"] == {"required": ("query", "platform"), "defaults": {}}
    assert FROZEN_TOOL_PARAMS["hermes_research"]["defaults"] == {"depth": "auto", "context": None}
    tools = make_tools(_RecordingEngine(tmp_path))
    assert sorted(tools) == sorted(TOOL_NAMES)


# ---------------------------------------------------------------------------
# Engine — fix-round regressions (deadline append, trust collision, cache bypass)
# ---------------------------------------------------------------------------


def test_context_survives_spent_deadline(tmp_path):
    """FIX-F1: a deadline-past run still returns caller evidence.

    The append costs no I/O, so caller context is part of a partial answer;
    only the local lookup stays deadline-guarded.
    """
    cfg = GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
        request_deadline_s=0.05,
    )
    engine = Engine(
        cfg,
        backend=SlowSearchBackend(0.4),  # probe alone exceeds the deadline
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )
    result = engine.run("weather in Hanoi today", context=list(_CTX))
    assert result.timings_ms["deadline_exceeded"] == 1
    assert "local_hits" not in result.timings_ms  # local lookup stays deadline-guarded
    caller = [s for s in result.sources if s.origin == "caller"]
    assert [s.url for s in caller] == ["https://caller.example/a", "https://caller.example/b"]
    assert _sequential_ids(result)


def test_context_url_never_overwrites_fetched_trust(cfg, monkeypatch):
    """FIX-F2: a caller passage must not upgrade a snippet-only fetched score."""
    import trust

    def fake_score(sources):
        return {"sources": [{"url": s["url"], "score": 0.11 if s.get("snippet_only") else 0.99} for s in sources]}

    monkeypatch.setattr(trust, "score_sources", fake_score)
    engine = Engine(
        cfg,
        backend=StubBackend(
            items=[SearchItem(title="Dup", url="https://dup.example/x", description="")],
            fail_extract=True,  # -> snippet-only fetched evidence (score 0.11)
        ),
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )
    result = engine.run(
        "weather in Hanoi today",
        depth="fast",
        context=[{"title": "Caller Dup", "url": "https://dup.example/x", "content": "full caller content"}],
    )
    dup = [s for s in result.sources if s.url == "https://dup.example/x"]
    assert len(dup) == 2 and {s.origin for s in dup} == {None, "caller"}
    assert all(s.trust_score == 0.11 for s in dup)  # fetched record preserved


def test_context_bypasses_fresh_cache_hit(stub_engine, cfg):
    """FIX-F3a: a cached answer must not be served to a context-scoped run."""
    GatewayCache(cfg.cache_db_path).put(_verified_pack("weather in Hanoi today"))
    result = stub_engine.run("weather in Hanoi today", context=list(_CTX))
    assert result.cached is False
    assert result.depth in ("fast", "deep")
    assert stub_engine._backend.search_calls  # the run actually proceeded
    assert [s.url for s in result.sources if s.origin == "caller"] == [
        "https://caller.example/a",
        "https://caller.example/b",
    ]


def test_contextual_deep_result_not_published(stub_engine, cfg):
    """FIX-F3b: a context-scoped deep answer must not enter the cache."""
    query = "Compare Alpha vs Beta in detail"
    result = stub_engine.run(query, context=list(_CTX))
    assert result.depth == "deep"
    assert "verify_ms" not in result.timings_ms  # publish stage skipped
    assert [s.url for s in result.sources if s.origin == "caller"] == [
        "https://caller.example/a",
        "https://caller.example/b",
    ]
    assert GatewayCache(cfg.cache_db_path).get(query) is None
    plain = stub_engine.run(query)
    assert plain.cached is False
