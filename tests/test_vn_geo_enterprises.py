"""Tests for vn_geo.enterprises (R4-E scope).

Hermetic: fixtures under tests/fixtures/vn_geo/enterprises/ (trimmed real
CKAN envelopes), tmp_path databases, monkeypatched urlopen. No network.
"""

import io
import json
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo import VnGeoError
from vn_geo import enterprises as ent
from vn_geo.enterprises import (
    DATASETS,
    fetch_dataset,
    ingest,
    list_datasets,
    main,
    normalize,
    query,
    records_to_documents,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vn_geo" / "enterprises"
HP_SAMPLE = FIXTURE_DIR / "hp_new_sample.json"
TN_SAMPLE = FIXTURE_DIR / "tn_list_sample.json"


def _load_records(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["result"]["records"]


@pytest.fixture()
def hp_records():
    return _load_records(HP_SAMPLE)


@pytest.fixture()
def tn_records():
    return _load_records(TN_SAMPLE)


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "test.db")
    yield s
    s.close()


def _saved_events(store):
    return [
        (r["kind"], json.loads(r["payload"]))
        for r in store.conn.execute("SELECT kind, payload FROM events ORDER BY id")
    ]


# ---------- fixtures sanity ----------


def test_fixtures_are_real_envelopes():
    for path in (HP_SAMPLE, TN_SAMPLE):
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["success"] is True
        recs = payload["result"]["records"]
        assert 3 <= len(recs) <= 5


# ---------- normalize: Hai Phong slash schema ----------


def test_normalize_hp_slash_schema(hp_records):
    out = normalize(hp_records, province="haiphong", kind="new")
    assert len(out) == len(hp_records)

    full = out[1]  # CÔNG TY TNHH ... PHÁT HUY
    assert full["code"] == "0202357404"
    assert full["name"] == "CÔNG TY TNHH DỊCH VỤ THƯƠNG MẠI PHÁT HUY"
    assert full["address"] == "Tổ dân phố Giữa, Phường Lưu Kiếm, Thành phố Hải Phòng, Việt Nam"
    assert full["representative"] == "NGUYỄN TRỌNG QUYNH"
    assert full["phone"] == "0385929859"
    assert full["capital_vnd"] == 3000000000
    assert full["province"] == "haiphong"
    assert full["kind"] == "new"
    assert full["raw"] == hp_records[1]  # raw stays complete

    sparse = out[0]  # null phone + null capital are stripped, not None-valued
    assert "phone" not in sparse
    assert "capital_vnd" not in sparse
    assert sparse["code"] == "00002"
    assert sparse["representative"] == "VŨ VĂN TIẾN"
    assert sparse["raw"]["Dien thoai lien he"] is None

    branch = out[2]  # dependent-unit code with dash survives verbatim
    assert branch["code"] == "2301408082-032"


def test_normalize_tolerates_missing_columns():
    out = normalize([{"_id": 9}], province="haiphong", kind="new")
    assert out == [{"province": "haiphong", "kind": "new", "raw": {"_id": 9}}]


def test_normalize_rejects_non_objects():
    with pytest.raises(VnGeoError):
        normalize(["nope"], province="haiphong", kind="new")


# ---------- normalize: Tay Ninh schema (lat/lng quirk) ----------


def test_normalize_tn_schema_keeps_lat_lng_raw(tn_records):
    out = normalize(tn_records, province="tayninh", kind="list")
    assert len(out) == len(tn_records)
    first = out[0]
    assert first["name"] == "HKD Phan Anh"
    assert first["address"] == "267 Châu Thị Kim; Phường 7; thành phố Tân An"
    assert first["phone"] == "0971 556 631"
    # QUIRK: Latitude column holds longitude-like values; keep verbatim.
    assert first["lat"] == pytest.approx(106.64557599680356)
    assert first["lon"] == pytest.approx(10.54317252393097)
    # No enterprise-code column in the TN schema.
    assert "code" not in first
    assert first["raw"] == tn_records[0]


# ---------- fetch_dataset (monkeypatched urlopen) ----------


class _FakeResp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, *a, **k):
        return self._buf.read(*a, **k)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen_factory(pages, seen, fail_success=False):
    """Serve *pages* (list of record-lists) keyed by offset; log (limit, offset)."""

    def fake(req, timeout=None):
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        limit = int(qs["limit"][0])
        offset = int(qs.get("offset", ["0"])[0])
        seen.append((limit, offset))
        assert req.get_header("User-agent") == "hermes-vn-geo/0.1"
        if fail_success:
            return _FakeResp({"success": False, "error": {"message": "bad resource id"}})
        batch = pages[offset // limit] if (offset // limit) < len(pages) else []
        total = sum(len(p) for p in pages)
        return _FakeResp({"success": True, "result": {"total": total, "records": batch}})

    return fake


def test_fetch_dataset_paginates_until_short_page(monkeypatch):
    pages = [[{"_id": 1}, {"_id": 2}], [{"_id": 3}]]
    seen: list = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(pages, seen))
    out = fetch_dataset("haiphong-new", page_size=2)
    assert [r["_id"] for r in out] == [1, 2, 3]
    assert seen == [(2, 0), (2, 2)]


def test_fetch_dataset_ckan_failure_raises(monkeypatch):
    seen: list = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory([], seen, fail_success=True))
    with pytest.raises(VnGeoError, match="bad resource id"):
        fetch_dataset("tayninh-list", page_size=10)


