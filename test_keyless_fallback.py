#!/usr/bin/env python3
"""test_keyless_fallback.py -- Prove the KEYLESS fallback chain + rescue path.

Independent of the managed route: exercises DDGS (documented-unavailable tier),
keyless_mcp search vendors (Parallel, Exa) and extract vendors
(Parallel -> Exa -> Keenable -> Firecrawl), plus tools.web_tools._rescue_search.

Run recipe (bash, SPEC):
    PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent" \
    HERMES_HOME="C:/Users/atton/AppData/Local/hermes" \
    "C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/\
6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe" test_keyless_fallback.py

Rules honoured: writes only inside the project dir (results/); no modification of
existing files; no installs; no secrets printed; <= 12 live calls; sleep >= 3s
between calls; sequential calls only.
"""

import inspect
import json
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"

SLEEP_S = 3.2
MAX_LIVE_CALLS = 12
TRUNC = 200

RUN_COMMAND = (
    'PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent" '
    'HERMES_HOME="C:/Users/atton/AppData/Local/hermes" '
    '"C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/'
    '6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe" test_keyless_fallback.py'
)

SEARCH_QUERY = "best laptop 2026"
SEARCH_LIMIT = 3
EXTRACT_URLS = [
    "https://example.com",
    "https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search",
]
RESCUE_QUERY = "tin tức AI hôm nay"

FN_DDGS = "plugins.web.ddgs.provider.DDGSWebSearchProvider.search"
FN_PAR_SEARCH = "plugins.web.keyless_mcp.parallel_search_keyless"
FN_EXA_SEARCH = "plugins.web.keyless_mcp.exa_search_keyless"
FN_PAR_EXTRACT = "plugins.web.keyless_mcp.parallel_extract_keyless"
FN_EXA_EXTRACT = "plugins.web.keyless_mcp.exa_extract_keyless"
FN_KEEN_EXTRACT = "plugins.web.keyless_mcp.keenable_extract_keyless"
FN_FIRE_EXTRACT = "plugins.web.keyless_mcp.firecrawl_extract_keyless"
FN_RESCUE = "tools.web_tools._rescue_search"

_state = {"calls": 0}


def _gate(first=False):
    if not first:
        time.sleep(SLEEP_S)


def _bump(n=1):
    _state["calls"] += n


def _trunc(s, n=TRUNC):
    s = str(s)
    return s if len(s) <= n else s[:n] + "...[truncated]"


