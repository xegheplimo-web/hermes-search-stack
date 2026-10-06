"""Simple per-key token-bucket rate limiting.

One bucket per API key (or per client host when auth is disabled); buckets
refill at ``rate_limit_rps`` tokens per second. Exceeding the bucket yields
an OpenAI-shaped 429. ``rate_limit_rps <= 0`` disables limiting.
"""

from __future__ import annotations

import hashlib
import threading
import time

from fastapi import HTTPException, Request

DEFAULT_RPS = 10.0


class RateLimiter:
    """Thread-safe token bucket keyed by an arbitrary string."""

    def __init__(self, rps: float = DEFAULT_RPS, burst: float | None = None) -> None:
        self.rps = float(rps)
        self.burst = float(burst if burst is not None else max(self.rps, 1.0))
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Whether limiting is active (non-positive rps disables it)."""
        return self.rps > 0

    def allow(self, key: str) -> bool:
        """Consume one token for ``key``; return True when allowed."""
        if not self.enabled:
            return True
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.burst, updated=now)
                self._buckets[key] = bucket
            bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * self.rps)
            bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False


class _Bucket:
    """Token bucket state for one key."""

    __slots__ = ("tokens", "updated")

    def __init__(self, *, tokens: float, updated: float) -> None:
        self.tokens = tokens
        self.updated = updated


def _rate_limit_key(request: Request) -> str:
    """Derive a stable bucket key without logging raw credentials."""
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        digest = hashlib.sha256(header[len("Bearer ") :].encode()).hexdigest()[:16]
        return f"key:{digest}"
    host = request.client.host if request.client is not None else ""
    return f"host:{host}"


def _rate_limited() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "error": {
                "message": "Rate limit exceeded. Slow down and retry.",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            }
        },
        headers={"Retry-After": "1"},
    )


def rate_limit_guard(request: Request) -> None:
    """FastAPI dependency enforcing the per-key token bucket."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    if not limiter.allow(_rate_limit_key(request)):
        raise _rate_limited()
