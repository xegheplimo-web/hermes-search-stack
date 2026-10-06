#!/usr/bin/env python3
"""Depth-escalation policy (P7) for the Hermes search stack.

Pure, deterministic, offline rule: decide whether a query deserves the
``deep`` research track or the ``fast`` track from lightweight signals
derived from the ``results/battery_*.json`` data model (search result
counts, extract char totals, backend errors, query markers).

Frozen decision table (see ``analysis/r6-interfaces.md`` section 5):

    +0.30  comparative marker
    +0.25  multi_part marker
    +0.20  deep-term hits >= 2
    +0.20  errors present AND char_sum < 4000
    +0.15  result_count sum < 6
    +0.10  char_sum < 2000
    -0.20  char_sum > 15000 AND result_count sum >= 10

``mode`` is ``"deep"`` when ``score >= 0.45``, else ``"fast"``.

Signals schema (section 2.4)::

    {"query": str,
     "search_result_counts": [int],
     "extract_char_totals": [int],
     "errors": [str],
     "query_markers": {"comparative": bool, "multi_part": bool, "vn": bool}}

Missing keys fall back to empty defaults. Wrong types raise
``TypeError``/``ValueError`` (the CLI maps these to exit code 2).

Stdlib only. No network. No config. No side effects.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# --- Frozen decision table (tunable constants) -------------------------------
COMPARATIVE_WEIGHT = 0.30
MULTI_PART_WEIGHT = 0.25
DEEP_TERM_WEIGHT = 0.20
ERRORS_THIN_WEIGHT = 0.20
FEW_RESULTS_WEIGHT = 0.15
TINY_CHARS_WEIGHT = 0.10
RICH_EVIDENCE_WEIGHT = -0.20
DEEP_THRESHOLD = 0.45

DEEP_TERMS: tuple[str, ...] = (
    "nghiên cứu sâu",
    "so sánh",
    "phân tích",
    "chi tiết",
    "toàn diện",
    "compare",
    "versus",
    "vs",
    "research",
)
DEEP_TERM_MIN_HITS = 2
FEW_RESULTS_MAX = 6
TINY_CHARS_MAX = 2000
THIN_CHARS_MAX = 4000
RICH_CHARS_MIN = 15000
RICH_RESULTS_MIN = 10

_VS_RE = re.compile(r"\bvs\b")


def _matched_deep_terms(query: str) -> list[str]:
    """Return the distinct deep-research terms found in ``query``.

    Matching is case-insensitive (via ``casefold``). Every term is a plain
    substring except ``"vs"``, which requires word boundaries so prose like
    ``"observations"`` does not count as a hit.
    """
    folded = query.casefold()
    matched: list[str] = []
    for term in DEEP_TERMS:
        if term == "vs":
            if _VS_RE.search(folded):
                matched.append(term)
        elif term in folded:
            matched.append(term)
    return matched


def _validate_signals(signals: object) -> tuple[str, int, int, bool, bool, bool, list[str]]:
    """Validate ``signals`` and return normalized components.

    Returns ``(query, result_sum, char_sum, has_errors, comparative,
    multi_part, deep_hits)``. Missing keys map to empty defaults
    (``""``, ``[]``, ``{}``/``False``). Wrong types raise ``TypeError``;
    negative counts raise ``ValueError``.
    """
    if not isinstance(signals, dict):
        raise TypeError("signals must be a JSON object")
    query = signals.get("query", "")
    counts = signals.get("search_result_counts", [])
    chars = signals.get("extract_char_totals", [])
    errors = signals.get("errors", [])
    markers = signals.get("query_markers", {})

    if not isinstance(query, str):
        raise TypeError("signals['query'] must be a string")
    if not isinstance(counts, list):
        raise TypeError("signals['search_result_counts'] must be a list of ints")
    if not isinstance(chars, list):
        raise TypeError("signals['extract_char_totals'] must be a list of ints")
    if not isinstance(errors, list):
        raise TypeError("signals['errors'] must be a list of strings")
    if not isinstance(markers, dict):
        raise TypeError("signals['query_markers'] must be an object")

    for value in counts:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("signals['search_result_counts'] must be a list of ints")
        if value < 0:
            raise ValueError("signals['search_result_counts'] must not contain negatives")
    for value in chars:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("signals['extract_char_totals'] must be a list of ints")
        if value < 0:
            raise ValueError("signals['extract_char_totals'] must not contain negatives")
    for value in errors:
        if not isinstance(value, str):
            raise TypeError("signals['errors'] must be a list of strings")

    comparative = markers.get("comparative", False)
    multi_part = markers.get("multi_part", False)
    for name, value in (("comparative", comparative), ("multi_part", multi_part)):
        if not isinstance(value, bool):
            raise TypeError(f"signals['query_markers']['{name}'] must be a boolean")
    vn = markers.get("vn", False)
    if "vn" in markers and not isinstance(vn, bool):
        raise TypeError("signals['query_markers']['vn'] must be a boolean")

    result_sum = sum(counts)
    char_sum = sum(chars)
    has_errors = any(item.strip() for item in errors)
    deep_hits = _matched_deep_terms(query)
    return query, result_sum, char_sum, has_errors, comparative, multi_part, deep_hits


def needs_depth(signals: dict) -> dict:
    """Apply the frozen decision table to ``signals``.

    Returns ``{"mode": "fast"|"deep", "score": float, "reasons": [str]}``
    where ``reasons`` names every rule that fired (including the rich
    evidence discount). The function is pure and deterministic: equal
    inputs always produce equal outputs, with ``score`` rounded to two
    decimals.
    """
    _, result_sum, char_sum, has_errors, comparative, multi_part, deep_hits = _validate_signals(signals)

    score = 0.0
    reasons: list[str] = []

    if comparative:
        score += COMPARATIVE_WEIGHT
        reasons.append(f"comparative query marker (+{COMPARATIVE_WEIGHT:.2f})")
    if multi_part:
        score += MULTI_PART_WEIGHT
        reasons.append(f"multi-part query marker (+{MULTI_PART_WEIGHT:.2f})")
    if len(deep_hits) >= DEEP_TERM_MIN_HITS:
        score += DEEP_TERM_WEIGHT
        reasons.append(f"deep-research terms matched ({', '.join(sorted(deep_hits))}) (+{DEEP_TERM_WEIGHT:.2f})")
    if has_errors and char_sum < THIN_CHARS_MAX:
        score += ERRORS_THIN_WEIGHT
        reasons.append(f"errors with thin evidence char_sum < {THIN_CHARS_MAX} (+{ERRORS_THIN_WEIGHT:.2f})")
    if result_sum < FEW_RESULTS_MAX:
        score += FEW_RESULTS_WEIGHT
        reasons.append(f"few search results sum < {FEW_RESULTS_MAX} (+{FEW_RESULTS_WEIGHT:.2f})")
    if char_sum < TINY_CHARS_MAX:
        score += TINY_CHARS_WEIGHT
        reasons.append(f"tiny extract chars sum < {TINY_CHARS_MAX} (+{TINY_CHARS_WEIGHT:.2f})")
    if char_sum > RICH_CHARS_MIN and result_sum >= RICH_RESULTS_MIN:
        score += RICH_EVIDENCE_WEIGHT
        reasons.append(
            f"rich evidence char_sum > {RICH_CHARS_MIN} with results >= {RICH_RESULTS_MIN} ({RICH_EVIDENCE_WEIGHT:.2f})"
        )

    score = round(score, 2)
    mode = "deep" if score >= DEEP_THRESHOLD else "fast"
    return {"mode": mode, "score": score, "reasons": reasons}


def render_table() -> str:
    """Render the frozen decision table as human-readable text."""
    lines = [
        "Depth-escalation policy (frozen decision table):",
        f"  +{COMPARATIVE_WEIGHT:.2f}  comparative query marker",
        f"  +{MULTI_PART_WEIGHT:.2f}  multi-part query marker",
        f"  +{DEEP_TERM_WEIGHT:.2f}  deep-term hits >= {DEEP_TERM_MIN_HITS} ({', '.join(DEEP_TERMS)})",
        f"  +{ERRORS_THIN_WEIGHT:.2f}  errors present AND char_sum < {THIN_CHARS_MAX}",
        f"  +{FEW_RESULTS_WEIGHT:.2f}  result_count sum < {FEW_RESULTS_MAX}",
        f"  +{TINY_CHARS_WEIGHT:.2f}  char_sum < {TINY_CHARS_MAX}",
        f"  {RICH_EVIDENCE_WEIGHT:.2f}  char_sum > {RICH_CHARS_MIN} AND result_count sum >= {RICH_RESULTS_MIN}",
        f"  mode = deep if score >= {DEEP_THRESHOLD:.2f} else fast",
    ]
    return "\n".join(lines)


def format_human(decision: dict) -> str:
    """Format a ``needs_depth`` decision for console output."""
    lines = [f"mode: {decision['mode']}", f"score: {decision['score']:.2f}", "reasons:"]
    if decision["reasons"]:
        lines.extend(f"  - {reason}" for reason in decision["reasons"])
    else:
        lines.append("  - (none)")
    return "\n".join(lines)


def _utf8_stdout() -> None:
    """Make console output UTF-8-safe on legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the depth-escalation policy."""
    parser = argparse.ArgumentParser(
        prog="depth_policy.py",
        description="Depth-escalation policy: decide fast vs deep from evidence signals.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_decide = sub.add_parser("decide", help="decide fast/deep from a signals JSON object")
    p_decide.add_argument("--signals", required=True, help="signals JSON object (see section 2.4)")
    p_decide.add_argument("--json", action="store_true", help="emit the decision as JSON")
    sub.add_parser("table", help="print the frozen decision table")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the depth-policy CLI; exit 0 on success, 2 on usage errors."""
    _utf8_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "table":
        print(render_table())
        return 0
    try:
        signals = json.loads(args.signals)
    except json.JSONDecodeError as exc:
        print(f"error: --signals is not valid JSON: {exc}", file=sys.stderr)
        return 2
    try:
        decision = needs_depth(signals)
    except (TypeError, ValueError) as exc:
        print(f"error: invalid signals: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    else:
        print(format_human(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
