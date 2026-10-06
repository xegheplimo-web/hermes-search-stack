"""Hermetic tests for vn_geo.admin_units (R4-A).

Fixtures under tests/fixtures/vn_geo/admin_units/ are raw API payloads trimmed
from one live fetch. All databases live under pytest's tmp_path; no network —
HTTP is exercised only via monkeypatched ``urllib.request.urlopen``.
"""

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo import VnGeoError
from vn_geo import admin_units as au

FIXTURES = Path(__file__).parent / "fixtures" / "vn_geo" / "admin_units"


@pytest.fixture()
def v2_payload() -> list[dict]:
    return json.loads((FIXTURES / "v2_sample.json").read_text(encoding="utf-8"))


@pytest.fixture()
def v1_payload() -> list[dict]:
    return json.loads((FIXTURES / "v1_sample.json").read_text(encoding="utf-8"))


@pytest.fixture()
def store(tmp_path) -> SearchStore:
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


class _FakeResponse:
    """Minimal context-manager response for monkeypatched urlopen."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


# --------------------------------------------------------------------------- normalize (v2)


def test_normalize_v2_flattens_provinces_and_wards(v2_payload):
    records = au.normalize(v2_payload, 2)
    provinces = [r for r in records if r["level"] == "province"]
    wards = [r for r in records if r["level"] == "ward"]
    assert len(provinces) == len(v2_payload)
    assert len(wards) == sum(len(p.get("wards") or []) for p in v2_payload)
    assert all(r["version"] == 2 for r in records)
    # v2 abolished districts
    assert all("district_code" not in r for r in wards)
    assert all("districts" not in r for r in records)


def test_normalize_v2_ward_fields(v2_payload):
    records = au.normalize(v2_payload, 2)
    bac_ninh = next(r for r in records if r["level"] == "province" and "Bắc Ninh" in r["name"])
    assert isinstance(bac_ninh["code"], int)
    assert bac_ninh["version"] == 2
    yen_dung = [r for r in records if r["level"] == "ward" and "Yên Dũng" in r["name"]]
    assert yen_dung, "fixture must contain a Yên Dũng ward"
    yd = yen_dung[0]
    assert "Bắc Ninh" in yd["province_name"]
    assert yd["province_code"] == bac_ninh["code"]
    assert yd["version"] == 2
    for key in ("level", "code", "name", "division_type", "codename", "province_code", "province_name", "version"):
        assert key in yd


def test_normalize_v2_province_fields(v2_payload):
    records = au.normalize(v2_payload, 2)
    prov = next(r for r in records if r["level"] == "province")
    for key in ("level", "code", "name", "division_type", "codename", "version"):
        assert key in prov
    assert isinstance(prov["code"], int)
    assert prov["name"]


# --------------------------------------------------------------------------- normalize (v1)


def test_normalize_v1_three_levels(v1_payload):
    records = au.normalize(v1_payload, 1)
    provinces = [r for r in records if r["level"] == "province"]
    districts = [r for r in records if r["level"] == "district"]
    wards = [r for r in records if r["level"] == "ward"]
    assert len(provinces) == len(v1_payload)
    assert len(districts) == sum(len(p.get("districts") or []) for p in v1_payload)
    assert len(wards) == sum(len(d.get("wards") or []) for p in v1_payload for d in (p.get("districts") or []))
    assert all(r["version"] == 1 for r in records)


def test_normalize_v1_ward_fields(v1_payload):
    records = au.normalize(v1_payload, 1)
    wards = [r for r in records if r["level"] == "ward"]
    assert wards
    w = wards[0]
    for key in ("district_code", "district_name", "province_code", "province_name", "version"):
        assert key in w
    assert w["version"] == 1
    # district record carries province fields and links to its wards
    d = next(r for r in records if r["level"] == "district")
    assert d["province_name"] == w["province_name"]
    assert d["province_code"] == w["province_code"]
    assert w["district_code"] == d["code"]
    assert w["district_name"] == d["name"]


def test_normalize_invalid_version(v2_payload):
    with pytest.raises(VnGeoError):
        au.normalize(v2_payload, 3)


# --------------------------------------------------------------------------- records_to_documents


def test_records_to_documents_v2(v2_payload):
    records = au.normalize(v2_payload, 2)
    docs = au.records_to_documents(records)
    assert len(docs) == len(records)
    rec = next(r for r in records if r["level"] == "province" and "Bắc Ninh" in r["name"])
    doc = next(d for d in docs if d["url"] == f"vn://provinces-api/v2/province/{rec['code']}")
    assert doc["title"] == rec["name"]
    assert doc["provider"] == "provinces-api"
    assert doc["format"] == "admin"
    assert doc["meta"] == rec
    assert doc["text"].startswith(rec["name"] + " — ")
    assert rec["division_type"] in doc["text"]
    assert f"Mã {rec['code']}." in doc["text"]


def test_records_to_documents_ward_text(v2_payload):
    records = au.normalize(v2_payload, 2)
    docs = au.records_to_documents(records)
    rec = next(r for r in records if r["level"] == "ward" and "Yên Dũng" in r["name"])
    doc = next(d for d in docs if d["url"] == f"vn://provinces-api/v2/ward/{rec['code']}")
    assert doc["text"].startswith(rec["name"] + " — ")
    assert f"Thuộc {rec['province_name']}." in doc["text"]
    assert f"Mã {rec['code']}." in doc["text"]
    assert rec["codename"] in doc["text"]
    # docs are valid ingest_document kwargs
    store = SearchStore(_tmp_db())
    try:
        doc_id = store.ingest_document(**doc)
        assert isinstance(doc_id, int)
    finally:
        store.close()


def _tmp_db() -> str:
    import tempfile

    return str(Path(tempfile.mkdtemp()) / "docmap.db")


# --------------------------------------------------------------------------- ingest


def test_ingest_v2_counts(store, v2_payload):
    records = au.normalize(v2_payload, 2)
    result = au.ingest(store, records)
    n_prov = sum(1 for r in records if r["level"] == "province")
    n_ward = sum(1 for r in records if r["level"] == "ward")
    assert result == {"documents": len(records), "provinces": n_prov, "wards": n_ward, "districts": 0}
    assert store.stats()["documents"] == len(records)


def test_ingest_v1_counts(store, v1_payload):
    records = au.normalize(v1_payload, 1)
    result = au.ingest(store, records)
    assert result["provinces"] == sum(1 for r in records if r["level"] == "province")
    assert result["districts"] == sum(1 for r in records if r["level"] == "district")
    assert result["wards"] == sum(1 for r in records if r["level"] == "ward")
    assert result["documents"] == len(records)
    assert result["districts"] > 0  # v1 fixture has a district


def test_ingest_dedup(store, v2_payload):
    records = au.normalize(v2_payload, 2)
    first = au.ingest(store, records)
    second = au.ingest(store, records)
    assert first["documents"] == second["documents"]
    # re-ingesting identical content must not create duplicate documents
    assert store.stats()["documents"] == first["documents"]


# --------------------------------------------------------------------------- lookup


def test_lookup_hit(store, v2_payload):
    records = au.normalize(v2_payload, 2)
    au.ingest(store, records)
    hits = au.lookup(store, "Yên Dũng")
    assert hits, "expected a hit for Yên Dũng"
    top = hits[0]
    assert "Yên Dũng" in top["name"]
    assert top["level"] == "ward"
    assert "Bắc Ninh" in top["province_name"]
    assert top["url"].startswith("vn://provinces-api/v2/ward/")
    assert "snippet" in top


def test_lookup_diacritics_fold(store, v2_payload):
    records = au.normalize(v2_payload, 2)
    au.ingest(store, records)
    # query without diacritics still matches (FTS tokenizer folds diacritics)
    hits = au.lookup(store, "Yen Dung")
    assert any("Yên Dũng" in h["name"] for h in hits)


def test_lookup_filters_other_providers(store, v2_payload):
    records = au.normalize(v2_payload, 2)
    au.ingest(store, records)
    # a non-provinces doc mentioning Yên Dũng must not leak into results
    store.ingest_document(
        "https://example.com/yen-dung",
        "Yên Dũng — some other provider doc.",
        title="Yên Dũng",
        provider="other",
        format="place",
    )
    hits = au.lookup(store, "Yên Dũng")
    assert hits
    assert all(h["url"].startswith("vn://provinces-api/") for h in hits)


def test_lookup_limit(store, v2_payload):
    records = au.normalize(v2_payload, 2)
    au.ingest(store, records)
    hits = au.lookup(store, "Bắc", limit=3)
    assert 0 < len(hits) <= 3


def test_lookup_no_match(store, v2_payload):
    records = au.normalize(v2_payload, 2)
    au.ingest(store, records)
    assert au.lookup(store, "Zzz Nonexistent") == []


# --------------------------------------------------------------------------- fetch_all


def test_fetch_all_success(monkeypatch, v2_payload):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, timeout))
        return _FakeResponse(json.dumps(v2_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    data = au.fetch_all(2, timeout=5.0)
    assert data == v2_payload
    assert calls == [(au.V2_API + "?depth=2", 5.0)]


def test_fetch_all_default_version_is_v2(monkeypatch, v2_payload):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResponse(json.dumps(v2_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    au.fetch_all()
    assert seen["url"] == au.V2_API + "?depth=2"


def test_fetch_all_v1_url(monkeypatch, v1_payload):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResponse(json.dumps(v1_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    data = au.fetch_all(1)
    assert data == v1_payload
    assert seen["url"] == au.V1_API + "?depth=2"


def test_fetch_all_http_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(VnGeoError):
        au.fetch_all(2)


def test_fetch_all_url_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(VnGeoError):
        au.fetch_all(2)


def test_fetch_all_invalid_json(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResponse(b"<html>not json</html>")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(VnGeoError):
        au.fetch_all(2)


def test_fetch_all_not_a_list(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResponse(b'{"error": "nope"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(VnGeoError):
        au.fetch_all(2)


def test_fetch_all_invalid_version(monkeypatch):
    with pytest.raises(VnGeoError):
        au.fetch_all(3)


# --------------------------------------------------------------------------- CLI


def test_cli_fetch_writes_records(monkeypatch, tmp_path, v2_payload):
    out = tmp_path / "admin_units_v2.json"
    monkeypatch.setattr(au, "fetch_all", lambda version, *, timeout: v2_payload)
    rc = au.main(["fetch", "--version", "2", "--out", str(out)])
    assert rc == 0
    records = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(records, list)
    assert all("level" in r and "version" in r for r in records)
    assert any("Bắc Ninh" in r["name"] for r in records)


def test_cli_fetch_error_exits_1(monkeypatch, tmp_path, capsys):
    def boom(version, *, timeout):
        raise VnGeoError("network down")

    monkeypatch.setattr(au, "fetch_all", boom)
    rc = au.main(["fetch", "--version", "2", "--out", str(tmp_path / "x.json")])
    assert rc == 1
    assert "network down" in capsys.readouterr().err


def test_cli_ingest_reads_version_from_records(tmp_path, capsys, v1_payload):
    records = au.normalize(v1_payload, 1)
    rec_file = tmp_path / "records.json"
    rec_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    db = tmp_path / "test.db"
    rc = au.main(["ingest", "--db", str(db), "--file", str(rec_file), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["version"] == 1
    assert out["districts"] > 0
    assert out["documents"] == out["provinces"] + out["districts"] + out["wards"]


def test_cli_ingest_tolerates_raw_payload(tmp_path, v2_payload):
    raw_file = tmp_path / "raw.json"
    raw_file.write_text(json.dumps(v2_payload, ensure_ascii=False), encoding="utf-8")
    db = tmp_path / "test.db"
    rc = au.main(["ingest", "--db", str(db), "--file", str(raw_file)])
    assert rc == 0
    with SearchStore(db) as store:
        assert au.lookup(store, "Yên Dũng")


def test_cli_lookup_json(tmp_path, capsys, v2_payload):
    records = au.normalize(v2_payload, 2)
    db = tmp_path / "test.db"
    with SearchStore(db) as store:
        au.ingest(store, records)
    rc = au.main(["lookup", "Yên Dũng", "--db", str(db), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["count"] >= 1
    assert any("Yên Dũng" in r["name"] for r in out["results"])


def test_cli_lookup_no_results(tmp_path, capsys, v2_payload):
    records = au.normalize(v2_payload, 2)
    db = tmp_path / "test.db"
    with SearchStore(db) as store:
        au.ingest(store, records)
    rc = au.main(["lookup", "Zzz Nonexistent", "--db", str(db), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 0


def test_cli_usage_error_exits_2():
    with pytest.raises(SystemExit) as exc:
        au.main(["fetch"])  # missing required --out
    assert exc.value.code == 2
