"""Hermetic unit tests for the ``search-prefetch`` plugin (``plugins/search-prefetch/__init__.py``).

No network and no Hermes runtime: the plugin is loaded straight from its file path and every
Hermes collaborator is faked / monkeypatched. The plugin directory has a hyphen in its name, so it
is imported via ``importlib`` rather than as a package.
"""

import importlib.util
import json
import re
import sys
import threading
import types
from pathlib import Path

import pytest

_PLUGIN_FILE = Path(__file__).resolve().parents[1] / "plugins" / "search-prefetch" / "__init__.py"


@pytest.fixture
def plugin():
    """Fresh module instance per test so module-level locks/in-flight state stay isolated."""
    spec = importlib.util.spec_from_file_location("search_prefetch_plugin", _PLUGIN_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["search_prefetch_plugin"] = module
    spec.loader.exec_module(module)
    return module


def _search_result(urls):
    return json.dumps({"success": True, "data": {"web": [{"url": u} for u in urls]}})


def _install_fake_keyless(monkeypatch, *, extract=None, ring=("exa", "parallel", "firecrawl", "keenable"),
                          cursor=0, tiers=None):
    """Inject a fake ``plugins.web.keyless_mcp`` with a recording extractor table.

    Returns ``(fake_module, calls)`` where ``calls`` records each extractor invocation as the
    URL list it was handed.
    """
    fake = types.ModuleType("plugins.web.keyless_mcp")
    calls = []

    def _default_extract(urls):
        calls.append(list(urls))
        return [{"url": urls[0], "title": "T", "content": "body", "raw_content": "body"}]

    extractor = extract if extract is not None else _default_extract
    fake._KEYLESS_EXTRACTORS = {name: extractor for name in ring}
    fake._KEYLESS_RING = ring
    fake._ring_cursor = cursor
    tier_map = dict(tiers or {})
    fake.provider_tier = lambda name: tier_map.get(name, "auto")
    fake.use_keyless = lambda name, key: True
    monkeypatch.setitem(sys.modules, "plugins", types.ModuleType("plugins"))
    monkeypatch.setitem(sys.modules, "plugins.web", types.ModuleType("plugins.web"))
    monkeypatch.setitem(sys.modules, "plugins.web.keyless_mcp", fake)
    return fake, calls


def _install_fake_config(monkeypatch, web_cfg):
    """Inject a fake ``hermes_cli.config.load_config_readonly`` returning ``{"web": web_cfg}``."""
    fake_cfg = types.ModuleType("hermes_cli.config")
    fake_cfg.load_config_readonly = lambda: {"web": dict(web_cfg)}
    fake_pkg = types.ModuleType("hermes_cli")
    fake_pkg.config = fake_cfg
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", fake_cfg)


def _install_fake_selection(monkeypatch, selection):
    """Inject a fake ``tools.tool_backend_helpers.read_selection`` returning *selection*."""
    helpers = types.ModuleType("tools.tool_backend_helpers")
    helpers.read_selection = lambda area: selection
    tools_pkg = types.ModuleType("tools")
    tools_pkg.tool_backend_helpers = helpers
    monkeypatch.setitem(sys.modules, "tools", tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.tool_backend_helpers", helpers)


def _install_fake_cache(monkeypatch, *, warm=False, puts=None, gets=None):
    """Inject a fake ``tools.web_result_cache`` recording ``extract_cache_put/get`` calls."""
    fake = types.ModuleType("tools.web_result_cache")
    put_calls = puts if puts is not None else []
    get_calls = gets if gets is not None else []

    def extract_cache_put(url, content, title="", format=None, provider=""):
        put_calls.append({"url": url, "content": content, "title": title, "format": format, "provider": provider})

    def extract_cache_get(url, format=None, provider=""):
        get_calls.append({"url": url, "format": format, "provider": provider})
        return {"url": url, "content": "cached"} if warm else None

    fake.extract_cache_put = extract_cache_put
    fake.extract_cache_get = extract_cache_get
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.web_result_cache", fake)
    return put_calls, get_calls


def _log_lines(monkeypatch, tmp_path):
    path = tmp_path / "search-prefetch.log"
    monkeypatch.setenv("HERMES_SEARCH_PREFETCH_LOG", str(path))
    return path


# ─── register ──────────────────────────────────────────────────────────────────

def test_register_hooks_post_tool_call(plugin):
    class FakeCtx:
        def __init__(self):
            self.hooks = {}

        def register_hook(self, name, callback):
            self.hooks[name] = callback

    ctx = FakeCtx()
    plugin.register(ctx)
    assert ctx.hooks["post_tool_call"] is plugin._on_post_tool_call
    assert plugin._CTX is ctx


# ─── URL extraction ────────────────────────────────────────────────────────────

def test_extract_urls_order_cap_and_dedupe(plugin):
    data = {"data": {"web": [{"url": "https://a/1"}, {"url": "https://b/2"}, {"url": "https://c/3"}]}}
    assert plugin._extract_urls(data) == ["https://a/1", "https://b/2"]


def test_extract_urls_dedupes_and_strips(plugin):
    data = {"data": {"web": [{"url": " https://a/1 "}, {"url": "https://a/1"}, {"url": "https://b/2"}]}}
    assert plugin._extract_urls(data) == ["https://a/1", "https://b/2"]


def test_extract_urls_skips_bad_items(plugin):
    data = {"data": {"web": [None, {}, {"url": 5}, {"url": "  "}, {"url": "https://ok/1"}]}}
    assert plugin._extract_urls(data) == ["https://ok/1"]


def test_extract_urls_non_list_web(plugin):
    assert plugin._extract_urls({"data": {"web": "nope"}}) == []
    assert plugin._extract_urls({}) == []


# ─── Hook dispatch + gates ─────────────────────────────────────────────────────

def test_hook_ignores_non_web_search(plugin, monkeypatch):
    spawned = []
    monkeypatch.setattr(plugin, "_prefetch_worker", lambda urls: spawned.append(urls))
    plugin._on_post_tool_call(tool_name="web_extract", result=_search_result(["https://a/1"]))
    assert spawned == []


def test_hook_ignores_failed_result(plugin, monkeypatch):
    spawned = []
    monkeypatch.setattr(plugin, "_prefetch_worker", lambda urls: spawned.append(urls))
    plugin._on_post_tool_call(tool_name="web_search", result=json.dumps({"success": False, "error": "x"}))
    assert spawned == []


def test_hook_ignores_unparseable_result(plugin, monkeypatch):
    spawned = []
    monkeypatch.setattr(plugin, "_prefetch_worker", lambda urls: spawned.append(urls))
    plugin._on_post_tool_call(tool_name="web_search", result="not json")
    assert spawned == []


def test_gate_disabled_env_kill_switch(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_SEARCH_PREFETCH", "0")
    spawned = []
    monkeypatch.setattr(plugin, "_prefetch_worker", lambda urls: spawned.append(urls))
    plugin._on_post_tool_call(tool_name="web_search", result=_search_result(["https://a/1"]))
    assert spawned == []
    assert log.read_text(encoding="utf-8").strip().endswith("| https://a/1 |  | SKIP:disabled")


def test_gate_disabled_config(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    monkeypatch.delenv("HERMES_SEARCH_PREFETCH", raising=False)
    monkeypatch.setattr(plugin, "_config_enabled", lambda: False)
    spawned = []
    monkeypatch.setattr(plugin, "_prefetch_worker", lambda urls: spawned.append(urls))
    plugin._on_post_tool_call(tool_name="web_search", result=_search_result(["https://a/1"]))
    assert spawned == []
    assert "SKIP:disabled" in log.read_text(encoding="utf-8")


def test_enabled_env_overrides_config(plugin, monkeypatch):
    monkeypatch.setenv("HERMES_SEARCH_PREFETCH", "0")
    monkeypatch.setattr(plugin, "_config_enabled", lambda: True)
    assert plugin._enabled() is False


def test_config_enabled_reads_ctx(plugin):
    class FakeCtx:
        def get_config(self, key, default=None):
            return {"enabled": False}.get(key, default)

    plugin._CTX = FakeCtx()
    assert plugin._config_enabled() is False


def test_coerce_bool_variants(plugin):
    assert plugin._coerce_bool("false", True) is False
    assert plugin._coerce_bool("OFF", True) is False
    assert plugin._coerce_bool(0, True) is False
    assert plugin._coerce_bool("yes", False) is True
    assert plugin._coerce_bool(None, True) is True


def test_gate_busy_single_worker(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    monkeypatch.delenv("HERMES_SEARCH_PREFETCH", raising=False)
    spawned = []
    monkeypatch.setattr(plugin, "_prefetch_worker", lambda urls: spawned.append(urls))
    assert plugin._worker_lock.acquire(blocking=False)  # simulate an active worker
    try:
        plugin._on_post_tool_call(tool_name="web_search", result=_search_result(["https://a/1"]))
    finally:
        plugin._worker_lock.release()
    assert spawned == []
    assert "SKIP:busy" in log.read_text(encoding="utf-8")


def test_non_blocking_spawn(plugin, monkeypatch, tmp_path):
    """The hook must return immediately while the worker thread is still running."""
    _log_lines(monkeypatch, tmp_path)
    monkeypatch.delenv("HERMES_SEARCH_PREFETCH", raising=False)
    monkeypatch.setattr(plugin, "_config_enabled", lambda: True)
    started = threading.Event()
    release = threading.Event()

    def slow_worker(urls):
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(plugin, "_prefetch_worker", slow_worker)
    plugin._on_post_tool_call(tool_name="web_search", result=_search_result(["https://a/1"]))
    assert started.wait(timeout=2)  # worker actually ran in the background
    assert not release.is_set()
    release.set()


def test_hook_fail_open_on_internal_error(plugin, monkeypatch):
    def boom(_data):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin, "_extract_urls", boom)
    # Must not raise into the pipeline.
    plugin._on_post_tool_call(tool_name="web_search", result=_search_result(["https://a/1"]))


def test_hook_fail_open_when_enabled_raises(plugin, monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin, "_enabled", boom)
    plugin._on_post_tool_call(tool_name="web_search", result=_search_result(["https://a/1"]))



# ─── Worker gates ──────────────────────────────────────────────────────────────

def test_worker_provider_not_keyless(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: None)
    plugin._prefetch_worker(["https://a/1", "https://b/2"])
    text = log.read_text(encoding="utf-8")
    assert text.count("SKIP:provider-not-keyless") == 2


def test_worker_unsafe_url(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: "exa")
    monkeypatch.setattr(plugin, "_is_safe_public_url", lambda url: False)
    plugin._prefetch_worker(["http://localhost/1"])
    assert "SKIP:unsafe-url" in log.read_text(encoding="utf-8")


def test_worker_cache_warm_skips_fetch(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    _, calls = _install_fake_keyless(monkeypatch)
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: "exa")
    monkeypatch.setattr(plugin, "_is_safe_public_url", lambda url: True)
    monkeypatch.setattr(plugin, "_is_cache_warm", lambda url, name: True)
    plugin._prefetch_worker(["https://a/1"])
    assert calls == []  # no network hop when already warm
    assert "SKIP:cache-warm" in log.read_text(encoding="utf-8")


def test_worker_in_flight_single_flight(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    _, calls = _install_fake_keyless(monkeypatch)
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: "exa")
    monkeypatch.setattr(plugin, "_is_safe_public_url", lambda url: True)
    monkeypatch.setattr(plugin, "_is_cache_warm", lambda url, name: False)
    assert plugin._claim_inflight("https://a/1", "exa")  # another worker owns it
    try:
        plugin._prefetch_worker(["https://a/1"])
    finally:
        plugin._release_inflight("https://a/1", "exa")
    assert calls == []
    assert "SKIP:in-flight" in log.read_text(encoding="utf-8")


# ─── Worker store + cache-key parity ───────────────────────────────────────────

def test_worker_stores_with_exact_cache_put_args(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    puts, gets = _install_fake_cache(monkeypatch, warm=False)
    monkeypatch.setattr(plugin.time, "sleep", lambda seconds: None)
    _, calls = _install_fake_keyless(monkeypatch)
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: "exa")
    monkeypatch.setattr(plugin, "_is_safe_public_url", lambda url: True)

    plugin._prefetch_worker(["https://a.example/1"])

    # Cache-key parity: exactly what tools/web_tools_extract.py writes.
    assert puts == [{
        "url": "https://a.example/1", "content": "body", "title": "T",
        "format": "markdown", "provider": "exa",
    }]
    assert gets == [{"url": "https://a.example/1", "format": "markdown", "provider": "exa"}]
    assert calls == [["https://a.example/1"]]
    line = log.read_text(encoding="utf-8").strip()
    assert line.endswith("| https://a.example/1 | exa | STORED")


def test_worker_records_provider_error(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    _install_fake_cache(monkeypatch, warm=False)
    monkeypatch.setattr(plugin.time, "sleep", lambda seconds: None)

    def err_extract(urls):
        return [{"url": urls[0], "title": "", "content": "", "error": "down"}]

    _install_fake_keyless(monkeypatch, extract=err_extract)
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: "exa")
    monkeypatch.setattr(plugin, "_is_safe_public_url", lambda url: True)
    plugin._prefetch_worker(["https://a/1"])
    assert "ERR:down" in log.read_text(encoding="utf-8")


def test_worker_fail_open_on_provider_exception(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    _install_fake_cache(monkeypatch, warm=False)
    monkeypatch.setattr(plugin.time, "sleep", lambda seconds: None)
    def raising(urls):
        raise RuntimeError("net")

    _install_fake_keyless(monkeypatch, extract=raising)
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: "exa")
    monkeypatch.setattr(plugin, "_is_safe_public_url", lambda url: True)
    plugin._prefetch_worker(["https://a/1"])  # must not raise
    assert "ERR:" in log.read_text(encoding="utf-8")


def test_worker_fail_open_when_resolver_raises(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)

    def boom():
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(plugin, "_next_extract_vendor", boom)
    plugin._prefetch_worker(["https://a/1"])  # must not raise
    assert "ERR:" in log.read_text(encoding="utf-8")



# ─── Pacing ────────────────────────────────────────────────────────────────────

def test_pacing_sleeps_at_least_1_5s_per_fetch(plugin, monkeypatch, tmp_path):
    _log_lines(monkeypatch, tmp_path)
    _install_fake_cache(monkeypatch, warm=False)
    sleeps = []
    monkeypatch.setattr(plugin.time, "sleep", lambda seconds: sleeps.append(seconds))
    _, calls = _install_fake_keyless(monkeypatch)
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: "exa")
    monkeypatch.setattr(plugin, "_is_safe_public_url", lambda url: True)

    plugin._prefetch_worker(["https://a/1", "https://b/2"])

    assert len(sleeps) == 2  # one pace per vendor fetch
    assert all(seconds >= 1.5 for seconds in sleeps)
    assert calls == [["https://a/1"], ["https://b/2"]]


def test_no_pacing_when_all_urls_skipped(plugin, monkeypatch, tmp_path):
    _log_lines(monkeypatch, tmp_path)
    sleeps = []
    monkeypatch.setattr(plugin.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(plugin, "_next_extract_vendor", lambda: "exa")
    monkeypatch.setattr(plugin, "_is_safe_public_url", lambda url: True)
    monkeypatch.setattr(plugin, "_is_cache_warm", lambda url, name: True)
    plugin._prefetch_worker(["https://a/1"])
    assert sleeps == []


# ─── Next-extract-vendor selection (ring peek + config/selection gates) ────────

def test_peek_ring_vendor_follows_cursor(plugin, monkeypatch):
    _install_fake_keyless(monkeypatch, cursor=0)
    assert plugin._peek_ring_vendor() == "exa"
    _install_fake_keyless(monkeypatch, cursor=1)
    assert plugin._peek_ring_vendor() == "parallel"


def test_peek_ring_vendor_skips_paid(plugin, monkeypatch):
    _install_fake_keyless(monkeypatch, cursor=2, tiers={"firecrawl": "paid"})
    assert plugin._peek_ring_vendor() == "keenable"  # firecrawl pinned paid is skipped


def test_peek_ring_vendor_all_paid_none(plugin, monkeypatch):
    _install_fake_keyless(monkeypatch, tiers={n: "paid" for n in ("exa", "parallel", "firecrawl", "keenable")})
    assert plugin._peek_ring_vendor() is None


def test_next_extract_vendor_unpinned_peeks_ring(plugin, monkeypatch):
    # No config layer importable, no selection helper → falls through to the ring peek.
    _install_fake_keyless(monkeypatch, cursor=0, tiers={"firecrawl": "paid"})
    assert plugin._next_extract_vendor() == "exa"


def test_next_extract_vendor_configured_keyless(plugin, monkeypatch):
    _install_fake_config(monkeypatch, {"extract_backend": "exa"})
    _install_fake_keyless(monkeypatch)
    assert plugin._next_extract_vendor() == "exa"


def test_next_extract_vendor_configured_non_ring_skips(plugin, monkeypatch):
    _install_fake_config(monkeypatch, {"extract_backend": "tavily"})
    _install_fake_keyless(monkeypatch)
    assert plugin._next_extract_vendor() is None


def test_next_extract_vendor_configured_paid_ring_skips(plugin, monkeypatch):
    _install_fake_config(monkeypatch, {"extract_backend": "firecrawl"})
    fake, _ = _install_fake_keyless(monkeypatch)
    fake.use_keyless = lambda name, key: name != "firecrawl"  # paid tier → keyed dispatch
    assert plugin._next_extract_vendor() is None


def test_next_extract_vendor_stored_selection_skips(plugin, monkeypatch):
    _install_fake_config(monkeypatch, {})
    _install_fake_keyless(monkeypatch, cursor=0)
    _install_fake_selection(monkeypatch, "nous")
    assert plugin._next_extract_vendor() is None


# ─── Safety helper + log format ────────────────────────────────────────────────

def test_conservative_host_ok(plugin):
    assert plugin._conservative_host_ok("example.com") is True
    assert plugin._conservative_host_ok("localhost") is False
    assert plugin._conservative_host_ok("127.0.0.1") is False
    assert plugin._conservative_host_ok("10.0.0.5") is False
    assert plugin._conservative_host_ok("intranet") is False


def test_is_safe_public_url_schemes(plugin):
    assert plugin._is_safe_public_url("ftp://example.com/x") is False
    assert plugin._is_safe_public_url("not a url") is False


def test_log_line_format(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    plugin._log("https://a/1", "exa", "STORED")
    plugin._log("https://b/2", "", "SKIP:disabled")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} \| https://a/1 \| exa \| STORED", lines[0])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} \| https://b/2 \|  \| SKIP:disabled", lines[1])


def test_log_collapses_newlines(plugin, monkeypatch, tmp_path):
    log = _log_lines(monkeypatch, tmp_path)
    plugin._log("https://a/1", "exa", "ERR:line1\nline2")
    assert log.read_text(encoding="utf-8").count("\n") == 1

