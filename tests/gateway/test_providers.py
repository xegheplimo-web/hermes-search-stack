"""Tests for ``gateway.providers`` — protocol + registry (R17-W1A).

Hermetic: no network, no live calls. ``PROVIDERS`` is monkeypatched per test
and restored automatically.
"""

from __future__ import annotations

import pytest

import gateway.providers as providers_mod
from gateway.config import DEFAULT_PROVIDERS, GatewayConfig
from gateway.protocols import ExtractItem, SearchItem
from gateway.providers import PROVIDERS, Provider, ProviderError, build_registry, get_provider


class FakeProvider:
    """Minimal provider double (search-only)."""

    name = "fake"
    capabilities = frozenset({"search"})

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        return [SearchItem(title="t", url="https://example.test/1")]

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        return []


class FakeA(FakeProvider):
    name = "a"


class FakeB(FakeProvider):
    name = "b"


class FakeC(FakeProvider):
    name = "c"


def test_providers_empty_in_w1a():
    """W1A ships the foundation only; W2 registers the real providers."""
    assert providers_mod.PROVIDERS == {}


def test_fake_provider_satisfies_protocol():
    assert isinstance(FakeProvider(), Provider)


def test_get_provider_unknown_raises():
    with pytest.raises(ProviderError) as excinfo:
        get_provider("nope")
    assert "unknown provider" in str(excinfo.value)
    assert excinfo.value.name == "nope"


def test_get_provider_builds_known(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "fake", FakeProvider)
    provider = get_provider("fake")
    assert isinstance(provider, FakeProvider)
    assert provider.name == "fake"


def test_get_provider_normalizes_name(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "fake", FakeProvider)
    assert isinstance(get_provider("  fake  "), FakeProvider)


def test_build_registry_disabled_returns_empty():
    cfg = GatewayConfig(providers_enabled=False)
    assert build_registry(cfg) == []


def test_build_registry_skips_unknown_keeps_config_order(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "a", FakeA)
    monkeypatch.setitem(PROVIDERS, "b", FakeB)
    monkeypatch.setitem(PROVIDERS, "c", FakeC)
    monkeypatch.setitem(PROVIDERS, "fake", FakeProvider)
    cfg = GatewayConfig(providers_enabled=True, providers="b,fake,unknown,a", providers_max=4)
    registry = build_registry(cfg)
    assert [p.name for p in registry] == ["b", "fake", "a"]


def test_build_registry_respects_max(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "a", FakeA)
    monkeypatch.setitem(PROVIDERS, "b", FakeB)
    monkeypatch.setitem(PROVIDERS, "c", FakeC)
    cfg = GatewayConfig(providers_enabled=True, providers="a,b,c", providers_max=2)
    registry = build_registry(cfg)
    assert [p.name for p in registry] == ["a", "b"]


def test_build_registry_zero_max_returns_empty(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "a", FakeA)
    cfg = GatewayConfig(providers_enabled=True, providers="a", providers_max=0)
    assert build_registry(cfg) == []


def test_build_registry_factory_failure_isolated(monkeypatch):
    def bad_factory():
        raise RuntimeError("boom")

    monkeypatch.setitem(PROVIDERS, "bad", bad_factory)
    monkeypatch.setitem(PROVIDERS, "b", FakeB)
    cfg = GatewayConfig(providers_enabled=True, providers="bad,b", providers_max=4)
    warnings: list[str] = []
    registry = build_registry(cfg, warnings=warnings)
    assert [p.name for p in registry] == ["b"]
    assert warnings == ["provider bad factory failed: boom"]


def test_config_defaults():
    cfg = GatewayConfig()
    assert cfg.providers_enabled is False
    assert cfg.providers_max == 4
    assert cfg.provider_timeout_s == 8.0
    assert cfg.providers == DEFAULT_PROVIDERS


def test_from_env_providers(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_PROVIDERS_ENABLED", "true")
    monkeypatch.setenv("HERMES_GATEWAY_PROVIDERS_MAX", "2")
    monkeypatch.setenv("HERMES_GATEWAY_PROVIDER_TIMEOUT_S", "3.5")
    monkeypatch.setenv("HERMES_GATEWAY_PROVIDERS", "a, b ,a,c")
    cfg = GatewayConfig.from_env()
    assert cfg.providers_enabled is True
    assert cfg.providers_max == 2
    assert cfg.provider_timeout_s == 3.5
    assert cfg.enabled_providers() == ("a", "b", "c")


def test_enabled_providers_empty():
    assert GatewayConfig(providers="").enabled_providers() == ()
