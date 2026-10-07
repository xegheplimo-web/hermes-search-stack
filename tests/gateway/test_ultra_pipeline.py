"""Ultra engine-integration tests (R15-B2; analysis/r15-interfaces.md §7.3/§7.6).

Hermetic: StubBackend primary + injected ``BackendPool`` fakes — a Barrier
fake proves parallel execution (a serialized pool trips the barrier
timeout and the assertions fail explicitly; nothing ever hangs). The
conftest autouse fixture keeps the vn-geo db absent (C2 merge is a no-op).
"""

from __future__ import annotations

import threading
import time

from gateway.backends.stub import StubBackend
from gateway.config import GatewayConfig
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine
from gateway.core.pool import BackendPool
from gateway.protocols import ExtractItem, SearchItem
from tests.gateway.conftest import FakeSynthesizer

BARRIER_TIMEOUT_S = 10.0
MULTIPART = "what is alpha thing? and what is beta thing?"
SUB_A = "what is alpha thing"
SUB_B = "what is beta thing"


class _Stats:
    """Shared in-flight counters + call log across pool worker instances."""

    def __init__(self):
        self.lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0
        self.search_calls: list[str] = []
        self.extract_calls: list[list[str]] = []

    def enter(self, tag: str) -> None:
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.search_calls.append(tag)

    def leave(self) -> None:
        with self.lock:
            self.inflight -= 1

    def record_extract(self, urls: list[str]) -> None:
        with self.lock:
            self.extract_calls.append(list(urls))


class PoolStubBackend:
    """Per-worker backend; ``search`` optionally waits on a shared barrier.

    With ``barrier = threading.Barrier(n)`` the op completes only when n
    searches are in flight together — direct proof of concurrency.
    """

    name = "pool-stub"

    def __init__(self, stats: _Stats, gate: threading.Barrier | None = None, sleep_s: float = 0.0):
        self._stats = stats
        self._gate = gate
        self._sleep = sleep_s
        self.closed = False

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        self._stats.enter(query)
        try:
            if self._sleep:
                time.sleep(self._sleep)
            if self._gate is not None:
                self._gate.wait(timeout=BARRIER_TIMEOUT_S)
            slug = query.replace(" ", "-")
            return [SearchItem(title=f"{query} result", url=f"https://pool.test/{slug}")]
        finally:
            self._stats.leave()

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        self._stats.record_extract(urls)
        return [ExtractItem(url=u, title=f"title {u}", content=f"content for {u}") for u in urls]

    def ping(self) -> dict:
        return {"ok": True, "detail": "pool stub"}

    def close(self) -> None:
        self.closed = True


def _cfg(tmp_path, **over) -> GatewayConfig:
    return GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
        **over,
    )


def _engine(tmp_path, *, pool=None, backend=None, synth=None, llm=None, **cfg_over) -> Engine:
    cfg = _cfg(tmp_path, **cfg_over)
    return Engine(
        cfg,
        backend=backend if backend is not None else StubBackend(),
        synth=synth if synth is not None else FakeSynthesizer(),
        cache=GatewayCache(cfg.cache_db_path),
        planner_llm=llm,
        pool=pool,
    )


def _ids_sequential(result) -> bool:
    return [s.id for s in result.sources] == list(range(1, len(result.sources) + 1))


# ---------- ultra runs in parallel ----------


def test_separable_deep_query_runs_ultra_in_parallel(tmp_path):
    """Barrier(2) releases only when both workstream searches overlap."""
    stats = _Stats()
    gate = threading.Barrier(2)
    pool = BackendPool(lambda: PoolStubBackend(stats, gate), size=2)
    engine = _engine(tmp_path, pool=pool)
    result = engine.run(MULTIPART, depth="deep")
    assert result.depth == "deep"
    assert result.timings_ms["ultra_n"] == 2
    assert "ultra_ms" in result.timings_ms
    assert stats.max_inflight == 2, "workstream searches did not overlap"
    # Probe ran on the primary backend; the two sub-searches ran on the pool.
    assert engine._backend.search_calls == [(MULTIPART, engine.config.fast_max_results)]
    assert sorted(stats.search_calls) == sorted([SUB_A, SUB_B])
    assert result.sources and _ids_sequential(result)
    assert not any("serial fallback" in w or "retried serially" in w for w in result.warnings)


