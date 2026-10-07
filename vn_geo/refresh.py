"""vn_geo.refresh — coverage report + auto-backfill engine (R5-A).

Contract: ``analysis/r5-interfaces.md`` §3.

- ``load_config(path)`` reads and validates the areas config JSON
  (``{"areas": [{name, province?, overpass?{bbox,categories}, ckan?{datasets},
  places?{provider,path}}]}``).
- ``coverage(store, ...)`` reports which sources hold documents for each
  configured area — pure SearchStore reads, no network — plus the gap list and
  the places gap/staleness lists (R14-D).
- ``refresh(store, ...)`` fetches whatever is configured (admin units once
  globally, Overpass per area bbox, CKAN per area datasets, offline places via a
  ``PlaceProvider``), classifies every outgoing document as new/updated/unchanged
  via the (url_key, sha256) dedup pattern from ``places.save_places``, then
  ingests through each round-4 module's own ``ingest()`` — so re-running an
  unchanged refresh adds nothing. A failing source is recorded as
  ``{"error": ...}`` and the run continues.

CLI: ``python -m vn_geo.refresh <coverage|run> ...`` — exit 0 ok ·
1 runtime error (or any per-source error inside ``run``) · 2 usage/IO.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from searchstore import SearchStore, SearchStoreError, content_sha256, url_key
from searchstore.store import fold_d

from . import VnGeoError, admin_units, enterprises, overpass_poi, places, providers

# ``places`` (R14-D) is opt-in: a bare ``refresh`` runs admin+overpass+ckan as
# before, and adds the places step only when an area configures a ``places``
# block (see ``_default_sources``). ``--sources places`` is always accepted.
SOURCES = ("admin", "overpass", "ckan", "places")
ADMIN_VERSION = 2
EVENT_KIND = "refresh_run"
GLOBAL_AREA = "(global)"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _area_code(name: str) -> str:
    """Stable area slug: ``fold_d`` + strip diacritics (``'Yên Dũng'`` -> ``'yen-dung'``)."""
    folded = fold_d(str(name)).casefold()
    ascii_ = "".join(c for c in unicodedata.normalize("NFD", folded) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-") or "area"


def _age_days(iso: str) -> int:
    """Whole days between *iso* and now (unparseable -> 0)."""
    try:
        then = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return 0
    if then.tzinfo is None:
        then = then.astimezone()
    return (datetime.now().astimezone() - then).days


def _places_max_age_days(config: dict | None) -> float:
    """``places_max_age_days`` threshold (config key, default 90)."""
    if isinstance(config, dict):
        value = config.get("places_max_age_days")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 90.0


# --------------------------------------------------------------------------- config


def load_config(path) -> dict:
    """Read + validate a refresh config file; return ``{"areas": [...]}``.

    Missing/unreadable/malformed files raise OSError / JSONDecodeError (the
    CLI maps those to exit 2); structurally invalid configs raise VnGeoError
    naming the offending area/key.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _validate_config(data)


def _validate_config(data) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("areas"), list) or not data["areas"]:
        raise VnGeoError("refresh config must be a JSON object with a non-empty 'areas' list")
    for i, area in enumerate(data["areas"]):
        label = f"areas[{i}]"
        if not isinstance(area, dict):
            raise VnGeoError(f"config {label} must be an object, got {type(area).__name__}")
        name = area.get("name")
        if not isinstance(name, str) or not name.strip():
            raise VnGeoError(f"config {label} needs a non-empty 'name' string")
        label = f"area {name!r}"
        province = area.get("province")
        if province is not None and not isinstance(province, str):
            raise VnGeoError(f"config {label}: 'province' must be a string, got {province!r}")
        _validate_overpass_cfg(area.get("overpass"), label)
        _validate_ckan_cfg(area.get("ckan"), label)
        _validate_places_cfg(area.get("places"), label)
    return data


