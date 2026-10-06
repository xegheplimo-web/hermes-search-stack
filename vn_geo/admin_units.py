"""VN admin units (provinces.open-api.vn) — fetch / normalize / ingest / lookup.

Contract: analysis/r4-interfaces.md section 3 (R4-A).

- ``fetch_all(version)`` GETs ``{V1_API|V2_API}?depth=2`` and returns the raw
  JSON list (v2 = post-2025-merger 34 provinces with nested wards; v1 = legacy
  3-level 63 provinces with nested districts + wards).
- ``normalize(payload, version)`` flattens the payload into province / district /
  ward records (districts only for v1).
- ``records_to_documents(records)`` maps records to ``ingest_document`` kwargs.
- ``ingest(store, records)`` persists documents; ``lookup(store, query)``
  FTS-searches them (``provinces-api`` provider only).

CLI: ``python -m vn_geo.admin_units <fetch|ingest|lookup>`` — exit 0 ok ·
1 runtime error · 2 usage/IO. ``fetch`` writes normalized records (JSON array);
``ingest`` reads the version from those records (and also tolerates a raw API
payload, auto-detecting v1 vs v2 by the presence of ``districts``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from searchstore import SearchStore, SearchStoreError

from . import VnGeoError

V1_API = "https://provinces.open-api.vn/api/v1/"
V2_API = "https://provinces.open-api.vn/api/v2/"

USER_AGENT = "hermes-vn-geo/0.1"
DEFAULT_TIMEOUT = 30.0


# --------------------------------------------------------------------------- fetch


def fetch_all(version: int = 2, *, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """GET ``{API}?depth=2`` and return the parsed JSON list.

    Raises VnGeoError on HTTP/timeout/JSON failure or a non-list payload.
    """
    if version not in (1, 2):
        raise VnGeoError(f"unsupported API version {version!r}; expected 1 or 2")
    api = V1_API if version == 1 else V2_API
    url = f"{api}?depth=2"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - URL is a module-level https constant
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VnGeoError(f"fetch failed for {url}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VnGeoError(f"invalid JSON from {url}: {exc}") from exc
    if not isinstance(data, list):
        raise VnGeoError(f"expected a JSON array from {url}, got {type(data).__name__}")
    return data


# --------------------------------------------------------------------------- normalize


def _as_int(value: object, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise VnGeoError(f"{field} must be an int, got {value!r}")
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise VnGeoError(f"{field} is not a valid int: {value!r}") from exc


def _as_str(value: object, field: str, *, required: bool = False) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise VnGeoError(f"{field} is missing or empty")
    return text


def normalize(payload: list[dict], version: int) -> list[dict]:
    """Flatten an API payload into province / district / ward records.

    v2: province + ward (districts abolished). v1: province + district + ward.
    Ward records carry ``province_code`` / ``province_name`` (and, for v1,
    ``district_code`` / ``district_name``).
    """
    if version not in (1, 2):
        raise VnGeoError(f"unsupported API version {version!r}; expected 1 or 2")
    records: list[dict] = []
    for prov in payload:
        if not isinstance(prov, dict):
            raise VnGeoError(f"province entry is not an object: {prov!r}")
        p_code = _as_int(prov.get("code"), "province.code")
        p_name = _as_str(prov.get("name"), "province.name", required=True)
        p_dt = _as_str(prov.get("division_type"), "province.division_type")
        p_cn = _as_str(prov.get("codename"), "province.codename")
        records.append(
            {
                "level": "province",
                "code": p_code,
                "name": p_name,
                "division_type": p_dt,
                "codename": p_cn,
                "version": version,
            }
        )
        if version == 2:
            for ward in prov.get("wards") or []:
                if not isinstance(ward, dict):
                    raise VnGeoError(f"ward entry is not an object: {ward!r}")
                records.append(
                    {
                        "level": "ward",
                        "code": _as_int(ward.get("code"), "ward.code"),
                        "name": _as_str(ward.get("name"), "ward.name", required=True),
                        "division_type": _as_str(ward.get("division_type"), "ward.division_type"),
                        "codename": _as_str(ward.get("codename"), "ward.codename"),
                        "province_code": p_code,
                        "province_name": p_name,
                        "version": version,
                    }
                )
        else:
            for dist in prov.get("districts") or []:
                if not isinstance(dist, dict):
                    raise VnGeoError(f"district entry is not an object: {dist!r}")
                d_code = _as_int(dist.get("code"), "district.code")
                d_name = _as_str(dist.get("name"), "district.name", required=True)
                records.append(
                    {
                        "level": "district",
                        "code": d_code,
                        "name": d_name,
                        "division_type": _as_str(dist.get("division_type"), "district.division_type"),
                        "codename": _as_str(dist.get("codename"), "district.codename"),
                        "province_code": p_code,
                        "province_name": p_name,
                        "version": version,
                    }
                )
                for ward in dist.get("wards") or []:
                    if not isinstance(ward, dict):
                        raise VnGeoError(f"ward entry is not an object: {ward!r}")
                    records.append(
                        {
                            "level": "ward",
                            "code": _as_int(ward.get("code"), "ward.code"),
                            "name": _as_str(ward.get("name"), "ward.name", required=True),
                            "division_type": _as_str(ward.get("division_type"), "ward.division_type"),
                            "codename": _as_str(ward.get("codename"), "ward.codename"),
                            "district_code": d_code,
                            "district_name": d_name,
                            "province_code": p_code,
                            "province_name": p_name,
                            "version": version,
                        }
                    )
    return records


# --------------------------------------------------------------------------- documents


def records_to_documents(records: list[dict]) -> list[dict]:
    """Map records to ``ingest_document`` kwargs (url/text/title/provider/format/meta)."""
    docs: list[dict] = []
    for r in records:
        level = r["level"]
        version = r["version"]
        code = r["code"]
        name = r["name"]
        text = f"{name} — {r['division_type']}, Việt Nam. Mã {code}."
        if level == "ward":
            text += f" Thuộc {r['province_name']}."
        if r.get("codename"):
            text += f" Codename: {r['codename']}."
        docs.append(
            {
                "url": f"vn://provinces-api/v{version}/{level}/{code}",
                "text": text,
                "title": name,
                "provider": "provinces-api",
                "format": "admin",
                "meta": r,
            }
        )
    return docs


# --------------------------------------------------------------------------- ingest / lookup


def ingest(store: SearchStore, records: list[dict]) -> dict:
    """Ingest all records into *store*; return per-level counts."""
    docs = records_to_documents(records)
    for doc in docs:
        store.ingest_document(
            doc["url"],
            doc["text"],
            title=doc["title"],
            provider=doc["provider"],
            format=doc["format"],
            meta=doc["meta"],
        )
    counts = {"documents": len(records), "provinces": 0, "wards": 0, "districts": 0}
    level_key = {"province": "provinces", "district": "districts", "ward": "wards"}
    for r in records:
        key = level_key.get(r["level"])
        if key is not None:
            counts[key] += 1
    return counts


def _fts_query(query: str) -> str:
    """Quote each whitespace-separated token so arbitrary user input is a safe FTS5 query."""
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def lookup(store: SearchStore, query: str, *, limit: int = 20) -> list[dict]:
    """FTS-search *store* for admin units; keep only ``provinces-api`` documents."""
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    if not tokens:
        return []
    hits = store.search(_fts_query(query), limit=limit)
    out: list[dict] = []
    for hit in hits:
        if hit.get("provider") != "provinces-api":
            continue
        doc = store.get_document(hit["doc_id"])
        if doc is None:
            continue
        try:
            meta = json.loads(doc["meta"])
        except (json.JSONDecodeError, TypeError):
            meta = {}
        out.append(
            {
                "name": hit.get("title"),
                "level": meta.get("level"),
                "code": meta.get("code"),
                "province_name": meta.get("province_name"),
                "url": hit.get("url"),
                "snippet": hit.get("snippet"),
            }
        )
    return out


# --------------------------------------------------------------------------- CLI


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print one JSON object to stdout")


def _open_store(db_path: str) -> SearchStore:
    if os.path.isdir(db_path):
        raise IsADirectoryError(f"db path is a directory: {db_path}")
    return SearchStore(db_path)


def _load_records(path: str) -> list[dict]:
    """Load a records JSON file (as written by ``fetch``); tolerate a raw API payload."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise VnGeoError(f"expected a JSON array in {path}, got {type(data).__name__}")
    if not data:
        raise VnGeoError(f"no records found in {path}")
    if all(isinstance(x, dict) and "level" in x and "version" in x for x in data):
        return data
    version = 1 if any(isinstance(x, dict) and "districts" in x for x in data) else 2
    return normalize(data, version)


