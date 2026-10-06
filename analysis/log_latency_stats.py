"""Log latency stats for Hermes agent.log (speed audit round 2).

Reads ``agent.log`` and emits, split at the round-1 quick-win cutoff
(default 2026-10-06 04:30 local):

(a) per-API-call latency by ``in=`` bucket (<50k, 50-100k, 100-150k,
    150-200k, >200k): n, p50, p90, max;
(b) pre-fix vs post-fix overall (n, p50, p90, max);
(c) tail-event counts, both periods: 429s, retry/wait, fallback,
    title-generation failures, search/extract cache hits;
(d) top-N slowest calls with the full log line.

Usage:
    python analysis/log_latency_stats.py [--log PATH] [--cutoff "YYYY-MM-DD HH:MM"]
        [--top N] [--json]

Markdown tables by default; ``--json`` emits a JSON document.
Deterministic: identical input always yields identical output
(sorted records, fixed float formatting, insertion-ordered keys).

API-call line format (example)::
    2026-10-06 06:41:20,442 INFO [session] agent.conversation_loop: API call #184:
    model=deepseek-flash provider=opencode-go in=101554 out=6134 total=107688
    latency=87.3s cache=90880/101554
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime

DEFAULT_LOG = "C:/Users/atton/AppData/Local/hermes/logs/agent.log"
DEFAULT_CUTOFF = "2026-10-06 04:30"
DEFAULT_TOP = 10

API_RE = re.compile(
    r"API call #(?P<num>\d+):\s+model=(?P<model>\S+)\s+provider=(?P<provider>\S+)\s+"
    r"in=(?P<tin>\d+)\s+out=(?P<tout>\d+)\s+total=(?P<total>\d+)\s+"
    r"latency=(?P<lat>[0-9.]+)s"
)
TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

BUCKETS: tuple[tuple[str, int, float], ...] = (
    ("<50k", 0, 50000),
    ("50-100k", 50000, 100000),
    ("100-150k", 100000, 150000),
    ("150-200k", 150000, 200000),
    (">200k", 200000, float("inf")),
)

# (label, needle, case_insensitive). Counted per line containing the needle.
EVENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("429s", "429"),
    ("retry/wait (Retrying API call)", "Retrying API call"),
    ("fallback (Fallback activated)", "Fallback activated"),
    ("title_generation failures (Title generation failed)", "Title generation failed"),
    ("search cache hits (web_search cache hit)", "web_search cache hit"),
    ("extract cache hits (web_extract cache hit)", "web_extract cache hit"),
    ("fair-share rate limit", "fair-share"),
    ("one-shot keyless rescue", "one-shot keyless rescue"),
    ("compression started (context compression started)", "context compression started"),
    ("compression done (context compression done)", "context compression done"),
    ("preflight compression (Preflight compression)", "Preflight compression"),
)


def parse_ts(line: str) -> datetime | None:
    """Parse the leading ``YYYY-MM-DD HH:MM:SS`` timestamp, else None."""
    m = TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def percentile(sorted_vals: list[float], q: float) -> float | None:
    """Linear-interpolation percentile over an already-sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    k = (n - 1) * q
    lo = int(k)
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def bucket_label(in_tokens: int) -> str:
    for label, lo, hi in BUCKETS:
        if lo <= in_tokens < hi:
            return label
    return ">200k"


def summarize(latencies: list[float]) -> dict:
    s = sorted(latencies)
    return {
        "n": len(s),
        "p50": percentile(s, 0.50),
        "p90": percentile(s, 0.90),
        "max": max(s) if s else None,
    }


def analyze(lines: list[str], cutoff: datetime, top: int) -> dict:
    pre_lats: list[float] = []
    post_lats: list[float] = []
    post_bucket_lats: dict[str, list[float]] = {label: [] for label, _, _ in BUCKETS}
    post_slowest: list[dict] = []
    events_pre: dict[str, int] = {label: 0 for label, _ in EVENT_PATTERNS}
    events_post: dict[str, int] = {label: 0 for label, _ in EVENT_PATTERNS}
    api_total = 0
    api_unparsed_ts = 0

    for line in lines:
        ts = parse_ts(line)
        is_post: bool | None = None
        if ts is not None:
            is_post = ts >= cutoff

        # Grep-style: each pattern is counted independently, exactly as
        # ``grep -c "needle"`` would count it (one line may match several rows).
        for label, needle in EVENT_PATTERNS:
            if needle in line:
                if is_post is True:
                    events_post[label] += 1
                elif is_post is False:
                    events_pre[label] += 1

        m = API_RE.search(line)
        if not m:
            continue
        api_total += 1
        if ts is None:
            api_unparsed_ts += 1
            continue
        lat = float(m.group("lat"))
        in_tok = int(m.group("tin"))
        if is_post:
            post_lats.append(lat)
            post_bucket_lats[bucket_label(in_tok)].append(lat)
            post_slowest.append(
                {
                    "ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "latency_s": lat,
                    "in": in_tok,
                    "out": int(m.group("tout")),
                    "model": m.group("model"),
                    "provider": m.group("provider"),
                    "line": line.rstrip("\n"),
                }
            )
        else:
            pre_lats.append(lat)

    post_slowest.sort(key=lambda r: (-r["latency_s"], r["ts"], r["line"]))
    return {
        "cutoff": cutoff.strftime("%Y-%m-%d %H:%M"),
        "api_lines_total": api_total,
        "api_lines_no_ts": api_unparsed_ts,
        "pre": summarize(pre_lats),
        "post": summarize(post_lats),
        "post_buckets": {
            label: summarize(post_bucket_lats[label]) for label, _, _ in BUCKETS
        },
        "events_pre": events_pre,
        "events_post": events_post,
        "top_post": post_slowest[:top],
    }


