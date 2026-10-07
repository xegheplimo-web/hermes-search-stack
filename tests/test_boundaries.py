"""Hermetic tests for vn_geo.boundaries (R14-A).

Fixtures under tests/fixtures/r14a/ are hand-written tiny GeoJSON files —
square, square-with-hole, multipolygon, and a mini 3-province collection
(including a C-shaped polygon whose bbox center is outside, exercising the
centroid grid fallback). No network: ``boundaries._http_get`` is monkeypatched
where fetch is exercised. All databases live under pytest's tmp_path.
"""

import hashlib
import json
import urllib.error
from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo import VnGeoError, admin_units
from vn_geo import boundaries as bd

FIXTURES = Path(__file__).parent / "fixtures" / "r14a"
ADMIN_V2 = Path(__file__).parent / "fixtures" / "vn_geo" / "admin_units" / "v2_sample.json"


def _geom(name: str) -> dict:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data["features"][0]["geometry"]


@pytest.fixture()
def mini_path() -> Path:
    return FIXTURES / "provinces_mini.geojson"


@pytest.fixture()
def db(tmp_path) -> str:
    return str(tmp_path / "test.db")


@pytest.fixture()
def ingested_db(db, mini_path) -> str:
    bd.ingest(db, geojson_path=mini_path)
    return db


# --------------------------------------------------------------------------- point_in_geometry


def test_point_in_square():
    geom = _geom("square.geojson")
    assert bd.point_in_geometry(20.5, 105.5, geom) is True
    assert bd.point_in_geometry(20.001, 105.999, geom) is True
    assert bd.point_in_geometry(19.5, 105.5, geom) is False
    assert bd.point_in_geometry(20.5, 104.5, geom) is False
    assert bd.point_in_geometry(22.0, 107.0, geom) is False


def test_point_in_hole_is_false():
    geom = _geom("square_hole.geojson")
    # the hole is the [4,6]x[4,6] square inside the [0,10] outer ring
    assert bd.point_in_geometry(5.0, 5.0, geom) is False
    assert bd.point_in_geometry(4.5, 5.5, geom) is False
    # inside outer, outside hole
    assert bd.point_in_geometry(2.0, 2.0, geom) is True
    assert bd.point_in_geometry(7.0, 5.0, geom) is True
    # outside entirely
    assert bd.point_in_geometry(11.0, 5.0, geom) is False


def test_point_in_multipolygon():
    geom = _geom("multipolygon.geojson")
    assert bd.point_in_geometry(1.0, 1.0, geom) is True  # first part
    assert bd.point_in_geometry(5.0, 5.0, geom) is True  # second part
    assert bd.point_in_geometry(3.0, 3.0, geom) is False  # gap between parts
    assert bd.point_in_geometry(1.0, 5.0, geom) is False


def test_point_in_geometry_rejects_other_types():
    with pytest.raises(VnGeoError):
        bd.point_in_geometry(0.0, 0.0, {"type": "Point", "coordinates": [0.0, 0.0]})


def test_point_in_geometry_empty():
    geom = {"type": "Polygon", "coordinates": []}
    assert bd.point_in_geometry(0.0, 0.0, geom) is False


# --------------------------------------------------------------------------- representative point / centroid


def test_centroid_of_square_is_center():
    geom = _geom("square.geojson")
    lat, lon = bd._representative_point(geom)
    assert (lat, lon) == (20.5, 105.5)
    assert bd.point_in_geometry(lat, lon, geom) is True


