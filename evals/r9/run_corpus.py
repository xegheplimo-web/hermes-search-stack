#!/usr/bin/env python3
"""R10-C Vietnamese quality-corpus runner (frozen splits + objective signals).

Runs ``evals/r9/corpus_v0.jsonl`` cases against the read-only gateway
``POST /v1/chat/completions`` and emits battery-compatible JSON + Markdown
under ``results/`` so ``scripts/scoreboard.py`` math applies.

Splits (FROZEN, see evals/r9/splits.json + analysis/r10-interfaces.md §C):
  holdout    = ids where int(id[3:]) % 10 == 5  (excluded unless --include-holdout)
  challenge  = difficulty == "hard" minus holdout
  regression = everything else minus holdout

Checks are OBJECTIVE SIGNALS only — never invented scores:
  * citations_present / sources_section_present / source_domains
  * required_fields via a small documented heuristic map where safely
    automatable, else "judge-pending"
  * must_include / must_not_include via normalized substring match
    (casefold + whitespace collapse, DIACRITICS PRESERVED);
    any unmatched semantic expectation -> "judge-pending" (NOT pass, NOT fail)
  * ground_truth.status == "dynamic": freshness signal = answer states an
    explicit date/period; stale marking follows analysis/r9-eval-inventory.md
    §3.4 (stale is never a fail; reported separately)

Honesty rules: never hard-code answers; never edit corpus expectations;
never tune scoring so results look better.

Usage (from repo root)::

    python evals/r9/run_corpus.py [--split regression,challenge] [--include-holdout]
        [--ids vn-001,vn-002] [--limit N] [--variants] [--sleep 2.0]
        [--gateway http://127.0.0.1:8787] [--out-dir results] [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
CORPUS_PATH = HERE / "corpus_v0.jsonl"
SPLITS_PATH = HERE / "splits.json"
DEFAULT_GATEWAY = "http://127.0.0.1:8787"
DEFAULT_OUT_DIR = REPO_ROOT / "results"

CITATION_RE = re.compile(r"\[\d+\]")
SOURCES_SECTION_RE = re.compile(r"^##\s*Sources", re.IGNORECASE | re.MULTILINE)
SOURCE_LINE_RE = re.compile(
    r"^\s*\[(\d+)\]\s*(.+?)\s*(?:[—–\-–|]\s*)?(https?://\S+)\s*$",
    re.MULTILINE,
)
# Explicit date/period signal (conservative): dd/mm/yyyy, yyyy-mm-dd,
# "ngày N tháng M", "tháng M năm Y", "quý N", ISO datetime.
AS_OF_RE = re.compile(
    r"(\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?)"
    r"|(\d{4}-\d{2}-\d{2})"
    r"|(ngày\s+\d{1,2}(\s+tháng\s+\d{1,2})?(\s+năm\s+\d{4})?)"
    r"|(tháng\s+\d{1,2}\s+năm\s+\d{4})"
    r"|(quý\s*[1-4](\s*/\s*\d{4})?)"
    r"|(tuần\s+(này|tới|trước))"
    r"|(năm\s+20\d{2})",
    re.IGNORECASE,
)
PRICE_RE = re.compile(
    r"\d[\d.,]*\s*(đồng|₫|vnd|nghìn|ngàn|triệu|tỷ|/lít|/kg|đ/lít)",
    re.IGNORECASE,
)
UNIT_RE = re.compile(
    r"(/lít|/kg|đồng|₫|vnd|lít|kg|kwh|lượng|chỉ\b|ounce|%)",
    re.IGNORECASE,
)
ADDRESS_RE = re.compile(
    r"(địa chỉ|số\s+\d+|đường\s|phố\s|phường|xã\b|quận|huyện|tỉnh|thành phố|tp\.?)",
    re.IGNORECASE,
)
OPENING_HOURS_RE = re.compile(
    r"(giờ\s*mở|giờ\s*đóng|mở cửa|đóng cửa|\b\d{1,2}:\d{2}\b|giờ hành chính)",
    re.IGNORECASE,
)
LEGAL_BASIS_RE = re.compile(
    r"(nghị định|luật\b|thông tư|quyết định|căn cứ|điều\s+\d+|khoản\s+\d+)",
    re.IGNORECASE,
)

# required_fields -> (heuristic regex, description). Fields NOT in this map
# are "judge-pending" (cannot be safely automated).
REQUIRED_FIELD_HEURISTICS: dict[str, tuple[re.Pattern, str]] = {
    "answer": (re.compile(r"\S+"), "non-empty answer text"),
    "citations": (CITATION_RE, "[n] citation markers present"),
    "as_of": (AS_OF_RE, "explicit date/period stated"),
    "price": (PRICE_RE, "numeric price with currency/unit"),
    "unit": (UNIT_RE, "unit token present"),
    "address": (ADDRESS_RE, "address-like tokens"),
    "opening_hours": (OPENING_HOURS_RE, "hours-like tokens"),
    "legal_basis": (LEGAL_BASIS_RE, "legal-basis tokens (NĐ/luật/điều/khoản)"),
}

# Small built-in fixture answer for --dry-run (no HTTP). Covers: citations,
# Sources block, an explicit date, a price, and a legal-basis token.
DRY_RUN_ANSWER = (
    "Giá xăng RON 95 tại kỳ điều hành ngày 02/10/2026 là 21.500 đồng/lít [1].\n"
    "Mức giá theo Nghị định 168/2024 còn hiệu lực, xem điều 5 khoản 2.\n"
    "\n"
    "## Sources\n"
    "\n"
    "[1] Giá xăng dầu — https://petrolimex.com.vn/gia-xang-dau\n"
)


def normalize(text: str) -> str:
    """Casefold + collapse whitespace. DIACRITICS PRESERVED."""
    return re.sub(r"\s+", " ", text.casefold().strip())


def load_corpus(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{lineno}: invalid JSON ({exc})") from exc
    return rows


def load_holdout_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    holdout = payload.get("holdout", [])
    return set(holdout) if isinstance(holdout, list) else set()


def assign_split(case: dict, holdout_ids: set[str]) -> str:
    """FROZEN rule: holdout = int(id[3:]) % 10 == 5; challenge = hard - holdout."""
    cid = str(case.get("id", ""))
    try:
        numeric = int(cid[3:])
    except (ValueError, IndexError):
        numeric = -1
    if cid in holdout_ids or numeric % 10 == 5:
        return "holdout"
    if case.get("difficulty") == "hard":
        return "challenge"
    return "regression"


def parse_sources(answer: str) -> list[dict]:
    """Parse the ``## Sources`` block into [{n, title, url, domain}]."""
    match = SOURCES_SECTION_RE.search(answer)
    if not match:
        return []
    block = answer[match.end() :]
    # Stop at the next top-level heading.
    next_heading = re.search(r"^##\s+\S", block, re.MULTILINE)
    if next_heading:
        block = block[: next_heading.start()]
    sources: list[dict] = []
    for m in SOURCE_LINE_RE.finditer(block):
        url = m.group(3).rstrip(").,;")
        try:
            domain = urllib.parse.urlparse(url).netloc.lower()
        except ValueError:
            domain = ""
        sources.append({"n": int(m.group(1)), "title": m.group(2).strip(), "url": url, "domain": domain})
    return sources


