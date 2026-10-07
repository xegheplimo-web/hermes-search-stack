"""Tests for the refresh ``places`` step + PlaceProvider integration (R14-D).

Contract: analysis/r14-interfaces.md §4 (2). Hermetic: the offline gosom-jsonl
provider reads fixtures; a stub provider covers the interface path; scratch dbs
live in ``tmp_path``. No network.
"""

from __future__ import annotations

import json
import socket
import urllib.request
from pathlib import Path

import pytest

from searchstore import SearchStore
from vn_geo import VnGeoError, places, providers, refresh
from vn_geo.refresh import coverage, load_config, main

FIXTURES = Path(__file__).parent / "fixtures" / "r14d"
GOSOM = FIXTURES / "gosom_places.jsonl"
GOSOM_UPDATED = FIXTURES / "gosom_places_updated.jsonl"
CONFIG_PLACES = FIXTURES / "config_places.json"
SCAN_STALE = FIXTURES / "scan_stale.jsonl"


def _write_config(tmp_path: Path, areas, extra: dict | None = None, name: str = "cfg.json") -> Path:
    data: dict = {"areas": areas}
    if extra:
        data.update(extra)
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _refresh_events(store) -> list[dict]:
    return [
        json.loads(r["payload"])
        for r in store.conn.execute("SELECT payload FROM events WHERE kind = 'refresh_run' ORDER BY id")
    ]


def _scratch_gosom(tmp_path: Path, source: Path = GOSOM) -> Path:
    dest = tmp_path / "gosom.jsonl"
    dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def _places_cfg(path) -> dict:
    return {"areas": [{"name": "Yên Dũng", "places": {"provider": "gosom-jsonl", "path": str(path)}}]}


def _net_boom(*a, **k):
    raise AssertionError("refresh places step attempted network access")


class _StubProvider:
    """Minimal PlaceProvider stub for the interface path (name matches a known provider)."""

    name = "gosom-jsonl"

    def __init__(self, records: list[dict]):
        self.records = records
        self.calls: list[dict] = []

    def fetch(self, area_cfg: dict) -> list[dict]:
        self.calls.append(area_cfg)
        return list(self.records)


@pytest.fixture()
def store(tmp_path):
    s = SearchStore(tmp_path / "refresh_places.db")
    yield s
    s.close()


# ---------- SOURCES / default sources ----------


def test_sources_includes_places():
    assert "places" in refresh.SOURCES


def test_default_sources_opt_in_places():
    assert refresh._default_sources([{"name": "A"}]) == ["admin", "overpass", "ckan"]
    assert refresh._default_sources([{"name": "A", "places": {"provider": "gosom-jsonl", "path": "x"}}]) == [
        "admin",
        "overpass",
        "ckan",
        "places",
    ]


# ---------- config validation ----------


def test_load_config_accepts_places_block():
    cfg = load_config(CONFIG_PLACES)
    assert cfg["areas"][0]["places"]["provider"] == "gosom-jsonl"
    assert cfg["places_max_age_days"] == 90


@pytest.mark.parametrize("places_cfg", ["x", 5, []])
def test_load_config_places_not_object(tmp_path, places_cfg):
    with pytest.raises(VnGeoError, match="places"):
        load_config(_write_config(tmp_path, [{"name": "A", "places": places_cfg}]))


@pytest.mark.parametrize("provider", [None, "", "   ", 5])
def test_load_config_places_bad_provider(tmp_path, provider):
    with pytest.raises(VnGeoError, match="provider"):
        load_config(_write_config(tmp_path, [{"name": "A", "places": {"provider": provider, "path": "x"}}]))


def test_load_config_places_unknown_provider(tmp_path):
    with pytest.raises(VnGeoError, match="unknown places provider"):
        load_config(_write_config(tmp_path, [{"name": "A", "places": {"provider": "nope", "path": "x"}}]))


@pytest.mark.parametrize("path", [None, "", "   ", 5])
def test_load_config_places_bad_path(tmp_path, path):
    with pytest.raises(VnGeoError, match="path"):
        load_config(_write_config(tmp_path, [{"name": "A", "places": {"provider": "gosom-jsonl", "path": path}}]))


