"""Tests for gateway.core.planner (R15-B1; analysis/r15-interfaces.md §6.2).

Hermetic: the duck-typed ``llm`` seam is a scripted fake — no network.
"""

from __future__ import annotations

import json

import pytest

from gateway.core.planner import Plan, plan_query


class FakeLLM:
    """Scripted ``llm.complete(prompt, *, max_tokens) -> str`` stand-in."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, int]] = []

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        if not self.responses:
            raise RuntimeError("no scripted response")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _payload(subs, multi_hop=False) -> str:
    return json.dumps({"sub_questions": subs, "multi_hop": multi_hop})


# ---------- model path ----------


def test_model_path_valid_plan():
    llm = FakeLLM(_payload(["alpha legal status", "beta legal status"], multi_hop=True))
    plan = plan_query("compare alpha and beta legal status", llm=llm)
    assert plan.needed is True
    assert plan.source == "model"
    assert plan.sub_questions == ["alpha legal status", "beta legal status"]
    assert plan.multi_hop is True
    assert len(llm.calls) == 1  # ONE bounded call


def test_model_path_empty_subs_not_needed():
    llm = FakeLLM(_payload([], multi_hop=False))
    plan = plan_query("what is the capital of france", llm=llm)
    assert plan.needed is False
    assert plan.source == "model"
    assert plan.sub_questions == []


def test_model_path_caps_subquestions():
    llm = FakeLLM(_payload([f"sub question number {i}" for i in range(6)]))
    plan = plan_query("many parts", llm=llm, max_subquestions=2)
    assert plan.sub_questions == ["sub question number 0", "sub question number 1"]


def test_model_path_item_validation():
    query = "the original question text"
    llm = FakeLLM(
        _payload(
            [
                "ok",  # too short (<3)
                "a perfectly good sub question",
                "a perfectly good sub question",  # duplicate
                query,  # the query itself must be excluded
                "x" * 300,  # too long (>200)
                42,  # non-string dropped
            ]
        )
    )
    plan = plan_query(query, llm=llm)
    assert plan.sub_questions == ["a perfectly good sub question"]


def test_model_path_tolerates_json_fences():
    llm = FakeLLM(f"```json\n{_payload(['fenced sub question'])}\n```")
    plan = plan_query("some query here", llm=llm)
    assert plan.source == "model"
    assert plan.sub_questions == ["fenced sub question"]


# ---------- fallback paths ----------


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        json.dumps(["sub_questions", "multi_hop"]),  # not an object
        json.dumps({"multi_hop": True}),  # missing sub_questions
        json.dumps({"sub_questions": "nope", "multi_hop": True}),  # wrong type
        json.dumps({"sub_questions": [], "multi_hop": "yes"}),  # non-bool flag
        RuntimeError("model exploded"),  # llm raises
    ],
)
def test_model_deviation_falls_back_to_heuristic(bad):
    llm = FakeLLM(bad)
    plan = plan_query("what is alpha and what is beta", llm=llm)
    assert plan.source == "heuristic"
    assert plan.needed is True
    assert plan.sub_questions == ["what is alpha", "what is beta"]
    assert len(llm.calls) == 1  # still exactly one model call


def test_no_llm_uses_heuristic():
    plan = plan_query("what is alpha and what is beta", llm=None)
    assert plan.source == "heuristic"
    assert plan.sub_questions == ["what is alpha", "what is beta"]
    assert plan.multi_hop is False


def test_heuristic_empty_gives_none_source():
    plan = plan_query("hi", llm=None)
    assert plan.needed is False
    assert plan.source == "none"
    assert plan.sub_questions == []


def test_empty_query_never_raises():
    for llm in (None, FakeLLM(_payload(["x"]))):
        plan = plan_query("", llm=llm)
        assert plan.needed is False and plan.source == "none"
        plan = plan_query(None, llm=llm)
        assert plan.needed is False and plan.source == "none"


def test_zero_max_subquestions_never_needed():
    plan = plan_query("what is alpha and what is beta", llm=FakeLLM(_payload(["a sub"])), max_subquestions=0)
    assert plan.needed is False and plan.source == "none"


def test_plan_dataclass_shape():
    plan = Plan(needed=False)
    assert plan.sub_questions == [] and plan.multi_hop is False and plan.source == "none"