def _cmd_fetch(args: argparse.Namespace) -> tuple[dict, list[str]]:
    payload = fetch_all(args.version, timeout=args.timeout)
    records = normalize(payload, args.version)
    path = Path(args.out)
    if str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_level: dict[str, int] = {}
    for r in records:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
    summary = ", ".join(f"{n} {lvl}" for lvl, n in sorted(by_level.items()))
    return (
        {"ok": True, "version": args.version, "records": len(records), "by_level": by_level, "out": str(path)},
        [f"fetched {len(records)} records (v{args.version}: {summary}) -> {path}"],
    )


def _cmd_ingest(args: argparse.Namespace) -> tuple[dict, list[str]]:
    records = _load_records(args.file)
    version = records[0]["version"]
    with _open_store(args.db) as store:
        result = ingest(store, records)
    return (
        {"ok": True, "version": version, **result},
        [f"ingested {result['documents']} documents (v{version}) into {args.db}"],
    )


def _cmd_lookup(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with _open_store(args.db) as store:
        hits = lookup(store, args.query, limit=args.limit)
    if hits:
        human = [f"{h['name']} ({h['level']}, {h.get('province_name') or '-'})" for h in hits]
    else:
        human = [f"no results for {args.query!r}"]
    return {"ok": True, "query": args.query, "count": len(hits), "results": hits}, human


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_geo.admin_units",
        description="VN admin units (provinces.open-api.vn): fetch / ingest / lookup",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="fetch admin units from the API and save records to a JSON file")
    p.add_argument("--version", type=int, choices=[1, 2], default=2, help="API version (default: 2)")
    p.add_argument("--out", required=True, metavar="PATH", help="output JSON file path")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SEC")
    _add_json(p)
    p.set_defaults(func=_cmd_fetch)

    p = sub.add_parser("ingest", help="ingest a records JSON file into a SearchStore database")
    p.add_argument("--db", required=True, metavar="PATH", help="SearchStore SQLite db path")
    p.add_argument("--file", required=True, metavar="PATH", help="records JSON file (from fetch)")
    _add_json(p)
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("lookup", help="FTS-lookup admin units in a SearchStore database")
    p.add_argument("query", help="search text (e.g. a province or ward name)")
    p.add_argument("--db", required=True, metavar="PATH", help="SearchStore SQLite db path")
    p.add_argument("--limit", type=int, default=20, metavar="N")
    _add_json(p)
    p.set_defaults(func=_cmd_lookup)

    return parser


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return the process exit code (0 ok · 1 runtime error · 2 usage/IO)."""
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
