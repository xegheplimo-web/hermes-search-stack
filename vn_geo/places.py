"""vn_geo.places — scanned place records (Google Maps, OSM, Foody, manual).

Contract: analysis/r4-interfaces.md §5. Place scans arrive as JSONL records,
are versioned inside a SearchStore database (dedup by (url_key, sha256(text));
new content for the same url_key creates a new document version — that powers
refresh/diff), and are queryable via FTS + metadata filters.

CLI: ``python -m vn_geo.places <save|query|diff> ...`` — exit 0 ok,
1 runtime error, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

from searchstore import SearchStore, SearchStoreError, content_sha256, url_key
from searchstore.store import fold_d

from . import VnGeoError

PLACE_SOURCES = frozenset({"google-maps", "osm", "foody", "manual"})
PLACE_FORMAT = "place"
DIFF_FIELDS = ("name", "rating", "review_count", "hours", "address")
_AREA_FIELDS = ("address", "province", "ward", "district", "city", "area")

_WS_RE = re.compile(r"\s+")
_NO_REVIEW_RE = re.compile(
    r"(?:chưa có|không có|chua co|khong co)\s+(?:bài\s+|lượt\s+|bai\s+|luot\s+)?đánh giá"
    r"|no reviews yet",
    re.IGNORECASE,
)
_RATING_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:/\s*5\s*)?(?:sao|stars?)\b", re.IGNORECASE)
_COUNT_RE = re.compile(
    r"(\d[\d.,\s ]*\d|\d)\s*(?:bài đánh giá|lượt đánh giá|bai danh gia|đánh giá|nhận xét|reviews?)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- helpers


def _fold(s: str) -> str:
    """Casefold + strip diacritics so 'quán phở' matches 'quan pho'."""
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).casefold()


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(text: str) -> int | None:
    digits = re.sub(r"[.,\s ]", "", text)
    try:
        return int(digits)
    except ValueError:
        return None


# --------------------------------------------------------------------------- parsing / keys


def parse_rating_label(label: str | None) -> tuple[float | None, int | None]:
    """Parse a Google Maps rating aria label into ``(rating, review_count)``.

    Handles VN labels (``"4,8 sao 24 bài đánh giá"``), English
    (``"4.8 stars 24 reviews"``), missing counts, and the no-reviews case
    (``"Chưa có bài đánh giá"`` -> ``(None, 0)``).
    """
    if not label or not str(label).strip():
        return None, None
    text = str(label).strip()
    if _NO_REVIEW_RE.search(text):
        return None, 0
    rating = None
    rest = text
    m = _RATING_RE.search(text)
    if m is not None:
        rating = _to_float(m.group(1).replace(",", "."))
        rest = text[: m.start()] + " " + text[m.end() :]
    count = None
    m = _COUNT_RE.search(rest)
    if m is not None:
        count = _to_int(m.group(1))
    return rating, count


def place_key(record: dict) -> str:
    """Stable dedup key: ``source_id`` if present, else ``name|lat,lon``."""
    sid = str(record.get("source_id") or "").strip()
    if sid:
        return sid
    name = _WS_RE.sub(" ", str(record.get("name") or "")).strip().lower()
    lat = _to_float(record.get("lat")) or 0.0
    lon = _to_float(record.get("lon")) or 0.0
    return f"{name}|{lat:.5f},{lon:.5f}"


# --------------------------------------------------------------------------- jsonl io


def load_scan_jsonl(path) -> list[dict]:
    """Load a place-scan JSONL file (one record per line, §5 schema)."""
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise VnGeoError(f"{path}:{lineno}: invalid JSON: {e}") from e
            if not isinstance(obj, dict):
                raise VnGeoError(f"{path}:{lineno}: expected a JSON object, got {type(obj).__name__}")
            records.append(obj)
    return records


def save_scan_jsonl(records: list[dict], path) -> None:
    """Write place records as JSONL (UTF-8, one object per line)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- save


def _record_source(record: dict, source: str | None, idx: int) -> str:
    src = source or record.get("source")
    if not src:
        raise VnGeoError(f"record {idx} has no 'source'; pass source= to save_places")
    return str(src)


def _slug(value) -> str:
    """Folded ASCII slug for ``vn://`` keys: ``Google Maps`` -> ``google-maps``,
    ``Quán Ăn/12`` -> ``quan-an-12`` (unaccent ``_fold``, non-alnum -> ``-``)."""
    return re.sub(r"[^a-z0-9]+", "-", _fold(str(value))).strip("-")


