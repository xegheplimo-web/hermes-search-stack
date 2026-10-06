"""Tests for vn_geo.connectors_masothue (R13-B scope).

Hermetic: fixture HTML under tests/fixtures/r13b/ — a real Yên Dũng district
listing page and the real province index, both fetched politely (≥2 s) during
development. Network is replaced by an injected getter/sleeper. No network.
"""

from pathlib import Path

import pytest

from vn_geo import VnGeoError
from vn_geo import connectors_masothue as m

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "r13b"
YEN_DUNG = FIXTURE_DIR / "masothue_yen_dung_listing.html"
PROVINCE_INDEX = FIXTURE_DIR / "masothue_province_index.html"

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


@pytest.fixture()
def yen_dung_html():
    return YEN_DUNG.read_text(encoding="utf-8")


def _getter_serving(html):
    body = html.encode("utf-8")
    calls: list[str] = []

    def getter(url):
        calls.append(url)
        return url, 200, body

    return getter, calls


def _recorder():
    slept: list[float] = []
    return slept.append, slept


# ---------- fixtures sanity ----------


def test_fixture_is_real_yen_dung_listing(yen_dung_html):
    assert "data-prefetch='/24" in yen_dung_html  # Bắc Giang tax codes start 24
    assert "Huyện Yên Dũng, Tỉnh Bắc Giang" in yen_dung_html
    assert len(m.parse_listing(yen_dung_html)) >= 20


def test_province_index_fixture_lists_bac_giang():
    links = m.parse_area_links(PROVINCE_INDEX.read_text(encoding="utf-8"))
    assert links["bac giang"] == "bac-giang-72"
    assert links["bac ninh"] == "bac-ninh-170"


# ---------- parse_listing ----------


def test_parse_listing_extracts_all_fields(yen_dung_html):
    recs = m.parse_listing(yen_dung_html)
    assert len(recs) == 25
    first = recs[0]
    assert first["tax_code"] == "2401011154"
    assert first["name"] == "CÔNG TY TNHH TM & DV CUNG ỨNG NHÂN LỰC THÀNH ĐẠT"
    assert first["representative"] == "NGÔ THỊ HƯỜNG"
    assert first["address"] == "xóm Giá, Xã Nội Hoàng, Huyện Yên Dũng, Tỉnh Bắc Giang, Việt Nam"
    assert first["source_url"] == "https://masothue.com/2401011154-cong-ty-tnhh-tm-dv-cung-ung-nhan-luc-thanh-dat"
    assert first["source_id"] == "2401011154"


def test_parse_listing_skips_blocks_without_name_or_tax():
    html = "<div data-prefetch='/x-empty'><h3></h3><address></address></div>"
    assert m.parse_listing(html) == []


def test_parse_listing_tolerates_empty_html():
    assert m.parse_listing("") == []
    assert m.parse_listing(None) == []


# ---------- JSON-LD ----------


def test_parse_jsonld_extracts_itemlist_records():
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"CÔNG TY ABC",'
        '"taxID":"0100000001","address":{"streetAddress":"1 Lê Lợi","addressLocality":"Hà Nội"},'
        '"url":"https://masothue.com/0100000001-cong-ty-abc"}]}'
        "</script>"
    )
    recs = m.parse_jsonld(html)
    assert len(recs) == 1
    assert recs[0]["name"] == "CÔNG TY ABC"
    assert recs[0]["tax_code"] == "0100000001"
    assert "1 Lê Lợi" in recs[0]["address"]


def test_parse_jsonld_ignores_site_level_blocks(yen_dung_html):
    # The real listing page only carries WebSite/Organization JSON-LD (no address).
    assert m.parse_jsonld(yen_dung_html) == []


# ---------- shaping ----------


def test_to_entity_matches_schema_v1(yen_dung_html):
    rec = m.parse_listing(yen_dung_html)[0]
    entity = m.to_entity(rec, area="Yên Dũng", province="Bắc Giang")
    assert set(entity) == SCHEMA_KEYS
    assert entity["source"] == "masothue"
    assert entity["source_id"] == "2401011154"
    assert entity["tax_code"] == "2401011154"
    assert entity["area_old"] == "Yên Dũng"
    assert entity["province"] == "Bắc Giang"
    assert entity["entity_id"].startswith("e_") and len(entity["entity_id"]) == 14
    assert entity["raw"]["name"] == rec["name"]  # raw keeps the unfolded original


def test_to_entity_folds_d_on_name_and_address(yen_dung_html):
    rec = m.parse_listing(yen_dung_html)[0]
    entity = m.to_entity(rec, area="Yên Dũng", province="Bắc Giang")
    assert entity["name"] == m.fold_d(rec["name"])
    assert entity["address_text"] == m.fold_d(rec["address"])
    assert "Đ" not in entity["name"]  # đ/Đ folded away
    assert "đ" not in entity["address_text"]


