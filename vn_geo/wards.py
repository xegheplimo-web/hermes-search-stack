"""vn_geo.wards — ward-level polygon layer pilot, R15-E (frozen: analysis/r15-interfaces.md §4).

Source: ``thanglequoc/vietnamese-provinces-database`` GIS add-on (ward-level
GeoJSON, one FeatureCollection file per ward) pinned at ``SOURCE_COMMIT``.
Pilot provinces only: Ha Noi (``01_ha_noi``, 126 wards, ~11.6 MB) + Hai Phong
(``31_hai_phong``, 114 wards, ~7.8 MB); total footprint ~20 MB (<= ~30 MB).

Pinned source
-------------
- repo: ``https://github.com/thanglequoc/vietnamese-provinces-database``
- commit SHA: ``8b78ba5118715e1fa81769286724db79346abf52`` (master @ 2026-09-22)
- raw URL pattern:
  ``https://raw.githubusercontent.com/thanglequoc/vietnamese-provinces-database/``
  ``<SHA>/json/geojson/<province_dir>/wards/<ward_file>.geojson``
- listing URL pattern (GitHub contents API, used by ``fetch_wards``):
  ``https://api.github.com/repos/thanglequoc/vietnamese-provinces-database/``
  ``contents/json/geojson/<province_dir>/wards?ref=<SHA>``
- on-disk layout mirrors upstream: ``<cache_dir>/<province_dir>/wards/*.geojson``

Each upstream ward file is a FeatureCollection with exactly one Feature whose
``id`` is the ward code and whose properties carry ``code``/``name``/
``fullName``/``codeName``/``areaKm2``. Geometry is GeoJSON ``[lon, lat]``
Polygon or MultiPolygon.

API
---
- ``fetch_wards(provinces, cache_dir=DEFAULT_WARDS_DIR, force=False)``
  downloads each pilot province's ward files (idempotent: existing files are
  skipped unless ``force``). Live network ONLY here — never in pytest.
- ``load_wards(cache_dir=DEFAULT_WARDS_DIR)`` parses cached files into
  ``{province_slug: [ward_feature, ...]}``; missing/empty cache -> ``{}``.
- ``ward_at(lat, lon, cache_dir=DEFAULT_WARDS_DIR)`` returns
  ``{"province", "ward_name", "ward_code", "unit_id"}`` for the ward containing
  the point (bbox pre-filter + ``boundaries.point_in_geometry``), or None
  outside coverage / when the cache is missing. Deterministic: provinces and
  wards are scanned in sorted order.

``unit_id`` is the upstream file stem (e.g. ``"00004_ba_dinh"``) — unique
within a province and traceable to the committed source file. ``ward_code``
is the administrative ward code (feature ``id`` / properties ``code``).

Import-only reuse of ``vn_geo.boundaries`` (that module is FROZEN): geometry
predicates and slug helpers. This module never touches ``data/vn-geo.db``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from . import VnGeoError, boundaries

SOURCE_COMMIT = "8b78ba5118715e1fa81769286724db79346abf52"
SOURCE_REPO = "thanglequoc/vietnamese-provinces-database"
SOURCE_DATE = "2026-09-22"
GEOJSON_ROOT = "json/geojson"
RAW_URL_PATTERN = (
    "https://raw.githubusercontent.com/thanglequoc/vietnamese-provinces-database/"
    "{commit}/json/geojson/{province_dir}/wards/{filename}"
)
LISTING_URL_PATTERN = (
    "https://api.github.com/repos/thanglequoc/vietnamese-provinces-database/"
    "contents/json/geojson/{province_dir}/wards?ref={commit}"
)
DEFAULT_WARDS_DIR = "data/boundaries/wards"
DEFAULT_TIMEOUT = 60.0
USER_AGENT = "hermes-vn-geo/0.1"

# Pilot scope: slug -> upstream province dir (see §4: Hai Phong + Ha Noi only).
PILOT_PROVINCES = {
    "ha-noi": "01_ha_noi",
    "hai-phong": "31_hai_phong",
}

# Property keys tried in order for the ward code / display name.
_CODE_KEYS = ("code", "id", "ward_code", "wardCode")
_NAME_KEYS = ("name", "fullName", "nameEn", "codeName")


def _resolve_province_dir(province: str) -> str:
    """Map a province name/slug/dir to its upstream dir; VnGeoError if outside the pilot."""
    q = str(province or "").strip()
    if not q:
        raise VnGeoError("fetch_wards needs a non-empty province name (pilot: Hai Phong, Ha Noi)")
    for known_dir in PILOT_PROVINCES.values():
        if q == known_dir or q.casefold() == known_dir.casefold():
            return known_dir
    slug = boundaries._slug(q)
    if slug in PILOT_PROVINCES:
        return PILOT_PROVINCES[slug]
    raise VnGeoError(
        f"province {province!r} is outside the R15-E pilot (supported: "
        f"{sorted(PILOT_PROVINCES)} / {sorted(PILOT_PROVINCES.values())})"
    )


def _province_slug(province_dir: str) -> str:
    """Upstream dir -> slug (``01_ha_noi`` -> ``ha-noi``); reverse of PILOT_PROVINCES."""
    for slug, known_dir in PILOT_PROVINCES.items():
        if known_dir == province_dir:
            return slug
    return boundaries._slug(province_dir.split("_", 1)[-1] if "_" in province_dir else province_dir)


def _http_get_bytes(url: str, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    """GET *url* and return raw bytes (the single monkeypatch seam for hermetic tests)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - pinned https hosts
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VnGeoError(f"fetch failed for {url}: {exc}") from exc


