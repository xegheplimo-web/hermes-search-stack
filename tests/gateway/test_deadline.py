"""Cooperative request deadline tests (R15-D §1.3/§1.4).

A backend slower than ``request_deadline_s`` must yield a partial result with
the warning ``request deadline exceeded (Ns) — partial result`` — never an
exception. Optional work (deep sub-searches, extraction, fact_check publish)
is skipped once the budget is spent; ``timings_ms`` carries the additive
``admission_wait_ms`` + ``deadline_exceeded`` keys on every path.
"""

from __future__ import annotations

import time

from gateway.backends.stub import StubBackend
from gateway.config import GatewayConfig
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine
from gateway.security.admission import ADMISSION_WAIT_MS
from tests.gateway.conftest import FakeSynthesizer


class SlowSearchBackend(StubBackend):
    """Stub that sleeps inside ``search`` — slower than the test deadline."""

    name = "slow-stub"

    def __init__(self, sleep_s: float):
        super().__init__()
        self.sleep_s = sleep_s

    def search(self, query: str, *, max_results: int = 10):
        time.sleep(self.sleep_s)
        return super().search(query, max_results=max_results)


def _engine(tmp_path, *, sleep_s: float, deadline_s: float) -> tuple[GatewayConfig, Engine]:
    cfg = GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
        request_deadline_s=deadline_s,
    )
    engine = Engine(
        cfg,
        backend=SlowSearchBackend(sleep_s),
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )
    return cfg, engine


def test_deadline_partial_result_no_exception(tmp_path):
    """Probe slower than the deadline -> snippet-only partial + warning."""
    _cfg, engine = _engine(tmp_path, sleep_s=0.4, deadline_s=0.05)
    result = engine.run("weather in Hanoi today")
    assert any("request deadline exceeded" in w and "partial result" in w for w in result.warnings), result.warnings
    assert result.timings_ms["deadline_exceeded"] == 1
    assert result.answer_markdown  # a partial answer is still served
    # Extraction is optional work once the budget is spent — never ran.
    assert engine._backend.extract_calls == []


def test_deadline_skips_deep_subsearches_and_publish(tmp_path):
    """Deep mode over budget: no extra searches, no fact_check, no cache put."""
    cfg, engine = _engine(tmp_path, sleep_s=0.4, deadline_s=0.05)
    result = engine.run("Compare Alpha vs Beta in detail", depth="deep")
    assert result.depth == "deep"
    assert result.timings_ms["deadline_exceeded"] == 1
    # Only the probe search ran; the multi-part sub-searches were skipped.
    assert len(engine._backend.search_calls) == 1
    assert engine._backend.extract_calls == []
    assert "verify_ms" not in result.timings_ms  # publish stage skipped
    assert GatewayCache(cfg.cache_db_path).get("Compare Alpha vs Beta in detail") is None


def test_deadline_disabled_when_not_positive(tmp_path):
    """request_deadline_s <= 0 disables the watchdog entirely."""
    _cfg, engine = _engine(tmp_path, sleep_s=0.05, deadline_s=0.0)
    result = engine.run("weather in Hanoi today")
    assert result.timings_ms["deadline_exceeded"] == 0
    assert not any("deadline" in w for w in result.warnings)
    assert engine._backend.extract_calls  # normal extraction ran


def test_timings_complete_on_normal_run(stub_engine):
    """Every phase key lands in timings_ms on a normal deep run."""
    result = stub_engine.run("Compare Alpha vs Beta in detail")
    for key in (
        "cache_ms",
        "probe_ms",
        "extract_ms",
        "synth_ms",
        "verify_ms",
        "total_ms",
        "admission_wait_ms",
        "deadline_exceeded",
    ):
        assert key in result.timings_ms, f"missing timing key {key}"
        assert isinstance(result.timings_ms[key], int)
    assert result.timings_ms["deadline_exceeded"] == 0
    assert result.timings_ms["admission_wait_ms"] == 0


def test_admission_wait_ms_flows_into_timings(stub_engine):
    """The middleware's queue wait lands in timings via the contextvar."""
    token = ADMISSION_WAIT_MS.set(250.0)
    try:
        result = stub_engine.run("weather in Hanoi today")
    finally:
        ADMISSION_WAIT_MS.reset(token)
    assert result.timings_ms["admission_wait_ms"] == 250


def test_deadline_hit_records_timeout_metric(tmp_path):
    """The engine's metrics sink counts deadline hits as timeouts."""
    from gateway.security.admission import GatewayMetrics

    cfg, engine = _engine(tmp_path, sleep_s=0.4, deadline_s=0.05)
    metrics = GatewayMetrics()
    engine.metrics = metrics
    engine.run("weather in Hanoi today")
    assert metrics.snapshot()["timeouts_total"] == 1
