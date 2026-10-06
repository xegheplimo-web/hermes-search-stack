"""Tests for vn_geo.business (R13-A; contract analysis/r13-interfaces.md §3).

Hermetic: databases live under tmp_path and every network seam is stubbed
(monkeypatch on business._goong_client / business._http_get_json / time.sleep).
No live calls — no masothue, no CKAN, no Goong, no Nominatim.
"""

import time
import urllib.request

import pytest

from searchstore import store as store_mod
from vn_geo import VnGeoError, business, goong


def _gmaps_record(**over):
    rec = {
        "title": "Nhà nghỉ Bảo An",
        "category": "Guest house",
        "address": "xã Nham Biền, huyện Yên Dũng, Bắc Giang",
        "phone": "0912 345 678",
        "website": "",
        "place_id": "plc-baoan-1",
        "status": "Open",
        "rating": 4.3,
        "review_count": 21,
    }
    rec.update(over)
    return rec


def _entity(**over):
    ent = business.normalize(_gmaps_record(), "gmaps")
    ent.update(over)
    return ent


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "biz.db")


# ---------- normalize ----------


def test_normalize_maps_gosom_fields():
    ent = business.normalize(_gmaps_record(), "gmaps")
    assert ent["name"] == "Nhà nghỉ Bảo An"
    assert ent["kind"] == "lodging"
    assert ent["category"] == "lodging_budget"
    assert ent["category_raw"] == "Guest house"
    assert ent["source_id"] == "plc-baoan-1"
    assert ent["status"] == "open"
    assert ent["source"] == "gmaps"
    assert ent["entity_id"].startswith("e_")
    assert ent["raw"]["place_id"] == "plc-baoan-1"
    assert ent["ttl_class"] == "poi"
    assert ent["first_seen"] and ent["last_seen"] and ent["checked_at"]


def test_normalize_masothue_fields():
    ent = business.normalize(
        {
            "name": "Hộ kinh doanh Trần A",
            "address": "12 Trần Phú",
            "tax_code": "0123456789",
            "trang_thai": "đang hoạt động",
        },
        "masothue",
    )
    assert ent["tax_code"] == "0123456789"
    assert ent["status"] == "open"
    assert ent["lat"] is None and ent["lng"] is None
    assert ent["geocode_status"] == "approximate"


def test_normalize_ckan_lat_lng_and_confidence():
    ent = business.normalize(
        {"name": "Doanh nghiệp X", "Dia chi": "1 Lê Lợi", "Latitude": 10.9, "Longitude": 106.6},
        "ckan_tn",
    )
    assert ent["lat"] == 10.9 and ent["lng"] == 106.6
    assert ent["geocode_status"] == "exact"
    assert ent["confidence"] == pytest.approx(0.9)


def test_normalize_status_closed_variants():
    assert business.normalize({"name": "X", "status": "Temporarily closed"}, "gmaps")["status"] == "closed"
    assert business.normalize({"name": "X", "status": "giải thể"}, "masothue")["status"] == "closed"
    assert business.normalize({"name": "X", "status": "ngừng hoạt động"}, "ckan_hp")["status"] == "closed"
    assert business.normalize({"name": "X"}, "manual")["status"] == "unknown"


def test_normalize_requires_name():
    with pytest.raises(VnGeoError, match="name"):
        business.normalize({"address": "nowhere"}, "gmaps")


def test_normalize_rejects_unknown_source():
    with pytest.raises(VnGeoError, match="unknown source"):
        business.normalize({"name": "X"}, "scraped")


def test_normalize_entity_id_deterministic():
    a = business.normalize(_gmaps_record(), "gmaps")
    b = business.normalize(_gmaps_record(), "gmaps")
    assert a["entity_id"] == b["entity_id"]
    c = business.normalize(_gmaps_record(place_id="other-id"), "gmaps")
    assert c["entity_id"] != a["entity_id"]


def test_normalize_id_without_source_id_uses_name_address():
    a = business.normalize({"name": "Cơm Tấm 286", "address": "Yên Dũng"}, "manual")
    b = business.normalize({"name": "cơm tấm 286", "address": "yên dũng"}, "manual")
    assert a["entity_id"] == b["entity_id"]  # folded name+address hash
    c = business.normalize({"name": "Cơm Tấm 286", "address": "Yên Dũng"}, "gmaps")
    assert c["entity_id"] != a["entity_id"]  # source is part of the hash


# ---------- geocode ----------


def test_geocode_passes_through_existing_coords():
    ent = _entity(lat=21.2, lng=106.2)
    out = business.geocode(ent)
    assert out["lat"] == 21.2 and out["lng"] == 106.2
    assert out["geocode_status"] == "exact"
    assert out["geocode_source"] == "input"