def check_required_field(name: str, answer: str) -> str:
    """Return 'present' | 'missing' | 'judge-pending' for one required field."""
    heuristic = REQUIRED_FIELD_HEURISTICS.get(name)
    if heuristic is None:
        return "judge-pending"
    pattern, _ = heuristic
    return "present" if pattern.search(answer) else "missing"


def evaluate_case(case: dict, answer: str) -> dict:
    """Objective signals for one (case, answer) pair. Never invents scores."""
    expected = case.get("expected", {}) or {}
    must_include = expected.get("must_include", []) or []
    must_not_include = expected.get("must_not_include", []) or []
    required_fields = expected.get("required_fields", []) or []
    ground_truth = case.get("ground_truth", {}) or {}

    norm_answer = normalize(answer)
    citations_present = bool(CITATION_RE.search(answer))
    sources_section_present = bool(SOURCES_SECTION_RE.search(answer))
    sources = parse_sources(answer)
    source_domains = sorted({s["domain"] for s in sources if s["domain"]})

    must_include_hits: list[str] = []
    must_include_pending: list[str] = []
    for item in must_include:
        if normalize(str(item)) and normalize(str(item)) in norm_answer:
            must_include_hits.append(item)
        else:
            must_include_pending.append(item)

    must_not_hits: list[str] = []
    for item in must_not_include:
        if normalize(str(item)) and normalize(str(item)) in norm_answer:
            must_not_hits.append(item)

    field_results: dict[str, str] = {}
    for field in required_fields:
        field_results[str(field)] = check_required_field(str(field), answer)
    field_missing = [f for f, v in field_results.items() if v == "missing"]
    field_pending = [f for f, v in field_results.items() if v == "judge-pending"]

    freshness_as_of_present = bool(AS_OF_RE.search(answer))
    stale = False
    stale_note = ""
    status = ground_truth.get("status")
    if status == "dynamic":
        checked_at = ground_truth.get("checked_at")
        ttl_days = None
        freshness = case.get("freshness")
        if isinstance(freshness, dict) and freshness.get("ttl_days") is not None:
            try:
                ttl_days = float(freshness["ttl_days"])
            except (TypeError, ValueError):
                ttl_days = None
        if checked_at:
            try:
                checked = datetime.fromisoformat(str(checked_at))
                now = datetime.now(UTC)
                if checked.tzinfo is None:
                    checked = checked.replace(tzinfo=UTC)
                ttl = ttl_days if ttl_days is not None else 7.0
                if (now - checked).total_seconds() > ttl * 86400:
                    stale = True
                    stale_note = f"dynamic ground truth older than ttl ({ttl} days)"
            except ValueError:
                stale_note = "unparseable checked_at; treated as not stale"
        elif ttl_days is not None:
            # checked_at is null in v0 -> stale only when an explicit ttl exists.
            stale = True
            stale_note = "checked_at is null and ttl_days is set"
        else:
            stale_note = "no ground truth yet (checked_at null, no ttl_days)"

    judge_pending = len(must_include_pending) + len(field_pending)
    # Pass = no objective failure. Stale / judge-pending never fail the case.
    failures: list[str] = []
    if must_not_hits:
        failures.append(f"must_not_include hit: {must_not_hits}")
    if field_missing:
        failures.append(f"required_fields missing: {field_missing}")
    passed = not failures

    signals = {
        "citations_present": citations_present,
        "sources_section_present": sources_section_present,
        "source_domains": source_domains,
        "sources_count": len(sources),
        "must_include_matched": len(must_include_hits),
        "must_include_total": len(must_include),
        "must_include_pending": must_include_pending,
        "must_not_include_hits": must_not_hits,
        "required_fields": field_results,
        "freshness_as_of_present": freshness_as_of_present,
        "stale": stale,
        "stale_note": stale_note,
    }
    return {
        "pass": passed,
        "failures": failures,
        "signals": signals,
        "judge_pending": judge_pending,
        "stale": stale,
    }