def test_centroid_fallback_for_concave_polygon():
    # mini Bắc Ninh is C-shaped: bbox center (103, 11) sits in the notch
    feats, _ = bd._load_features(FIXTURES / "provinces_mini.geojson")
    c_shape = next(f for f in feats if f["code"] == "bac-ninh")
    geom = c_shape["geometry"]
    bbox = bd._geometry_bbox(geom)
    cy, cx = (bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2
    assert bd.point_in_geometry(cy, cx, geom) is False  # center really is outside
    lat, lon = bd._representative_point(geom)
    assert bd.point_in_geometry(lat, lon, geom) is True


def test_scanline_fallback_on_needle_polygon():
    # 0.0004 deg wide needle — a 100x100 grid over a 1x1 deg bbox can miss it
    geom = {
        "type": "Polygon",
        "coordinates": [
            [
                [0.0, 0.0],
                [0.0002, 0.0],
                [0.0001, 1.0],
                [0.0, 0.0],
            ]
        ],
    }
    lat, lon = bd._representative_point(geom)
    assert bd.point_in_geometry(lat, lon, geom) is True


# --------------------------------------------------------------------------- _load_features / slug


def test_load_features_codes_and_fix(mini_path):
    feats, sha = bd._load_features(mini_path)
    assert sha == hashlib.sha256(mini_path.read_bytes()).hexdigest()
    codes = [f["code"] for f in feats]
    assert codes == ["ha-noi", "bac-ninh", "gamma"]
    assert all(f["source_props_code"] for f in feats)


def test_slug_examples_from_contract():
    assert bd._slug("Hà Nội") == "ha-noi"
    assert bd._slug("TP. Hồ Chí Minh") == "tp-ho-chi-minh"
    assert bd._slug("Đồng Tháp") == "dong-thap"


def test_name_fix_for_mislabeled_source_feature(tmp_path):
    # the pinned source labels Đồng Tháp's geometry "Lạng Sơn" (Ma == "31");
    # _NAME_FIXES must repair it (else the code collides with real Lạng Sơn)
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"Ma": "31", "TinhThanh": "Lạng Sơn"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[105.0, 10.0], [106.0, 10.0], [106.0, 11.0], [105.0, 11.0], [105.0, 10.0]]],
                },
            }
        ],
    }
    path = tmp_path / "dup.geojson"
    path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    feats, _ = bd._load_features(path)
    assert feats[0]["name"] == "Đồng Tháp"
    assert feats[0]["code"] == "dong-thap"
    assert feats[0]["name_raw"] == "Lạng Sơn"


def test_duplicate_codes_raise(tmp_path):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"TinhThanh": "Same"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
            },
            {
                "type": "Feature",
                "properties": {"TinhThanh": "Same"},
                "geometry": {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]},
            },
        ],
    }
    path = tmp_path / "dupes.geojson"
    path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(VnGeoError, match="duplicate"):
        bd._load_features(path)


def test_load_features_rejects_bad_file(tmp_path):
    bad = tmp_path / "bad.geojson"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(VnGeoError):
        bd._load_features(bad)


# --------------------------------------------------------------------------- fetch_source


def _fake_http(payload: bytes, calls: list):
    def fake(url: str, timeout: float) -> bytes:
        calls.append((url, timeout))
        return payload

    return fake


def test_fetch_source_caches(monkeypatch, tmp_path):
    payload = b'{"type":"FeatureCollection","features":[]}'
    calls: list = []
    monkeypatch.setattr(bd, "_http_get", _fake_http(payload, calls))
    monkeypatch.setattr(bd, "EXPECTED_SOURCE_BYTES", len(payload))
    cache = tmp_path / "boundaries"

    p1 = bd.fetch_source(str(cache))
    assert Path(p1).read_bytes() == payload
    assert calls == [(bd.SOURCE_URL, bd.DEFAULT_TIMEOUT)]

    p2 = bd.fetch_source(str(cache))  # cache hit — no second fetch
    assert p2 == p1 and len(calls) == 1

    bd.fetch_source(str(cache), force=True)  # force re-downloads
    assert len(calls) == 2


def test_fetch_source_404_fallback(monkeypatch, tmp_path):
    payload = b'{"type":"FeatureCollection","features":[]}'
    calls: list = []

    def fake(url: str, timeout: float) -> bytes:
        calls.append(url)
        if url == bd.SOURCE_URL:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return payload

    monkeypatch.setattr(bd, "_http_get", fake)
    monkeypatch.setattr(bd, "EXPECTED_SOURCE_BYTES", len(payload))
    path = bd.fetch_source(str(tmp_path / "b"))
    assert calls == [bd.SOURCE_URL, bd._SOURCE_FALLBACK_URL]
    assert Path(path).read_bytes() == payload