def _validate_overpass_cfg(cfg, label: str) -> None:
    if cfg is None:
        return
    if not isinstance(cfg, dict):
        raise VnGeoError(f"config {label}: 'overpass' must be an object with 'bbox' and 'categories'")
    bbox = cfg.get("bbox")
    if not (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox)
    ):
        raise VnGeoError(f"config {label}: overpass 'bbox' must be [south, west, north, east] (4 numbers)")
    categories = cfg.get("categories")
    if not isinstance(categories, list) or not categories:
        raise VnGeoError(f"config {label}: overpass 'categories' must be a non-empty list")
    unknown = [c for c in categories if c not in overpass_poi.CATEGORIES]
    if unknown:
        raise VnGeoError(
            f"config {label}: unknown overpass categories {unknown}; known: {sorted(overpass_poi.CATEGORIES)}"
        )


def _validate_ckan_cfg(cfg, label: str) -> None:
    if cfg is None:
        return
    if not isinstance(cfg, dict):
        raise VnGeoError(f"config {label}: 'ckan' must be an object with 'datasets'")
    datasets = cfg.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise VnGeoError(f"config {label}: ckan 'datasets' must be a non-empty list")
    unknown = [d for d in datasets if d not in enterprises.DATASETS]
    if unknown:
        raise VnGeoError(f"config {label}: unknown ckan datasets {unknown}; known: {sorted(enterprises.DATASETS)}")


def _validate_places_cfg(cfg, label: str) -> None:
    """Validate an area's optional ``places`` block (additive, R14-D)."""
    if cfg is None:
        return
    if not isinstance(cfg, dict):
        raise VnGeoError(f"config {label}: 'places' must be an object with 'provider' and 'path'")
    provider = cfg.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise VnGeoError(f"config {label}: places 'provider' must be a non-empty string")
    if provider not in providers.PROVIDERS:
        raise VnGeoError(f"config {label}: unknown places provider {provider!r}; known: {sorted(providers.PROVIDERS)}")
    path = cfg.get("path")
    if not isinstance(path, str) or not path.strip():
        raise VnGeoError(f"config {label}: places 'path' must be a non-empty string")


# --------------------------------------------------------------------------- coverage


def _stat(count: int, latest) -> dict:
    return {"count": count, "present": count > 0, "latest_fetched_at": latest}


def _latest(a, b):
    if b is None:
        return a
    return b if a is None else max(a, b)


def _admin_stats(store: SearchStore, term: str) -> dict:
    row = store.conn.execute(
        "SELECT COUNT(*) AS n, MAX(fetched_at) AS latest FROM documents_current"
        " WHERE provider = 'provinces-api' AND meta LIKE ?",
        (f"%{term}%",),
    ).fetchone()
    return _stat(row["n"], row["latest"])


def _ckan_stats(store: SearchStore, area: dict) -> dict:
    providers = sorted(
        {f"ckan-{enterprises.DATASETS[k]['province']}" for k in (area.get("ckan") or {}).get("datasets") or []}
    )
    count, latest = 0, None
    for provider in providers:
        row = store.conn.execute(
            "SELECT COUNT(*) AS n, MAX(fetched_at) AS latest FROM documents_current WHERE provider = ?",
            (provider,),
        ).fetchone()
        count += row["n"]
        latest = _latest(latest, row["latest"])
    return _stat(count, latest)


def _overpass_stats(store: SearchStore, area: dict) -> dict:
    rows = store.conn.execute("SELECT meta, fetched_at FROM documents_current WHERE provider = 'osm'").fetchall()
    bbox = (area.get("overpass") or {}).get("bbox")
    if bbox is None:
        stats = _stat(len(rows), max((r["fetched_at"] for r in rows), default=None))
        stats["scope"] = "global"  # no bbox configured -> every osm doc counts for this area
        return stats
    south, west, north, east = (float(v) for v in bbox)
    count, latest = 0, None
    for row in rows:
        try:
            meta = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            continue
        lat, lon = meta.get("lat"), meta.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if south <= float(lat) <= north and west <= float(lon) <= east:
            count += 1
            latest = _latest(latest, row["fetched_at"])
    return _stat(count, latest)


