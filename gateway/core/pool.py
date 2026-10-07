"""gateway/core/pool.py — bounded parallel ``SearchBackend`` execution (R15-B2; §7.1).

``BackendPool`` fans backend ops out across ``size`` lazy workers. Each
worker is a daemon thread owning ONE backend instance, built by *factory*
on its first op claim — never at pool construction, and CACHED per slot:
reused across map calls for the pool's lifetime (build once, close once;
§7.1a — a fresh backend per map call would re-spawn a bridge worker
subprocess on every call). The pool exists because the primary backend
(``HermesBridge``) serializes every op on one worker lock: real
concurrency needs DISTINCT instances, which is why ``§7.5`` restricts it
to consuming ``create_backend`` via the engine's factory.

Semantics (frozen §7.1):

- ``map_search`` / ``map_extract`` return one entry per input, in INPUT
  order regardless of completion order. A failed op yields ``None`` + an
  error string; a deadline-skipped op yields ``None`` + ``"deadline"``.
- Per-op failures are isolated — the map raises only when the pool cannot
  run at all (closed, or every worker's factory call failed).
- In-flight ops are never cancelled (cooperative semantics, same as the
  R15-D watchdog); ``deadline_passed`` is checked before each op STARTS.
- Worker threads are daemons — they never block interpreter exit.
- The factory may legitimately return the same instance for every worker
  (e.g. a shared stub); ops then race on it — factories must provide
  thread-safe or distinct instances.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.protocols import ExtractItem, SearchBackend, SearchItem

_MAX_WORKERS = 4
_UNSET = object()


class BackendPool:
    """Bounded parallel map over ``SearchBackend`` ops (frozen §7.1)."""

    def __init__(self, factory: Callable[[], SearchBackend], *, size: int = 4, deadline_passed=None):
        self._factory = factory
        try:
            n = int(size)
        except (TypeError, ValueError):
            n = 1
        self._size = max(1, min(_MAX_WORKERS, n))
        self._deadline_passed = deadline_passed
        self._lock = threading.Lock()
        self._slot_backends: list = [None] * self._size
        self._closed = False

    # ---------- public API ----------

    def map_search(self, queries: list[str], *, max_results: int) -> list[tuple[str, list[SearchItem] | None, str]]:
        """Parallel searches; ``(query, items_or_None, error_or_\"\")`` per op."""
        ops = [("search", str(q)) for q in queries]
        out = self._map(ops, lambda backend, op: backend.search(op[1], max_results=max_results))
        return [(op[1], items, err) for op, (items, err) in zip(ops, out, strict=True)]

    def map_extract(self, batches: list[list[str]], *, char_limit: int) -> list[list[ExtractItem] | None]:
        """Parallel extracts; ``items_or_None`` per batch (same op semantics)."""
        ops = [("extract", [str(u) for u in batch]) for batch in batches]
        out = self._map(ops, lambda backend, op: backend.extract(op[1], char_limit=char_limit))
        return [items for items, _err in out]

    def close(self) -> None:
        """Close every backend the pool built (those exposing ``close()``)."""
        with self._lock:
            self._closed = True
            backends = [b for b in self._slot_backends if b is not None]
            self._slot_backends = [None] * self._size
        for backend in backends:
            close = getattr(backend, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 — teardown is best-effort
                    pass

    def __enter__(self) -> BackendPool:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- internals ----------

    def _deadline(self) -> bool:
        """True when the (optional) deadline hook says the budget is spent."""
        check = self._deadline_passed
        if not callable(check):
            return False
        try:
            return bool(check())
        except Exception:  # noqa: BLE001 — a broken hook disables itself
            return False

    def _map(self, ops: list, invoke) -> list:
        """Fan *ops* over lazy workers; ``[(result, error), ...]`` in input order."""
        if self._closed:
            raise RuntimeError("backend pool is closed")
        if not ops:
            return []
        work: queue.Queue = queue.Queue()
        for idx, op in enumerate(ops):
            work.put((idx, op))
        results: list = [_UNSET] * len(ops)
        fatal: list[BaseException] = []

        def worker(slot: int) -> None:
            backend = self._slot_backends[slot]  # cached from an earlier call?
            while True:
                try:
                    idx, op = work.get_nowait()
                except queue.Empty:
                    return
                if self._deadline():
                    results[idx] = (None, "deadline")
                    continue
                if backend is None:
                    try:
                        backend = self._factory()
                    except Exception as exc:  # noqa: BLE001 — a worker that
                        fatal.append(exc)  # cannot build a backend dies here;
                        results[idx] = (None, f"{type(exc).__name__}: {exc}")
                        return  # the map raises below when nothing can run
                    with self._lock:
                        self._slot_backends[slot] = backend
                try:
                    results[idx] = (invoke(backend, op), "")
                except Exception as exc:  # noqa: BLE001 — per-op isolation
                    results[idx] = (None, f"{type(exc).__name__}: {exc}")

        threads = [
            threading.Thread(target=worker, args=(slot,), name=f"backend-pool-{slot}", daemon=True)
            for slot in range(min(self._size, len(ops)))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        stranded = [r for r in results if r is _UNSET]
        if fatal and not any(b is not None for b in self._slot_backends):
            raise fatal[0]  # no worker could construct a backend — cannot run
        if stranded:
            if fatal:
                raise fatal[0]
            raise RuntimeError(f"backend pool could not run {len(stranded)} op(s)")
        return results
