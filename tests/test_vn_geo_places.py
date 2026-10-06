"""Tests for vn_geo.places (R4-D scope, contract analysis/r4-interfaces.md §5).

Hermetic: all databases live under pytest's tmp_path; scan fixtures under
tests/fixtures/vn_geo/places/; no network.
"""

import json
from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo import VnGeoError
from vn_geo.places import (
    diff,
    load_scan_jsonl,
    main,
    parse_rating_label,
    place_key,
    query_places,
    save_places,
    save_scan_jsonl,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vn_geo" / "places"
SCAN_A = FIXTURE_DIR / "scan_a.jsonl"


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def scan_a():
    return load_scan_jsonl(SCAN_A)


def _saved_events(store):
    return [
        json.loads(r["payload"])
        for r in store.conn.execute("SELECT payload FROM events WHERE kind = 'places_saved' ORDER BY id")
    ]


# ---------- parse_rating_label ----------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("4,8 sao 24 bài đánh giá", (4.8, 24)),
        ("4.8 stars 24 reviews", (4.8, 24)),
        ("Chưa có bài đánh giá", (None, 0)),
        ("4,5 sao", (4.5, None)),
        ("4,2 sao 1.234 bài đánh giá", (4.2, 1234)),
        ("4.2 stars 1,234 reviews", (4.2, 1234)),
        ("5 sao 3 bài đánh giá", (5.0, 3)),
        ("12 bài đánh giá", (None, 12)),
        ("", (None, None)),
        ("   ", (None, None)),
        ("no reviews yet", (None, 0)),
        ("không có thông tin", (None, None)),
    ],
)
def test_parse_rating_label(label, expected):
    assert parse_rating_label(label) == expected


# ---------- place_key ----------


def test_place_key_prefers_source_id():
    rec = {"source_id": "gmaps-yd-001", "name": "Phở Bò", "lat": 21.2, "lon": 106.2}
    assert place_key(rec) == "gmaps-yd-001"


def test_place_key_fallback_name_lat_lon():
    rec = {"source_id": "", "name": "  Quán   PHỞ  ", "lat": 21.2, "lon": 106.23}
    assert place_key(rec) == "quán phở|21.20000,106.23000"


def test_place_key_fallback_missing_coords():
    rec = {"name": "Trà Chanh"}
    assert place_key(rec) == "trà chanh|0.00000,0.00000"


# ---------- load/save scan jsonl ----------


def test_load_scan_jsonl_fixture(scan_a):
    assert len(scan_a) == 5
    assert all(r["source"] == "google-maps" for r in scan_a)
    assert scan_a[0]["extra"]["rating_label"] == "4,8 sao 24 bài đánh giá"
    assert scan_a[2]["rating"] is None


def test_scan_jsonl_roundtrip(tmp_path, scan_a):
    out = tmp_path / "sub" / "copy.jsonl"
    save_scan_jsonl(scan_a, out)
    assert load_scan_jsonl(out) == scan_a


