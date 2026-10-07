"""C2 local-evidence wiring tests (R15-B1; analysis/r15-interfaces.md §6.4).

After trust ordering, non-ambiguous vn-geo entities prepend as
``local://vn-geo/<id>`` evidence and ALL ids renumber 1..N — in BOTH fast
and deep modes. Empty/missing db -> no-op. Hermetic: scratch dbs are built
in tmp_path via ``vn_geo.business.upsert_entity``; the engine reads the
path from ``HERMES_GATEWAY_VN_GEO_DB``.
"""

from __future__ import annotations

from gateway.backends.stub import StubBackend
from gateway.config import GatewayConfig
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine
from gateway.core.local_context import VN_GEO_DB_ENV
from tests.gateway.conftest import FakeSynthesizer
from vn_geo import business


def _entity(name: str, address: str, *, area: str = "", province: str = "", lat=None, lng=None) -> dict:
    return {
        "name": name,
        "address_text": address,
        "area_old": area,
        "province": province,
        "lat": lat,
        "lng": lng,
        "source": "gmaps",
        "category": "food",
        "kind": "food",
        "status": "open",
        "geocode_status": "exact" if lat is not None else "approximate",
    }


def _cfg(tmp_path, **over) -> GatewayConfig:
    return GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
        **over,
    )


def _engine(tmp_path, **cfg_over) -> Engine:
    cfg = _cfg(tmp_path, **cfg_over)
    return Engine(
        cfg,
        backend=StubBackend(),
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )


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


def _ids_sequential(result) -> bool:
    return [s.id for s in result.sources] == list(range(1, len(result.sources) + 1))


# ---------- prepend + renumber, both modes ----------


def test_fast_mode_prepends_local_and_renumbers(tmp_path, monkeypatch):
    db = tmp_path / "vn-geo.db"
    eid = _seed_bao_an(str(db))
    monkeypatch.setenv(VN_GEO_DB_ENV, str(db))
    result = _engine(tmp_path).run("nha nghi bao an yen dung", depth="fast")
    assert result.depth == "fast"
    assert result.timings_ms["local_hits"] == 1
    assert "local_ms" in result.timings_ms
    assert result.sources[0].url == f"local://vn-geo/{eid}"
    assert result.sources[0].title == "Nhà nghỉ Bảo An"
    assert _ids_sequential(result)
    # Web evidence follows the local items.
    assert result.sources[1].url.startswith("https://stub.example/")


def test_deep_mode_prepends_local_and_renumbers(tmp_path, monkeypatch):
    db = tmp_path / "vn-geo.db"
    eid = _seed_bao_an(str(db))
    monkeypatch.setenv(VN_GEO_DB_ENV, str(db))
    result = _engine(tmp_path).run("nha nghi bao an yen dung", depth="deep")
    assert result.depth == "deep"
    assert result.timings_ms["local_hits"] == 1
    assert result.sources[0].url == f"local://vn-geo/{eid}"
    assert _ids_sequential(result)


def test_local_items_have_no_trust_score(tmp_path, monkeypatch):
    db = tmp_path / "vn-geo.db"
    _seed_bao_an(str(db))
    monkeypatch.setenv(VN_GEO_DB_ENV, str(db))
    result = _engine(tmp_path).run("nha nghi bao an yen dung", depth="fast")
    assert result.sources[0].url.startswith("local://")
    assert result.sources[0].trust_score is None  # trust_by_url unaffected


# ---------- ambiguity guard ----------


def test_ambiguous_same_name_entities_dropped(tmp_path, monkeypatch):
    db = tmp_path / "vn-geo.db"
    business.upsert_entity(
        str(db), _entity("Cafe Trung Nguyên", "1 Lê Lợi, Hải Phòng", area="Hải Phòng", lat=20.86, lng=106.68)
    )
    business.upsert_entity(
        str(db), _entity("Cafe Trung Nguyên", "9 Trần Phú, Hà Nội", area="Hà Nội", lat=21.03, lng=105.85)
    )
    monkeypatch.setenv(VN_GEO_DB_ENV, str(db))
    result = _engine(tmp_path).run("cafe trung nguyen", depth="fast")
    assert result.timings_ms["local_hits"] == 0  # both flagged ambiguous -> dropped
    assert all(not s.url.startswith("local://") for s in result.sources)


# ---------- graceful no-op ----------


def test_missing_db_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv(VN_GEO_DB_ENV, str(tmp_path / "does-not-exist.db"))
    result = _engine(tmp_path).run("nha nghi bao an yen dung", depth="fast")
    assert result.timings_ms["local_hits"] == 0
    assert "local_ms" in result.timings_ms
    assert not any("local" in w for w in result.warnings)
    assert result.sources and result.sources[0].url.startswith("https://stub.example/")


def test_no_match_is_noop(tmp_path, monkeypatch):
    db = tmp_path / "vn-geo.db"
    _seed_bao_an(str(db))
    monkeypatch.setenv(VN_GEO_DB_ENV, str(db))
    result = _engine(tmp_path).run("totally unrelated question zzz", depth="fast")
    assert result.timings_ms["local_hits"] == 0
    assert all(not s.url.startswith("local://") for s in result.sources)


def test_local_wiring_with_xhigh_disabled(tmp_path, monkeypatch):
    # C2 wiring is orthogonal to the xhigh deep-pipeline flag: it applies in
    # both modes either way; xhigh only gates plan/claims/revise stages.
    db = tmp_path / "vn-geo.db"
    eid = _seed_bao_an(str(db))
    monkeypatch.setenv(VN_GEO_DB_ENV, str(db))
    result = _engine(tmp_path, xhigh_enabled=False).run("nha nghi bao an yen dung", depth="deep")
    assert result.sources[0].url == f"local://vn-geo/{eid}"
    assert result.timings_ms["local_hits"] == 1
    for key in ("plan_ms", "verify_claims_ms", "revise_ms"):
        assert key not in result.timings_ms