def test_geocode_goong_branch_used_when_configured(monkeypatch):
    class _StubGoong:
        calls = []

        def geocode(self, address):
            self.calls.append(address)
            return [{"geometry": {"location": {"lat": 21.21, "lng": 106.23}}, "formatted_address": "Yên Dũng"}]

    stub = _StubGoong()
    monkeypatch.setattr(business, "_goong_client", lambda: stub)
    monkeypatch.setattr(business, "_nominatim_geocode", lambda q: pytest.fail("nominatim must not be called"))
    ent = business.normalize({"name": "Nhà nghỉ Bảo An", "address": "Yên Dũng"}, "manual")
    out = business.geocode(ent)
    assert out["lat"] == 21.21 and out["lng"] == 106.23
    assert out["geocode_status"] == "exact" and out["geocode_source"] == "goong"
    assert stub.calls, "goong geocode was never invoked"


def test_geocode_goong_client_uses_real_key_resolution(monkeypatch):
    # The branch constructs goong.GoongClient(), which resolves the key itself.
    monkeypatch.setattr(goong, "resolve_api_key", lambda *a, **kw: "test-key")
    seen = {}

    def _fake_geocode(self, address, **kw):
        seen["address"] = address
        return [{"geometry": {"location": {"lat": 10.5, "lng": 105.5}}}]

    monkeypatch.setattr(goong.GoongClient, "geocode", _fake_geocode)
    out = business._goong_geocode("some address")
    assert out == (10.5, 105.5, "goong")
    assert seen["address"] == "some address"


def test_geocode_goong_skipped_without_key(monkeypatch):
    def _no_key(*a, **kw):
        raise VnGeoError("no key")

    monkeypatch.setattr(goong, "resolve_api_key", _no_key)
    assert business._goong_client() is None
    assert business._goong_geocode("x") is None


def test_geocode_falls_back_to_nominatim(monkeypatch):
    monkeypatch.setattr(business, "_goong_client", lambda: None)
    monkeypatch.setattr(business, "_last_nominatim_call", 0.0)
    monkeypatch.setattr(
        business,
        "_http_get_json",
        lambda url: [{"lat": "21.206", "lon": "106.239", "display_name": "Yên Dũng"}],
    )
    ent = business.normalize({"name": "Nhà nghỉ Bảo An", "address": "Yên Dũng, Bắc Giang"}, "manual")
    out = business.geocode(ent)
    assert out["lat"] == pytest.approx(21.206) and out["lng"] == pytest.approx(106.239)
    assert out["geocode_status"] == "exact" and out["geocode_source"] == "nominatim"


def test_geocode_all_misses_gives_approximate(monkeypatch):
    monkeypatch.setattr(business, "_goong_client", lambda: None)
    monkeypatch.setattr(business, "_last_nominatim_call", 0.0)
    monkeypatch.setattr(business, "_http_get_json", lambda url: [])
    ent = business.normalize({"name": "Nowhere", "address": "Hư Cấu"}, "manual")
    out = business.geocode(ent)
    assert out["lat"] is None and out["lng"] is None
    assert out["geocode_status"] == "approximate"


def test_geocode_nominatim_error_is_approximate(monkeypatch):
    monkeypatch.setattr(business, "_goong_client", lambda: None)
    monkeypatch.setattr(business, "_last_nominatim_call", 0.0)

    def _boom(url):
        raise VnGeoError("HTTP 500")

    monkeypatch.setattr(business, "_http_get_json", _boom)
    out = business.geocode(business.normalize({"name": "X", "address": "Y"}, "manual"))
    assert out["geocode_status"] == "approximate"


def test_geocode_no_queryable_text_is_approximate(monkeypatch):
    monkeypatch.setattr(business, "_nominatim_geocode", lambda q: pytest.fail("no query -> no call"))
    monkeypatch.setattr(business, "_goong_geocode", lambda q: pytest.fail("no query -> no call"))
    ent = {"name": "", "address_text": "", "area_old": "", "province": "", "lat": None, "lng": None}
    out = business.geocode(ent)
    assert out["geocode_status"] == "approximate"


