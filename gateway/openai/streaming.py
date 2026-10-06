"""SSE helpers for OpenAI-compatible chat completion streaming.

Pure functions only: chunk builders, the ``data:`` frame serializer, the
``[DONE]`` sentinel, and the cache-hit text splitter (300-500 char pieces).
"""

from __future__ import annotations

import json
import math
import time
import uuid

MODEL_ID = "hermes-search"
MODEL_OWNER = "hermes-search-stack"
DONE = "[DONE]"

# Cache-hit answers are streamed in pieces of 300-500 characters.
CACHE_PIECE_TARGET = 400
CACHE_PIECE_MIN = 300
CACHE_PIECE_MAX = 500


def new_chunk_id() -> str:
    """Return a unique ``chatcmpl-`` id for one completion."""
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def chat_chunk(
    *,
    chunk_id: str | None = None,
    role: str | None = None,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> dict:
    """Build one ``chat.completion.chunk`` object.

    Normally exactly one of ``role`` / ``content`` / ``finish_reason`` is
    set; ``usage`` is attached only to the optional trailing usage chunk.
    """
    delta: dict[str, str] = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    chunk: dict = {
        "id": chunk_id or new_chunk_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def sse(event: dict | str) -> str:
    """Serialize an event to a single SSE ``data:`` frame."""
    payload = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
    return f"data: {payload}\n\n"


def split_cache_text(
    text: str,
    *,
    target: int = CACHE_PIECE_TARGET,
    minimum: int = CACHE_PIECE_MIN,
    maximum: int = CACHE_PIECE_MAX,
) -> list[str]:
    """Split cached answer text into SSE pieces of 300-500 characters.

    Short texts are returned as a single piece; longer texts are split into
    evenly sized pieces that all fall inside ``[minimum, maximum]`` whenever
    the text length makes that mathematically possible.
    """
    if not text:
        return []
    if len(text) <= maximum:
        return [text]
    count = max(2, math.ceil(len(text) / target))
    max_count = len(text) // minimum
    if max_count >= 2:
        count = min(count, max_count)
    base, extra = divmod(len(text), count)
    pieces: list[str] = []
    start = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        pieces.append(text[start : start + size])
        start += size
    return pieces
