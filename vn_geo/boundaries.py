"""vn_geo.boundaries — 34-province polygon boundary layer (R14-A).

Frozen contract: ``analysis/r14-interfaces.md`` §1.

Source: pinned GeoJSON FeatureCollection (34 province features) from
adminvsrm/GISData @ ``SOURCE_COMMIT``. The one permitted live fetch lands in
``data/boundaries/`` (gitignored); tests never touch the network.

Identity / naming
-----------------
``code = slug(fold_d(name))`` — unaccented hyphenated slug of the source
province name (``Hà Nội`` -> ``ha-noi``, ``TP. Hồ Chí Minh`` ->
``tp-ho-chi-minh``). The source's own ``Ma`` property is kept verbatim in the
document meta as ``source_props_code`` — it follows a different numbering
than provinces.open-api.vn, so the two codes are never mixed.

Source-data quirk (verified 2026-10-07 on the pinned file): feature
``Ma == "31"`` is labeled ``TinhThanh == "Lạng Sơn"`` but its geometry is the
post-merger Đồng Tháp polygon (bbox 105.18–106.82 E / 10.13–10.97 N, Mekong
Delta; real Lạng Sơn is ``Ma == "11"`` at 21.3–22.5 N). Upstream duplicated
Lạng Sơn's property row onto Đồng Tháp's geometry, and Đồng Tháp is
otherwise absent from the file. ``_NAME_FIXES`` repairs the label
deterministically — without it, the two "Lạng Sơn" features collide on the
``lang-son`` primary key and only 33 provinces would ingest.

admin_code
----------
``admin_code`` is matched by NAME against ``vn_geo.admin_units`` v2 province
records already present in the same store (``provider='provinces-api'``,
``format='admin'``, ``meta.level == 'province'``; version==2 preferred). Both
sides are compared via ``_admin_key`` — ``fold_d`` + unaccent + casefold,
with a leading ``Tỉnh`` / ``Thành phố`` / ``TP.`` token stripped. ``None``
when the db has no matching admin doc (a fresh scratch db yields
``admin_code=None`` until ``admin_units`` is ingested there).

Geometry rules
--------------
GeoJSON coordinate order is ``[lon, lat]`` (x=lon, y=lat) — never swapped.
Polygon and MultiPolygon are supported; a polygon's rings are its outer ring
plus hole rings. Point-in-polygon is hole-aware even-odd ray casting
(``inside outer AND NOT inside any hole``), bbox-prefiltered first.
``centroid`` is a representative point GUARANTEED inside the geometry: the
bbox center if inside, else the first hit of a deterministic 100×100 grid
scan over the bbox, else a horizontal-scanline intersection fallback (the
deterministic safety net for needle-thin shapes a fixed grid can miss).

Storage
-------
``boundary_geom`` (same db, CREATE IF NOT EXISTS) holds the raw GeoJSON
geometry per province ``code``. Each province also lands in ``documents``
as ``vn://boundary/province/{code}`` (format ``'boundary'``, provider
``'gisdata'``, text=name, meta=lookup dict + level/geom_in_table/source
fields) so boundaries version and dedupe like every other corpus. Re-ingest
of identical geometry counts ``unchanged``; changed geometry counts
``updated``.

CLI: ``python -m vn_geo.boundaries fetch|ingest|lookup|locate|list`` —
exit 0 ok · 1 runtime error · 2 usage/IO.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from searchstore import SearchStore, SearchStoreError
from searchstore.store import fold_d

from . import VnGeoError

SOURCE_COMMIT = "7645534d0a482ee867f26f137d3dd3fc54d9446f"
SOURCE_URL = (
    "https://raw.githubusercontent.com/adminvsrm/GISData/"
    f"{SOURCE_COMMIT}/Vietnam%20Administrative%20Divisions%20(Post-2025)%20-%20"
    "%C4%90%C6%A1n%20v%E1%BB%8B%20h%C3%A0nh%20ch%C3%ADnh%20Vi%E1%BB%87t%20Nam%20"
    "(T%E1%BB%AB%202025)/Provinces.geojson"
)
# Pinned-spec fallback: same sha + path served via github.com/.../raw/.
_SOURCE_FALLBACK_URL = SOURCE_URL.replace(
    "raw.githubusercontent.com/adminvsrm/GISData/", "github.com/adminvsrm/GISData/raw/"
)
EXPECTED_SOURCE_BYTES = 15_927_181
SOURCE_SIZE_TOLERANCE = 0.01
CACHE_FILENAME = "Provinces.geojson"
DEFAULT_CACHE_DIR = "data/boundaries"
DEFAULT_TIMEOUT = 60.0
USER_AGENT = "hermes-vn-geo/0.1"

BOUNDARY_PROVIDER = "gisdata"
BOUNDARY_FORMAT = "boundary"
GRID_RESOLUTION = 100
_KM_PER_DEG = 111.32

# Property keys tried in order for the province name / source feature code.
_NAME_KEYS = ("TinhThanh", "ten_tinh", "name", "Name", "NAME_1", "province")
_CODE_KEYS = ("Ma", "code", "ma_tinh", "id")

# (source feature code, raw source name) -> corrected province name.
# See the module docstring for the verified Lạng Sơn/Đồng Tháp mislabel.
_NAME_FIXES = {("31", "Lạng Sơn"): "Đồng Tháp"}

_DDL = """\
CREATE TABLE IF NOT EXISTS boundary_geom(
  code TEXT PRIMARY KEY,
  level TEXT NOT NULL DEFAULT 'province',
  name TEXT,
  geom_json TEXT NOT NULL,
  source TEXT,
  source_commit TEXT,
  source_sha256 TEXT,
  fetched_at TEXT
);
"""


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- name helpers


def _unaccent(text: str) -> str:
    s = fold_d(str(text))
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _slug(text: str) -> str:
    """``slug(fold_d(name))``: 'Hà Nội' -> 'ha-noi', 'TP. Hồ Chí Minh' -> 'tp-ho-chi-minh'."""
    return re.sub(r"[^a-z0-9]+", "-", _unaccent(text).casefold()).strip("-")


def _admin_key(name: str) -> str:
    """Folded token key minus a leading admin-type prefix (Tỉnh/Thành phố/TP.)."""
    toks = re.findall(r"[a-z0-9]+", _unaccent(name).casefold())
    if toks[:2] == ["thanh", "pho"]:
        toks = toks[2:]
    elif toks[:1] in (["tinh"], ["tp"], ["thanhpho"]):
        toks = toks[1:]
    return " ".join(toks)


# --------------------------------------------------------------------------- geometry (pure)


def _polygon_rings(geom: dict) -> list[list]:
    """Normalize a geometry to a list of ring-lists (one per polygon part)."""
    gtype = geom.get("type") if isinstance(geom, dict) else None
    coords = geom.get("coordinates") if isinstance(geom, dict) else None
    if gtype == "Polygon":
        return [coords] if isinstance(coords, list) else []
    if gtype == "MultiPolygon":
        return [p for p in (coords or []) if isinstance(p, list)]
    raise VnGeoError(f"unsupported geometry type {gtype!r}; expected Polygon or MultiPolygon")


def _geometry_bbox(geom: dict) -> tuple[float, float, float, float] | None:
    """Return ``(west, south, east, north)`` over all rings, or None if empty."""
    w = s = float("inf")
    e = n = float("-inf")
    found = False
    for rings in _polygon_rings(geom):
        for ring in rings:
            for pos in ring:
                x, y = pos[0], pos[1]
                found = True
                if x < w:
                    w = x
                if x > e:
                    e = x
                if y < s:
                    s = y
                if y > n:
                    n = y
    return (w, s, e, n) if found else None


def _ring_contains(x: float, y: float, ring: list) -> bool:
    """Even-odd ray cast toward +x for point (x, y) against one linear ring."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(x: float, y: float, rings: list) -> bool:
    """Hole-aware containment: inside the outer ring AND NOT inside any hole."""
    if not rings or not _ring_contains(x, y, rings[0]):
        return False
    return not any(_ring_contains(x, y, hole) for hole in rings[1:])


