"""plan_ultra partition tests (R15-B2; analysis/r15-interfaces.md §7.2)."""

from __future__ import annotations

from gateway.core.ultra import UltraPlan, plan_ultra


def test_round_robin_partition_two_streams():
    plan = plan_ultra(["a", "b", "c", "d", "e"], max_workstreams=2)
    assert plan.n == 2
    assert plan.reason == "partitioned"
    assert plan.workstreams == [["a", "c", "e"], ["b", "d"]]


def test_n_is_min_of_workstreams_and_queries():
    plan = plan_ultra(["a", "b", "c"], max_workstreams=4)
    assert plan.n == 3
    assert plan.workstreams == [["a"], ["b"], ["c"]]


def test_four_queries_four_streams():
    plan = plan_ultra(["a", "b", "c", "d"], max_workstreams=4)
    assert plan.n == 4
    assert plan.workstreams == [["a"], ["b"], ["c"], ["d"]]


def test_single_query_is_inert():
    plan = plan_ultra(["only"], max_workstreams=4)
    assert plan == UltraPlan(workstreams=[], n=1, reason="single")


def test_empty_input_is_inert():
    assert plan_ultra([], max_workstreams=4).n == 1
    assert plan_ultra(None).n == 1  # type: ignore[arg-type] — never raises


def test_max_workstreams_below_two_is_inert():
    assert plan_ultra(["a", "b"], max_workstreams=1).reason == "single"
    assert plan_ultra(["a", "b"], max_workstreams=0).n == 1


def test_every_query_partitioned_exactly_once():
    queries = [f"q{i}" for i in range(7)]
    plan = plan_ultra(queries, max_workstreams=3)
    flat = [q for stream in plan.workstreams for q in stream]
    assert sorted(flat) == sorted(queries)
    assert len(plan.workstreams) == 3


def test_never_raises_on_weird_input():
    assert plan_ultra("not-a-list-of-queries", max_workstreams=2).n >= 1  # iterable -> partitioned or single
    assert plan_ultra(["a", "b"], max_workstreams="junk").n == 1  # type: ignore[arg-type]
    assert plan_ultra([None, 42], max_workstreams=2).n in (1, 2)
    assert plan_ultra(["a", "b"], max_workstreams=-5).n == 1


def test_plan_shape_is_frozen():
    plan = plan_ultra(["a", "b"], max_workstreams=2)
    assert isinstance(plan.workstreams, list)
    assert isinstance(plan.n, int)
    assert isinstance(plan.reason, str)