def test_entity_id_is_deterministic():
    assert m._entity_id("masothue", "2401011154") == m._entity_id("masothue", "2401011154")
    assert m._entity_id("masothue", "1") != m._entity_id("masothue", "2")


# ---------- fetch ----------


def test_fetch_yen_dung_returns_entities(yen_dung_html):
    getter, calls = _getter_serving(yen_dung_html)
    sleeper, slept = _recorder()
    out = m.fetch("Yên Dũng", politeness_s=2.0, getter=getter, sleeper=sleeper)
    assert len(out) == 25
    assert all(set(e) == SCHEMA_KEYS for e in out)
    assert {e["source"] for e in out} == {"masothue"}
    assert {e["province"] for e in out} == {"Bắc Giang"}
    assert calls == ["https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/huyen-yen-dung-2119"]
    assert slept == []  # a single page -> no inter-call sleep


def test_fetch_sleeps_politeness_between_pages(yen_dung_html):
    getter, calls = _getter_serving(yen_dung_html)
    sleeper, slept = _recorder()
    m.fetch("Yên Dũng", politeness_s=2.0, max_pages=3, getter=getter, sleeper=sleeper)
    assert len(calls) == 3
    assert calls[1].endswith("?page=2") and calls[2].endswith("?page=3")
    assert slept == [2.0, 2.0]  # one sleep between each pair of calls


def test_fetch_dedupes_by_tax_code():
    html = (
        "<div data-prefetch='/111-a'><h3><a href='/111-a'>A</a></h3>"
        "Mã số thuế: <a href='/111-a'>111</a><address><i></i> X1</address></div>"
        "<div data-prefetch='/111-a2'><h3><a href='/111-a2'>A dup</a></h3>"
        "Mã số thuế: <a href='/111-a2'>111</a><address><i></i> X2</address></div>"
    )
    getter, _ = _getter_serving(html)
    out = m.fetch("Yên Dũng", getter=getter, sleeper=lambda *_: None)
    assert len(out) == 1


def test_fetch_rejects_bad_params():
    with pytest.raises(VnGeoError):
        m.fetch("Yên Dũng", politeness_s=-1)
    with pytest.raises(VnGeoError):
        m.fetch("Yên Dũng", max_pages=0)


def test_fetch_records_http_failure():
    def getter(url):
        return url, 404, b""

    assert m.fetch("Yên Dũng", getter=getter, sleeper=lambda *_: None) == []


# ---------- resolution ----------


def test_resolve_area_province_uses_no_network():
    getter, calls = _getter_serving("")
    pacer = m._Pacer(getter, lambda *_: None, 2.0)
    slug, province, scope = m.resolve_area("Bắc Giang", pacer)
    assert (slug, scope) == ("bac-giang-72", "province")
    assert calls == []  # direct province hit


def test_resolve_area_discovers_province_from_index():
    # Đà Nẵng is not in the built-in PROVINCES map, so discovery must hit the index.
    getter, calls = _getter_serving(PROVINCE_INDEX.read_text(encoding="utf-8"))
    pacer = m._Pacer(getter, lambda *_: None, 2.0)
    slug, _, scope = m.resolve_area("Đà Nẵng", pacer)
    assert slug == "da-nang-35"
    assert scope == "province"
    assert calls == ["https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/"]


def test_resolve_area_unknown_raises():
    getter, _ = _getter_serving(PROVINCE_INDEX.read_text(encoding="utf-8"))
    pacer = m._Pacer(getter, lambda *_: None, 2.0)
    with pytest.raises(VnGeoError):
        m.resolve_area("Atlantis", pacer)


# ---------- search ----------


def test_search_scopes_by_province_city(yen_dung_html):
    getter, calls = _getter_serving(yen_dung_html)
    out = m.search("nhà nghỉ", area="Bắc Giang", getter=getter, sleeper=lambda *_: None)
    assert len(out) == 25
    assert "city=72" in calls[-1] and "type=auto" in calls[-1]


def test_search_rejects_empty_keyword():
    with pytest.raises(VnGeoError):
        m.search("  ")


# ---------- robots / /Ajax/* guard ----------


def test_ajax_paths_are_blocked():
    assert m.DISALLOWED_PREFIX == "/Ajax/"
    with pytest.raises(VnGeoError):
        m._assert_allowed("https://masothue.com/Ajax/search")
    # allowed listing/search paths pass unchanged
    assert m._assert_allowed("https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/bac-giang-72")
    assert m._assert_allowed("https://masothue.com/Search/?q=a&type=auto")
