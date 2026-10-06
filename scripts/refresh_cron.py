#!/usr/bin/env python3
"""Weekly cron runner for the vn_geo refresh engine (watchdog pattern).

Runs ``python -m vn_geo.refresh run`` against the production store and prints
a short report ONLY when data changed or a source errored — empty stdout
means the cron job sends nothing (all quiet). Every run appends one line to
``data/refresh.log``. Wired to the Hermes cron job "vn-geo weekly refresh"
(every Monday 08:00).

Usage: python scripts/refresh_cron.py
"""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
DB = REPO / "data" / "vn-geo.db"
CONFIG = REPO / "analysis" / "refresh-areas.json"
LOG = REPO / "data" / "refresh.log"


def main() -> int:
    proc = subprocess.run(
        [str(PYTHON), "-m", "vn_geo.refresh", "run", "--db", str(DB), "--config", str(CONFIG), "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=900,
    )
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = None

    if isinstance(report, dict):
        totals = report.get("totals", {})
        log_line = (
            f"{now} new={totals.get('new')} updated={totals.get('updated')}"
            f" unchanged={totals.get('unchanged')} errors={totals.get('errors')}"
        )
    else:
        log_line = f"{now} FAILED rc={proc.returncode} stderr={proc.stderr.strip()[:200]}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(log_line + "\n")

    if not isinstance(report, dict):
        print(f"vn-geo refresh FAILED (rc={proc.returncode}): {proc.stderr.strip()[:400]}")
        return 1
    totals = report.get("totals", {})
    changed = int(totals.get("new", 0)) + int(totals.get("updated", 0))
    errors = int(totals.get("errors", 0))
    if errors:
        print(f"vn-geo refresh: {errors} source error(s) — {json.dumps(report, ensure_ascii=False)[:600]}")
        return 1
    if changed:
        print(
            f"vn-geo refresh: +{totals.get('new', 0)} new · {totals.get('updated', 0)} updated"
            f" · {totals.get('unchanged', 0)} unchanged (store: data/vn-geo.db)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