def _list_ward_files(province_dir: str) -> list[str]:
    """List upstream ward filenames for *province_dir* via the GitHub contents API."""
    url = LISTING_URL_PATTERN.format(commit=SOURCE_COMMIT, province_dir=province_dir)
    try:
        entries = json.loads(_http_get_bytes(url).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VnGeoError(f"invalid listing response for {province_dir}: {exc}") from exc
    if not isinstance(entries, list):
        raise VnGeoError(f"unexpected listing response for {province_dir}: {entries!r:.200}")
    names = sorted(e["name"] for e in entries if isinstance(e, dict) and str(e.get("name", "")).endswith(".geojson"))
    if not names:
        raise VnGeoError(f"no ward files listed for {province_dir} at commit {SOURCE_COMMIT}")
    return names


def fetch_wards(
    provinces: list[str],
    cache_dir: str | Path = DEFAULT_WARDS_DIR,
    force: bool = False,
) -> list[str]:
    """Download ward GeoJSON files for *provinces* into *cache_dir*; return saved paths.

    Idempotent: files already on disk are skipped unless ``force=True``.
    This is the ONLY function in this module that touches the network.
    """
    if not provinces:
        raise VnGeoError("fetch_wards needs at least one province (pilot: Hai Phong, Ha Noi)")
    base = Path(cache_dir)
    saved: list[str] = []
    for province in provinces:
        province_dir = _resolve_province_dir(province)
        dest_dir = base / province_dir / "wards"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for filename in _list_ward_files(province_dir):
            dest = dest_dir / filename
            if dest.exists() and not force:
                saved.append(str(dest))
                continue
            url = RAW_URL_PATTERN.format(commit=SOURCE_COMMIT, province_dir=province_dir, filename=filename)
            data = _http_get_bytes(url)
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VnGeoError(f"invalid GeoJSON for {province_dir}/{filename}: {exc}") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
                raise VnGeoError(f"unexpected GeoJSON shape for {province_dir}/{filename}: not a FeatureCollection")
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(dest)
            saved.append(str(dest))
    return sorted(saved)


def _first_prop(props: dict, keys: tuple[str, ...], fallback: str = "") -> str:
    for key in keys:
        value = props.get(key) if isinstance(props, dict) else None
        if value is not None and str(value).strip():
            return str(value).strip()
    return fallback


def _parse_ward_file(path: Path, province_dir: str, province_slug: str) -> dict:
    """Parse one cached ward file into a ward feature dict (raises VnGeoError if invalid)."""
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise VnGeoError(f"invalid ward GeoJSON in {path}: {exc}") from exc
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list) or len(features) != 1 or not isinstance(features[0], dict):
        raise VnGeoError(f"expected exactly one Feature in {path}")
    feat = features[0]
    props = feat.get("properties") or {}
    geom = feat.get("geometry") or {}
    if geom.get("type") not in ("Polygon", "MultiPolygon"):
        raise VnGeoError(f"unsupported geometry type {geom.get('type')!r} in {path}")
    geometry = {"type": geom["type"], "coordinates": geom.get("coordinates")}
    bbox = feat.get("bbox") if isinstance(feat.get("bbox"), list) and len(feat.get("bbox")) == 4 else None
    if bbox is None:
        bbox = boundaries._geometry_bbox(geometry)
    if bbox is None:
        raise VnGeoError(f"ward geometry has no coordinates in {path}")
    ward_code = _first_prop(props, _CODE_KEYS, fallback=str(feat.get("id") or "").strip())
    if not ward_code:
        raise VnGeoError(f"ward file has no code property or feature id in {path}")
    ward_name = _first_prop(props, _NAME_KEYS, fallback=ward_code)
    return {
        "province": province_slug,
        "province_dir": province_dir,
        "ward_name": ward_name,
        "ward_code": ward_code,
        "unit_id": path.stem,
        "geometry": geometry,
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
    }


