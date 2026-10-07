"""Bounded admission control + request metrics for the gateway (R15-D §1).

``Admission`` is a thread-safe gate for the expensive answer endpoints: at
most ``max_inflight`` requests hold a slot at once and at most ``queue_cap``
additional requests wait for one. Anything beyond
``max_inflight + queue_cap`` is rejected so the HTTP layer can answer
``503`` + ``Retry-After: 1`` (same body shape as ``rate_limit``). Queued
waiters park on a condition variable with an optional timeout — the app
passes ``request_deadline_s`` so a queued request never outlives its own
deadline budget.

``GatewayMetrics`` keeps the process counters and a ring buffer of the last
``capacity`` request durations behind the ``GET /metrics`` snapshot. Both
classes are stdlib-only so the engine can read ``ADMISSION_WAIT_MS`` without
importing FastAPI.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections import deque
from contextvars import ContextVar

# Per-request admission wait in milliseconds, set by the app middleware
# before the endpoint runs and read by ``Engine.run_iter`` so the queue wait
# counts against the request deadline and lands in ``timings_ms``.
ADMISSION_WAIT_MS: ContextVar[float] = ContextVar("hermes_gateway_admission_wait_ms", default=0.0)

# 503 body — same OpenAI-error shape as security.rate_limit._rate_limited.
ADMISSION_REJECTED_BODY = {
    "error": {
        "message": "Server is at capacity — too many requests in flight. Retry shortly.",
        "type": "server_error",
        "code": "overloaded",
    }
}


class AdmissionRejected(Exception):
    """Raised by ``Admission.acquire`` when in-flight + wait queue are full."""


class AdmissionTimeout(AdmissionRejected):
    """Raised when a queued request spends its whole wait budget in line."""


class Admission:
    """Bounded in-flight semaphore + capped wait queue.

    ``max_inflight <= 0`` disables admission entirely (pass-through), matching
    ``RateLimiter``'s "non-positive disables" convention. ``queue_cap <= 0``
    means no waiting: every request beyond ``max_inflight`` is rejected.
    """

    def __init__(self, max_inflight: int = 4, queue_cap: int = 16) -> None:
        self.max_inflight = int(max_inflight)
        self.queue_cap = max(0, int(queue_cap))
        self._cond = threading.Condition()
        self._in_flight = 0
        self._queued = 0

    @property
    def enabled(self) -> bool:
        """Whether the gate restricts anything at all."""
        return self.max_inflight > 0

    @property
    def in_flight(self) -> int:
        with self._cond:
            return self._in_flight

    @property
    def queue_depth(self) -> int:
        with self._cond:
            return self._queued

    @property
    def capacity(self) -> int:
        """Total requests admitted at once: ``max_inflight + queue_cap``."""
        return self.max_inflight + self.queue_cap

    def acquire(self, timeout: float | None = None) -> float:
        """Take a slot, queueing if needed; returns the wait in milliseconds.

        ``timeout`` (seconds) bounds only the time spent queued — once a slot
        is granted it is held until :meth:`release`. Raises
        :class:`AdmissionRejected` when the queue is full and
        :class:`AdmissionTimeout` when the wait budget runs out first.
        """
        if not self.enabled:
            return 0.0
        with self._cond:
            # New arrivals yield to waiters already in line so a busy gateway
            # cannot starve the queue.
            if self._in_flight < self.max_inflight and self._queued == 0:
                self._in_flight += 1
                return 0.0
            if self._queued >= self.queue_cap:
                raise AdmissionRejected(f"admission full ({self._in_flight} in flight, {self._queued} queued)")
            self._queued += 1
            started = time.monotonic()
            try:
                deadline = None if timeout is None else started + float(timeout)
                while self._in_flight >= self.max_inflight:
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        raise AdmissionTimeout(f"no admission slot after {float(timeout):g}s in queue")
                    self._cond.wait(remaining)
                self._in_flight += 1
                return (time.monotonic() - started) * 1000.0
            finally:
                self._queued -= 1

    def release(self) -> None:
        """Return a slot and wake one waiter; tolerant of double-release."""
        with self._cond:
            if self._in_flight > 0:
                self._in_flight -= 1
                self._cond.notify()

    @contextlib.contextmanager
    def hold(self, timeout: float | None = None):
        """Context manager form of acquire/release; yields the wait in ms."""
        wait_ms = self.acquire(timeout)
        try:
            yield wait_ms
        finally:
            self.release()


class GatewayMetrics:
    """Process counters + last-N durations for ``GET /metrics`` (stdlib only)."""

    def __init__(self, capacity: int = 200) -> None:
        self.capacity = max(1, int(capacity))
        self._durations: deque[float] = deque(maxlen=self.capacity)
        self._lock = threading.Lock()
        self.requests_total = 0
        self.rejected_total = 0
        self.timeouts_total = 0

    def record_request(self) -> None:
        with self._lock:
            self.requests_total += 1

    def record_rejection(self) -> None:
        with self._lock:
            self.rejected_total += 1

    def record_timeout(self) -> None:
        with self._lock:
            self.timeouts_total += 1

    def record_duration(self, duration_ms: float) -> None:
        with self._lock:
            self._durations.append(float(duration_ms))

    def snapshot(self, *, in_flight: int = 0, queue_depth: int = 0) -> dict:
        """Frozen §1.5 shape; percentiles are nearest-rank (deterministic)."""
        with self._lock:
            durations = list(self._durations)
            return {
                "requests_total": self.requests_total,
                "rejected_total": self.rejected_total,
                "timeouts_total": self.timeouts_total,
                "in_flight": int(in_flight),
                "queue_depth": int(queue_depth),
                "durations_ms": {
                    "last": durations[-1] if durations else None,
                    "p50": percentile(durations, 50.0),
                    "p95": percentile(durations, 95.0),
                },
            }


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of *values* (``None`` when empty)."""
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    rank = min(max(math.ceil(pct / 100.0 * len(ordered)), 1), len(ordered))
    return ordered[rank - 1]
