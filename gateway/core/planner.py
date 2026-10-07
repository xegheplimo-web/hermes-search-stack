"""gateway/core/planner.py — semantic query planner (R15-B1; §6.2).

Decides whether a deep query should be decomposed into bounded
sub-searches before retrieval. The optional model path goes through a
duck-typed seam — ``llm.complete(prompt: str, *, max_tokens: int) -> str``
(built lazily by the engine from the synth config; tests inject fakes) —
answering ONE strict-JSON completion ``{"sub_questions": [...],
"multi_hop": bool}``. Any deviation (transport error, non-JSON, schema
mismatch) falls back to the heuristic ``router.split_query`` path.
``plan_query`` NEVER raises; the engine surfaces the fallback as a
warning, never silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from gateway.core.router import split_query

_MIN_SQ_CHARS = 3
_MAX_SQ_CHARS = 200
_PLAN_MAX_TOKENS = 400


@dataclass(slots=True)
class Plan:
    """Frozen §6.2 plan shape; ``source`` in {"model", "heuristic", "none"}."""

    needed: bool
    sub_questions: list[str] = field(default_factory=list)
    multi_hop: bool = False
    source: str = "none"


def _prompt(query: str, max_subquestions: int) -> str:
    return (
        "You are the planning stage of a research engine. Decide whether the "
        "question needs to be decomposed into sub-questions for retrieval.\n"
        "Reply with ONLY a JSON object, no prose, no markdown fences:\n"
        '{"sub_questions": ["..."], "multi_hop": true}\n'
        f"Rules: at most {max_subquestions} sub_questions; each is a "
        "self-contained search query; never repeat the original question. "
        'For a simple single-fact question return {"sub_questions": [], '
        '"multi_hop": false}.\n'
        f"Question: {query}"
    )


def _parse_plan_json(text: str) -> dict | None:
    """Strict JSON object parse; ``None`` when the model did not comply."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        # Tolerate ```json fences / surrounding prose: widest {...} span.
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _sanitize_subs(items: list, *, max_subquestions: int, query: str) -> list[str]:
    """§6.2 item validation: 3–200 chars, deduped, query-echo excluded."""
    out: list[str] = []
    seen: set[str] = set()
    qkey = query.strip().casefold()
    for item in items:
        if not isinstance(item, str):
            continue
        sub = item.strip()
        key = sub.casefold()
        if not (_MIN_SQ_CHARS <= len(sub) <= _MAX_SQ_CHARS) or key in seen or key == qkey:
            continue
        seen.add(key)
        out.append(sub)
        if len(out) >= max_subquestions:
            break
    return out


def _heuristic(query: str, max_subquestions: int) -> Plan:
    """``router.split_query`` fallback; empty -> ``needed=False, "none"``."""
    parts = split_query(query, max_parts=max_subquestions)
    if not parts:
        return Plan(needed=False, source="none")
    return Plan(needed=True, sub_questions=parts, multi_hop=False, source="heuristic")


def plan_query(query: str, *, llm=None, max_subquestions: int = 3) -> Plan:
    """Plan the retrieval decomposition for *query*; NEVER raises.

    Model path (llm given): one completion, strict JSON; schema-level
    deviations (unparseable, missing/wrong-typed keys, llm failure) fall
    back to the heuristic split. ``needed`` mirrors whether usable
    sub-questions remain after validation.
    """
    query = str(query or "").strip()
    max_subquestions = max(0, int(max_subquestions))
    if not query or max_subquestions <= 0:
        return Plan(needed=False, source="none")
    if llm is not None:
        try:
            raw = llm.complete(_prompt(query, max_subquestions), max_tokens=_PLAN_MAX_TOKENS)
        except Exception:  # noqa: BLE001 — any model failure -> heuristic
            raw = None
        data = _parse_plan_json(raw)
        if data is not None and isinstance(data.get("sub_questions"), list) and isinstance(data.get("multi_hop"), bool):
            subs = _sanitize_subs(data["sub_questions"], max_subquestions=max_subquestions, query=query)
            return Plan(needed=bool(subs), sub_questions=subs, multi_hop=data["multi_hop"], source="model")
    return _heuristic(query, max_subquestions)