def test_nominatim_politeness_sleeps_at_least_interval(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    monkeypatch.setattr(business, "_last_nominatim_call", time.monotonic())
    monkeypatch.setattr(business, "_http_get_json", lambda url: [])
    business._nominatim_geocode("một địa chỉ")
    assert len(slept) == 1
    assert slept[0] == pytest.approx(business.NOMINATIM_MIN_INTERVAL_S, abs=0.2)


def test_nominatim_request_sends_user_agent(monkeypatch):
    monkeypatch.setattr(business, "_last_nominatim_call", 0.0)
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"[]"

    def _fake_urlopen(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert business._nominatim_geocode("Yên Dũng") is None
    headers = {k.lower(): v for k, v in captured["req"].headers.items()}
    assert headers["user-agent"] == business.USER_AGENT
    assert "nominatim.openstreetmap.org" in captured["req"].full_url


# ---------- upsert_entity + diff_events ----------


def _doc_count(db_path, url_fragment="vn://entity/"):
    from searchstore import SearchStore

    with SearchStore(db_path) as s:
        return s.conn.execute("SELECT COUNT(*) FROM documents WHERE format = 'entity'").fetchone()[0]


def test_upsert_new_entity_returns_id_and_event(db_path):
    eid = business.upsert_entity(db_path, _entity())
    assert eid.startswith("e_")
    events = business.diff_events(db_path)
    assert [e["kind"] for e in events] == ["entity_new"]
    assert events[0]["entity_id"] == eid


def test_upsert_idempotent_zero_duplicate_rows(db_path):
    ent = _entity()
    eid1 = business.upsert_entity(db_path, ent)
    eid2 = business.upsert_entity(db_path, ent)
    assert eid1 == eid2
    assert _doc_count(db_path) == 1
    assert len(business.diff_events(db_path)) == 1  # the entity_new event
    assert business.diff_events(db_path) == []  # watermark consumed


def test_upsert_fuzzy_merges_cross_source_duplicates(db_path):
    eid_a = business.upsert_entity(db_path, _entity())
    # Unaccented name/address variant from a different source: token overlap
    # >= 0.8 merges into the existing entity instead of creating a second one.
    variant = business.normalize(
        {"name": "nha nghi bao an", "address": "Nham Bien, Yen Dung, Bac Giang"},
        "manual",
    )
    eid_b = business.upsert_entity(db_path, variant)
    assert eid_a == eid_b
    rows = business.query_entities(db_path, "nha nghi")
    assert len(rows) == 1
    assert rows[0]["entity_id"] == eid_a
    assert sorted(rows[0]["sources"]) == ["gmaps", "manual"]
    events = [e for e in business.diff_events(db_path) if e["kind"] != "entity_new"]
    assert events and events[0]["entity_id"] == eid_a  # surface-form change logged


def test_upsert_exact_source_id_match_without_fuzzy(db_path):
    eid_a = business.upsert_entity(db_path, _entity())
    # Same (source, source_id) with an upstream-minted different entity_id and
    # a totally different name: exact (source, source_id) still wins over hash.
    moved = _entity(
        name="Khác Hẳn Không Trùng",
        address_text="Phường Khác, Nơi Xa",
        entity_id="e_deadbeef00",
    )
    eid_b = business.upsert_entity(db_path, moved)
    assert eid_b == eid_a
    assert len(business.query_entities(db_path, "")) == 1  # merged, not duplicated


def test_upsert_below_fuzzy_threshold_creates_second_entity(db_path):
    business.upsert_entity(db_path, _entity())
    different = business.normalize(
        {"name": "Nhà nghỉ An Bình", "address": "Tân Liễu, Yên Dũng, Bắc Giang"},
        "manual",
    )
    eid_b = business.upsert_entity(db_path, different)
    assert eid_b != _entity()["entity_id"]
    assert len(business.query_entities(db_path, "", area="Yên Dũng")) == 2


def test_upsert_changed_content_versions_and_events(db_path):
    ent = _entity(phone="0111")
    eid = business.upsert_entity(db_path, ent)
    business.diff_events(db_path)  # consume entity_new
    changed = dict(ent, phone="0222")
    assert business.upsert_entity(db_path, changed) == eid
    assert _doc_count(db_path) == 2  # append-only version, same url_key
    events = business.diff_events(db_path)
    assert [e["kind"] for e in events] == ["entity_changed"]
    assert events[0]["changes"]["phone"] == ["0111", "0222"]
    assert business.query_entities(db_path, "bao an")[0]["phone"] == "0222"


def test_upsert_status_to_closed_emits_entity_closed(db_path):
    ent = _entity()
    eid = business.upsert_entity(db_path, ent)
    business.diff_events(db_path)
    business.upsert_entity(db_path, dict(ent, status="closed"))
    events = business.diff_events(db_path)
    assert [e["kind"] for e in events] == ["entity_closed"]
    assert events[0]["entity_id"] == eid


def test_upsert_keeps_first_seen_bumps_last_seen(db_path):
    ent = _entity(first_seen="2026-01-01T00:00:00+07:00", checked_at="2026-01-01T00:00:00+07:00")
    eid = business.upsert_entity(db_path, ent)
    business.upsert_entity(db_path, ent)
    meta = business.query_entities(db_path, "bao an")[0]
    assert meta["entity_id"] == eid
    assert meta["first_seen"] == "2026-01-01T00:00:00+07:00"
    assert meta["last_seen"] != "2026-01-01T00:00:00+07:00"


def test_upsert_requires_name(db_path):
    with pytest.raises(VnGeoError, match="name"):
        business.upsert_entity(db_path, {"source": "manual", "name": "  "})


def test_upsert_requires_dict(db_path):
    with pytest.raises(VnGeoError):
        business.upsert_entity(db_path, "not a dict")


def test_diff_events_only_entity_kinds(db_path):
    # document_ingested etc. must not leak into the entity diff.
    events = business.diff_events(db_path)
    assert all(e["kind"].startswith("entity_") for e in events)


# ---------- query_entities ----------


def test_query_fold_d_accented_and_unaccented(db_path):
    business.upsert_entity(db_path, _entity())
    for text in ("nha nghi", "nhà nghỉ", "NHA NGHI"):
        rows = business.query_entities(db_path, text, "Yên Dũng")
        assert [r["name"] for r in rows] == ["Nhà nghỉ Bảo An"], f"text={text!r}"
    for area in ("Yên Dũng", "yen dung", "YÊN DŨNG"):
        rows = business.query_entities(db_path, "nha nghi", area)
        assert [r["name"] for r in rows] == ["Nhà nghỉ Bảo An"], f"area={area!r}"


def test_query_area_filters_out(db_path):
    business.upsert_entity(db_path, _entity())
    assert business.query_entities(db_path, "nha nghi", "Hà Nội") == []


def test_query_category_prefix_and_raw(db_path):
    business.upsert_entity(db_path, _entity())
    business.upsert_entity(
        db_path,
        business.normalize({"name": "Cơm Tấm 286", "address": "Yên Dũng"}, "manual"),
    )
    assert [r["category"] for r in business.query_entities(db_path, "", category="lodging")] == ["lodging_budget"]
    food = business.query_entities(db_path, "", category="food")
    assert [r["name"] for r in food] == ["Cơm Tấm 286"]
    assert len(business.query_entities(db_path, "", category="guest house")) == 1  # category_raw hit


def test_query_limit(db_path):
    for i in range(5):
        business.upsert_entity(
            db_path,
            business.normalize({"name": f"Nhà nghỉ số {i}", "address": "Yên Dũng"}, "manual"),
        )
    assert len(business.query_entities(db_path, "nha nghi", limit=3)) == 3


def test_query_empty_text_lists_entities(db_path):
    business.upsert_entity(db_path, _entity())
    rows = business.query_entities(db_path, "")
    assert len(rows) == 1 and rows[0]["name"] == "Nhà nghỉ Bảo An"


def test_query_marks_stale_entities(db_path):
    business.upsert_entity(db_path, _entity(checked_at="2020-01-01T00:00:00+07:00"))
    business.upsert_entity(db_path, business.normalize({"name": "Cơm Tấm 286", "address": "Yên Dũng"}, "manual"))
    stale = business.query_entities(db_path, "", area="Yên Dũng")
    by_name = {r["name"]: r for r in stale}
    assert by_name["Nhà nghỉ Bảo An"]["stale"] is True
    assert by_name["Cơm Tấm 286"]["stale"] is False


# ---------- store entity helpers ----------


def test_entities_view_exposes_columns(db_path):
    eid = business.upsert_entity(db_path, _entity())
    from searchstore import SearchStore

    with SearchStore(db_path) as s:
        row = s.conn.execute("SELECT entity_id, name, kind, category FROM entities").fetchone()
    assert row["entity_id"] == eid
    assert row["name"] == "Nhà nghỉ Bảo An"
    assert row["kind"] == "lodging" and row["category"] == "lodging_budget"


def test_entities_view_created_on_open(tmp_path):
    from searchstore import SearchStore

    with SearchStore(tmp_path / "fresh.db") as s:
        count = s.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name='entities'").fetchone()[0]
    assert count == 1


def test_entity_token_overlap_threshold_math():
    assert store_mod.entity_token_overlap("Nhà nghỉ Bảo An Yên Dũng", "nha nghi bao an yen dung") == 1.0
    assert store_mod.entity_token_overlap("a b c d", "a b c e") == pytest.approx(0.6)
    assert store_mod.entity_token_overlap("", "x") == 0.0


def test_entity_id_for_stable_and_source_scoped():
    a = store_mod.entity_id_for("gmaps", "plc-1")
    assert a == store_mod.entity_id_for("gmaps", "plc-1")
    assert a != store_mod.entity_id_for("masothue", "plc-1")
    b = store_mod.entity_id_for("manual", "", "Nhà Nghỉ", "Yên Dũng")
    assert b == store_mod.entity_id_for("manual", "", "nha nghi", "yen dung")
    assert b.startswith("e_") and len(b) == 14
