"""vn_geo.business — VN business-entity pipeline core (R13-A).

Frozen contract: ``analysis/r13-interfaces.md`` §3::

    normalize(conn_input: dict, source: str) -> dict   # -> entity schema v1 (§2)
    geocode(entity: dict) -> dict                      # Goong -> Nominatim -> approximate
    upsert_entity(db_path: str, entity: dict) -> str   # dedupe §2; returns entity_id
    query_entities(db_path: str, text: str, area: str = "", category: str = "", limit: int = 20) -> list[dict]
    diff_events(db_path: str) -> list[dict]            # new/closed/changed since last run

Entities persist in a single-file SearchStore SQLite database as
``format="entity"`` documents (meta = the schema v1 dict), exposed through
the ``entities`` view — see ``searchstore.store`` entity helpers. FTS queries
always fold through ``searchstore.store.fold_d`` (R9-W2B hard rule: đ/Đ is
pre-folded into the index, so an unfolded accented query returns 0 hits).

Geocode chain order per the R13-A task card: (1) Goong when a GOONG_API_KEY
is configured (not yet active — the branch is coded and stubbed in tests),
(2) OSM Nominatim with the project User-Agent and >= 1.5 s politeness,
(3) no result -> ``geocode_status == "approximate"`` with null lat/lng.

No live connector calls live here — fetch belongs to R13-B/R13-E
(``vn_geo.connectors_*``); this module only normalizes connector inputs.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from searchstore import SearchStore, SearchStoreError
from searchstore import store as _store

from . import VnGeoError, categories, goong

USER_AGENT = "hermes-vn-geo/0.1"

# §2: allowed entity sources. Connectors must emit one of these.
ENTITY_SOURCES = frozenset({"gmaps", "masothue", "ckan_hp", "ckan_tn", "manual"})

# Nominatim usage policy: a descriptive UA plus >= 1 request / 1.5 s.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_MIN_INTERVAL_S = 1.5
NOMINATIM_TIMEOUT_S = 30.0
_last_nominatim_call = 0.0  # time.monotonic() of the last Nominatim request

_STATUS_CLOSED_HINTS = ("closed", "giai the", "ngung", "dissolved", "thanh ly", "dong cua", "tam ngung")
_STATUS_OPEN_HINTS = ("open", "operating", "operational", "hoat dong", "active", "dang ky")


# --------------------------------------------------------------------------- helpers


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _pick_str(record: dict, *keys: str) -> str | None:
    """First non-empty string among *keys* (connector field variants)."""
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        s = str(value).strip()
        if s:
            return s
    return None


def _pick_float(record: dict, *keys: str) -> float | None:
    for key in keys:
        value = record.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip().replace(",", "."))
        except (TypeError, ValueError):
            continue
    return None


def _pick_int(record: dict, *keys: str) -> int | None:
    for key in keys:
        value = record.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        digits = re.sub(r"[^\d]", "", str(value))
        if digits:
            try:
                return int(digits)
            except ValueError:
                continue
    return None


def _map_status(value: str | None) -> str:
    """Map raw status text to ``open|closed|unknown`` (§2). Closed wins when
    both hints appear (``ngừng hoạt động`` folds to ``ngung hoat dong``)."""
    s = categories.fold_text(value or "")
    if not s:
        return "unknown"
    if any(hint in s for hint in _STATUS_CLOSED_HINTS):
        return "closed"
    if any(hint in s for hint in _STATUS_OPEN_HINTS):
        return "open"
    return "unknown"


# --------------------------------------------------------------------------- normalize (contract §3)


def normalize(conn_input: dict, source: str) -> dict:
    """Map a connector input record to entity schema v1 (§2).

    Accepts the connector field variants defined in §3A of
    ``analysis/r13-gmaps-scraper-analysis.md`` (title/category/address/phone/
    website/latitude/longitude/place_id/cid/status) plus the masothue/CKAN
    shapes (name/address/tax_code/dien_thoai...). The original record is kept
    verbatim under ``raw``. Raises VnGeoError for non-dict input, an unknown
    *source*, or a missing name.
    """
    if not isinstance(conn_input, dict):
        raise VnGeoError(f"normalize: expected a dict record, got {type(conn_input).__name__}")
    source = str(source or "").strip()
    if source not in ENTITY_SOURCES:
        raise VnGeoError(f"normalize: unknown source {source!r}; expected one of {sorted(ENTITY_SOURCES)}")
    name = _pick_str(conn_input, "name", "title", "Name", "ten")
    if not name:
        raise VnGeoError("normalize: record is missing a name (expected 'name' or 'title')")

    category_raw = _pick_str(conn_input, "category_raw", "maps_category", "category", "type") or ""
    cat_id, cat_confidence = categories.classify(category_raw, name)
    lat = _pick_float(conn_input, "lat", "latitude", "Lat", "Latitude", "vi_do")
    lng = _pick_float(conn_input, "lng", "lon", "longitude", "Lon", "Lng", "Longitude", "kinh_do")
    source_id = _pick_str(conn_input, "source_id", "place_id", "cid") or ""
    address_text = _pick_str(conn_input, "address_text", "address", "addr", "dia_chi", "Dia chi", "Địa chỉ") or ""
    now = _iso_now()
    entity = {
        "entity_id": _store.entity_id_for(source, source_id, name, address_text),
        "name": name,
        "kind": categories.kind_of(cat_id),
        "category_raw": category_raw,
        "category": cat_id,
        "cat_confidence": cat_confidence,
        "tax_code": _pick_str(conn_input, "tax_code", "mst", "ma_so_thue") or "",
        "address_text": address_text,
        "area_old": _pick_str(conn_input, "area_old", "district", "ward", "area") or "",
        "province": _pick_str(conn_input, "province", "city", "tinh") or "",
        "lat": lat,
        "lng": lng,
        "phone": _pick_str(conn_input, "phone", "dien_thoai", "Dien thoai", "Điện thoại") or "",
        "website": _pick_str(conn_input, "website", "web") or "",
        "source": source,
        "source_url": _pick_str(conn_input, "source_url", "link", "detail_url") or "",
        "source_id": source_id,
        "status": _map_status(_pick_str(conn_input, "status", "trang_thai")),
        "rating": _pick_float(conn_input, "rating"),
        "review_count": _pick_int(conn_input, "review_count", "reviews"),
        "first_seen": _pick_str(conn_input, "first_seen") or now,
        "last_seen": _pick_str(conn_input, "last_seen") or now,
        "checked_at": _pick_str(conn_input, "checked_at", "scanned_at") or now,
        "ttl_class": _pick_str(conn_input, "ttl_class") or _store.ENTITY_DEFAULT_TTL_CLASS,
        # record-trust heuristic: verified coordinates raise confidence.
        "confidence": 0.9 if (lat is not None and lng is not None) else 0.5,
        "geocode_status": str(
            conn_input.get("geocode_status") or ("exact" if lat is not None and lng is not None else "approximate")
        ),
        "raw": conn_input,
    }
    if conn_input.get("geocode_source"):
        entity["geocode_source"] = str(conn_input["geocode_source"])
    elif lat is not None and lng is not None:
        entity["geocode_source"] = "input"
    if isinstance(conn_input.get("sources"), list):
        entity["sources"] = [str(s) for s in conn_input["sources"] if s]
    return entity


# --------------------------------------------------------------------------- geocode chain (task hard rules)


def _geocode_query(entity: dict) -> str:
    """Geocode input string: name + address + area + province."""
    parts = [entity.get("name"), entity.get("address_text"), entity.get("area_old"), entity.get("province")]
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _goong_client() -> goong.GoongClient | None:
    """A configured GoongClient, or None when no API key is resolved.

    The Goong key is not active yet (owner blocker, r13-candidate-plan §7):
    ``GoongClient()`` fails fast in ``resolve_api_key`` and the chain falls
    through to Nominatim.
    """
    try:
        return goong.GoongClient()
    except VnGeoError:
        return None


def _goong_geocode(query: str) -> tuple[float, float, str] | None:
    """Chain step 1: Goong ``/v2/geocode``. None when no key or no result."""
    client = _goong_client()
    if client is None:
        return None
    try:
        results = client.geocode(query)
    except VnGeoError:
        return None
    for result in results or []:
        loc = (result.get("geometry") or {}).get("location") or {}
        try:
            return float(loc["lat"]), float(loc["lng"]), "goong"
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _http_get_json(url: str):
    """GET *url* as JSON with the project User-Agent. VnGeoError on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=NOMINATIM_TIMEOUT_S) as resp:  # nosec B310 - URL is a module-level https constant
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise VnGeoError(f"geocode request failed: HTTP {e.code} {e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise VnGeoError(f"geocode request failed: {e}") from e
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise VnGeoError(f"geocoder returned invalid JSON: {e}") from e


def _nominatim_geocode(query: str) -> tuple[float, float, str] | None:
    """Chain step 2: OSM Nominatim, >= 1.5 s between calls, VN-biased."""
    global _last_nominatim_call
    wait = NOMINATIM_MIN_INTERVAL_S - (time.monotonic() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)
    params = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "vn"})
    try:
        payload = _http_get_json(f"{NOMINATIM_URL}?{params}")
    except VnGeoError:
        return None
    finally:
        _last_nominatim_call = time.monotonic()
    if isinstance(payload, list):
        for item in payload:
            try:
                return float(item["lat"]), float(item["lon"]), "nominatim"
            except (KeyError, TypeError, ValueError):
                continue
    return None