def test_fetch_source_size_mismatch_not_cached(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(bd, "_http_get", _fake_http(b"tiny", calls))
    with pytest.raises(VnGeoError, match="size"):
        bd.fetch_source(str(tmp_path / "b"))
    assert not (tmp_path / "b" / bd.CACHE_FILENAME).exists()


def test_fetch_source_http_error(monkeypatch, tmp_path):
    def boom(url: str, timeout: float) -> bytes:
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)

    monkeypatch.setattr(bd, "_http_get", boom)
    with pytest.raises(VnGeoError):
        bd.fetch_source(str(tmp_path / "b"))


# --------------------------------------------------------------------------- ingest / query surface


def test_ingest_counts_and_schema(ingested_db):
    second = bd.ingest(ingested_db, geojson_path=FIXTURES / "provinces_mini.geojson")
    assert second == {"provinces": 3, "new": 0, "updated": 0, "unchanged": 3}
    with SearchStore(ingested_db, create=False) as store:
        n = store.conn.execute("SELECT COUNT(*) FROM boundary_geom").fetchone()[0]
        assert n == 3
        docs = store.conn.execute(
            "SELECT url, format, provider, meta FROM documents_current WHERE format = 'boundary'"
        ).fetchall()
        assert len(docs) == 3
        urls = {d["url"] for d in docs}
        assert "vn://boundary/province/ha-noi" in urls
        meta = json.loads(docs[0]["meta"])
        for key in (
            "code",
            "name",
            "admin_code",
            "bbox",
            "centroid",
            "area_km2",
            "source",
            "source_commit",
            "source_sha256",
            "level",
            "geom_in_table",
            "source_props_code",
        ):
            assert key in meta
        assert meta["level"] == "province" and meta["geom_in_table"] is True


def test_ingest_first_run_counts(db, mini_path):
    counts = bd.ingest(db, geojson_path=mini_path)
    assert counts == {"provinces": 3, "new": 3, "updated": 0, "unchanged": 0}


def test_ingest_updated_on_geom_change(db, mini_path):
    bd.ingest(db, geojson_path=mini_path)
    data = json.loads(mini_path.read_text(encoding="utf-8"))
    ring = data["features"][0]["geometry"]["coordinates"][0]
    data["features"][0]["geometry"]["coordinates"][0] = [[x + 0.1, y + 0.1] for x, y in ring]
    changed = Path(db).parent / "changed.geojson"
    changed.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    counts = bd.ingest(db, geojson_path=changed)
    assert counts == {"provinces": 3, "new": 0, "updated": 1, "unchanged": 2}


def test_lookup_by_code_name_and_folded(ingested_db):
    by_code = bd.lookup(ingested_db, "ha-noi")
    assert by_code is not None
    assert by_code["name"] == "Hà Nội"
    assert by_code["bbox"] == [100.0, 10.0, 101.0, 11.0]
    assert by_code["centroid"] == [10.5, 100.5]
    assert by_code["area_km2"] > 0
    assert by_code["source_commit"] == bd.SOURCE_COMMIT
    assert len(by_code["source_sha256"]) == 64

    assert bd.lookup(ingested_db, "Hà Nội")["code"] == "ha-noi"
    assert bd.lookup(ingested_db, "ha noi")["code"] == "ha-noi"
    assert bd.lookup(ingested_db, "Thành phố Hà Nội")["code"] == "ha-noi"
    assert bd.lookup(ingested_db, "Bắc Ninh")["code"] == "bac-ninh"
    assert bd.lookup(ingested_db, "Tỉnh Bắc Ninh")["code"] == "bac-ninh"


def test_lookup_not_found_and_empty(ingested_db, tmp_path):
    assert bd.lookup(ingested_db, "Zzz Nowhere") is None
    assert bd.lookup(ingested_db, "") is None
    # db exists but has no boundary_geom table
    empty_db = str(tmp_path / "empty.db")
    with SearchStore(empty_db) as store:
        store.ingest_document("https://example.com/x", "hello", title="x")
    assert bd.lookup(empty_db, "ha-noi") is None
    assert bd.list_provinces(empty_db) == []
    assert bd.locate(empty_db, 10.5, 100.5) is None
    assert bd.province_bbox(empty_db, "ha-noi") is None
    assert bd.load_geometry(empty_db, "ha-noi") is None


