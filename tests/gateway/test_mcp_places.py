"""Tests for the ``hermes_places`` MCP tool (R16-A; r16-interfaces.md §1).

Direct tool-function calls against a duck-typed stub engine plus a tmp
SearchStore seeded via ``vn_geo.places.save_places`` from the
``tests/fixtures/vn_geo/places/scan_a.jsonl`` records. ``query_places`` is
monkeypatched only where the frozen contract requires it (enrichment
passthrough, count clamp, viewport edge cases); everything else exercises
the real db path. Hermetic — no network, no live ``data/places.db``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

mcp = pytest.importorskip("mcp")

from gateway.mcp.server import build_mcp  # noqa: E402
from gateway.mcp.tools import make_tools  # noqa: E402
from searchstore import SearchStore  # noqa: E402
from vn_geo import places as vn_places  # noqa: E402

SCAN_A = Path(__file__).resolve().parent.parent / "fixtures" / "vn_geo" / "places" / "scan_a.jsonl"

#: Frozen §1 Place schema — all keys always present, nullable -> None.
PLACE_KEYS = {
    "id",
    "name",
    "source",
    "address",
    "lat",
    "lon",
    "rating",
    "review_count",
    "category",
    "phone",
    "website",
    "hours",
    "thumbnail",
    "url",
    "scanned_at",
}


class _StubEngine:
    """Minimal engine surface make_tools needs (config + backend attr)."""

    def __init__(self, tmp_path):
        self.backend = SimpleNamespace()
        self.config = SimpleNamespace(
            store_db=str(tmp_path / "store.db"),
            repo_root=str(tmp_path),
        )


@pytest.fixture()
def engine(tmp_path):
    return _StubEngine(tmp_path)


@pytest.fixture()
def places_db(tmp_path):
    """Tmp places.db seeded with the five scan_a records (path str)."""
    db_path = tmp_path / "places.db"
    with SearchStore(str(db_path)) as store:
        vn_places.save_places(store, vn_places.load_scan_jsonl(SCAN_A))
    return str(db_path)


def _tool(engine, db) -> object:
    return make_tools(engine, places_db=str(db))["hermes_places"]


# ---------------------------------------------------------------------------
# real-db behavior (scan_a fixture)
# ---------------------------------------------------------------------------


def test_hermes_places_blank_query_browses_all(engine, places_db):
    out = _tool(engine, places_db)("   ")
    assert out["ok"] is True
    assert out["kind"] == "places"
    assert out["query"] == "   "  # echoed as passed
    assert out["area"] is None
    assert out["count"] == 5 == len(out["places"])
    assert "error" not in out
    for place in out["places"]:
        assert set(place) == PLACE_KEYS
    # sort passthrough: query_places orders rating desc, None last.
    assert [p["name"] for p in out["places"]] == [
        "Phở Bò Gia Truyền Yên Dũng",
        "Bún Chả Hương Giang",
        "Bánh Mì Pate Cô Hai",
        "Trà Chanh Nhà Mình",
        "Cơm Rang Dưa Bò Tô Hiệu",
    ]
    # viewport = bbox midpoint over all five numeric lat/lon pairs.
    assert out["viewport"]["bbox"] == pytest.approx([106.23544, 21.20571, 106.24012, 21.20901])
    assert out["viewport"]["center"] == pytest.approx([106.23778, 21.20736])


def test_hermes_places_text_query_and_id_url_mapping(engine, places_db):
    out = _tool(engine, places_db)("phở")
    assert out["ok"] is True and out["count"] == 1
    place = out["places"][0]
    # id = doc url (vn://<source>/<source_id-slug>); with r16-b merged the row
    # carries source_url, so url prefers it (fallback to id is covered in
    # test_hermes_places_enrichment_passthrough).
    assert place["id"] == "vn://google-maps/gmaps-yd-001"
    assert place["url"] == "https://www.google.com/maps/place/pho-bo-gia-truyen-yen-dung"
    assert place["name"] == "Phở Bò Gia Truyền Yên Dũng"
    assert place["source"] == "google-maps"
    assert place["lat"] == 21.20712 and place["lon"] == 106.23691
    assert place["rating"] == 4.8 and place["review_count"] == 24
    assert place["scanned_at"] == "2026-10-05T09:12:00+07:00"
    # viewport degenerates to the single point.
    assert out["viewport"] == {
        "center": [106.23691, 21.20712],
        "bbox": [106.23691, 21.20712, 106.23691, 21.20712],
    }


def test_hermes_places_filters_on_real_db(engine, places_db):
    tool = _tool(engine, places_db)
    out = tool("", min_rating=4.5)
    assert out["ok"] is True and out["count"] == 2
    assert {p["name"] for p in out["places"]} == {"Phở Bò Gia Truyền Yên Dũng", "Bún Chả Hương Giang"}
    out = tool("", category="phở")
    assert out["ok"] is True and out["count"] == 1
    out = tool("", area="chợ neo")
    assert out["ok"] is True and out["count"] == 1
    assert out["places"][0]["name"] == "Bún Chả Hương Giang"
    assert out["area"] == "chợ neo"


def test_hermes_places_empty_db(engine, tmp_path):
    db_path = tmp_path / "empty.db"
    with SearchStore(str(db_path)):
        pass  # schema exists, zero place docs
    out = _tool(engine, db_path)("pho")
    assert out == {
        "ok": True,
        "kind": "places",
        "query": "pho",
        "area": None,
        "count": 0,
        "places": [],
        "viewport": None,
    }


def test_hermes_places_missing_db_error(engine, tmp_path):
    missing = tmp_path / "absent-places.db"
    out = _tool(engine, missing)("pho", area="Yên Dũng")
    assert out["ok"] is False
    assert out["kind"] == "places"
    assert out["query"] == "pho"
    assert out["area"] == "Yên Dũng"
    assert out["count"] == 0 and out["places"] == [] and out["viewport"] is None
    assert out["error"] == f"places db not found at {missing}"
    assert not missing.exists(), "tool must not create the db file"


def test_hermes_places_default_path_missing(engine, tmp_path):
    # No places_db override: resolution falls back to <repo_root>/data/places.db.
    out = make_tools(engine)["hermes_places"]("pho")
    assert out["ok"] is False
    assert out["error"] == f"places db not found at {tmp_path / 'data' / 'places.db'}"


def test_hermes_places_default_path_resolution(engine, tmp_path):
    db_path = tmp_path / "data" / "places.db"
    db_path.parent.mkdir()
    with SearchStore(str(db_path)) as store:
        vn_places.save_places(store, vn_places.load_scan_jsonl(SCAN_A))
    out = make_tools(engine)["hermes_places"]("phở")  # no places_db kwarg
    assert out["ok"] is True and out["count"] == 1


# ---------------------------------------------------------------------------
# monkeypatched query_places — contract passthrough + viewport math
# ---------------------------------------------------------------------------


def test_hermes_places_kwargs_passthrough(engine, places_db, monkeypatch):
    seen: list[dict] = []

    def fake(store, **kwargs):
        seen.append(kwargs)
        return []

    monkeypatch.setattr(vn_places, "query_places", fake)
    tool = _tool(engine, places_db)
    tool("quán ăn ", area="Yên Dũng", category="quán phở", min_rating=4.0, count=12)
    assert seen == [{"text": "quán ăn", "area": "Yên Dũng", "category": "quán phở", "min_rating": 4.0, "limit": 12}]
    seen.clear()
    tool("   ")
    assert seen[0]["text"] is None, "blank query browses (text=None)"
    assert seen[0]["limit"] == 8


def test_hermes_places_count_clamped(engine, places_db, monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(vn_places, "query_places", lambda store, **kw: seen.append(kw["limit"]) or [])
    tool = _tool(engine, places_db)
    tool("x", count=0)
    tool("x", count=-3)
    tool("x", count=999)
    tool("x", count=5)
    assert seen == [1, 1, 50, 5]


def test_hermes_places_enrichment_passthrough(engine, places_db, monkeypatch):
    """r16-b rows (may not exist on this branch): extra keys ride through via
    ``.get()``, ``url`` prefers ``source_url``, ``source_id`` stays internal."""
    rows = [
        {
            "url": "vn://google-maps/gmaps-yd-001",
            "name": "Phở Bò",
            "source": "google-maps",
            "source_id": "gmaps-yd-001",
            "address": "12 Trần Hưng Đạo",
            "lat": 21.20712,
            "lon": 106.23691,
            "rating": 4.8,
            "review_count": 24,
            "category": "quán phở",
            "phone": "0987 111 222",
            "website": "https://example.com",
            "hours": "06:00–14:00",
            "thumbnail": "https://img.example/t.jpg",
            "source_url": "https://www.google.com/maps/place/pho-bo",
            "scanned_at": "2026-10-05T09:12:00+07:00",
        },
        {
            "url": "vn://google-maps/gmaps-yd-002",
            "name": "Bún Chả",
            "source": "google-maps",
            "lat": None,
            "lon": None,
        },
    ]
    monkeypatch.setattr(vn_places, "query_places", lambda store, **kw: rows)
    out = _tool(engine, places_db)("x")
    assert out["ok"] is True and out["count"] == 2
    first, second = out["places"]
    assert set(first) == PLACE_KEYS
    assert first["id"] == "vn://google-maps/gmaps-yd-001"
    assert first["url"] == "https://www.google.com/maps/place/pho-bo"
    assert first["phone"] == "0987 111 222"
    assert first["website"] == "https://example.com"
    assert first["hours"] == "06:00–14:00"
    assert first["thumbnail"] == "https://img.example/t.jpg"
    # no source_url -> url falls back to id; missing keys -> None
    assert set(second) == PLACE_KEYS
    assert second["url"] == second["id"] == "vn://google-maps/gmaps-yd-002"
    assert second["phone"] is None and second["rating"] is None
    # only the first row has coords -> degenerate single-point viewport
    assert out["viewport"] == {"center": [106.23691, 21.20712], "bbox": [106.23691, 21.20712, 106.23691, 21.20712]}


def test_hermes_places_sort_passthrough(engine, places_db, monkeypatch):
    """Row order is preserved 1:1 — the tool never re-sorts."""
    rows = [
        {"url": "vn://s/3", "name": "z-last", "rating": 1.0},
        {"url": "vn://s/1", "name": "a-first", "rating": 5.0},
        {"url": "vn://s/2", "name": "m-mid", "rating": 3.0},
    ]
    monkeypatch.setattr(vn_places, "query_places", lambda store, **kw: rows)
    out = _tool(engine, places_db)("x")
    assert [p["id"] for p in out["places"]] == ["vn://s/3", "vn://s/1", "vn://s/2"]


def test_hermes_places_viewport_numeric_only(engine, places_db, monkeypatch):
    rows = [
        {"url": "vn://s/1", "name": "a", "lat": 10.0, "lon": 20.0},
        {"url": "vn://s/2", "name": "b", "lat": 14, "lon": 26},  # ints count
        {"url": "vn://s/3", "name": "c", "lat": None, "lon": 99.0},  # half-null excluded
        {"url": "vn://s/4", "name": "d", "lat": "21.2", "lon": "106.2"},  # strings excluded
        {"url": "vn://s/5", "name": "e"},  # missing excluded
    ]
    monkeypatch.setattr(vn_places, "query_places", lambda store, **kw: rows)
    out = _tool(engine, places_db)("x")
    assert out["viewport"] == {"center": [23.0, 12.0], "bbox": [20.0, 10.0, 26.0, 14.0]}


def test_hermes_places_viewport_none_when_no_coords(engine, places_db, monkeypatch):
    rows = [{"url": "vn://s/1", "name": "a"}, {"url": "vn://s/2", "name": "b", "lat": "x", "lon": "y"}]
    monkeypatch.setattr(vn_places, "query_places", lambda store, **kw: rows)
    out = _tool(engine, places_db)("x")
    assert out["ok"] is True and out["viewport"] is None


def test_hermes_places_query_failure_is_structured(engine, places_db, monkeypatch):
    def boom(store, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(vn_places, "query_places", boom)
    out = _tool(engine, places_db)("x")
    assert out["ok"] is False
    assert out["error"] == "places query failed: db exploded"
    assert out["places"] == [] and out["count"] == 0 and out["viewport"] is None


# ---------------------------------------------------------------------------
# MCP smoke
# ---------------------------------------------------------------------------


def test_hermes_places_mcp_smoke(engine, places_db):
    """tools/call-style: through the MCP server built with places_db=."""

    async def _call():
        server = build_mcp(engine, places_db=places_db)
        return await server.call_tool("hermes_places", {"query": "phở", "count": 3})

    result = asyncio.run(_call())
    assert getattr(result, "is_error", getattr(result, "isError", False)) is False
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is True and payload["kind"] == "places"
    assert payload["count"] == 1
    assert payload["places"][0]["id"] == "vn://google-maps/gmaps-yd-001"
    assert payload["viewport"]["bbox"] == pytest.approx([106.23691, 21.20712, 106.23691, 21.20712])