def geocode(entity: dict) -> dict:
    """Resolve lat/lng for an entity dict; returns a new dict.

    Already-geocoded entities (lat+lng present) pass through as ``exact``.
    Otherwise the chain runs Goong (when a key is configured) -> Nominatim ->
    ``geocode_status="approximate"`` with null coordinates. ``geocode_source``
    records which step produced the fix.
    """
    ent = dict(entity)
    if ent.get("lat") is not None and ent.get("lng") is not None:
        ent["geocode_status"] = "exact"
        ent.setdefault("geocode_source", "input")
        ent["confidence"] = max(float(ent.get("confidence") or 0.0), 0.9)
        return ent
    query = _geocode_query(ent)
    result = _goong_geocode(query) if query else None
    if result is None and query:
        result = _nominatim_geocode(query)
    if result is None:
        ent["lat"] = None
        ent["lng"] = None
        ent["geocode_status"] = "approximate"
        ent.pop("geocode_source", None)
        return ent
    lat, lng, geo_source = result
    ent["lat"] = lat
    ent["lng"] = lng
    ent["geocode_status"] = "exact"
    ent["geocode_source"] = geo_source
    ent["confidence"] = max(float(ent.get("confidence") or 0.0), 0.9)
    return ent


# --------------------------------------------------------------------------- store wrappers (contract §3)