def _places_stats(store: SearchStore, name: str) -> dict:
    # Counts all four PLACE_SOURCES per contract; 'osm' overlaps the overpass
    # count by design (documented approximation, §3).
    count, latest = 0, None
    for provider in sorted(places.PLACE_SOURCES):
        row = store.conn.execute(
            "SELECT COUNT(*) AS n, MAX(fetched_at) AS latest FROM documents_current WHERE provider = ? AND meta LIKE ?",
            (provider, f"%{name}%"),
        ).fetchone()
        count += row["n"]
        latest = _latest(latest, row["latest"])
    return _stat(count, latest)


def coverage(store: SearchStore, *, config: dict | None = None, area: str | None = None) -> dict:
    """Report per-area per-source document coverage; pure store reads, no network.

    ``area`` checks a single name (no config needed, all filters fall back to
    the name); otherwise areas come from ``config["areas"]``; with neither the
    report has an empty ``areas`` list. ``gaps`` lists sources with no current
    documents. Counting is a documented approximation (see §3): admin/places
    use ``meta LIKE %<province-or-name>%``, ckan sums the configured datasets'
    ``ckan-<province>`` providers, overpass bbox-filters osm metas (or counts
    all osm docs with a ``"scope": "global"`` note when no bbox is set).

    Additive (R14-D): ``places_gap`` lists area codes with zero place docs and
    ``places_stale`` lists ``{"area", "last_scan", "age_days"}`` for areas whose
    latest place scan is older than ``places_max_age_days`` (config key, default
    90). Both use the same stable area code (``code`` field if present, else a
    ``fold_d`` slug of the name).
    """
    if area is not None:
        areas = [{"name": area}]
    elif config:
        areas = config.get("areas") or []
    else:
        areas = []
    threshold = _places_max_age_days(config)
    report = []
    places_gap: list[str] = []
    places_stale: list[dict] = []
    for a in areas:
        name = a["name"]
        sources = {
            "admin": _admin_stats(store, a.get("province") or name),
            "overpass": _overpass_stats(store, a),
            "ckan": _ckan_stats(store, a),
            "places": _places_stats(store, name),
        }
        report.append(
            {
                "name": name,
                "sources": sources,
                "gaps": [src for src, s in sources.items() if not s["present"]],
            }
        )
        code = a.get("code") or _area_code(name)
        places = sources["places"]
        if places["count"] == 0:
            places_gap.append(code)
        elif places["latest_fetched_at"]:
            age = _age_days(places["latest_fetched_at"])
            if age > threshold:
                places_stale.append({"area": code, "last_scan": places["latest_fetched_at"], "age_days": age})
    return {"checked_at": _now_iso(), "areas": report, "places_gap": places_gap, "places_stale": places_stale}


# --------------------------------------------------------------------------- refresh


def _dedup_counts(store: SearchStore, docs: list[dict]) -> dict:
    """Classify each doc as new/updated/unchanged — the §2 dedup pattern."""
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    for doc in docs:
        ukey, sha = url_key(doc["url"]), content_sha256(doc["text"])
        same = store.conn.execute(
            "SELECT 1 FROM documents WHERE url_key = ? AND content_sha256 = ?", (ukey, sha)
        ).fetchone()
        if same is not None:
            counts["unchanged"] += 1
        elif store.conn.execute("SELECT 1 FROM documents WHERE url_key = ? LIMIT 1", (ukey,)).fetchone():
            counts["updated"] += 1
        else:
            counts["new"] += 1
    return counts


def _default_sources(areas: list[dict]) -> list[str]:
    """Sources a bare ``refresh`` runs: admin+overpass+ckan, plus ``places`` when
    any area configures a ``places`` block (R14-D). Keeps the legacy default set
    — and therefore the run-event ``sources`` value — unchanged for old configs.
    """
    wanted = [s for s in SOURCES if s != "places"]
    if any(isinstance(a, dict) and a.get("places") for a in areas):
        wanted.append("places")
    return wanted


