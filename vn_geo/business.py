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
from typing import Any

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


# ------------------------------------------------- seed orchestration glue (R13 integration — Hermes-owned)


def _load_area_config(path: str) -> dict:
    """Read a refresh-areas JSON config. Raises VnGeoError on bad input."""
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise VnGeoError(f"seed: cannot read config {path!r}: {e}") from e
    if not isinstance(cfg, dict):
        raise VnGeoError(f"seed: config {path!r} must be a JSON object")
    areas = cfg.get("areas")
    if not isinstance(areas, list) or not areas:
        raise VnGeoError(f"seed: config {path!r} has no 'areas' list")
    return cfg


def _connector_map() -> dict[str, Any]:
    """Resolve available connector modules, themselves reporting availability."""
    from . import connectors_ckan_ext, connectors_masothue

    modules: dict[str, Any] = {}
    for mod in (connectors_masothue, connectors_ckan_ext):
        available = getattr(mod, "AVAILABLE", True)
        if available is False:
            continue
        if callable(getattr(mod, "fetch", None)):
            modules[mod.__name__.rsplit(".", 1)[-1]] = mod
    return modules


def _seed_hosts_for(area_cfg: dict, connectors: dict[str, Any]) -> list[tuple[str, dict]]:
    """Choose (connector_name, sub-config) pairs for one area entry.

    Both connectors take the area name positionally (``fetch(area, …)``) and
    resolve their own per-source config (CKAN datasets / masothue index).
    """
    hosts: list[tuple[str, dict]] = []
    name = str(area_cfg.get("name") or "").strip()
    if not name:
        return hosts
    if "connectors_ckan_ext" in connectors and isinstance(area_cfg.get("ckan"), dict):
        hosts.append(("connectors_ckan_ext", {}))
    if "connectors_masothue" in connectors:
        hosts.append(("connectors_masothue", {}))
    return hosts


def seed_from_config(config_path: str, db_path: str, max_entities: int | None = None) -> dict:
    """Seed the entity store from a refresh-areas config (§4 CLI contract).

    Per area: run connector ``fetch()`` -> ``normalize()`` -> ``geocode()``
    (only for records WITHOUT coordinates) -> ``upsert_entity()``. Idempotent:
    re-running an unchanged config and source data adds 0 rows (dedupe §2).
    Returns a summary dict for the CLI payload.
    """
    cfg = _load_area_config(config_path)
    if cfg.get("business_enabled") is False:
        return {"ok": True, "seeded": 0, "skipped": "business_enabled=false", "areas": []}
    connectors = _connector_map()
    if not connectors:
        raise VnGeoError("seed: no connectors available (expected connectors_masothue / connectors_ckan_ext)")
    geocode_missing = bool(cfg.get("business_geocode_missing", True))
    totals: dict[str, Any] = {"areas": []}
    seeded = 0
    for area_cfg in cfg["areas"]:
        if not isinstance(area_cfg, dict) or not area_cfg.get("name"):
            continue
        area_name = str(area_cfg["name"])
        province = str(area_cfg.get("province") or "")
        area_summary = {"area": area_name, "fetched": 0, "seeded": 0, "skipped_sources": []}
        for conn_name, sub_cfg in _seed_hosts_for(area_cfg, connectors):
            module = connectors[conn_name]
            try:
                records = module.fetch(area_name, **sub_cfg)
            except Exception as e:  # noqa: BLE001 - per-area isolation, summarized below
                area_summary["skipped_sources"].append(f"{conn_name}: {type(e).__name__}: {e}")
                continue
            area_summary["fetched"] += len(records or [])
            for rec in records or []:
                if max_entities is not None and seeded >= max_entities:
                    break
                try:
                    entity = normalize(rec, conn_source(conn_name, province))
                except VnGeoError:
                    continue  # malformed record — the connector's raw payload, skipped
                if geocode_missing and entity.get("lat") is None and entity.get("lng") is None:
                    if area_name and not entity.get("area_old"):
                        entity["area_old"] = area_name
                    if province and not entity.get("province"):
                        entity["province"] = province
                    entity = geocode(entity)
                    time.sleep(1.5)  # Nominatim politeness (>=1.5 s between calls)
                upsert_entity(db_path, entity)
                seeded += 1
                area_summary["seeded"] += 1
            if max_entities is not None and seeded >= max_entities:
                break
        totals["areas"].append(area_summary)
        if max_entities is not None and seeded >= max_entities:
            break
    totals["ok"] = True
    totals["seeded"] = seeded
    return totals


def conn_source(conn_name: str, province: str = "") -> str:
    """Map a connector module name to its entity-schema ``source`` id.

    ``ENTITY_SOURCES`` has no plain ``ckan``: CKAN entities carry the
    province-specific ids the §2 schema froze (``ckan_hp``, ``ckan_tn``).
    ``province`` disambiguates when the connector covers several provinces.
    """
    if conn_name == "connectors_masothue":
        return "masothue"
    if conn_name == "connectors_ckan_ext":
        return "ckan_hp" if "hải phòng" in (province or "").lower() else "ckan_tn"
    return conn_name


# ------------------------------------------------- classify-rev glue (R13 integration — Hermes-owned)


def classify_rev(db_path: str) -> dict:
    """Re-run ``categories.classify`` over stored entities (§4 CLI contract).

    Recomputes ``(category, kind, cat_confidence)`` from each entity's
    ``category_raw`` + ``name`` and re-upserts ONLY changed entities — the
    store's versioned write path records an ``entity_changed`` event, so
    ``diff_events`` sees reclassifications. Idempotent: an unchanged second
    run reports ``changed=0`` and writes nothing. Returns
    ``{"checked": <n>, "changed": <m>}``.
    """
    checked = 0
    changed = 0
    try:
        with SearchStore(db_path) as store:
            conn = store.conn
            _store.ensure_entity_schema(conn)
            docs = list(_store._iter_entity_docs(conn))
            for _doc_id, meta in docs:
                checked += 1
                cat_id, cat_confidence = categories.classify(
                    str(meta.get("category_raw") or ""), str(meta.get("name") or "")
                )
                kind = categories.kind_of(cat_id)
                if (
                    cat_id == meta.get("category")
                    and kind == meta.get("kind")
                    and cat_confidence == meta.get("cat_confidence")
                ):
                    continue
                ent = dict(meta)
                ent["category"] = cat_id
                ent["kind"] = kind
                ent["cat_confidence"] = cat_confidence
                _store.entity_upsert(store, ent)
                changed += 1
    except SearchStoreError as e:
        raise VnGeoError(str(e)) from e
    return {"checked": checked, "changed": changed}
