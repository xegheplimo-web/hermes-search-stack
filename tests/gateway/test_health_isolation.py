"""Health isolation tests (R15-D §1.2).

``/healthz`` is liveness-only: it must answer <1 s while the backend is
stalled and must never call into the engine/backend. ``/readyz`` may report
backend status but is bounded — a hung ``ping()`` flips it to not-ready
instead of hanging the probe.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway.app import create_app  # noqa: E402
from gateway.backends.stub import StubBackend  # noqa: E402
from gateway.config import GatewayConfig  # noqa: E402
from gateway.core.cache import GatewayCache  # noqa: E402
from gateway.core.engine import Engine  # noqa: E402
from tests.gateway.conftest import FakeSynthesizer  # noqa: E402


class StalledBackend(StubBackend):
    """Stub that sleeps inside ``search`` and ``ping`` — a wedged worker."""

    name = "stalled-stub"

    def __init__(self, sleep_s: float):
        super().__init__()
        self.sleep_s = sleep_s
        self.ping_calls = 0

    def search(self, query: str, *, max_results: int = 10):
        time.sleep(self.sleep_s)
        return super().search(query, max_results=max_results)

    def ping(self) -> dict:
        self.ping_calls += 1
        time.sleep(self.sleep_s)
        return {"ok": True, "detail": f"stalled stub ({self.sleep_s}s)"}


def _app(tmp_path, backend: StubBackend):
    cfg = GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
    )
    engine = Engine(
        cfg,
        backend=backend,
        synth=FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
    )
    return create_app(config=cfg, engine=engine), cfg


def test_healthz_never_touches_backend(tmp_path):
    """Liveness must not call into the engine/backend at all."""
    backend = StalledBackend(sleep_s=5.0)
    app, _cfg = _app(tmp_path, backend)
    # No ``with``: each request runs on its own portal, so no lifespan entry.
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert backend.ping_calls == 0  # no engine.status() on the liveness path


def test_healthz_subsecond_while_backend_busy(tmp_path):
    """<1 s liveness while an in-flight request sleeps inside the backend."""
    backend = StalledBackend(sleep_s=2.5)
    app, _cfg = _app(tmp_path, backend)
    client = TestClient(app)
    statuses: list[int] = []

    def load() -> None:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "hermes-search",
                "messages": [{"role": "user", "content": "q"}],
            },
        )
        statuses.append(response.status_code)

    worker = threading.Thread(target=load)
    worker.start()
    deadline = time.monotonic() + 5
    while not backend.search_calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert backend.search_calls, "load request never reached the backend"
    try:
        start = time.monotonic()
        response = client.get("/healthz")
        elapsed = time.monotonic() - start
        assert response.status_code == 200
        assert elapsed < 1.0, f"healthz took {elapsed:.2f}s with a stalled backend"
        body = response.json()
        assert body["status"] == "ok"
        assert isinstance(body["backend"], str) and body["backend"]
    finally:
        worker.join(15)
    assert statuses == [200]


def test_readyz_bounded_when_backend_stalled(tmp_path):
    """A 10 s ping must not hang readyz — bounded probe reports not-ready."""
    backend = StalledBackend(sleep_s=10.0)
    app, _cfg = _app(tmp_path, backend)
    client = TestClient(app)
    start = time.monotonic()
    response = client.get("/readyz")
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert elapsed < 5.0, f"readyz blocked {elapsed:.2f}s on a stalled backend"
    body = response.json()
    assert body["ready"] is False
    assert set(body["checks"]) == {"backend", "cache", "synth_config"}
