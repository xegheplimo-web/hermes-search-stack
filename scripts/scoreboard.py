#!/usr/bin/env python3
"""scoreboard.py -- quality/speed scoreboard for the Hermes web-layer batteries.

Reads ``results/battery_*.json`` and ``results/keyless_*.json`` (the newest
``--last N`` of each, default 5), aggregates per-run quality and speed
statistics, compares the latest run against the previous
``results/scoreboard.json`` for a trend, and writes
``analysis/scoreboard.md`` + ``results/scoreboard.json``.

Frozen contract: ``analysis/r6-interfaces.md`` section 6. Design rules:
  * stdlib-only, deterministic, no network;
  * malformed or missing input files produce a warning on stderr and are
    skipped -- the run still completes with exit code 0;
  * battery case schema: ``id``/``kind``/``pass``/``latency_s``/
    ``n_results``/``backends``/``error`` plus a ``totals{}`` block;
  * keyless case schema: ``id``/``pass``/``error``/``notes`` plus
    top-level ``live_calls`` and a ``totals{}`` block.

Run from the repo root::

    python scripts/scoreboard.py [--results-dir results]
        [--out-md analysis/scoreboard.md] [--out-json results/scoreboard.json]
        [--last 5] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = REPO_ROOT / "results"
DEFAULT_OUT_MD = REPO_ROOT / "analysis" / "scoreboard.md"
DEFAULT_OUT_JSON = REPO_ROOT / "results" / "scoreboard.json"
DEFAULT_LAST = 5

BATTERY_GLOB = "battery_*.json"
KEYLESS_GLOB = "keyless_*.json"
SCOREBOARD_NAME = "scoreboard.json"

# Float-dust guard for the nearest-rank ceil: a product that should be an
# exact integer (e.g. 0.9 * 10) must not ceil to the next rank.
_RANK_EPSILON = 1e-9


def _warn(message: str) -> None:
    """Print a warning to stderr; warnings never fail the run."""
    print(f"[warn] {message}", file=sys.stderr)


def _round(value: float | None, digits: int = 3) -> float | None:
    """Round ``value`` to ``digits`` decimals, passing None through."""
    return None if value is None else round(value, digits)


def _is_num(value: Any) -> bool:
    """True for int/float values (bool excluded)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def nearest_rank_percentile(values: list[float], percentile: float) -> float | None:
    """Return the nearest-rank percentile of ``values`` (None when empty).

    Nearest-rank definition: sort the values ascending, compute the 1-based
    rank ``ceil(percentile / 100 * n)`` (clamped to ``1..n``) and return the
    value at that rank. Example: ``[1, 2, 3, 4]`` gives p50 = 2 (rank
    ``ceil(0.5 * 4) = 2``) and p90 = 4 (rank ``ceil(0.9 * 4) = 4``).
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    rank = math.ceil(percentile / 100.0 * n - _RANK_EPSILON)
    rank = max(1, min(n, rank))
    return ordered[rank - 1]


def _load_json(path: Path) -> Any:
    """Load a JSON file, raising ValueError with context on any failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{path.name}: unreadable ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: malformed JSON ({exc})") from exc


def _kind_stats(cases: list[dict], kind: str) -> dict:
    """Aggregate one case kind: count, nearest-rank p50/p90 latency and,
    for search cases, the mean result count."""
    of_kind = [case for case in cases if case.get("kind") == kind]
    latencies = [case["latency_s"] for case in of_kind if _is_num(case.get("latency_s"))]
    stats: dict[str, Any] = {
        "n": len(of_kind),
        "p50": _round(nearest_rank_percentile(latencies, 50)),
        "p90": _round(nearest_rank_percentile(latencies, 90)),
    }
    if kind == "search":
        n_results = [case["n_results"] for case in of_kind if _is_num(case.get("n_results"))]
        stats["mean_n_results"] = _round(sum(n_results) / len(n_results), 2) if n_results else None
    return stats


def _backends_seen(cases: list[dict]) -> list[str]:
    """Sorted union of backend names across all cases."""
    return sorted({backend for case in cases for backend in (case.get("backends") or []) if isinstance(backend, str)})


