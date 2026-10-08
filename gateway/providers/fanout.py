"""gateway/providers/fanout.py — ``SearchBackend`` adapter over a provider registry (R17-W1B).

``ProviderFanoutBackend`` wraps a built provider registry
(:func:`gateway.providers.build_registry`) as a
:class:`gateway.protocols.SearchBackend` so the engine's deep path can fan
the query set (probe query + sub-queries, deduped — contract
``analysis/r17-interfaces.md`` §2) out through the existing
``BackendPool`` machinery:

- ``search`` calls every registry provider advertising ``"search"`` in its
  ``capabilities``, in registry order; non-search providers are skipped
  silently. A provider that raises appends
  ``provider <name> failed: <exc>`` to the shared warnings list and the
  merge continues — per-source isolation, never a raise.
- ``extract`` is protocol completeness only in v1 (the engine never calls
  it): every url gets an :class:`ExtractItem` carrying a not-supported
  ``error``.
- *warnings* is the request's shared list; ``list.append`` is thread-safe
  under the GIL, so pool workers append directly — no locks.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from gateway.protocols import ExtractItem, SearchItem

if TYPE_CHECKING:
    from gateway.providers import Provider


class ProviderFanoutBackend:
    """``SearchBackend`` adapter: one fanned-out query -> merged provider hits."""

    name = "providers"

    def __init__(self, providers: list[Provider], *, warnings: list[str]) -> None:
        self._providers = list(providers)
        self._warnings = warnings

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        """Query each search-capable provider; items merged in provider order."""
        merged: list[SearchItem] = []
        for provider in self._providers:
            if "search" not in getattr(provider, "capabilities", ()):
                continue  # non-search providers are skipped silently
            try:
                merged.extend(provider.search(query, max_results=max_results) or [])
            except Exception as exc:  # noqa: BLE001 — per-source isolation: warn + continue
                self._warnings.append(f"provider {provider.name} failed: {exc}")
        return [replace(item, position=i + 1) for i, item in enumerate(merged)]

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        """v1: provider extract is unsupported — per-url error items only."""
        return [ExtractItem(url=u, error="provider extract not supported (v1)") for u in urls]

    def ping(self) -> dict:
        return {"ok": True, "detail": f"provider fan-out ({len(self._providers)} providers)"}
