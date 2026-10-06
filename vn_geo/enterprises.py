"""vn_geo.enterprises — provincial CKAN open-data enterprise lists (R4-E).

Provincial portals (Hai Phong, Tay Ninh) publish enterprise lists through the
standard CKAN Action API (``datastore_search``). Record schemas vary:

- Hai Phong datasets use slash column names (``Ma so doanh nghiep/Ma so don vi
  phu thuoc``, ``Ten doanh nghiep/Ten don vi phu thuoc``, ...), a ``Von dieu
  le`` capital string, and sometimes-null phones.
- Tay Ninh uses ``Ten doanh nghiep`` / ``Dia chi`` / ``Dien thoai lien he`` /
  ``Latitude`` / ``Longitude`` and has NO enterprise-code column.

QUIRK (preserved, not fixed): Tay Ninh's ``Latitude`` column holds
longitude-like values (~106.x) while ``Longitude`` holds latitude-like values
(~10.x). :func:`normalize` copies both verbatim into ``lat``/``lon``; the raw
record is always kept under ``raw`` for audit.

Known limitation (2026-10-06): the ``haiphong-registrations`` resource is an
XLSX file whose datastore is inactive (``datastore_active: false``), so
``datastore_search`` answers HTTP 404. The entry is kept in :data:`DATASETS`
per the module spec; :func:`fetch_dataset` surfaces the CKAN error as a
``VnGeoError`` with an actionable message.

CLI: ``python -m vn_geo.enterprises <list|fetch|ingest|query> ...`` — exit 0
ok, 1 runtime error, 2 usage/IO.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from searchstore import SearchStore, SearchStoreError

from . import VnGeoError

USER_AGENT = "hermes-vn-geo/0.1"
FORMAT = "enterprise"
EVENT_KIND = "enterprises_saved"

DATASETS: dict[str, dict] = {
    "haiphong-new": {
        "portal": "https://data.haiphong.gov.vn",
        "resource_id": "a119cc68-9a99-4f80-ac93-b0d22728838e",
        "province": "haiphong",
        "kind": "new",
        "label": "Hai Phong newly established enterprises, June 2026 (1,429 records)",
    },
    "haiphong-dissolved": {
        "portal": "https://data.haiphong.gov.vn",
        "resource_id": "e440ad11-47a3-4565-b426-f31903dda311",
        "province": "haiphong",
        "kind": "dissolved",
        "label": "Hai Phong dissolved enterprises, June 2026",
    },
    "haiphong-registrations": {
        "portal": "https://data.haiphong.gov.vn",
        "resource_id": "99643789-e6ac-42c6-8095-adfa1c487fcf",
        "province": "haiphong",
        "kind": "registrations",
        "label": "Hai Phong registrations Oct 2024-Mar 2025 (XLSX upload; datastore inactive)",
    },
    "tayninh-list": {
        "portal": "https://data.tayninh.gov.vn",
        "resource_id": "160b59c9-e94f-441b-b75c-85a6a1fbcbec",
        "province": "tayninh",
        "kind": "list",
        "label": "Tay Ninh enterprise list with lat/lng (198 records)",
    },
}

# Column-name variants, in lookup priority order.
_CODE_KEYS = (
    "Ma so doanh nghiep/Ma so don vi phu thuoc",
    "Ma so doanh nghiep",
    "Mã số doanh nghiệp",
    "Enterprise code",
    "code",
)
_NAME_KEYS = (
    "Ten doanh nghiep/Ten don vi phu thuoc",
    "Ten doanh nghiep",
    "Tên doanh nghiệp",
    "name",
)
_ADDRESS_KEYS = (
    "Dia chi tru so chinh/Dia chi don vi phu thuoc",
    "Dia chi",
    "Địa chỉ",
    "address",
)
_REP_KEYS = (
    "Nguoi dai dien theo phap luat/Nguoi dung dau don vi phu thuoc",
    "Nguoi dai dien theo phap luat",
    "Người đại diện",
    "representative",
)
_PHONE_KEYS = (
    "Dien thoai lien he",
    "Điện thoại liên hệ",
    "phone",
)
_CAPITAL_KEYS = (
    "Von dieu le",
    "Vốn điều lệ",
    "capital",
)
_LAT_KEYS = ("Latitude", "latitude", "lat", "Lat", "Vĩ độ")
_LON_KEYS = ("Longitude", "longitude", "lon", "Lon", "Lng", "Kinh độ")


# --------------------------------------------------------------------------- helpers


def _slug(province: str) -> str:
    """Lowercase ASCII slug: ``Hải Phòng`` -> ``haiphong``."""
    s = str(province).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    s = s.casefold()
    import re

    return re.sub(r"[^a-z0-9]+", "", s)


def _clean_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _first(record: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in record:
            v = _clean_str(record.get(k))
            if v is not None:
                return v
    return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None


def _parse_capital(value) -> int | float | None:
    """Parse a Vietnamese capital string (``10.000.000.000`` / ``1,5 tỷ``-ish).

    Dots are thousand separators, comma is the decimal mark. Returns int when
    integral, else float; None when unparseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    if not s:
        return None
    import re

    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s in {"-", ".", ","}:
        return None
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        num = float(s)
    except ValueError:
        return None
    return int(num) if num.is_integer() else num


