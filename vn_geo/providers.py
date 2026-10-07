"""vn_geo.providers — PlaceProvider interface + offline place providers (R14-D).

Frozen contract: ``analysis/r14-interfaces.md`` §4 (1).

A *place provider* turns an area's crawl configuration into ``places``-shaped
records — plain dicts accepted by :func:`vn_geo.places.save_places`. Providers
are **offline**: they never touch the network; live scraping stays outside the
hermetic core (gosom runs out-of-band via Docker, see the R13-E runbook).

- :class:`PlaceProvider` — the structural interface (a ``name`` + ``fetch``).
- :class:`GosomJsonlProvider` — ``name = "gosom-jsonl"``: reads an area's
  ``places.path`` JSONL (gosom's native ``-results`` output), runs each raw
  record through the read-only :mod:`vn_geo.connectors_gosom` adapter, then maps
  the resulting entity-schema-§1 dict to a ``places``-shaped dict.
- :func:`get_provider` — name -> provider factory (unknown name -> VnGeoError).

Mapping (§1 dict -> places dict): ``title``/``name`` stays ``name``;
``address_text`` -> ``address``; ``category_raw`` -> ``category``; ``lat``/
``lng`` -> ``lat``/``lon``; ``source_url`` (gosom ``link``) -> ``extra.url`` so
the URL is the dedup identity when present; ``source`` is normalized from the
adapter's ``"gmaps"`` to the frozen place source ``"google-maps"`` (so coverage
counts it — see ``places.PLACE_SOURCES``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from . import VnGeoError, connectors_gosom

#: Provider names this module can build (used by ``refresh._validate_config``).
PROVIDERS = ("gosom-jsonl",)

#: gosom scrapes Google Maps; map the adapter's ``"gmaps"`` to the frozen
#: ``places.PLACE_SOURCES`` id so coverage sees these docs.
_PLACE_SOURCE = "google-maps"


@runtime_checkable
class PlaceProvider(Protocol):
    """Structural interface for offline place sources (contract §4 (1))."""

    name: str

    def fetch(self, area_cfg: dict) -> list[dict]:
        """Return ``places.save_places``-compatible records for *area_cfg*."""
        ...


def gosom_record_to_place(rec: dict) -> dict:
    """Map one :mod:`connectors_gosom` §1 dict to a ``places``-shaped record.

    The result carries the keys :func:`places.save_places` / ``places.query_places``
    read: ``name`` (required), ``source``, ``source_id``, ``address``, ``lat``,
    ``lon``, ``rating``, ``review_count``, ``category``, ``phone``, ``website``,
    ``status`` and ``extra.url``.
    """
    source_url = str(rec.get("source_url") or "").strip()
    return {
        "source": _PLACE_SOURCE,
        "source_id": str(rec.get("source_id") or ""),
        "name": rec.get("name"),
        "address": rec.get("address_text"),
        "lat": rec.get("lat"),
        "lon": rec.get("lng"),
        "rating": rec.get("rating"),
        "review_count": rec.get("review_count"),
        "category": rec.get("category_raw"),
        "phone": rec.get("phone") or None,
        "website": rec.get("website") or None,
        "status": rec.get("status"),
        "extra": {"url": source_url} if source_url else {},
    }


class GosomJsonlProvider:
    """Offline provider over gosom JSONL output (``name = "gosom-jsonl"``).

    ``fetch`` reads ``area_cfg["places"]["path"]`` (falling back to a ``path`` in
    the provider's own ``cfg``), adapts every raw gosom record via
    :mod:`vn_geo.connectors_gosom`, and returns ``places``-shaped dicts. No area
    filtering is applied: each area is expected to point at its own JSONL file.
    """

    name = "gosom-jsonl"

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}

    def fetch(self, area_cfg: dict) -> list[dict]:
        places_cfg = (area_cfg or {}).get("places") or {}
        path = places_cfg.get("path") or self.cfg.get("path")
        if not path:
            raise VnGeoError("gosom-jsonl provider needs a JSONL 'path' (set area_cfg['places']['path'])")
        records = connectors_gosom.load_records(path)
        return [gosom_record_to_place(connectors_gosom.parse_record(raw)) for raw in records]


def get_provider(name: str, cfg: dict | None = None) -> PlaceProvider:
    """Build a provider by *name*; unknown names raise :class:`VnGeoError`."""
    key = str(name or "").strip()
    if key == "gosom-jsonl":
        return GosomJsonlProvider(cfg)
    raise VnGeoError(f"unknown places provider {name!r}; known: {list(PROVIDERS)}")


__all__ = [
    "PROVIDERS",
    "GosomJsonlProvider",
    "PlaceProvider",
    "get_provider",
    "gosom_record_to_place",
]
