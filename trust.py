#!/usr/bin/env python3
"""trust.py — R6-B source trust scoring (P3).

Deterministic, stdlib-only, offline scorer for research sources. Domain tiers
mirror ``fact_check.py`` (ordering ``primary > news > aggregator > unknown``,
``www.`` stripped, first suffix match wins) so outputs stay consistent with the
verification pass; demotion values follow the frozen contract
``analysis/r6-interfaces.md`` §4.

Outputs
-------
* ``trust_report.v1`` — rich per-source view (§2.3): url, host, score, tier,
  reasons, inputs, plus a summary (n, mean_score).
* ``trust.json`` — host overrides compatible with ``fact_check.py --trust``
  (§2.2): ``{"host": {"score": <0..1>, "note": "auto (R6-B)"}}``. Per host the
  MIN score of its sources is used; a ``{"score": x}`` override sets the final
  score directly in fact_check.py (mechanical demotions suppressed).

CLI
---
    python trust.py rank --sources s.json [--ledger l.json]
        [--out trust.json] [--report r.json] [--json]
    python trust.py score --url U [--served-by X] [--snippet-only]
        [--backend-error] [--json]
    python trust.py explain --trust t.json [--host H]

Exit codes: 0 ok · 2 usage/IO (missing/unreadable files, malformed JSON,
bad args). Inputs follow ``trust_sources.v1`` (§2.5); ``--ledger`` accepts the
grounded-citations ledger format (``evals/answer_quality/ledger_*.json``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

SCHEMA_REPORT = "trust_report.v1"
SCHEMA_SOURCES = "trust_sources.v1"

# Tier base scores — mirror fact_check.py::TIER_SCORE (frozen ordering).
TIER_SCORE = {"primary": 0.95, "news": 0.80, "aggregator": 0.55, "unknown": 0.30}

# Extra tier for blocklisted hosts (see §2.3: primary|news|aggregator|unknown|blocked).
BLOCKED_TIER = "blocked"
BLOCKED_SCORE = 0.0

# Demotion values (r6-interfaces.md §4).
SNIPPET_DEMOTION = 0.10  # snippet_only
RESCUED_DEMOTION = 0.15  # served_by == "rescued_from"
BACKEND_ERROR_DEMOTION = 0.30  # backend_error
UNKNOWN_HOST_DEMOTION = 0.05  # host not in any tier suffix

# Host-suffix table, checked in order — first match wins. Mirrors
# fact_check.py::_DOMAIN_TIERS so tier resolution stays identical.
_DOMAIN_TIERS = (
    (
        "primary",
        (
            "gov",
            "edu",
            "gov.uk",
            "ac.uk",
            "edu.au",
            "gov.au",
            "gc.ca",
            "gouv.fr",
            "bund.de",
            "europa.eu",
            "un.org",
            "who.int",
            "nato.int",
            "nih.gov",
            "cdc.gov",
            "fda.gov",
            "nasa.gov",
            "noaa.gov",
            "sec.gov",
            "arxiv.org",
            "biorxiv.org",
            "doi.org",
            "pubmed.ncbi.nlm.nih.gov",
            "nature.com",
            "science.org",
            "nejm.org",
            "thelancet.com",
            "gov.vn",
            "chinhphu.vn",
            "thuvienphapluat.vn",
            "vbpl.vn",
        ),
    ),
    (
        "news",
        (
            "reuters.com",
            "apnews.com",
            "ap.org",
            "afp.com",
            "bbc.com",
            "bbc.co.uk",
            "nytimes.com",
            "wsj.com",
            "washingtonpost.com",
            "theguardian.com",
            "ft.com",
            "bloomberg.com",
            "economist.com",
            "npr.org",
            "dw.com",
            "france24.com",
            "aljazeera.com",
            "cnn.com",
            "politico.com",
            "arstechnica.com",
            "wired.com",
            "theverge.com",
            "statnews.com",
            "vnexpress.net",
            "tuoitre.vn",
            "thanhnien.vn",
            "nld.com.vn",
            "vietnamnet.vn",
            "dantri.com.vn",
            "laodong.vn",
            "plo.vn",
            "cafef.vn",
            "vneconomy.vn",
            "znews.vn",
            "genk.vn",
            "ictnews.vn",
            "vtv.vn",
            "vov.vn",
        ),
    ),
    (
        "aggregator",
        (
            "wikipedia.org",
            "britannica.com",
            "news.google.com",
            "news.yahoo.com",
            "msn.com",
            "yahoo.com",
            "bing.com",
        ),
    ),
)

# Seed blocklist — known fake/deceptive domains caught in eval cases.
# eathealthy365.blogspot.com is the snippet-only UGC blog from
# evals/answer_quality/case_eathealthy365.json (health-misinformation case).
# Extend as new eval cases land; matching is exact-host or subdomain.
BLOCKED_HOSTS = frozenset(
    {
        "eathealthy365.blogspot.com",
    }
)

# Ledger-entry keys indicating a full-page extract exists on disk, and the
# kind values that count as extracts — mirror fact_check.py's snippet rule.
_EXTRACT_KEYS = ("extracted", "fetched", "full_text", "fulltext", "text_path", "extract_path", "cache_path")
_EXTRACT_KINDS = {"extract", "extracted", "full_text", "fulltext", "webpage", "page"}


class UsageError(Exception):
    """Bad input file / unreadable JSON — maps to exit code 2."""


# ---------------------------------------------------------------------------
# Host helpers (mirror fact_check.py)
# ---------------------------------------------------------------------------


def _normalize_host(host: str) -> str:
    """Lowercase, strip a trailing dot and a leading ``www.``."""
    host = str(host or "").lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _host_of(url: str) -> str:
    """Normalized hostname of a URL (``""`` when missing/unparseable)."""
    try:
        host = urlparse(str(url or "")).hostname or ""
    except ValueError:
        host = ""
    return _normalize_host(host)


def _matches(host: str, suffix: str) -> bool:
    """True when ``host`` equals ``suffix`` or is a subdomain of it."""
    return host == suffix or host.endswith("." + suffix)


def _table_tier(host: str) -> str:
    """Resolve the tier for a host via the suffix table (else ``unknown``)."""
    for tier, suffixes in _DOMAIN_TIERS:
        if any(_matches(host, s) for s in suffixes):
            return tier
    return "unknown"


def _is_blocked(host: str) -> bool:
    return any(_matches(host, bad) for bad in BLOCKED_HOSTS)


# ---------------------------------------------------------------------------
# Scoring (r6-interfaces.md §4)
# ---------------------------------------------------------------------------


def score_source(src: dict) -> dict:
    """Score one source (a ``trust_sources.v1`` item) -> report entry.

    Tier base by domain class, then demotions: ``snippet_only`` −0.10,
    ``served_by="rescued_from"`` −0.15, ``backend_error`` −0.30, unknown host
    −0.05. Blocklisted hosts score 0.0 flat (tier ``blocked``). Score is
    clamped to 0..1 and rounded to 2 decimals.
    """
    url = str(src.get("url") or "")
    host = _host_of(url)
    served_by = src.get("served_by")
    snippet_only = bool(src.get("snippet_only"))
    backend_error = bool(src.get("backend_error"))
    reasons: list[str] = []
    if _is_blocked(host):
        tier = BLOCKED_TIER
        score = BLOCKED_SCORE
        reasons.append("blocked_host")
    else:
        tier = _table_tier(host)
        score = TIER_SCORE[tier]
        if snippet_only:
            score -= SNIPPET_DEMOTION
            reasons.append("snippet_only")
        if served_by == "rescued_from":
            score -= RESCUED_DEMOTION
            reasons.append("rescued_from")
        if backend_error:
            score -= BACKEND_ERROR_DEMOTION
            reasons.append("backend_error")
        if tier == "unknown":
            score -= UNKNOWN_HOST_DEMOTION
            reasons.append("unknown_host")
        score = max(0.0, min(1.0, score))
    return {
        "url": url,
        "host": host,
        "score": round(score, 2),
        "tier": tier,
        "reasons": reasons,
        "inputs": {
            "served_by": served_by,
            "snippet_only": snippet_only,
            "backend_error": backend_error,
        },
    }


def score_sources(sources: list[dict]) -> dict:
    """Score a list of sources -> ``trust_report.v1`` (§2.3)."""
    entries = [score_source(s) for s in sources]
    n = len(entries)
    mean = round(sum(e["score"] for e in entries) / n, 2) if n else 0.0
    return {
        "version": 1,
        "sources": entries,
        "summary": {"n": n, "mean_score": mean},
    }


def host_overrides(report: dict) -> dict:
    """``trust.json`` host overrides (§2.2): per host the MIN score of its
    sources, ``note: "auto (R6-B)"``."""
    overrides: dict[str, dict] = {}
    for entry in report.get("sources", []):
        host = str(entry.get("host") or "")
        if not host:
            continue
        score = float(entry["score"])
        if host not in overrides or score < overrides[host]["score"]:
            overrides[host] = {"score": score, "note": "auto (R6-B)"}
    return overrides


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> object:
    """Read a JSON file; raise UsageError on IO/parse failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"{path}: malformed JSON: {exc}") from exc