def test_extract_batches_split_per_workstream(tmp_path):
    """Per-workstream URL batches cover the SAME capped set as B1's call."""
    stats = _Stats()
    pool = BackendPool(lambda: PoolStubBackend(stats), size=4)
    engine = _engine(tmp_path, pool=pool, fast_max_results=1, deep_extract=6)
    result = engine.run(MULTIPART, depth="deep")
    assert result.timings_ms["ultra_n"] == 2
    # probe url + one url per workstream -> three batches, merged in order.
    assert len(stats.extract_calls) >= 2
    flat = [u for batch in stats.extract_calls for u in batch]
    assert sorted(flat) == sorted({u for b in stats.extract_calls for u in b})
    assert "https://pool.test/what-is-alpha-thing" in flat
    assert "https://pool.test/what-is-beta-thing" in flat
    # The identical set B1 would extract — nothing extra, nothing dropped.
    assert engine._backend.extract_calls == []
    assert _ids_sequential(result)


def test_non_separable_deep_query_has_no_ultra_keys(tmp_path):
    engine = _engine(tmp_path)
    result = engine.run("plain question", depth="deep")
    assert result.depth == "deep"
    assert "ultra_ms" not in result.timings_ms
    assert "ultra_n" not in result.timings_ms
    # Single-part query: only the probe search ran.
    assert engine._backend.search_calls == [("plain question", engine.config.fast_max_results)]


def test_fast_path_never_touches_ultra(tmp_path):
    engine = _engine(tmp_path)
    result = engine.run(MULTIPART, depth="fast")
    assert result.depth == "fast"
    assert "ultra_ms" not in result.timings_ms
    assert "ultra_n" not in result.timings_ms


# ---------- ultra disabled = byte-compatible B1 ----------


class ExplodingPool:
    """Any map call is a bug — must never be reached when ultra is off."""

    def map_search(self, *a, **k):  # pragma: no cover
        raise AssertionError("pool used while ultra_enabled=False")

    def map_extract(self, *a, **k):  # pragma: no cover
        raise AssertionError("pool used while ultra_enabled=False")

    def close(self):
        pass


def test_ultra_disabled_is_byte_compatible_b1(tmp_path):
    engine = _engine(tmp_path, pool=ExplodingPool(), ultra_enabled=False)
    result = engine.run(MULTIPART, depth="deep")
    assert "ultra_ms" not in result.timings_ms and "ultra_n" not in result.timings_ms
    assert not any("ultra" in w for w in result.warnings)
    # Same calls, same order as the serial B1 path.
    assert engine._backend.search_calls == [
        (MULTIPART, engine.config.fast_max_results),
        (SUB_A, engine.config.fast_max_results),
        (SUB_B, engine.config.fast_max_results),
    ]
    assert len(engine._backend.extract_calls) == 1


def test_single_workstream_is_inert(tmp_path):
    """deep_search_queries=1 -> the B1 set has <=1 sub-query -> serial."""
    engine = _engine(tmp_path, deep_search_queries=1)
    result = engine.run(MULTIPART, depth="deep")
    assert "ultra_n" not in result.timings_ms
    assert engine._backend.search_calls == [
        (MULTIPART, engine.config.fast_max_results),
        (SUB_A, engine.config.fast_max_results),
    ]


# ---------- infra failure -> serial retry ----------


def test_infra_failure_retried_serially_on_primary(tmp_path):
    stats = _Stats()

    class PartialFailBackend(PoolStubBackend):
        def search(self, query: str, *, max_results: int = 10):
            if "beta" in query:
                raise RuntimeError("worker boom")
            return super().search(query, max_results=max_results)

    pool = BackendPool(lambda: PartialFailBackend(stats), size=2)
    engine = _engine(tmp_path, pool=pool)
    result = engine.run(MULTIPART, depth="deep")
    assert result.timings_ms["ultra_n"] == 2
    assert "ultra: 1 op(s) retried serially" in result.warnings
    # The failed op was retried ONCE on the primary backend (probe + retry).
    assert engine._backend.search_calls == [
        (MULTIPART, engine.config.fast_max_results),
        (SUB_B, engine.config.fast_max_results),
    ]
    assert result.sources and _ids_sequential(result)