def test_load_scan_jsonl_bad_line(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"ok": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(VnGeoError, match="bad.jsonl:2"):
        load_scan_jsonl(bad)


# ---------- save_places ----------


def test_save_places_new_unchanged_updated_cycle(store, scan_a):
    counts = save_places(store, scan_a, source="google-maps")
    assert counts == {"new": 5, "updated": 0, "unchanged": 0}
    counts = save_places(store, scan_a, source="google-maps")
    assert counts == {"new": 0, "updated": 0, "unchanged": 5}
    changed = [dict(r) for r in scan_a]
    changed[0]["rating"] = 4.6
    counts = save_places(store, changed, source="google-maps")
    assert counts == {"new": 0, "updated": 1, "unchanged": 4}


def test_save_places_records_event(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    events = _saved_events(store)
    assert len(events) == 1
    assert events[0]["source"] == "google-maps"
    assert events[0]["count"] == 5
    assert events[0]["keys"] == [place_key(r) for r in scan_a]


def test_save_places_uses_record_source_when_no_override(store, scan_a):
    save_places(store, scan_a)
    assert _saved_events(store)[0]["source"] == "google-maps"


def test_save_places_synthesizes_vn_url(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    row = store.conn.execute("SELECT url FROM documents WHERE title = 'Trà Chanh Nhà Mình'").fetchone()
    assert row["url"].startswith("vn://google-maps/")


# ---------- query_places ----------


def test_query_all_sorted_rating_desc_none_last(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    results = query_places(store)
    assert [r["rating"] for r in results] == [4.8, 4.5, 4.2, 3.6, None]
    assert results[0]["name"] == "Phở Bò Gia Truyền Yên Dũng"


def test_query_area_filter(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    assert len(query_places(store, area="Yên Dũng")) == 5
    assert query_places(store, area="Hà Nội") == []


def test_query_category_fold(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    assert [r["name"] for r in query_places(store, category="quán phở")] == ["Phở Bò Gia Truyền Yên Dũng"]
    assert [r["name"] for r in query_places(store, category="QUAN PHO")] == ["Phở Bò Gia Truyền Yên Dũng"]


def test_query_min_rating(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    assert [r["rating"] for r in query_places(store, min_rating=4)] == [4.8, 4.5, 4.2]
    assert [r["rating"] for r in query_places(store, min_rating=4.5)] == [4.8, 4.5]


def test_query_text_fts(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    names = [r["name"] for r in query_places(store, text="phở")]
    assert names == ["Phở Bò Gia Truyền Yên Dũng"]
    names = [r["name"] for r in query_places(store, text="bún chả")]
    assert names == ["Bún Chả Hương Giang"]


def test_query_limit(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    assert len(query_places(store, limit=2)) == 2


def test_query_updated_doc_shows_latest(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    changed = [dict(r) for r in scan_a]
    changed[0]["rating"] = 4.1
    save_places(store, changed, source="google-maps")
    results = query_places(store, category="phở")
    assert len(results) == 1
    assert results[0]["rating"] == 4.1


# ---------- diff ----------


def test_diff_no_events(store):
    assert diff(store, source="google-maps") == {"added": [], "removed": [], "changed": []}


def test_diff_first_scan_all_added(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    result = diff(store, source="google-maps")
    assert result["added"] == sorted(place_key(r) for r in scan_a)
    assert result["removed"] == []
    assert result["changed"] == []


def test_diff_added_removed_changed(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    batch_b = [dict(r) for r in scan_a[:-1]]  # drop last record -> removed
    batch_b[0]["rating"] = 4.7  # rating change -> changed
    new_place = {
        "source": "google-maps",
        "source_id": "gmaps-yd-006",
        "name": "Phở Cuốn Mới",
        "address": "1 Đường Mới, Yên Dũng, Bắc Giang",
        "lat": 21.206,
        "lon": 106.239,
        "rating": 4.0,
        "review_count": 3,
        "category": "quán phở",
        "hours": None,
        "phone": None,
        "website": None,
        "extra": {},
        "scanned_at": "2026-10-06T08:00:00+07:00",
    }
    batch_b.append(new_place)  # -> added
    save_places(store, batch_b, source="google-maps")

    result = diff(store, source="google-maps")
    assert result["added"] == ["gmaps-yd-006"]
    assert result["removed"] == [place_key(scan_a[-1])]
    assert result["changed"] == [
        {
            "key": "gmaps-yd-001",
            "name": "Phở Bò Gia Truyền Yên Dũng",
            "changes": {"rating": [4.8, 4.7]},
        }
    ]


def test_diff_source_filter(store, scan_a):
    save_places(store, scan_a, source="google-maps")
    assert diff(store, source="foody") == {"added": [], "removed": [], "changed": []}


# ---------- cli ----------


def test_cli_save_query_diff(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert main(["save", "--db", db, "--file", str(SCAN_A), "--source", "google-maps"]) == 0
    capsys.readouterr()
    assert main(["query", "--db", db, "--area", "Yên Dũng", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["count"] == 5
    assert main(["diff", "--db", db, "--source", "google-maps", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and len(payload["added"]) == 5


def test_cli_missing_file_exit_2(tmp_path):
    db = str(tmp_path / "cli.db")
    assert main(["save", "--db", db, "--file", str(tmp_path / "nope.jsonl"), "--source", "google-maps"]) == 2
