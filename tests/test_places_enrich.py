"""Tests for R16-B: query_places output enrichment (contract analysis/r16-interfaces.md §2).

Hermetic: all databases live under pytest's tmp_path; no network.
"""

from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo.places import load_scan_jsonl, query_places, save_places

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vn_geo" / "places"
SCAN_A = FIXTURE_DIR / "scan_a.jsonl"

NEW_KEYS = ("source_id", "phone", "website", "hours", "source_url", "thumbnail")


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_scan_a_regression_and_enrichment(store):
    records = load_scan_jsonl(SCAN_A)
    save_places(store, records)
    rows = query_places(store, limit=20)
    assert len(rows) == 5
    by_name = _by_name(rows)

    # Regression: old keys unchanged (names/ratings/urls).
    assert by_name["Phở Bò Gia Truyền Yên Dũng"]["rating"] == 4.8
    assert by_name["Phở Bò Gia Truyền Yên Dũng"]["review_count"] == 24
    assert by_name["Phở Bò Gia Truyền Yên Dũng"]["url"] == "vn://google-maps/gmaps-yd-001"
    assert by_name["Bún Chả Hương Giang"]["rating"] == 4.5
    assert by_name["Cơm Rang Dưa Bò Tô Hiệu"]["rating"] is None
    assert by_name["Cơm Rang Dưa Bò Tô Hiệu"]["review_count"] == 0
    assert by_name["Bánh Mì Pate Cô Hai"]["rating"] == 4.2
    assert by_name["Bánh Mì Pate Cô Hai"]["category"] == "tiệm bánh mì"
    # Record without source_id keeps legacy extra.url-less fallback url.
    tra_chanh = by_name["Trà Chanh Nhà Mình"]
    assert tra_chanh["url"].startswith("vn://google-maps/")
    assert tra_chanh["address"] == "Ngã tư Neo, Yên Dũng, Bắc Giang"

    # New keys present on every row.
    for r in rows:
        for k in NEW_KEYS:
            assert k in r

    # Correct values straight from stored meta rec.
    pho = by_name["Phở Bò Gia Truyền Yên Dũng"]
    assert pho["source_id"] == "gmaps-yd-001"
    assert pho["phone"] == "0987 111 222"
    assert pho["website"] is None
    assert pho["hours"] == "06:00–14:00"
    assert pho["source_url"] == "https://www.google.com/maps/place/pho-bo-gia-truyen-yen-dung"
    assert pho["thumbnail"] is None

    banh_mi = by_name["Bánh Mì Pate Cô Hai"]
    assert banh_mi["website"] == "https://facebook.com/banhmicohai"
    assert banh_mi["source_url"] == "https://www.google.com/maps/place/banh-mi-pate-co-hai"

    # Blank source_id -> None; no extra.url -> source_url None.
    assert tra_chanh["source_id"] is None
    assert tra_chanh["source_url"] is None
    assert tra_chanh["thumbnail"] is None


def test_enrichment_values_fallbacks_and_blanks(store):
    records = [
        {
            "source": "osm",
            "source_id": "  osm:node/1  ",
            "name": "Enriched Full",
            "address": "1 Main St",
            "lat": 21.2,
            "lon": 106.23,
            "rating": 4.0,
            "review_count": 5,
            "category": "cafe",
            "phone": "  0123 456  ",
            "website": " https://example.com ",
            "hours": " 09:00-18:00 ",
            "source_url": " https://source.example/full ",
            "thumbnail": " https://img.example/full.jpg ",
            "extra": {"url": "https://extra.example/full", "thumbnail": "https://img.example/extra.jpg"},
            "scanned_at": "2026-10-06T00:00:00+07:00",
        },
        {
            "source": "osm",
            "name": "Extra Fallback",
            "address": "2 Main St",
            "lat": 21.21,
            "lon": 106.24,
            "rating": 3.0,
            "review_count": 2,
            "category": "cafe",
            "extra": {"url": "https://extra.example/fallback", "thumbnail": "https://img.example/fb.jpg"},
            "scanned_at": "2026-10-06T00:00:00+07:00",
        },
        {
            "source": "osm",
            "source_id": "   ",
            "name": "Blank Fields",
            "address": "3 Main St",
            "lat": 21.22,
            "lon": 106.25,
            "rating": 2.0,
            "review_count": 1,
            "category": "cafe",
            "phone": "   ",
            "website": "",
            "hours": "  ",
            "source_url": "   ",
            "thumbnail": "",
            "extra": {"url": "  ", "thumbnail": None},
            "scanned_at": "2026-10-06T00:00:00+07:00",
        },
    ]
    save_places(store, records)
    by_name = _by_name(query_places(store, limit=20))

    full = by_name["Enriched Full"]
    assert full["source_id"] == "osm:node/1"
    assert full["phone"] == "0123 456"
    assert full["website"] == "https://example.com"
    assert full["hours"] == "09:00-18:00"
    # Top-level wins over extra.
    assert full["source_url"] == "https://source.example/full"
    assert full["thumbnail"] == "https://img.example/full.jpg"

    fb = by_name["Extra Fallback"]
    assert fb["source_id"] is None
    assert fb["phone"] is None
    assert fb["website"] is None
    assert fb["hours"] is None
    assert fb["source_url"] == "https://extra.example/fallback"
    assert fb["thumbnail"] == "https://img.example/fb.jpg"

    blank = by_name["Blank Fields"]
    assert blank["source_id"] is None
    assert blank["phone"] is None
    assert blank["website"] is None
    assert blank["hours"] is None
    assert blank["source_url"] is None
    assert blank["thumbnail"] is None