def _ckan_error_message(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
    return None


# --------------------------------------------------------------------------- fetch


def fetch_dataset(key: str, *, timeout: float = 30.0, page_size: int = 1000) -> list[dict]:
    """Fetch every record of a CKAN datastore resource, following offsets.

    Raises :class:`VnGeoError` for unknown keys, CKAN ``success: false``
    payloads (message included), and wrapped HTTP/JSON failures.
    """
    ds = DATASETS.get(key)
    if ds is None:
        raise VnGeoError(f"unknown dataset {key!r}; known: {', '.join(sorted(DATASETS))}")
    if page_size <= 0:
        raise VnGeoError(f"page_size must be > 0, got {page_size!r}")
    base = ds["portal"].rstrip("/")
    records: list[dict] = []
    offset = 0
    total: int | None = None
    while True:
        qs = urllib.parse.urlencode({"resource_id": ds["resource_id"], "limit": page_size, "offset": offset})
        url = f"{base}/api/3/action/datastore_search?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - URL is a module-level https constant
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = _ckan_error_message(e.read()) or e.msg
            raise VnGeoError(
                f"CKAN datastore_search for dataset {key!r} failed: HTTP {e.code} {detail} "
                f"(resource {ds['resource_id']}; the datastore may be inactive for file uploads)"
            ) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise VnGeoError(f"CKAN datastore_search for dataset {key!r} failed: {e}") from e
        except (ValueError, UnicodeDecodeError) as e:
            raise VnGeoError(f"CKAN datastore_search for dataset {key!r} returned invalid JSON: {e}") from e
        if not isinstance(payload, dict) or not payload.get("success"):
            if isinstance(payload, dict):
                detail = _ckan_error_message(json.dumps(payload).encode("utf-8")) or "unknown CKAN error"
            else:
                detail = "non-object response"
            raise VnGeoError(f"CKAN datastore_search for dataset {key!r} failed: {detail}")
        result = payload.get("result") or {}
        batch = result.get("records") or []
        if total is None:
            try:
                total = int(result.get("total", 0))
            except (TypeError, ValueError):
                total = None
        records.extend(batch)
        offset += len(batch)
        if len(batch) < page_size:
            break
        if total is not None and len(records) >= total:
            break
    return records


# --------------------------------------------------------------------------- normalize


def normalize(records: list[dict], *, province: str, kind: str) -> list[dict]:
    """Map raw CKAN rows to enterprise records.

    Output keys: ``code`` / ``name`` / ``address`` / ``representative`` /
    ``phone`` / ``capital_vnd`` / ``lat`` / ``lon`` / ``raw``. Keys whose
    value is None are dropped from the top level; ``raw`` always keeps the
    complete original row.
    """
    out: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            raise VnGeoError(f"cannot normalize non-object record: {type(rec).__name__}")
        norm: dict = {
            "code": _first(rec, _CODE_KEYS),
            "name": _first(rec, _NAME_KEYS),
            "address": _first(rec, _ADDRESS_KEYS),
            "representative": _first(rec, _REP_KEYS),
            "phone": _first(rec, _PHONE_KEYS),
            "capital_vnd": _parse_capital(
                next((rec[k] for k in _CAPITAL_KEYS if k in rec and rec[k] is not None), None)
            ),
            "lat": _to_float(next((rec[k] for k in _LAT_KEYS if k in rec and rec[k] is not None), None)),
            "lon": _to_float(next((rec[k] for k in _LON_KEYS if k in rec and rec[k] is not None), None)),
        }
        norm["province"] = _clean_str(province)
        norm["kind"] = _clean_str(kind)
        norm = {k: v for k, v in norm.items() if v is not None}
        norm["raw"] = rec
        out.append(norm)
    return out


# --------------------------------------------------------------------------- documents / ingest / query


def records_to_documents(records: list[dict], *, province: str, kind: str, resource_id: str) -> list[dict]:
    """Map normalized records to :meth:`SearchStore.ingest_document` kwargs.

    URL: ``vn://ckan/{slug}/{resource_id}/{code or idx}``; provider
    ``ckan-{slug}``; format ``enterprise``; meta = record + source info.
    """
    slug = _slug(province)
    docs: list[dict] = []
    for idx, rec in enumerate(records):
        code = _clean_str(rec.get("code"))
        doc_key = code if code else str(idx)
        url = f"vn://ckan/{slug}/{resource_id}/{urllib.parse.quote(doc_key, safe='')}"
        name = _clean_str(rec.get("name"))
        parts = [
            p
            for p in (
                name,
                _clean_str(rec.get("address")),
                _clean_str(rec.get("representative")),
                _clean_str(rec.get("phone")),
            )
            if p
        ]
        text = (". ".join(parts) + ".") if parts else f"{doc_key}."
        meta = dict(rec)
        meta.update({"province": province, "kind": kind, "resource_id": resource_id, "source": "ckan"})
        docs.append(
            {
                "url": url,
                "text": text,
                "title": name or code or doc_key,
                "provider": f"ckan-{slug}",
                "format": FORMAT,
                "meta": meta,
            }
        )
    return docs


def _looks_normalized(records: list[dict]) -> bool:
    return bool(records) and all(isinstance(r, dict) and isinstance(r.get("raw"), dict) for r in records)


def ingest(store: SearchStore, records: list[dict], *, dataset_key: str) -> dict:
    """Ingest records (raw CKAN rows or normalized) into *store*.

    Returns ``{"documents": n}`` and records an ``enterprises_saved`` event
    with the dataset, count, and per-document keys (code or positional idx).
    """
    ds = DATASETS.get(dataset_key)
    if ds is None:
        raise VnGeoError(f"unknown dataset {dataset_key!r}; known: {', '.join(sorted(DATASETS))}")
    normalized = records if _looks_normalized(records) else normalize(records, province=ds["province"], kind=ds["kind"])
    docs = records_to_documents(normalized, province=ds["province"], kind=ds["kind"], resource_id=ds["resource_id"])
    keys: list[str] = []
    for idx, (rec, doc) in enumerate(zip(normalized, docs, strict=True)):
        code = _clean_str(rec.get("code"))
        keys.append(code if code else str(idx))
        store.ingest_document(
            doc["url"],
            doc["text"],
            title=doc["title"],
            provider=doc["provider"],
            format=doc["format"],
            meta=doc["meta"],
        )
    store.record_event(EVENT_KIND, {"dataset": dataset_key, "count": len(docs), "keys": keys})
    return {"documents": len(docs)}


def query(store: SearchStore, text: str, *, province: str | None = None, limit: int = 20) -> list[dict]:
    """FTS-search ingested enterprises; optional province filter.

    The province filter matches the document provider (``ckan-{slug}``).
    Returns ``name`` / ``code`` / ``address`` / ``representative`` / ``phone`` /
    ``url`` per hit.
    """
    if not str(text or "").strip():
        raise VnGeoError("query text must not be empty")
    sql = (
        "SELECT d.url, d.title, d.meta FROM documents_current d "
        "WHERE d.format = 'enterprise' "
        "AND d.id IN (SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?)"
    )
    params: list = [text]
    if province is not None:
        sql += " AND d.provider = ?"
        params.append(f"ckan-{_slug(province)}")
    sql += " LIMIT ?"
    params.append(limit)
    try:
        rows = store.conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        raise VnGeoError(f"invalid query text {text!r}: {e}") from e
    out: list[dict] = []
    for row in rows:
        try:
            meta = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        out.append(
            {
                "name": meta.get("name") or row["title"],
                "code": meta.get("code"),
                "address": meta.get("address"),
                "representative": meta.get("representative"),
                "phone": meta.get("phone"),
                "url": row["url"],
            }
        )
    return out


def list_datasets() -> list[dict]:
    """Describe the known CKAN enterprise datasets (each includes its key)."""
    return [{"key": key, **info} for key, info in DATASETS.items()]


# --------------------------------------------------------------------------- json io


def save_records_json(records: list[dict], path) -> None:
    """Write raw records as a UTF-8 JSON array (creates parents)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)


def load_records_json(path) -> list[dict]:
    """Load records written by :func:`save_records_json`.

    Accepts a bare JSON array or ``{"records": [...]}``.
    """
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError as e:
        raise VnGeoError(f"file not found: {path} ({e})") from e
    except json.JSONDecodeError as e:
        raise VnGeoError(f"{path}: invalid JSON: {e}") from e
    except OSError as e:
        raise VnGeoError(f"cannot read {path}: {e}") from e
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return payload["records"]
    if isinstance(payload, list):
        return payload
    raise VnGeoError(f'{path}: expected a JSON array or {{"records": [...]}} object')


# --------------------------------------------------------------------------- cli


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def _cmd_list(args: argparse.Namespace) -> tuple[dict, list[str]]:
    datasets = list_datasets()
    payload = {"ok": True, "count": len(datasets), "datasets": datasets}
    human = [f"{len(datasets)} datasets"]
    for d in datasets:
        human.append(f"  {d['key']}: {d['label']} [{d['province']}/{d['kind']}]")
    return payload, human


def _cmd_fetch(args: argparse.Namespace) -> tuple[dict, list[str]]:
    records = fetch_dataset(args.dataset)
    save_records_json(records, args.out)
    payload = {"ok": True, "dataset": args.dataset, "count": len(records), "out": str(args.out)}
    human = [f"fetched {len(records)} records for {args.dataset} -> {args.out}"]
    return payload, human


def _cmd_ingest(args: argparse.Namespace) -> tuple[dict, list[str]]:
    if args.file:
        records = load_records_json(args.file)
    else:
        records = fetch_dataset(args.dataset)
    with SearchStore(args.db) as store:
        result = ingest(store, records, dataset_key=args.dataset)
    payload = {"ok": True, "dataset": args.dataset, "db": str(args.db), **result}
    human = [f"ingested {result['documents']} documents for {args.dataset} into {args.db}"]
    return payload, human


def _cmd_query(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with SearchStore(args.db) as store:
        results = query(store, args.text, province=args.province, limit=args.limit)
    payload = {"ok": True, "count": len(results), "results": results}
    human = [f"{len(results)} enterprises"]
    for r in results:
        bits = " — ".join(b for b in (r["name"], r["address"]) if b)
        extra = " / ".join(b for b in (r["code"], r["phone"]) if b)
        human.append(f"  {bits}" + (f" ({extra})" if extra else ""))
    return payload, human


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_geo.enterprises",
        description="Provincial CKAN enterprise lists: list/fetch/ingest/query",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list known CKAN enterprise datasets")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("fetch", help="fetch a dataset's records to a JSON file")
    p.add_argument("--dataset", required=True, metavar="KEY")
    p.add_argument("--out", required=True, metavar="PATH")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_fetch)

    p = sub.add_parser("ingest", help="ingest a dataset into a SearchStore DB")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--dataset", required=True, metavar="KEY")
    p.add_argument("--file", metavar="JSON", help="records file (fetch live when omitted)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("query", help="FTS-search ingested enterprises")
    p.add_argument("text", metavar="TEXT")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--province", metavar="P", help="filter by province (e.g. haiphong)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_query)
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