def _case_totals(cases: list[dict], declared: Any, name: str) -> tuple[int, int, int, float | None]:
    """Recompute pass/fail/total from ``cases``.

    When a ``totals{}`` block is present but disagrees with the recomputed
    counts, warn and keep the recomputed values. ``elapsed_s`` is taken from
    the declared block when numeric (it cannot be recomputed).
    """
    total = len(cases)
    passed = sum(1 for case in cases if case.get("pass"))
    failed = total - passed
    elapsed = None
    if isinstance(declared, dict):
        recomputed = {"pass": passed, "fail": failed, "total": total}
        mismatch = [key for key, val in recomputed.items() if key in declared and declared[key] != val]
        if mismatch:
            _warn(f"{name}: declared totals disagree with cases ({', '.join(mismatch)}); recomputing")
        if _is_num(declared.get("elapsed_s")):
            elapsed = declared["elapsed_s"]
    return passed, failed, total, elapsed


def _base_run(path: Path) -> tuple[dict, list[dict]]:
    """Parse the shared run fields (file, generated, live_calls, totals)."""
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}: top-level JSON is not an object")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(f"{path.name}: missing 'cases' list")
    cases = [case for case in raw_cases if isinstance(case, dict)]
    passed, failed, total, elapsed = _case_totals(cases, payload.get("totals"), path.name)
    live_calls = payload.get("live_calls")
    run: dict[str, Any] = {
        "file": path.name,
        "generated": payload.get("generated"),
        "live_calls": int(live_calls) if _is_num(live_calls) else 0,
        "pass": passed,
        "fail": failed,
        "total": total,
        "elapsed_s": elapsed,
    }
    return run, cases


def parse_battery_run(path: Path | str) -> dict:
    """Parse one ``battery_*.json`` run file into an aggregate dict."""
    path = Path(path)
    run, cases = _base_run(path)
    kinds = sorted({case.get("kind") for case in cases if isinstance(case.get("kind"), str)})
    run["kinds"] = {kind: _kind_stats(cases, kind) for kind in kinds}
    run["backends"] = _backends_seen(cases)
    return run


def parse_keyless_run(path: Path | str) -> dict:
    """Parse one ``keyless_*.json`` run file (pass/fail + live_calls only)."""
    run, _cases = _base_run(Path(path))
    return run


def discover_runs(results_dir: Path | str, last: int) -> tuple[list[Path], list[Path]]:
    """Find battery/keyless run files, sorted oldest-to-newest by name,
    keeping only the newest ``last`` of each."""
    results_dir = Path(results_dir)
    batteries = sorted(results_dir.glob(BATTERY_GLOB), key=lambda p: p.name)
    keyless = sorted(results_dir.glob(KEYLESS_GLOB), key=lambda p: p.name)
    if last:
        batteries = batteries[-last:]
        keyless = keyless[-last:]
    return batteries, keyless


def load_runs(results_dir: Path | str, last: int) -> tuple[list[dict], list[dict]]:
    """Discover + parse runs; malformed files warn on stderr and are skipped."""
    battery_paths, keyless_paths = discover_runs(results_dir, last)
    batteries: list[dict] = []
    keyless: list[dict] = []
    for path in battery_paths:
        try:
            batteries.append(parse_battery_run(path))
        except ValueError as exc:
            _warn(str(exc))
    for path in keyless_paths:
        try:
            keyless.append(parse_keyless_run(path))
        except ValueError as exc:
            _warn(str(exc))
    return batteries, keyless


