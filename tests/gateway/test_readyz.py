"""GET /readyz tests — the ``cache`` check must be a REAL probe (r9 §A).

``cache.ok`` is true only when the cache DB actually answers; a constructed-
but-broken cache reports ``ok: false`` + ``detail`` and flips ``ready``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway.app import create_app  # noqa: E402
from gateway.backends.stub import StubBackend  # noqa: E402
from gateway.config import GatewayConfig  # noqa: E402
from gateway.core.engine import Engine  # noqa: E402


def _client(engine) -> TestClient:
    return TestClient(create_app(config=engine.config, engine=engine))


def test_readyz_healthy_cache_reports_ok(stub_engine):
    with _client(stub_engine) as client:
        body = client.get("/readyz").json()
    assert body["checks"]["cache"] == {"ok": True}
    assert body["ready"] is True


def test_readyz_dead_cache_reports_not_ok(stub_engine):
    """Constructed-but-closed cache -> ok:false + detail + not ready."""
    stub_engine._cache.close()
    with _client(stub_engine) as client:
        body = client.get("/readyz").json()
    cache = body["checks"]["cache"]
    assert cache["ok"] is False
    assert cache["detail"]
    assert body["ready"] is False


def test_readyz_unbuildable_cache_reports_detail(tmp_path):
    """Cache that cannot even open its DB -> ok:false + init error detail."""
    cfg = GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path),  # a directory: sqlite cannot open it
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
    )
    engine = Engine(cfg, backend=StubBackend(), synth=SimpleNamespace())
    with _client(engine) as client:
        body = client.get("/readyz").json()
    cache = body["checks"]["cache"]
    assert cache["ok"] is False
    assert cache["detail"]
    assert body["ready"] is False