# ---------- pool cannot run -> serial fallback ----------


def test_pool_factory_failure_falls_back_to_serial(tmp_path):
    def boom():
        raise RuntimeError("no workers today")

    engine = _engine(tmp_path, pool=BackendPool(boom, size=2))
    result = engine.run(MULTIPART, depth="deep")
    assert "ultra pool unavailable — serial fallback" in result.warnings
    assert "ultra_n" not in result.timings_ms and "ultra_ms" not in result.timings_ms
    # Sub-queries ran sequentially on the primary backend, exactly like B1.
    assert engine._backend.search_calls == [
        (MULTIPART, engine.config.fast_max_results),
        (SUB_A, engine.config.fast_max_results),
        (SUB_B, engine.config.fast_max_results),
    ]


def test_map_raising_pool_falls_back_to_serial(tmp_path):
    class DeadPool:
        def map_search(self, *a, **k):
            raise RuntimeError("pool dead")

        def close(self):
            pass

    engine = _engine(tmp_path, pool=DeadPool())
    result = engine.run(MULTIPART, depth="deep")
    assert "ultra pool unavailable — serial fallback" in result.warnings
    assert "ultra_n" not in result.timings_ms
    assert engine._backend.search_calls[0] == (MULTIPART, engine.config.fast_max_results)


# ---------- deadline ----------


def test_deadline_after_parallel_searches_yields_partial(tmp_path):
    stats = _Stats()
    gate = threading.Barrier(2)
    # Ops in flight when the deadline lands still finish (cooperative, §7.1);
    # extraction is then skipped and snippets are the partial evidence.
    pool = BackendPool(lambda: PoolStubBackend(stats, gate, sleep_s=0.6), size=2)
    engine = _engine(tmp_path, pool=pool, request_deadline_s=0.4)
    result = engine.run(MULTIPART, depth="deep")
    assert result.timings_ms["deadline_exceeded"] == 1
    assert result.timings_ms["ultra_n"] == 2  # the parallel searches did run
    assert any("request deadline exceeded" in w for w in result.warnings)
    assert engine._backend.extract_calls == []
    assert stats.extract_calls == []  # paid extraction skipped past deadline


# ---------- engine close / context manager ----------


def test_engine_close_releases_pool_and_primary_backend(tmp_path):
    stats = _Stats()
    pool = BackendPool(lambda: PoolStubBackend(stats), size=2)
    engine = _engine(tmp_path, pool=pool)
    engine.run(MULTIPART, depth="deep")
    engine.close()
    assert engine._pool._closed is True  # injected pool terminated


def test_engine_context_manager_closes(tmp_path):
    class ClosableBackend(StubBackend):
        def __init__(self):
            super().__init__()
            self.closed = False

        def close(self):
            self.closed = True

    backend = ClosableBackend()
    cfg = _cfg(tmp_path)
    with Engine(cfg, backend=backend, synth=FakeSynthesizer(), cache=GatewayCache(cfg.cache_db_path)) as engine:
        engine.run("plain question", depth="fast")
    assert backend.closed is True


# ---------- xhigh + ultra coexistence ----------


def test_ultra_does_not_change_the_retrieval_set(tmp_path):
    """The sub-query list is B1's — planner parts ∪ marker splits, capped."""
    import json

    from tests.gateway.test_xhigh_pipeline import FakePlannerLLM

    llm = FakePlannerLLM(json.dumps({"sub_questions": ["gamma details", "delta details"], "multi_hop": False}))
    stats = _Stats()
    pool = BackendPool(lambda: PoolStubBackend(stats), size=4)
    engine = _engine(tmp_path, pool=pool, llm=llm)
    engine.run(MULTIPART, depth="deep")
    # plan parts first, then marker splits, capped at deep_search_queries=3.
    assert sorted(stats.search_calls) == sorted(["gamma details", "delta details", SUB_A])