def _record_url(record: dict, src: str) -> str:
    """Dedup URL for a place record — stable identity (R14-C, contract §3).

    A non-empty ``source_id`` wins over ``extra.url``: the doc is keyed
    ``vn://<slug(src)>/<slug(source_id)>`` so a rotated/expired Maps URL no
    longer mints a phantom new document (analysis/r14-plan.md §0.1 row 2).
    Without ``source_id`` the previous behavior is preserved exactly:
    ``extra.url`` when present, else ``vn://<src>/<quoted place_key>``.
    ``record['extra']`` is never mutated — the raw URL stays in meta.
    """
    sid = str(record.get("source_id") or "").strip()
    if sid:
        return f"vn://{_slug(src)}/{_slug(sid)}"
    extra = record.get("extra") or {}
    if extra.get("url"):
        return str(extra["url"])
    return f"vn://{src}/{quote(place_key(record), safe='')}"


def _record_text(record: dict) -> str:
    """FTS text for a record: ``name. category. address.`` + rating + hours.

    rating/review_count are included in the text on purpose: searchstore
    versions on sha256(text), so a rating-only change must change the text
    or diff() could never see a new version.
    """
    parts = [str(record["name"]).strip()]
    for field in ("category", "address"):
        if record.get(field):
            parts.append(str(record[field]).strip())
    if record.get("rating") is not None:
        parts.append(f"{record['rating']} sao")
    if record.get("review_count"):
        parts.append(f"{record['review_count']} bài đánh giá")
    text = ". ".join(parts) + "."
    if record.get("hours"):
        text += f" {str(record['hours']).strip()}"
    return text


def save_places(store: SearchStore, records: list[dict], *, source: str | None = None) -> dict:
    """Ingest scan records into *store*; return ``{"new","updated","unchanged"}``.

    ``new`` = no prior doc for the url_key; ``updated`` = prior doc exists with
    different content (searchstore appends a new version); ``unchanged`` = same
    content already stored. Records a ``places_saved`` event after the batch.
    """
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    keys: list[str] = []
    for i, rec in enumerate(records):
        src = _record_source(rec, source, i)
        if not str(rec.get("name") or "").strip():
            raise VnGeoError(f"record {i} missing required 'name'")
        url = _record_url(rec, src)
        text = _record_text(rec)
        ukey, sha = url_key(url), content_sha256(text)
        same = store.conn.execute(
            "SELECT 1 FROM documents WHERE url_key = ? AND content_sha256 = ?", (ukey, sha)
        ).fetchone()
        if same is not None:
            counts["unchanged"] += 1
        elif store.conn.execute("SELECT 1 FROM documents WHERE url_key = ? LIMIT 1", (ukey,)).fetchone():
            counts["updated"] += 1
        else:
            counts["new"] += 1
        store.ingest_document(
            url,
            text,
            title=str(rec["name"]).strip(),
            provider=src,
            fetched_at=rec.get("scanned_at") or None,
            format=PLACE_FORMAT,
            meta=rec,
        )
        keys.append(place_key(rec))
    src = source or (records[0].get("source") if records else None)
    store.record_event("places_saved", {"source": src, "count": len(records), "keys": keys})
    return counts


# --------------------------------------------------------------------------- query


def _area_match(record: dict, area_q: str) -> bool:
    hay = [record.get(k) for k in _AREA_FIELDS]
    extra = record.get("extra") or {}
    hay += [extra.get(k) for k in _AREA_FIELDS]
    return any(area_q in str(h).casefold() for h in hay if h)


def query_places(
    store: SearchStore,
    *,
    text: str | None = None,
    area: str | None = None,
    category: str | None = None,
    min_rating: float | None = None,
    limit: int = 20,
) -> list[dict]:
    """Query current place docs: FTS *text* plus meta filters, rating desc."""
    sql = "SELECT d.id, d.url, d.title, d.provider, d.meta FROM documents_current d WHERE d.format = 'place'"
    params: list = []
    if text:
        # Standalone đ-folding index (analysis/r9-interfaces.md §D): fold query text via searchstore.store.fold_d.
        sql += " AND d.id IN (SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?)"
        params.append(fold_d(text))
    try:
        rows = store.conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        raise VnGeoError(f"invalid query text {text!r}: {e}") from e
    area_q = area.strip().casefold() if area else None
    cat_q = _fold(category.strip()) if category else None
    out: list[dict] = []
    for row in rows:
        try:
            rec = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            continue
        if area_q and not _area_match(rec, area_q):
            continue
        if cat_q and cat_q not in _fold(str(rec.get("category") or "")):
            continue
        rating = _to_float(rec.get("rating"))
        if min_rating is not None and (rating is None or rating < min_rating):
            continue
        out.append(
            {
                "name": rec.get("name"),
                "source": rec.get("source") or row["provider"],
                "address": rec.get("address"),
                "lat": rec.get("lat"),
                "lon": rec.get("lon"),
                "rating": rating,
                "review_count": rec.get("review_count"),
                "category": rec.get("category"),
                "url": row["url"],
                "scanned_at": rec.get("scanned_at"),
            }
        )
    out.sort(key=lambda r: (r["rating"] is None, -(r["rating"] or 0.0), str(r["name"]).casefold()))
    return out[:limit]


