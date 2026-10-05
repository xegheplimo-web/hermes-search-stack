#!/usr/bin/env python3
"""verify_web_stack.py -- Search/Extract quality battery for the Hermes web layer.

Runs Hermes's REAL in-process web layer (``tools.web_tools``) against a fixed battery
of search + extract cases, captures backend evidence from the ROOT logger, and writes
JSON + Markdown reports into ``results/``.

Run recipe (Windows PowerShell):
    $env:PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent"; \
    $env:HERMES_HOME="C:/Users/atton/AppData/Local/hermes"; \
    & "C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/\
6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe" verify_web_stack.py

Rules honoured: writes only inside the project dir; never touches Hermes config/.env/auth;
no installs; <=30 live calls; sleep 1.5-2s between live calls.
"""

import asyncio
import json
import logging
import random
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"

RUN_COMMAND_BASH = (
    'PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent" '
    'HERMES_HOME="C:/Users/atton/AppData/Local/hermes" '
    '"C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/'
    '6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe" verify_web_stack.py'
)
RUN_COMMAND_PS = (
    '$env:PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent"; '
    '$env:HERMES_HOME="C:/Users/atton/AppData/Local/hermes"; '
    '& "C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/'
    '6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe" verify_web_stack.py'
)

# (case_id, query, label)
SEARCH_QUERIES = [
    ("S1", "capital of Australia population 2026", "EN factual"),
    ("S2", "AI news October 2026", "EN recency"),
    ("S3", "gi\u00e1 v\u00e0ng SJC h\u00f4m nay", "VI"),
    ("S4", "Nous Research Hermes Agent web search", "EN project"),
    ("S5", "DeepSeek V4 release", "EN product"),
]

# Fixed extract targets (case_id, url, label)
EXTRACT_FIXED = {
    "E2": ("https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search", "docs page"),
    "E3": ("https://github.com/NousResearch/hermes-agent", "github README"),
}

MIN_EXTRACT_CHARS = 1200
MIN_SEARCH_RESULTS = 3
MAX_LIVE_CALLS = 30

# ─── Live-call politeness gate (sleep 1.5-2s between live calls) ──────────────
_state = {"calls": 0, "last_end": None}


def _gate():
    """Sleep so that >= random(1.5, 2.0)s elapses since the previous live call ended."""
    if _state["last_end"] is not None:
        target = _state["last_end"] + random.uniform(1.5, 2.0)
        dt = target - time.time()
        if dt > 0:
            time.sleep(dt)


def _mark():
    _state["calls"] += 1
    _state["last_end"] = time.time()


