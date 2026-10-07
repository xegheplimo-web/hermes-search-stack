"""vn_geo.connectors_ckan_ext — provincial CKAN enterprise lists as R13 connectors.

Extends the HP / Tây Ninh CKAN pattern (the ``vn_geo.enterprises`` Action-API
client) to every area that carries a ``ckan`` block in the refresh-areas config
(``analysis/refresh-areas.json`` — also accepted from ``data/refresh-areas.json``).

Contract: ``analysis/r13-interfaces.md`` §3 —

    def fetch(area: str, politeness_s: float = 2.0) -> list[dict]

Each returned dict matches the R13 entity schema v1 (interfaces §2):
``name`` / ``address_text`` are ``fold_d``-folded (đ/Đ→d) and the full source
row is preserved under ``raw``. ``source`` is ``ckan_hp`` / ``ckan_tn`` (per the
interfaces source enum). The HTTP client itself is reused verbatim from
:mod:`vn_geo.enterprises` (``fetch_dataset`` / ``normalize`` / ``DATASETS``).
"""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from pathlib import Path

from searchstore.store import fold_d

from . import VnGeoError, enterprises

TTL_CLASS = "poi"
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATHS = (
    _REPO_ROOT / "analysis" / "refresh-areas.json",
    _REPO_ROOT / "data" / "refresh-areas.json",
)


# --------------------------------------------------------------------------- helpers


def _fold_text(value) -> str:
    s = fold_d(str(value or ""))
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return " ".join(s.split()).lower()


def _iso_now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _entity_id(source: str, source_id: str) -> str:
    digest = hashlib.sha1(f"{source}|{source_id}".encode(), usedforsecurity=False).hexdigest()
    return f"e_{digest[:12]}"


def _source_for(province: str) -> str:
    p = _fold_text(province)
    if p == "haiphong":
        return "ckan_hp"
    if p == "tayninh":
        return "ckan_tn"
    return f"ckan_{p}"


def _resource_url(dataset_key: str) -> str:
    ds = enterprises.DATASETS[dataset_key]
    base = ds["portal"].rstrip("/")
    return f"{base}/api/3/action/datastore_search?resource_id={ds['resource_id']}"


def _looks_normalized(records: list) -> bool:
    return bool(records) and all(isinstance(r, dict) and isinstance(r.get("raw"), dict) for r in records)


class _Pacer:
    """Sleeps ``politeness_s`` before every fetch after the first (injectable)."""

    def __init__(self, sleeper, politeness_s: float):
        self._sleeper = sleeper
        self._politeness = politeness_s
        self.calls = 0

    def tick(self) -> None:
        if self.calls:
            self._sleeper(self._politeness)
        self.calls += 1


# --------------------------------------------------------------------------- config


def _resolve_config_path(config_path=None) -> Path:
    if config_path is not None:
        return Path(config_path)
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            return path
    raise VnGeoError(f"refresh-areas config not found at {[str(p) for p in DEFAULT_CONFIG_PATHS]}")


def load_areas(config_path=None) -> list[dict]:
    """Read the refresh-areas config and return its ``areas`` list."""
    path = _resolve_config_path(config_path)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise VnGeoError(f"config not found: {path}") from e
    except (ValueError, OSError) as e:
        raise VnGeoError(f"cannot read {path}: {e}") from e
    areas = data.get("areas") if isinstance(data, dict) else None
    if not isinstance(areas, list):
        raise VnGeoError(f"{path}: expected a JSON object with an 'areas' list")
    return areas


def resolve_datasets(area: str, areas: list[dict]) -> tuple[dict, list[str]]:
    """Return ``(area_config, dataset_keys)`` for the CKAN-enabled *area*."""
    folded = _fold_text(area)
    for cfg in areas:
        if not isinstance(cfg, dict) or _fold_text(cfg.get("name")) != folded:
            continue
        datasets = (cfg.get("ckan") or {}).get("datasets") or []
        if not datasets:
            raise VnGeoError(f"area {area!r} has no ckan datasets configured")
        unknown = [d for d in datasets if d not in enterprises.DATASETS]
        if unknown:
            raise VnGeoError(f"area {area!r}: unknown ckan datasets {unknown}; known: {sorted(enterprises.DATASETS)}")
        return cfg, list(datasets)
    known = [cfg.get("name") for cfg in areas if isinstance(cfg, dict)]
    raise VnGeoError(f"no area named {area!r} with a ckan block; configured areas: {known}")


# --------------------------------------------------------------------------- shaping


def to_entity(record: dict, *, dataset_key: str, source: str, province: str, area: str = "") -> dict:
    """Shape a normalized CKAN enterprise record into entity-schema-v1 (interfaces §2)."""
    name = record.get("name") or ""
    address = record.get("address") or ""
    code = str(record.get("code") or "").strip()
    # Some CKAN rows carry neither code nor name (Hai Phong has 130 such rows):
    # fall back to the datastore's unique ``_id`` so distinct rows are not
    # collapsed by the (source, source_id) dedupe.
    row_id = (record.get("raw") or {}).get("_id")
    source_id = (
        code
        or (f"row-{row_id}" if row_id is not None else "")
        or _fold_text(name)
        or str(record.get("source_id") or "")
    )
    now = _iso_now()
    return {
        "entity_id": _entity_id(source, source_id),
        "name": fold_d(name),
        "kind": "other",
        "category_raw": "",
        "category": "other",
        "cat_confidence": 0.0,
        "tax_code": code,
        "address_text": fold_d(address),
        "area_old": area,
        "province": province,
        "lat": record.get("lat"),
        "lng": record.get("lon"),
        "phone": record.get("phone") or "",
        "website": "",
        "source": source,
        "source_url": _resource_url(dataset_key),
        "source_id": source_id,
        "status": "unknown",
        "rating": None,
        "review_count": None,
        "first_seen": now,
        "last_seen": now,
        "checked_at": now,
        "ttl_class": TTL_CLASS,
        "confidence": 0.7,
        "raw": dict(record),
    }


# --------------------------------------------------------------------------- fetch


def fetch(
    area: str,
    politeness_s: float = 2.0,
    *,
    config_path=None,
    areas: list[dict] | None = None,
    fetch_fn=None,
    sleeper=None,
) -> list[dict]:
    """Fetch the CKAN datasets configured for *area*; return §1-shaped dicts.

    ``fetch_fn`` defaults to :func:`vn_geo.enterprises.fetch_dataset` and is
    injectable for hermetic tests. Every dataset fetch after the first sleeps
    ``politeness_s`` seconds (default 2.0). Records are deduped by
    ``(source, source_id)``.
    """
    if politeness_s < 0:
        raise VnGeoError(f"politeness_s must be >= 0, got {politeness_s!r}")
    area_cfg, dataset_keys = resolve_datasets(area, areas if areas is not None else load_areas(config_path))
    do_fetch = fetch_fn or enterprises.fetch_dataset
    pacer = _Pacer(sleeper or time.sleep, politeness_s)
    province = area_cfg.get("province") or area
    seen: dict[tuple[str, str], dict] = {}
    for key in dataset_keys:
        ds = enterprises.DATASETS[key]
        pacer.tick()
        raw = do_fetch(key)
        records = (
            raw if _looks_normalized(raw) else enterprises.normalize(raw, province=ds["province"], kind=ds["kind"])
        )
        source = _source_for(ds["province"])
        for rec in records:
            entity = to_entity(rec, dataset_key=key, source=source, province=province, area=area)
            seen.setdefault((entity["source"], entity["source_id"]), entity)
    return list(seen.values())
