"""gateway/core/ultra.py — ultra separability routing (R15-B2; §7.2).

``plan_ultra`` is a PURE partitioner: it splits the SAME sub-query list B1
computed (plan parts ∪ marker splits, deduped, capped by
``deep_search_queries`` — §6.4) into ``n`` round-robin workstreams. Ultra
changes execution, not the retrieval set — there is no model call here and
the function NEVER raises; ``n < 2`` yields the inert plan and the engine
runs the serial B1 path unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class UltraPlan:
    """Frozen §7.2 plan shape: per-workstream sub-queries + routing note."""

    workstreams: list[list[str]]
    n: int
    reason: str


def plan_ultra(sub_queries: list[str], *, max_workstreams: int = 4) -> UltraPlan:
    """Round-robin partition of *sub_queries* into <= ``max_workstreams`` streams.

    ``n = min(max_workstreams, len(sub_queries))``; ``n < 2`` returns the
    inert plan ``UltraPlan(workstreams=[], n=1, reason="single")`` and
    ``n >= 2`` returns ``reason="partitioned"``. NEVER raises — malformed
    input degrades to the inert plan.
    """
    try:
        queries = list(sub_queries or [])
        n = min(int(max_workstreams), len(queries))
        if n < 2:
            return UltraPlan(workstreams=[], n=1, reason="single")
        streams: list[list[str]] = [[] for _ in range(n)]
        for i, query in enumerate(queries):
            streams[i % n].append(query)
        return UltraPlan(workstreams=streams, n=n, reason="partitioned")
    except Exception:  # noqa: BLE001 — routing must never break the query
        return UltraPlan(workstreams=[], n=1, reason="single")
