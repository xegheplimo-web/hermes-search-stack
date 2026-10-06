"""HTTP surface tests: auth (bearer / loopback) and rate limiting."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway.app import create_app  # noqa: E402


@pytest.fixture
def client(cfg, stub_engine):
    app = create_app(config=cfg, engine=stub_engine)
    with TestClient(app) as client:
        yield client


def _chat_body(query: str = "What is the capital of Vietnam?") -> dict:
    return {"model": "hermes-search", "messages": [{"role": "user", "content": query}]}


def _client_with(cfg, stub_engine):
    app = create_app(config=cfg, engine=stub_engine)
    return TestClient(app)


def test_auth_off_loopback_ok(client):
    # No API key configured: loopback (TestClient) requests are accepted.
    response = client.post("/v1/chat/completions", json=_chat_body())
    assert response.status_code == 200


def test_auth_on_missing_key_401(cfg, stub_engine):
    authed = _client_with(replace(cfg, api_key="test-secret-key"), stub_engine)
    response = authed.post("/v1/chat/completions", json=_chat_body())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_auth_on_wrong_key_401(cfg, stub_engine):
    authed = _client_with(replace(cfg, api_key="test-secret-key"), stub_engine)
    response = authed.post(
        "/v1/chat/completions",
        json=_chat_body(),
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_auth_on_correct_key_200(cfg, stub_engine):
    authed = _client_with(replace(cfg, api_key="test-secret-key"), stub_engine)
    response = authed.post(
        "/v1/chat/completions",
        json=_chat_body(),
        headers={"Authorization": "Bearer test-secret-key"},
    )
    assert response.status_code == 200


def test_rate_limit_429(cfg, stub_engine):
    limited = _client_with(replace(cfg, rate_limit_rps=0.5), stub_engine)
    first = limited.post("/v1/chat/completions", json=_chat_body())
    assert first.status_code == 200
    second = limited.post("/v1/chat/completions", json=_chat_body())
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limit_exceeded"
