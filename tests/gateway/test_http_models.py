"""HTTP surface tests: GET /v1/models and health probes."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway.app import create_app  # noqa: E402


@pytest.fixture
def client(cfg, stub_engine):
    app = create_app(config=cfg, engine=stub_engine)
    with TestClient(app) as client:
        yield client


def test_models_list(client):
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    data = body["data"]
    assert len(data) == 1
    model = data[0]
    assert model["id"] == "hermes-search"
    assert model["object"] == "model"
    assert model["owned_by"] == "hermes-search-stack"
    assert isinstance(model["created"], int)


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]
    assert isinstance(body["backend"], str) and body["backend"]
    assert body["uptime_s"] >= 0


def test_readyz(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["ready"], bool)
    assert set(body["checks"]) == {"backend", "cache", "synth_config"}
    for check in body["checks"].values():
        assert "ok" in check
