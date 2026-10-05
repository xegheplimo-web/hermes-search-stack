"""Offline unit + pipeline tests for ``verify_web_stack.py``.

Fully offline: no network, no sleeps, no Hermes imports. The live web layer
(``WEB_SEARCH`` / ``WEB_EXTRACT``) is replaced with deterministic fakes injected
onto the module under test, and ``_ensure_imports`` / ``_gate`` are neutralised.
"""

import asyncio
import json
import logging

import verify_web_stack as mod


# ─── _parse_search ────────────────────────────────────────────────────────────
def test_parse_search_valid():
    raw = json.dumps({"success": True, "data": {"web": [{"title": "t", "url": "u"}]}})
    results, err, notes = mod._parse_search(raw)
    assert results == [{"title": "t", "url": "u"}]
    assert err is None
    assert notes == ""


def test_parse_search_non_json():
    results, err, notes = mod._parse_search("not json{")
    assert results is None
    assert "non-JSON search output" in err
    assert "raw excerpt" in notes


def test_parse_search_success_false():
    results, err, notes = mod._parse_search(json.dumps({"success": False, "error": "boom"}))
    assert results is None
    assert err == "success=false: boom"
    assert notes == ""


def test_parse_search_missing_data_web():
    results, err, notes = mod._parse_search(json.dumps({"success": True, "data": {}}))
    assert results is None
    assert err == "no data.web[] in search output"
    assert "raw excerpt" in notes


# ─── _parse_extract ───────────────────────────────────────────────────────────
def test_parse_extract_ok():
    raw = json.dumps({"success": True, "results": [{"url": "u", "content": "x" * 2000}]})
    ok, chars, err, notes = mod._parse_extract(raw)
    assert ok is True
    assert chars == 2000
    assert err is None
    assert notes == ""


def test_parse_extract_per_url_error():
    raw = json.dumps({"success": True, "results": [{"url": "u", "error": "down"}]})
    ok, chars, err, notes = mod._parse_extract(raw)
    assert ok is False
    assert err == "per-url error: down"
    assert notes == ""


def test_parse_extract_short_content():
    raw = json.dumps({"success": True, "results": [{"url": "u", "content": "x" * 10}]})
    ok, chars, err, notes = mod._parse_extract(raw)
    assert ok is False
    assert chars == 10
    assert err == f"content length 10 < {mod.MIN_EXTRACT_CHARS}"
    assert notes == ""


def test_parse_extract_success_false():
    ok, chars, err, notes = mod._parse_extract(json.dumps({"success": False, "error": "nope"}))
    assert ok is False
    assert chars == 0
    assert err == "success=false: nope"
    assert notes == ""


def test_parse_extract_non_json():
    ok, chars, err, notes = mod._parse_extract("<html>not json</html>")
    assert ok is False
    assert chars == 0
    assert "non-JSON extract output" in err
    assert "raw excerpt" in notes


# ─── _article_candidates ──────────────────────────────────────────────────────
def test_article_candidates_prefers_path_depth_dedupes_and_max_n():
    results = [
        {"url": "https://example.com"},  # depth 0 (homepage)
        {"url": "https://example.com/a/b"},  # depth 2
        {"url": "https://example.com/a/b"},  # duplicate -> dropped
        {"url": "https://example.com/c"},  # depth 1
        {"url": "https://example.com/d"},  # depth 1
    ]
    cands = mod._article_candidates(results, max_n=3)
    assert cands == [
        "https://example.com/a/b",
        "https://example.com/c",
        "https://example.com/d",
    ]
    assert len(cands) == 3


def test_article_candidates_none_and_empty():
    assert mod._article_candidates(None) == []
    assert mod._article_candidates([]) == []


# ─── _extract_backends ────────────────────────────────────────────────────────
def test_extract_backends_via_markers_and_tags():
    lines = [
        "INFO tools.web_tools: Web search via perplexity",
        "INFO tools.web_tools: Web extract via parallel (keyless)",
        "INFO plugins.web: Parallel keyless extract",
        "INFO tools.web_tools: Perplexity search: 'x' (managed)",
    ]
    backends = mod._extract_backends(lines)
    assert "perplexity" in backends
    assert "parallel" in backends
    assert "keyless" in backends
    assert "managed" in backends


