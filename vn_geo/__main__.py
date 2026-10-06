"""vn_geo.__main__ — package CLI: business data-layer glue (R13-C).

Contract: analysis/r13-interfaces.md §4 (frozen CLI contract).

    python -m vn_geo business seed --config analysis/refresh-areas.json --db data/vn-geo.db
    python -m vn_geo business query "nha nghi" --area "Yen Dung" [--category lodging]
    python -m vn_geo business diff --db data/vn-geo.db
    python -m vn_geo business classify-rev --db data/vn-geo.db

The ``vn_geo.business`` pipeline module is owned by R13-A and is imported
lazily inside each handler, so ``--help`` works before R13-A lands. When the
module is absent the handler raises VnGeoError with guidance (exit 1, no
traceback) instead of stubbing the module here.

CLI conventions (exit codes, --json flag, _emit_error) mirror
vn_geo.places / vn_geo.refresh. No new dependencies.

Exits: 0 ok · 1 runtime error (incl. business module absent) · 2 usage/IO.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from . import VnGeoError

BUSINESS_GUIDANCE = (
    "vn_geo.business is not available yet — run r13-a first "
    "(it ships vn_geo/business.py + vn_geo/categories.py), then retry this command."
)


def _require_business():
    """Import the R13-A pipeline module; raise guidance error when absent."""
    try:
        from . import business as biz
    except ImportError as exc:
        raise VnGeoError(BUSINESS_GUIDANCE) from exc
    return biz


# --------------------------------------------------------------------------- handlers


def _cmd_business_seed(args: argparse.Namespace) -> tuple[dict, list[str]]:
    biz = _require_business()
    entry = getattr(biz, "seed_from_config", None) or getattr(biz, "seed", None)
    if not callable(entry):
        raise VnGeoError(f"{BUSINESS_GUIDANCE} (no seed entrypoint in vn_geo.business yet)")
    try:
        result = entry(args.config, args.db)
    except TypeError as exc:
        raise VnGeoError(f"vn_geo.business seed API mismatch — run r13-a first, then retry ({exc})") from exc
    payload = {"ok": True, "config": str(args.config), "db": str(args.db)}
    human = [f"seeded from {args.config} into {args.db}"]
    if isinstance(result, dict):
        payload.update(result)
        for key, value in result.items():
            human.append(f"  {key}: {value}")
    elif result is not None:
        human.append(f"  {result}")
    return payload, human


def _cmd_business_query(args: argparse.Namespace) -> tuple[dict, list[str]]:
    biz = _require_business()
    query_fn = getattr(biz, "query_entities", None)
    if not callable(query_fn):
        raise VnGeoError(f"{BUSINESS_GUIDANCE} (no query_entities in vn_geo.business yet)")
    try:
        results = query_fn(
            args.db, args.text or "", area=args.area or "", category=args.category or "", limit=args.limit
        )
    except TypeError as exc:
        raise VnGeoError(f"vn_geo.business query API mismatch — run r13-a first, then retry ({exc})") from exc
    results = list(results or [])
    payload = {"ok": True, "count": len(results), "results": results}
    human = [f"{len(results)} businesses"]
    for r in results:
        if not isinstance(r, dict):
            human.append(f"  {r}")
            continue
        name = r.get("name") or r.get("entity_id") or "?"
        cat = r.get("category") or r.get("kind") or "?"
        addr = r.get("address_text") or r.get("area_old") or ""
        line = f"  {name} [{cat}]"
        if addr:
            line += f" — {addr}"
        human.append(line)
    return payload, human


def _cmd_business_diff(args: argparse.Namespace) -> tuple[dict, list[str]]:
    biz = _require_business()
    diff_fn = getattr(biz, "diff_events", None)
    if not callable(diff_fn):
        raise VnGeoError(f"{BUSINESS_GUIDANCE} (no diff_events in vn_geo.business yet)")
    try:
        events = diff_fn(args.db)
    except TypeError as exc:
        raise VnGeoError(f"vn_geo.business diff API mismatch — run r13-a first, then retry ({exc})") from exc
    events = list(events or [])
    payload = {"ok": True, "count": len(events), "events": events}
    human = [f"{len(events)} events"]
    for e in events:
        if not isinstance(e, dict):
            human.append(f"  {e}")
            continue
        kind = e.get("kind") or e.get("event") or "event"
        name = e.get("name") or e.get("entity_id") or ""
        line = f"  {kind}: {name}" if name else f"  {kind}"
        human.append(line)
    return payload, human


def _cmd_business_classify_rev(args: argparse.Namespace) -> tuple[dict, list[str]]:
    biz = _require_business()
    entry = getattr(biz, "classify_rev", None) or getattr(biz, "reclassify", None)
    if not callable(entry):
        raise VnGeoError(f"{BUSINESS_GUIDANCE} (no classify_rev entrypoint in vn_geo.business yet)")
    try:
        result = entry(args.db)
    except TypeError as exc:
        raise VnGeoError(f"vn_geo.business classify-rev API mismatch — run r13-a first, then retry ({exc})") from exc
    payload = {"ok": True, "db": str(args.db)}
    human = [f"classify-rev on {args.db}"]
    if isinstance(result, dict):
        payload.update(result)
        for key, value in result.items():
            human.append(f"  {key}: {value}")
    elif result is not None:
        human.append(f"  {result}")
    return payload, human


# --------------------------------------------------------------------------- cli


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def _add_json(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="print one JSON object to stdout")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_geo",
        description="Vietnam geo/business data kit (R13-C CLI glue; contract analysis/r13-interfaces.md §4)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    biz = sub.add_parser("business", help="business-location data layer (seed/query/diff/classify-rev)")
    bsub = biz.add_subparsers(dest="business_command", required=True)

    p = bsub.add_parser("seed", help="seed business entities per the refresh-areas config")
    p.add_argument("--config", default="analysis/refresh-areas.json", metavar="FILE")
    p.add_argument("--db", default="data/vn-geo.db", metavar="PATH")
    _add_json(p)
    p.set_defaults(func=_cmd_business_seed)

    p = bsub.add_parser("query", help="query seeded business entities (local-first, FTS)")
    p.add_argument("text", nargs="?", default="", metavar="TEXT", help="FTS text query (accents optional)")
    p.add_argument("--area", default="", metavar="A", help="area filter, e.g. 'Yen Dung'")
    p.add_argument("--category", default="", metavar="C", help="canonical category filter, e.g. 'lodging'")
    p.add_argument("--db", default="data/vn-geo.db", metavar="PATH")
    p.add_argument("--limit", type=int, default=20)
    _add_json(p)
    p.set_defaults(func=_cmd_business_query)

    p = bsub.add_parser("diff", help="list new/closed/changed business events since last run")
    p.add_argument("--db", default="data/vn-geo.db", metavar="PATH")
    _add_json(p)
    p.set_defaults(func=_cmd_business_diff)

    p = bsub.add_parser("classify-rev", help="re-run canonical category classification over stored entities")
    p.add_argument("--db", default="data/vn-geo.db", metavar="PATH")
    _add_json(p)
    p.set_defaults(func=_cmd_business_classify_rev)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return 0 ok · 1 runtime error · 2 usage/IO."""
    args = _build_parser().parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        payload, human = args.func(args)
    except VnGeoError as exc:
        _emit_error(exc, json_mode)
        return 1
    except ImportError as exc:
        # Defensive: a lazy vn_geo.business import escaping as ImportError still
        # reports the clean R13-A guidance (exit 1), never a traceback.
        _emit_error(VnGeoError(f"{BUSINESS_GUIDANCE} ({exc})"), json_mode)
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
