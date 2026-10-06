"""Tests for the gateway engine, depth router, and config (hermetic, tmp dbs)."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from gateway.backends.stub import StubBackend
from gateway.config import GatewayConfig
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine
from gateway.core.router import build_signals, decide, query_markers

# ---------------------------------------------------------------------------
# Router (depth_policy wrapper)
# ---------------------------------------------------------------------------


def test_router_fast_on_rich_results():
    signals = build_signals("plain factual question", search_result_counts=[12], errors=[])
    decision = decide(signals)
    assert decision["mode"] == "fast"
    assert decision["score"] < 0.45


def test_router_deep_on_thin_evidence_and_errors():
    signals = build_signals(
        "obscure question",
        search_result_counts=[2],
        extract_char_totals=[500],
        errors=["backend timeout"],
    )
    decision = decide(signals)
    assert decision["mode"] == "deep"
    assert decision["score"] >= 0.45
    assert decision["reasons"]


def test_router_signals_have_exact_keys():
    signals = build_signals("q")
    assert set(signals) == {"query", "search_result_counts", "extract_char_totals", "errors", "query_markers"}
    assert set(signals["query_markers"]) == {"comparative", "multi_part", "vn"}


@pytest.mark.parametrize(
    "query,marker",
    [
        ("Compare Alpha vs Beta in detail", "comparative"),
        ("what is A? and what is B?", "multi_part"),
        ("giá vàng SJC hôm nay", "vn"),
    ],
)
def test_router_marker_detection(query, marker):
    markers = query_markers(query)
    assert markers[marker] is True


def test_router_no_markers_on_plain_query():
    markers = query_markers("weather in Hanoi today")
    assert markers == {"comparative": False, "multi_part": False, "vn": False}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _clear_gateway_env(monkeypatch):
    for key in [k for k in os.environ if k.startswith("HERMES_GATEWAY_")]:
        monkeypatch.delenv(key, raising=False)


def test_config_from_env_defaults(monkeypatch):
    _clear_gateway_env(monkeypatch)
    cfg = GatewayConfig.from_env()
    assert cfg.backend == "auto"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8787
    assert cfg.synth_base_url == "https://opencode.ai/zen/go/v1"
    assert cfg.synth_model == "deepseek-flash"
    assert cfg.synth_timeout == 120.0
    assert cfg.cache_db == "data/answers.db"
    assert cfg.store_db == "data/searchstore.db"
    assert cfg.fast_max_results == 10
    assert cfg.fast_extract == 4
    assert cfg.deep_search_queries == 3
    assert cfg.deep_extract == 8
    assert cfg.api_key is None


def test_config_from_env_overrides(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("HERMES_GATEWAY_BACKEND", "stub")
    monkeypatch.setenv("HERMES_GATEWAY_PORT", "9999")
    monkeypatch.setenv("HERMES_GATEWAY_SYNTH_MODEL", "test-model")
    monkeypatch.setenv("HERMES_GATEWAY_FAST_EXTRACT", "2")
    monkeypatch.setenv("HERMES_GATEWAY_RATE_LIMIT_RPS", "7.5")
    monkeypatch.setenv("HERMES_GATEWAY_API_KEY", "secret-test")
    cfg = GatewayConfig.from_env()
    assert cfg.backend == "stub"
    assert cfg.port == 9999
    assert cfg.synth_model == "test-model"
    assert cfg.fast_extract == 2
    assert cfg.rate_limit_rps == 7.5
    assert cfg.api_key == "secret-test"


def test_config_paths_resolve_against_repo_root(tmp_path):
    cfg = GatewayConfig(repo_root=str(tmp_path))
    assert cfg.cache_db_path == tmp_path / "data" / "answers.db"
    assert cfg.store_db_path == tmp_path / "data" / "searchstore.db"
    assert cfg.tmp_dir == tmp_path / "data" / "tmp"


def test_synth_key_resolution(tmp_path, monkeypatch):
    """D7: explicit key wins, else OPENCODE_GO_API_KEY from the Hermes .env."""
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("OTHER=1\nOPENCODE_GO_API_KEY=dotenv-key-123\n", encoding="utf-8")
    cfg = GatewayConfig(hermes_home=str(tmp_path))
    assert cfg.resolve_synth_api_key() == "dotenv-key-123"
    cfg2 = GatewayConfig(hermes_home=str(tmp_path), synth_api_key="explicit-wins")
    assert cfg2.resolve_synth_api_key() == "explicit-wins"
    cfg3 = GatewayConfig(hermes_home=str(tmp_path / "nonexistent"))
    assert cfg3.resolve_synth_api_key() is None


# ---------------------------------------------------------------------------
# Engine — cache serve
# ---------------------------------------------------------------------------


def _verified_pack(query: str, answer: str = "cached verified answer") -> dict:
    return {
        "schema": "research_pack.v1",
        "query": query,
        "mode": "deep",
        "answer_markdown": answer,
        "sources": [{"url": "https://src.test/a", "title": "Source A", "trust_score": 0.8}],
        "verification": {"fact_check_exit": 0},
        "created_at": datetime.now(UTC).isoformat(),
        "ttl_days": 14,
    }


def test_engine_serves_fresh_cache_hit(stub_engine, cfg):
    cache = GatewayCache(cfg.cache_db_path)
    cache.put(_verified_pack("what is alpha"))
    backend = stub_engine._backend
    result = stub_engine.run("what is alpha")
    assert result.depth == "cache"
    assert result.cached is True
    assert result.answer_markdown == "cached verified answer"
    assert result.sources[0].url == "https://src.test/a"
    assert backend.search_calls == []  # no live calls on a cache serve
    assert backend.extract_calls == []


def test_engine_ignores_cache_when_disabled(stub_engine, cfg):
    GatewayCache(cfg.cache_db_path).put(_verified_pack("what is alpha"))
    result = stub_engine.run("what is alpha", allow_cache=False)
    assert result.cached is False
    assert result.depth in ("fast", "deep")


# ---------------------------------------------------------------------------
# Engine — fast flow
# ---------------------------------------------------------------------------


def test_engine_fast_flow(stub_engine):
    backend = stub_engine._backend
    result = stub_engine.run("weather in Hanoi today")
    assert result.depth == "fast"
    assert result.cached is False
    assert "## Sources" in result.answer_markdown
    assert len(backend.extract_calls) == 1
    urls, _char_limit = backend.extract_calls[0]
    assert 0 < len(urls) <= 4  # fast_extract cap
    assert result.sources
    assert result.timings_ms.get("total_ms", 0) >= 0


def test_run_iter_event_sequence(stub_engine):
    types = [e["type"] for e in stub_engine.run_iter("weather in Hanoi today")]
    assert types[0] == "route"
    assert types[-1] == "done"
    assert set(types) <= {"route", "delta", "done"}
    done = [e for e in stub_engine.run_iter("weather in Hanoi today") if e["type"] == "done"]
    assert done[0]["result"].depth == "fast"


# ---------------------------------------------------------------------------
# Engine — deep flow (publish gated on fact_check pass)
# ---------------------------------------------------------------------------


def test_engine_deep_flow_publishes_on_real_fact_check(stub_engine, cfg):
    """Real fact_check accepts the cited fake answer; pack must be published."""
    result = stub_engine.run("Compare Alpha vs Beta in detail")
    assert result.depth == "deep"
    assert result.sources
    hit = GatewayCache(cfg.cache_db_path).get("Compare Alpha vs Beta in detail")
    assert hit is not None, "verified deep answer must land in the answer cache"
    second = stub_engine.run("Compare Alpha vs Beta in detail")
    assert second.depth == "cache"
    assert second.cached is True


def test_engine_deep_publish_only_on_fact_check_pass(stub_engine, cfg, monkeypatch):
    """A passing fact_check stub publishes; a failing one does not."""

    def fake_run_check(report_summary, code):
        return lambda draft, ledger, **kw: (
            {"schema": "fact_check.v1", "summary": report_summary, "stats": {"coverage": 1.0}},
            code,
            [],
        )

    monkeypatch.setitem(sys.modules, "fact_check", SimpleNamespace(run_check=fake_run_check({"pass": False}, 1)))
    failed = stub_engine.run("Compare Alpha vs Beta in detail")
    assert failed.depth == "deep"
    assert GatewayCache(cfg.cache_db_path).get("Compare Alpha vs Beta in detail") is None
    assert any("fact_check" in w for w in failed.warnings)

    monkeypatch.setitem(sys.modules, "fact_check", SimpleNamespace(run_check=fake_run_check({"pass": True}, 0)))
    passed = stub_engine.run("Compare Gamma vs Delta in detail", allow_cache=False)
    assert passed.depth == "deep"
    assert GatewayCache(cfg.cache_db_path).get("Compare Gamma vs Delta in detail") is not None


def test_engine_backend_failure_never_crashes(cfg):
    engine = Engine(
        cfg,
        backend=StubBackend(fail_search=True, fail_extract=True),
        synth=SimpleNamespace(stream=lambda *a, **k: iter(()), last_warning="synth down"),
        cache=GatewayCache(cfg.cache_db_path),
    )
    result = engine.run("anything at all")
    assert result.warnings  # failures are warnings, not exceptions
    assert result.depth in ("fast", "deep")


# ---------------------------------------------------------------------------
# r9 §B — public engine.backend accessor (MCP tools resolve through it)
# ---------------------------------------------------------------------------


def test_engine_backend_property_returns_injected(stub_engine):
    """The public ``backend`` property returns the injected backend."""
    assert stub_engine.backend is stub_engine._backend


def test_engine_backend_property_lazy_builds(cfg):
    """No injected backend -> first access resolves ``config.backend``."""
    engine = Engine(cfg, synth=SimpleNamespace(), cache=None)
    backend = engine.backend
    assert isinstance(backend, StubBackend)
    assert engine.backend is backend  # resolved once, then cached


def test_engine_backend_property_is_read_only(stub_engine):
    """The accessor has no setter — construction-time injection only."""
    with pytest.raises(AttributeError):
        stub_engine.backend = StubBackend()


# ---------------------------------------------------------------------------
# r9 §A — status() cache block is a REAL probe, not a presence check
# ---------------------------------------------------------------------------


def test_engine_status_cache_probe_ok(stub_engine):
    status = stub_engine.status()
    assert status["cache"] == {"ok": True}


def test_engine_status_cache_probe_broken_reports_detail(stub_engine):
    """A constructed-but-dead cache must report ok:false + detail."""
    stub_engine._cache.close()  # kill the underlying connection
    status = stub_engine.status()
    assert status["cache"]["ok"] is False
    assert status["cache"]["detail"]


def test_engine_status_cache_unavailable_reports_detail(tmp_path):
    """Cache construction failure -> ok:false + the real init error."""
    bad_cfg = GatewayConfig(backend="stub", cache_db=str(tmp_path), repo_root=str(tmp_path))
    engine = Engine(bad_cfg, backend=StubBackend(), synth=SimpleNamespace())
    status = engine.status()
    assert status["cache"]["ok"] is False
    assert status["cache"]["detail"]  # e.g. "OperationalError: unable to open database file"


# ---------------------------------------------------------------------------
# r9 §C — engine builds signals without unmeasured extract chars
# ---------------------------------------------------------------------------


def test_engine_signals_have_unmeasured_extract_chars(stub_engine, monkeypatch):
    """The depth decision runs before extraction -> extract_char_totals=[]."""
    captured = []
    import gateway.core.engine as engine_mod

    real_decide = engine_mod.decide

    def spy(signals):
        captured.append(dict(signals))
        return real_decide(signals)

    monkeypatch.setattr(engine_mod, "decide", spy)
    result = stub_engine.run("weather in Hanoi today")
    assert captured, "auto depth must consult the policy"
    signals = captured[0]
    assert signals["extract_char_totals"] == []  # nothing measured yet
    assert signals["search_result_counts"] == [len(stub_engine._backend.items[:10])]
    # no phantom TINY reason leaks into the routed explanation
    assert "tiny extract" not in result.reason
    assert result.depth == "fast"
