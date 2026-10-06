"""Depth-routing wrapper over repo ``depth_policy.needs_depth`` (r8 §5/§6).

Builds the frozen signals dict — keys ``query``, ``search_result_counts``,
``extract_char_totals``, ``errors``, ``query_markers`` — derives lightweight
query markers (comparative / multi_part / vn), and returns the policy decision
``{"mode": "fast"|"deep", "score": float, "reasons": [str]}`` unchanged.
``depth_policy`` is imported lazily so this module stays importable anywhere.
"""

from __future__ import annotations

import re

# Distinctly Vietnamese letters (unambiguous) plus the full marked-vowel set.
_VN_CHARS_RE = re.compile(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]")
_VN_WORDS_RE = re.compile(
    r"\b(của|và|không|cho|trong|được|với|người|hôm nay|giá|tin tức|tại|thành phố|là gì|nào)\b",
    re.IGNORECASE,
)
_COMPARATIVE_RE = re.compile(
    r"\b(vs\.?|versus|compare|comparison|compared|difference|differences|better than|worse than"
    r"|so sánh|khác nhau|khác biệt)\b",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"[?;!]+|\band\b|\bvà\b|\bthen\b", re.IGNORECASE)


def query_markers(query: str) -> dict:
    """Derive ``query_markers`` for the depth signals from the raw query.

    ``comparative`` fires on comparison vocabulary (EN + VN); ``multi_part``
    when the query splits into two+ substantive clauses or carries 2+ question
    marks; ``vn`` on distinctly Vietnamese characters or common VN function
    words. Deterministic heuristics — markers are advisory inputs to the
    policy, never a routing decision on their own.
    """
    text = str(query or "")
    clauses = [c for c in _SPLIT_RE.split(text) if len(c.split()) >= 4]
    return {
        "comparative": bool(_COMPARATIVE_RE.search(text)),
        "multi_part": len(clauses) >= 2 or text.count("?") >= 2,
        "vn": bool(_VN_CHARS_RE.search(text) or _VN_WORDS_RE.search(text)),
    }


def build_signals(
    query: str,
    *,
    search_result_counts: list[int] | None = None,
    extract_char_totals: list[int] | None = None,
    errors: list[str] | None = None,
    markers: dict | None = None,
) -> dict:
    """Assemble the frozen ``depth_policy`` signals object (exact keys)."""
    return {
        "query": str(query or ""),
        "search_result_counts": [int(c) for c in (search_result_counts or [])],
        "extract_char_totals": [int(c) for c in (extract_char_totals or [])],
        "errors": [str(e) for e in (errors or [])],
        "query_markers": dict(markers) if markers is not None else query_markers(query),
    }


def decide(signals: dict) -> dict:
    """Apply the depth policy to *signals* -> ``{"mode", "score", "reasons"}``."""
    import depth_policy

    return depth_policy.needs_depth(signals)


def split_query(query: str, *, max_parts: int = 2) -> list[str]:
    """Simple sub-question splits for multi_part deep queries (contract §5).

    Returns up to *max_parts* substantive clauses distinct from the original
    query — used as additional deep-search probes, never alone.
    """
    parts: list[str] = []
    seen = set()
    for clause in _SPLIT_RE.split(str(query or "")):
        clause = clause.strip(" \t\n-–—:,")
        key = clause.casefold()
        if len(clause.split()) >= 3 and key not in seen:
            seen.add(key)
            parts.append(clause)
        if len(parts) >= max_parts:
            break
    return parts
