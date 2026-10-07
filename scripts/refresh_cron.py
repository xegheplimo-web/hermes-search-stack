#!/usr/bin/env python3
"""Weekly cron runner for the vn_geo refresh engine + business seed (watchdog pattern).

Part 1 — geo refresh: runs ``python -m vn_geo.refresh run`` against the
production store and prints a short report ONLY when data changed or a source
errored. Part 2 — business seed: when ``business_enabled`` is true in the
areas config, seeds the areas flagged ``"business": true`` via
``python -m vn_geo business seed`` (a filtered config is written to
``data/business-cron-config.json``) and reports new entities / source errors
the same way. Empty stdout means the cron job sends nothing (all quiet).
Every run appends one line per part to ``data/refresh.log``. Wired to the
Hermes cron job "vn-geo weekly refresh" (every Monday 08:00).

Usage: python scripts/refresh_cron.py
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
DB = REPO / "data" / "vn-geo.db"
CONFIG = REPO / "analysis" / "refresh-areas.json"
LOG = REPO / "data" / "refresh.log"


def _log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _entity_count() -> int | None:
    if not DB.is_file():
        return 0  # fresh db — everything seeded counts as new
    try:
        with sqlite3.connect(DB) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    except Exception:
        return None


def run_refresh() -> tuple[int, list[str]]:
    """Geo refresh part; returns (rc, stdout report lines)."""
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
        _log(
            f"{now} new={totals.get('new')} updated={totals.get('updated')}"
            f" unchanged={totals.get('unchanged')} errors={totals.get('errors')}"
        )
    else:
        _log(f"{now} FAILED rc={proc.returncode} stderr={proc.stderr.strip()[:200]}")

    if not isinstance(report, dict):
        return 1, [f"vn-geo refresh FAILED (rc={proc.returncode}): {proc.stderr.strip()[:400]}"]
    totals = report.get("totals", {})
    changed = int(totals.get("new", 0)) + int(totals.get("updated", 0))
    errors = int(totals.get("errors", 0))
    if errors:
        return 1, [f"vn-geo refresh: {errors} source error(s) — {json.dumps(report, ensure_ascii=False)[:600]}"]
    if changed:
        return 0, [
            f"vn-geo refresh: +{totals.get('new', 0)} new · {totals.get('updated', 0)} updated"
            f" · {totals.get('unchanged', 0)} unchanged (store: data/vn-geo.db)"
        ]
    return 0, []


def run_business() -> tuple[int, list[str]]:
    """Business seed part (areas flagged ``business: true``); returns (rc, stdout lines)."""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, []  # config unreadable — the refresh part already reported it
    if not cfg.get("business_enabled"):
        return 0, []
    flagged = [a for a in cfg.get("areas", []) if isinstance(a, dict) and a.get("business")]
    if not flagged:
        return 0, []

    tmp_cfg = DB.parent / "business-cron-config.json"
    tmp_cfg.write_text(
        json.dumps({**cfg, "areas": flagged}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    before = _entity_count()
    try:
        proc = subprocess.run(
            [str(PYTHON), "-m", "vn_geo", "business", "seed", "--config", str(tmp_cfg), "--db", str(DB), "--json"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=7200,
        )
    except subprocess.TimeoutExpired:
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        _log(f"{now} business FAILED timeout=7200s")
        return 1, ["vn-geo business FAILED: seed exceeded 7200 s"]
    after = _entity_count()
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = None

    if not isinstance(report, dict) or not report.get("ok"):
        _log(f"{now} business FAILED rc={proc.returncode} stderr={proc.stderr.strip()[:200]}")
        return 1, [f"vn-geo business FAILED (rc={proc.returncode}): {proc.stderr.strip()[:400]}"]

    new = None if before is None or after is None else after - before
    skipped = sum(len(a.get("skipped_sources", [])) for a in report.get("areas", []) if isinstance(a, dict))
    _log(f"{now} business seeded={report.get('seeded', 0)} new={new} skipped={skipped}")

    if skipped:
        return 1, [f"vn-geo business: {skipped} source error(s) — {json.dumps(report, ensure_ascii=False)[:500]}"]
    if new:
        return 0, [f"vn-geo business: +{new} new entities ({report.get('seeded', 0)} records processed)"]
    return 0, []


def main() -> int:
    rc, lines = run_refresh()
    rc_b, lines_b = run_business()
    for line in lines + lines_b:
        print(line)
    return max(rc, rc_b)


if __name__ == "__main__":
    raise SystemExit(main())
