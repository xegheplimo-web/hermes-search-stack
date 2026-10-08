"""Engine provider fan-out tests (R17-W1B; analysis/r17-interfaces.md §2).

Hermetic: fake providers registered through ``PROVIDERS`` + ``StubBackend``
+ ``FakeSynthesizer`` — zero network. ``PROVIDERS`` is monkeypatched per
test and restored automatically; the conftest autouse fixture keeps the
vn-geo db absent (C2 merge is a no-op). The stub backend returns 3 items
so provider URLs stay under the ``deep_extract`` cap and reach evidence.
"""

from __future__ import annotations

import gateway.providers as providers_mod
from gateway.backends.stub import StubBackend
from gateway.config import GatewayConfig
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine, _normalize_url
from gateway.protocols import ExtractItem, SearchItem
from gateway.providers.fanout import ProviderFanoutBackend
from tests.gateway.conftest import FakeSynthesizer

COMPARATIVE = "Compare Alpha vs Beta in detail"
MULTIPART = "what is alpha thing? and what is beta thing?"
SUB_A = "what is alpha thing"
SUB_B = "what is beta thing"


def _provider_items(prefix: str, n: int = 2) -> list[SearchItem]:
    return [
        SearchItem(
            title=f"{prefix} Result {i}",
            url=f"https://{prefix}.example/{i}",
            description=f"{prefix} snippet {i}.",
        )
        for i in range(1, n + 1)
    ]


class FakeProvider:
    """Provider double: records queries, canned items, optional failure."""

    capabilities = frozenset({"search"})

    def __init__(self, name: str, items: list[SearchItem], *, fail: bool = False, calls: list | None = None):
        self.name = name
        self._items = list(items)
        self.fail = fail
        self.calls = calls if calls is not None else []

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        self.calls.append(query)
        if self.fail:
            raise RuntimeError("boom")
        return list(self._items)

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        return []


class ExtractOnlyProvider:
    """``capabilities={"extract"}`` — must never see a search call (v1)."""

    name = "extract_only"
    capabilities = frozenset({"extract"})

    def __init__(self):
        self.calls: list[str] = []
        self.extract_calls: list[list[str]] = []

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        self.calls.append(query)
        return _provider_items("extract_only")

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        self.extract_calls.append(list(urls))
        return []


def _register(monkeypatch, **providers) -> None:
    """Register provider instances as ``PROVIDERS`` factories."""
    for name, provider in providers.items():
        monkeypatch.setitem(providers_mod.PROVIDERS, name, lambda p=provider: p)


def _stub_backend(n: int = 3) -> StubBackend:
    """3-item stub: provider URLs stay under the ``deep_extract`` cap."""
    return StubBackend(
        items=[SearchItem(title=f"Stub Result {i}", url=f"https://stub.example/article-{i}") for i in range(1, n + 1)]
    )


def _fanout_engine(tmp_path, *, backend=None, **cfg_over) -> Engine:
    cfg = GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "searchstore.db"),
        repo_root=str(tmp_path),
        **{"providers_enabled": True, "providers": "fake1,fake2", **cfg_over},
    )
    return Engine(
        cfg,
        backend=backend if backend is not None else _stub_backend(),
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )


# ---------- ProviderFanoutBackend adapter (unit) ----------


def test_fanout_backend_merges_positions_ping_extract():
    warnings: list[str] = []
    p1 = FakeProvider("p1", _provider_items("a"), calls=[])
    p2 = FakeProvider("p2", _provider_items("b", 1), calls=[])
    backend = ProviderFanoutBackend([p1, p2], warnings=warnings)
    items = backend.search("q", max_results=5)
    assert [it.url for it in items] == ["https://a.example/1", "https://a.example/2", "https://b.example/1"]
    assert [it.position for it in items] == [1, 2, 3]
    assert warnings == []
    assert backend.name == "providers"
    assert backend.ping() == {"ok": True, "detail": "provider fan-out (2 providers)"}
    extracts = backend.extract(["https://x.example/1"], char_limit=100)
    assert extracts == [ExtractItem(url="https://x.example/1", error="provider extract not supported (v1)")]


# ---------- engine fan-out (deep path only) ----------


def test_disabled_fanout_never_runs(tmp_path, monkeypatch):
    fake1 = FakeProvider("fake1", _provider_items("fake1"), calls=[])
    fake2 = FakeProvider("fake2", _provider_items("fake2"), calls=[])
    _register(monkeypatch, fake1=fake1, fake2=fake2)
    engine = _fanout_engine(tmp_path, providers_enabled=False)
    result = engine.run(COMPARATIVE, depth="deep")
    assert result.depth == "deep"
    assert fake1.calls == [] and fake2.calls == []
    assert not any("fan-out" in w for w in result.warnings)


