"""GET /metrics + GatewayMetrics math (R15-D §1.5).

Snapshot shape is frozen: ``requests_total``, ``rejected_total``,
``timeouts_total``, ``in_flight``, ``queue_depth`` and
``durations_ms{last,p50,p95}`` over a ring buffer of the last 200 request
durations (nearest-rank percentiles — deterministic on known inputs).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway.app import create_app  # noqa: E402
from gateway.backends.stub import StubBackend  # noqa: E402
from gateway.config import GatewayConfig  # noqa: E402
from gateway.core.cache import GatewayCache  # noqa: E402
from gateway.core.engine import Engine  # noqa: E402
from gateway.security.admission import GatewayMetrics, percentile  # noqa: E402
from tests.gateway.conftest import FakeSynthesizer  # noqa: E402


def _chat_body() -> dict:
    return {"model": "hermes-search", "messages": [{"role": "user", "content": "q"}], "stream": False}


def _app(tmp_path, backend: StubBackend | None = None, **config_overrides):
    cfg = GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
        **config_overrides,
    )
    engine = Engine(
        cfg,
        backend=backend or StubBackend(),
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )
    return create_app(config=cfg, engine=engine)


# ---------------------------------------------------------------------------
# GatewayMetrics unit math
# ---------------------------------------------------------------------------


def test_percentile_exact_on_known_durations():
    m = GatewayMetrics()
    for value in range(10, 101, 10):  # 10..100
        m.record_duration(value)
    durations = m.snapshot()["durations_ms"]
    assert durations["last"] == 100.0
    assert durations["p50"] == 50.0  # nearest-rank: ceil(0.50*10) -> 5th = 50
    assert durations["p95"] == 100.0  # ceil(0.95*10) -> 10th = 100


def test_percentile_ring_buffer_keeps_last_n():
    m = GatewayMetrics(capacity=200)
    for value in range(1, 251):  # 250 in, 200 kept -> 51..250
        m.record_duration(value)
    durations = m.snapshot()["durations_ms"]
    assert durations["last"] == 250.0
    assert durations["p50"] == 150.0  # rank ceil(0.50*200)=100 -> 51+99
    assert durations["p95"] == 240.0  # rank ceil(0.95*200)=190 -> 51+189


def test_snapshot_empty_and_counts():
    m = GatewayMetrics()
    snap = m.snapshot(in_flight=3, queue_depth=2)
    assert snap == {
        "requests_total": 0,
        "rejected_total": 0,
        "timeouts_total": 0,
        "in_flight": 3,
        "queue_depth": 2,
        "durations_ms": {"last": None, "p50": None, "p95": None},
    }
    m.record_request()
    m.record_rejection()
    m.record_timeout()
    snap = m.snapshot()
    assert snap["requests_total"] == 1
    assert snap["rejected_total"] == 1
    assert snap["timeouts_total"] == 1
    assert percentile([], 95.0) is None
    assert percentile([7.0], 50.0) == 7.0


# ---------------------------------------------------------------------------
# HTTP wiring
# ---------------------------------------------------------------------------


def test_metrics_endpoint_counts_answer_requests(tmp_path):
    app = _app(tmp_path)
    # No ``with``: per-request portals, no lifespan (MCP manager is single-run).
    client = TestClient(app)
    first = client.get("/metrics").json()
    assert first["requests_total"] == 0
    assert first["durations_ms"] == {"last": None, "p50": None, "p95": None}
    assert client.post("/v1/chat/completions", json=_chat_body()).status_code == 200
    assert client.post("/v1/chat/completions", json=_chat_body()).status_code == 200
    # Probes and utility endpoints are not part of the answer-path counts.
    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/models").status_code == 200
    snap = client.get("/metrics").json()
    assert snap["requests_total"] == 2
    assert snap["rejected_total"] == 0
    assert snap["in_flight"] == 0
    assert snap["queue_depth"] == 0
    durations = snap["durations_ms"]
    assert durations["last"] > 0
    assert durations["p50"] > 0 and durations["p95"] >= durations["p50"]


def test_metrics_endpoint_404_when_disabled(tmp_path):
    app = _app(tmp_path, metrics_enabled=False)
    client = TestClient(app)
    assert client.get("/metrics").status_code == 404
    assert client.post("/v1/chat/completions", json=_chat_body()).status_code == 200


def test_metrics_timeout_counter_from_engine(tmp_path):
    """A deadline-exceeded engine run lands in timeouts_total."""

    class SlowBackend(StubBackend):
        def search(self, query, *, max_results=10):
            time.sleep(0.4)
            return super().search(query, max_results=max_results)

    app = _app(tmp_path, SlowBackend(), request_deadline_s=0.05)
    client = TestClient(app)
    assert client.post("/v1/chat/completions", json=_chat_body()).status_code == 200
    snap = client.get("/metrics").json()
    assert snap["timeouts_total"] == 1
    assert snap["requests_total"] == 1


def test_concurrent_requests_counters_consistent(tmp_path):
    """ThreadPoolExecutor(8) vs max_inflight=2: no deadlock, cap honored."""

    class SlowishBackend(StubBackend):
        def search(self, query, *, max_results=10):
            time.sleep(0.15)
            return super().search(query, max_results=max_results)

    app = _app(tmp_path, SlowishBackend(), admission_max_inflight=2)
    admission = app.state.admission
    client = TestClient(app)
    stop = threading.Event()
    max_in_flight = 0

    def monitor() -> None:
        nonlocal max_in_flight
        while not stop.is_set():
            max_in_flight = max(max_in_flight, admission.in_flight)
            time.sleep(0.005)

    def call() -> int:
        return client.post("/v1/chat/completions", json=_chat_body()).status_code

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = [f.result(timeout=60) for f in (pool.submit(call) for _ in range(8))]
    stop.set()
    watcher.join(5)

    assert statuses == [200] * 8
    assert 0 < max_in_flight <= 2  # the in-flight cap held under real concurrency
    snap = app.state.metrics.snapshot(in_flight=admission.in_flight, queue_depth=admission.queue_depth)
    assert snap["in_flight"] == 0 and snap["queue_depth"] == 0
    assert snap["requests_total"] == 8
    assert snap["rejected_total"] == 0
    assert snap["durations_ms"]["last"] is not None
