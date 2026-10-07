"""Hermetic tests for vn_geo.coverage (R14-B, contract analysis/r14-interfaces.md §2).

No network: geometry comes from tiny fixtures through a fake ``boundaries``
module exposing the SAME signatures as r14-a's
``province_bbox`` / ``load_geometry`` / ``point_in_geometry`` (+ ``lookup``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vn_geo import VnGeoError, coverage

FIXTURES = Path(__file__).parent / "fixtures" / "r14b"


# --------------------------------------------------------------------------- fake boundaries


def _point_in_ring(lat: float, lon: float, ring: list) -> bool:
    """Even-odd ray casting; ring coords are GeoJSON ``[lon, lat]``."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_polygon_rings(lat: float, lon: float, rings: list) -> bool:
    if not _point_in_ring(lat, lon, rings[0]):
        return False
    return not any(_point_in_ring(lat, lon, hole) for hole in rings[1:])


def _point_in_geometry(lat: float, lon: float, geom: dict) -> bool:
    kind = geom.get("type")
    if kind == "Polygon":
        return _point_in_polygon_rings(lat, lon, geom["coordinates"])
    if kind == "MultiPolygon":
        return any(_point_in_polygon_rings(lat, lon, poly) for poly in geom["coordinates"])
    raise ValueError(f"unsupported geometry type {kind!r}")


class FakeBoundaries:
    """Same signatures as vn_geo.boundaries; reads tests/fixtures/r14b/*."""

    def __init__(self, fixtures: Path = FIXTURES):
        self._dir = fixtures
        self._meta = json.loads((fixtures / "boundaries.json").read_text(encoding="utf-8"))

    def _entry(self, code):
        return self._meta.get(code)

    def province_bbox(self, db_path, code):
        entry = self._entry(code)
        return tuple(entry["bbox"]) if entry else None

    def load_geometry(self, db_path, code):
        entry = self._entry(code)
        if not entry:
            return None
        return json.loads((self._dir / entry["geom"]).read_text(encoding="utf-8"))

    def lookup(self, db_path, code):
        entry = self._entry(code)
        if not entry:
            return None
        return {"code": code, "name": entry["name"], "area_km2": entry["area_km2"], "bbox": entry["bbox"]}

    @staticmethod
    def point_in_geometry(lat, lon, geom):
        return _point_in_geometry(lat, lon, geom)


@pytest.fixture()
def fake(monkeypatch):
    fb = FakeBoundaries()
    monkeypatch.setattr(coverage, "boundaries", fb)
    return fb


# --------------------------------------------------------------------------- plan


def test_plan_square_expected_cells(fake):
    p = coverage.plan("db", "square", cell_km=2.0)
    assert p["area_code"] == "square"
    assert p["area_name"] == "Ô Vuông Test"
    assert p["area_km2"] == 123.4
    assert p["cell_km"] == 2.0
    assert p["cells_total"] == len(p["cells"]) == 30
    assert p["cells"][0]["cell_id"] == "square:0_0"
    assert p["cells"][-1]["cell_id"] == "square:4_5"
    # every cell center must re-verify as inside via the point-in-polygon fn
    geom = fake.load_geometry("db", "square")
    for cell in p["cells"]:
        assert _point_in_geometry(cell["lat"], cell["lon"], geom) is True


def test_plan_sorted_by_iy_then_ix(fake):
    p = coverage.plan("db", "square", cell_km=2.0)
    keys = []
    for cell in p["cells"]:
        ix, iy = cell["cell_id"].split(":", 1)[1].split("_")
        keys.append((int(iy), int(ix)))
    assert keys == sorted(keys)


def test_plan_deterministic(fake):
    a = coverage.plan("db", "square", cell_km=2.0)
    b = coverage.plan("db", "square", cell_km=2.0)
    assert json.dumps(a["cells"], ensure_ascii=False) == json.dumps(b["cells"], ensure_ascii=False)
    assert a["cells_total"] == b["cells_total"]


def test_plan_cell_bbox_matches_grid(fake):
    p = coverage.plan("db", "square", cell_km=2.0)
    cell = p["cells"][0]
    w, s, e, n = cell["bbox"]
    # center is the midpoint of the cell bbox
    assert cell["lon"] == pytest.approx((w + e) / 2)
    assert cell["lat"] == pytest.approx((s + n) / 2)
    # height ~= dlat; width ~= dlon
    assert (n - s) == pytest.approx(2.0 / 111.32, rel=1e-9)
    assert (e - w) > (n - s)  # dlon > dlat at 20 deg lat (cos < 1)


def test_plan_hole_clipped(fake):
    p = coverage.plan("db", "square-hole", cell_km=2.0)
    assert p["cells_total"] > 0
    # no kept center may fall inside the hole rectangle
    for cell in p["cells"]:
        in_hole = 106.05 < cell["lon"] < 106.15 and 20.05 < cell["lat"] < 20.15
        assert not in_hole
    geom = fake.load_geometry("db", "square-hole")
    for cell in p["cells"]:
        assert _point_in_geometry(cell["lat"], cell["lon"], geom) is True


