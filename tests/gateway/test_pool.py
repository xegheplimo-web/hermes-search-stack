"""BackendPool tests (R15-B2; analysis/r15-interfaces.md §7.1).

Parallelism is PROVEN, not timed: ``BarrierBackend`` blocks inside each op
on a ``threading.Barrier`` — a serialized pool would trip the barrier
timeout, fail the op, and the assertion below fails explicitly (never a
hanging test). All fakes are offline.
"""

from __future__ import annotations

import threading
import time

import pytest

from gateway.core.pool import BackendPool
from gateway.protocols import ExtractItem, SearchItem

BARRIER_TIMEOUT_S = 10.0


class _Stats:
    """Shared in-flight counters for one fake-backend family."""

    def __init__(self):
        self.lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0
        self.calls: list[str] = []

    def enter(self, tag: str) -> None:
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.calls.append(tag)

    def leave(self) -> None:
        with self.lock:
            self.inflight -= 1


class BarrierBackend:
    """``SearchBackend`` whose ``search`` waits on a shared barrier.

    With ``parties = size`` a concurrent pool releases every op together;
    a serialized pool leaves the first op waiting until the barrier
    timeout raises ``BrokenBarrierError`` — the op returns an error and
    the test's success assertion fails (bounded, never hangs).
    """

    name = "barrier-stub"

    def __init__(
        self,
        gate: threading.Barrier | None,
        stats: _Stats,
        *,
        close_log: list | None = None,
        sleep_s: float = 0.0,
    ):
        self._gate = gate
        self._stats = stats
        self._close_log = close_log
        self._sleep = sleep_s
        self.closed = False

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        self._stats.enter(query)
        try:
            if self._sleep:
                time.sleep(self._sleep)
            if self._gate is not None:
                self._gate.wait(timeout=BARRIER_TIMEOUT_S)
            return [SearchItem(title=f"hit {query}", url=f"https://t.test/{query}")]
        finally:
            self._stats.leave()

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        return [ExtractItem(url=u, title=f"t {u}", content=f"content {u}") for u in urls]

    def ping(self) -> dict:
        return {"ok": True, "detail": "barrier stub"}

    def close(self) -> None:
        self.closed = True
        if self._close_log is not None:
            self._close_log.append(self)


def _family(parties: int):
    gate = threading.Barrier(parties)
    stats = _Stats()
    return gate, stats, lambda: BarrierBackend(gate, stats)


# ---------- parallelism proof ----------


def test_map_search_runs_size_ops_concurrently():
    """Barrier(3) releases only if all 3 ops are in flight at once."""
    gate, stats, factory = _family(3)
    pool = BackendPool(factory, size=3)
    results = pool.map_search(["q1", "q2", "q3"], max_results=5)
    assert stats.max_inflight == 3, "ops did not overlap — pool serialized"
    assert [q for q, _items, _e in results] == ["q1", "q2", "q3"]
    assert all(items and not err for _q, items, err in results)


def test_inflight_never_exceeds_size():
    """More ops than workers: in-flight peaks at ``size``, never above."""
    stats = _Stats()
    pool = BackendPool(lambda: BarrierBackend(None, stats, sleep_s=0.05), size=2)
    results = pool.map_search([f"q{i}" for i in range(6)], max_results=5)
    assert stats.max_inflight == 2
    assert all(items and not err for _q, items, err in results)


def test_size_clamped_to_four():
    """size > 4 clamps to the 1..4 bound — never more than 4 in flight."""
    stats = _Stats()
    pool = BackendPool(lambda: BarrierBackend(None, stats, sleep_s=0.05), size=99)
    results = pool.map_search([f"q{i}" for i in range(6)], max_results=5)
    assert stats.max_inflight == 4
    assert all(items and not err for _q, items, err in results)


# ---------- failure isolation + ordering ----------


class FlakyBackend(BarrierBackend):
    """Raises for one configured query; other ops must be unaffected."""

    def __init__(self, gate, stats, *, fail_on: str):
        super().__init__(gate, stats)
        self._fail_on = fail_on

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        if self._fail_on in query:
            raise RuntimeError(f"boom on {query}")
        return super().search(query, max_results=max_results)


def test_failure_isolation_per_op():
    """One op raising -> its entry is (None, error); the rest succeed."""
    stats = _Stats()
    pool = BackendPool(lambda: FlakyBackend(None, stats, fail_on="bad"), size=2)
    results = pool.map_search(["bad query", "good query"], max_results=5)
    assert results[0][1] is None and "boom" in results[0][2]
    assert results[1][1] is not None and results[1][2] == ""
    assert [q for q, _i, _e in results] == ["bad query", "good query"]


class SlowBackend(BarrierBackend):
    """Per-query staggered sleep — completion order differs from input."""

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        delay = 0.15 if "first" in query else 0.0
        time.sleep(delay)
        return super().search(query, max_results=max_results)


def test_output_order_matches_input_regardless_of_completion():
    """A slower first op still lands in slot 0 — output order == input order."""
    gate, stats, factory = _family(2)
    pool = BackendPool(lambda: SlowBackend(gate, stats), size=2)
    results = pool.map_search(["first", "second"], max_results=5)
    assert [q for q, _i, _e in results] == ["first", "second"]
    assert all(items and not err for _q, items, err in results)


# ---------- deadline ----------