def test_admin_code_matching(db, mini_path):
    # admin_units v2 fixture carries: Tỉnh Bắc Ninh=24, Thành phố Hà Nội=1
    records = admin_units.normalize(json.loads(ADMIN_V2.read_text(encoding="utf-8")), 2)
    with SearchStore(db) as store:
        admin_units.ingest(store, records)
    bd.ingest(db, geojson_path=mini_path)
    assert bd.lookup(db, "ha-noi")["admin_code"] == 1
    assert bd.lookup(db, "bac-ninh")["admin_code"] == 24
    assert bd.lookup(db, "gamma")["admin_code"] is None


def test_admin_code_none_without_admin_docs(ingested_db):
    assert bd.lookup(ingested_db, "ha-noi")["admin_code"] is None


def test_list_provinces(ingested_db):
    rows = bd.list_provinces(ingested_db)
    assert [r["code"] for r in rows] == ["bac-ninh", "gamma", "ha-noi"]
    assert all(r["centroid"] and r["bbox"] for r in rows)


def test_locate_hits_and_miss(ingested_db):
    hit = bd.locate(ingested_db, 10.5, 100.5)
    assert hit == {"code": "ha-noi", "name": "Hà Nội"}
    # multipolygon second part
    assert bd.locate(ingested_db, 10.5, 107.5)["code"] == "gamma"
    # inside bac-ninh bbox but in the C-notch -> not bac-ninh
    assert bd.locate(ingested_db, 11.0, 103.0) is None
    assert bd.locate(ingested_db, 0.0, 0.0) is None


def test_centroid_round_trip(ingested_db):
    for prov in bd.list_provinces(ingested_db):
        lat, lon = prov["centroid"]
        assert bd.locate(ingested_db, lat, lon) == {"code": prov["code"], "name": prov["name"]}


def test_province_bbox_and_load_geometry(ingested_db):
    bbox = bd.province_bbox(ingested_db, "ha-noi")
    assert bbox == (100.0, 10.0, 101.0, 11.0)
    assert bd.province_bbox(ingested_db, "nope") is None
    geom = bd.load_geometry(ingested_db, "gamma")
    assert geom["type"] == "MultiPolygon"
    assert len(geom["coordinates"]) == 2
    assert bd.load_geometry(ingested_db, "nope") is None


# --------------------------------------------------------------------------- CLI


def test_cli_ingest_lookup_locate_list(tmp_path, capsys, mini_path):
    db = str(tmp_path / "cli.db")
    assert bd.main(["ingest", "--db", db, "--file", str(mini_path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["provinces"] == 3 and out["new"] == 3

    assert bd.main(["lookup", "Hà Nội", "--db", db, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["code"] == "ha-noi"

    assert bd.main(["lookup", "--code", "gamma", "--db", db, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["name"] == "Gamma"

    assert bd.main(["locate", "--db", db, "--lat", "10.5", "--lon", "107.5", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == {"code": "gamma", "name": "Gamma"}

    assert bd.main(["list", "--db", db, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 3


def test_cli_locate_miss(tmp_path, capsys, mini_path):
    db = str(tmp_path / "cli.db")
    bd.main(["ingest", "--db", db, "--file", str(mini_path)])
    assert bd.main(["locate", "--db", db, "--lat", "0", "--lon", "0"]) == 0
    assert "no province" in capsys.readouterr().out


def test_cli_lookup_needs_query(tmp_path, capsys, mini_path):
    db = str(tmp_path / "cli.db")
    bd.main(["ingest", "--db", db, "--file", str(mini_path)])
    assert bd.main(["lookup", "--db", db]) == 1
    assert "needs a province name or code" in capsys.readouterr().err


def test_cli_errors(tmp_path, capsys):
    # missing db -> exit 2 (IO)
    assert bd.main(["lookup", "x", "--db", str(tmp_path / "missing.db")]) == 2
    # ingest missing file -> exit 2
    assert bd.main(["ingest", "--db", str(tmp_path / "d.db"), "--file", str(tmp_path / "none.geojson")]) == 2
    capsys.readouterr()


def test_cli_fetch(monkeypatch, tmp_path, capsys):
    payload = b'{"type":"FeatureCollection","features":[]}'
    monkeypatch.setattr(bd, "_http_get", _fake_http(payload, []))
    monkeypatch.setattr(bd, "EXPECTED_SOURCE_BYTES", len(payload))
    rc = bd.main(["fetch", "--cache-dir", str(tmp_path / "cache"), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["bytes"] == len(payload)
