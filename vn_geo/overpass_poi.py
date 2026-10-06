"""Overpass POI fetch + SearchStore ingest (R4-B).

Frozen contract: ``analysis/r4-interfaces.md`` §4.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from vn_geo import VnGeoError

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

CATEGORIES: dict[str, list[str]] = {
    "food": ["restaurant", "cafe", "fast_food"],
    "shops": ["supermarket", "convenience_store", "bakery"],
    "services": ["bank", "atm", "pharmacy", "hospital"],
    "fuel": ["fuel"],
    "stay": ["hotel", "guest_house"],
    "education": ["school"],
}

USER_AGENT = "hermes-vn-geo/0.1"

# Reverse index: OSM tag value -> our category (first category wins).
_VALUE_TO_CATEGORY: dict[str, str] = {}
for _cat, _vals in CATEGORIES.items():
    for _val in _vals:
        _VALUE_TO_CATEGORY.setdefault(_val, _cat)

# Tag keys kept on the normalized record (spec §4).
_TAG_KEYS = (
    "phone",
    "website",
    "opening_hours",
    "cuisine",
    "addr:street",
    "addr:city",
    "addr:ward",
)


def build_query(south: float, west: float, north: float, east: float, categories: list[str]) -> str:
    """Build an Overpass QL query for the bbox and categories."""
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        raise VnGeoError(f"unknown POI categories: {', '.join(unknown)} (known: {', '.join(sorted(CATEGORIES))})")
    if not categories:
        raise VnGeoError("no POI categories given (e.g. --categories food,shops)")
    bbox = f"{south},{west},{north},{east}"
    blocks: list[str] = []
    for cat in categories:
        values = CATEGORIES[cat]
        alternation = "|".join(values)
        blocks.append(f'  nwr["amenity"~"^({alternation})$"]({bbox});')
        blocks.append(f'  nwr["shop"~"^({alternation})$"]({bbox});')
    body = "\n".join(blocks)
    return f"[out:json][timeout:60];(\n{body}\n);out center tags;"


def fetch(query: str, *, mirrors: list[str] | None = None, timeout: float = 60.0) -> dict:
    """POST the query to an Overpass mirror, falling back on failure."""
    targets = list(mirrors) if mirrors else list(MIRRORS)
    if not targets:
        raise VnGeoError("no Overpass mirrors to try (mirrors list is empty)")
    errors: list[str] = []
    for mirror in targets:
        try:
            body = urllib.parse.urlencode({"data": query}).encode("utf-8")
            request = urllib.request.Request(
                mirror,
                data=body,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - URL is a module-level https constant
                raw = response.read().decode("utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{mirror}: invalid JSON ({exc})")
                continue
            if not isinstance(payload, dict):
                errors.append(f"{mirror}: unexpected response (not a JSON object)")
                continue
            return payload
        except urllib.error.HTTPError as exc:
            errors.append(f"{mirror}: HTTP {exc.code} {exc.reason}")
        except urllib.error.URLError as exc:
            errors.append(f"{mirror}: URL error ({exc.reason})")
        except TimeoutError as exc:
            errors.append(f"{mirror}: timeout ({exc})")
        except OSError as exc:
            errors.append(f"{mirror}: network error ({exc})")
    raise VnGeoError(f"all Overpass mirrors failed: {'; '.join(errors)}")


def _element_category(tags: dict) -> str:
    for key in ("amenity", "shop", "tourism"):
        value = tags.get(key)
        if isinstance(value, str) and value in _VALUE_TO_CATEGORY:
            return _VALUE_TO_CATEGORY[value]
    for key in ("amenity", "shop", "tourism"):
        value = tags.get(key)
        if isinstance(value, str) and value:
            return value
    return "other"


def normalize(payload: dict) -> list[dict]:
    """Flatten an Overpass response into searchable POI records."""
    elements = payload.get("elements", [])
    if not isinstance(elements, list):
        raise VnGeoError("invalid Overpass payload: 'elements' is not a list")
    records: list[dict] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        osm_type = el.get("type")
        if osm_type not in ("node", "way", "relation"):
            continue
        osm_id = el.get("id")
        if not isinstance(osm_id, int):
            continue
        tags = el.get("tags", {})
        if not isinstance(tags, dict):
            tags = {}
        name = tags.get("name")
        if not isinstance(name, str) or not name.strip():
            continue  # not searchable without a name
        if osm_type == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            if not isinstance(center, dict):
                continue
            lat, lon = center.get("lat"), center.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        kept_tags = {k: tags[k] for k in _TAG_KEYS if tags.get(k)}
        records.append(
            {
                "osm_type": osm_type,
                "osm_id": osm_id,
                "lat": float(lat),
                "lon": float(lon),
                "name": name.strip(),
                "category": _element_category(tags),
                "tags": kept_tags,
            }
        )
    return records


def records_to_documents(records: list[dict]) -> list[dict]:
    """Map POI records to SearchStore ``ingest_document`` kwargs."""
    docs: list[dict] = []
    for rec in records:
        addr_parts = [
            rec.get("tags", {}).get(k) for k in ("addr:street", "addr:ward", "addr:city") if rec.get("tags", {}).get(k)
        ]
        text = f"{rec['name']} — {rec['category']}."
        if addr_parts:
            text += f" {', '.join(addr_parts)}."
        docs.append(
            {
                "url": f"https://www.openstreetmap.org/{rec['osm_type']}/{rec['osm_id']}",
                "text": text,
                "title": rec["name"],
                "provider": "osm",
                "format": "poi",
                "meta": rec,
            }
        )
    return docs


def ingest(store, records: list[dict]) -> dict:
    """Ingest POI records into a SearchStore; return counts."""
    by_category: dict[str, int] = {}
    for rec, doc in zip(records, records_to_documents(records), strict=True):
        store.ingest_document(
            doc["url"],
            doc["text"],
            title=doc["title"],
            provider=doc["provider"],
            format=doc["format"],
            meta=doc["meta"],
        )
        by_category[rec["category"]] = by_category.get(rec["category"], 0) + 1
    return {"documents": len(records), "by_category": by_category}


def save_jsonl(records: list[dict], path: str | Path) -> None:
    """Save records as JSONL (one JSON object per line, UTF-8)."""
    dest = Path(path)
    if dest.parent != Path(".") and str(dest.parent):
        dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_jsonl(path: str | Path) -> list[dict]:
    """Load records previously saved with :func:`save_jsonl`."""
    try:
        with Path(path).open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except FileNotFoundError:
        raise VnGeoError(f"file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise VnGeoError(f"invalid JSONL in {path}: {exc}") from None
    except OSError as exc:
        raise VnGeoError(f"cannot read {path}: {exc}") from None


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    try:
        parts = [float(p) for p in raw.split(",")]
    except ValueError:
        raise VnGeoError(f"invalid --bbox {raw!r}: expected S,W,N,E floats (e.g. 21.17,106.20,21.24,106.30)") from None
    if len(parts) != 4:
        raise VnGeoError(f"invalid --bbox {raw!r}: expected 4 numbers S,W,N,E (e.g. 21.17,106.20,21.24,106.30)")
    south, west, north, east = parts
    if not (south < north and west < east):
        raise VnGeoError(f"invalid --bbox {raw!r}: need south<north and west<east")
    return south, west, north, east


def _cmd_fetch(args: argparse.Namespace) -> int:
    south, west, north, east = _parse_bbox(args.bbox)
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    query = build_query(south, west, north, east, categories)
    payload = fetch(query, timeout=args.timeout)
    records = normalize(payload)
    save_jsonl(records, args.out)
    summary = {"records": len(records), "out": args.out}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"fetched {len(records)} POIs -> {args.out}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    from searchstore import SearchStore

    records = load_jsonl(args.file)
    store = SearchStore(args.db)
    try:
        summary = ingest(store, records)
    finally:
        store.close()
    summary = {"db": args.db, **summary}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"ingested {summary['documents']} POIs into {args.db} {summary['by_category']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vn_geo.overpass_poi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_p = sub.add_parser("fetch", help="fetch POIs from Overpass into a JSONL file")
    fetch_p.add_argument("--bbox", required=True, help="S,W,N,E (e.g. 21.17,106.20,21.24,106.30)")
    fetch_p.add_argument("--categories", required=True, help="comma-separated (e.g. food,shops)")
    fetch_p.add_argument("--out", required=True, help="output JSONL path")
    fetch_p.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds")
    fetch_p.add_argument("--json", action="store_true", help="print machine-readable summary")
    fetch_p.set_defaults(func=_cmd_fetch)
    ingest_p = sub.add_parser("ingest", help="ingest a POI JSONL file into a SearchStore db")
    ingest_p.add_argument("--db", required=True, help="SearchStore database path")
    ingest_p.add_argument("--file", required=True, help="input JSONL path")
    ingest_p.add_argument("--json", action="store_true", help="print machine-readable summary")
    ingest_p.set_defaults(func=_cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except VnGeoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