def post_chat(gateway: str, query: str, timeout_s: float = 120.0) -> tuple[str, float]:
    """POST one chat completion; return (answer_text, wall_seconds)."""
    url = gateway.rstrip("/") + "/v1/chat/completions"
    payload = json.dumps(
        {
            "model": "hermes-search",
            "stream": False,
            "messages": [{"role": "user", "content": query}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    finally:
        wall_s = time.monotonic() - start
    try:
        content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except (AttributeError, IndexError, TypeError):
        content = ""
    return content, wall_s


def select_cases(
    corpus: list[dict],
    holdout_ids: set[str],
    splits: list[str],
    include_holdout: bool,
    ids: set[str] | None,
    limit: int | None,
    with_variants: bool,
) -> list[tuple[dict, str, str]]:
    """Return [(case, probe_input, probe_label)] in corpus order."""
    wanted = {s.strip() for s in splits if s.strip()}
    selected: list[tuple[dict, str, str]] = []
    for case in corpus:
        split = assign_split(case, holdout_ids)
        if split == "holdout" and not include_holdout:
            continue
        if split not in wanted and not (include_holdout and split == "holdout"):
            continue
        if ids is not None and case.get("id") not in ids:
            continue
        selected.append((case, case.get("query", ""), "main"))
        if with_variants:
            for i, variant in enumerate(case.get("variants", []) or []):
                selected.append((case, variant, f"variant-{i}"))
        if limit is not None and len(selected) >= limit:
            break
    if limit is not None:
        selected = selected[:limit]
    return selected


def _pct(values: list[float], pct: float) -> float | None:
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.scoreboard import nearest_rank_percentile

        return nearest_rank_percentile(values, pct)
    except (ImportError, ValueError):
        if not values:
            return None
        import math

        ordered = sorted(values)
        rank = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered) - 1e-9)))
        return ordered[rank - 1]
    finally:
        try:
            sys.path.remove(str(REPO_ROOT))
        except ValueError:
            pass