# ---------- plan / dry run ----------


def test_dry_run_plan_includes_places_step(store):
    cfg = _places_cfg("somewhere.jsonl")
    result = refresh.refresh(store, config=cfg, sources=["places"], dry_run=True)
    assert result == {
        "dry_run": True,
        "plan": [{"source": "places", "area": "Yên Dũng", "provider": "gosom-jsonl", "path": "somewhere.jsonl"}],
    }


def test_dry_run_default_adds_places_only_when_configured(store):
    cfg = _places_cfg("x.jsonl")
    sources = [s["source"] for s in refresh.refresh(store, config=cfg, dry_run=True)["plan"]]
    assert sources == ["admin", "places"]


def test_plan_steps_places_needs_provider_and_path(store):
    with pytest.raises(VnGeoError, match="provider"):
        refresh._plan_steps([{"name": "A", "places": {"provider": "", "path": "x"}}], ["places"])


# ---------- end-to-end (real offline provider + scratch db) ----------


def test_places_step_end_to_end_first_run(tmp_path):
    src = _scratch_gosom(tmp_path)
    with SearchStore(tmp_path / "scratch.db") as store:
        result = refresh.refresh(store, config=_places_cfg(src), sources=["places"])
        assert result["dry_run"] is False
        (area,) = result["areas"]
        assert area["sources"]["places"] == {"new": 3, "updated": 0, "unchanged": 0}
        (event,) = _refresh_events(store)
        assert event["sources"] == ["places"]
        assert event["places"] == {
            "provider": "gosom-jsonl",
            "records_raw": 3,
            "records_new": 3,
            "records_updated": 0,
            "records_unchanged": 0,
            "errors": [],
        }
        # the place docs are queryable back out of the scratch db
        rows = places.query_places(store, area="Yên Dũng", limit=10)
        assert len(rows) == 3
        assert {r["name"] for r in rows} == {
            "Quán Phở Bò Yên Dũng",
            "Bún Chả Hương Giang",
            "Nha Khoa Huy Tâm",
        }


def test_places_step_rerun_is_idempotent(tmp_path):
    src = _scratch_gosom(tmp_path)
    with SearchStore(tmp_path / "scratch.db") as store:
        refresh.refresh(store, config=_places_cfg(src), sources=["places"])
        result = refresh.refresh(store, config=_places_cfg(src), sources=["places"])
        (area,) = result["areas"]
        assert area["sources"]["places"] == {"new": 0, "updated": 0, "unchanged": 3}
        event = _refresh_events(store)[-1]
        assert event["places"]["records_new"] == 0
        assert event["places"]["records_unchanged"] == 3
        assert event["places"]["records_raw"] == 3


def test_places_step_detects_update(tmp_path):
    src = _scratch_gosom(tmp_path)
    with SearchStore(tmp_path / "scratch.db") as store:
        refresh.refresh(store, config=_places_cfg(src), sources=["places"])
        src.write_text(GOSOM_UPDATED.read_text(encoding="utf-8"), encoding="utf-8")
        result = refresh.refresh(store, config=_places_cfg(src), sources=["places"])
        (area,) = result["areas"]
        assert area["sources"]["places"] == {"new": 0, "updated": 1, "unchanged": 2}
        event = _refresh_events(store)[-1]
        assert event["places"] == {
            "provider": "gosom-jsonl",
            "records_raw": 3,
            "records_new": 0,
            "records_updated": 1,
            "records_unchanged": 2,
            "errors": [],
        }


