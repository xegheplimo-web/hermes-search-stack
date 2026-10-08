"""gateway.providers — multi-source provider protocol + registry (R17-W1A).

Frozen contract: ``analysis/r17-interfaces.md`` §1 (frozen v1.0, 2026-10-08).

A *provider* is an extra web source the deep path can fan out to (exa,
parallel, jina, v2ex, bilibili, youtube, rss — registered in W2). W1A ships
the foundation only:

- :class:`Provider` — the structural interface (``name`` + ``capabilities`` +
  ``search``/``extract``), reusing the frozen wire types
  :class:`gateway.protocols.SearchItem` / ``ExtractItem`` (no new wire types).
- :data:`PROVIDERS` — name -> factory registry; intentionally EMPTY in W1A.
- :func:`get_provider` — name -> provider instance (unknown -> ProviderError).
- :func:`build_registry` — config-driven registry build (enabled/max/order).

Per-source isolation: provider failures raise :class:`ProviderError`; the
engine (W2+) isolates them and never fails the request.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from gateway.config import GatewayConfig
from gateway.protocols import ExtractItem, SearchItem

#: name -> factory. Intentionally EMPTY in W1A; W2 registers
#: exa/parallel/jina/v2ex/bilibili/youtube/rss.
PROVIDERS: dict[str, Callable[[], Provider]] = {}


@runtime_checkable
class Provider(Protocol):
    """Structural interface for multi-source providers (contract §1).

    ``capabilities`` is a subset of ``{"search", "extract"}``; v1 engine usage
    is ``search()`` only — ``extract()`` is protocol-level (adopted later).
    """

    name: str
    capabilities: frozenset[str]

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Return search hits for *query* (metadata only)."""
        ...

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """Extract page content for *urls*."""
        ...


class ProviderError(Exception):
    """Typed provider failure (``name`` + ``detail``); isolated by the engine."""

    def __init__(self, name: str, detail: str) -> None:
        super().__init__(name, detail)
        self.name = name
        self.detail = detail

    def __str__(self) -> str:
        return f"provider {self.name!r}: {self.detail}"


def get_provider(name: str) -> Provider:
    """Build a provider by *name*; unknown names raise :class:`ProviderError`."""
    key = str(name or "").strip()
    if key in PROVIDERS:
        return PROVIDERS[key]()
    raise ProviderError(name, "unknown provider")


def build_registry(config: GatewayConfig, *, warnings: list[str] | None = None) -> list[Provider]:
    """Build the enabled provider registry from *config*.

    Empty when ``config.providers_enabled`` is False. Otherwise walks
    ``config.enabled_providers()`` in config order, skipping unknown names
    silently (per-source isolation), and stops at ``config.providers_max``
    (checked *before* instantiating, so a zero budget yields an empty
    registry). Each factory is invoked in isolation: if one raises, that
    provider is skipped and the walk continues; when *warnings* is given the
    failure is appended as ``provider <name> factory failed: <exc>``.
    """
    if not config.providers_enabled:
        return []
    registry: list[Provider] = []
    for n in config.enabled_providers():
        if len(registry) >= config.providers_max:
            break
        if n in PROVIDERS:
            try:
                registry.append(PROVIDERS[n]())
            except Exception as exc:  # noqa: BLE001 — isolation: one bad factory must not block others
                if warnings is not None:
                    warnings.append(f"provider {n} factory failed: {exc}")
                continue
    return registry


__all__ = ["PROVIDERS", "Provider", "ProviderError", "build_registry", "get_provider"]
