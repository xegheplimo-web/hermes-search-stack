"""Hermetic tests for vn_geo.wards (R15-E pilot).

Fixtures under tests/fixtures/wards/mini-cache/ are hand-made tiny ward
polygons mirroring the upstream per-unit file shape (one-Feature
FeatureCollection with id/code/name/bbox): two adjacent Ha Noi squares
sharing an edge, plus one Hai Phong MultiPolygon with a hole. No network:
``wards._http_get_bytes`` is monkeypatched where fetch is exercised.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vn_geo import VnGeoError, wards

FIXTURE_CACHE = Path(__file__).parent.parent / "fixtures" / "wards" / "mini-cache"


@pytest.fixture()
def cache_dir() -> Path:
    return FIXTURE_CACHE


# --------------------------------------------------------------------------- load_wards


def test_load_wards_groups_by_province(cache_dir):
    loaded = wards.load_wards(cache_dir)
    assert sorted(loaded) == ["ha-noi", "hai-phong"]
    assert sorted(f["unit_id"] for f in loaded["ha-noi"]) == ["00001_phuong_a", "00002_phuong_b"]
    assert [f["unit_id"] for f in loaded["hai-phong"]] == ["10501_phuong_c"]
    feat = loaded["ha-noi"][0]
    assert feat["ward_code"] == "00001"
    assert feat["ward_name"] == "Phường A"
    assert feat["bbox"] == [105.8, 21.0, 105.81, 21.01]


def test_load_wards_missing_cache_is_empty(tmp_path):
    assert wards.load_wards(tmp_path / "no-such-dir") == {}


def test_load_wards_invalid_file_raises(tmp_path):
    bad = tmp_path / "01_ha_noi" / "wards"
    bad.mkdir(parents=True)
    (bad / "00001_broken.geojson").write_text("{not json", encoding="utf-8")
    with pytest.raises(VnGeoError):
        wards.load_wards(tmp_path)


# --------------------------------------------------------------------------- ward_at


def test_ward_at_inside(cache_dir):
    hit = wards.ward_at(21.005, 105.805, cache_dir)
    assert hit == {
        "province": "ha-noi",
        "ward_name": "Phường A",
        "ward_code": "00001",
        "unit_id": "00001_phuong_a",
    }
    hit = wards.ward_at(21.005, 105.815, cache_dir)
    assert hit is not None and hit["ward_code"] == "00002"
    hit = wards.ward_at(20.86, 106.685, cache_dir)
    assert hit == {
        "province": "hai-phong",
        "ward_name": "Phường C",
        "ward_code": "10501",
        "unit_id": "10501_phuong_c",
    }


def test_ward_at_hole_is_none(cache_dir):
    # inside ward C's outer ring but inside its hole -> no ward
    assert wards.ward_at(20.86, 106.68, cache_dir) is None


def test_ward_at_outside(cache_dir):
    assert wards.ward_at(0.0, 0.0, cache_dir) is None
    assert wards.ward_at(21.005, 105.9, cache_dir) is None
    assert wards.ward_at(-33.86, 151.20, cache_dir) is None


def test_ward_at_edge_is_deterministic(cache_dir):
    # shared edge x=105.81 between wards A and B: ray-cast assigns it to one
    # side or the other — the contract only requires determinism.
    first = wards.ward_at(21.005, 105.81, cache_dir)
    second = wards.ward_at(21.005, 105.81, cache_dir)
    assert first == second
    assert first is not None and first["ward_code"] in ("00001", "00002")


def test_ward_at_missing_cache_is_none(tmp_path):
    assert wards.ward_at(20.86, 106.68, tmp_path / "no-such-dir") is None


def test_ward_at_bad_coords_raise(cache_dir):
    with pytest.raises(VnGeoError):
        wards.ward_at("north", "east", cache_dir)


# --------------------------------------------------------------------------- fetch_wards (fixture mode, no network)


def listing_ha_noi() -> list[str]:
    return ["00001_phuong_a.geojson", "00002_phuong_b.geojson"]


def _stub_http_factory(calls: list):
    listing = {
        "01_ha_noi": listing_ha_noi(),
        "31_hai_phong": ["10501_phuong_c.geojson"],
    }

    def _stub(url: str, timeout: float = 60.0) -> bytes:
        calls.append(url)
        if "api.github.com" in url:
            for province_dir, names in listing.items():
                if f"/{province_dir}/" in url:
                    return json.dumps([{"name": n} for n in names]).encode()
            raise AssertionError(f"unexpected listing url {url}")
        for fixture_file in FIXTURE_CACHE.rglob("*.geojson"):
            if url.endswith("/" + fixture_file.name):
                return fixture_file.read_bytes()
        raise AssertionError(f"unexpected raw url {url}")

    return _stub


def test_fetch_wards_idempotent_fixture_mode(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(wards, "_http_get_bytes", _stub_http_factory(calls))
    first = wards.fetch_wards(["ha-noi", "Hải Phòng"], tmp_path)
    assert len(first) == 3
    assert all(Path(p).exists() for p in first)
    n_calls = len(calls)
    assert n_calls > 0
    # second run: everything cached -> same paths, no file re-downloads
    # (only the cheap GitHub listing is re-checked, no raw file bytes fetched)
    calls.clear()
    before = {p: Path(p).read_bytes() for p in first}
    second = wards.fetch_wards(["01_ha_noi", "31_hai_phong"], tmp_path)
    assert second == first
    assert all("raw.githubusercontent" not in url for url in calls)
    assert {p: Path(p).read_bytes() for p in second} == before
    # force=True re-downloads
    forced = wards.fetch_wards(["ha-noi"], tmp_path, force=True)
    assert len(calls) > 0
    expected = sorted(str(tmp_path / "01_ha_noi" / "wards" / name) for name in listing_ha_noi())
    assert forced == expected


def test_fetch_wards_unknown_province_raises(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(wards, "_http_get_bytes", _stub_http_factory(calls))
    with pytest.raises(VnGeoError):
        wards.fetch_wards(["Paris"], tmp_path)
    assert calls == []


def test_fetch_wards_needs_provinces(tmp_path):
    with pytest.raises(VnGeoError):
        wards.fetch_wards([], tmp_path)