def test_extract_backends_empty():
    assert mod._extract_backends([]) == []


# ─── ListHandler ──────────────────────────────────────────────────────────────
def test_list_handler_captures_formatted_lines():
    handler = mod.ListHandler()
    logger = logging.getLogger("test_list_handler_capture")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.info("hello %s", "world")
    finally:
        logger.removeHandler(handler)
    assert any("hello world" in line for line in handler.lines)
    assert any(line.startswith("INFO test_list_handler_capture:") for line in handler.lines)


# ─── _crash_rec ───────────────────────────────────────────────────────────────
def test_crash_rec_search_shape():
    rec = mod._crash_rec("S9", "search", "q", ValueError("boom"), "label")
    assert rec["id"] == "S9"
    assert rec["kind"] == "search"
    assert rec["input"] == "q"
    assert rec["pass"] is False
    assert rec["n_results"] == 0
    assert "chars" not in rec
    assert rec["error"] == "ValueError: boom"
    assert rec["notes"] == "label"


def test_crash_rec_extract_shape():
    rec = mod._crash_rec("E9", "extract", "https://x", RuntimeError("x"), "label")
    assert rec["id"] == "E9"
    assert rec["kind"] == "extract"
    assert rec["pass"] is False
    assert rec["chars"] == 0
    assert "n_results" not in rec
    assert rec["error"] == "RuntimeError: x"


# ─── Fake web layer + offline pipeline ────────────────────────────────────────
def _fake_search(query, limit=5):
    items = [
        {
            "title": f"title {i}",
            "url": f"https://example.com/article/{i}",
            "description": "AI news October 2026",
            "position": i,
        }
        for i in range(1, 6)
    ]
    return json.dumps({"success": True, "data": {"web": items}})


async def _fake_extract_ok(urls, format=None, char_limit=None):
    return json.dumps({"success": True, "results": [{"url": urls[0], "title": "t", "content": "x" * 2000}]})


async def _fake_extract_err(urls, format=None, char_limit=None):
    return json.dumps({"success": True, "results": [{"url": urls[0], "error": "fake down"}]})


def _install_fakes(monkeypatch, tmp_path, extract_fake):
    monkeypatch.setattr(mod, "WEB_SEARCH", _fake_search)
    monkeypatch.setattr(mod, "WEB_EXTRACT", extract_fake)
    monkeypatch.setattr(mod, "IMPORT_ERROR", None)
    monkeypatch.setattr(mod, "_ensure_imports", lambda: None)
    monkeypatch.setattr(mod, "_gate", lambda: None)
    monkeypatch.setattr(mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setitem(mod._state, "calls", 0)
    monkeypatch.setitem(mod._state, "last_end", None)
    monkeypatch.setattr(mod._handler, "lines", [])


def test_full_pipeline_offline_all_pass(monkeypatch, tmp_path):
    _install_fakes(monkeypatch, tmp_path, _fake_extract_ok)
    cases = asyncio.run(mod._amain())

    assert len(cases) == 9
    assert all(c["pass"] for c in cases)
    passed = sum(1 for c in cases if c["pass"])
    totals = {"pass": passed, "fail": 9 - passed, "total": 9}
    assert totals == {"pass": 9, "fail": 0, "total": 9}

    json_files = list(tmp_path.glob("battery_*.json"))
    md_files = list(tmp_path.glob("battery_*.md"))
    assert len(json_files) == 1
    assert len(md_files) == 1

    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert {"generated", "live_calls", "cases", "totals"} <= set(payload)
    assert payload["totals"]["pass"] == 9
    assert payload["totals"]["fail"] == 0
    assert payload["totals"]["total"] == 9
    assert len(payload["cases"]) == 9


def test_full_pipeline_offline_extract_failure_returns_1(monkeypatch, tmp_path, capsys):
    _install_fakes(monkeypatch, tmp_path, _fake_extract_err)
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert "PASS 5/9" in out
