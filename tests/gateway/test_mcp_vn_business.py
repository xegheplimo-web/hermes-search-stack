"""MCP ``hermes_vn kind="business"`` tests (R15-C; analysis/r15-interfaces.md §3).

Direct tool-function calls against a duck-typed stub engine plus a tmp
vn-geo entity db pointed at via ``HERMES_GATEWAY_VN_GEO_DB``. Hermetic.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.core import local_context
from gateway.mcp.tools import _VN_KINDS, make_tools
from vn_geo import business


class _StubEngine:
    """Minimal engine surface make_tools needs (config + backend attr)."""

    def __init__(self, tmp_path):
        self.backend = SimpleNamespace()
        self.config = SimpleNamespace(
            store_db=str(tmp_path / "store.db"),
            repo_root=str(tmp_path),
        )


@pytest.fixture()
def vn_geo_db(tmp_path, monkeypatch):
    """Tmp entity db wired through the env override; returns the path str."""
    db_path = tmp_path / "vn-geo.db"
    monkeypatch.setenv(local_context.VN_GEO_DB_ENV, str(db_path))
    business.upsert_entity(
        str(db_path),
        {
            "name": "Nhà nghỉ Bảo An",
            "address_text": "xã Nham Biền, huyện Yên Dũng, Bắc Giang",
            "area_old": "Yên Dũng",
            "province": "Bắc Giang",
            "lat": 21.2,
            "lng": 106.2,
            "source": "gmaps",
            "category": "lodging_budget",
            "kind": "lodging",
            "status": "open",
            "geocode_status": "exact",
        },
    )
    return str(db_path)


def test_vn_kinds_includes_business():
    assert "business" in _VN_KINDS


def test_hermes_vn_business_returns_items(tmp_path, vn_geo_db):
    tools = make_tools(_StubEngine(tmp_path))
    out = tools["hermes_vn"]("business", "nha nghi bao an")
    assert out["ok"] is True
    assert out["kind"] == "business"
    assert out["count"] == 1
    assert len(out["items"]) == out["count"]
    item = out["items"][0]
    # entity fields plus the R15-C scoring additions
    assert item["name"] == "Nhà nghỉ Bảo An"
    assert item["entity_id"].startswith("e_")
    assert item["address_text"].startswith("xã Nham Biền")
    assert item["lat"] == 21.2 and item["lng"] == 106.2
    assert item["sources"] == ["gmaps"]
    assert item["confidence"] > 0
    assert item["ambiguous"] is False


def test_hermes_vn_business_area_narrows(tmp_path, vn_geo_db):
    tools = make_tools(_StubEngine(tmp_path))
    out = tools["hermes_vn"]("business", "nha nghi bao an", "yen dung")
    assert out["ok"] is True and out["count"] == 1
    out = tools["hermes_vn"]("business", "nha nghi bao an", "hoang mai")
    assert out["ok"] is True and out["count"] == 0 and out["items"] == []


def test_hermes_vn_business_ambiguous_items(tmp_path, vn_geo_db):
    for address, area in (("1 Lê Lợi, Hải Phòng", "Hải Phòng"), ("9 Trần Phú, Hà Nội", "Hà Nội")):
        business.upsert_entity(
            vn_geo_db,
            {
                "name": "Cafe Trung Nguyên",
                "address_text": address,
                "area_old": area,
                "lat": 21.0,
                "lng": 105.8,
                "source": "manual",
                "status": "open",
            },
        )
    tools = make_tools(_StubEngine(tmp_path))
    out = tools["hermes_vn"]("business", "cafe trung nguyen")
    assert out["ok"] is True and out["count"] == 2
    assert all(item["ambiguous"] is True for item in out["items"])
    assert {item["address_text"] for item in out["items"]} == {
        "1 Lê Lợi, Hải Phòng",
        "9 Trần Phú, Hà Nội",
    }


def test_hermes_vn_business_missing_db(tmp_path, monkeypatch):
    missing = tmp_path / "absent.db"
    monkeypatch.setenv(local_context.VN_GEO_DB_ENV, str(missing))
    tools = make_tools(_StubEngine(tmp_path))
    out = tools["hermes_vn"]("business", "nha nghi")
    assert out["ok"] is False
    assert out["error"] == f"vn-geo.db not found at {missing}"


def test_hermes_vn_business_default_path_missing(tmp_path, monkeypatch):
    # No env override: resolution falls back to <repo>/data/vn-geo.db; point
    # the default at a tmp path so the missing-db branch is hermetic.
    monkeypatch.delenv(local_context.VN_GEO_DB_ENV, raising=False)
    missing = tmp_path / "data" / "vn-geo.db"
    monkeypatch.setattr(local_context, "DEFAULT_VN_GEO_DB", missing)
    tools = make_tools(_StubEngine(tmp_path))
    out = tools["hermes_vn"]("business", "nha nghi")
    assert out["ok"] is False
    assert out["error"] == f"vn-geo.db not found at {missing}"


def test_hermes_vn_unknown_kind_still_errors(tmp_path):
    tools = make_tools(_StubEngine(tmp_path))
    out = tools["hermes_vn"]("bogus", "q")
    assert out["results"] == [] and "error" in out
    assert "business" in out["error"]


def test_existing_kinds_untouched(tmp_path, vn_geo_db):
    # places/admin/enterprises still go through the SearchStore path and
    # degrade gracefully on an empty tmp store — shape unchanged.
    tools = make_tools(_StubEngine(tmp_path))
    out = tools["hermes_vn"]("places", "nha nghi")
    assert "results" in out and out.get("kind") == "places"