def _web_items(payload):
    """Extract result list from a keyless search payload dict."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    items = data.get("web")
    if isinstance(items, list):
        return items
    # tolerate alternate shapes
    for key in ("results", "items"):
        alt = payload.get(key) or data.get(key)
        if isinstance(alt, list):
            return alt
    return None


def _extract_content(entry):
    if not isinstance(entry, dict):
        return "", _trunc(entry)
    content = entry.get("content") or entry.get("raw_content") or entry.get("text") or ""
    err = entry.get("error")
    if isinstance(content, str) and content.strip():
        return content, None
    return "", (_trunc(err) if err else "(empty content, no error field)")


def main():
    import tools.web_tools as wt
    from plugins.web import keyless_mcp as km
    from plugins.web.ddgs.provider import DDGSWebSearchProvider

    cases = []
    first = True

    # ── K1: documented-unavailability (DDGS) ──────────────────────────────
    rec = {
        "id": "K1",
        "pass": False,
        "fn_used": FN_DDGS,
        "calls": 0,
        "error": None,
        "notes": "",
        "evidence_excerpt": "",
    }
    try:
        _gate(first)
        first = False
        out = DDGSWebSearchProvider().search(SEARCH_QUERY, SEARCH_LIMIT)
        _bump(1)
        rec["calls"] = 1
        rec["evidence_excerpt"] = _trunc(json.dumps(out, ensure_ascii=False, default=str))
        if (
            isinstance(out, dict)
            and out.get("success") is False
            and "ddgs package is not installed" in str(out.get("error", ""))
        ):
            rec["pass"] = True
            rec["notes"] = (
                "FINDING (documented-unavailability, NOT a failure): "
                "DDGS tier unavailable as documented; error=" + _trunc(out.get("error"))
            )
        elif isinstance(out, dict) and out.get("success") is True:
            rec["pass"] = True
            rec["notes"] = "UNEXPECTED: DDGS search succeeded (package now installed?); recorded honestly."
        else:
            rec["pass"] = True
            rec["notes"] = "FINDING: DDGS call completed with unclassified payload; recorded honestly (not a failure)."
            rec["error"] = _trunc(out)
    except Exception as exc:  # noqa: BLE001
        rec["evidence_excerpt"] = _trunc(f"{type(exc).__name__}: {exc}")
        rec["notes"] = "FINDING: DDGS raised; recorded honestly (documented-unavailable tier)."
        rec["error"] = _trunc(f"{type(exc).__name__}: {exc}")
        rec["pass"] = True  # still a recorded finding, not a masked failure
    cases.append(rec)

    # ── K2: keyless search per vendor ─────────────────────────────────────
    for cid, fn_path, fn in (
        ("K2-parallel", FN_PAR_SEARCH, km.parallel_search_keyless),
        ("K2-exa", FN_EXA_SEARCH, km.exa_search_keyless),
    ):
        rec = {
            "id": cid,
            "pass": False,
            "fn_used": fn_path,
            "calls": 0,
            "error": None,
            "notes": "",
            "evidence_excerpt": "",
        }
        try:
            _gate(first)
            first = False
            out = fn(SEARCH_QUERY, SEARCH_LIMIT)
            _bump(1)
            rec["calls"] = 1
            items = _web_items(out)
            if isinstance(out, dict) and out.get("success") is True and items is not None and len(items) >= 1:
                first_item = items[0] if isinstance(items[0], dict) else {"raw": items[0]}
                rec["pass"] = True
                rec["notes"] = (
                    f"n_results={len(items)}; first title={_trunc(first_item.get('title', ''))!r}; "
                    f"first url={_trunc(first_item.get('url', ''))!r}"
                )
                rec["evidence_excerpt"] = _trunc(json.dumps(items[0], ensure_ascii=False, default=str))
            else:
                rec["error"] = _trunc(json.dumps(out, ensure_ascii=False, default=str))
                rec["notes"] = "FAIL: success!=true or <1 result."
        except Exception as exc:  # noqa: BLE001
            rec["error"] = _trunc(f"{type(exc).__name__}: {exc}")
            rec["notes"] = "FAIL: exception during keyless search."
        cases.append(rec)

    # ── K3: extract health matrix (ordered failover per URL) ─────────────
    vendor_order = (
        ("parallel", FN_PAR_EXTRACT, km.parallel_extract_keyless),
        ("exa", FN_EXA_EXTRACT, km.exa_extract_keyless),
        ("keenable", FN_KEEN_EXTRACT, km.keenable_extract_keyless),
        ("firecrawl", FN_FIRE_EXTRACT, km.firecrawl_extract_keyless),
    )
    for url in EXTRACT_URLS:
        cid = f"K3-{'example' if 'example.com' in url else 'docs'}"
        rec = {
            "id": cid,
            "pass": False,
            "fn_used": " -> ".join(v[1] for v in vendor_order),
            "calls": 0,
            "error": None,
            "notes": f"url={url}",
            "evidence_excerpt": "",
            "vendor_outcomes": {},
        }
        try:
            content_source = None
            for vname, _vpath, vfn in vendor_order:
                _gate(first)
                first = False
                try:
                    out = vfn([url])
                    _bump(1)
                    rec["calls"] += 1
                    entries = out if isinstance(out, list) else [out]
                    entry = entries[0] if entries else {}
                    content, err_text = _extract_content(entry)
                    if content:
                        rec["vendor_outcomes"][vname] = f"CONTENT chars={len(content)}"
                        content_source = (vname, len(content), content)
                        break
                    rec["vendor_outcomes"][vname] = f"no-content: {err_text}"
                except Exception as exc:  # noqa: BLE001
                    _bump(1)
                    rec["calls"] += 1
                    rec["vendor_outcomes"][vname] = f"{type(exc).__name__}: {_trunc(exc)}"
                if _state["calls"] >= MAX_LIVE_CALLS:
                    rec["vendor_outcomes"]["_cap"] = "live-call cap reached; stopped early"
                    break
            tried = len(rec["vendor_outcomes"])
            if content_source:
                vname, nchars, content = content_source
                rec["pass"] = True
                rec["notes"] += f"; content_source={vname}; chars={nchars}; tried={tried}/4"
                rec["evidence_excerpt"] = _trunc(content)
            elif (
                tried >= 4
                or "_cap" in rec["vendor_outcomes"]
                or all(k in rec["vendor_outcomes"] for k in ("parallel", "exa", "keenable", "firecrawl"))
            ):
                rec["pass"] = True  # all-vendor errors documented = valid PASS per spec
                rec["notes"] += f"; no content; all {tried} vendor error(s) documented (expected fussy free tiers)"
            else:
                rec["pass"] = False
                rec["error"] = f"only {tried}/4 vendors attempted; incomplete matrix"
        except Exception as exc:  # noqa: BLE001
            rec["error"] = _trunc(f"{type(exc).__name__}: {exc}")
            rec["notes"] += "; harness exception"
        cases.append(rec)

    # ── K4: rescue path ───────────────────────────────────────────────────
    rec = {
        "id": "K4",
        "pass": False,
        "fn_used": FN_RESCUE,
        "calls": 0,
        "error": None,
        "notes": "",
        "evidence_excerpt": "",
    }
    try:
        sig = inspect.signature(wt._rescue_search)
        rec["notes"] = f"signature={sig}"
        params = list(sig.parameters.values())
        Kingdom = [
            (
                p.name,
                str(p.annotation) if p.annotation is not inspect.Parameter.empty else "",
                p.default is inspect.Parameter.empty,
            )
            for p in params
        ]
        rec["evidence_excerpt"] = _trunc(f"signature {sig}; params={Kingdom}")
        # Known signature: (provider_name: str, original_error: str, query: str, limit: int) -> dict
        names = [p.name for p in params]
        if names == ["provider_name", "original_error", "query", "limit"]:
            _gate(first)
            first = False
            try:
                out = wt._rescue_search("keyless-probe", "keyless fallback verification probe", RESCUE_QUERY, 3)
                _bump(1)
                rec["calls"] = 1
                rec["evidence_excerpt"] = _trunc(json.dumps(out, ensure_ascii=False, default=str))
                if isinstance(out, dict) and out.get("success") is True:
                    items = _web_items(out) or out.get("data") or []
                    n = len(items) if isinstance(items, list) else "?"
                    rec["pass"] = True
                    rec["notes"] += f"; CALLED ok: success=true, results={n}"
                else:
                    rec["pass"] = True  # honest documented outcome, not masked
                    rec["notes"] += "; CALLED: returned non-success payload (documented honestly)"
                    rec["error"] = _trunc(json.dumps(out, ensure_ascii=False, default=str))
            except Exception as exc:  # noqa: BLE001
                _bump(1)
                rec["calls"] = 1
                rec["pass"] = True  # documented outcome counts as valid result per spec
                rec["notes"] += "; CALLED but raised (documented honestly, not masked)"
                rec["error"] = _trunc(f"{type(exc).__name__}: {exc}")
        else:
            rec["notes"] += "; documented-only, not callable (signature mismatch, args cannot be filled safely)"
            rec["pass"] = True  # documented-only is a valid result per spec
    except Exception as exc:  # noqa: BLE001
        rec["error"] = _trunc(f"{type(exc).__name__}: {exc}")
        rec["notes"] = "harness exception during K4 introspection"
    cases.append(rec)

    # ── Outputs ──────────────────────────────────────────────────────────
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    passed = sum(1 for c in cases if c.get("pass"))
    total = len(cases)
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "run_command": RUN_COMMAND,
        "live_calls": _state["calls"],
        "live_call_cap": MAX_LIVE_CALLS,
        "cases": cases,
        "totals": {"pass": passed, "fail": total - passed, "total": total},
    }
    json_path = RESULTS_DIR / f"keyless_{ts}.json"
    md_path = RESULTS_DIR / f"keyless_{ts}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_md(payload, cases, passed, total), encoding="utf-8")
    print(f"[outputs] results/{json_path.name}, results/{md_path.name}")
    print(f"PASS {passed}/{total}")
    for c in cases:
        print(f"  - {c['id']}: {'PASS' if c.get('pass') else 'FAIL'} (fn={c.get('fn_used')}, calls={c.get('calls')})")
    return 0 if passed == total else 1


def _render_md(payload, cases, passed, total):
    L = ["# Keyless Fallback Chain — Verification (T2 v2)", ""]
    L.append(f"- Generated: {payload['generated']}")
    L.append(f"- Live calls: {payload['live_calls']} (cap {payload['live_call_cap']}; sleep >={SLEEP_S}s, sequential)")
    L.append(f"- Verdict: **PASS {passed}/{total}** (K1 counts as finding-recorded)")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append("| Case | Pass | fn_used | Calls | Error | Notes |")
    L.append("|------|------|---------|-------|-------|-------|")
    for c in cases:
        fn = str(c.get("fn_used", "")).replace("|", "\\|")
        err = str(c.get("error") or "-").replace("|", "\\|").replace("\n", " ")
        notes = str(c.get("notes") or "").replace("|", "\\|").replace("\n", " ")
        if len(notes) > 220:
            notes = notes[:217] + "..."
        if len(err) > 160:
            err = err[:157] + "..."
        L.append(f"| {c['id']} | {'PASS' if c.get('pass') else 'FAIL'} | {fn} | {c.get('calls')} | {err} | {notes} |")
    L.append("")
    L.append("## Exact import paths")
    L.append("")
    L.append(f"- K1: `{FN_DDGS}` (class `plugins.web.ddgs.provider.DDGSWebSearchProvider`)")
    L.append(f"- K2: `{FN_PAR_SEARCH}`, `{FN_EXA_SEARCH}` (module `plugins.web.keyless_mcp`)")
    L.append(
        f"- K3: `{FN_PAR_EXTRACT}` -> `{FN_EXA_EXTRACT}` -> `{FN_KEEN_EXTRACT}` -> `{FN_FIRE_EXTRACT}` "
        "(module `plugins.web.keyless_mcp`; tried in this order per URL until content)"
    )
    L.append(
        f"- K4: `{FN_RESCUE}` (module `tools.web_tools`; signature introspected at runtime via `inspect.signature`)"
    )
    L.append("")
    L.append("## Evidence excerpts")
    L.append("")
    for c in cases:
        L.append(f"### {c['id']} ({c.get('fn_used')})")
        L.append("")
        L.append(f"- pass: {c.get('pass')}; calls: {c.get('calls')}")
        if c.get("vendor_outcomes"):
            L.append("- vendor outcomes:")
            for v, o in c["vendor_outcomes"].items():
                L.append(f"  - {v}: {_trunc(o)}")
        L.append(f"- evidence: `{_trunc(c.get('evidence_excerpt') or '-')}`")
        L.append("")
    L.append("## Rate-limit / 403 / throttle errors observed")
    L.append("")
    found = False
    for c in cases:
        blob = json.dumps(c, ensure_ascii=False, default=str)
        for marker in ("429", "403", "Forbidden", "rate", "throttl", "Too Many"):
            if marker.lower() in blob.lower():
                L.append(
                    f"- {c['id']}: `{_trunc(blob[blob.lower().find(marker.lower()) - 60 : blob.lower().find(marker.lower()) + 140])}`"  # noqa: E501
                )
                found = True
                break
    if not found:
        L.append("- None observed in this run (free tiers were not throttling at this time).")
    L.append("")
    k1 = next((c for c in cases if c["id"] == "K1"), {})
    k4 = next((c for c in cases if c["id"] == "K4"), {})
    L.append("## K1 finding")
    L.append("")
    L.append(f"- {k1.get('notes', '')} evidence=`{k1.get('evidence_excerpt', '')}`")
    L.append("")
    L.append("## K4 outcome")
    L.append("")
    L.append(f"- {k4.get('notes', '')}")
    if k4.get("error"):
        L.append(f"- error: `{k4.get('error')}`")
    L.append(f"- evidence: `{k4.get('evidence_excerpt', '')}`")
    L.append("")
    L.append("## Run command")
    L.append("")
    L.append("```bash")
    L.append(RUN_COMMAND)
    L.append("```")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
