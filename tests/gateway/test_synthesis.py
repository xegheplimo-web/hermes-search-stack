"""Synthesizer header behavior (R8 §5 integration).

The OpenCode Zen/Go relay rejects requests without ``x-opencode-session``
(Hermes #105841), so the gateway adds one automatically for opencode.ai
targets; explicit extra headers win, and non-OpenCode targets stay clean.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

pytest.importorskip("httpx")

from gateway.core.synthesis import Synthesizer, _messages
from gateway.protocols import EvidenceItem


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


# ---------------------------------------------------------------------------
# R10-A — freshness/date correctness: deterministic current-date block
# (Asia/Ho_Chi_Minh) + relative-date/staleness rules, hermetic via injected
# ``now``. No network.
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone(timedelta(hours=7)))

_EVIDENCE = [EvidenceItem(id=1, title="T", url="https://e.test/1", content="body")]


def _system(deep: bool = False, now: datetime | None = FIXED_NOW) -> str:
    return _messages("thời tiết hà nội ngày mai", _EVIDENCE, deep, now=now)[0]["content"]


def test_current_date_block_fast():
    system = _system(deep=False)
    assert "Current date: 2026-10-07 (Wednesday), Asia/Ho_Chi_Minh, UTC+7." in system
    assert "Keep the answer short" in system


def test_current_date_block_deep():
    system = _system(deep=True)
    assert "Current date: 2026-10-07 (Wednesday), Asia/Ho_Chi_Minh, UTC+7." in system
    assert "DEEP research answer" in system


def test_date_rules_present():
    system = _system()
    for needle in (
        "time-relative",
        "resolved absolute date",
        "as-of date",
        "in days",
        "fabricate dates",
        "not time-sensitive",
    ):
        assert needle in system, needle


def test_answer_language_rule_unchanged():
    assert "Answer in the SAME LANGUAGE as the user's question" in _system()


def test_now_is_converted_to_vietnam_time():
    # 2026-10-06 22:00 UTC == 2026-10-07 05:00 at UTC+7
    system = _system(now=datetime(2026, 10, 6, 22, 0, tzinfo=UTC))
    assert "Current date: 2026-10-07 (Wednesday)" in system


def test_now_naive_treated_as_vietnam_wall_time():
    system = _system(now=datetime(2026, 10, 7, 9, 0))
    assert "Current date: 2026-10-07 (Wednesday)" in system


def test_now_default_is_real_clock():
    system = _system(now=None)
    match = re.search(r"Current date: (\d{4}-\d{2}-\d{2}) \((\w+)\)", system)
    assert match, system
    datetime.strptime(match.group(1), "%Y-%m-%d")
    assert match.group(2) in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def test_now_is_keyword_only_with_none_default():
    for name in ("synthesize", "stream"):
        param = inspect.signature(getattr(Synthesizer, name)).parameters["now"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"choices": [{"message": {"content": "ok [1]"}}]}


class _FakeStreamResponse:
    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"ok [1]"}}]}'
        yield "data: [DONE]"

    def read(self):
        return b""


class _CapturingClient:
    """Minimal ``httpx.Client`` stand-in that records request payloads."""

    def __init__(self):
        self.payloads: list[dict] = []

    def post(self, url, json=None, **_kw):
        self.payloads.append(json)
        return _FakeResponse()

    def stream(self, method, url, json=None, **_kw):
        self.payloads.append(json)
        return _FakeStreamResponse()

    def close(self):
        pass


def _synth_with(client) -> Synthesizer:
    synth = Synthesizer(base_url="https://api.example.com/v1", model="m")
    synth._client = client
    return synth


def test_synthesize_sends_injected_now_in_messages():
    client = _CapturingClient()
    text = _synth_with(client).synthesize("giá xăng hôm nay", _EVIDENCE, now=FIXED_NOW)
    assert text == "ok [1]"
    payload = client.payloads[0]
    assert payload["stream"] is False
    assert "Current date: 2026-10-07 (Wednesday), Asia/Ho_Chi_Minh, UTC+7." in payload["messages"][0]["content"]


def test_stream_sends_injected_now_in_messages():
    client = _CapturingClient()
    synth = _synth_with(client)
    chunks = list(synth.stream("giá xăng hôm nay", _EVIDENCE, now=FIXED_NOW))
    assert "".join(chunks) == "ok [1]"
    payload = client.payloads[0]
    assert payload["stream"] is True
    assert "Current date: 2026-10-07 (Wednesday), Asia/Ho_Chi_Minh, UTC+7." in payload["messages"][0]["content"]
