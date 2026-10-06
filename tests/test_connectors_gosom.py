"""Hermetic tests for vn_geo.connectors_gosom (R13-E). No network, no Docker.

The fixture ``tests/fixtures/r13e/sample.jsonl`` holds the first 3 REAL lines of
the R13-E scrape output (``agent_logs/r13e_gmaps.jsonl``); synthetic records cover
status + missing-field edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vn_geo import VnGeoError
from vn_geo.connectors_gosom import (
    DEFAULT_RESULTS,
    SOURCE,
    fetch,
    load_records,
    main,
    normalize_status,
    parse_record,
    save_jsonl,
)

FIXTURE = Path(__file__).parent / "fixtures" / "r13e" / "sample.jsonl"

# entity schema v1 (§1) keys the adapter is contracted to emit.
REQUIRED_KEYS = frozenset(
    {
        "name",
        "category_raw",
        "address_text",
        "phone",
        "website",
        "lat",
        "lng",
        "source_id",
        "status",
        "source",
        "source_url",
        "rating",
        "review_count",
    }
)


# ---------- fixtures / loading ----------


def test_fixture_has_three_real_records():
    raw = load_records(FIXTURE)
    assert len(raw) == 3
    assert raw[0]["title"].startswith("Nha Khoa Huy T")


def test_default_results_path_points_at_seed_run():
    assert DEFAULT_RESULTS.name == "r13e_gmaps.jsonl"
    assert DEFAULT_RESULTS.parent.name == "agent_logs"


# ---------- parse_record mapping (real records) ----------


def test_parse_record_maps_first_real_record():
    rec = parse_record(load_records(FIXTURE)[0])
    assert rec["name"] == "Nha Khoa Huy Tâm- Yên Dũng -BG"
    assert rec["category_raw"] == "Nha sĩ"
    assert rec["address_text"] == "Trần nhân tông TT, Tổ dân phố 5, Yên Dũng, Bắc Ninh, Việt Nam"
    assert rec["phone"] == "+84 988 834 579"
    assert rec["website"] == ""  # gosom web_site was empty
    assert rec["lat"] == pytest.approx(21.2025549)
    assert rec["lng"] == pytest.approx(106.2487459)
    assert rec["source_id"] == "ChIJBdL50xVuNTERKUSXGNaVQJo"  # place_id preferred
    assert rec["status"] == "unknown"  # gosom status was ""
    assert rec["source"] == SOURCE == "gmaps"
    assert rec["source_url"].startswith("https://www.google.com/maps/place/")
    assert rec["rating"] == pytest.approx(4.7)
    assert rec["review_count"] == 14


def test_parse_record_keeps_required_schema_keys():
    for raw in load_records(FIXTURE):
        rec = parse_record(raw)
        assert REQUIRED_KEYS <= rec.keys()
        assert rec["raw"] is raw  # untouched gosom object preserved


def test_fold_d_applied_to_name_and_address():
    rec = parse_record(load_records(FIXTURE)[1])  # "Phòng Khám Đa Khoa Bác Sĩ Đăng Khoa"
    assert rec["name_folded"] == "Phòng Khám da Khoa Bác Sĩ dăng Khoa"  # only đ/Đ -> d
    assert "Đ" not in rec["name_folded"] and "đ" not in rec["name_folded"]
    assert "Đ" not in rec["address_folded"] and "đ" not in rec["address_folded"]


def test_parse_record_categories_fallback_and_website_alias():
    rec = parse_record({"title": "X", "categories": ["Quán ăn"], "website": "https://x.vn"})
    assert rec["category_raw"] == "Quán ăn"
    assert rec["website"] == "https://x.vn"  # fallback key when web_site missing


def test_source_id_prefers_place_id_then_cid():
    assert parse_record({"place_id": "P1", "cid": "C1"})["source_id"] == "P1"
    assert parse_record({"cid": "C1"})["source_id"] == "C1"
    assert parse_record({})["source_id"] == ""


def test_parse_record_missing_fields_defaults():
    rec = parse_record({})
    assert rec["name"] == ""
    assert rec["category_raw"] == ""
    assert rec["address_text"] == ""
    assert rec["phone"] == ""
    assert rec["website"] == ""
    assert rec["lat"] is None
    assert rec["lng"] is None
    assert rec["source_id"] == ""
    assert rec["status"] == "unknown"
    assert rec["rating"] is None
    assert rec["review_count"] is None


def test_parse_record_longtitude_typo_alias():
    rec = parse_record({"latitude": 21.1, "longtitude": 106.2})
    assert rec["lat"] == pytest.approx(21.1)
    assert rec["lng"] == pytest.approx(106.2)


def test_parse_record_non_dict_raises():
    with pytest.raises(VnGeoError, match="must be an object"):
        parse_record(["not", "a", "dict"])  # type: ignore[arg-type]


# ---------- normalize_status ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "unknown"),
        (None, "unknown"),
        ("Permanently closed", "closed"),
        ("Temporarily closed", "closed"),
        ("Đã đóng cửa", "closed"),
        ("Đang mở cửa", "open"),
        ("Open ⋅ Closes 9 PM", "open"),
        ("Bữa nửa buổi", "unknown"),  # gosom quirk -> not open/closed
        (["Đóng cửa"], "closed"),
    ],
)
def test_normalize_status(raw, expected):
    assert normalize_status(raw) == expected


# ---------- load_records ----------


def test_load_records_jsonl_fixture():
    assert len(load_records(FIXTURE)) == 3


def test_load_records_json_array(tmp_path):
    p = tmp_path / "arr.json"
    p.write_text(json.dumps([{"title": "A"}, {"title": "B"}]), encoding="utf-8")
    assert [r["title"] for r in load_records(p)] == ["A", "B"]


def test_load_records_single_object(tmp_path):
    p = tmp_path / "one.json"
    p.write_text(json.dumps({"title": "Solo"}), encoding="utf-8")
    assert load_records(p) == [{"title": "Solo"}]


def test_load_records_skips_non_dict_array_items(tmp_path):
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps([{"title": "A"}, 42, "x"]), encoding="utf-8")
    assert load_records(p) == [{"title": "A"}]


def test_load_records_empty_file(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    assert load_records(p) == []


def test_load_records_invalid_jsonl_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"title": "ok"}\n{not json}\n', encoding="utf-8")
    with pytest.raises(VnGeoError, match="invalid JSONL"):
        load_records(p)


def test_load_records_missing_file_raises():
    with pytest.raises(VnGeoError, match="file not found"):
        load_records(FIXTURE.parent / "does-not-exist.jsonl")


def test_save_jsonl_round_trip(tmp_path):
    records = [{"title": "A", "n": 1}, {"title": "B"}]
    dest = tmp_path / "sub" / "out.jsonl"
    save_jsonl(records, dest)
    assert load_records(dest) == records


# ---------- fetch ----------


def test_fetch_returns_schema_dicts():
    out = fetch(path=FIXTURE)
    assert len(out) == 3
    assert all(REQUIRED_KEYS <= rec.keys() for rec in out)
    assert all(rec["source"] == "gmaps" for rec in out)


def test_fetch_area_filter_matches_and_is_diacritic_insensitive():
    assert len(fetch("Yên Dũng", path=FIXTURE)) == 3
    assert len(fetch("Yen Dung", path=FIXTURE)) == 3  # diacritics-insensitive
    assert fetch("Hà Nội", path=FIXTURE) == []


# ---------- CLI ----------


def test_cli_main_json_ok(capsys):
    rc = main(["--path", str(FIXTURE), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["records"] == 3


def test_cli_main_missing_file_returns_1(tmp_path, capsys):
    rc = main(["--path", str(tmp_path / "nope.jsonl")])
    assert rc == 1
    assert "error:" in capsys.readouterr().err
