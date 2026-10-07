"""vn_geo.coverage — deterministic polygon-clipped grid coverage planner (R14-B).

Contract: ``analysis/r14-interfaces.md`` §2 (FROZEN). The planner turns a
province boundary (r14-a ``vn_geo.boundaries``) into a resumable list of
square crawl cells whose CENTERS fall inside the polygon, then fans each cell
out into per-category crawl jobs.

Grid (deterministic — same inputs → byte-identical ``cells`` list):

- ``origin`` = bbox min corner ``(west, south)`` rounded to 3 decimals.
- ``dlat = cell_km / 111.32``
- ``dlon = cell_km / (111.32 * cos(radians(bbox_center_lat)))``
- cell center = ``origin + ((ix + 0.5) * dlon, (iy + 0.5) * dlat)``
- ``cell_id = f"{area_code}:{ix}_{iy}"`` (indices are relative to origin and
  MAY be negative — the origin corner rounds to 3 dp, so ``ix_min``/``iy_min``
  can be ``-1`` when rounding nudges the origin past the bbox edge).
- keep a cell iff its CENTER passes ``boundaries.point_in_geometry``.
- rows sorted by ``(iy, ix)`` ascending.

The module consumes geometry through the frozen §1 signatures
(``boundaries.province_bbox`` / ``load_geometry`` / ``point_in_geometry``,
plus ``lookup`` for the area name + area_km2). It is **stdlib-only and does
no network** — the geometry already lives in the SearchStore db; tests inject
a fake ``boundaries`` module (same signatures) over fixtures.

CLI: ``python -m vn_geo.coverage <plan|stats|expand> ...`` — exit 0 ok ·
1 runtime error · 2 usage/IO.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

from . import VnGeoError

# r14-a (vn_geo/boundaries.py) may not be merged into this worktree yet; import
# it defensively so this module still imports, and fail with clear guidance when
# a plan is actually requested. Tests monkeypatch ``coverage.boundaries`` with a
# fake exposing the SAME signatures.
try:  # pragma: no cover - exercised only when r14-a is merged
    from . import boundaries
except ImportError:  # pragma: no cover - r14-a not merged yet
    boundaries = None  # type: ignore[assignment]

EARTH_KM_PER_DEG_LAT = 111.32
ORIGIN_DECIMALS = 3

BOUNDARIES_GUIDANCE = (
    "vn_geo.boundaries is not available yet — merge r14-a first, or inject a "
    "fake boundaries module exposing province_bbox/load_geometry/point_in_geometry."
)


# --------------------------------------------------------------------------- helpers


def _boundaries():
    """Return the boundaries module, or raise actionable guidance when absent."""
    if boundaries is None:
        raise VnGeoError(BOUNDARIES_GUIDANCE)
    return boundaries


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _bbox_tuple(raw) -> tuple[float, float, float, float]:
    """Normalize a boundary bbox to ``(west, south, east, north)`` floats."""
    try:
        west, south, east, north = (float(v) for v in raw)
    except (TypeError, ValueError) as exc:
        raise VnGeoError(f"invalid boundary bbox {raw!r}: expected 4 numbers W,S,E,N") from exc
    if not (west < east and south < north):
        raise VnGeoError(f"invalid boundary bbox {raw!r}: need west<east and south<north")
    return west, south, east, north


def _grid(bbox: tuple[float, float, float, float], cell_km: float) -> tuple[float, float, float, float]:
    """Return ``(origin_lon, origin_lat, dlon, dlat)`` for the frozen grid."""
    west, south, east, north = bbox
    origin_lon = round(west, ORIGIN_DECIMALS)
    origin_lat = round(south, ORIGIN_DECIMALS)
    center_lat = (south + north) / 2.0
    dlat = cell_km / EARTH_KM_PER_DEG_LAT
    dlon = cell_km / (EARTH_KM_PER_DEG_LAT * math.cos(math.radians(center_lat)))
    return origin_lon, origin_lat, dlon, dlat


def _candidate_indices(bbox, origin_lon, origin_lat, dlon, dlat):
    """Integer index ranges whose cell centers can fall within the bbox.

    Indices are relative to ``origin`` and may be negative. The upper bounds may
    include one extra cell whose center lies just beyond the bbox edge — that
    cell is outside the polygon (polygon ⊆ bbox) and is rejected by the
    point-in-polygon test, so coverage is never under-counted.
    """
    west, south, east, north = bbox
    ix_min = math.floor((west - origin_lon) / dlon)
    ix_max = math.floor((east - origin_lon) / dlon)
    iy_min = math.floor((south - origin_lat) / dlat)
    iy_max = math.floor((north - origin_lat) / dlat)
    return ix_min, ix_max, iy_min, iy_max


# --------------------------------------------------------------------------- core


def plan(db_path, area_code: str, cell_km: float = 2.0) -> dict:
    """Build a deterministic, polygon-clipped grid plan for one province.

    Returns ``{"area_code","area_name","cell_km","cells":[{"cell_id","lat","lon",
    "bbox":[w,s,e,n]}],"cells_total","area_km2","generated_at"}``.
    """
    if not isinstance(area_code, str) or not area_code.strip():
        raise VnGeoError("area_code must be a non-empty province code (e.g. 'hai-phong')")
    if not isinstance(cell_km, (int, float)) or isinstance(cell_km, bool) or cell_km <= 0:
        raise VnGeoError(f"cell_km must be a positive number, got {cell_km!r}")

    b = _boundaries()
    raw_bbox = b.province_bbox(db_path, area_code)
    if raw_bbox is None:
        raise VnGeoError(f"no boundary for area {area_code!r} in {db_path} — run r14-a ingest first")
    geom = b.load_geometry(db_path, area_code)
    if geom is None:
        raise VnGeoError(f"no geometry for area {area_code!r} in {db_path} — run r14-a ingest first")
    bbox = _bbox_tuple(raw_bbox)

    area_name = area_code
    area_km2 = None
    lookup = getattr(b, "lookup", None)
    if callable(lookup):
        info = lookup(db_path, area_code)
        if isinstance(info, dict):
            area_name = info.get("name") or area_code
            area_km2 = info.get("area_km2")

    cell_km = float(cell_km)
    origin_lon, origin_lat, dlon, dlat = _grid(bbox, cell_km)
    ix_min, ix_max, iy_min, iy_max = _candidate_indices(bbox, origin_lon, origin_lat, dlon, dlat)

    keyed: list[tuple[int, int, dict]] = []
    for iy in range(iy_min, iy_max + 1):
        lat = origin_lat + (iy + 0.5) * dlat
        for ix in range(ix_min, ix_max + 1):
            lon = origin_lon + (ix + 0.5) * dlon
            if not b.point_in_geometry(lat, lon, geom):
                continue
            keyed.append(
                (
                    iy,
                    ix,
                    {
                        "cell_id": f"{area_code}:{ix}_{iy}",
                        "lat": lat,
                        "lon": lon,
                        "bbox": [
                            origin_lon + ix * dlon,
                            origin_lat + iy * dlat,
                            origin_lon + (ix + 1) * dlon,
                            origin_lat + (iy + 1) * dlat,
                        ],
                    },
                )
            )
    keyed.sort(key=lambda item: (item[0], item[1]))  # (iy, ix)
    cells = [cell for _, _, cell in keyed]

    return {
        "area_code": area_code,
        "area_name": area_name,
        "cell_km": cell_km,
        "cells": cells,
        "cells_total": len(cells),
        "area_km2": area_km2,
        "generated_at": _now_iso(),
    }


def stats(plan_dict: dict) -> dict:
    """Return ``{"cells_total","area_km2","cell_km"}`` for a plan dict."""
    return {
        "cells_total": plan_dict.get("cells_total", len(plan_dict.get("cells", []))),
        "area_km2": plan_dict.get("area_km2"),
        "cell_km": plan_dict.get("cell_km"),
    }


def expand(plan_dict: dict, categories) -> list[dict]:
    """Fan a plan into per-category crawl jobs (order-preserving).

    ``categories`` is an iterable of category names; duplicates are dropped
    (first occurrence wins) so every ``job_id`` = ``f"{cell_id}|{cat}"`` is
    unique.
    """
    seen: set[str] = set()
    cats: list[str] = []
    for raw in categories:
        cat = str(raw).strip()
        if cat and cat not in seen:
            seen.add(cat)
            cats.append(cat)
    area_name = plan_dict.get("area_name") or plan_dict.get("area_code") or ""
    jobs: list[dict] = []
    for cell in plan_dict.get("cells", []):
        for cat in cats:
            jobs.append(
                {
                    "job_id": f"{cell['cell_id']}|{cat}",
                    "cell_id": cell["cell_id"],
                    "lat": cell["lat"],
                    "lon": cell["lon"],
                    "category": cat,
                    "query": f"{cat} {area_name}",
                }
            )
    return jobs


def save_plan(plan_dict: dict, path) -> None:
    """Write a plan dict to ``path`` as pretty UTF-8 JSON."""
    text = json.dumps(plan_dict, ensure_ascii=False, indent=2) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def load_plan(path) -> dict:
    """Read a plan dict written by :func:`save_plan`."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- cli