def test_deadline_skips_unstarted_ops():
    """Ops claimed after the deadline latch get ``("…", None, "deadline")``."""
    flag = {"hit": False}
    flag_set = threading.Event()
    gate = threading.Barrier(2)
    stats = _Stats()

    class DeadlineMarker(BarrierBackend):
        def search(self, query: str, *, max_results: int = 10):
            self._stats.enter(query)
            try:
                self._gate.wait(timeout=BARRIER_TIMEOUT_S)  # q1+q2 in flight together
                if query == "q1":
                    flag["hit"] = True  # later claims see the spent budget
                    flag_set.set()
                else:
                    flag_set.wait(timeout=BARRIER_TIMEOUT_S)
                return [SearchItem(title=f"hit {query}", url=f"https://t.test/{query}")]
            finally:
                self._stats.leave()

    pool = BackendPool(lambda: DeadlineMarker(gate, stats), size=2, deadline_passed=lambda: flag["hit"])
    results = pool.map_search(["q1", "q2", "q3", "q4"], max_results=5)
    # q1 + q2 entered together (in-flight ops are never cancelled); the ops
    # claimed after q1 flipped the flag were skipped, never retried here.
    assert results[0][2] == "" and results[1][2] == ""
    assert results[2][1] is None and results[2][2] == "deadline"
    assert results[3][1] is None and results[3][2] == "deadline"
    assert stats.max_inflight == 2


def test_no_deadline_hook_means_no_skips():
    gate, stats, factory = _family(2)
    pool = BackendPool(factory, size=2)  # deadline_passed=None
    results = pool.map_search(["q1", "q2"], max_results=5)
    assert all(err != "deadline" for _q, _i, err in results)


# ---------- extract ----------


def test_map_extract_batches_preserve_order_and_isolate_failures():
    stats = _Stats()

    class ExtractBackend(BarrierBackend):
        def extract(self, urls: list[str], *, char_limit: int = 15000):
            self._stats.enter("extract")
            try:
                if any("bad" in u for u in urls):
                    raise RuntimeError("extract boom")
                return super().extract(urls, char_limit=char_limit)
            finally:
                self._stats.leave()

    pool = BackendPool(lambda: ExtractBackend(threading.Barrier(1), stats), size=2)
    batches = [["https://a.test/1"], ["https://bad.test/2"], ["https://c.test/3"]]
    results = pool.map_extract(batches, char_limit=15000)
    assert results[0] is not None and results[0][0].url == "https://a.test/1"
    assert results[1] is None
    assert results[2] is not None and results[2][0].url == "https://c.test/3"


def test_map_extract_char_limit_passes_through():
    seen: list[int] = []

    class SpyBackend(BarrierBackend):
        def extract(self, urls: list[str], *, char_limit: int = 15000):
            seen.append(char_limit)
            return super().extract(urls, char_limit=char_limit)

    pool = BackendPool(lambda: SpyBackend(threading.Barrier(1), _Stats()), size=1)
    pool.map_extract([["https://a.test/1"]], char_limit=1234)
    assert seen == [1234]


# ---------- laziness, cannot-run, close ----------


def test_workers_are_lazy_until_first_op():
    calls = []
    pool = BackendPool(lambda: calls.append(1) or BarrierBackend(threading.Barrier(1), _Stats()), size=4)
    assert calls == []  # nothing built at construction
    pool.map_search(["q1"], max_results=5)
    assert len(calls) == 1  # one worker, one backend, on first claim


def test_backends_reused_across_map_calls():
    """Slot backends are built once and reused by later maps (§7.1a)."""
    calls = []
    stats = _Stats()
    gate = threading.Barrier(2)  # forces both ops in flight -> both slots build
    pool = BackendPool(lambda: calls.append(1) or BarrierBackend(gate, stats), size=2)
    pool.map_search(["q1", "q2"], max_results=5)
    assert len(calls) == 2  # one per slot on the first map
    pool.map_extract([["https://a.test/1"], ["https://a.test/2"]], char_limit=100)
    assert len(calls) == 2  # reused — no rebuild on the second map
    pool.map_extract([["https://a.test/3"]], char_limit=100)
    assert len(calls) == 2  # single-batch map still rides the cached slot


def test_map_on_empty_input_spawns_nothing():
    calls = []
    pool = BackendPool(lambda: calls.append(1), size=4)
    assert pool.map_search([], max_results=5) == []
    assert pool.map_extract([], char_limit=100) == []
    assert calls == []


def test_factory_failure_raises_cannot_run():
    """Every worker's factory call fails -> the map raises (cannot run at all)."""

    def boom():
        raise RuntimeError("no backends today")

    pool = BackendPool(boom, size=3)
    with pytest.raises(RuntimeError, match="no backends today"):
        pool.map_search(["q1", "q2"], max_results=5)


def test_close_terminates_backends_and_blocks_new_maps():
    stats = _Stats()
    closed: list = []
    pool = BackendPool(lambda: BarrierBackend(threading.Barrier(1), stats, close_log=closed), size=2)
    pool.map_search(["q1", "q2"], max_results=5)
    pool.close()
    assert closed and all(b.closed for b in closed)
    with pytest.raises(RuntimeError, match="closed"):
        pool.map_search(["q3"], max_results=5)


def test_close_without_work_is_noop():
    BackendPool(lambda: BarrierBackend(threading.Barrier(1), _Stats()), size=2).close()


def test_context_manager_closes():
    closed: list = []
    stats = _Stats()
    with BackendPool(lambda: BarrierBackend(threading.Barrier(1), stats, close_log=closed), size=1) as pool:
        pool.map_search(["q1"], max_results=5)
    assert closed and closed[0].closed
