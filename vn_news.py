"""vn_news — Vietnamese news RSS ring → normalize → searchstore ingest → query.

Contract: analysis/r8-interfaces.md §8 (R8-D). Stdlib-only, Python 3.11+.

Pipeline:

- ``fetch`` downloads the feed ring (urllib; UA ``hermes-vn-news/0.1
  (local)``; 20s timeout; ≥1.5s between feeds), parses RSS 2.0 with
  xml.etree.ElementTree (CDATA-safe), normalizes each item to
  ``{title, url, source, published, summary}`` — entities unescaped, HTML
  tags stripped, whitespace collapsed; ``published`` is ISO-8601 (or "" when
  missing/unparseable) — dedupes by URL per run, and emits JSONL. Per-feed
  failures are recorded and never fatal.
- ``ingest`` stores records in the repo SearchStore as documents tagged
  ``provider="vn_news"``. Dedup is content-addressed
  ``(url_key, content_sha256)`` — re-running over unchanged input adds 0.
  (``ingest_search`` carries no dedup, so documents are the store of record;
  this is what the §8 idempotency proof requires.)
- ``query`` FTS-searches the store filtered to ``vn_news``; ``--days``
  filters on ``published`` freshness (records without a published date are
  skipped and counted in ``note``).
- ``list`` shows the feed ring + last-ingest info from the store.

Feed ring — every §8 candidate verified live once on 2026-10-06:

- VnExpress   https://vnexpress.net/rss/tin-moi-nhat.rss  ✅ working
- Tuổi Trẻ   https://tuoitre.vn/rss/tin-moi-nhat.rss    ✅ working (non-standard pubDate
  ``10/6/2026 5:34:00 PM`` → parsed via a VN-format fallback, assumed +07:00)
- Thanh Niên  https://thanhnien.vn/rss/home.rss         ✅ working (2-digit-year RFC-822 pubDates)
- VietnamNet  https://vietnamnet.vn/thoi-su.rss          ✅ working (§8 candidate
  /rss/tin-moi-nhat.rss is 404; /rss/thoi-su.rss 301s here)
- CafeF       https://cafef.vn/home.rss                  ✅ working (§8 candidate
  /rss/tin-moi-nhat.rss is 404; this URL is linked from the CafeF homepage)
- GenK        https://genk.vn/rss/home.rss              ✅ working (§8 candidate
  /rss/tin-moi-nhat.rss is 500)
- ZNews       https://znews.vn/rss/tin-moi-nhat.rss      ❌ dropped — RSS discontinued
  (all candidate paths 404; no feed links anywhere on znews.vn)

Usage:

    python -m vn_news fetch --out news.jsonl   # fetch ring → JSONL file
    python -m vn_news fetch --json             # fetch ring → JSONL on stdout
    python -m vn_news ingest --file news.jsonl # → repo store (default ./searchstore.db)
    python -m vn_news query "bão" --days 7     # fresh hits only
    python -m vn_news list                     # ring + last-ingest info

Exit codes: 0 ok · 2 usage/IO/runtime error.
"""

from __future__ import annotations

import argparse
import email.utils
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from searchstore import SearchStore, SearchStoreError, content_sha256, url_key

USER_AGENT = "hermes-vn-news/0.1 (local)"
DEFAULT_TIMEOUT = 20.0
MIN_FEED_DELAY = 1.5
DEFAULT_DB = "./searchstore.db"
SOURCE_TAG = "vn_news"

# The verified ring (see docstring). ZNews dropped: RSS discontinued (2026-10-06).
FEEDS: tuple[dict, ...] = (
    {"name": "VnExpress", "url": "https://vnexpress.net/rss/tin-moi-nhat.rss"},
    {"name": "Tuổi Trẻ", "url": "https://tuoitre.vn/rss/tin-moi-nhat.rss"},
    {"name": "Thanh Niên", "url": "https://thanhnien.vn/rss/home.rss"},
    {"name": "VietnamNet", "url": "https://vietnamnet.vn/thoi-su.rss"},
    {"name": "CafeF", "url": "https://cafef.vn/home.rss"},
    {"name": "GenK", "url": "https://genk.vn/rss/home.rss"},
)

_DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
# VN portals sometimes use "10/6/2026 5:34:00 PM" (month-first, +07:00).
_VN_DATE_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S")
_VN_TZ = timezone(timedelta(hours=7))
_TAG_RE = re.compile(r"<[^>]*>")


class VnNewsError(Exception):
    """All vn_news library errors (the CLI maps them to exit 2)."""


