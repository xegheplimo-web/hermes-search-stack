"""POST /v1/chat/completions — OpenAI-compatible chat completions.

v1 semantics: the LAST user message is the query; other messages are
ignored (documented). Non-stream requests call ``engine.run``; streaming
requests map ``engine.run_iter`` events to SSE chunks.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from gateway.openai.streaming import DONE, MODEL_ID, chat_chunk, new_chunk_id, split_cache_text, sse

router = APIRouter()


class ChatMessage(BaseModel):
    """One chat message; only ``role`` and string ``content`` are consumed."""

    role: str = "user"
    content: str = ""


class StreamOptions(BaseModel):
    """Streaming options per the OpenAI schema."""

    include_usage: bool | None = None


class ChatCompletionRequest(BaseModel):
    """Request body per the OpenAI chat completions schema (subset)."""

    model: str = MODEL_ID
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    stream_options: StreamOptions | None = None


def _error(status_code: int, message: str, code: str, type_: str = "invalid_request_error") -> HTTPException:
    """Build an HTTPException carrying the OpenAI error shape."""
    return HTTPException(
        status_code=status_code,
        detail={"error": {"message": message, "type": type_, "code": code}},
    )


def _last_user_query(messages: list[ChatMessage]) -> str | None:
    """Return the content of the last user message, or None if absent."""
    for message in reversed(messages):
        if message.role == "user":
            return message.content or None
    return None


def _estimate_usage(prompt_text: str, completion_text: str) -> dict:
    """Best-effort token usage: ~4 characters per token (no tokenizer here)."""
    prompt_tokens = max(1, math.ceil(len(prompt_text) / 4))
    completion_tokens = max(1, math.ceil(len(completion_text) / 4))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _completion_response(query: str, result) -> dict:
    """Build the non-stream ``chat.completion`` object."""
    return {
        "id": new_chunk_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.answer_markdown},
                "finish_reason": "stop",
            }
        ],
        "usage": _estimate_usage(query, result.answer_markdown),
    }


def _stream_chat(engine, query: str, *, include_usage: bool) -> Iterator[str]:
    """Map ``engine.run_iter`` events to SSE frames.

    Structure per the frozen contract: a role chunk first, then content
    deltas, then a ``finish_reason: "stop"`` chunk, an optional usage
    chunk, and the ``[DONE]`` sentinel. Cache-hit answers are buffered and
    re-served in 300-500 character pieces.
    """
    chunk_id = new_chunk_id()
    yield sse(chat_chunk(chunk_id=chunk_id, role="assistant"))
    is_cache_hit = False
    cache_buffer: list[str] = []
    result = None
    try:
        for event in engine.run_iter(query):
            event_type = event.get("type")
            if event_type == "route":
                is_cache_hit = bool(event.get("cached", False))
            elif event_type == "delta":
                text = event.get("text") or ""
                if is_cache_hit:
                    cache_buffer.append(text)
                else:
                    yield sse(chat_chunk(chunk_id=chunk_id, content=text))
            elif event_type == "done":
                result = event.get("result")
        if is_cache_hit:
            cached_text = result.answer_markdown if result is not None else ""
            if not cached_text:
                cached_text = "".join(cache_buffer)
            for piece in split_cache_text(cached_text):
                yield sse(chat_chunk(chunk_id=chunk_id, content=piece))
        yield sse(chat_chunk(chunk_id=chunk_id, finish_reason="stop"))
        if include_usage and result is not None:
            yield sse(chat_chunk(chunk_id=chunk_id, usage=_estimate_usage(query, result.answer_markdown)))
        yield sse(DONE)
    except Exception as exc:
        yield sse(
            {"error": {"message": f"Backend unavailable: {exc}", "type": "server_error", "code": "backend_unavailable"}}
        )
        yield sse(DONE)


@router.post("/v1/chat/completions")
def create_chat_completion(request: Request, body: ChatCompletionRequest):
    """Handle an OpenAI-compatible chat completion request."""
    engine = request.app.state.engine
    if body.model != MODEL_ID:
        raise _error(404, f"Model '{body.model}' not found", "model_not_found")
    query = _last_user_query(body.messages)
    if query is None:
        raise _error(400, "No user message found in 'messages'", "missing_query")
    include_usage = bool(body.stream_options and body.stream_options.include_usage)
    if body.stream:
        return StreamingResponse(
            _stream_chat(engine, query, include_usage=include_usage),
            media_type="text/event-stream",
        )
    try:
        result = engine.run(query)
    except Exception as exc:  # noqa: BLE001 - map backend/synth failures to 503
        raise _error(503, f"Backend unavailable: {exc}", "backend_unavailable", "server_error") from exc
    return _completion_response(query, result)