def _plan_steps(areas: list[dict], wanted: list[str]) -> list[dict]:
    """Execution order: one global admin step, then per-area overpass + ckan + places."""
    steps: list[dict] = []
    if "admin" in wanted:
        steps.append({"source": "admin", "area": areas[0]["name"] if areas else GLOBAL_AREA, "version": ADMIN_VERSION})
    for area in areas:
        name = area["name"]
        ov = area.get("overpass")
        if "overpass" in wanted and ov is not None:
            if not ov.get("bbox") or not ov.get("categories"):
                raise VnGeoError(f"area {name!r}: 'overpass' config needs 'bbox' and 'categories'")
            steps.append(
                {
                    "source": "overpass",
                    "area": name,
                    "bbox": list(ov["bbox"]),
                    "categories": list(ov["categories"]),
                }
            )
        ck = area.get("ckan")
        if "ckan" in wanted and ck is not None:
            if not ck.get("datasets"):
                raise VnGeoError(f"area {name!r}: 'ckan' config needs a non-empty 'datasets' list")
            steps.append({"source": "ckan", "area": name, "datasets": list(ck["datasets"])})
        pl = area.get("places")
        if "places" in wanted and pl is not None:
            if not pl.get("provider") or not pl.get("path"):
                raise VnGeoError(f"area {name!r}: 'places' config needs 'provider' and 'path'")
            steps.append({"source": "places", "area": name, "provider": pl["provider"], "path": pl["path"]})
    return steps


def _run_step(store: SearchStore, step: dict, before_fetch) -> dict:
    """Fetch -> records_to_documents -> dedup-count -> ingest. One step's counts."""
    source = step["source"]
    if source == "admin":
        version = step.get("version", ADMIN_VERSION)
        before_fetch()
        records = admin_units.normalize(admin_units.fetch_all(version), version)
        counts = _dedup_counts(store, admin_units.records_to_documents(records))
        admin_units.ingest(store, records)
        return counts
    if source == "overpass":
        south, west, north, east = step["bbox"]
        query = overpass_poi.build_query(south, west, north, east, step["categories"])
        before_fetch()
        records = overpass_poi.normalize(overpass_poi.fetch(query, attempts=2))
        counts = _dedup_counts(store, overpass_poi.records_to_documents(records))
        overpass_poi.ingest(store, records)
        return counts
    if source == "places":
        # Offline (R14-D): the provider reads a local JSONL file, so there is no
        # network call and no politeness sleep. save_places classifies every
        # record as new / updated / unchanged against the (url_key, sha256) dedup.
        provider = providers.get_provider(step.get("provider"))
        area_cfg = {"name": step["area"], "places": {"provider": step.get("provider"), "path": step.get("path")}}
        return places.save_places(store, provider.fetch(area_cfg))
    # ckan: one step covers all of the area's configured datasets
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    for key in step["datasets"]:
        ds = enterprises.DATASETS.get(key)
        if ds is None:
            raise VnGeoError(f"unknown ckan dataset {key!r}; known: {sorted(enterprises.DATASETS)}")
        before_fetch()
        raw = enterprises.fetch_dataset(key)
        normalized = enterprises.normalize(raw, province=ds["province"], kind=ds["kind"])
        docs = enterprises.records_to_documents(
            normalized, province=ds["province"], kind=ds["kind"], resource_id=ds["resource_id"]
        )
        for k, v in _dedup_counts(store, docs).items():
            counts[k] += v
        enterprises.ingest(store, normalized, dataset_key=key)
    return counts