def point_in_geometry(lat: float, lon: float, geom: dict) -> bool:
    """Pure predicate: True iff (lat, lon) lies inside *geom*.

    *geom* is a GeoJSON ``Polygon`` or ``MultiPolygon`` dict with ``[lon, lat]``
    coordinate order. Bbox prefilter first, then hole-aware even-odd ray cast
    per polygon part. No db access — ``vn_geo.coverage`` imports this.
    """
    lat, lon = float(lat), float(lon)
    bbox = _geometry_bbox(geom)
    if bbox is None or not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
        return False
    return any(_point_in_polygon(lon, lat, rings) for rings in _polygon_rings(geom))


def _ring_area_deg2(ring: list) -> float:
    """Signed shoelace area in square degrees (sign = winding direction)."""
    total = 0.0
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        total += xj * yi - xi * yj
        j = i
    return total / 2.0


def _area_km2(geom: dict, ref_lat: float) -> float:
    """Approximate geodesic area: shoelace deg² scaled by km/deg at *ref_lat*."""
    deg2 = 0.0
    for rings in _polygon_rings(geom):
        if not rings:
            continue
        deg2 += abs(_ring_area_deg2(rings[0])) - sum(abs(_ring_area_deg2(h)) for h in rings[1:])
    km_per_deg_lon = _KM_PER_DEG * math.cos(math.radians(ref_lat))
    return deg2 * km_per_deg_lon * _KM_PER_DEG


