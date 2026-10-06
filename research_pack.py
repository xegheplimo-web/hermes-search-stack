#!/usr/bin/env python3
"""research_pack.py — R7-A integration glue: build ``research_pack.v1`` packs.

Assembles a cacheable answer pack from real deep-research workflow artifacts:

* **ledger** — grounded-citations ledger
  ``{version, sources: [{id, url, title, accessed, quotes?}]``;
* **trust report** — ``trust_report.v1`` from ``trust.py rank --report``
  (``sources[]`` joined by normalized URL for ``trust_score``/``served_by``);
* **fact_check report** — ``fact_check.v1`` from ``fact_check.py --json``
  (``schema``, ``stats.coverage``, ``summary.pass`` -> ``verification``);
* **draft** — the report markdown (``answer_markdown``).

The field mapping and CLI are frozen in ``analysis/r7-interfaces.md`` §2/§3.
``info`` mirrors the ``searchstore.answer_cache`` put gate so a pack can be
checked before ``python -m searchstore.answer_cache put --pack pack.json``.
stdlib-only; does NOT import ``trust.py``, ``fact_check.py`` or
``searchstore`` — the trust join runs on their JSON outputs. No live calls.

CLI::

    python research_pack.py build --query Q --ledger L.json [--scope S]
        [--mode fast|deep] [--trust-report T.json] [--answer DRAFT.md]
        [--verification FC.json] [--min-coverage 0.5] [--ttl-days 14]
        (--out PACK.json | --stdout) [--json]
    python research_pack.py info PACK.json [--json]

Exit codes: 0 ok · 2 usage/IO (missing/unreadable/malformed input file, wrong
schema in ``--verification``, bad args, neither ``--out`` nor ``--stdout``).
``info`` exits 0 for a parseable pack even when the gate verdict is REJECT;
with ``build --stdout --json`` the pack owns stdout and the summary goes to
stderr so the pack stays pipeable.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "research_pack.v1"
FACT_CHECK_SCHEMA = "fact_check.v1"
_MODES = ("fast", "deep")

# mirrors searchstore.answer_cache (r7-interfaces §2/§3)
_REQUIRED_KEYS = ("schema", "query", "sources", "created_at", "ttl_days")


class UsageError(Exception):
    """Bad input file / unreadable JSON / bad args — maps to exit code 2."""


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def _load_json(path: Path, what: str):
    """Read a JSON file; raise UsageError on IO/parse failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise UsageError(f"{what} not found: {path}") from None
    except OSError as exc:
        raise UsageError(f"{what} unreadable: {path} ({exc})") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"{what} is not valid JSON: {path} ({exc})") from None


def _load_ledger(path: Path) -> list:
    """Load a grounded-citations ledger: a JSON object with a ``sources`` list."""
    data = _load_json(path, "ledger")
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise UsageError(f"{path}: expected a JSON object with a 'sources' list (grounded-citations ledger)")
    return data["sources"]


def _normalize_url(url: str) -> str:
    """Canonicalize a URL for the trust join — sources.py::normalize_url semantics.

    Strips the fragment and a trailing slash so ``/page``, ``/page/`` and
    ``/page#section`` are one source; query strings are significant and kept.
    Applied to BOTH sides of the join; no host fallback.
    """
    u = (url or "").strip()
    if "#" in u:
        u = u.split("#", 1)[0]
    stripped = u.rstrip("/")
    return stripped or u


def _load_trust_report(path: Path) -> dict[str, dict]:
    """Load a ``trust_report.v1`` file; index its sources by normalized URL."""
    data = _load_json(path, "trust report")
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise UsageError(f"{path}: expected a JSON object with a 'sources' list (trust_report.v1)")
    by_url: dict[str, dict] = {}
    for entry in data["sources"]:
        if isinstance(entry, dict) and entry.get("url"):
            by_url[_normalize_url(str(entry["url"]))] = entry
    return by_url


def _load_fact_check(path: Path) -> dict:
    """Load a ``fact_check.v1`` report; the ``schema`` tag is required."""
    data = _load_json(path, "verification report")
    if not isinstance(data, dict) or data.get("schema") != FACT_CHECK_SCHEMA:
        got = data.get("schema") if isinstance(data, dict) else type(data).__name__
        raise UsageError(f"{path}: expected schema {FACT_CHECK_SCHEMA!r}, got {got!r}")
    return data


