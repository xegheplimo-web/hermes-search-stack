"""Tests for the R13 integration glue in vn_geo.business: seed_from_config,
conn_source and classify_rev (Round-0 interface-gap close, R13-F).

Hermetic: tmp_path databases; connectors stubbed via business._connector_map;
geocode and time.sleep monkeypatched. No live calls.
"""

import json
from types import SimpleNamespace

import pytest

from vn_geo import business


def _masothue_records():
    return [
        {"name": "Nhà nghỉ Bảo An", "address": "xã Nham Biền, Yên Dũng", "tax_code": "0123456789"},
        {"name": "Quán cơm 286", "address": "1 Trần Phú", "tax_code": "9876543210", "lat": 21.2, "lng": 106.25},
    ]


def _stub_map(records, name="connectors_masothue"):
    module = SimpleNamespace(__name__=f"vn_geo.{name}", fetch=lambda area, **kw: [dict(r) for r in records])
    return {name: module}


def _write_config(tmp_path, *, enabled=True, areas=None, **extra):
    cfg = {
        "business_enabled": enabled,
        "areas": areas or [{"name": "Yên Dũng", "province": "Tỉnh Bắc Ninh"}],
    }
    cfg.update(extra)
    path = tmp_path / "areas.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "biz.db")


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(business.time, "sleep", lambda _s: None)


@pytest.fixture()
def stub_geocode(monkeypatch):
    calls: list[str] = []

    def fake(entity):
        calls.append(entity["name"])
        ent = dict(entity)
        ent["lat"], ent["lng"] = 21.2, 106.25
        ent["geocode_status"] = "exact"
        ent["geocode_source"] = "stub"
        ent["confidence"] = max(float(ent.get("confidence") or 0.0), 0.9)
        return ent

    monkeypatch.setattr(business, "geocode", fake)
    return calls


# ---------- conn_source ----------


def test_conn_source_mapping():
    assert business.conn_source("connectors_masothue") == "masothue"
    assert business.conn_source("connectors_ckan_ext", "Thành phố Hải Phòng") == "ckan_hp"
    assert business.conn_source("connectors_ckan_ext", "Tỉnh Tây Ninh") == "ckan_tn"
    assert business.conn_source("manual") == "manual"


# ---------- seed_from_config ----------


def test_seed_disabled_skips(tmp_path, db_path):
    cfg = _write_config(tmp_path, enabled=False)
    out = business.seed_from_config(cfg, db_path)
    assert out["ok"] is True and out["seeded"] == 0
    assert out["skipped"] == "business_enabled=false"
    assert not (tmp_path / "biz.db").exists()


def test_seed_inserts_and_is_idempotent(tmp_path, db_path, monkeypatch, no_sleep, stub_geocode):
    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(_masothue_records()))
    cfg = _write_config(tmp_path)
    out = business.seed_from_config(cfg, db_path)
    assert out["ok"] is True and out["seeded"] == 2
    assert out["areas"][0] == {"area": "Yên Dũng", "fetched": 2, "seeded": 2, "skipped_sources": []}
    assert stub_geocode == ["Nhà nghỉ Bảo An"]  # only the coordinate-less record is geocoded
    assert len(business.query_entities(db_path, "", limit=10)) == 2
    events = business.diff_events(db_path)
    assert sorted(e["kind"] for e in events) == ["entity_new", "entity_new"]
    # second identical run: records re-processed, but zero new rows and zero events
    out2 = business.seed_from_config(cfg, db_path)
    assert out2["seeded"] == 2
    assert len(business.query_entities(db_path, "", limit=10)) == 2
    assert business.diff_events(db_path) == []


def test_seed_geocode_missing_false_keeps_coords_null(tmp_path, db_path, monkeypatch, no_sleep, stub_geocode):
    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(_masothue_records()[:1]))
    cfg = _write_config(tmp_path, business_geocode_missing=False)
    out = business.seed_from_config(cfg, db_path)
    assert out["seeded"] == 1
    assert stub_geocode == []
    row = business.query_entities(db_path, "", limit=5)[0]
    assert row["lat"] is None and row["lng"] is None
    assert row["geocode_status"] == "approximate"


def test_seed_uses_ckan_source_for_ckan_areas(tmp_path, db_path, monkeypatch, no_sleep):
    recs = [{"name": "Doanh nghiệp X", "Dia chi": "1 Lê Lợi", "Latitude": 20.8, "Longitude": 106.6}]
    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(recs, name="connectors_ckan_ext"))
    areas = [{"name": "Hải Phòng", "province": "Thành phố Hải Phòng", "ckan": {"datasets": ["x"]}}]
    cfg = _write_config(tmp_path, areas=areas)
    out = business.seed_from_config(cfg, db_path)
    assert out["seeded"] == 1
    assert business.query_entities(db_path, "", limit=5)[0]["source"] == "ckan_hp"


def test_seed_isolates_connector_errors(tmp_path, db_path, monkeypatch, no_sleep):
    def boom(area, **kw):
        raise RuntimeError("upstream down")

    module = SimpleNamespace(__name__="vn_geo.connectors_masothue", fetch=boom)
    monkeypatch.setattr(business, "_connector_map", lambda: {"connectors_masothue": module})
    cfg = _write_config(tmp_path)
    out = business.seed_from_config(cfg, db_path)
    assert out["ok"] is True and out["seeded"] == 0
    assert "upstream down" in out["areas"][0]["skipped_sources"][0]


def test_seed_no_connectors_raises(tmp_path, db_path, monkeypatch):
    monkeypatch.setattr(business, "_connector_map", dict)
    cfg = _write_config(tmp_path)
    with pytest.raises(business.VnGeoError, match="no connectors available"):
        business.seed_from_config(cfg, db_path)


def test_seed_bad_config_raises(tmp_path, db_path):
    with pytest.raises(business.VnGeoError, match="cannot read config"):
        business.seed_from_config(str(tmp_path / "missing.json"), db_path)


# ---------- classify_rev ----------


def _stale_entity(**over):
    ent = {
        "name": "Nhà nghỉ Bảo An",
        "category_raw": "nhà nghỉ",
        "category": "other",
        "kind": "other",
        "cat_confidence": 0.2,
        "source": "manual",
        "status": "unknown",
    }
    ent.update(over)
    return ent


def test_classify_rev_fixes_and_is_idempotent(db_path):
    business.upsert_entity(db_path, _stale_entity())
    assert business.classify_rev(db_path) == {"checked": 1, "changed": 1}
    row = business.query_entities(db_path, "", limit=5)[0]
    assert row["category"] == "lodging_budget"
    assert row["kind"] == "lodging"
    assert row["cat_confidence"] == pytest.approx(0.9)
    events = business.diff_events(db_path)
    assert [e["kind"] for e in events] == ["entity_new", "entity_changed"]
    assert events[1]["changes"]["category"] == ["other", "lodging_budget"]
    assert business.classify_rev(db_path) == {"checked": 1, "changed": 0}
    assert business.diff_events(db_path) == []


def test_classify_rev_counts_only_changed(db_path):
    business.upsert_entity(db_path, _stale_entity())
    good = _stale_entity(name="Quán cơm 286", category_raw="food", category="food", kind="food", cat_confidence=0.95)
    business.upsert_entity(db_path, good)
    assert business.classify_rev(db_path) == {"checked": 2, "changed": 1}


def test_classify_rev_empty_db(db_path):
    assert business.classify_rev(db_path) == {"checked": 0, "changed": 0}
