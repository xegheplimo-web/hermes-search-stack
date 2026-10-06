"""Tests for vn_geo.connectors_ckan_ext (R13-B scope).

Hermetic: reuses the real trimmed CKAN envelopes under
tests/fixtures/vn_geo/enterprises/ plus the real sample saved under
tests/fixtures/r13b/. fetch_fn/sleeper are injected — no network.
"""

import json
from pathlib import Path

import pytest

from vn_geo import VnGeoError, enterprises
from vn_geo import connectors_ckan_ext as c

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "r13b"
ENT_DIR = Path(__file__).parent / "fixtures" / "vn_geo" / "enterprises"
HP_SAMPLE = ENT_DIR / "hp_new_sample.json"
TN_SAMPLE = ENT_DIR / "tn_list_sample.json"
CKAN_SAMPLE = FIXTURE_DIR / "ckan_haiphong_new.json"

# entity schema v1 field names (analysis/r13-interfaces.md §2).
SCHEMA_KEYS = {
    "entity_id",
    "name",
    "kind",
    "category_raw",
    "category",
    "cat_confidence",
    "tax_code",
    "address_text",
    "area_old",
    "province",
    "lat",
    "lng",
    "phone",
    "website",
    "source",
    "source_url",
    "source_id",
    "status",
    "rating",
    "review_count",
    "first_seen",
    "last_seen",
    "checked_at",
    "ttl_class",
    "confidence",
    "raw",
}


def _records(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["result"]["records"]


@pytest.fixture()
def hp_records():
    return _records(HP_SAMPLE)


@pytest.fixture()
def tn_records():
    return _records(TN_SAMPLE)


@pytest.fixture()
def fake_fetch(hp_records, tn_records):
    table = {"haiphong-new": hp_records, "haiphong-dissolved": hp_records, "tayninh-list": tn_records}
    return lambda key: table[key]


# ---------- fixture sanity ----------


def test_ckan_fixture_is_real_envelope():
    payload = json.loads(CKAN_SAMPLE.read_text(encoding="utf-8"))
    assert payload["success"] is True
    assert payload["result"]["total"] == 1429
    assert 1 <= len(payload["result"]["records"]) <= 5


# ---------- config ----------


def test_load_areas_reads_refresh_config():
    areas = c.load_areas()
    names = [a["name"] for a in areas]
    assert {"Yên Dũng", "Hải Phòng", "Tây Ninh"} <= set(names)


def test_load_areas_config_path_override(tmp_path):
    cfg = tmp_path / "areas.json"
    cfg.write_text(json.dumps({"areas": [{"name": "X", "ckan": {"datasets": ["tayninh-list"]}}]}), encoding="utf-8")
    assert c.load_areas(cfg)[0]["name"] == "X"


def test_load_areas_missing_file_raises(tmp_path):
    with pytest.raises(VnGeoError):
        c.load_areas(tmp_path / "nope.json")


# ---------- resolve_datasets ----------


def test_resolve_datasets_hai_phong():
    area_cfg, keys = c.resolve_datasets("Hải Phòng", c.load_areas())
    assert keys == ["haiphong-new", "haiphong-dissolved"]
    assert area_cfg["province"] == "Thành phố Hải Phòng"


def test_resolve_datasets_tay_ninh():
    _, keys = c.resolve_datasets("tay ninh", c.load_areas())  # folded match
    assert keys == ["tayninh-list"]


def test_resolve_datasets_area_without_ckan_raises():
    with pytest.raises(VnGeoError):
        c.resolve_datasets("Yên Dũng", c.load_areas())


def test_resolve_datasets_unknown_area_raises():
    with pytest.raises(VnGeoError):
        c.resolve_datasets("Atlantis", c.load_areas())


def test_resolve_datasets_unknown_dataset_raises():
    areas = [{"name": "X", "province": "P", "ckan": {"datasets": ["bogus"]}}]
    with pytest.raises(VnGeoError):
        c.resolve_datasets("X", areas)


# ---------- shaping ----------


def test_source_for_mapping():
    assert c._source_for("haiphong") == "ckan_hp"
    assert c._source_for("tayninh") == "ckan_tn"


def test_to_entity_matches_schema_v1(hp_records):
    norm = enterprises.normalize(hp_records, province="haiphong", kind="new")[1]
    entity = c.to_entity(norm, dataset_key="haiphong-new", source="ckan_hp", province="Thành phố Hải Phòng")
    assert set(entity) == SCHEMA_KEYS
    assert entity["source"] == "ckan_hp"
    assert entity["tax_code"] == "0202357404"
    assert entity["source_id"] == "0202357404"
    assert entity["name"] == c.fold_d(norm["name"])
    assert entity["address_text"] == c.fold_d(norm["address"])
    assert entity["entity_id"].startswith("e_") and len(entity["entity_id"]) == 14


def test_resource_url_is_ckan_action_api():
    url = c._resource_url("haiphong-new")
    assert url.startswith("https://data.haiphong.gov.vn/api/3/action/datastore_search?resource_id=")
    assert enterprises.DATASETS["haiphong-new"]["resource_id"] in url


# ---------- fetch ----------


def test_fetch_hai_phong_returns_schema_entities(fake_fetch):
    slept: list[float] = []
    out = c.fetch("Hải Phòng", politeness_s=2.0, fetch_fn=fake_fetch, sleeper=slept.append)
    assert len(out) == 3  # two datasets, deduped by (source, source_id)
    assert all(set(e) == SCHEMA_KEYS for e in out)
    assert {e["source"] for e in out} == {"ckan_hp"}
    assert slept == [2.0]  # one sleep between the two dataset fetches


def test_fetch_tay_ninh_source_and_lat_lng(fake_fetch):
    out = c.fetch("Tây Ninh", fetch_fn=fake_fetch, sleeper=lambda *_: None)
    assert len(out) == 4
    assert {e["source"] for e in out} == {"ckan_tn"}
    # Tây Ninh quirk preserved verbatim (lat holds ~106.x, lng holds ~10.x).
    assert out[0]["lat"] is not None and out[0]["lng"] is not None


def test_fetch_uses_province_config(fake_fetch):
    out = c.fetch("Hải Phòng", fetch_fn=fake_fetch, sleeper=lambda *_: None)
    assert {e["province"] for e in out} == {"Thành phố Hải Phòng"}


def test_fetch_accepts_areas_override(hp_records):
    areas = [{"name": "HP", "province": "hp", "ckan": {"datasets": ["haiphong-new"]}}]
    out = c.fetch("HP", areas=areas, fetch_fn=lambda k: hp_records, sleeper=lambda *_: None)
    assert len(out) == 3


def test_fetch_rejects_negative_politeness(fake_fetch):
    with pytest.raises(VnGeoError):
        c.fetch("Hải Phòng", politeness_s=-1, fetch_fn=fake_fetch)