def test_stub_provider_end_to_end(store, monkeypatch):
    stub = _StubProvider(
        [
            {"source": "google-maps", "source_id": "s1", "name": "A", "address": "x, Yên Dũng", "lat": 1.0, "lon": 2.0},
            {"source": "google-maps", "source_id": "s2", "name": "B", "address": "y, Yên Dũng", "lat": 3.0, "lon": 4.0},
        ]
    )
    monkeypatch.setattr(providers, "get_provider", lambda name, cfg=None: stub)
    result = refresh.refresh(store, config=_places_cfg("ignored.jsonl"), sources=["places"])
    (area,) = result["areas"]
    assert area["sources"]["places"] == {"new": 2, "updated": 0, "unchanged": 0}
    assert stub.calls and stub.calls[0]["places"]["path"] == "ignored.jsonl"
    (event,) = _refresh_events(store)
    assert event["places"]["provider"] == "gosom-jsonl"
    assert event["places"]["records_raw"] == 2


def test_places_step_error_isolation(store):
    cfg = _places_cfg("does-not-exist.jsonl")
    result = refresh.refresh(store, config=cfg, sources=["places"], sleeper=lambda s: None)
    (area,) = result["areas"]
    assert "error" in area["sources"]["places"]
    assert result["totals"]["errors"] == 1
    (event,) = _refresh_events(store)
    assert event["places"]["records_raw"] == 0
    assert event["places"]["errors"] == [{"area": "Yên Dũng", "error": area["sources"]["places"]["error"]}]


def test_refresh_places_no_network(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", _net_boom)
    monkeypatch.setattr(urllib.request, "urlopen", _net_boom)
    src = _scratch_gosom(tmp_path)
    with SearchStore(tmp_path / "scratch.db") as store:
        result = refresh.refresh(store, config=_places_cfg(src), sources=["places"])
        assert result["areas"][0]["sources"]["places"]["new"] == 3


# ---------- coverage: places_gap + places_stale ----------


def test_coverage_places_gap_and_stale(tmp_path):
    with SearchStore(tmp_path / "cov.db") as store:
        places.save_places(store, places.load_scan_jsonl(SCAN_STALE), source="google-maps")
        report = coverage(store, config={"areas": [{"name": "Yên Dũng"}, {"name": "Hải Phòng"}]})
    assert report["places_gap"] == ["hai-phong"]
    (stale,) = report["places_stale"]
    assert stale["area"] == "yen-dung"
    assert stale["last_scan"] == "2020-01-01T00:00:00+07:00"
    assert stale["age_days"] > 90


def test_coverage_places_threshold_from_config(tmp_path):
    with SearchStore(tmp_path / "cov.db") as store:
        places.save_places(store, places.load_scan_jsonl(SCAN_STALE), source="google-maps")
        relaxed = coverage(
            store,
            config={"places_max_age_days": 100000, "areas": [{"name": "Yên Dũng"}]},
        )
    assert relaxed["places_stale"] == []
    assert relaxed["places_gap"] == []


def test_coverage_places_keys_present_without_config(store):
    report = coverage(store)
    assert report["places_gap"] == []
    assert report["places_stale"] == []


def test_coverage_places_uses_code_field(tmp_path):
    with SearchStore(tmp_path / "cov.db") as store:
        report = coverage(store, config={"areas": [{"name": "Nowhere Land", "code": "custom-code"}]})
    assert report["places_gap"] == ["custom-code"]


# ---------- CLI ----------


def test_cli_run_sources_places(tmp_path):
    src = _scratch_gosom(tmp_path)
    cfg_path = _write_config(tmp_path, _places_cfg(src)["areas"])
    db = str(tmp_path / "cli.db")
    assert main(["run", "--db", db, "--config", str(cfg_path), "--sources", "places", "--min-interval", "0"]) == 0
    with SearchStore(db) as store:
        (event,) = _refresh_events(store)
        assert event["sources"] == ["places"]
        assert event["places"]["records_new"] == 3


def test_cli_run_default_runs_places_when_configured(tmp_path):
    src = _scratch_gosom(tmp_path)
    cfg_path = _write_config(tmp_path, _places_cfg(src)["areas"])
    db = str(tmp_path / "cli.db")
    assert main(["run", "--db", db, "--config", str(cfg_path), "--min-interval", "0"]) == 0
    with SearchStore(db) as store:
        (event,) = _refresh_events(store)
        assert "places" in event["sources"]
        assert event["places"]["records_new"] == 3