def fmt(v: float | None, digits: int = 1) -> str:
    return f"{v:.{digits}f}" if v is not None else "-"


def to_markdown(res: dict, top: int) -> str:
    out: list[str] = []
    out.append("# agent.log latency stats (speed round 2)")
    out.append("")
    out.append(f"Cutoff (local): `{res['cutoff']}` — pre = `< cutoff`, post = `>= cutoff`.")
    out.append(f"API-call lines parsed: {res['api_lines_total']} "
               f"(no-timestamp skipped: {res['api_lines_no_ts']}).")
    out.append("")
    out.append("## Table A — post-fix latency by in= bucket")
    out.append("")
    out.append("| in= bucket | n | p50 (s) | p90 (s) | max (s) |")
    out.append("|---|---|---|---|---|")
    for label, _, _ in BUCKETS:
        s = res["post_buckets"][label]
        out.append(f"| {label} | {s['n']} | {fmt(s['p50'])} | {fmt(s['p90'])} | {fmt(s['max'])} |")
    out.append("")
    out.append("## Table B — pre vs post overall")
    out.append("")
    out.append("| period | n | p50 (s) | p90 (s) | max (s) |")
    out.append("|---|---|---|---|---|")
    for name in ("pre", "post"):
        s = res[name]
        out.append(f"| {name} | {s['n']} | {fmt(s['p50'])} | {fmt(s['p90'])} | {fmt(s['max'])} |")
    out.append("")
    out.append("## Table C — tail-event counts pre vs post")
    out.append("")
    out.append("| event (needle) | pre | post |")
    out.append("|---|---|---|")
    for label, _ in EVENT_PATTERNS:
        out.append(f"| {label} | {res['events_pre'][label]} | {res['events_post'][label]} |")
    out.append("")
    out.append(f"## Top-{top} slowest post-fix calls (full log line)")
    out.append("")
    out.append("| rank | ts | latency (s) | in | out | model/provider |")
    out.append("|---|---|---|---|---|---|")
    for i, r in enumerate(res["top_post"], 1):
        out.append(f"| {i} | {r['ts']} | {r['latency_s']:.1f} | {r['in']} | "
                   f"{r['out']} | {r['model']}/{r['provider']} |")
    out.append("")
    for i, r in enumerate(res["top_post"], 1):
        out.append(f"### #{i} — {r['ts']} latency={r['latency_s']:.1f}s "
                   f"in={r['in']} out={r['out']}")
        out.append("")
        out.append("```")
        out.append(r["line"])
        out.append("```")
        out.append("")
    return "\n".join(out)


def to_jsonable(res: dict) -> dict:
    def round_or_none(v: float | None) -> float | None:
        return round(v, 3) if v is not None else None

    def summ(s: dict) -> dict:
        return {
            "n": s["n"],
            "p50": round_or_none(s["p50"]),
            "p90": round_or_none(s["p90"]),
            "max": round_or_none(s["max"]),
        }

    return {
        "cutoff": res["cutoff"],
        "api_lines_total": res["api_lines_total"],
        "api_lines_no_ts": res["api_lines_no_ts"],
        "pre": summ(res["pre"]),
        "post": summ(res["post"]),
        "post_buckets": {k: summ(v) for k, v in res["post_buckets"].items()},
        "events_pre": dict(res["events_pre"]),
        "events_post": dict(res["events_post"]),
        "top_post": res["top_post"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hermes agent.log latency stats (round 2).")
    ap.add_argument("--log", default=DEFAULT_LOG, help="path to agent.log")
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF,
                    help='pre/post split, format "YYYY-MM-DD HH:MM" (default: %(default)s)')
    ap.add_argument("--top", type=int, default=DEFAULT_TOP, help="slowest-N to list")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = ap.parse_args(argv)

    try:
        cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d %H:%M")
    except ValueError:
        print(f"error: bad --cutoff {args.cutoff!r}, want 'YYYY-MM-DD HH:MM'",
              file=sys.stderr)
        return 2
    if args.top < 1:
        print("error: --top must be >= 1", file=sys.stderr)
        return 2
    try:
        with open(args.log, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        print(f"error: cannot read log: {exc}", file=sys.stderr)
        return 2

    res = analyze(lines, cutoff, args.top)
    if args.json:
        print(json.dumps(to_jsonable(res), indent=2))
    else:
        print(to_markdown(res, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
