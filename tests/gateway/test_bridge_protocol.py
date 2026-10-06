"""Bridge worker protocol tests — ``--echo`` round-trip via sys.executable.

Spawns ``gateway/bridge/worker.py --echo`` as a subprocess and exercises the
frozen JSON-lines protocol (request ``{"id","op","params"}`` -> response
``{"id","ok","result","error"}``) for all three ops plus error paths — fully
hermetic, no Hermes imports, no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER = REPO_ROOT / "gateway" / "bridge" / "worker.py"


def _roundtrip(requests: list[str]) -> list[dict]:
    """Send request lines to the echo worker; return decoded response objects."""
    payload = "\n".join(requests) + "\n"
    proc = subprocess.run(
        [sys.executable, str(WORKER), "--echo"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        timeout=60,
    )
    assert proc.returncode == 0, f"worker exited {proc.returncode}: {proc.stderr[-500:]}"
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_ping_pong():
    (resp,) = _roundtrip([json.dumps({"id": 1, "op": "ping", "params": {}})])
    assert resp["id"] == 1
    assert resp["ok"] is True
    assert resp["result"]["pong"] is True
    assert resp["result"]["python"]
    assert resp["error"] is None


def test_search_items_shape():
    (resp,) = _roundtrip([json.dumps({"id": 7, "op": "search", "params": {"query": "hello", "max_results": 2}})])
    assert resp["id"] == 7
    assert resp["ok"] is True
    items = resp["result"]
    assert isinstance(items, list) and len(items) >= 1
    for item in items:
        assert set(item) >= {"title", "url", "description", "position"}
        assert item["url"].startswith("http")


def test_extract_items_shape_and_char_limit():
    req = {"id": 9, "op": "extract", "params": {"urls": ["https://a.test/x", "https://b.test/y"], "char_limit": 10}}
    (resp,) = _roundtrip([json.dumps(req)])
    assert resp["id"] == 9
    assert resp["ok"] is True
    items = resp["result"]
    assert len(items) == 2
    for url, item in zip(("https://a.test/x", "https://b.test/y"), items, strict=True):
        assert item["url"] == url
        assert item["error"] is None
        assert len(item["content"]) <= 10


def test_all_ops_one_session_and_error_paths():
    """One worker session handles every op + malformed input without dying."""
    requests = [
        json.dumps({"id": 1, "op": "ping", "params": {}}),
        json.dumps({"id": 2, "op": "search", "params": {"query": "multi", "max_results": 3}}),
        json.dumps({"id": 3, "op": "extract", "params": {"urls": ["https://c.test"], "char_limit": 50}}),
        "this is not json",
        json.dumps({"id": 5, "op": "bogus", "params": {}}),
        json.dumps({"id": 6, "op": "ping", "params": {}}),
    ]
    responses = _roundtrip(requests)
    assert len(responses) == 6
    by_id = {r["id"]: r for r in responses}
    assert by_id[1]["ok"] and by_id[1]["result"]["pong"]
    assert by_id[2]["ok"] and isinstance(by_id[2]["result"], list)
    assert by_id[3]["ok"] and by_id[3]["result"][0]["url"] == "https://c.test"
    bad_json = next(r for r in responses if r["id"] is None)
    assert bad_json["ok"] is False and bad_json["error"]
    assert by_id[5]["ok"] is False and "unknown op" in by_id[5]["error"]
    assert by_id[6]["ok"] is True  # loop survives errors


def test_worker_is_importable_as_module():
    """worker.py has no import-time side effects or Hermes imports."""
    import gateway.bridge.worker as worker

    assert callable(worker.main)
    assert "tools.web_tools" not in sys.modules
    assert "plugins.web.keyless_mcp" not in sys.modules
