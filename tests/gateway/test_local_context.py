"""Tests for gateway.core.local_context (R15-C; analysis/r15-interfaces.md §3).

Hermetic: entity stores are built in tmp_path via ``vn_geo.business.upsert_entity``
with pre-filled coordinates — no network, no geocoding. The live
``data/vn-geo.db`` is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.core import local_context
from gateway.core.local_context import LocalEvidence, build_local_evidence
from vn_geo import business


def _entity(
    name: str,
    address: str,
    *,
    area: str = "",
    province: str = "",
    lat: float | None = None,
    lng: float | None = None,
    source: str = "gmaps",
    category: str = "food",
    status: str = "open",
) -> dict:
    """Minimal schema-v1-shaped entity for ``upsert_entity`` (coords pre-filled)."""
    return {
        "name": name,
        "address_text": address,
        "area_old": area,
        "province": province,
        "lat": lat,
        "lng": lng,
        "source": source,
        "category": category,
        "kind": "food",
        "status": status,
        "geocode_status": "exact" if lat is not None else "approximate",
    }


@pytest.fixture()
def db_path(tmp_path) -> str:
    return str(tmp_path / "vn-geo-test.db")


def _seed_bao_an(db_path: str) -> str:
    return business.upsert_entity(
        db_path,
        _entity(
            "Nhà nghỉ Bảo An",
            "xã Nham Biền, huyện Yên Dũng, Bắc Giang",
            area="Yên Dũng",
            province="Bắc Giang",
            lat=21.2,
            lng=106.2,
        ),
    )


# ---------- path resolution ----------


def test_resolve_db_path_precedence(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.db"
    assert local_context.resolve_db_path(explicit) == explicit
    monkeypatch.setenv(local_context.VN_GEO_DB_ENV, str(tmp_path / "env.db"))
    assert local_context.resolve_db_path(None) == tmp_path / "env.db"
    assert local_context.resolve_db_path(explicit) == explicit  # explicit wins
    monkeypatch.delenv(local_context.VN_GEO_DB_ENV)
    assert local_context.resolve_db_path(None) == local_context.DEFAULT_VN_GEO_DB


# ---------- match + confidence math ----------


def test_match_full_confidence_one(db_path):
    eid = _seed_bao_an(db_path)
    out = build_local_evidence("nhà nghỉ bảo an yên dũng", db_path=db_path)
    assert len(out) == 1
    ev = out[0]
    # name (0.4) + area (0.2) + address (0.2) + coords (0.2) = 1.0
    assert ev.confidence == pytest.approx(1.0)
    assert ev.id == f"local:vn-geo:{eid}"
    assert ev.url == f"local://vn-geo/{eid}"
    assert ev.title == "Nhà nghỉ Bảo An"
    assert ev.authority == "aggregator"
    assert ev.ambiguous is False
    assert ev.freshness  # checked_at stamped by upsert
    for part in ("Nhà nghỉ Bảo An", "Yên Dũng", "xã Nham Biền", "food", "open", "gmaps"):
        assert part in ev.content


def test_match_unaccented_query_hits_diacritics(db_path):
    _seed_bao_an(db_path)
    for text in ("nha nghi bao an yen dung", "NHA NGHI BAO AN YEN DUNG", "nhà nghỉ bảo an yên dũng"):
        out = build_local_evidence(text, db_path=db_path)
        assert len(out) == 1, text
        assert out[0].title == "Nhà nghỉ Bảo An"


def test_confidence_partial_components(db_path):
    _seed_bao_an(db_path)
    # name only: "bao an" tokens are fully inside the name -> 0.4 + coords 0.2 = 0.6
    out = build_local_evidence("bao an", db_path=db_path)
    assert len(out) == 1 and out[0].confidence == pytest.approx(0.6)
    # no name match, area+address+coords = 0.6
    out = build_local_evidence("yen dung bac giang", db_path=db_path)
    assert len(out) == 1 and out[0].confidence == pytest.approx(0.6)


# ---------- threshold exclusion ----------


def test_below_min_confidence_excluded(db_path):
    business.upsert_entity(
        db_path,
        _entity(
            "Quán Ăn Đặc Sản",
            "1 Đường Xa Xôi, Tỉnh Khác",
            area="Tỉnh Khác",
            lat=None,
            lng=None,
        ),
    )
    # Name-only match scores 0.4 < 0.5 default -> excluded.
    assert build_local_evidence("quan an dac san", db_path=db_path) == []
    # Lowering the floor admits it.
    out = build_local_evidence("quan an dac san", db_path=db_path, min_confidence=0.4)
    assert len(out) == 1 and out[0].confidence == pytest.approx(0.4)


def test_exact_threshold_included(db_path):
    _seed_bao_an(db_path)
    out = build_local_evidence("bao an", db_path=db_path, min_confidence=0.6)
    assert len(out) == 1 and out[0].confidence == pytest.approx(0.6)


def test_no_textual_relation_excluded(db_path):
    _seed_bao_an(db_path)
    assert build_local_evidence("zzz khong lien quan", db_path=db_path) == []
    assert build_local_evidence("", db_path=db_path) == []


# ---------- ambiguity guard ----------


def test_ambiguous_same_name_different_address(db_path):
    id_a = business.upsert_entity(
        db_path,
        _entity("Cafe Trung Nguyên", "1 Lê Lợi, Hải Phòng", area="Hải Phòng", lat=20.86, lng=106.68),
    )
    id_b = business.upsert_entity(
        db_path,
        _entity("Cafe Trung Nguyên", "9 Trần Phú, Hà Nội", area="Hà Nội", lat=21.03, lng=105.85),
    )
    assert id_a != id_b
    out = build_local_evidence("cafe trung nguyen", db_path=db_path)
    assert len(out) == 2, "same-name different-address entities must stay separate"
    assert {ev.id for ev in out} == {f"local:vn-geo:{id_a}", f"local:vn-geo:{id_b}"}
    assert all(ev.ambiguous is True for ev in out)
    # Tied confidence (both 0.6) -> entity_id ascending.
    assert [ev.id for ev in out] == sorted(ev.id for ev in out)


def test_single_entity_never_ambiguous(db_path):
    _seed_bao_an(db_path)
    out = build_local_evidence("bao an", db_path=db_path)
    assert len(out) == 1 and out[0].ambiguous is False


def test_distinct_names_not_ambiguous(db_path):
    business.upsert_entity(
        db_path,
        _entity("Cafe Trung Nguyên", "1 Lê Lợi, Hải Phòng", area="Hải Phòng", lat=20.86, lng=106.68),
    )
    business.upsert_entity(
        db_path,
        _entity("Cafe Phin Đen", "9 Trần Phú, Hải Phòng", area="Hải Phòng", lat=20.87, lng=106.69),
    )
    out = build_local_evidence("cafe hai phong", db_path=db_path)
    assert len(out) == 2
    assert all(ev.ambiguous is False for ev in out)


# ---------- deterministic order ----------


def test_deterministic_order_confidence_then_entity_id(db_path):
    weak = business.upsert_entity(
        db_path,
        _entity("Pho 24 B", "5 Phố Huế, Hà Nội", area="Hà Nội", lat=None, lng=None),
    )
    strong = business.upsert_entity(
        db_path,
        _entity("Pho 24 A", "1 Lê Lợi, Hải Phòng", area="Hải Phòng", lat=20.86, lng=106.68),
    )
    out = build_local_evidence("pho 24 hai phong", db_path=db_path)
    confs = [ev.confidence for ev in out]
    assert confs == sorted(confs, reverse=True)
    assert out[0].id == f"local:vn-geo:{strong}"
    assert weak != strong


def test_limit_applies(db_path):
    for i in range(5):
        business.upsert_entity(
            db_path,
            _entity(f"Phở Quán Số {i}", "Hải Phòng", area="Hải Phòng", lat=20.8 + i / 100, lng=106.6),
        )
    out = build_local_evidence("pho quan hai phong", db_path=db_path, limit=3)
    assert len(out) == 3


# ---------- authority ----------


def test_authority_registry_vs_aggregator(db_path):
    business.upsert_entity(
        db_path,
        _entity("Công Ty TNHH Mẫu", "12 Trần Phú, Hà Nội", lat=21.0, lng=105.8, source="masothue"),
    )
    business.upsert_entity(
        db_path,
        _entity("Công Ty CKAN Mẫu", "34 Lê Lợi, Hà Nội", lat=21.0, lng=105.8, source="ckan_hp"),
    )
    business.upsert_entity(
        db_path,
        _entity("Công Ty Aggregator Mẫu", "56 Hàng Bài, Hà Nội", lat=21.0, lng=105.8, source="gmaps"),
    )
    out = build_local_evidence("cong ty mau ha noi", db_path=db_path)
    by_title = {ev.title: ev for ev in out}
    assert by_title["Công Ty TNHH Mẫu"].authority == "registry"
    assert by_title["Công Ty CKAN Mẫu"].authority == "registry"
    assert by_title["Công Ty Aggregator Mẫu"].authority == "aggregator"


# ---------- graceful degradation ----------


def test_missing_db_returns_empty(tmp_path):
    assert build_local_evidence("nha nghi", db_path=tmp_path / "nope.db") == []


def test_missing_db_via_env_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(local_context.VN_GEO_DB_ENV, str(tmp_path / "nope.db"))
    assert build_local_evidence("nha nghi") == []


def test_not_a_database_returns_empty(tmp_path):
    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"this is not sqlite")
    assert build_local_evidence("nha nghi", db_path=bogus) == []


def test_db_without_entity_schema_returns_empty(tmp_path):
    import sqlite3

    bare = tmp_path / "bare.db"
    conn = sqlite3.connect(str(bare))
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    conn.close()
    assert build_local_evidence("nha nghi", db_path=bare) == []


# ---------- read-only proof ----------


def test_read_only_leaves_db_untouched(db_path):
    _seed_bao_an(db_path)
    path = Path(db_path)
    before_bytes = path.read_bytes()
    before_names = sorted(p.name for p in path.parent.iterdir())
    out = build_local_evidence("nha nghi bao an yen dung", db_path=db_path)
    assert len(out) == 1
    assert path.read_bytes() == before_bytes, "db file changed"
    after_names = sorted(p.name for p in path.parent.iterdir())
    assert after_names == before_names, f"new files appeared: {set(after_names) - set(before_names)}"


def test_read_only_while_writer_holds_db(db_path):
    _seed_bao_an(db_path)
    from searchstore import SearchStore

    path = Path(db_path)
    wal = Path(f"{db_path}-wal")
    store = SearchStore(db_path)  # live writer: -shm/-wal siblings exist
    try:
        names = sorted(p.name for p in path.parent.iterdir())
        wal_size = wal.stat().st_size if wal.exists() else None
        out = build_local_evidence("nha nghi bao an", db_path=db_path)
        assert len(out) == 1
        assert sorted(p.name for p in path.parent.iterdir()) == names
        if wal.exists():
            assert wal.stat().st_size == wal_size  # our read grew nothing
    finally:
        store.close()


# ---------- output shape ----------


def test_local_evidence_dataclass_fields(db_path):
    _seed_bao_an(db_path)
    (ev,) = build_local_evidence("bao an", db_path=db_path)
    assert isinstance(ev, LocalEvidence)
    assert set(ev.__dataclass_fields__) == {
        "id",
        "title",
        "url",
        "content",
        "confidence",
        "freshness",
        "authority",
        "ambiguous",
    }
    assert json.dumps(ev.content, ensure_ascii=False)  # serializable str