def _places_summary(steps: list[dict], buckets: dict[str, dict]) -> dict:
    """Aggregate places-step results for the run-event ``"places"`` key (R14-D).

    ``records_raw`` = total records returned by the provider(s) = new+updated+
    unchanged (``places.save_places`` classifies every record into exactly one
    bucket). ``provider`` is the single provider name, a sorted list when several
    providers ran, or ``None`` when no places step was planned.
    """
    providers_seen: set[str] = set()
    summary: dict = {
        "provider": None,
        "records_raw": 0,
        "records_new": 0,
        "records_updated": 0,
        "records_unchanged": 0,
        "errors": [],
    }
    for step in steps:
        if step.get("source") != "places":
            continue
        if step.get("provider"):
            providers_seen.add(step["provider"])
        counts = buckets.get(step["area"], {}).get("sources", {}).get("places")
        if counts is None:
            continue
        if "error" in counts:
            summary["errors"].append({"area": step["area"], "error": counts["error"]})
            continue
        summary["records_new"] += counts["new"]
        summary["records_updated"] += counts["updated"]
        summary["records_unchanged"] += counts["unchanged"]
        summary["records_raw"] += counts["new"] + counts["updated"] + counts["unchanged"]
    names = sorted(providers_seen)
    summary["provider"] = names[0] if len(names) == 1 else (names or None)
    return summary


def refresh(
    store: SearchStore,
    *,
    config: dict,
    sources: list[str] | None = None,
    dry_run: bool = False,
    min_interval: float = 1.5,
    sleeper=None,
) -> dict:
    """Fetch configured VN data into *store* with per-source dedup counts.

    ``sources`` defaults to admin+overpass+ckan, plus ``places`` when an area
    configures a ``places`` block (R14-D; unknown -> VnGeoError).
    ``dry_run`` returns ``{"dry_run": True, "plan": [...]}`` with no network
    and no sleeping. Real runs sleep ``min_interval`` (via ``sleeper or
    time.sleep``) before every fetch except the first. A failing source is
    recorded as ``{"error": msg}`` and the run continues; totals carry the
    error count, and a ``refresh_run`` event is always recorded afterwards.
    """
    areas = config.get("areas") or []
    for i, a in enumerate(areas):
        if not isinstance(a, dict) or not str(a.get("name") or "").strip():
            raise VnGeoError(f"config areas[{i}] needs a non-empty 'name' (validate via load_config)")
    wanted = _default_sources(areas) if sources is None else list(sources)
    unknown = [s for s in wanted if s not in SOURCES]
    if unknown:
        raise VnGeoError(f"unknown refresh sources {unknown}; known: {list(SOURCES)}")
    steps = _plan_steps(areas, wanted)
    if dry_run:
        return {"dry_run": True, "plan": steps}

    sleep = time.sleep if sleeper is None else sleeper
    fetch_calls = 0

    def before_fetch() -> None:
        nonlocal fetch_calls
        if fetch_calls:
            sleep(min_interval)
        fetch_calls += 1

    buckets: dict[str, dict] = {a["name"]: {"name": a["name"], "sources": {}} for a in areas}
    totals = {"new": 0, "updated": 0, "unchanged": 0, "errors": 0}
    for step in steps:
        bucket = buckets.setdefault(step["area"], {"name": step["area"], "sources": {}})
        try:
            counts = _run_step(store, step, before_fetch)
        except Exception as exc:  # per-source error isolation: record + continue
            bucket["sources"][step["source"]] = {"error": str(exc)}
            totals["errors"] += 1
            continue
        bucket["sources"][step["source"]] = counts
        for k in ("new", "updated", "unchanged"):
            totals[k] += counts[k]
    store.record_event(
        EVENT_KIND,
        {
            "areas": [b["name"] for b in buckets.values()],
            "sources": wanted,
            "totals": totals,
            "places": _places_summary(steps, buckets),
        },
    )
    return {"dry_run": False, "areas": list(buckets.values()), "totals": totals}


# --------------------------------------------------------------------------- cli


def _open_store(db_path: str) -> SearchStore:
    if os.path.isdir(db_path):
        raise IsADirectoryError(f"db path is a directory: {db_path}")
    return SearchStore(db_path)


