"""Tests for the R14-C stable place identity fix (analysis/r14-interfaces.md §3.1).

A non-empty ``source_id`` keys the document ``vn://<slug(source)>/<slug(source_id)>``
regardless of ``extra.url`` — a rotated Maps URL can no longer mint a phantom
document. Records without ``source_id`` keep the pre-R14 fallback exactly.
"""

import json
from urllib.parse import quote

import pytest

from searchstore import SearchStore
from vn_geo.places import _record_url, place_key, save_places


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


def _place(**overrides):
    rec = {
        "source": "google-maps",
        "name": "Quán Ăn Ngon",
        "address": "1 Đường Chính, Yên Dũng",
        "lat": 21.2,
        "lon": 106.2,
        "category": "quán ăn",
        "extra": {},
        "scanned_at": "2026-10-06T08:00:00+07:00",
    }
    rec.update(overrides)
    return rec


# ---------- source_id present -> stable vn:// slug key ----------


def test_same_source_id_different_extra_url_same_url_key(store):
    rec_a = _place(source_id="gmaps-yd-001", extra={"url": "https://maps.example/original"})
    rec_b = _place(source_id="gmaps-yd-001", rating=4.9, extra={"url": "https://maps.example/rotated"})
    assert save_places(store, [rec_a], source="google-maps") == {"new": 1, "updated": 0, "unchanged": 0}
    assert save_places(store, [rec_b], source="google-maps") == {"new": 0, "updated": 1, "unchanged": 0}
    keys = {r["url_key"] for r in store.conn.execute("SELECT url_key FROM documents")}
    assert keys == {"vn://google-maps/gmaps-yd-001"}


def test_source_id_overrides_extra_url():
    rec = _place(source_id="foody-99", extra={"url": "https://www.foody.vn/quan-an"})
    assert _record_url(rec, "foody") == "vn://foody/foody-99"


def test_source_id_slug_folds_case_diacritics_punctuation():
    rec = _place(source_id="Quán Ăn/12")
    assert _record_url(rec, "Google Maps") == "vn://google-maps/quan-an-12"


def test_numeric_source_id():
    assert _record_url(_place(source_id=12345), "osm") == "vn://osm/12345"


def test_same_source_id_same_key_across_name_change(store):
    rec_a = _place(source_id="gmaps-yd-007", name="Phở Bò Cũ")
    rec_b = _place(source_id="gmaps-yd-007", name="Phở Bò Mới Đổi Tên")
    save_places(store, [rec_a], source="google-maps")
    save_places(store, [rec_b], source="google-maps")
    keys = {r["url_key"] for r in store.conn.execute("SELECT url_key FROM documents")}
    assert keys == {"vn://google-maps/gmaps-yd-007"}


def test_source_id_scoped_per_source(store):
    save_places(store, [_place(source_id="x1")], source="google-maps")
    save_places(store, [_place(source_id="x1")], source="foody")
    keys = {r["url_key"] for r in store.conn.execute("SELECT url_key FROM documents")}
    assert keys == {"vn://google-maps/x1", "vn://foody/x1"}


def test_extra_url_kept_in_meta(store):
    rec = _place(source_id="gmaps-9", extra={"url": "https://maps.example/x", "rating_label": "4,5 sao"})
    save_places(store, [rec], source="google-maps")
    meta = json.loads(store.conn.execute("SELECT meta FROM documents").fetchone()["meta"])
    assert meta["extra"]["url"] == "https://maps.example/x"
    assert meta["extra"]["rating_label"] == "4,5 sao"


# ---------- no source_id -> pre-R14 behavior preserved exactly ----------


def test_no_source_id_extra_url_unchanged():
    rec = _place(extra={"url": "https://www.foody.vn/quan-an-ngon"})
    assert _record_url(rec, "foody") == "https://www.foody.vn/quan-an-ngon"


def test_no_source_id_fallback_unchanged():
    rec = _place(name="  Quán   PHỞ  ", lat=21.2, lon=106.23)
    rec.pop("extra")
    assert _record_url(rec, "osm") == f"vn://osm/{quote(place_key(rec), safe='')}"


def test_empty_source_id_falls_back():
    rec = _place(source_id="", extra={"url": "https://maps.example/keep"})
    assert _record_url(rec, "google-maps") == "https://maps.example/keep"


def test_whitespace_source_id_falls_back():
    # Whitespace-only is treated as absent — same semantics as place_key —
    # so it can never collapse distinct places onto an empty-slug key.
    rec = _place(source_id="   ")
    rec.pop("extra")
    assert _record_url(rec, "manual") == f"vn://manual/{quote(place_key(rec), safe='')}"
