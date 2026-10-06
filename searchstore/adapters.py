"""Adapters: load battery/keyless JSON and report Markdown into SearchStore-ready dicts (contract §5)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

_PROVIDER_RE = re.compile(r"Web search via ([^:]+):")


def _read_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _parse_provider(evidence: list[str] | None) -> str | None:
    """Extract the provider name from a 'Web search via <name>:' evidence line, if present."""
    for line in evidence or []:
        match = _PROVIDER_RE.search(line)
        if match:
            return match.group(1).strip()
    return None


def _file_meta(data: dict) -> dict:
    return {key: value for key, value in data.items() if key != "cases"}


def load_battery_json(path: str | Path) -> dict:
    """Load a battery-run JSON file into searches + events (contract §5).

    Searches come from cases where kind == "search": query = case["input"],
    provider parsed from a 'Web search via <name>:' evidence line (else None),
    ts = file "generated", latency_ms = round(latency_s * 1000).
    Events: one "battery_case" event per case.
    """
    data = _read_json(path)
    generated = data.get("generated")
    searches: list[dict] = []
    events: list[dict] = []
    for case in data.get("cases", []):
        events.append({"kind": "battery_case", "payload": case})
        if case.get("kind") != "search":
            continue
        latency_s = case.get("latency_s")
        searches.append(
            {
                "query": case["input"],
                "provider": _parse_provider(case.get("evidence")),
                "ts": generated,
                "latency_ms": round(latency_s * 1000) if latency_s is not None else None,
                "result_count": case.get("n_results", 0),
                "meta": {
                    "case_id": case.get("id"),
                    "pass": case.get("pass"),
                    "backends": case.get("backends", []),
                },
            }
        )
    return {
        "kind": "battery",
        "generated": generated,
        "searches": searches,
        "events": events,
        "meta": _file_meta(data),
    }


def load_keyless_json(path: str | Path) -> dict:
    """Load a keyless-run JSON file into events only (contract §5).

    Queries are not recoverable from keyless files -> "searches" is always [].
    """
    data = _read_json(path)
    events = [{"kind": "keyless_case", "payload": case} for case in data.get("cases", [])]
    return {
        "kind": "keyless",
        "generated": data.get("generated"),
        "searches": [],
        "events": events,
        "meta": _file_meta(data),
    }


def load_report_md(path: str | Path, sources: list[dict] | None = None) -> dict:
    """Load a report Markdown file into a report dict (contract §5).

    slug = file stem; title = first "# " line (content after the prefix) or the
    stem; word_count = len(text.split()); sources pass through (default []).
    """
    report_path = Path(path)
    text = report_path.read_text(encoding="utf-8")
    title = report_path.stem
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return {
        "kind": "report",
        "slug": report_path.stem,
        "title": title,
        "text": text,
        "word_count": len(text.split()),
        "sources": sources if sources else [],
        "created_at": datetime.fromtimestamp(report_path.stat().st_mtime).isoformat(),
        "meta": {"path": str(path)},
    }