def _save_jsonl(records: list[dict], path) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _cmd_plan(args: argparse.Namespace) -> int:
    plan_dict = plan(args.db, args.area, cell_km=args.cell_km)
    if args.out:
        save_plan(plan_dict, args.out)
    summary = {**stats(plan_dict), "area_code": plan_dict["area_code"], "out": args.out}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        area = plan_dict["area_km2"]
        tail = f" · area {area:.1f} km²" if isinstance(area, (int, float)) else ""
        out = f" -> {args.out}" if args.out else ""
        print(
            f"planned {summary['cells_total']} cells for {plan_dict['area_code']} "
            f"({plan_dict['area_name']}) @ {plan_dict['cell_km']} km{tail}{out}"
        )
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    plan_dict = load_plan(args.plan)
    summary = {"area_code": plan_dict.get("area_code"), **stats(plan_dict)}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(
            f"{summary['area_code']}: {summary['cells_total']} cells @ {summary['cell_km']} km "
            f"· area_km2={summary['area_km2']}"
        )
    return 0


def _cmd_expand(args: argparse.Namespace) -> int:
    plan_dict = load_plan(args.plan)
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    jobs = expand(plan_dict, categories)
    _save_jsonl(jobs, args.out)
    summary = {"jobs": len(jobs), "categories": categories, "out": args.out}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"expanded {len(jobs)} jobs -> {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vn_geo.coverage", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="build a polygon-clipped grid plan for a province")
    p.add_argument("--db", required=True, help="SearchStore db path holding the boundary layer")
    p.add_argument("--area", required=True, help="province area code (e.g. hai-phong)")
    p.add_argument("--cell-km", type=float, default=2.0, help="grid cell size in km (default 2.0)")
    p.add_argument("--out", help="write the plan JSON here")
    p.add_argument("--json", action="store_true", help="print machine-readable summary")
    p.set_defaults(func=_cmd_plan)

    p = sub.add_parser("stats", help="summarize a saved plan")
    p.add_argument("--plan", required=True, help="plan JSON path (from 'plan --out')")
    p.add_argument("--json", action="store_true", help="print machine-readable summary")
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("expand", help="fan a saved plan into per-category crawl jobs")
    p.add_argument("--plan", required=True, help="plan JSON path (from 'plan --out')")
    p.add_argument("--categories", required=True, help="comma-separated categories (e.g. food,shops)")
    p.add_argument("--out", required=True, help="output JSONL path")
    p.add_argument("--json", action="store_true", help="print machine-readable summary")
    p.set_defaults(func=_cmd_expand)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except VnGeoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