def load_wards(cache_dir: str | Path = DEFAULT_WARDS_DIR) -> dict:
    """Load cached ward files into ``{province_slug: [ward_feature, ...]}``.

    Missing/empty cache -> ``{}`` (graceful). Each ward feature carries
    ``province/ward_name/ward_code/unit_id/geometry/bbox``. Deterministic:
    provinces and files are read in sorted order.
    """
    base = Path(cache_dir)
    if not base.is_dir():
        return {}
    out: dict[str, list[dict]] = {}
    for province_dir in sorted(p.name for p in base.iterdir() if p.is_dir()):
        wards_dir = base / province_dir / "wards"
        if not wards_dir.is_dir():
            continue
        slug = _province_slug(province_dir)
        feats: list[dict] = []
        for path in sorted(wards_dir.glob("*.geojson")):
            feats.append(_parse_ward_file(path, province_dir, slug))
        if feats:
            out[slug] = feats
    return out


def ward_at(lat: float, lon: float, cache_dir: str | Path = DEFAULT_WARDS_DIR) -> dict | None:
    """Return the ward containing (lat, lon) or None outside coverage / missing cache.

    Result: ``{"province", "ward_name", "ward_code", "unit_id"}``. Bbox
    pre-filter first, then ``boundaries.point_in_geometry`` per candidate.
    Deterministic scan order (sorted province, then sorted unit_id).
    """
    try:
        flat, flon = float(lat), float(lon)
    except (TypeError, ValueError) as exc:
        raise VnGeoError(f"ward_at needs numeric lat/lon, got ({lat!r}, {lon!r}): {exc}") from exc
    wards = load_wards(cache_dir)
    for slug in sorted(wards):
        for feat in sorted(wards[slug], key=lambda f: f["unit_id"]):
            bb = feat["bbox"]
            if not (bb[0] <= flon <= bb[2] and bb[1] <= flat <= bb[3]):
                continue
            if boundaries.point_in_geometry(flat, flon, feat["geometry"]):
                return {
                    "province": feat["province"],
                    "ward_name": feat["ward_name"],
                    "ward_code": feat["ward_code"],
                    "unit_id": feat["unit_id"],
                }
    return None