def _load_previous(path: Path) -> dict | None:
    """Load a previous scoreboard.json; malformed files warn and are ignored."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _warn(f"{path.name}: unreadable previous scoreboard ({exc}); ignoring")
        return None
    if not isinstance(data, dict):
        _warn(f"{path.name}: previous scoreboard is not an object; ignoring")
        return None
    return data


def _delta(current: Any, previous: Any) -> float | None:
    """Difference ``current - previous`` when both are numeric, else None."""
    if _is_num(current) and _is_num(previous):
        return round(current - previous, 3)
    return None


def _kind_p50(run: dict | None, kind: str) -> float | None:
    """Pull ``kinds.<kind>.p50`` out of a run dict, tolerating missing keys."""
    if not isinstance(run, dict):
        return None
    kinds = run.get("kinds")
    if not isinstance(kinds, dict):
        return None
    stats = kinds.get(kind)
    if not isinstance(stats, dict):
        return None
    p50 = stats.get("p50")
    return p50 if _is_num(p50) else None


def _trend(latest: dict | None, previous: dict | None) -> dict:
    """Compare the latest run against the previous scoreboard's latest run."""
    prev_latest = previous.get("latest") if isinstance(previous, dict) else None
    if not isinstance(prev_latest, dict):
        prev_latest = None
    trend: dict[str, Any] = {
        "previous": previous.get("generated_at") if isinstance(previous, dict) else None,
        "previous_file": prev_latest.get("file") if prev_latest else None,
        "pass_delta": None,
        "fail_delta": None,
        "live_calls_delta": None,
        "p50_search_delta_s": None,
        "p50_extract_delta_s": None,
        "note": "",
    }
    if latest is None:
        trend["note"] = "no battery runs found"
        return trend
    if previous is None:
        trend["note"] = "no previous scoreboard.json found; latest run is the baseline"
        return trend
    if prev_latest is None:
        trend["note"] = "previous scoreboard has no 'latest' run to compare"
        return trend
    trend["pass_delta"] = _delta(latest.get("pass"), prev_latest.get("pass"))
    trend["fail_delta"] = _delta(latest.get("fail"), prev_latest.get("fail"))
    trend["live_calls_delta"] = _delta(latest.get("live_calls"), prev_latest.get("live_calls"))
    trend["p50_search_delta_s"] = _delta(_kind_p50(latest, "search"), _kind_p50(prev_latest, "search"))
    trend["p50_extract_delta_s"] = _delta(_kind_p50(latest, "extract"), _kind_p50(prev_latest, "extract"))
    if prev_latest.get("file") == latest.get("file"):
        trend["note"] = f"latest run unchanged since previous scoreboard ({latest.get('file')})"
    else:
        trend["note"] = f"latest run {latest.get('file')} vs previous {prev_latest.get('file')}"
    return trend


def build_scoreboard(
    results_dir: Path | str,
    last: int = DEFAULT_LAST,
    previous_path: Path | str | None = None,
) -> dict:
    """Assemble the full scoreboard payload for ``results_dir``.

    ``previous_path`` is the scoreboard.json to diff against for the trend;
    it defaults to ``<results_dir>/scoreboard.json``.
    """
    results_dir = Path(results_dir)
    runs, keyless = load_runs(results_dir, last)
    latest = runs[-1] if runs else None
    prev_path = Path(previous_path) if previous_path is not None else results_dir / SCOREBOARD_NAME
    previous = _load_previous(prev_path)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": runs,
        "keyless": keyless,
        "latest": latest,
        "trend": _trend(latest, previous),
    }


# ─── markdown rendering ──────────────────────────────────────────────────────


def _fmt_s(value: Any) -> str:
    """Format a seconds value with two decimals; em dash when absent."""
    return f"{value:.2f}" if _is_num(value) else "—"


def _fmt_int(value: Any) -> str:
    """Format a count; em dash when absent."""
    return str(value) if _is_num(value) else "—"


def _fmt_delta(value: Any, suffix: str = "") -> str:
    """Format a signed delta; 'n/a' when not computable."""
    if not _is_num(value):
        return "n/a"
    if isinstance(value, float):
        return f"{value:+.2f}{suffix}"
    return f"{value:+d}{suffix}"