def _load_sources(path: Path) -> list[dict]:
    """Load a ``trust_sources.v1`` (or grounded-citations ledger) envelope:
    a JSON object with a ``sources`` list of objects."""
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise UsageError(f"{path}: expected a JSON object with a 'sources' list ({SCHEMA_SOURCES})")
    for item in data["sources"]:
        if not isinstance(item, dict):
            raise UsageError(f"{path}: every 'sources' entry must be a JSON object")
    return data["sources"]


def _ledger_is_snippet_only(entry: dict) -> bool:
    """Mirror fact_check.py::_is_snippet_only: ``quotes[]`` or an explicit
    extract marker/path proves a full fetch; anything else is snippet-only."""
    if entry.get("quotes"):
        return False
    if any(entry.get(k) for k in _EXTRACT_KEYS):
        return False
    kind = (
        str(entry.get("kind") or entry.get("source_type") or entry.get("provenance") or entry.get("origin") or "")
        .strip()
        .lower()
    )
    if kind in _EXTRACT_KINDS:
        return False
    return True


def _merge_ledger(sources: list[dict], ledger_path: Path | None) -> list[dict]:
    """Fold grounded-citations ledger signals into trust_sources.v1 sources.

    Ledger entries are matched to sources by exact URL. ``snippet_only`` and
    ``backend_error`` are OR-ed (a ledger entry with no quotes and no extract
    marker counts as snippet-only); ``served_by`` is filled from the ledger
    when the source lacks one.
    """
    if ledger_path is None:
        return [dict(s) for s in sources]
    by_url = {str(e.get("url") or ""): e for e in _load_sources(ledger_path)}
    merged: list[dict] = []
    for src in sources:
        out = dict(src)
        entry = by_url.get(str(src.get("url") or ""))
        if entry is not None:
            out["snippet_only"] = (
                bool(out.get("snippet_only")) or bool(entry.get("snippet_only")) or _ledger_is_snippet_only(entry)
            )
            out["backend_error"] = bool(out.get("backend_error")) or bool(entry.get("backend_error"))
            if out.get("served_by") is None and entry.get("served_by") is not None:
                out["served_by"] = entry.get("served_by")
        merged.append(out)
    return merged