def upsert_entity(db_path: str, entity: dict) -> str:
    """Dedupe-upsert *entity* into the SearchStore DB at *db_path*.

    Returns the (possibly pre-existing) entity_id. Idempotent: re-upserting
    an unchanged input adds zero document rows and zero diff events.
    """
    if not isinstance(entity, dict):
        raise VnGeoError(f"upsert_entity: expected a dict entity, got {type(entity).__name__}")
    ent = dict(entity)
    if "geocode_status" not in ent or not ent["geocode_status"]:
        ent["geocode_status"] = "exact" if ent.get("lat") is not None and ent.get("lng") is not None else "approximate"
    with SearchStore(db_path) as store:
        try:
            return _store.entity_upsert(store, ent)
        except SearchStoreError as e:
            raise VnGeoError(str(e)) from e


def query_entities(db_path: str, text: str, area: str = "", category: str = "", limit: int = 20) -> list[dict]:
    """Query entities: FTS on *text* (fold_d-folded) + *area*/*category*
    meta filters. Each result is the entity schema v1 dict plus a ``stale``
    freshness flag (§5 — stale records are flagged, never silently served).
    """
    with SearchStore(db_path) as store:
        try:
            return _store.entity_query(store.conn, text, area=area, category=category, limit=limit)
        except SearchStoreError as e:
            raise VnGeoError(str(e)) from e


def diff_events(db_path: str) -> list[dict]:
    """Entity diff events (new/closed/changed) since the previous call.

    Consumption watermark lives in the store's ``kv`` table, so the second of
    two identical upserts reports zero events.
    """
    with SearchStore(db_path) as store:
        return _store.entity_events(store.conn)
