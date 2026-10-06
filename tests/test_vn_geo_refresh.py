"""Tests for vn_geo.refresh (R5-A scope, contract analysis/r5-interfaces.md §3).

Hermetic: tmp_path databases, monkeypatched fetchers, round-4 fixtures reused
read-only. No live network.
"""

import json
from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo import VnGeoError, admin_units, enterprises, overpass_poi, places, refresh
from vn_geo.refresh import coverage, load_config, main

FIXTURES = Path(__file__).parent / "fixtures" / "vn_geo"
CONFIG_AREAS = FIXTURES / "refresh" / "config_areas.json"
V2_SAMPLE = FIXTURES / "admin_units" / "v2_sample.json"
OVERPASS_YD = FIXTURES / "overpass" / "yen_dung.json"
HP_NEW = FIXTURES / "enterprises" / "hp_new_sample.json"
SCAN_A = FIXTURES / "places" / "scan_a.jsonl"


def _v2_payload() -> list:
    return json.loads(V2_SAMPLE.read_text(encoding="utf-8"))


def _v2_records() -> list:
    return admin_units.normalize(_v2_payload(), 2)


def _overpass_payload() -> dict:
    return json.loads(OVERPASS_YD.read_text(encoding="utf-8"))


def _overpass_records() -> list:
    return overpass_poi.normalize(_overpass_payload())


def _hp_records() -> list:
    return json.loads(HP_NEW.read_text(encoding="utf-8"))["result"]["records"]