def _load_answer(path: Path) -> str:
    """Read the draft markdown; raise UsageError on IO failure."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise UsageError(f"answer file not found: {path}") from None
    except OSError as exc:
        raise UsageError(f"answer file unreadable: {path} ({exc})") from None


# ---------------------------------------------------------------------------
# Pack assembly (r7-interfaces §2 mapping)
# ---------------------------------------------------------------------------


def _source_quote(entry: dict):
    """``quotes[0].text`` when the entry carries quotes, else ``None``."""
    quotes = entry.get("quotes")
    if isinstance(quotes, list) and quotes and isinstance(quotes[0], dict):
        return quotes[0].get("text")
    return None


def build_pack(
    *,
    query: str,
    ledger_sources: list,
    scope: str = "",
    mode: str = "deep",
    trust_by_url: dict[str, dict] | None = None,
    answer_markdown: str | None = None,
    fact_check_report: dict | None = None,
    min_coverage: float = 0.5,
    ttl_days: float = 14,
    now: datetime | None = None,
) -> tuple[dict, list[str]]:
    """Assemble a ``research_pack.v1`` dict (frozen mapping, r7-interfaces §2).

    Source order follows the ledger; entries with no ``url`` are skipped (one
    warning string per skip in the returned list). ``trust_score``/``served_by``
    join *trust_by_url* (normalized URL -> trust_report entry) by normalized
    URL only — no host fallback; a miss leaves both ``None``. ``verification``
    is emitted only when *fact_check_report* is given. ``provider`` is always
    ``None`` at build time. Returns ``(pack, warnings)``.
    """
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    trust_by_url = trust_by_url or {}
    sources: list[dict] = []
    warnings: list[str] = []
    for i, entry in enumerate(ledger_sources):
        url = entry.get("url") if isinstance(entry, dict) else None
        if not url:
            warnings.append(f"ledger source #{i} has no 'url' — skipped")
            continue
        t_entry = trust_by_url.get(_normalize_url(str(url)))
        served_by = None
        if t_entry is not None and isinstance(t_entry.get("inputs"), dict):
            served_by = t_entry["inputs"].get("served_by")
        if served_by is None:
            served_by = entry.get("served_by")
        sources.append(
            {
                "url": str(url),
                "title": entry.get("title") or None,
                "quote": _source_quote(entry),
                "provider": None,
                "served_by": served_by,
                "fetched_at": entry.get("accessed") or None,
                "trust_score": t_entry.get("score") if t_entry is not None else None,
            }
        )
    pack = {
        "schema": SCHEMA,
        "query": query,
        "scope": scope,
        "mode": mode,
        "answer_markdown": answer_markdown,
        "sources": sources,
        "created_at": stamp,
        "ttl_days": ttl_days,
    }
    if fact_check_report is not None:
        summary = fact_check_report.get("summary") or {}
        stats = fact_check_report.get("stats") or {}
        pack["verification"] = {
            "fact_check_exit": 0 if summary.get("pass") else 1,
            "coverage": stats.get("coverage"),
            "min_coverage": min_coverage,
            "verified_at": stamp,
        }
    return pack, warnings


# ---------------------------------------------------------------------------
# Put gate
# ---------------------------------------------------------------------------


def _is_verified(verification) -> bool:
    """``fact_check_exit == 0`` OR ``verified is True``."""
    # mirrors searchstore.answer_cache (r7-interfaces §2/§3)
    if not isinstance(verification, dict):
        return False
    return verification.get("fact_check_exit") == 0 or verification.get("verified") is True


def _put_gate(pack: object) -> str | None:
    """``None`` when *pack* would pass the answer-cache put gate, else the reason.

    mirrors searchstore.answer_cache (r7-interfaces §2/§3): the
    ``_validate_pack`` required keys (schema, query, sources, created_at,
    ttl_days), schema equality, non-empty query, numeric ttl — then the
    ``_is_verified`` gate.
    """
    if not isinstance(pack, dict):
        return f"pack must be a JSON object, got {type(pack).__name__}"
    missing = [k for k in _REQUIRED_KEYS if k not in pack]
    if missing:
        return f"pack missing required key(s): {', '.join(missing)}"
    if pack["schema"] != SCHEMA:
        return f"unsupported schema {pack['schema']!r}; expected {SCHEMA!r}"
    query = pack["query"]
    if not isinstance(query, str) or not query.strip():
        return "pack 'query' must be a non-empty string"
    ttl = pack["ttl_days"]
    if not isinstance(ttl, (int, float)) or isinstance(ttl, bool) or ttl < 0:
        return "pack 'ttl_days' must be a non-negative number"
    if not _is_verified(pack.get("verification")):
        return "pack is not verified (verification.fact_check_exit != 0 and verified is not true)"
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_build(args: argparse.Namespace) -> int:
    if not args.query.strip():
        raise UsageError("--query must be a non-empty string")
    if not math.isfinite(args.ttl_days) or args.ttl_days < 0:
        raise UsageError("--ttl-days must be a non-negative number")
    ledger_sources = _load_ledger(args.ledger)
    trust_by_url = _load_trust_report(args.trust_report) if args.trust_report else {}
    fc_report = _load_fact_check(args.verification) if args.verification else None
    answer_markdown = _load_answer(args.answer) if args.answer else None
    ttl = args.ttl_days
    if float(ttl).is_integer():
        ttl = int(ttl)
    pack, warnings = build_pack(
        query=args.query,
        ledger_sources=ledger_sources,
        scope=args.scope,
        mode=args.mode,
        trust_by_url=trust_by_url,
        answer_markdown=answer_markdown,
        fact_check_report=fc_report,
        min_coverage=args.min_coverage,
        ttl_days=ttl,
    )
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    payload = json.dumps(pack, indent=2, ensure_ascii=False) + "\n"
    if args.stdout:
        sys.stdout.write(payload)
    else:
        try:
            args.out.write_text(payload, encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot write --out {args.out} ({exc})") from None
    summary = {"stdout": True} if args.stdout else {"out": str(args.out)}
    summary.update(
        {
            "schema": SCHEMA,
            "query": args.query,
            "mode": args.mode,
            "sources_n": len(pack["sources"]),
            "skipped_n": len(warnings),
            "trust_scored_n": sum(1 for s in pack["sources"] if s["trust_score"] is not None),
            "verified": _is_verified(pack.get("verification")),
        }
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False), file=sys.stderr if args.stdout else sys.stdout)
    elif not args.stdout:
        print(
            f"wrote {args.out}: {summary['sources_n']} source(s), "
            f"{summary['skipped_n']} skipped, {summary['trust_scored_n']} trust-scored, "
            f"verified={summary['verified']}"
        )
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    pack = _load_json(args.pack, "pack")
    reason = _put_gate(pack)
    verdict = "PASS" if reason is None else "REJECT"
    p = pack if isinstance(pack, dict) else {}
    raw_sources = p.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    scores = [
        s["trust_score"]
        for s in sources
        if isinstance(s, dict)
        and isinstance(s.get("trust_score"), (int, float))
        and not isinstance(s["trust_score"], bool)
    ]
    info = {
        "schema": p.get("schema"),
        "query": p.get("query"),
        "scope": p.get("scope", ""),
        "mode": p.get("mode"),
        "created_at": p.get("created_at"),
        "ttl_days": p.get("ttl_days"),
        "sources_n": len(sources),
        "trust_scored_n": len(scores),
        "trust_mean": round(sum(scores) / len(scores), 2) if scores else None,
        "verified": _is_verified(p.get("verification")),
        "put_gate": verdict,
        "reason": reason,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
        return 0
    ver = p.get("verification")
    vline = " ".join(f"{k}={v}" for k, v in ver.items()) if isinstance(ver, dict) else "(none)"
    trust_line = f"{info['trust_scored_n']} scored, mean {info['trust_mean']}" if scores else "none scored"
    print(
        "\n".join(
            [
                f"schema:       {info['schema']}",
                f"query:        {info['query']}",
                f"scope:        {info['scope'] or '(none)'}",
                f"mode:         {info['mode']}",
                f"created_at:   {info['created_at']}",
                f"ttl_days:     {info['ttl_days']}",
                f"sources:      {info['sources_n']} ({trust_line})",
                f"verification: {vline}",
                f"put-gate:     {verdict}" + (f" — {reason}" if reason else ""),
            ]
        )
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_pack.py",
        description="Build research_pack.v1 packs from workflow artifacts, or inspect one (r7-interfaces §3).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="assemble a research_pack.v1 pack from ledger/trust/fact_check/draft")
    p.add_argument("--query", required=True, metavar="Q", help="the research question")
    p.add_argument("--ledger", required=True, type=Path, metavar="L.json", help="grounded-citations ledger JSON")
    p.add_argument("--scope", default="", metavar="S", help="cache scope partition (default: '')")
    p.add_argument("--mode", choices=_MODES, default="deep", help="pack mode (default: deep)")
    p.add_argument("--trust-report", type=Path, metavar="T.json", help="trust_report.v1 from trust.py rank --report")
    p.add_argument("--answer", type=Path, metavar="DRAFT.md", help="report markdown for answer_markdown")
    p.add_argument("--verification", type=Path, metavar="FC.json", help="fact_check.v1 report (adds verification)")
    p.add_argument(
        "--min-coverage",
        type=float,
        default=0.5,
        metavar="X",
        help="min coverage recorded in verification (default: 0.5)",
    )
    p.add_argument("--ttl-days", type=float, default=14, metavar="N", help="cache TTL in days (default: 14)")
    out = p.add_mutually_exclusive_group(required=True)
    out.add_argument("--out", type=Path, metavar="PACK.json", help="write the pack here")
    out.add_argument("--stdout", action="store_true", help="print the pack to stdout")
    p.add_argument("--json", action="store_true", help="print a build summary JSON")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("info", help="summarize a pack and print the put-gate verdict")
    p.add_argument("pack", type=Path, metavar="PACK.json")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.set_defaults(func=_cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return the process exit code (0 ok · 2 usage/IO)."""
    args = _build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
