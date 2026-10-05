"""Offline unit + pipeline tests for ``test_keyless_fallback.py``.

Fully deterministic: no network, no sleeps. Fake Hermes modules are injected into
``sys.modules`` (all parents included) so ``main()`` exercises the keyless fallback
and rescue paths without touching the real Hermes install.
"""

import json
import sys
import types

import test_keyless_fallback as mod


# ─── _trunc ───────────────────────────────────────────────────────────────────
def test_trunc_short():
    assert mod._trunc("abc", 10) == "abc"


def test_trunc_long():
    out = mod._trunc("a" * 100, 10)
    assert out == "a" * 10 + "...[truncated]"


def test_trunc_non_str():
    assert mod._trunc(12345, 10) == "12345"


# ─── _web_items ───────────────────────────────────────────────────────────────
def test_web_items_canonical():
    assert mod._web_items({"data": {"web": [1, 2]}}) == [1, 2]


def test_web_items_alternates():
    assert mod._web_items({"results": [3, 4]}) == [3, 4]
    assert mod._web_items({"data": {"items": [5, 6]}}) == [5, 6]


def test_web_items_missing_returns_none():
    assert mod._web_items({"data": {}}) is None


def test_web_items_non_dict_returns_none():
    assert mod._web_items("nope") is None
    assert mod._web_items(None) is None


# ─── _extract_content ─────────────────────────────────────────────────────────
def test_extract_content_priority_content_over_raw_over_text():
    content, err = mod._extract_content({"content": "primary", "raw_content": "raw", "text": "txt"})
    assert content == "primary"
    assert err is None


def test_extract_content_priority_raw_over_text():
    content, err = mod._extract_content({"raw_content": "raw", "text": "txt"})
    assert content == "raw"
    assert err is None


def test_extract_content_error_path():
    content, err = mod._extract_content({"url": "u", "error": "down"})
    assert content == ""
    assert err == "down"


def test_extract_content_empty_path():
    content, err = mod._extract_content({})
    assert content == ""
    assert err == "(empty content, no error field)"


def test_extract_content_non_dict():
    content, err = mod._extract_content("raw-string")
    assert content == ""
    assert err == "raw-string"


# ─── _render_md ───────────────────────────────────────────────────────────────
def test_render_md_contains_markers():
    cases = [
        {"id": "K1", "pass": True, "fn_used": "f", "calls": 1, "error": None, "notes": "n", "evidence_excerpt": "e"},
    ]
    payload = {"generated": "2026-01-01T00:00:00", "live_calls": 1, "live_call_cap": 12}
    md = mod._render_md(payload, cases, 6, 6)
    assert "PASS 6/6" in md
    assert mod.FN_DDGS in md
    assert mod.FN_RESCUE in md


# ─── Fake Hermes modules + offline pipeline ───────────────────────────────────
def _install_keyless_fakes(monkeypatch, tmp_path):
    # plugins.web.ddgs.provider
    provider_mod = types.ModuleType("plugins.web.ddgs.provider")

    class DDGSWebSearchProvider:
        def search(self, query, limit=5):
            return {
                "success": False,
                "error": "ddgs package is not installed (documented-unavailable tier)",
            }

    provider_mod.DDGSWebSearchProvider = DDGSWebSearchProvider

    # plugins.web.keyless_mcp
    keyless_mod = types.ModuleType("plugins.web.keyless_mcp")

    def _search_ok(query, limit=3):
        return {
            "success": True,
            "data": {
                "web": [
                    {"title": "a", "url": "https://a.example/1"},
                    {"title": "b", "url": "https://b.example/2"},
                ]
            },
        }

    def _parallel_extract(urls):
        return [{"url": urls[0], "content": "y" * 800}]

    def _extract_err(urls):
        return [{"url": urls[0], "error": "fake down"}]

    keyless_mod.parallel_search_keyless = _search_ok
    keyless_mod.exa_search_keyless = _search_ok
    keyless_mod.parallel_extract_keyless = _parallel_extract
    keyless_mod.exa_extract_keyless = _extract_err
    keyless_mod.keenable_extract_keyless = _extract_err
    keyless_mod.firecrawl_extract_keyless = _extract_err

    # plugins.web.ddgs
    ddgs_mod = types.ModuleType("plugins.web.ddgs")
    ddgs_mod.provider = provider_mod

    # plugins.web
    web_mod = types.ModuleType("plugins.web")
    web_mod.keyless_mcp = keyless_mod
    web_mod.ddgs = ddgs_mod

    # plugins
    plugins_mod = types.ModuleType("plugins")
    plugins_mod.web = web_mod

    # tools.web_tools
    web_tools_mod = types.ModuleType("tools.web_tools")

    def _rescue_search(provider_name, original_error, query, limit):
        return {
            "success": True,
            "data": {
                "web": [
                    {"title": "r1", "url": "https://r.example/1"},
                    {"title": "r2", "url": "https://r.example/2"},
                    {"title": "r3", "url": "https://r.example/3"},
                ]
            },
        }

    web_tools_mod._rescue_search = _rescue_search

    # tools
    tools_mod = types.ModuleType("tools")
    tools_mod.web_tools = web_tools_mod

    for name, module in (
        ("plugins", plugins_mod),
        ("plugins.web", web_mod),
        ("plugins.web.ddgs", ddgs_mod),
        ("plugins.web.ddgs.provider", provider_mod),
        ("plugins.web.keyless_mcp", keyless_mod),
        ("tools", tools_mod),
        ("tools.web_tools", web_tools_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setattr(mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(mod, "_gate", lambda first=False: None)
    monkeypatch.setitem(mod._state, "calls", 0)


def test_keyless_pipeline_offline_pass_6(monkeypatch, tmp_path, capsys):
    _install_keyless_fakes(monkeypatch, tmp_path)
    rc = mod.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert "PASS 6/6" in out

    json_files = list(tmp_path.glob("keyless_*.json"))
    md_files = list(tmp_path.glob("keyless_*.md"))
    assert len(json_files) == 1
    assert len(md_files) == 1

    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert {"generated", "live_calls", "cases", "totals"} <= set(payload)
    assert payload["totals"] == {"pass": 6, "fail": 0, "total": 6}
    assert len(payload["cases"]) == 6