def _write_config(tmp_path: Path, data, name: str = "cfg.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _patch_fetchers(monkeypatch, *, admin=None, overpass=None, ckan=None) -> None:
    monkeypatch.setattr(admin_units, "fetch_all", admin or (lambda *a, **k: _v2_payload()))
    monkeypatch.setattr(overpass_poi, "fetch", overpass or (lambda *a, **k: _overpass_payload()))
    monkeypatch.setattr(enterprises, "fetch_dataset", ckan or (lambda *a, **k: _hp_records()))


def _boom(message: str = "should not fetch"):
    def fetch(*a, **k):
        raise AssertionError(message)

    return fetch


def _failing(message: str):
    def fetch(*a, **k):
        raise VnGeoError(message)

    return fetch


def _refresh_events(store) -> list:
    return [
        json.loads(r["payload"])
        for r in store.conn.execute("SELECT payload FROM events WHERE kind = 'refresh_run' ORDER BY id")
    ]


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def config():
    return load_config(CONFIG_AREAS)


@pytest.fixture()
def full_store(store):
    """A store holding documents from all four coverage sources."""
    admin_units.ingest(store, _v2_records())
    overpass_poi.ingest(store, _overpass_records())
    enterprises.ingest(store, _hp_records(), dataset_key="haiphong-new")
    places.save_places(store, places.load_scan_jsonl(SCAN_A), source="google-maps")
    return store


# ---------- load_config ----------


def test_load_config_fixture_ok(config):
    areas = config["areas"]
    assert [a["name"] for a in areas] == ["Yên Dũng", "Hải Phòng"]
    assert areas[0]["province"] == "Tỉnh Bắc Ninh"
    assert areas[0]["overpass"]["bbox"] == [21.17, 106.2, 21.24, 106.3]
    assert areas[0]["overpass"]["categories"] == ["food"]
    assert areas[1]["ckan"]["datasets"] == ["haiphong-new"]


@pytest.mark.parametrize("data", [[], None, "x", 42])
def test_load_config_not_object(tmp_path, data):
    with pytest.raises(VnGeoError, match="areas"):
        load_config(_write_config(tmp_path, data))


@pytest.mark.parametrize("data", [{}, {"areas": []}, {"areas": {}}, {"areas": None}])
def test_load_config_areas_missing_or_empty(tmp_path, data):
    with pytest.raises(VnGeoError, match="areas"):
        load_config(_write_config(tmp_path, data))


@pytest.mark.parametrize("area", [[], "x", 5, None])
def test_load_config_area_not_object(tmp_path, area):
    with pytest.raises(VnGeoError, match=r"areas\[0\]"):
        load_config(_write_config(tmp_path, {"areas": [area]}))


@pytest.mark.parametrize("name", [None, "", "   ", 5])
def test_load_config_bad_name(tmp_path, name):
    with pytest.raises(VnGeoError, match="name"):
        load_config(_write_config(tmp_path, {"areas": [{"name": name}]}))


def test_load_config_bad_province(tmp_path):
    with pytest.raises(VnGeoError, match="province"):
        load_config(_write_config(tmp_path, {"areas": [{"name": "Bad Area", "province": 7}]}))


@pytest.mark.parametrize("ov", ["x", 5, []])
def test_load_config_overpass_not_object(tmp_path, ov):
    with pytest.raises(VnGeoError, match="overpass"):
        load_config(_write_config(tmp_path, {"areas": [{"name": "A", "overpass": ov}]}))


@pytest.mark.parametrize("bbox", [None, [1, 2, 3], [1, 2, 3, 4, 5], "s,w,n,e", ["a", 0, 0, 0], [True, 0, 0, 0]])
def test_load_config_bad_bbox(tmp_path, bbox):
    area = {"name": "A", "overpass": {"bbox": bbox, "categories": ["food"]}}
    with pytest.raises(VnGeoError, match="bbox"):
        load_config(_write_config(tmp_path, {"areas": [area]}))


@pytest.mark.parametrize("cats", [None, [], "food", 5])
def test_load_config_categories_not_list(tmp_path, cats):
    area = {"name": "A", "overpass": {"bbox": [1, 2, 3, 4], "categories": cats}}
    with pytest.raises(VnGeoError, match="categories"):
        load_config(_write_config(tmp_path, {"areas": [area]}))


def test_load_config_unknown_category(tmp_path):
    area = {"name": "A", "overpass": {"bbox": [1, 2, 3, 4], "categories": ["food", "hotel"]}}
    with pytest.raises(VnGeoError, match="hotel"):
        load_config(_write_config(tmp_path, {"areas": [area]}))


@pytest.mark.parametrize("ck", ["x", 5, []])
def test_load_config_ckan_not_object(tmp_path, ck):
    with pytest.raises(VnGeoError, match="ckan"):
        load_config(_write_config(tmp_path, {"areas": [{"name": "A", "ckan": ck}]}))


@pytest.mark.parametrize("ds", [None, [], "haiphong-new"])
def test_load_config_datasets_not_list(tmp_path, ds):
    with pytest.raises(VnGeoError, match="datasets"):
        load_config(_write_config(tmp_path, {"areas": [{"name": "A", "ckan": {"datasets": ds}}]}))


def test_load_config_unknown_dataset(tmp_path):
    area = {"name": "A", "ckan": {"datasets": ["haiphong-new", "bogus-key"]}}
    with pytest.raises(VnGeoError, match="bogus-key"):
        load_config(_write_config(tmp_path, {"areas": [area]}))


def test_load_config_names_offending_area(tmp_path):
    cfg = {"areas": [{"name": "Yên Dũng"}, {"name": "Broken", "ckan": {"datasets": []}}]}
    with pytest.raises(VnGeoError, match="Broken"):
        load_config(_write_config(tmp_path, cfg))


def test_load_config_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.json")


def test_load_config_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_config(path)


# ---------- coverage ----------


def test_coverage_empty_store_all_gaps(store, config):
    report = coverage(store, config=config)
    assert report["checked_at"]
    yen, hp = report["areas"]
    assert yen["name"] == "Yên Dũng"
    assert hp["name"] == "Hải Phòng"
    for a in (yen, hp):
        assert all(s["present"] is False for s in a["sources"].values())
        assert all(s["count"] == 0 for s in a["sources"].values())
        assert a["gaps"] == ["admin", "overpass", "ckan", "places"]
    assert "scope" not in yen["sources"]["overpass"]  # bbox configured
    assert hp["sources"]["overpass"]["scope"] == "global"  # no bbox -> global


def test_coverage_counts_per_source(full_store, config):
    report = coverage(full_store, config=config)
    yen, hp = report["areas"]

    s = yen["sources"]
    assert s["admin"]["count"] == 11  # Tỉnh Bắc Ninh province + its 10 wards
    assert s["admin"]["present"] is True
    assert s["admin"]["latest_fetched_at"]
    assert s["overpass"]["count"] == 1  # the hospital inside the bbox
    assert "scope" not in s["overpass"]
    assert s["ckan"] == {"count": 0, "present": False, "latest_fetched_at": None}
    # 5 scan_a rows + the osm hospital (its meta contains "Yên Dũng") — the documented overlap
    assert s["places"]["count"] == 6
    assert yen["gaps"] == ["ckan"]

    s = hp["sources"]
    assert s["admin"]["count"] == 11  # Thành phố Hải Phòng + its 10 wards
    assert s["overpass"]["count"] == 1  # no bbox -> all osm docs
    assert s["overpass"]["scope"] == "global"
    assert s["ckan"]["count"] == 3
    assert s["places"]["count"] == 0
    assert hp["gaps"] == ["places"]


def test_coverage_area_arg_no_config(full_store):
    (a,) = coverage(full_store, area="Yên Dũng")["areas"]
    assert a["name"] == "Yên Dũng"
    assert a["sources"]["admin"]["count"] == 1  # only the ward Phường Yên Dũng's meta matches
    assert a["sources"]["overpass"]["scope"] == "global"
    assert a["sources"]["overpass"]["count"] == 1
    assert a["sources"]["places"]["count"] == 6
    assert a["sources"]["ckan"]["count"] == 0
    assert a["gaps"] == ["ckan"]


def test_coverage_no_args_empty_areas(full_store):
    report = coverage(full_store)
    assert report["areas"] == []


def test_coverage_area_without_config_overpass_is_global(full_store):
    (a,) = coverage(full_store, config={"areas": [{"name": "Nowhere Land"}]})["areas"]
    assert a["sources"]["overpass"]["scope"] == "global"
    assert a["sources"]["overpass"]["count"] == 1
    assert a["gaps"] == ["admin", "ckan", "places"]


def test_coverage_latest_fetched_at(full_store, config):
    report = coverage(full_store, config=config)
    yen = report["areas"][0]["sources"]
    # places mixes scan timestamps (2026-10-05) with the osm doc's ingest time (now)
    assert yen["places"]["latest_fetched_at"] >= "2026-10-05T09:12:00+07:00"
    assert yen["admin"]["latest_fetched_at"]
    assert yen["ckan"]["latest_fetched_at"] is None
    hp = report["areas"][1]["sources"]
    assert hp["places"]["latest_fetched_at"] is None


# ---------- refresh: validation + dry run ----------


def test_refresh_unknown_source(store, config):
    with pytest.raises(VnGeoError, match="nope"):
        refresh.refresh(store, config=config, sources=["admin", "nope"])


def test_refresh_dry_run_makes_no_fetch(store, config, monkeypatch):
    monkeypatch.setattr(admin_units, "fetch_all", _boom())
    monkeypatch.setattr(overpass_poi, "fetch", _boom())
    monkeypatch.setattr(enterprises, "fetch_dataset", _boom())
    sleeps = []
    result = refresh.refresh(store, config=config, dry_run=True, sleeper=sleeps.append)
    assert result == {
        "dry_run": True,
        "plan": [
            {"source": "admin", "area": "Yên Dũng", "version": 2},
            {
                "source": "overpass",
                "area": "Yên Dũng",
                "bbox": [21.17, 106.2, 21.24, 106.3],
                "categories": ["food"],
            },
            {"source": "ckan", "area": "Hải Phòng", "datasets": ["haiphong-new"]},
        ],
    }
    assert sleeps == []


def test_refresh_dry_run_validates_sources(store, config):
    with pytest.raises(VnGeoError):
        refresh.refresh(store, config=config, sources=["bogus"], dry_run=True)


def test_refresh_bad_area_name(store):
    with pytest.raises(VnGeoError, match=r"areas\[1\]"):
        refresh.refresh(store, config={"areas": [{"name": "Ok"}, {"name": ""}]}, dry_run=True)


# ---------- refresh: execute ----------


def test_refresh_execute_counts_and_event(store, config, monkeypatch):
    _patch_fetchers(monkeypatch)
    sleeps = []
    result = refresh.refresh(store, config=config, sleeper=sleeps.append)
    assert result["dry_run"] is False
    yen, hp = result["areas"]
    assert yen["sources"]["admin"] == {"new": 33, "updated": 0, "unchanged": 0}
    assert yen["sources"]["overpass"] == {"new": 1, "updated": 0, "unchanged": 0}
    assert hp["sources"]["ckan"] == {"new": 3, "updated": 0, "unchanged": 0}
    assert result["totals"] == {"new": 37, "updated": 0, "unchanged": 0, "errors": 0}
    # 3 fetches (admin, overpass, ckan); sleeper fires before all but the first
    assert sleeps == [1.5, 1.5]
    (event,) = _refresh_events(store)
    assert event["areas"] == ["Yên Dũng", "Hải Phòng"]
    assert event["sources"] == ["admin", "overpass", "ckan"]
    assert event["totals"] == {"new": 37, "updated": 0, "unchanged": 0, "errors": 0}


def test_refresh_rerun_all_unchanged(store, config, monkeypatch):
    _patch_fetchers(monkeypatch)
    refresh.refresh(store, config=config, sleeper=lambda s: None)
    result = refresh.refresh(store, config=config, sleeper=lambda s: None)
    for a in result["areas"]:
        for src, c in a["sources"].items():
            assert c["new"] == 0 and c["updated"] == 0, (a["name"], src, c)
    assert result["totals"] == {"new": 0, "updated": 0, "unchanged": 37, "errors": 0}
    assert len(_refresh_events(store)) == 2


def test_refresh_detects_updated_documents(store, config, monkeypatch):
    _patch_fetchers(monkeypatch)
    refresh.refresh(store, config=config, sleeper=lambda s: None)
    changed = [dict(r) for r in _hp_records()]
    changed[0]["Ten doanh nghiep/Ten don vi phu thuoc"] = "CÔNG TY ĐỔI TÊN MỚI"
    _patch_fetchers(monkeypatch, ckan=lambda *a, **k: changed)
    result = refresh.refresh(store, config=config, sleeper=lambda s: None)
    hp = result["areas"][1]
    assert hp["sources"]["ckan"] == {"new": 0, "updated": 1, "unchanged": 2}
    yen = result["areas"][0]
    assert yen["sources"]["admin"]["unchanged"] == 33
    assert yen["sources"]["overpass"]["unchanged"] == 1


def test_refresh_error_isolation(store, config, monkeypatch):
    def down(*a, **k):
        raise VnGeoError("overpass down")

    _patch_fetchers(monkeypatch, overpass=down)
    result = refresh.refresh(store, config=config, sleeper=lambda s: None)
    yen, hp = result["areas"]
    assert yen["sources"]["admin"]["new"] == 33
    assert yen["sources"]["overpass"] == {"error": "overpass down"}
    assert hp["sources"]["ckan"]["new"] == 3
    assert result["totals"] == {"new": 36, "updated": 0, "unchanged": 0, "errors": 1}
    (event,) = _refresh_events(store)
    assert event["totals"]["errors"] == 1


def test_refresh_all_steps_fail(store, config, monkeypatch):
    monkeypatch.setattr(admin_units, "fetch_all", _failing("admin down"))
    monkeypatch.setattr(overpass_poi, "fetch", _failing("osm down"))
    monkeypatch.setattr(enterprises, "fetch_dataset", _failing("ckan down"))
    result = refresh.refresh(store, config=config, sleeper=lambda s: None)
    assert result["totals"]["errors"] == 3
    assert result["totals"]["new"] == 0


def test_refresh_sources_subset(store, config, monkeypatch):
    monkeypatch.setattr(admin_units, "fetch_all", _boom())
    monkeypatch.setattr(overpass_poi, "fetch", _boom())
    monkeypatch.setattr(enterprises, "fetch_dataset", lambda *a, **k: _hp_records())
    result = refresh.refresh(store, config=config, sources=["ckan"], sleeper=lambda s: None)
    yen, hp = result["areas"]
    assert yen["sources"] == {}
    assert hp["sources"]["ckan"]["new"] == 3
    assert result["totals"]["new"] == 3


def test_refresh_admin_attaches_to_global_without_areas(store, monkeypatch):
    _patch_fetchers(monkeypatch)
    result = refresh.refresh(store, config={"areas": []}, sources=["admin"], sleeper=lambda s: None)
    assert result["areas"] == [{"name": "(global)", "sources": {"admin": {"new": 33, "updated": 0, "unchanged": 0}}}]


def test_refresh_sleeper_min_interval(store, config, monkeypatch):
    _patch_fetchers(monkeypatch)
    sleeps = []
    refresh.refresh(store, config=config, min_interval=0.25, sleeper=sleeps.append)
    assert sleeps == [0.25, 0.25]


def test_refresh_multi_dataset_ckan(store, tmp_path, monkeypatch):
    cfg = {"areas": [{"name": "HP", "ckan": {"datasets": ["haiphong-new", "haiphong-dissolved"]}}]}
    calls = []

    def fake_fetch(key, **k):
        calls.append(key)
        return _hp_records() if key == "haiphong-new" else _hp_records()[:2]

    monkeypatch.setattr(enterprises, "fetch_dataset", fake_fetch)
    sleeps = []
    result = refresh.refresh(store, config=cfg, sources=["ckan"], sleeper=sleeps.append)
    assert calls == ["haiphong-new", "haiphong-dissolved"]
    (hp,) = result["areas"]
    # urls carry the resource_id, so the same rows under a second dataset are new
    assert hp["sources"]["ckan"] == {"new": 5, "updated": 0, "unchanged": 0}
    assert sleeps == [1.5]


# ---------- cli ----------


def test_cli_coverage_json(tmp_path, capsys):
    db = str(tmp_path / "c.db")
    with SearchStore(db) as s:
        enterprises.ingest(s, _hp_records(), dataset_key="haiphong-new")
    assert main(["coverage", "--db", db, "--config", str(CONFIG_AREAS), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    areas = {a["name"]: a for a in payload["areas"]}
    assert areas["Hải Phòng"]["sources"]["ckan"]["count"] == 3
    assert areas["Yên Dũng"]["gaps"] == ["admin", "overpass", "ckan", "places"]


def test_cli_coverage_area_human(tmp_path, capsys):
    db = str(tmp_path / "c.db")
    assert main(["coverage", "--db", db, "--area", "Yên Dũng"]) == 0
    out = capsys.readouterr().out
    assert "Yên Dũng" in out
    assert "gaps:" in out
    assert "missing" in out


def test_cli_run_dry_run_no_fetch(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(admin_units, "fetch_all", _boom())
    monkeypatch.setattr(overpass_poi, "fetch", _boom())
    monkeypatch.setattr(enterprises, "fetch_dataset", _boom())
    db = str(tmp_path / "r.db")
    assert main(["run", "--db", db, "--config", str(CONFIG_AREAS), "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert len(payload["plan"]) == 3


def test_cli_run_executes_and_rerun_is_idempotent(tmp_path, capsys, monkeypatch):
    _patch_fetchers(monkeypatch)
    db = str(tmp_path / "r.db")
    assert main(["run", "--db", db, "--config", str(CONFIG_AREAS), "--min-interval", "0"]) == 0
    out = capsys.readouterr().out
    assert "admin: new 33" in out
    assert "overpass: new 1" in out
    assert "ckan: new 3" in out
    assert "totals: new 37" in out
    assert main(["run", "--db", db, "--config", str(CONFIG_AREAS), "--min-interval", "0", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"] == {"new": 0, "updated": 0, "unchanged": 37, "errors": 0}


def test_cli_run_error_exit_1(tmp_path, capsys, monkeypatch):
    def down(*a, **k):
        raise VnGeoError("overpass down")

    _patch_fetchers(monkeypatch, overpass=down)
    db = str(tmp_path / "r.db")
    assert main(["run", "--db", db, "--config", str(CONFIG_AREAS), "--min-interval", "0"]) == 1
    out = capsys.readouterr().out
    assert "overpass: error: overpass down" in out
    assert "errors 1" in out


def test_cli_run_missing_config_exit_2(tmp_path):
    db = str(tmp_path / "r.db")
    assert main(["run", "--db", db, "--config", str(tmp_path / "nope.json")]) == 2


def test_cli_run_unknown_source_exit_1(tmp_path, monkeypatch):
    _patch_fetchers(monkeypatch)
    db = str(tmp_path / "r.db")
    assert main(["run", "--db", db, "--config", str(CONFIG_AREAS), "--sources", "admin,bogus"]) == 1


def test_cli_db_is_directory_exit_2(tmp_path):
    assert main(["coverage", "--db", str(tmp_path)]) == 2
