"""Command-line interface for SearchStore (contract §4).

Exit codes: 0 ok · 1 SearchStoreError/data error · 2 usage/IO
(missing file, argparse, bad args). ``--json`` prints ONE JSON object
({"ok": true/false, ...}) to stdout; errors go to stderr in both modes.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .adapters import load_battery_json, load_keyless_json
from .store import SearchStore, SearchStoreError

DEFAULT_DB = "./searchstore.db"


# --------------------------------------------------------------------------- helpers


def _open_store(args: argparse.Namespace) -> SearchStore:
    db_path = str(args.db)
    if os.path.isdir(db_path):
        raise IsADirectoryError(f"db path is a directory: {db_path}")
    return SearchStore(db_path)


def _read_text(file_arg: str | None, text_arg: str | None) -> str:
    if file_arg is not None:
        return Path(file_arg).read_text(encoding="utf-8")
    return text_arg or ""


def _load_json_array(path: str) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON array in {path}, got {type(data).__name__}")
    return data


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- commands


def _cmd_init(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with _open_store(args) as store:
        schema_version = store.stats()["schema_version"]
    payload = {"ok": True, "db": str(args.db), "schema_version": schema_version}
    return payload, [f"initialized {args.db} (schema v{schema_version})"]


def _cmd_ingest_doc(args: argparse.Namespace) -> tuple[dict, list[str]]:
    text = _read_text(args.file, args.text)
    with _open_store(args) as store:
        doc_id = store.ingest_document(
            args.url, text, title=args.title, provider=args.provider, fetched_at=args.fetched_at
        )
    return {"ok": True, "doc_id": doc_id, "url": args.url}, [f"document {doc_id} {args.url}"]


def _cmd_ingest_battery(args: argparse.Namespace) -> tuple[dict, list[str]]:
    data = load_battery_json(args.path)
    with _open_store(args) as store:
        searches = 0
        for search in data["searches"]:
            meta = dict(search.get("meta") or {})
            meta["result_count"] = search.get("result_count", 0)
            store.ingest_search(
                search["query"],
                [],
                provider=search.get("provider"),
                ts=search.get("ts"),
                latency_ms=search.get("latency_ms"),
                meta=meta,
            )
            searches += 1
        events = 0
        for event in data["events"]:
            store.record_event(event["kind"], event.get("payload"))
            events += 1
    payload = {"ok": True, "searches": searches, "events": events}
    return payload, [f"ingested {searches} searches, {events} events from {args.path}"]


def _cmd_ingest_keyless(args: argparse.Namespace) -> tuple[dict, list[str]]:
    data = load_keyless_json(args.path)
    with _open_store(args) as store:
        events = 0
        for event in data["events"]:
            store.record_event(event["kind"], event.get("payload"))
            events += 1
    return {"ok": True, "events": events}, [f"recorded {events} events from {args.path}"]


def _cmd_ingest_report(args: argparse.Namespace) -> tuple[dict, list[str]]:
    text = _read_text(args.file, args.text)
    report_path = str(args.file) if args.file is not None else None
    sources = _load_json_array(args.sources) if args.sources is not None else None
    with _open_store(args) as store:
        report_id = store.ingest_report(args.slug, path=report_path, title=args.title, text=text, sources=sources)
    return {"ok": True, "report_id": report_id, "slug": args.slug}, [f"report {report_id} {args.slug}"]


def _cmd_search(args: argparse.Namespace) -> tuple[dict, list[str]]:
    query_vector = None
    if args.mode == "hybrid":
        if args.query_vector is None:
            raise ValueError("hybrid mode requires --query-vector JSONFILE")
        query_vector = _load_json_array(args.query_vector)
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in query_vector):
            raise ValueError(f"--query-vector must be a JSON array of numbers: {args.query_vector}")
    with _open_store(args) as store:
        results = store.search(args.query, limit=args.limit, mode=args.mode, query_vector=query_vector)
    payload = {"ok": True, "count": len(results), "results": results}
    human = [f"{len(results)} results for {args.query!r}"]
    human += [f"  {row['score']:.4f}  {row.get('title') or row.get('url')}" for row in results]
    return payload, human


def _cmd_stats(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with _open_store(args) as store:
        stats = store.stats()
    return {"ok": True, **stats}, [f"{key}: {value}" for key, value in stats.items()]


def _cmd_export(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with _open_store(args) as store:
        rows = store.export(args.table, args.out, format=args.format)
    payload = {"ok": True, "table": args.table, "rows": rows, "out": str(args.out)}
    return payload, [f"exported {rows} rows from {args.table} to {args.out}"]


def _cmd_rebuild_fts(args: argparse.Namespace) -> tuple[dict, list[str]]:
    with _open_store(args) as store:
        rebuilt = store.rebuild_fts()
    return {"ok": True, "rebuilt": rebuilt}, [f"rebuilt fts index ({rebuilt} documents)"]


# --------------------------------------------------------------------------- parser


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print one JSON object to stdout")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="searchstore", description="SearchStore CLI (contract section 4)")
    parser.add_argument(
        "--db", default=DEFAULT_DB, metavar="PATH", help=f"path to the SQLite db (default: {DEFAULT_DB})"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create + migrate the database")
    _add_json(p)
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser("ingest-doc", help="ingest one document")
    p.add_argument("--url", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", metavar="F")
    group.add_argument("--text", metavar="T")
    p.add_argument("--title", metavar="T")
    p.add_argument("--provider", metavar="P")
    p.add_argument("--fetched-at", metavar="ISO")
    _add_json(p)
    p.set_defaults(func=_cmd_ingest_doc)

    p = sub.add_parser("ingest-battery", help="ingest a battery-run JSON file (searches + events)")
    p.add_argument("path")
    _add_json(p)
    p.set_defaults(func=_cmd_ingest_battery)

    p = sub.add_parser("ingest-keyless", help="ingest a keyless-run JSON file (events only)")
    p.add_argument("path")
    _add_json(p)
    p.set_defaults(func=_cmd_ingest_keyless)

    p = sub.add_parser("ingest-report", help="ingest a report")
    p.add_argument("--slug", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", metavar="F")
    group.add_argument("--text", metavar="T")
    p.add_argument("--title", metavar="T")
    p.add_argument("--sources", metavar="JSONFILE")
    _add_json(p)
    p.set_defaults(func=_cmd_ingest_report)

    p = sub.add_parser("search", help="search documents")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--mode", choices=["fts", "hybrid"], default="fts")
    p.add_argument("--query-vector", metavar="JSONFILE")
    _add_json(p)
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("stats", help="print database statistics")
    _add_json(p)
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("export", help="export a table")
    p.add_argument("table")
    p.add_argument("--out", required=True)
    p.add_argument("--format", choices=["jsonl", "md"], default="jsonl")
    _add_json(p)
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser("rebuild-fts", help="rebuild the FTS index")
    _add_json(p)
    p.set_defaults(func=_cmd_rebuild_fts)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return the process exit code (0 ok · 1 data error · 2 usage/IO)."""
    args = _build_parser().parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        payload, human = args.func(args)
    except SearchStoreError as exc:
        _emit_error(exc, json_mode)
        return 1
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        _emit_error(exc, json_mode)
        return 2
    except sqlite3.Error as exc:
        _emit_error(exc, json_mode)
        return 2
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in human:
            print(line)
    return 0
