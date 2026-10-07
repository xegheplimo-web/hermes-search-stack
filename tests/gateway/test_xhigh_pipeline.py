"""xhigh deep-path pipeline tests (R15-B1; analysis/r15-interfaces.md §6.4/§6.6).

Hermetic: StubBackend + scripted synth/planner fakes; the vn-geo db env is
pointed at an absent tmp path so the C2 merge is a deterministic no-op.
"""

from __future__ import annotations

import json
import time
from collections import deque

import pytest

from gateway.backends.stub import StubBackend
from gateway.config import GatewayConfig
from gateway.core.cache import GatewayCache
from gateway.core.engine import Engine
from gateway.core.local_context import VN_GEO_DB_ENV
from tests.gateway.conftest import FakeSynthesizer


class FakePlannerLLM:
    """Scripted ``llm.complete(prompt, *, max_tokens)`` fake."""

    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls: list[tuple[str, int]] = []

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        item = self.responses.popleft() if self.responses else RuntimeError("no scripted response")
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedSynth:
    """``stream`` fake with the additive ``revise`` kwarg; scripted answers."""

    def __init__(self, *answers: str):
        self._answers = deque(answers)
        self.stream_calls = 0
        self.revise_seen: list[list[str] | None] = []
        self.last_warning = None

    def stream(self, query, evidence, *, deep=False, revise=None):
        self.stream_calls += 1
        self.revise_seen.append(revise)
        text = self._answers.popleft() if self._answers else "Stub extract content article [1]."
        yield text


class SlowSearchBackend(StubBackend):
    name = "slow-stub"

    def __init__(self, sleep_s: float):
        super().__init__()
        self.sleep_s = sleep_s

    def search(self, query: str, *, max_results: int = 10):
        time.sleep(self.sleep_s)
        return super().search(query, max_results=max_results)


SUPPORTED_ANSWER = "Stub extract content article [1]. It is fine."
BAD_CLAIM_ANSWER = "The floods came yearly [99]."
MULTIPART = "what is alpha thing? and what is beta thing?"


@pytest.fixture(autouse=True)
def _no_real_vn_geo_db(tmp_path, monkeypatch):
    """Keep the C2 merge a deterministic no-op (no real db read)."""
    monkeypatch.setenv(VN_GEO_DB_ENV, str(tmp_path / "absent-vn-geo.db"))


def _cfg(tmp_path, **over) -> GatewayConfig:
    return GatewayConfig(
        backend="stub",
        cache_db=str(tmp_path / "answers.db"),
        store_db=str(tmp_path / "store.db"),
        repo_root=str(tmp_path),
        **over,
    )


def _engine(tmp_path, *, synth=None, llm=None, backend=None, **cfg_over) -> Engine:
    cfg = _cfg(tmp_path, **cfg_over)
    return Engine(
        cfg,
        backend=backend or StubBackend(),
        synth=synth or ScriptedSynth(SUPPORTED_ANSWER),
        cache=GatewayCache(cfg.cache_db_path),
        planner_llm=llm,
    )


def _search_queries(engine: Engine) -> list[str]:
    return [q for q, _n in engine._backend.search_calls]


# ---------- plan stage ----------


def test_plan_stage_uses_model_subquestions(tmp_path):
    llm = FakePlannerLLM(
        json.dumps({"sub_questions": ["alpha detail question", "beta detail question"], "multi_hop": True})
    )
    engine = _engine(tmp_path, llm=llm)
    result = engine.run("plain single question", depth="deep")
    assert result.depth == "deep"
    assert _search_queries(engine) == [
        "plain single question",
        "alpha detail question",
        "beta detail question",
    ]
    assert "plan_ms" in result.timings_ms
    assert len(llm.calls) == 1  # ONE plan call
    assert not any("planner" in w for w in result.warnings)


def test_plan_union_with_marker_splits_capped(tmp_path):
    # plan parts ∪ marker splits, deduped, <= deep_search_queries (=3).
    llm = FakePlannerLLM(json.dumps({"sub_questions": ["gamma details", "delta details"], "multi_hop": False}))
    engine = _engine(tmp_path, llm=llm)
    engine.run(MULTIPART, depth="deep")
    queries = _search_queries(engine)
    # probe + 3 capped subs: plan parts first, then marker splits.
    assert queries[:4] == [MULTIPART, "gamma details", "delta details", "what is alpha thing"]


def test_plan_not_needed_keeps_todays_splits(tmp_path):
    llm = FakePlannerLLM(json.dumps({"sub_questions": [], "multi_hop": False}))
    engine = _engine(tmp_path, llm=llm)
    result = engine.run(MULTIPART, depth="deep")
    # needed=False -> today's behavior: marker splits only (<= d_s_q - 1).
    assert _search_queries(engine) == [MULTIPART, "what is alpha thing", "what is beta thing"]
    assert "plan_ms" in result.timings_ms


def test_planner_heuristic_fallback_warns(tmp_path):
    # No llm configured (FakeSynthesizer has no _client) -> heuristic + warning.
    engine = _engine(tmp_path, synth=FakeSynthesizer())
    result = engine.run(MULTIPART, depth="deep")
    assert any("planner" in w for w in result.warnings)
    assert "plan_ms" in result.timings_ms


def test_model_failure_also_warns(tmp_path):
    llm = FakePlannerLLM(RuntimeError("planner down"))
    engine = _engine(tmp_path, llm=llm)
    result = engine.run(MULTIPART, depth="deep")
    assert any("planner" in w for w in result.warnings)


# ---------- claim verify + conditional revise ----------


def test_claims_verify_sets_confidence(tmp_path):
    engine = _engine(tmp_path)
    result = engine.run("plain question", depth="deep")
    assert "verify_claims_ms" in result.timings_ms
    assert result.confidence == "high"
    assert result.gaps == []
    assert engine._synth.stream_calls == 1  # no issues -> no revise


