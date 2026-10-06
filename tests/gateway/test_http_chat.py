"""HTTP surface tests: POST /v1/chat/completions (non-stream + SSE)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway.app import create_app  # noqa: E402
from gateway.openai.streaming import split_cache_text  # noqa: E402


@pytest.fixture
def client(cfg, stub_engine):
    app = create_app(config=cfg, engine=stub_engine)
    with TestClient(app) as client:
        yield client


def _chat_body(query: str, **overrides) -> dict:
    body = {"model": "hermes-search", "messages": [{"role": "user", "content": query}], "stream": False}
    body.update(overrides)
    return body


def test_chat_non_stream_happy_path(client):
    response = client.post("/v1/chat/completions", json=_chat_body("What is the capital of Vietnam?"))
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "hermes-search"
    assert body["choices"][0]["message"]["role"] == "assistant"
    content = body["choices"][0]["message"]["content"]
    assert isinstance(content, str) and content
    # Grounded answer carries [n] citations and a Sources section.
    assert "[1]" in content or "## Sources" in content
    assert body["choices"][0]["finish_reason"] == "stop"
    usage = body["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_chat_unknown_model_404(client):
    response = client.post("/v1/chat/completions", json=_chat_body("hi", model="gpt-4o"))
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "model_not_found"
    assert "gpt-4o" in error["message"]


def test_chat_bad_json_400(client):
    response = client.post(
        "/v1/chat/completions",
        content="{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"


def test_chat_missing_user_message_400(client):
    response = client.post(
        "/v1/chat/completions",
        json={"model": "hermes-search", "messages": [{"role": "system", "content": "ignore me"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_query"


def _parse_sse(text: str) -> list:
    events = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: ")
        payload = frame[len("data: ") :]
        events.append(payload if payload == "[DONE]" else json.loads(payload))
    return events


def test_chat_stream_structure(client):
    response = client.post("/v1/chat/completions", json=_chat_body("Tell me about Hanoi.", stream=True))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert events[-1] == "[DONE]"
    chunks = events[:-1]
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    content_deltas = [c for c in chunks if c["choices"][0]["delta"].get("content")]
    assert content_deltas, "expected at least one content delta"
    assert any(c["choices"][0]["finish_reason"] == "stop" for c in chunks)


def test_chat_stream_include_usage(client):
    body = _chat_body("Tell me about Hanoi.", stream=True)
    body["stream_options"] = {"include_usage": True}
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    events = _parse_sse(response.text)
    usage_chunks = [c for c in events[:-1] if "usage" in c]
    assert usage_chunks, "expected a usage chunk when stream_options.include_usage is set"
    assert usage_chunks[0]["usage"]["total_tokens"] > 0


def test_split_cache_text_pieces():
    text = "x" * 2000
    pieces = split_cache_text(text)
    assert len(pieces) > 1
    assert all(300 <= len(p) <= 500 for p in pieces)
    assert "".join(pieces) == text
    assert split_cache_text("short") == ["short"]
    assert split_cache_text("") == []
