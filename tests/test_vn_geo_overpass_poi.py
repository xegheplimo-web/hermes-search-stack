"""Hermetic tests for vn_geo.overpass_poi (R4-B). No live network."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo import VnGeoError, overpass_poi
from vn_geo.overpass_poi import (
    build_query,
    fetch,
    ingest,
    load_jsonl,
    normalize,
    records_to_documents,
    save_jsonl,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vn_geo" / "overpass" / "yen_dung.json"


def _fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------- build_query ----------


def test_build_query_shape():
    q = build_query(21.17, 106.20, 21.24, 106.30, ["food"])
    assert q.startswith("[out:json][timeout:60];")
    assert q.rstrip().endswith("out center tags;")
    assert "21.17,106.2,21.24,106.3" in q
    assert 'nwr["amenity"~"^(restaurant|cafe|fast_food)$"]' in q
    assert 'nwr["shop"~"^(restaurant|cafe|fast_food)$"]' in q


def test_build_query_multiple_categories():
    q = build_query(1.0, 2.0, 3.0, 4.0, ["food", "fuel"])
    assert "^(restaurant|cafe|fast_food)$" in q
    assert '"^(fuel)$"' in q


def test_build_query_unknown_category():
    with pytest.raises(VnGeoError, match="unknown POI categories"):
        build_query(1.0, 2.0, 3.0, 4.0, ["food", "nope"])


# ---------- fetch / mirror fallback ----------


def test_fetch_mirror_fallback_on_429(monkeypatch):
    calls: list[str] = []
    good = {"elements": []}

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return _FakeResponse(json.dumps(good).encode())

    monkeypatch.setattr(overpass_poi.urllib.request, "urlopen", fake_urlopen)
    out = fetch("[out:json];out;", mirrors=["https://m1.example/x", "https://m2.example/x"])
    assert out == good
    assert calls == ["https://m1.example/x", "https://m2.example/x"]


def test_fetch_retries_transient_failure(monkeypatch):
    calls: list[str] = []
    good = {"elements": []}

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 504, "Gateway Timeout", {}, None)
        return _FakeResponse(json.dumps(good).encode())

    monkeypatch.setattr(overpass_poi.urllib.request, "urlopen", fake_urlopen)
    out = fetch("q", mirrors=["https://m1.example/x"], attempts=2, backoff=0.0)
    assert out == good
    assert calls == ["https://m1.example/x", "https://m1.example/x"]


def test_fetch_all_mirrors_fail(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(overpass_poi.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(VnGeoError, match="all Overpass mirrors failed"):
        fetch("q", mirrors=["https://m1.example/x", "https://m2.example/x"])


def test_fetch_user_agent_header(monkeypatch):
    seen: dict = {}

    def fake_urlopen(request, timeout=None):
        seen["agent"] = request.get_header("User-agent")
        return _FakeResponse(b'{"elements": []}')

    monkeypatch.setattr(overpass_poi.urllib.request, "urlopen", fake_urlopen)
    fetch("q", mirrors=["https://m1.example/x"])
    assert seen["agent"] == "hermes-vn-geo/0.1"


# ---------- normalize ----------


def test_normalize_real_fixture_way_with_center():
    records = normalize(_fixture_payload())
    assert len(records) == 1
    rec = records[0]
    assert rec["osm_type"] == "way"
    assert rec["osm_id"] == 1219499698
    assert rec["name"] == "Bệnh viện Đa khoa Yên Dũng"
    assert rec["category"] == "services"
    assert rec["lat"] == pytest.approx(21.2027719)
    assert rec["lon"] == pytest.approx(106.2479574)


def test_normalize_skips_unnamed_and_node_relation():
    payload = {
        "elements": [
            {"type": "node", "id": 1, "lat": 21.2, "lon": 106.25, "tags": {"amenity": "restaurant", "name": "Phở Hòa"}},
            {"type": "node", "id": 2, "lat": 21.2, "lon": 106.25, "tags": {"amenity": "restaurant"}},  # unnamed -> skip
            {
                "type": "relation",
                "id": 3,
                "center": {"lat": 21.3, "lon": 106.26},
                "tags": {"shop": "supermarket", "name": "Co.op", "addr:street": "1 Main St", "phone": "+84 1"},
            },
            {"type": "way", "id": 4, "tags": {"amenity": "cafe", "name": "NoCenter"}},  # no center -> skip
        ]
    }
    records = normalize(payload)
    assert [(r["osm_type"], r["osm_id"]) for r in records] == [("node", 1), ("relation", 3)]
    assert records[0]["category"] == "food"
    assert records[0]["lat"] == pytest.approx(21.2)
    assert records[1]["tags"] == {"phone": "+84 1", "addr:street": "1 Main St"}


# ---------- documents / ingest ----------


def test_records_to_documents_mapping():
    records = normalize(_fixture_payload())
    docs = records_to_documents(records)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["url"] == "https://www.openstreetmap.org/way/1219499698"
    assert doc["title"] == "Bệnh viện Đa khoa Yên Dũng"
    assert doc["provider"] == "osm"
    assert doc["format"] == "poi"
    assert doc["meta"] == records[0]
    assert doc["text"].startswith("Bệnh viện Đa khoa Yên Dũng — services.")


def test_ingest_counts(tmp_path):
    records = normalize(_fixture_payload()) + [
        {
            "osm_type": "node",
            "osm_id": 9,
            "lat": 21.2,
            "lon": 106.25,
            "name": "Phở Hòa",
            "category": "food",
            "tags": {},
        },
    ]
    store = SearchStore(tmp_path / "t.db")
    try:
        summary = ingest(store, records)
    finally:
        store.close()
    assert summary == {"documents": 2, "by_category": {"services": 1, "food": 1}}


def test_save_load_jsonl_roundtrip(tmp_path):
    records = normalize(_fixture_payload())
    path = tmp_path / "poi.jsonl"
    save_jsonl(records, path)
    assert load_jsonl(path) == records


def test_load_jsonl_missing_file(tmp_path):
    with pytest.raises(VnGeoError, match="file not found"):
        load_jsonl(tmp_path / "nope.jsonl")


# ---------- CLI ----------


def test_cli_ingest_json(tmp_path, capsys):
    records = normalize(_fixture_payload())
    poi_file = tmp_path / "poi.jsonl"
    save_jsonl(records, poi_file)
    rc = overpass_poi.main(["ingest", "--db", str(tmp_path / "t.db"), "--file", str(poi_file), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["documents"] == 1


def test_cli_fetch_uses_fake_network(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(overpass_poi, "fetch", lambda query, **kw: _fixture_payload())
    out_file = tmp_path / "poi.jsonl"
    rc = overpass_poi.main(
        ["fetch", "--bbox", "21.17,106.20,21.24,106.30", "--categories", "food", "--out", str(out_file), "--json"]
    )
    assert rc == 0
    assert len(load_jsonl(out_file)) == 1
    assert json.loads(capsys.readouterr().out)["records"] == 1


def test_cli_bad_bbox_is_runtime_error(capsys):
    rc = overpass_poi.main(["fetch", "--bbox", "21.24,106.30,21.17,106.20", "--categories", "food", "--out", "x.jsonl"])
    assert rc == 1


def test_cli_missing_args_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        overpass_poi.main(["fetch"])
    assert exc.value.code == 2