def _fmt_counts(c: dict) -> str:
    if "error" in c:
        return f"error: {c['error']}"
    return f"new {c['new']} · updated {c['updated']} · unchanged {c['unchanged']}"


def _cmd_coverage(args: argparse.Namespace) -> tuple[dict, list[str]]:
    config = load_config(args.config) if args.config else None
    with _open_store(args.db) as store:
        report = coverage(store, config=config, area=args.area)
    human: list[str] = []
    for a in report["areas"]:
        human.append(a["name"])
        for src, s in a["sources"].items():
            if s["present"]:
                note = f"{s['count']} docs · latest {s['latest_fetched_at']}"
            else:
                note = "missing"
            if s.get("scope"):
                note += f" [scope {s['scope']}]"
            human.append(f"  {src}: {note}")
        human.append(f"  gaps: {', '.join(a['gaps']) if a['gaps'] else 'none'}")
    if report["places_gap"]:
        human.append(f"places gap: {', '.join(report['places_gap'])}")
    for stale in report["places_stale"]:
        human.append(f"places stale: {stale['area']} (last {stale['last_scan']}, {stale['age_days']}d)")
    if not report["areas"]:
        human.append("no areas (pass --config or --area)")
    return {"ok": True, **report}, human


def _cmd_run(args: argparse.Namespace) -> tuple[dict, list[str]]:
    config = load_config(args.config)
    wanted = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    with _open_store(args.db) as store:
        result = refresh(store, config=config, sources=wanted, dry_run=args.dry_run, min_interval=args.min_interval)
    if result.get("dry_run"):
        human = ["plan (dry run):"]
        for step in result["plan"]:
            extra = " ".join(f"{k}={v}" for k, v in step.items() if k not in ("source", "area"))
            human.append(f"  {step['source']} -> {step['area']}" + (f" ({extra})" if extra else ""))
        return {"ok": True, **result}, human
    human = []
    for a in result["areas"]:
        human.append(a["name"])
        for src, c in a["sources"].items():
            human.append(f"  {src}: {_fmt_counts(c)}")
    t = result["totals"]
    human.append(f"totals: new {t['new']} · updated {t['updated']} · unchanged {t['unchanged']} · errors {t['errors']}")
    return {"ok": t["errors"] == 0, **result}, human


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_geo.refresh",
        description="VN data coverage report + auto-backfill (contract analysis/r5-interfaces.md §3)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("coverage", help="report per-area source coverage (no network)")
    p.add_argument("--db", required=True, metavar="PATH", help="SearchStore SQLite db path")
    p.add_argument("--config", metavar="FILE", help="areas config JSON (see load_config)")
    p.add_argument("--area", metavar="NAME", help="check a single area name; no config needed")
    p.add_argument("--json", action="store_true", help="print one JSON object to stdout")
    p.set_defaults(func=_cmd_coverage)

    p = sub.add_parser("run", help="fetch missing/stale data per the config (dedup-safe)")
    p.add_argument("--db", required=True, metavar="PATH", help="SearchStore SQLite db path")
    p.add_argument("--config", required=True, metavar="FILE", help="areas config JSON")
    p.add_argument(
        "--sources",
        default=None,
        metavar="LIST",
        help="comma-separated subset of admin,overpass,ckan,places (default: the configured sources)",
    )
    p.add_argument("--dry-run", action="store_true", help="print the fetch plan; no network")
    p.add_argument("--min-interval", type=float, default=1.5, metavar="SEC", help="min seconds between fetches")
    p.add_argument("--json", action="store_true", help="print one JSON object to stdout")
    p.set_defaults(func=_cmd_run)
    return parser


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return the process exit code (0 ok · 1 runtime · 2 usage/IO)."""
    args = _build_parser().parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        payload, human = args.func(args)
    except (VnGeoError, SearchStoreError) as exc:
        _emit_error(exc, json_mode)
        return 1
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        _emit_error(exc, json_mode)
        return 2
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in human:
            print(line)
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