def _write_json(path: Path, payload: object) -> None:
    try:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot write {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_rank(args: argparse.Namespace) -> int:
    sources = _merge_ledger(_load_sources(args.sources), args.ledger)
    report = score_sources(sources)
    overrides = host_overrides(report)
    if args.out:
        _write_json(args.out, overrides)
    if args.report:
        _write_json(args.report, report)
    if args.json or not (args.out or args.report):
        print(json.dumps(overrides, indent=2, ensure_ascii=False))
    else:
        print(f"scored {report['summary']['n']} sources -> {len(overrides)} host overrides")
        for host in sorted(overrides):
            print(f"  {host}: {overrides[host]['score']:.2f}")
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    src = {
        "url": args.url,
        "served_by": args.served_by,
        "snippet_only": args.snippet_only,
        "backend_error": args.backend_error,
    }
    entry = score_source(src)
    if args.json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    else:
        reasons = ", ".join(entry["reasons"]) or "-"
        print(
            f"{entry['url']}  host={entry['host']}  score={entry['score']:.2f}  tier={entry['tier']}  reasons={reasons}"
        )
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    trust = _load_json(args.trust)
    if not isinstance(trust, dict):
        raise UsageError(f"{args.trust}: expected a JSON object keyed by host")
    query = _normalize_host(args.host) if args.host else None
    shown = 0
    for key in sorted(trust):
        if query and not _matches(_normalize_host(key), query):
            continue
        spec = trust[key]
        if isinstance(spec, dict):
            note = spec.get("note")
            line = f"{key}: {spec.get('score', '?')}" + (f" ({note})" if note else "")
        else:
            line = f"{key}: {spec}"
        print(line)
        shown += 1
    if query and not shown:
        print(f"no override matches host '{args.host}'")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trust.py",
        description="R6-B source trust scoring: rank a source list into trust.json "
        "host overrides, score a single URL, or explain a trust.json.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rank = sub.add_parser("rank", help="score a trust_sources.v1 file into trust.json / trust_report.v1")
    p_rank.add_argument("--sources", required=True, type=Path, help="trust_sources.v1 JSON input")
    p_rank.add_argument("--ledger", type=Path, help="optional grounded-citations ledger JSON")
    p_rank.add_argument("--out", type=Path, help="write trust.json host overrides here")
    p_rank.add_argument("--report", type=Path, help="write the trust_report.v1 JSON here")
    p_rank.add_argument("--json", action="store_true", help="print the trust.json JSON to stdout")

    p_score = sub.add_parser("score", help="score a single URL")
    p_score.add_argument("--url", required=True, help="source URL to score")
    p_score.add_argument("--served-by", help="provenance marker, e.g. rescued_from")
    p_score.add_argument("--snippet-only", action="store_true", help="source is snippet-only (no full extract)")
    p_score.add_argument("--backend-error", action="store_true", help="source was served after a backend error")
    p_score.add_argument("--json", action="store_true", help="emit the report entry as JSON")

    p_explain = sub.add_parser("explain", help="show host overrides from a trust.json")
    p_explain.add_argument("--trust", required=True, type=Path, help="trust.json sidecar to explain")
    p_explain.add_argument("--host", help="only show overrides matching this host (exact or parent domain)")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "rank":
            return _cmd_rank(args)
        if args.cmd == "score":
            return _cmd_score(args)
        return _cmd_explain(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