# --------------------------------------------------------------------------- fetch


def download_feed(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    """GET *url* with the module UA; return raw bytes.

    Raises VnNewsError on HTTP/timeout/IO failure.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - https URLs from the FEEDS ring / --feed flag
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VnNewsError(f"fetch failed for {url}: {exc}") from exc


def _clean_text(text: str) -> str:
    """Strip HTML tags (replaced by a space to keep sentence boundaries) and
    collapse whitespace (entities are already unescaped by the XML parser)."""
    return " ".join(_TAG_RE.sub(" ", text).split())


def _child_text(item: ET.Element, tag: str) -> str:
    el = item.find(tag)
    if el is None or el.text is None:
        return ""
    return el.text


def parse_published(text: str) -> str:
    """RFC-822 / ISO-8601 / VN-style date → ISO-8601 with seconds; "" when
    missing or unparseable. Naive RFC-822/ISO dates are assumed UTC; VN-style
    formats are assumed +07:00."""
    text = (text or "").strip()
    if not text:
        return ""
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in _VN_DATE_FORMATS:
                try:
                    dt = datetime.strptime(text, fmt).replace(tzinfo=_VN_TZ)
                    break
                except ValueError:
                    continue
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat(timespec="seconds")


def _normalize_item(item: ET.Element, *, source: str) -> dict | None:
    """Map one RSS <item> to a normalized record; None = malformed (skipped).

    An item without a usable <link> or <title> is malformed — skipping it is
    not fatal for the rest of the feed.
    """
    title = _clean_text(_child_text(item, "title"))
    url = _child_text(item, "link").strip()
    if not title or not url:
        return None
    published = parse_published(_child_text(item, "pubDate") or _child_text(item, _DC_DATE))
    summary = _clean_text(_child_text(item, "description"))
    return {"title": title, "url": url, "source": source, "published": published, "summary": summary}


def parse_rss(xml: bytes, *, source: str, feed_url: str | None = None) -> list[dict]:
    """Parse RSS 2.0 bytes into normalized records (malformed items skipped).

    Raises VnNewsError when the payload is not well-formed RSS 2.0 (a
    feed-level error — the fetch loop records it and continues).
    """
    where = feed_url or source
    # Curated https feed ring; stdlib ElementTree does not resolve external
    # entities or DTDs, and the ring is fetched from verified hosts.
    try:
        root = ET.fromstring(xml)  # nosec B314 - trusted ring, no entity expansion
    except (ET.ParseError, ValueError) as exc:
        raise VnNewsError(f"invalid RSS XML from {where}: {exc}") from exc
    if root.tag.split("}")[-1] != "rss":
        raise VnNewsError(f"expected an RSS 2.0 document from {where}, got <{root.tag}>")
    channel = root.find("channel")
    if channel is None:
        raise VnNewsError(f"RSS feed from {where} has no <channel>")
    records = []
    for item in channel.findall("item"):
        rec = _normalize_item(item, source=source)
        if rec is not None:
            records.append(rec)
    return records


def fetch_feeds(
    feeds: list[dict] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    delay: float = MIN_FEED_DELAY,
    sleep=time.sleep,
) -> dict:
    """Fetch + parse + dedupe a ring of ``{"name", "url"}`` feeds.

    Returns ``{"records": [...], "errors": [...], "feeds": [...]}`` where
    ``feeds`` carries per-feed record counts (or the error). Dedup is by
    normalized URL (first feed in the ring wins). *sleep* is injectable so
    tests stay hermetic and fast.
    """
    ring = list(feeds) if feeds is not None else list(FEEDS)
    records: list[dict] = []
    errors: list[dict] = []
    feed_reports: list[dict] = []
    seen: set[str] = set()
    for i, feed in enumerate(ring):
        if i and delay > 0:
            sleep(delay)
        name, url = feed["name"], feed["url"]
        try:
            xml = download_feed(url, timeout=timeout)
            feed_records = parse_rss(xml, source=name, feed_url=url)
        except VnNewsError as exc:
            errors.append({"feed": name, "url": url, "error": str(exc)})
            feed_reports.append({"name": name, "url": url, "error": str(exc)})
            continue
        feed_reports.append({"name": name, "url": url, "records": len(feed_records)})
        for rec in feed_records:
            key = url_key(rec["url"])
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)
    return {"records": records, "errors": errors, "feeds": feed_reports}


# --------------------------------------------------------------------------- ingest


def load_records(path: str | Path) -> list[dict]:
    """Read a JSONL records file (as produced by ``fetch --out``)."""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def ingest_records(store: SearchStore, records: list[dict], *, provider: str = SOURCE_TAG) -> dict:
    """Ingest normalized records into *store*; return ``{"added", "skipped"}``.

    Content-addressed dedup on ``(url_key, content_sha256)``: re-running over
    unchanged input adds 0. The document text is title + summary; ``meta``
    keeps source/published/summary for query-time enrichment and freshness.
    """
    added = skipped = 0
    for rec in records:
        text = f"{rec['title']}\n\n{rec.get('summary', '')}"
        key = url_key(rec["url"])
        sha = content_sha256(text)
        row = store.conn.execute(
            "SELECT id FROM documents WHERE url_key = ? AND content_sha256 = ?", (key, sha)
        ).fetchone()
        if row is not None:
            skipped += 1
            continue
        store.ingest_document(
            rec["url"],
            text,
            title=rec["title"],
            provider=provider,
            fetched_at=rec.get("published") or None,
            format="markdown",
            meta={
                "source": rec.get("source", ""),
                "published": rec.get("published", ""),
                "summary": rec.get("summary", ""),
            },
        )
        added += 1
    return {"added": added, "skipped": skipped}


# --------------------------------------------------------------------------- query


def query_records(store: SearchStore, q: str, *, days: int | None = None, limit: int = 10) -> dict:
    """FTS-search *store* for *q*, filtered to the ``vn_news`` provider.

    With *days*, keep only records whose ``published`` is within the last
    *days* days; records without a published date are skipped and counted in
    ``note``. Returns ``{"query", "count", "results", "skipped_no_published",
    "note"}``.
    """
    if not q or not q.strip():
        raise VnNewsError("query text must not be empty")
    hits = store.search(q, limit=max(limit * 20, 50))
    cutoff = datetime.now(UTC) - timedelta(days=days) if days is not None else None
    results: list[dict] = []
    skipped_no_published = 0
    for hit in hits:
        if hit.get("provider") != SOURCE_TAG:
            continue
        doc = store.get_document(hit["doc_id"])
        meta = json.loads(doc["meta"] or "{}") if doc else {}
        published = meta.get("published", "")
        if cutoff is not None:
            if not published:
                skipped_no_published += 1
                continue
            try:
                pub_dt = datetime.fromisoformat(published)
            except ValueError:
                skipped_no_published += 1
                continue
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=UTC)
            if pub_dt < cutoff:
                continue
        results.append(
            {
                "title": hit["title"],
                "url": hit["url"],
                "source": meta.get("source", ""),
                "published": published,
                "summary": meta.get("summary", ""),
                "snippet": hit.get("snippet", ""),
                "score": hit.get("score"),
            }
        )
        if len(results) >= limit:
            break
    note = ""
    if skipped_no_published:
        note = f"{skipped_no_published} record(s) without a published date skipped (--days filter)"
    return {
        "query": q,
        "count": len(results),
        "results": results,
        "skipped_no_published": skipped_no_published,
        "note": note,
    }


def query(q: str, *, db_path: str | Path = DEFAULT_DB, days: int | None = None, limit: int = 10) -> list[dict]:
    """Convenience adapter: search a store db, return the result dicts.

    Used by external callers (e.g. the gateway MCP ``hermes_vn`` tool); the
    CLI path uses ``query_records`` directly. Returns [] when the db does not
    exist yet (never creates it).
    """
    path = Path(db_path)
    if not path.exists():
        return []
    with SearchStore(path) as store:
        return query_records(store, q, days=days, limit=limit)["results"]


def list_feeds(*, db_path: str | Path = DEFAULT_DB) -> dict:
    """Feed ring + last-ingest info from the store (never creates the DB)."""
    out = {"feeds": [{"name": f["name"], "url": f["url"]} for f in FEEDS], "store": None}
    if not Path(db_path).exists():
        return out
    with SearchStore(db_path) as store:
        row = store.conn.execute(
            "SELECT COUNT(*) AS n, MAX(fetched_at) AS last FROM documents WHERE provider = ?",
            (SOURCE_TAG,),
        ).fetchone()
        out["store"] = {"path": str(db_path), "documents": row["n"], "last_fetched_at": row["last"]}
    return out


# --------------------------------------------------------------------------- CLI


def _feed_from_url(url: str) -> dict:
    for feed in FEEDS:
        if feed["url"] == url:
            return {"name": feed["name"], "url": url}
    return {"name": urllib.parse.urlparse(url).netloc, "url": url}


def _cmd_fetch(args: argparse.Namespace) -> tuple[dict, list[str]]:
    ring = [_feed_from_url(u) for u in args.feed] if args.feed else None
    result = fetch_feeds(ring, timeout=args.timeout, delay=args.delay)
    records = result["records"]
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    human = []
    for fr in result["feeds"]:
        if "error" in fr:
            human.append(f"{fr['name']}: ERROR {fr['error']}")
        else:
            human.append(f"{fr['name']}: {fr['records']} record(s)")
    human.append(
        f"total: {len(records)} record(s) from {len(result['feeds'])} feed(s), {len(result['errors'])} error(s)"
    )
    if args.out:
        human.append(f"wrote {args.out}")
    # exit 2 only when every feed failed; partial failures still exit 0
    ok = len(result["errors"]) < len(result["feeds"])
    payload = {"ok": ok, "records": records, "errors": result["errors"], "feeds": result["feeds"], "out": args.out}
    return payload, human


def _cmd_ingest(args: argparse.Namespace) -> tuple[dict, list[str]]:
    records = load_records(args.file)
    with SearchStore(args.db) as store:
        result = ingest_records(store, records)
    payload = {"ok": True, "db": str(args.db), "file": str(args.file), **result}
    human = [f"ingested {result['added']} new, skipped {result['skipped']} (already stored) into {args.db}"]
    return payload, human


def _cmd_query(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with SearchStore(args.db) as store:
        result = query_records(store, args.query, days=args.days, limit=args.limit)
    payload = {"ok": True, "db": str(args.db), **result}
    human = [f"{result['count']} result(s) for {args.query!r}"]
    for r in result["results"]:
        human.append(f"  {r['title']} — {r['url']}")
    if result["note"]:
        human.append(f"note: {result['note']}")
    return payload, human


def _cmd_list(args: argparse.Namespace) -> tuple[dict, list[str]]:
    result = list_feeds(db_path=args.db)
    payload = {"ok": True, **result}
    human = ["feeds:"]
    for f in result["feeds"]:
        human.append(f"  {f['name']}: {f['url']}")
    if result["store"] is None:
        human.append(f"store: (no database at {args.db})")
    else:
        s = result["store"]
        human.append(f"store: {s['documents']} vn_news document(s), last fetched {s['last_fetched_at'] or '-'}")
    return payload, human


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_news",
        description="Vietnamese news RSS ring: fetch / ingest / query / list",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="fetch the feed ring and emit normalized records (JSONL)")
    p.add_argument(
        "--feed",
        action="append",
        metavar="URL",
        help="fetch only this feed URL (repeatable; default: the verified ring)",
    )
    p.add_argument("--out", metavar="PATH", help="write JSONL records to this file")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SEC")
    p.add_argument("--delay", type=float, default=MIN_FEED_DELAY, metavar="SEC", help="min seconds between feeds")
    p.add_argument("--json", action="store_true", help="print JSONL records to stdout")
    p.set_defaults(func=_cmd_fetch)

    p = sub.add_parser("ingest", help="ingest a JSONL records file (from fetch) into a SearchStore DB")
    p.add_argument("--db", default=DEFAULT_DB, metavar="PATH", help="SearchStore SQLite db path")
    p.add_argument("--file", required=True, metavar="PATH", help="JSONL records file (from fetch)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("query", help="search ingested vn_news documents")
    p.add_argument("query", metavar="Q")
    p.add_argument("--db", default=DEFAULT_DB, metavar="PATH")
    p.add_argument("--days", type=int, default=None, metavar="N", help="only records published within the last N days")
    p.add_argument("--limit", type=int, default=10, metavar="L")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_query)

    p = sub.add_parser("list", help="list the feed ring + last-ingest info")
    p.add_argument("--db", default=DEFAULT_DB, metavar="PATH")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_list)

    return parser


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return the process exit code (0 ok · 2 usage/IO/runtime)."""
    args = _build_parser().parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        payload, human = args.func(args)
    except (
        VnNewsError,
        SearchStoreError,
        FileNotFoundError,
        ValueError,
        OSError,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        _emit_error(exc, json_mode)
        return 2
    if args.command == "fetch":
        if json_mode and not args.out:
            for rec in payload["records"]:
                print(json.dumps(rec, ensure_ascii=False))
            for err in payload["errors"]:
                print(f"error: {err['feed']}: {err['error']}", file=sys.stderr)
        else:
            for line in human:
                print(line)
    elif json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in human:
            print(line)
    return 0 if payload.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