def test_fetch_dataset_http_error_wrapped(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "NOT FOUND", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(VnGeoError, match="HTTP 404"):
        fetch_dataset("haiphong-registrations")


def test_fetch_dataset_unknown_key():
    with pytest.raises(VnGeoError, match="unknown dataset"):
        fetch_dataset("nope")


# ---------- records_to_documents ----------


def test_records_to_documents_mapping(hp_records):
    norm = normalize(hp_records, province="haiphong", kind="new")
    docs = records_to_documents(norm, province="haiphong", kind="new", resource_id="rid-123")
    assert len(docs) == 3
    d = docs[1]
    assert d["url"] == "vn://ckan/haiphong/rid-123/0202357404"
    assert d["title"] == "CÔNG TY TNHH DỊCH VỤ THƯƠNG MẠI PHÁT HUY"
    assert d["provider"] == "ckan-haiphong"
    assert d["format"] == "enterprise"
    assert "0385929859" in d["text"] and "NGUYỄN TRỌNG QUYNH" in d["text"]
    assert "None" not in d["text"]
    assert d["meta"]["code"] == "0202357404"
    assert d["meta"]["resource_id"] == "rid-123"
    assert d["meta"]["source"] == "ckan"


def test_records_to_documents_idx_fallback_for_codeless(tn_records):
    norm = normalize(tn_records, province="Tây Ninh", kind="list")
    docs = records_to_documents(norm, province="Tây Ninh", kind="list", resource_id="rid-tn")
    assert docs[0]["url"] == "vn://ckan/tayninh/rid-tn/0"
    assert docs[0]["provider"] == "ckan-tayninh"


# ---------- ingest + query ----------


def test_ingest_counts_and_event(store, hp_records):
    result = ingest(store, hp_records, dataset_key="haiphong-new")
    assert result == {"documents": 3}
    events = [e for e in _saved_events(store) if e[0] == "enterprises_saved"]
    assert len(events) == 1
    _, payload = events[0]
    assert payload["dataset"] == "haiphong-new"
    assert payload["count"] == 3
    assert payload["keys"] == ["00002", "0202357404", "2301408082-032"]


def test_ingest_accepts_normalized_records(store, hp_records):
    norm = normalize(hp_records, province="haiphong", kind="new")
    assert ingest(store, norm, dataset_key="haiphong-new") == {"documents": 3}


def test_ingest_unknown_dataset(store, hp_records):
    with pytest.raises(VnGeoError, match="unknown dataset"):
        ingest(store, hp_records, dataset_key="nope")


def test_query_without_province_filter(store, hp_records, tn_records):
    ingest(store, hp_records, dataset_key="haiphong-new")
    ingest(store, tn_records, dataset_key="tayninh-list")
    hits = query(store, "TNHH")
    assert len(hits) == 4  # 2 HP + 2 TN
    first = hits[0]
    assert set(first) == {"name", "code", "address", "representative", "phone", "url"}
    assert any(h["code"] == "0202357404" for h in hits)


def test_query_with_province_filter(store, hp_records, tn_records):
    ingest(store, hp_records, dataset_key="haiphong-new")
    ingest(store, tn_records, dataset_key="tayninh-list")
    hp_hits = query(store, "TNHH", province="haiphong")
    assert len(hp_hits) == 2
    assert all(h["url"].startswith("vn://ckan/haiphong/") for h in hp_hits)
    tn_hits = query(store, "Tân An", province="tayninh")
    # FTS matches tokens ("tân" AND "an"): all 4 TN docs contain both
    # ("Long An" / "Tân Đô" match too), including the 2 "thành phố Tân An" ones.
    assert len(tn_hits) == 4
    assert sum(1 for h in tn_hits if "thành phố Tân An" in (h["address"] or "")) == 2
    assert query(store, "Tân An", province="haiphong") == []
    assert query(store, "TNHH", province="tayninh", limit=20) != []
    assert len(query(store, "TNHH", province="haiphong", limit=1)) == 1


def test_query_empty_text_rejected(store):
    with pytest.raises(VnGeoError):
        query(store, "   ")


# ---------- list_datasets ----------


def test_list_datasets():
    ds = list_datasets()
    assert {d["key"] for d in ds} == {
        "haiphong-new",
        "haiphong-dissolved",
        "haiphong-registrations",
        "tayninh-list",
    }
    for d in ds:
        assert set(d) >= {"key", "portal", "resource_id", "province", "kind", "label"}
    by_key = {d["key"]: d for d in ds}
    assert by_key["haiphong-new"]["kind"] == "new"
    assert by_key["tayninh-list"]["province"] == "tayninh"
    assert DATASETS["haiphong-new"]["resource_id"] == "a119cc68-9a99-4f80-ac93-b0d22728838e"


# ---------- CLI smoke ----------


def test_cli_list_ok():
    assert main(["list"]) == 0
    assert main(["list", "--json"]) == 0


def test_cli_fetch_ingest_query_roundtrip(tmp_path, monkeypatch, hp_records, capsys):
    out_file = tmp_path / "hp.json"
    monkeypatch.setattr(ent, "fetch_dataset", lambda key, **k: hp_records)
    db = tmp_path / "cli.db"
    assert main(["fetch", "--dataset", "haiphong-new", "--out", str(out_file)]) == 0
    assert len(json.loads(out_file.read_text(encoding="utf-8"))) == 3
    assert main(["ingest", "--db", str(db), "--dataset", "haiphong-new", "--file", str(out_file)]) == 0
    capsys.readouterr()  # discard human-readable fetch/ingest lines
    assert main(["query", "TNHH", "--db", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert main(["query", "zzzqqq", "--db", str(db)]) == 0
    assert main(["query", '"', "--db", str(db)]) == 1  # invalid FTS syntax -> runtime error
    assert main(["ingest", "--db", str(db), "--dataset", "bogus", "--file", str(out_file)]) == 1