# --------------------------------------------------------------------------- representative point


def _scan_xs(y: float, ring: list) -> list[float]:
    """Sorted x-coordinates where the horizontal line at *y* crosses *ring*."""
    xs: list[float] = []
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            xs.append(xi + (y - yi) * (xj - xi) / (yj - yi))
        j = i
    xs.sort()
    return xs


def _subtract_interval(interval: tuple[float, float], holes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = [interval]
    for ha, hb in holes:
        nxt: list[tuple[float, float]] = []
        for a, b in out:
            if hb <= a or ha >= b:
                nxt.append((a, b))
            else:
                if ha > a:
                    nxt.append((a, ha))
                if hb < b:
                    nxt.append((hb, b))
        out = nxt
    return out


def _scanline_point(geom: dict) -> tuple[float, float] | None:
    """Deterministic interior point via horizontal scanlines (fallback).

    For each polygon (largest first), scan y at the midpoints between
    consecutive sorted outer-ring vertex latitudes — every such y avoids
    vertices, so ring crossings pair cleanly into inside intervals. Holes are
    subtracted; the widest surviving interval's midpoint is verified with the
    real predicate. Returns ``(lat, lon)`` or None for degenerate geometry.
    """
    polys = sorted(
        (r for r in _polygon_rings(geom) if r),
        key=lambda r: -abs(_ring_area_deg2(r[0])),
    )
    for rings in polys:
        ys = sorted({p[1] for p in rings[0]})
        for ya, yb in zip(ys, ys[1:], strict=False):
            y = (ya + yb) / 2.0
            xs = _scan_xs(y, rings[0])
            hole_xs: list[tuple[float, float]] = []
            for hole in rings[1:]:
                hx = _scan_xs(y, hole)
                hole_xs += [(hx[i], hx[i + 1]) for i in range(0, len(hx) - 1, 2)]
            inside: list[tuple[float, float]] = []
            for i in range(0, len(xs) - 1, 2):
                inside += _subtract_interval((xs[i], xs[i + 1]), hole_xs)
            if not inside:
                continue
            xa, xb = max(inside, key=lambda iv: iv[1] - iv[0])
            x = (xa + xb) / 2.0
            if point_in_geometry(y, x, geom):
                return y, x
    return None


def _representative_point(geom: dict) -> tuple[float, float]:
    """Return ``(lat, lon)`` guaranteed inside *geom*.

    Order (contract §1): bbox center if inside; else first inside point of a
    deterministic 100×100 grid over the bbox (row-major from SW); else the
    scanline fallback. Raises VnGeoError for empty/degenerate geometry.
    """
    bbox = _geometry_bbox(geom)
    if bbox is None:
        raise VnGeoError("cannot derive a representative point: geometry has no coordinates")
    w, s, e, n = bbox
    cy, cx = (s + n) / 2.0, (w + e) / 2.0
    if point_in_geometry(cy, cx, geom):
        return cy, cx
    dy = (n - s) / GRID_RESOLUTION
    dx = (e - w) / GRID_RESOLUTION
    if dx > 0 and dy > 0:
        for iy in range(GRID_RESOLUTION):
            y = s + (iy + 0.5) * dy
            for ix in range(GRID_RESOLUTION):
                x = w + (ix + 0.5) * dx
                if point_in_geometry(y, x, geom):
                    return y, x
    pt = _scanline_point(geom)
    if pt is not None:
        return pt
    raise VnGeoError("cannot derive a representative point: degenerate geometry")


# --------------------------------------------------------------------------- fetch


def _http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - pinned https module constant
        return resp.read()


def fetch_source(cache_dir: str = DEFAULT_CACHE_DIR, force: bool = False) -> str:
    """Fetch the pinned provinces GeoJSON into *cache_dir*; return its path.

    Cached files are reused (one live fetch total). On a 404 the pinned sha is
    retried via the ``github.com/.../raw/`` mirror per §1. The download size is
    verified within ±1% of the pinned expected byte count.
    """
    dest = Path(cache_dir) / CACHE_FILENAME
    if dest.exists() and not force:
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = SOURCE_URL
    try:
        data = _http_get(url, DEFAULT_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise VnGeoError(f"fetch failed for {url}: HTTP {exc.code} {exc.reason}") from exc
        url = _SOURCE_FALLBACK_URL
        try:
            data = _http_get(url, DEFAULT_TIMEOUT)
        except (urllib.error.URLError, TimeoutError, OSError) as exc2:
            raise VnGeoError(f"fetch failed for {url}: {exc2}") from exc2
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VnGeoError(f"fetch failed for {url}: {exc}") from exc
    lo = EXPECTED_SOURCE_BYTES * (1 - SOURCE_SIZE_TOLERANCE)
    hi = EXPECTED_SOURCE_BYTES * (1 + SOURCE_SIZE_TOLERANCE)
    if not lo <= len(data) <= hi:
        raise VnGeoError(
            f"unexpected download size {len(data)} bytes (expected ~{EXPECTED_SOURCE_BYTES} ±1%); not caching"
        )
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return str(dest)


# --------------------------------------------------------------------------- feature parsing


def _first_prop(props: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = props.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _load_features(path) -> tuple[list[dict], str]:
    """Parse the GeoJSON file into normalized feature dicts + file sha256."""
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VnGeoError(f"invalid GeoJSON in {path}: {exc}") from exc
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise VnGeoError(f"expected a GeoJSON FeatureCollection with 'features' in {path}")
    out: list[dict] = []
    for i, feat in enumerate(features):
        if not isinstance(feat, dict):
            raise VnGeoError(f"feature {i}: not a JSON object")
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        if gtype not in ("Polygon", "MultiPolygon"):
            raise VnGeoError(f"feature {i}: unsupported geometry type {gtype!r}")
        raw_name = _first_prop(props, _NAME_KEYS)
        if not raw_name:
            raise VnGeoError(f"feature {i}: no province name property (tried {_NAME_KEYS})")
        src_code = _first_prop(props, _CODE_KEYS) or None
        fixed = _NAME_FIXES.get((src_code, raw_name))
        name = fixed or raw_name
        geometry = {"type": gtype, "coordinates": geom.get("coordinates")}
        if _geometry_bbox(geometry) is None:
            raise VnGeoError(f"feature {i} ({name}): geometry has no coordinates")
        out.append(
            {
                "code": _slug(name),
                "name": name,
                "geometry": geometry,
                "source_props_code": src_code,
                "name_raw": raw_name if fixed else None,
                "source_sha256": sha,
                "source_commit": SOURCE_COMMIT,
            }
        )
    seen: dict[str, int] = {}
    for feat in out:
        seen[feat["code"]] = seen.get(feat["code"], 0) + 1
    dupes = sorted(c for c, n in seen.items() if n > 1)
    if dupes:
        raise VnGeoError(f"duplicate province codes after normalization: {dupes}")
    return out, sha


# --------------------------------------------------------------------------- db helpers


def _open_store(db_path) -> SearchStore:
    """Open an existing db for reads (FileNotFoundError if missing)."""
    return SearchStore(db_path, create=False)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL)


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='boundary_geom'").fetchone()
    return row is not None


def _admin_code_map(conn: sqlite3.Connection) -> dict[str, object]:
    """Map folded province name -> admin_units code for ingested admin docs.

    Only ``provinces-api``/``admin`` province docs count; ``version == 2``
    records win over v1 for the same folded name.
    """
    try:
        rows = conn.execute(
            "SELECT meta FROM documents_current WHERE provider = 'provinces-api' AND format = 'admin'"
        ).fetchall()
    except sqlite3.Error:
        return {}
    best: dict[str, tuple[int, object]] = {}
    for row in rows:
        try:
            meta = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            continue
        if meta.get("level") != "province":
            continue
        key = _admin_key(meta.get("name") or "")
        if not key:
            continue
        prio = 0 if meta.get("version") == 2 else 1
        if key not in best or prio < best[key][0]:
            best[key] = (prio, meta.get("code"))
    return {k: v[1] for k, v in best.items()}


def _lookup_dict(entry: dict, admin_map: dict) -> dict:
    """Build the §1 lookup dict for one entry {code, name, geometry, source_*}."""
    geom = entry["geometry"]
    bbox = _geometry_bbox(geom)
    lat, lon = _representative_point(geom)
    ref_lat = (bbox[1] + bbox[3]) / 2.0
    return {
        "code": entry["code"],
        "name": entry["name"],
        "admin_code": admin_map.get(_admin_key(entry["name"])),
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
        "centroid": [lat, lon],
        "area_km2": round(_area_km2(geom, ref_lat), 3),
        "source_commit": entry.get("source_commit"),
        "source_sha256": entry.get("source_sha256"),
    }


def _row_entry(row: sqlite3.Row) -> dict:
    return {
        "code": row["code"],
        "name": row["name"],
        "geometry": json.loads(row["geom_json"]),
        "source_commit": row["source_commit"],
        "source_sha256": row["source_sha256"],
    }


# --------------------------------------------------------------------------- ingest


def ingest(db_path, geojson_path=None) -> dict:
    """Ingest province boundaries into *db_path*.

    *geojson_path* defaults to the cached pinned source (``fetch_source``).
    Per province: upsert into ``boundary_geom`` + a ``vn://boundary/province/
    {code}`` document (format ``'boundary'``). Counts classify geometry:
    ``new`` (no prior row), ``updated`` (row exists, geometry changed),
    ``unchanged`` (identical geometry). Returns
    ``{"provinces", "new", "updated", "unchanged"}``.
    """
    path = str(geojson_path) if geojson_path else fetch_source()
    features, sha = _load_features(path)
    counts = {"provinces": 0, "new": 0, "updated": 0, "unchanged": 0}
    now = _iso_now()
    with SearchStore(db_path) as store:
        conn = store.conn
        _ensure_table(conn)
        admin_map = _admin_code_map(conn)
        staged: list[tuple[dict, dict]] = []
        with conn:
            for feat in features:
                geom_json = json.dumps(feat["geometry"], ensure_ascii=False, separators=(",", ":"))
                row = conn.execute("SELECT geom_json FROM boundary_geom WHERE code = ?", (feat["code"],)).fetchone()
                if row is None:
                    counts["new"] += 1
                elif row["geom_json"] == geom_json:
                    counts["unchanged"] += 1
                else:
                    counts["updated"] += 1
                counts["provinces"] += 1
                conn.execute(
                    "INSERT OR REPLACE INTO boundary_geom"
                    "(code, level, name, geom_json, source, source_commit, source_sha256, fetched_at)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (feat["code"], "province", feat["name"], geom_json, SOURCE_URL, SOURCE_COMMIT, sha, now),
                )
                staged.append((feat, _lookup_dict(feat, admin_map)))
        for feat, meta in staged:
            doc_meta = dict(
                meta,
                source=SOURCE_URL,
                level="province",
                geom_in_table=True,
                source_props_code=feat["source_props_code"],
            )
            if feat.get("name_raw"):
                doc_meta["source_name_raw"] = feat["name_raw"]
            store.ingest_document(
                f"vn://boundary/province/{feat['code']}",
                feat["name"],
                title=feat["name"],
                provider=BOUNDARY_PROVIDER,
                fetched_at=now,
                format=BOUNDARY_FORMAT,
                meta=doc_meta,
            )
        store.record_event(
            "boundaries_ingested",
            {
                **counts,
                "source_commit": SOURCE_COMMIT,
                "source_sha256": sha,
                "geojson": path,
            },
        )
    return counts


# --------------------------------------------------------------------------- queries


def lookup(db_path, name_or_code) -> dict | None:
    """Resolve a province by slug code or (accent-flexible) name → §1 dict."""
    q = str(name_or_code or "").strip()
    if not q:
        return None
    with _open_store(db_path) as store:
        conn = store.conn
        if not _table_exists(conn):
            return None
        rows = conn.execute("SELECT * FROM boundary_geom ORDER BY code").fetchall()
        admin_map = _admin_code_map(conn)
    q_slug, q_key = _slug(q), _admin_key(q)
    best: dict | None = None
    best_rank = 99
    for row in rows:
        entry = _row_entry(row)
        name_key = _admin_key(entry["name"])
        if entry["code"] == q_slug or entry["code"] == q.casefold():
            rank = 0
        elif name_key == q_key:
            rank = 1
        elif q_key and (q_key in name_key or name_key in q_key):
            rank = 2
        else:
            continue
        if rank < best_rank:
            best, best_rank = entry, rank
    return _lookup_dict(best, admin_map) if best is not None else None


def list_provinces(db_path) -> list[dict]:
    """All provinces as §1 lookup dicts, sorted by code."""
    with _open_store(db_path) as store:
        conn = store.conn
        if not _table_exists(conn):
            return []
        rows = conn.execute("SELECT * FROM boundary_geom ORDER BY code").fetchall()
        admin_map = _admin_code_map(conn)
    return [_lookup_dict(_row_entry(r), admin_map) for r in rows]


def locate(db_path, lat, lon) -> dict | None:
    """Return ``{"code","name"}`` of the province containing (lat, lon).

    Bbox prefilter uses the boundary document meta (cheap) so only candidate
    geometries are parsed; falls back to all rows when no meta is available.
    """
    lat, lon = float(lat), float(lon)
    with _open_store(db_path) as store:
        conn = store.conn
        if not _table_exists(conn):
            return None
        candidates: list[str] = []
        for row in conn.execute("SELECT meta FROM documents_current WHERE format = ?", (BOUNDARY_FORMAT,)).fetchall():
            try:
                meta = json.loads(row["meta"] or "{}")
            except json.JSONDecodeError:
                continue
            code, bb = meta.get("code"), meta.get("bbox")
            if not code:
                continue
            if isinstance(bb, list) and len(bb) == 4 and not (bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3]):
                continue
            candidates.append(str(code))
        if not candidates:
            candidates = [r["code"] for r in conn.execute("SELECT code FROM boundary_geom").fetchall()]
        for code in sorted(set(candidates)):
            row = conn.execute("SELECT code, name, geom_json FROM boundary_geom WHERE code = ?", (code,)).fetchone()
            if row is None:
                continue
            if point_in_geometry(lat, lon, json.loads(row["geom_json"])):
                return {"code": row["code"], "name": row["name"]}
    return None


def province_bbox(db_path, code) -> tuple | None:
    """Return ``(west, south, east, north)`` for *code*, or None."""
    with _open_store(db_path) as store:
        conn = store.conn
        if not _table_exists(conn):
            return None
        row = conn.execute("SELECT geom_json FROM boundary_geom WHERE code = ?", (str(code),)).fetchone()
    if row is None:
        return None
    return _geometry_bbox(json.loads(row["geom_json"]))


def load_geometry(db_path, code) -> dict | None:
    """Return the raw GeoJSON geometry (Polygon | MultiPolygon) for *code*."""
    with _open_store(db_path) as store:
        conn = store.conn
        if not _table_exists(conn):
            return None
        row = conn.execute("SELECT geom_json FROM boundary_geom WHERE code = ?", (str(code),)).fetchone()
    return json.loads(row["geom_json"]) if row is not None else None


# --------------------------------------------------------------------------- CLI


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def _cmd_fetch(args: argparse.Namespace) -> tuple[dict, list[str]]:
    path = fetch_source(args.cache_dir, force=args.force)
    size = Path(path).stat().st_size
    return {"ok": True, "path": path, "bytes": size}, [f"cached {path} ({size} bytes)"]


def _cmd_ingest(args: argparse.Namespace) -> tuple[dict, list[str]]:
    counts = ingest(args.db, geojson_path=args.file)
    payload = {"ok": True, "db": args.db, **counts}
    human = [
        f"ingested {counts['provinces']} provinces into {args.db}: "
        f"{counts['new']} new, {counts['updated']} updated, {counts['unchanged']} unchanged"
    ]
    return payload, human


def _cmd_lookup(args: argparse.Namespace) -> tuple[dict, list[str]]:
    query = args.query or args.name or args.code
    if not query:
        raise VnGeoError("lookup needs a province name or code (positional, --name, or --code)")
    result = lookup(args.db, query)
    if result is None:
        return {"ok": True, "result": None}, [f"no province matches {query!r}"]
    human = [f"{result['name']} ({result['code']}) — bbox {result['bbox']} centroid {result['centroid']}"]
    return {"ok": True, "result": result}, human


def _cmd_locate(args: argparse.Namespace) -> tuple[dict, list[str]]:
    result = locate(args.db, args.lat, args.lon)
    if result is None:
        return {"ok": True, "result": None}, [f"no province contains ({args.lat}, {args.lon})"]
    return {"ok": True, "result": result}, [f"{result['name']} ({result['code']})"]


def _cmd_list(args: argparse.Namespace) -> tuple[dict, list[str]]:
    rows = list_provinces(args.db)
    human = [f"{r['code']:<18} {r['name']}" for r in rows] or ["no provinces ingested"]
    return {"ok": True, "count": len(rows), "provinces": rows}, human


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print one JSON object to stdout")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_geo.boundaries",
        description="Province boundary layer: fetch / ingest / lookup / locate / list (contract §1)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="fetch the pinned provinces GeoJSON into the cache dir")
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, metavar="DIR")
    p.add_argument("--force", action="store_true", help="re-download even if cached")
    _add_json(p)
    p.set_defaults(func=_cmd_fetch)

    p = sub.add_parser("ingest", help="ingest province boundaries into a SearchStore db")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--file", metavar="GEOJSON", help="GeoJSON file (default: cached pinned source)")
    _add_json(p)
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("lookup", help="look up one province by name or code")
    p.add_argument("query", nargs="?", help="province name or code (e.g. 'Hà Nội', 'ha-noi')")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--name", metavar="S", help="province name (alternative to positional)")
    p.add_argument("--code", metavar="S", help="province code (alternative to positional)")
    _add_json(p)
    p.set_defaults(func=_cmd_lookup)

    p = sub.add_parser("locate", help="find the province containing a point")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--lat", type=float, required=True, metavar="F")
    p.add_argument("--lon", type=float, required=True, metavar="F")
    _add_json(p)
    p.set_defaults(func=_cmd_locate)

    p = sub.add_parser("list", help="list all ingested provinces")
    p.add_argument("--db", required=True, metavar="PATH")
    _add_json(p)
    p.set_defaults(func=_cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return 0 ok · 1 runtime error · 2 usage/IO."""
    args = _build_parser().parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        payload, human = args.func(args)
    except (VnGeoError, SearchStoreError) as exc:
        _emit_error(exc, json_mode)
        return 1
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError, sqlite3.Error) as exc:
        _emit_error(exc, json_mode)
        return 2
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in human:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
