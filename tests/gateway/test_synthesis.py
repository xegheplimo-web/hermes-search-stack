"""Synthesizer header behavior (R8 §5 integration).

The OpenCode Zen/Go relay rejects requests without ``x-opencode-session``
(Hermes #105841), so the gateway adds one automatically for opencode.ai
targets; explicit extra headers win, and non-OpenCode targets stay clean.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from gateway.core.synthesis import Synthesizer


def test_opencode_target_gets_session_header():
    synth = Synthesizer(base_url="https://opencode.ai/zen/go/v1", model="deepseek-flash", api_key="k")
    try:
        headers = synth._client.headers
        assert headers.get("x-opencode-session")
        assert headers.get("authorization") == "Bearer k"
    finally:
        synth.close()


def test_non_opencode_target_no_session_header():
    synth = Synthesizer(base_url="https://api.example.com/v1", model="m")
    try:
        assert "x-opencode-session" not in synth._client.headers
    finally:
        synth.close()


def test_extra_headers_merge_and_override():
    synth = Synthesizer(
        base_url="https://opencode.ai/zen/go/v1",
        model="m",
        extra_headers={"x-opencode-session": "pinned-session", "X-Custom": "1"},
    )
    try:
        assert synth._client.headers.get("x-opencode-session") == "pinned-session"
        assert synth._client.headers.get("x-custom") == "1"
    finally:
        synth.close()


def test_env_headers_parsing():
    from gateway.config import _env_headers

    assert _env_headers("HERMES_TEST_NOT_SET_12345") == {}
