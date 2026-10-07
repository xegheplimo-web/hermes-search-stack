"""Admission control tests (R15-D §1.1): cap math, 503 + Retry-After, counters.

Hermetic: a ``GatedBackend`` blocks ``search()`` on an event so in-flight
slots stay occupied deterministically — no sleeps on the assertion path.
NOTE: hermes_bridge.py serializes all backend ops on one worker lock, so
"in-flight" here means gateway-side admission slots, not parallel backend
calls — the bridge itself is a known limit owned by a later task.
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
from gateway.security.admission import Admission, AdmissionRejected, AdmissionTimeout  # noqa: E402
from tests.gateway.conftest import FakeSynthesizer  # noqa: E402


def _chat_body() -> dict:
    return {"model": "hermes-search", "messages": [{"role": "user", "content": "q"}], "stream": False}


def _wait_for(predicate, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# Admission unit semantics
# ---------------------------------------------------------------------------


def test_admission_cap_math_unit():
    """in-flight cap + queue cap: the (max_inflight + queue_cap + 1)th rejects."""
    adm = Admission(max_inflight=2, queue_cap=1)
    assert adm.acquire() == 0.0
    assert adm.acquire() == 0.0
    assert adm.in_flight == 2

    waited: list[float] = []
    waiter = threading.Thread(target=lambda: waited.append(adm.acquire(timeout=10)))
    waiter.start()
    assert _wait_for(lambda: adm.queue_depth == 1)
    # 2 running + 1 queued fills capacity; the next arrival is rejected.
    with pytest.raises(AdmissionRejected):
        adm.acquire()
    assert adm.in_flight == 2 and adm.queue_depth == 1

    adm.release()  # frees a slot -> the queued waiter takes it
    waiter.join(10)
    assert not waiter.is_alive()
    assert waited and waited[0] >= 0.0
    assert adm.in_flight == 2 and adm.queue_depth == 0
    adm.release()
    adm.release()
    assert adm.in_flight == 0


def test_admission_queue_wait_timeout():
    """A queued request that outlives its wait budget raises AdmissionTimeout."""
    adm = Admission(max_inflight=1, queue_cap=4)
    adm.acquire()
    with pytest.raises(AdmissionTimeout):
        adm.acquire(timeout=0.05)
    assert adm.queue_depth == 0  # timed-out waiter left the queue
    assert adm.in_flight == 1
    adm.release()


def test_admission_disabled_is_passthrough():
    """max_inflight <= 0 disables the gate (RateLimiter-style convention)."""
    adm = Admission(max_inflight=0, queue_cap=0)
    assert not adm.enabled
    assert adm.acquire() == 0.0
    adm.release()  # tolerant of releases without acquires
    assert adm.in_flight == 0


def test_admission_zero_queue_rejects_immediately():
    adm = Admission(max_inflight=1, queue_cap=0)
    adm.acquire()
    with pytest.raises(AdmissionRejected):
        adm.acquire()
    assert adm.queue_depth == 0
    adm.release()


# ---------------------------------------------------------------------------
# HTTP wiring
# ---------------------------------------------------------------------------


class GatedBackend(StubBackend):
    """Stub whose ``search()`` blocks until *gate* opens (deterministic hold)."""

    name = "gated-stub"

    def __init__(self, gate: threading.Event, **kwargs):
        super().__init__(**kwargs)
        self._gate = gate

    def search(self, query: str, *, max_results: int = 10):
        self._gate.wait(30)
        return super().search(query, max_results=max_results)


def _app_with(tmp_path, backend, **config_overrides):
    cfg = GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
        **config_overrides,
    )
    engine = Engine(
        cfg,
        backend=backend,
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )
    return create_app(config=cfg, engine=engine)


def test_admission_503_retry_after_math(tmp_path):
    """8 concurrent vs max_inflight=2 + queue_cap=2 -> 4 served, 4 rejected."""
    gate = threading.Event()
    app = _app_with(tmp_path, GatedBackend(gate), admission_max_inflight=2, admission_queue_cap=2)
    admission = app.state.admission
    # No ``with TestClient``: __enter__ runs the app lifespan, and the MCP
    # session manager only tolerates one run per instance. Without it each
    # request still gets its own portal — thread-safe across the pool.
    client = TestClient(app)

    def call():
        return client.post("/v1/chat/completions", json=_chat_body())

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(call) for _ in range(8)]
        filled = _wait_for(
            lambda: admission.in_flight == 2 and admission.queue_depth == 2 and sum(f.done() for f in futures) == 4
        )
        assert filled, "expected 2 running + 2 queued + 4 already rejected"
        gate.set()
        responses = [f.result(timeout=30) for f in futures]

    assert _wait_for(lambda: admission.in_flight == 0 and admission.queue_depth == 0)
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 200, 200, 200, 503, 503, 503, 503]
    for response in responses:
        if response.status_code == 503:
            assert response.headers["retry-after"] == "1"
            error = response.json()["error"]
            assert error["code"] == "overloaded"
            assert error["type"] == "server_error"

    metrics = app.state.metrics.snapshot(in_flight=admission.in_flight, queue_depth=admission.queue_depth)
    assert metrics["requests_total"] == 8
    assert metrics["rejected_total"] == 4
    assert metrics["timeouts_total"] == 0


def test_admission_probes_not_gated(tmp_path):
    """healthz/readyz/metrics bypass the gate even while every slot is held."""
    gate = threading.Event()
    app = _app_with(tmp_path, GatedBackend(gate), admission_max_inflight=1, admission_queue_cap=0)
    client = TestClient(app)

    def call() -> int:
        return client.post("/v1/chat/completions", json=_chat_body()).status_code

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(call)
    assert _wait_for(lambda: app.state.admission.in_flight == 1)
    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200
    rejected = client.post("/v1/chat/completions", json=_chat_body())
    assert rejected.status_code == 503
    assert rejected.headers["retry-after"] == "1"
    gate.set()
    assert future.result(timeout=30) == 200
    pool.shutdown()