def test_fast_depth_never_fans_out(tmp_path, monkeypatch):
    fake1 = FakeProvider("fake1", _provider_items("fake1"), calls=[])
    _register(monkeypatch, fake1=fake1)
    engine = _fanout_engine(tmp_path, providers="fake1")
    result = engine.run("weather in Hanoi today", depth="fast")
    assert result.depth == "fast"
    assert fake1.calls == []


def test_provider_urls_merge_into_evidence(tmp_path, monkeypatch):
    fake1 = FakeProvider("fake1", _provider_items("fake1"), calls=[])
    fake2 = FakeProvider("fake2", _provider_items("fake2"), calls=[])
    _register(monkeypatch, fake1=fake1, fake2=fake2)
    engine = _fanout_engine(tmp_path)
    events = list(engine.run_iter(COMPARATIVE, depth="deep"))
    assert events[0]["type"] == "route"
    assert events[-1]["type"] == "done"
    result = events[-1]["result"]
    assert result.depth == "deep"
    urls = {s.url for s in result.sources}
    assert any(u.startswith("https://fake1.example/") for u in urls)
    assert any(u.startswith("https://fake2.example/") for u in urls)


def test_fanout_targets_include_probe_query(tmp_path, monkeypatch):
    fake1 = FakeProvider("fake1", _provider_items("fake1"), calls=[])
    fake2 = FakeProvider("fake2", _provider_items("fake2"), calls=[])
    _register(monkeypatch, fake1=fake1, fake2=fake2)
    engine = _fanout_engine(tmp_path)
    engine.run(MULTIPART, depth="deep")
    expected = {MULTIPART, SUB_A, SUB_B}  # probe query + deduped sub-queries
    assert set(fake1.calls) == expected
    assert set(fake2.calls) == expected
    assert sorted(fake1.calls) == sorted(fake2.calls)
    assert 1 <= len(fake1.calls) <= 1 + engine.config.deep_search_queries


def test_provider_failure_isolated(tmp_path, monkeypatch):
    fake1 = FakeProvider("fake1", _provider_items("fake1"), fail=True, calls=[])
    fake2 = FakeProvider("fake2", _provider_items("fake2"), calls=[])
    _register(monkeypatch, fake1=fake1, fake2=fake2)
    engine = _fanout_engine(tmp_path)
    result = engine.run(COMPARATIVE, depth="deep")
    assert result.depth == "deep"
    assert fake1.calls  # attempted, then failed
    assert any("provider fake1 failed" in w for w in result.warnings)
    assert any(s.url.startswith("https://fake2.example/") for s in result.sources)


def test_providers_max_caps_fanout(tmp_path, monkeypatch):
    fakes = [FakeProvider(f"fake{i}", _provider_items(f"fake{i}"), calls=[]) for i in range(1, 4)]
    _register(monkeypatch, **{p.name: p for p in fakes})
    engine = _fanout_engine(tmp_path, providers="fake1,fake2,fake3", providers_max=2)
    engine.run(COMPARATIVE, depth="deep")
    assert fakes[0].calls and fakes[1].calls
    assert fakes[2].calls == []


def test_extract_only_provider_skipped_silently(tmp_path, monkeypatch):
    extract_only = ExtractOnlyProvider()
    fake1 = FakeProvider("fake1", _provider_items("fake1"), calls=[])
    _register(monkeypatch, extract_only=extract_only, fake1=fake1)
    engine = _fanout_engine(tmp_path, providers="extract_only,fake1")
    result = engine.run(COMPARATIVE, depth="deep")
    assert result.depth == "deep"
    assert extract_only.calls == []
    assert extract_only.extract_calls == []
    assert not any("extract_only" in w for w in result.warnings)
    assert fake1.calls


def test_duplicate_urls_deduped_across_sources(tmp_path, monkeypatch):
    provider_items = [
        SearchItem(title="dup", url="https://stub.example/article-1"),
        SearchItem(title="dup-slash", url="https://stub.example/article-2/"),
        SearchItem(title="unique", url="https://fake1.example/unique"),
    ]
    fake1 = FakeProvider("fake1", provider_items, calls=[])
    _register(monkeypatch, fake1=fake1)
    engine = _fanout_engine(tmp_path, providers="fake1")
    result = engine.run(COMPARATIVE, depth="deep")
    normalized = [_normalize_url(s.url) for s in result.sources]
    assert len(normalized) == len(set(normalized))
    assert "https://stub.example/article-1" in normalized
    assert "https://fake1.example/unique" in normalized