# ─── Root-logger capture ──────────────────────────────────────────────────────
class ListHandler(logging.Handler):
    """Append every formatted record (>= INFO) to an in-memory list."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = []
        self._lock = threading.Lock()

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 -- never let logging kill a call
            msg = "<unformattable>"
        line = f"{record.levelname} {record.name}: {msg}"
        with self._lock:
            self.lines.append(line)


_handler = ListHandler()


def _attach_root_handler():
    """Attach the capture handler to the ROOT logger (level INFO) and force INFO through."""
    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _handler.setLevel(logging.INFO)
    root.addHandler(_handler)
    # Ensure the web-layer loggers actually emit at INFO and propagate to root.
    for name in (
        "tools.web_tools",
        "tools.web_tools_extract",
        "tools.web_tools_rescue",
        "agent.web_search_registry",
        "plugins.web",
        "plugins.web.perplexity.provider",
        "plugins.web.firecrawl.provider",
        "plugins.web.parallel.provider",
        "plugins.web.exa.provider",
        "plugins.web.keenable.provider",
        "plugins.web.keyless_mcp",
    ):
        lg = logging.getLogger(name)
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)
        lg.propagate = True


def _capture_start() -> int:
    return len(_handler.lines)


def _capture_since(start: int):
    return list(_handler.lines[start:])


_MARKER_RE = re.compile(r"via\s+([A-Za-z][\w\-]*)")


def _extract_backends(lines):
    """Backend markers from captured lines: 'via <backend>', plus 'keyless'/'managed' tags."""
    text = "\n".join(lines)
    backends = {m.group(1).lower() for m in _MARKER_RE.finditer(text)}
    if re.search(r"\bkeyless\b", text, re.I):
        backends.add("keyless")
    if re.search(r"\bmanaged\b", text, re.I):
        backends.add("managed")
    return sorted(backends)


# ─── Output parsing ───────────────────────────────────────────────────────────
def _parse_search(raw):
    """Return (results_list_or_None, error, notes)."""
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return None, f"non-JSON search output ({type(exc).__name__})", f"raw excerpt: {raw[:300]!r}"
    if not isinstance(data, dict):
        return None, "unexpected search output type", f"raw excerpt: {raw[:300]!r}"
    if not data.get("success"):
        return None, f"success=false: {data.get('error')}", ""
    results = (data.get("data") or {}).get("web")
    if not isinstance(results, list):
        return None, "no data.web[] in search output", f"raw excerpt: {raw[:300]!r}"
    return results, None, ""


def _parse_extract(raw):
    """Return (ok, chars, error, notes)."""
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return False, 0, f"non-JSON extract output ({type(exc).__name__})", f"raw excerpt: {raw[:300]!r}"
    if not isinstance(data, dict):
        return False, 0, "unexpected extract output type", f"raw excerpt: {raw[:300]!r}"
    if data.get("success") is False:
        return False, 0, f"success=false: {data.get('error')}", ""
    results = data.get("results")
    if not isinstance(results, list) or not results:
        if "error" in data:
            return False, 0, f"extract error: {data.get('error')}", ""
        return False, 0, "no results[] in extract output", f"raw excerpt: {raw[:300]!r}"
    entry = results[0]
    if not isinstance(entry, dict):
        return False, 0, "result entry not an object", f"raw excerpt: {raw[:300]!r}"
    content = entry.get("content") or entry.get("raw_content") or ""
    chars = len(content)
    if entry.get("error"):
        return False, chars, f"per-url error: {entry.get('error')}", ""
    if chars < MIN_EXTRACT_CHARS:
        return False, chars, f"content length {chars} < {MIN_EXTRACT_CHARS}", ""
    return True, chars, None, ""


def _article_candidates(results, max_n=3):
    """URLs from search results, preferring path depth >= 1 (not a homepage)."""

    def depth(u):
        try:
            p = urllib.parse.urlparse(u)
            return len([s for s in p.path.split("/") if s])
        except Exception:  # noqa: BLE001
            return 0

    seen, uniq = set(), []
    for r in results or []:
        u = (r.get("url") or "").strip()
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    preferred = [u for u in uniq if depth(u) >= 1]
    others = [u for u in uniq if depth(u) < 1]
    return (preferred + others)[:max_n]


# ─── Tool import (lazy, so an import failure still yields a report) ───────────
WEB_SEARCH = None
WEB_EXTRACT = None
IMPORT_ERROR = None


def _ensure_imports():
    global WEB_SEARCH, WEB_EXTRACT, IMPORT_ERROR
    try:
        from tools.web_tools import web_extract_tool, web_search_tool

        WEB_SEARCH = web_search_tool
        WEB_EXTRACT = web_extract_tool
    except Exception as exc:  # noqa: BLE001
        IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def _crash_rec(cid, kind, input_val, exc, label):
    rec = {
        "id": cid,
        "kind": kind,
        "input": input_val,
        "pass": False,
        "latency_s": None,
        "backends": [],
        "error": f"{type(exc).__name__}: {exc}",
        "notes": label,
    }
    if kind == "search":
        rec["n_results"] = 0
    else:
        rec["chars"] = 0
    return rec


# ─── Search case ──────────────────────────────────────────────────────────────
def run_search_case(cid, query, label):
    """Return (case_record, results_list_or_None)."""
    rec = {
        "id": cid,
        "kind": "search",
        "input": query,
        "pass": False,
        "latency_s": None,
        "n_results": 0,
        "backends": [],
        "error": None,
        "notes": label,
    }
    if IMPORT_ERROR:
        rec["error"] = f"import failed: {IMPORT_ERROR}"
        return rec, None
    start = _capture_start()
    _gate()
    t0 = time.time()
    try:
        raw = WEB_SEARCH(query, limit=5)
    except Exception as exc:  # noqa: BLE001
        _mark()
        rec["latency_s"] = round(time.time() - t0, 2)
        lines = _capture_since(start)
        rec["backends"] = _extract_backends(lines)
        rec["evidence"] = lines
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec, None
    _mark()
    rec["latency_s"] = round(time.time() - t0, 2)
    lines = _capture_since(start)
    rec["backends"] = _extract_backends(lines)
    rec["evidence"] = lines

    results, err, notes = _parse_search(raw)
    if err:
        rec["error"] = err
        if notes:
            rec["notes"] = f"{label}; {notes}"
        return rec, None

    n = len(results)
    rec["n_results"] = n
    missing = [r for r in results if not (r.get("title") or "").strip() or not (r.get("url") or "").strip()]
    extra = []
    if n < MIN_SEARCH_RESULTS:
        extra.append(f"only {n} results (< {MIN_SEARCH_RESULTS})")
    if missing:
        extra.append(f"{len(missing)} result(s) missing title/url")
    if cid == "S2":
        recency = any(re.search(r"2026", (r.get("description") or "") + " " + (r.get("url") or "")) for r in results)
        extra.append(f"recency_2026={recency}")
    rec["pass"] = n >= MIN_SEARCH_RESULTS and not missing
    rec["notes"] = label + ("; " + "; ".join(extra) if extra else "")
    if not rec["pass"]:
        rec["error"] = "; ".join(extra) if extra else "search criteria not met"
    return rec, results


# ─── Extract case ─────────────────────────────────────────────────────────────
async def run_extract_case(cid, candidates, label, source_note):
    rec = {
        "id": cid,
        "kind": "extract",
        "input": None,
        "pass": False,
        "latency_s": None,
        "chars": 0,
        "backends": [],
        "error": None,
        "notes": label,
    }
    if IMPORT_ERROR:
        rec["error"] = f"import failed: {IMPORT_ERROR}"
        return rec
    candidates = [u for u in (candidates or []) if u]
    if not candidates:
        rec["error"] = "no candidate URL available"
        rec["notes"] = f"{label}; {source_note}"
        return rec

    attempts, total_latency = [], 0.0
    for url in candidates:
        start = _capture_start()
        _gate()
        t0 = time.time()
        try:
            raw = await WEB_EXTRACT([url], char_limit=None)
        except Exception as exc:  # noqa: BLE001
            _mark()
            dt = time.time() - t0
            total_latency += dt
            lines = _capture_since(start)
            attempts.append(
                {
                    "url": url,
                    "latency_s": round(dt, 2),
                    "backends": _extract_backends(lines),
                    "error": f"{type(exc).__name__}: {exc}",
                    "evidence": lines,
                }
            )
            continue
        _mark()
        dt = time.time() - t0
        total_latency += dt
        lines = _capture_since(start)
        ok, chars, err, notes = _parse_extract(raw)
        attempts.append(
            {
                "url": url,
                "latency_s": round(dt, 2),
                "chars": chars,
                "backends": _extract_backends(lines),
                "error": err,
                "notes": notes,
                "evidence": lines,
            }
        )
        if ok:
            rec.update(
                {
                    "input": url,
                    "pass": True,
                    "latency_s": round(dt, 2),
                    "chars": chars,
                    "backends": _extract_backends(lines),
                    "evidence": lines,
                    "notes": f"{label}; {source_note}; url={url}",
                    "attempts": attempts,
                }
            )
            return rec

    last = attempts[-1] if attempts else {}
    rec.update(
        input=candidates[0],
        latency_s=round(total_latency, 2),
        chars=last.get("chars", 0),
        backends=last.get("backends", []),
        evidence=last.get("evidence", []),
        attempts=attempts,
        notes=f"{label}; {source_note}; tried {len(attempts)} candidate(s)",
        error=last.get("error") or last.get("notes") or "all candidates failed",
    )
    return rec


# ─── Orchestration ────────────────────────────────────────────────────────────
async def _amain():
    _attach_root_handler()
    _ensure_imports()
    t_start = time.time()
    cases = []
    search_results = {}

    for cid, query, label in SEARCH_QUERIES:
        try:
            rec, results = run_search_case(cid, query, label)
        except Exception as exc:  # noqa: BLE001
            rec, results = _crash_rec(cid, "search", query, exc, label), None
        cases.append(rec)
        if results is not None:
            search_results[cid] = results

    # E1: best article-like URL from S3 (Vietnamese) results.
    e1_cands = _article_candidates(search_results.get("S3"))
    try:
        cases.append(await run_extract_case("E1", e1_cands, "article from S3 (VI gold)", "source S3"))
    except Exception as exc:  # noqa: BLE001
        cases.append(_crash_rec("E1", "extract", e1_cands[0] if e1_cands else None, exc, "article from S3"))

    # E2: fixed docs URL.
    url2, label2 = EXTRACT_FIXED["E2"]
    try:
        cases.append(await run_extract_case("E2", [url2], label2, "fixed URL"))
    except Exception as exc:  # noqa: BLE001
        cases.append(_crash_rec("E2", "extract", url2, exc, label2))

    # E3: fixed GitHub README URL.
    url3, label3 = EXTRACT_FIXED["E3"]
    try:
        cases.append(await run_extract_case("E3", [url3], label3, "fixed URL"))
    except Exception as exc:  # noqa: BLE001
        cases.append(_crash_rec("E3", "extract", url3, exc, label3))

    # E4: best article-like URL from S2 (AI news) results.
    e4_cands = _article_candidates(search_results.get("S2"))
    try:
        cases.append(await run_extract_case("E4", e4_cands, "article from S2 (AI news)", "source S2"))
    except Exception as exc:  # noqa: BLE001
        cases.append(_crash_rec("E4", "extract", e4_cands[0] if e4_cands else None, exc, "article from S2"))

    _write_outputs(cases, time.time() - t_start)
    return cases


# ─── Reporting ────────────────────────────────────────────────────────────────
def _render_md(cases, elapsed, passed, total):
    md = ["# Hermes Web Stack \u2014 Search/Extract Battery", ""]
    md.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
    md.append(f"- Live calls: {_state['calls']} (cap {MAX_LIVE_CALLS})")
    md.append(f"- Elapsed: {round(elapsed, 2)}s")
    md.append(f"- Verdict: **PASS {passed}/{total}**")
    md.append("")
    md.append("## Summary")
    md.append("")
    md.append("| Case | Kind | Input | Pass | Latency (s) | Metric | Backends | Notes |")
    md.append("|------|------|-------|------|-------------|--------|----------|-------|")
    for c in cases:
        metric = f"{c.get('n_results')} results" if c["kind"] == "search" else f"{c.get('chars', 0)} chars"
        inp = (c.get("input") or "").replace("|", "\\|")
        if len(inp) > 60:
            inp = inp[:57] + "..."
        notes = (c.get("notes") or "").replace("|", "\\|")
        backends = ", ".join(c.get("backends") or []) or "-"
        md.append(
            f"| {c['id']} | {c['kind']} | {inp} | {'PASS' if c.get('pass') else 'FAIL'} "
            f"| {c.get('latency_s')} | {metric} | {backends} | {notes} |"
        )
    md.append("")
    md.append("## Failures")
    md.append("")
    fails = [c for c in cases if not c.get("pass")]
    if not fails:
        md.append("None \u2014 all cases passed.")
    else:
        for c in fails:
            md.append(f"### {c['id']} ({c['kind']}) \u2014 {c.get('input')}")
            md.append("")
            md.append(f"- Error: `{c.get('error')}`")
            md.append(f"- Notes: {c.get('notes')}")
            for a in c.get("attempts") or []:
                md.append(
                    f"  - attempt `{a.get('url')}`: chars={a.get('chars')} "
                    f"backends={a.get('backends')} error={a.get('error')}"
                )
            md.append("")
    md.append("## Run command")
    md.append("")
    md.append("Bash (SPEC recipe):")
    md.append("")
    md.append("```bash")
    md.append(RUN_COMMAND_BASH)
    md.append("```")
    md.append("")
    md.append("PowerShell (used):")
    md.append("")
    md.append("```powershell")
    md.append(RUN_COMMAND_PS)
    md.append("```")
    md.append("")
    return "\n".join(md)


def _write_outputs(cases, elapsed):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    passed = sum(1 for c in cases if c.get("pass"))
    total = len(cases)
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "run_command_bash": RUN_COMMAND_BASH,
        "run_command_powershell": RUN_COMMAND_PS,
        "live_calls": _state["calls"],
        "cases": cases,
        "totals": {"pass": passed, "fail": total - passed, "total": total, "elapsed_s": round(elapsed, 2)},
    }
    json_path = RESULTS_DIR / f"battery_{ts}.json"
    md_path = RESULTS_DIR / f"battery_{ts}.md"
    log_path = RESULTS_DIR / f"battery_{ts}.log"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_md(cases, elapsed, passed, total), encoding="utf-8")
    log_path.write_text("\n".join(_handler.lines), encoding="utf-8")
    print(f"[outputs] results/{json_path.name}, results/{md_path.name}, results/{log_path.name}")


def main():
    cases = asyncio.run(_amain())
    passed = sum(1 for c in cases if c.get("pass"))
    total = len(cases)
    print(f"PASS {passed}/{total}")
    fails = [c for c in cases if not c.get("pass")]
    if fails:
        print("FAILURES:")
        for c in fails:
            print(f"  - {c['id']} ({c['kind']}): {c.get('error')}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
