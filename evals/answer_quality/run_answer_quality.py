#!/usr/bin/env python3
"""Answer-quality golden-eval runner for ``fact_check.py`` (contract v1, R2-C).

Runs every ``case_<name>.json`` in this directory through ``fact_check.py`` in
``--judge fixture`` mode and asserts the FROZEN expectations from
``analysis/round2-interfaces.md`` §3.2.

Usage::

    python evals/answer_quality/run_answer_quality.py [--fact-check-path PATH] [--json]

The fact-check path defaults to ``<repo-root>/fact_check.py``; ``--json`` emits a
machine-readable result object instead of the human PASS/FAIL lines. The process
exits 0 iff every case passes (and at least one case was found). ``fact_check.py``
is built in parallel (R2-B) and may be absent while R2-C is authored — point
``--fact-check-path`` at a stub to validate this runner in isolation.

Each case's ``expect`` block is asserted against the parsed ``fact_check.v1``
JSON (and the subprocess exit code). Supported keys:

    exit_code         int|null   exact subprocess exit code to require
    flags_empty       bool|null  true -> no flags; false -> at least one flag
    flag_present      [ {id?, type_any?, sentence?} ]   each matched by a flag
    flag_absent_types [str]      no flag may carry any of these types
    claim_present     [ {verdict?, ids_include?, ids_any?} ]  each matched by a claim
    claims_all_verdict str|null  every claim verdict equals this (>=1 claim)
    summary_min       {key: int} each output.summary[key] >= value
    summary_exact     {key: int} each output.summary[key] == value
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).resolve().parent
REPO_ROOT = CASES_DIR.parents[1]
DEFAULT_FACT_CHECK = REPO_ROOT / "fact_check.py"


def _flag_matches(flag: dict[str, Any], spec: dict[str, Any]) -> bool:
    if spec.get("id") is not None and flag.get("id") != spec["id"]:
        return False
    if spec.get("type_any") and flag.get("type") not in spec["type_any"]:
        return False
    if spec.get("sentence") is not None and flag.get("sentence") != spec["sentence"]:
        return False
    return True


def _claim_matches(claim: dict[str, Any], spec: dict[str, Any]) -> bool:
    if spec.get("verdict") is not None and claim.get("verdict") != spec["verdict"]:
        return False
    ids = claim.get("ids") or []
    for wanted in spec.get("ids_include", []) or []:
        if wanted not in ids:
            return False
    if spec.get("ids_any") and not any(wanted in ids for wanted in spec["ids_any"]):
        return False
    return True


def evaluate(expect: dict[str, Any], payload: dict[str, Any], returncode: int) -> list[str]:
    """Return the list of failed assertions (empty list == pass)."""
    failures: list[str] = []
    flags = payload.get("flags") or []
    claims = payload.get("claims") or []
    summary = payload.get("summary") or {}

    if expect.get("exit_code") is not None and returncode != expect["exit_code"]:
        failures.append(f"exit_code {returncode} != {expect['exit_code']}")
    if expect.get("flags_empty") is True and flags:
        failures.append(f"expected no flags, got {[f.get('type') for f in flags]}")
    if expect.get("flags_empty") is False and not flags:
        failures.append("expected at least one flag, got none")
    for spec in expect.get("flag_present", []) or []:
        if not any(_flag_matches(f, spec) for f in flags):
            failures.append(f"no flag matched {spec}")
    absent = set(expect.get("flag_absent_types", []) or [])
    if absent:
        bad = [f.get("type") for f in flags if f.get("type") in absent]
        if bad:
            failures.append(f"unexpected flag type(s): {bad}")
    for spec in expect.get("claim_present", []) or []:
        if not any(_claim_matches(c, spec) for c in claims):
            failures.append(f"no claim matched {spec}")
    if expect.get("claims_all_verdict") is not None:
        want = expect["claims_all_verdict"]
        if not claims:
            failures.append("expected >=1 claim, got none")
        else:
            bad = [c.get("verdict") for c in claims if c.get("verdict") != want]
            if bad:
                failures.append(f"claims not all '{want}': {bad}")
    for key, value in (expect.get("summary_min", {}) or {}).items():
        got = summary.get(key)
        if got is None or got < value:
            failures.append(f"summary.{key}={got} < {value}")
    for key, value in (expect.get("summary_exact", {}) or {}).items():
        if summary.get(key) != value:
            failures.append(f"summary.{key}={summary.get(key)} != {value}")
    return failures


def run_case(case_path: Path, fact_check_path: Path) -> dict[str, Any]:
    """Run one case and return a result dict (name, pass, details, returncode)."""
    case = json.loads(case_path.read_text(encoding="utf-8"))
    name = case.get("name") or case_path.stem.removeprefix("case_")
    draft = case_path.parent / case["draft"]
    ledger = case_path.parent / case["ledger"]
    judge = case_path.parent / case["judge"]
    cmd = [
        sys.executable,
        str(fact_check_path),
        "--draft",
        str(draft),
        "--ledger",
        str(ledger),
        "--judge",
        "fixture",
        "--judge-fixture",
        str(judge),
        "--json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(fact_check_path.parent))
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        detail = f"could not parse JSON stdout ({exc}); stdout={proc.stdout[:400]!r}; stderr={proc.stderr[:200]!r}"
        return {"name": name, "pass": False, "details": [detail], "returncode": proc.returncode}
    failures = evaluate(case.get("expect", {}), payload, proc.returncode)
    return {"name": name, "pass": not failures, "details": failures, "returncode": proc.returncode}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_answer_quality.py",
        description="Run the answer-quality golden evals against fact_check.py.",
    )
    parser.add_argument(
        "--fact-check-path",
        default=str(DEFAULT_FACT_CHECK),
        help="path to fact_check.py (default: <repo-root>/fact_check.py)",
    )
    parser.add_argument(
        "--cases-dir",
        default=str(CASES_DIR),
        help="directory holding case_*.json (default: this directory)",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable JSON report")
    args = parser.parse_args(argv)

    fact_check_path = Path(args.fact_check_path).resolve()
    case_files = sorted(Path(args.cases_dir).glob("case_*.json"))

    results: list[dict[str, Any]] = []
    if not fact_check_path.is_file():
        for case_file in case_files:
            name = case_file.stem.removeprefix("case_")
            results.append(
                {
                    "name": name,
                    "pass": False,
                    "details": [f"fact_check.py not found at {fact_check_path}"],
                    "returncode": None,
                }
            )
    else:
        for case_file in case_files:
            results.append(run_case(case_file, fact_check_path))

    passed = sum(1 for result in results if result["pass"])
    total = len(results)
    ok = passed == total and total > 0

    if args.json:
        print(json.dumps({"results": results, "passed": passed, "total": total, "ok": ok}, indent=2))
    else:
        for result in results:
            tag = "PASS" if result["pass"] else "FAIL"
            details = "" if result["pass"] else " [" + "; ".join(result["details"]) + "]"
            print(f"{tag} {result['name']}{details}")
        print(f"summary: {passed}/{total} cases passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