def test_revise_improves_and_accepts_draft2(tmp_path):
    synth = ScriptedSynth(BAD_CLAIM_ANSWER, SUPPORTED_ANSWER)
    engine = _engine(tmp_path, synth=synth)
    result = engine.run("plain question", depth="deep")
    assert result.answer_markdown == SUPPORTED_ANSWER
    assert "revise_ms" in result.timings_ms
    assert synth.stream_calls == 2  # draft 1 + ONE re-draft
    assert synth.revise_seen[-1] == ["The floods came yearly [99]."]  # issue claims attached
    assert not any("did not improve" in w for w in result.warnings)
    assert result.confidence == "high"


def test_revise_not_improved_keeps_draft1(tmp_path):
    draft2 = "Still bad claim here [98]."
    synth = ScriptedSynth(BAD_CLAIM_ANSWER, draft2)
    engine = _engine(tmp_path, synth=synth)
    result = engine.run("plain question", depth="deep")
    assert result.answer_markdown == BAD_CLAIM_ANSWER
    assert any("revision did not improve (1→1) — keeping draft 1" in w for w in result.warnings)
    assert synth.stream_calls == 2


def test_revise_bounded_extra_searches(tmp_path):
    answer = "Alpha missing [91]. Beta missing [92]. Gamma missing [93]."
    synth = ScriptedSynth(answer, answer)
    engine = _engine(tmp_path, synth=synth)
    engine.run("plain question", depth="deep")
    queries = _search_queries(engine)
    # probe + <=2 targeted re-searches (top <=2 of 3 issue claims); no subs.
    assert queries == [
        "plain question",
        "Alpha missing [91].",
        "Beta missing [92].",
    ]
    assert synth.stream_calls == 2  # ONE revise pass max


def test_no_revise_when_zero_budget(tmp_path):
    synth = ScriptedSynth(BAD_CLAIM_ANSWER)
    engine = _engine(tmp_path, synth=synth, xhigh_revise_max=0)
    result = engine.run("plain question", depth="deep")
    assert result.answer_markdown == BAD_CLAIM_ANSWER
    assert "revise_ms" not in result.timings_ms
    assert synth.stream_calls == 1
    assert result.gaps  # issues still reported


# ---------- deadline + xhigh_enabled=False ----------


def test_deadline_skips_xhigh_stages(tmp_path):
    cfg = _cfg(tmp_path, request_deadline_s=0.05)
    engine = Engine(
        cfg,
        backend=SlowSearchBackend(0.4),
        synth=ScriptedSynth(BAD_CLAIM_ANSWER),
        cache=GatewayCache(cfg.cache_db_path),
    )
    result = engine.run("Compare Alpha vs Beta in detail", depth="deep")
    assert result.timings_ms["deadline_exceeded"] == 1
    for key in ("plan_ms", "verify_claims_ms", "revise_ms", "local_ms"):
        assert key not in result.timings_ms
    assert result.confidence is None


def test_xhigh_disabled_is_legacy(tmp_path):
    llm = FakePlannerLLM(json.dumps({"sub_questions": ["never used"], "multi_hop": True}))
    synth = ScriptedSynth(BAD_CLAIM_ANSWER)
    engine = _engine(tmp_path, llm=llm, synth=synth, xhigh_enabled=False)
    result = engine.run(MULTIPART, depth="deep")
    for key in ("plan_ms", "verify_claims_ms", "revise_ms"):
        assert key not in result.timings_ms
    assert llm.calls == []  # planner never called
    assert synth.stream_calls == 1  # single pass, no revise
    assert result.confidence is None
    assert result.gaps == []
    assert not any("planner" in w for w in result.warnings)
    # legacy marker splits still happen
    assert _search_queries(engine) == [MULTIPART, "what is alpha thing", "what is beta thing"]


def test_fast_path_has_no_xhigh_stage_keys(tmp_path):
    llm = FakePlannerLLM(json.dumps({"sub_questions": ["never used"], "multi_hop": True}))
    engine = _engine(tmp_path, llm=llm)
    result = engine.run("weather in Hanoi today", depth="fast")
    for key in ("plan_ms", "verify_claims_ms", "revise_ms"):
        assert key not in result.timings_ms
    assert llm.calls == []
    assert result.confidence is None
    # C2 wiring is both modes: local keys exist (hits 0 — db absent).
    assert "local_ms" in result.timings_ms
    assert result.timings_ms["local_hits"] == 0


def test_deep_xhigh_timings_keys(tmp_path):
    engine = _engine(tmp_path)
    result = engine.run("plain question", depth="deep")
    for key in ("plan_ms", "verify_claims_ms", "local_ms", "local_hits", "verify_ms"):
        assert key in result.timings_ms, key


# ---------- synthesis revise kwarg (§6.4 additive) ----------


def test_synthesis_revise_default_is_byte_identical():
    from datetime import datetime

    from gateway.core.synthesis import _messages
    from gateway.protocols import EvidenceItem

    ev = [EvidenceItem(id=1, title="T", url="https://e.test/1", content="body")]
    now = datetime(2026, 10, 7, 9, 0)
    assert _messages("q", ev, True, now) == _messages("q", ev, True, now, revise=None)


def test_synthesis_revise_prepends_block():
    from gateway.core.synthesis import _messages
    from gateway.protocols import EvidenceItem

    ev = [EvidenceItem(id=1, title="T", url="https://e.test/1", content="body")]
    user = _messages("q", ev, True, revise=["claim one", "claim two"])[1]["content"]
    assert user.startswith("REVISION REQUIRED — fix/remove these claims:")
    assert "- claim one\n- claim two" in user
    assert "Question: q" in user
