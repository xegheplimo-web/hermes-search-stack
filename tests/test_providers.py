"""Tests for vn_geo.providers (R14-D, contract analysis/r14-interfaces.md §4 (1)).

Hermetic: fixtures only, no network. The gosom JSONL fixture stands in for a real
gosom scrape output; the provider is offline by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo import VnGeoError, connectors_gosom, places
from vn_geo.providers import (
    PROVIDERS,
    GosomJsonlProvider,
    PlaceProvider,
    get_provider,
    gosom_record_to_place,
)

FIXTURES = Path(__file__).parent / "fixtures" / "r14d"
GOSOM = FIXTURES / "gosom_places.jsonl"

# keys places.save_places / places.query_places rely on.
PLACE_KEYS = frozenset(
    {"source", "source_id", "name", "address", "lat", "lon", "rating", "review_count", "category", "extra"}
)


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "providers.db")
    yield s
    s.close()


# ---------- interface ----------


def test_gosom_provider_satisfies_protocol():
    assert isinstance(GosomJsonlProvider(), PlaceProvider)
    assert GosomJsonlProvider().name == "gosom-jsonl"


def test_get_provider_known_and_unknown():
    assert isinstance(get_provider("gosom-jsonl"), GosomJsonlProvider)
    assert get_provider("gosom-jsonl").name in PROVIDERS
    with pytest.raises(VnGeoError, match="unknown places provider"):
        get_provider("nope")
    with pytest.raises(VnGeoError, match="nope"):
        get_provider("nope", {})


# ---------- mapping ----------


def test_gosom_record_to_place_maps_fields():
    rec = connectors_gosom.parse_record(connectors_gosom.load_records(GOSOM)[0])
    place = gosom_record_to_place(rec)
    assert place["source"] == "google-maps"  # adapter's "gmaps" normalized to a frozen place source
    assert place["name"] == "Quán Phở Bò Yên Dũng"
    assert place["address"] == "12 Đường Trần Hưng Đạo, thị trấn Neo, Yên Dũng, Bắc Ninh, Việt Nam"
    assert place["category"] == "Quán phở"
    assert place["source_id"] == "ChIJ-yd-pho-0001"
    assert place["lat"] == pytest.approx(21.20712)
    assert place["lon"] == pytest.approx(106.23691)
    assert place["rating"] == pytest.approx(4.8)
    assert place["review_count"] == 24
    assert place["extra"]["url"].startswith("https://www.google.com/maps/place/")
    assert PLACE_KEYS <= place.keys()


def test_gosom_record_to_place_longtitude_alias():
    rec = connectors_gosom.parse_record(connectors_gosom.load_records(GOSOM)[2])
    place = gosom_record_to_place(rec)
    assert place["lon"] == pytest.approx(106.2487459)  # gosom's longtitude typo alias
    assert place["phone"] == "+84 988 834 579"


def test_gosom_record_to_place_no_url_gives_empty_extra():
    rec = connectors_gosom.parse_record({"title": "No URL", "latitude": 1.0, "longitude": 2.0})
    place = gosom_record_to_place(rec)
    assert place["extra"] == {}


# ---------- fetch ----------


def test_fetch_reads_jsonl_and_shapes_places():
    out = GosomJsonlProvider().fetch({"name": "Yên Dũng", "places": {"provider": "gosom-jsonl", "path": str(GOSOM)}})
    assert len(out) == 3
    assert all(p["source"] == "google-maps" for p in out)
    assert all(PLACE_KEYS <= p.keys() for p in out)


def test_fetch_path_falls_back_to_provider_cfg():
    provider = GosomJsonlProvider({"path": str(GOSOM)})
    out = provider.fetch({"name": "X", "places": {"provider": "gosom-jsonl"}})
    assert len(out) == 3


def test_fetch_missing_path_raises():
    with pytest.raises(VnGeoError, match="path"):
        GosomJsonlProvider().fetch({"name": "X", "places": {"provider": "gosom-jsonl"}})


def test_fetch_accepts_area_cfg_from_get_provider():
    provider = get_provider("gosom-jsonl")
    out = provider.fetch({"name": "Yên Dũng", "places": {"provider": "gosom-jsonl", "path": str(GOSOM)}})
    assert {p["source_id"] for p in out} == {
        "ChIJ-yd-pho-0001",
        "ChIJ-yd-bun-0002",
        "ChIJBdL50xVuNTERKUSXGNaVQJo",
    }


# ---------- end-to-end into the store ----------


def test_fetch_then_save_places_round_trip(store):
    records = GosomJsonlProvider().fetch({"name": "Yên Dũng", "places": {"path": str(GOSOM)}})
    counts = places.save_places(store, records)
    assert counts == {"new": 3, "updated": 0, "unchanged": 0}
    rows = places.query_places(store, area="Yên Dũng", limit=10)
    assert len(rows) == 3
    names = {r["name"] for r in rows}
    assert "Quán Phở Bò Yên Dũng" in names
    # re-run is idempotent (same url_key + content)
    assert places.save_places(store, records) == {"new": 0, "updated": 0, "unchanged": 3}


def test_no_network_imports(monkeypatch):
    # The provider must not reach the network: sockets/urllib are tripwires.
    import socket
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("provider attempted network access")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    out = GosomJsonlProvider().fetch({"name": "Yên Dũng", "places": {"path": str(GOSOM)}})
    assert len(out) == 3


def test_fixture_jsonl_is_valid():
    lines = [ln for ln in GOSOM.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    for line in lines:
        assert isinstance(json.loads(line), dict)
