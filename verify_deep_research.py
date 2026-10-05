#!/usr/bin/env python3
"""verify_deep_research.py -- mechanical C1-C9 acceptance subset for a
deep-research report file.

Prints one line per acceptance check (PASS / FAIL / INFO / SKIP) and exits
non-zero if any file-checkable rule fails. Only the file itself is inspected:
ledger consistency (C2) stays with ``sources.py verify``; honesty/gaps (C6),
runtime + politeness (C8) and the config constraints (C9) are decided by
live-run logs and review, not by this script. Fully offline, stdlib only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import deep_research as dr

_TITLES = {
    "C1": "structure",
    "C2": "sources",
    "C3": "citation style",
    "C4": "prose",
    "C5": "scale",
    "C6": "honesty / gaps",
    "C7": "language",
    "C8": "runtime + politeness",
    "C9": "constraints",
}

_NOT_FILE_CHECKABLE = {
    "C6": "needs judgement: gaps section and [unverified] markers",
    "C8": "measured on live runs (< 15 min, >= 1.5s gaps between calls)",
    "C9": "config review: backend auto, no ddgs, firecrawl stays paid",
}

_ORDER = [f"C{i}" for i in range(1, 10)]

_C2_NOTE = "ledger consistency (C2) stays with `sources.py verify` (not file-checkable here)"


def _rollup(results, cid: str) -> tuple[str, str]:
    """Collapse the granular results of one C-group into (status, detail)."""
    sub = [r for r in results if r.check_id == cid or r.check_id.startswith(cid + ".")]
    if not sub:
        return "SKIP", _NOT_FILE_CHECKABLE[cid]
    fails = [r for r in sub if r.status == "FAIL"]
    if fails:
        return "FAIL", "; ".join(f"{r.check_id}: {r.detail}" for r in fails)
    infos = [r for r in sub if r.status == "INFO"]
    if infos:
        return "INFO", "; ".join(r.detail for r in infos)
    return "PASS", "; ".join(r.detail for r in sub)


def main(argv=None) -> int:
    dr._utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="verify_deep_research.py",
        description="Mechanical C1-C9 acceptance subset on a deep-research report file (offline).",
    )
    parser.add_argument("report", type=Path, help="path to the report markdown file")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also print every granular sub-check under its C-group",
    )
    args = parser.parse_args(argv)
    if not args.report.is_file():
        parser.error(f"report file not found: {args.report}")

    results = dr.check(args.report.read_text(encoding="utf-8"))
    failed = False
    for cid in _ORDER:
        status, detail = _rollup(results, cid)
        if cid == "C2":
            detail = f"{detail} | {_C2_NOTE}"
        failed |= status == "FAIL"
        print(f"{status:<4} {cid} {_TITLES[cid]:<22} {detail}")
        if args.verbose:
            for r in results:
                if r.check_id == cid or r.check_id.startswith(cid + "."):
                    print(f"     {r.status:<4} {r.check_id:<26} {r.detail}")
    print()
    print("Ledger consistency (C2) is checked by `sources.py verify`, e.g.:")
    print(
        '  python "$HERMES_HOME/skills/research/grounded-citations/scripts/sources.py"'
        " verify <report> --strict --min-coverage 0.5"
    )
    print("C6/C8/C9 are not file-checkable here and stay with review + live-run logs.")
    print(f"RESULT: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