# --------------------------------------------------------------------------- diff


def _load_saved_events(store: SearchStore, source: str | None) -> list[dict]:
    rows = store.conn.execute("SELECT payload FROM events WHERE kind = 'places_saved' ORDER BY id DESC").fetchall()
    events = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        if source is not None and payload.get("source") != source:
            continue
        events.append(payload)
    return events


def diff(store: SearchStore, *, source: str | None = None) -> dict:
    """Diff the last two ``places_saved`` scans plus versioned field changes.

    added/removed compare the ``keys`` of the two most recent events (with a
    single event everything is ``added``; none -> empty result). ``changed``
    lists place docs with >= 2 stored versions whose name/rating/review_count/
    hours/address differ between the previous and latest version.
    """
    result: dict = {"added": [], "removed": [], "changed": []}
    events = _load_saved_events(store, source)
    if events:
        curr = set(events[0].get("keys") or [])
        prev = set(events[1].get("keys") or []) if len(events) > 1 else set()
        result["added"] = sorted(curr - prev)
        result["removed"] = sorted(prev - curr)
    sql = "SELECT url_key, id, meta FROM documents WHERE format = 'place'"
    params: list = []
    if source is not None:
        sql += " AND provider = ?"
        params.append(source)
    sql += " ORDER BY url_key, id"
    versions: dict[str, list[dict]] = {}
    for row in store.conn.execute(sql, params):
        try:
            meta = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            continue
        versions.setdefault(row["url_key"], []).append(meta)
    for metas in versions.values():
        if len(metas) < 2:
            continue
        old, new = metas[-2], metas[-1]
        changes = {f: [old.get(f), new.get(f)] for f in DIFF_FIELDS if old.get(f) != new.get(f)}
        if changes:
            result["changed"].append({"key": place_key(new), "name": new.get("name"), "changes": changes})
    result["changed"].sort(key=lambda c: c["key"])
    return result


# --------------------------------------------------------------------------- cli


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def _cmd_save(args: argparse.Namespace) -> tuple[dict, list[str]]:
    records = load_scan_jsonl(args.file)
    with SearchStore(args.db) as store:
        counts = save_places(store, records, source=args.source)
    src = args.source or (records[0].get("source") if records else None)
    payload = {"ok": True, "file": str(args.file), "source": src, "count": len(records), **counts}
    human = [
        f"saved {len(records)} records from {args.file}: "
        f"{counts['new']} new, {counts['updated']} updated, {counts['unchanged']} unchanged"
    ]
    return payload, human


def _cmd_query(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with SearchStore(args.db) as store:
        results = query_places(
            store,
            text=args.text,
            area=args.area,
            category=args.category,
            min_rating=args.min_rating,
            limit=args.limit,
        )
    payload = {"ok": True, "count": len(results), "results": results}
    human = [f"{len(results)} places"]
    for r in results:
        rating = f"{r['rating']}★" if r["rating"] is not None else "no rating"
        human.append(f"  {rating:>10}  {r['name']} — {r['address'] or r['url']}")
    return payload, human


def _cmd_diff(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with SearchStore(args.db) as store:
        result = diff(store, source=args.source)
    payload = {"ok": True, **result}
    human = [f"added: {len(result['added'])}, removed: {len(result['removed'])}, changed: {len(result['changed'])}"]
    human += [f"  + {k}" for k in result["added"]]
    human += [f"  - {k}" for k in result["removed"]]
    for c in result["changed"]:
        fields = ", ".join(f"{f}: {v[0]!r} -> {v[1]!r}" for f, v in c["changes"].items())
        human.append(f"  ~ {c['key']} ({c['name']}): {fields}")
    return payload, human


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_geo.places",
        description="Place-scan records: save JSONL scans, query, diff (contract §5)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("save", help="ingest a scan JSONL file into the store")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--file", required=True, metavar="JSONL")
    p.add_argument("--source", metavar="S", help="override record source (google-maps|osm|foody|manual)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_save)

    p = sub.add_parser("query", help="query saved places")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--text", metavar="T", help="FTS text query")
    p.add_argument("--area", metavar="A", help="substring match on address/province/ward fields")
    p.add_argument("--category", metavar="C", help="category substring (case/diacritics-insensitive)")
    p.add_argument("--min-rating", type=float, metavar="R")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_query)

    p = sub.add_parser("diff", help="diff the last two saved scans")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--source", metavar="S")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_diff)
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