def write_markdown(
    *,
    generated: str,
    splits: list[str],
    include_holdout: bool,
    gateway: str,
    probes: list[dict],
    elapsed_s: float,
) -> str:
    latencies = [p["latency_s"] for p in probes if isinstance(p.get("latency_s"), (int, float))]
    p50 = _pct(latencies, 50)
    p90 = _pct(latencies, 90)
    passed = sum(1 for p in probes if p.get("pass"))
    failed = sum(1 for p in probes if not p.get("pass"))
    judge_total = sum(int(p.get("judge_pending", 0) or 0) for p in probes)
    stale = [p for p in probes if p.get("signals", {}).get("stale")]
    lines = [
        "# R10 corpus run (Vietnamese quality corpus v0)",
        "",
        f"generated: `{generated}` · splits: `{','.join(splits)}` · "
        f"include_holdout: `{include_holdout}` · gateway: `{gateway}`",
        "",
        f"probes: {len(probes)} · pass {passed} / fail {failed} · "
        f"judge-pending {judge_total} · stale {len(stale)} · "
        f"elapsed {elapsed_s:.1f}s",
        "",
        f"latency p50 {_fmt(p50)}s · p90 {_fmt(p90)}s (nearest-rank over probe wall time)",
        "",
        "## Per-difficulty",
        "",
        "| difficulty | n | pass | fail | judge-pending |",
        "| --- | --: | --: | --: | --: |",
    ]
    for difficulty in ("easy", "medium", "hard"):
        group = [p for p in probes if p.get("difficulty") == difficulty]
        if not group:
            continue
        gp = sum(1 for p in group if p.get("pass"))
        gj = sum(int(p.get("judge_pending", 0) or 0) for p in group)
        lines.append(f"| {difficulty} | {len(group)} | {gp} | {len(group) - gp} | {gj} |")
    lines += [
        "",
        "## Per-domain",
        "",
        "| domain | n | pass | fail | judge-pending |",
        "| --- | --: | --: | --: | --: |",
    ]
    for domain in sorted({str(p.get("domain", "?")) for p in probes}):
        group = [p for p in probes if str(p.get("domain")) == domain]
        gp = sum(1 for p in group if p.get("pass"))
        gj = sum(int(p.get("judge_pending", 0) or 0) for p in group)
        lines.append(f"| {domain} | {len(group)} | {gp} | {len(group) - gp} | {gj} |")
    lines += ["", "## Failures", ""]
    failures = [p for p in probes if not p.get("pass")]
    if not failures:
        lines.append("None.")
    else:
        for p in failures:
            detail = "; ".join(p.get("notes", "").split("; ")[:3])
            lines.append(f"- `{p['id']}` ({p.get('probe')}) — {detail}")
    lines += ["", "## Judge-pending", ""]
    pending = [p for p in probes if int(p.get("judge_pending", 0) or 0) > 0]
    if not pending:
        lines.append("None.")
    else:
        for p in pending[:50]:
            sig = p.get("signals", {})
            mi = sig.get("must_include_pending", []) or []
            rf = [f for f, v in (sig.get("required_fields", {}) or {}).items() if v == "judge-pending"]
            bits = []
            if mi:
                bits.append(f"must_include unmatched: {len(mi)}")
            if rf:
                bits.append(f"fields pending: {rf}")
            lines.append(f"- `{p['id']}` ({p.get('probe')}) — {'; '.join(bits) or 'pending'}")
        if len(pending) > 50:
            lines.append(f"- … and {len(pending) - 50} more")
    lines += ["", "## Stale", ""]
    if not stale:
        lines.append("None (stale is never a fail; see signals.stale_note).")
    else:
        for p in stale:
            lines.append(f"- `{p['id']}` ({p.get('probe')}) — {p.get('signals', {}).get('stale_note', '')}")
    lines += [
        "",
        "---",
        "Signals only: unmatched semantic expectations are judge-pending, never fabricated verdicts.",
        "Objective pass rule: no must_not_include hit and no automatable required_field missing.",
        "",
    ]
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "—"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_corpus.py",
        description="Run the Vietnamese quality corpus v0 against the gateway (signals only).",
    )
    parser.add_argument(
        "--split",
        default="regression,challenge",
        help="comma-separated splits to run (default: regression,challenge)",
    )
    parser.add_argument("--include-holdout", action="store_true", help="include the frozen holdout split")
    parser.add_argument("--ids", default="", help="comma-separated case ids to run (e.g. vn-001,vn-002)")
    parser.add_argument("--limit", type=int, default=None, help="max probes to run (cases × variants)")
    parser.add_argument("--variants", action="store_true", help="also run each case's variants")
    parser.add_argument("--sleep", type=float, default=2.0, help="seconds between live calls (min 1)")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help="gateway base URL")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="results directory")
    parser.add_argument("--dry-run", action="store_true", help="no HTTP: check logic vs fixture answer")
    parser.add_argument("--json", action="store_true", help="print the output JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sleep < 1.0:
        print("[run_corpus] --sleep must be >= 1.0s (live politeness)", file=sys.stderr)
        return 2

    try:
        corpus = load_corpus(CORPUS_PATH)
    except (OSError, ValueError) as exc:
        print(f"[run_corpus] cannot load corpus: {exc}", file=sys.stderr)
        return 2
    try:
        holdout_ids = load_holdout_ids(SPLITS_PATH)
    except (OSError, ValueError) as exc:
        print(f"[run_corpus] cannot load splits: {exc}", file=sys.stderr)
        return 2

    splits = [s.strip() for s in str(args.split).split(",") if s.strip()]
    if not splits:
        print("[run_corpus] --split is empty", file=sys.stderr)
        return 2
    ids = {i.strip() for i in str(args.ids).split(",") if i.strip()} or None

    probes = select_cases(corpus, holdout_ids, splits, args.include_holdout, ids, args.limit, args.variants)
    if not probes:
        print("[run_corpus] no cases selected", file=sys.stderr)
        return 2

    started = time.monotonic()
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    cases_out: list[dict] = []
    live_calls = 0
    for index, (case, probe_input, probe_label) in enumerate(probes):
        split = assign_split(case, holdout_ids)
        if args.dry_run:
            answer, wall_s = DRY_RUN_ANSWER, 0.0
            error = ""
        else:
            try:
                answer, wall_s = post_chat(args.gateway, probe_input)
                error = ""
                live_calls += 1
            except Exception as exc:  # noqa: BLE001 — recorded, never raised
                answer, wall_s, error = "", 0.0, f"{type(exc).__name__}: {exc}"
        verdict = evaluate_case(case, answer)
        if error:
            verdict["pass"] = False
        notes_bits = list(verdict["failures"])
        if verdict["judge_pending"]:
            notes_bits.append(f"judge-pending={verdict['judge_pending']}")
        if verdict["stale"]:
            notes_bits.append("stale (not a fail)")
        stale_note = verdict["signals"].get("stale_note", "")
        if stale_note and not verdict["stale"]:
            notes_bits.append(stale_note)
        cases_out.append(
            {
                "id": f"{case.get('id')}:{probe_label}" if probe_label != "main" else case.get("id"),
                "kind": "corpus",
                "input": probe_input,
                "pass": bool(verdict["pass"]) and not error,
                "latency_s": round(wall_s, 3),
                "notes": "; ".join(notes_bits),
                "error": error,
                "domain": case.get("domain"),
                "difficulty": case.get("difficulty"),
                "split": split,
                "probe": probe_label,
                "signals": verdict["signals"],
                "judge_pending": verdict["judge_pending"],
            }
        )
        if not args.dry_run and index < len(probes) - 1:
            time.sleep(args.sleep)

    elapsed_s = time.monotonic() - started
    passed = sum(1 for c in cases_out if c["pass"])
    payload = {
        "generated": generated,
        "run_command": " ".join(["python", "evals/r9/run_corpus.py", *sys.argv[1:]])
        if argv is None
        else " ".join(["python", "evals/r9/run_corpus.py", *argv]),
        "run_command_argv": sys.argv,
        "gateway": args.gateway,
        "splits": splits,
        "include_holdout": args.include_holdout,
        "live_calls": 0 if args.dry_run else live_calls,
        "dry_run": bool(args.dry_run),
        "cases": cases_out,
        "totals": {
            "pass": passed,
            "fail": len(cases_out) - passed,
            "total": len(cases_out),
            "elapsed_s": round(elapsed_s, 3),
        },
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"r10_corpus_{ts}.json"
    md_path = out_dir / f"r10_corpus_{ts}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_text = write_markdown(
        generated=generated,
        splits=splits,
        include_holdout=args.include_holdout,
        gateway=args.gateway,
        probes=cases_out,
        elapsed_s=elapsed_s,
    )
    md_path.write_text(md_text, encoding="utf-8")

    if args.json:
        print(str(json_path))
    else:
        print(
            f"corpus: {json_path.name} + {md_path.name} "
            f"({passed}/{len(cases_out)} pass, "
            f"judge-pending {sum(c['judge_pending'] for c in cases_out)}, "
            f"elapsed {elapsed_s:.1f}s)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