def test_plan_multipolygon(fake):
    p = coverage.plan("db", "multi", cell_km=2.0)
    assert p["cells_total"] > 0
    geom = fake.load_geometry("db", "multi")
    for cell in p["cells"]:
        assert _point_in_geometry(cell["lat"], cell["lon"], geom) is True
    # two disjoint squares -> every kept center sits in one of the two boxes
    for cell in p["cells"]:
        in_a = 106.0 < cell["lon"] < 106.1 and 20.0 < cell["lat"] < 20.1
        in_b = 106.5 < cell["lon"] < 106.6 and 20.5 < cell["lat"] < 20.6
        assert in_a or in_b


def test_plan_origin_rounds_up(fake):
    # bbox west=105.9996 rounds UP to origin 106.0 -> ix_min == -1 candidates exist
    p = coverage.plan("db", "round-up", cell_km=2.0)
    assert p["cells_total"] == 30
    assert min(int(c["cell_id"].split(":", 1)[1].split("_")[0]) for c in p["cells"]) == 0


def test_plan_province_demo(fake):
    # province-scale polygon (rectangle approximating merged Hải Phòng); stands in
    # for the real r14-a boundary until that layer is merged into this worktree.
    p = coverage.plan("db", "hai-phong", cell_km=2.0)
    assert p["area_name"] == "Hải Phòng (demo)"
    assert p["area_km2"] == 3765.0
    assert p["cells_total"] > 0
    geom = fake.load_geometry("db", "hai-phong")
    for cell in p["cells"]:
        assert _point_in_geometry(cell["lat"], cell["lon"], geom) is True


def test_plan_missing_area_raises(fake):
    with pytest.raises(VnGeoError):
        coverage.plan("db", "does-not-exist", cell_km=2.0)


def test_plan_bad_cell_km_raises(fake):
    with pytest.raises(VnGeoError):
        coverage.plan("db", "square", cell_km=0)
    with pytest.raises(VnGeoError):
        coverage.plan("db", "square", cell_km=-1)


def test_plan_bad_area_code_raises(fake):
    with pytest.raises(VnGeoError):
        coverage.plan("db", "", cell_km=2.0)


def test_plan_without_boundaries_raises(monkeypatch):
    monkeypatch.setattr(coverage, "boundaries", None)
    with pytest.raises(VnGeoError):
        coverage.plan("db", "square", cell_km=2.0)


# --------------------------------------------------------------------------- expand


def test_expand_unique_job_ids(fake):
    p = coverage.plan("db", "square", cell_km=2.0)
    jobs = coverage.expand(p, ["food", "shops", "food"])  # dup dropped
    ids = [j["job_id"] for j in jobs]
    assert len(ids) == len(set(ids)) == p["cells_total"] * 2
    for job in jobs:
        assert job["job_id"] == f"{job['cell_id']}|{job['category']}"
        assert job["query"] == f"{job['category']} {p['area_name']}"
        assert job["lat"] == next(c["lat"] for c in p["cells"] if c["cell_id"] == job["cell_id"])


def test_expand_empty_categories(fake):
    p = coverage.plan("db", "square", cell_km=2.0)
    assert coverage.expand(p, []) == []
    assert coverage.expand(p, ["", "   "]) == []


# --------------------------------------------------------------------------- save / load / stats


def test_save_load_roundtrip(fake, tmp_path):
    p = coverage.plan("db", "square", cell_km=2.0)
    path = tmp_path / "plan.json"
    coverage.save_plan(p, path)
    assert coverage.load_plan(path) == p


def test_stats_keys(fake):
    p = coverage.plan("db", "square", cell_km=2.0)
    s = coverage.stats(p)
    assert s == {"cells_total": 30, "area_km2": 123.4, "cell_km": 2.0}


# --------------------------------------------------------------------------- cli


def test_cli_plan_stats_expand(fake, tmp_path):
    plan_path = tmp_path / "plan.json"
    jobs_path = tmp_path / "jobs.jsonl"

    assert (
        coverage.main(["plan", "--db", "db", "--area", "square", "--cell-km", "2", "--out", str(plan_path), "--json"])
        == 0
    )
    assert coverage.main(["stats", "--plan", str(plan_path), "--json"]) == 0
    assert (
        coverage.main(
            ["expand", "--plan", str(plan_path), "--categories", "food,shops", "--out", str(jobs_path), "--json"]
        )
        == 0
    )

    jobs = [json.loads(line) for line in jobs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(jobs) == 30 * 2
    assert len({j["job_id"] for j in jobs}) == len(jobs)


def test_cli_plan_missing_area_exit_1(fake, capsys):
    rc = coverage.main(["plan", "--db", "db", "--area", "nope"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err