def render_markdown(sb: dict, results_dir: Path | str) -> str:
    """Render the scoreboard payload as a Markdown report."""
    runs = sb.get("runs") or []
    keyless = sb.get("keyless") or []
    latest = sb.get("latest")
    trend = sb.get("trend") or {}
    lines: list[str] = [
        "# Hermes web-layer scoreboard",
        "",
        f"Source: `{results_dir}` — {len(runs)} battery run(s), {len(keyless)} keyless run(s) in window.",
        "",
        "## Latest battery run",
        "",
    ]
    if latest is None:
        lines.append("No battery runs found.")
    else:
        header = (
            f"`{latest.get('file')}` — generated `{latest.get('generated')}` · "
            f"live calls {_fmt_int(latest.get('live_calls'))} · "
            f"pass {_fmt_int(latest.get('pass'))} / fail {_fmt_int(latest.get('fail'))} / "
            f"total {_fmt_int(latest.get('total'))} · elapsed {_fmt_s(latest.get('elapsed_s'))}s"
        )
        lines.append(header)
        lines.append("")
        lines.append("| kind | n | p50 (s) | p90 (s) | mean n_results |")
        lines.append("| --- | --: | -----: | -----: | -------------: |")
        for kind, stats in (latest.get("kinds") or {}).items():
            mean = _fmt_s(stats.get("mean_n_results")) if "mean_n_results" in stats else "—"
            lines.append(
                f"| {kind} | {stats.get('n', 0)} | {_fmt_s(stats.get('p50'))} | {_fmt_s(stats.get('p90'))} | {mean} |"
            )
        backends = latest.get("backends") or []
        if backends:
            lines.append("")
            lines.append("Backends seen: " + ", ".join(f"`{b}`" for b in backends))
    lines.append("")

    if runs:
        kinds_seen = sorted({kind for run in runs for kind in (run.get("kinds") or {})})
        lines.append("### Battery runs in window")
        lines.append("")
        header = "| run | generated | pass | fail | total | live_calls |"
        divider = "| --- | --- | --: | --: | --: | --: |"
        for kind in kinds_seen:
            header += f" {kind} p50 (s) |"
            divider += " --: |"
        lines.append(header)
        lines.append(divider)
        for run in runs:
            row = (
                f"| `{run.get('file')}` | {run.get('generated')} | {_fmt_int(run.get('pass'))} "
                f"| {_fmt_int(run.get('fail'))} | {_fmt_int(run.get('total'))} "
                f"| {_fmt_int(run.get('live_calls'))} |"
            )
            for kind in kinds_seen:
                row += f" {_fmt_s(_kind_p50(run, kind))} |"
            lines.append(row)
        lines.append("")

    lines.append("## Cross-run trend")
    lines.append("")
    note = trend.get("note") or "no trend data"
    if trend.get("previous_file"):
        lines.append(
            f"vs `{trend['previous_file']}`: pass {_fmt_delta(trend.get('pass_delta'))} · "
            f"fail {_fmt_delta(trend.get('fail_delta'))} · "
            f"live_calls {_fmt_delta(trend.get('live_calls_delta'))} · "
            f"p50_search {_fmt_delta(trend.get('p50_search_delta_s'), 's')} · "
            f"p50_extract {_fmt_delta(trend.get('p50_extract_delta_s'), 's')}"
        )
        lines.append("")
    lines.append(f"_{note}_")
    lines.append("")

    lines.append("## Keyless runs")
    lines.append("")
    if not keyless:
        lines.append("No keyless runs in window.")
    else:
        lines.append("| run | generated | pass | fail | total | live_calls |")
        lines.append("| --- | --- | --: | --: | --: | --: |")
        for run in keyless:
            lines.append(
                f"| `{run.get('file')}` | {run.get('generated')} | {_fmt_int(run.get('pass'))} "
                f"| {_fmt_int(run.get('fail'))} | {_fmt_int(run.get('total'))} "
                f"| {_fmt_int(run.get('live_calls'))} |"
            )
    lines.append("")
    lines.append("---")
    lines.append(f"generated_at: {sb.get('generated_at')}")
    return "\n".join(lines) + "\n"


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _positive_int(text: str) -> int:
    """argparse type for ``--last``: must be an integer >= 1."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"--last must be >= 1, got {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scoreboard.py",
        description="Aggregate battery/keyless run files into a quality/speed scoreboard.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="directory holding battery_*.json / keyless_*.json (default: results)",
    )
    parser.add_argument(
        "--out-md", type=Path, default=DEFAULT_OUT_MD, help="markdown report path (default: analysis/scoreboard.md)"
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=DEFAULT_OUT_JSON,
        help="JSON output path; also read as the previous scoreboard (default: results/scoreboard.json)",
    )
    parser.add_argument(
        "--last",
        type=_positive_int,
        default=DEFAULT_LAST,
        help=f"keep only the newest N runs of each kind (default: {DEFAULT_LAST})",
    )
    parser.add_argument("--json", action="store_true", help="print the scoreboard JSON to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Always exits 0 unless arguments or output IO fail."""
    args = _build_parser().parse_args(argv)
    if not args.results_dir.is_dir():
        _warn(f"results dir not found: {args.results_dir}")
    sb = build_scoreboard(args.results_dir, args.last, previous_path=args.out_json)
    md = render_markdown(sb, args.results_dir)
    try:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(md, encoding="utf-8")
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(sb, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        _warn(f"could not write outputs: {exc}")
        return 2
    if args.json:
        print(json.dumps(sb, indent=2))
    else:
        print(
            f"scoreboard: {args.out_md} + {args.out_json} "
            f"({len(sb['runs'])} battery, {len(sb['keyless'])} keyless runs)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
