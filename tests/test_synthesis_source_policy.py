"""R10-B — synthesis source-policy rules (hermetic).

The system prompt must carry source-tier policy for law/government/admin
questions while keeping the R10-A date/freshness rules intact (co-existence).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gateway.core.synthesis import _messages
from gateway.protocols import EvidenceItem

FIXED_NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone(timedelta(hours=7)))

_EVIDENCE = [EvidenceItem(id=1, title="T", url="https://e.test/1", content="body")]


def _system(deep: bool = False) -> str:
    return _messages("xe máy điện có cần bằng lái không", _EVIDENCE, deep, now=FIXED_NOW)[0]["content"]


def test_source_policy_lines_present():
    system = _system()
    for needle in (
        "law, government, or administrative questions",
        "primary legal-tier sources",
        "commercial or retail sources as the legal basis",
        "non-authoritative evidence",
    ):
        assert needle in system, needle


def test_source_policy_present_for_deep_answers_too():
    system = _system(deep=True)
    assert "primary legal-tier sources" in system
    assert "DEEP research answer" in system


def test_r10a_date_block_and_rules_still_present():
    """Co-existence: the R10-A date block and rules survive the R10-B edit."""
    system = _system()
    assert "Current date: 2026-10-07 (Wednesday), Asia/Ho_Chi_Minh, UTC+7." in system
    for needle in (
        "time-relative",
        "resolved absolute date",
        "as-of date",
        "in days",
        "fabricate dates",
        "not time-sensitive",
    ):
        assert needle in system, needle


def test_base_rules_still_present():
    system = _system()
    for needle in (
        "Answer in the SAME LANGUAGE as the user's question",
        "Ground every statement ONLY on the evidence passages below",
        "citation marker [n]",
        "If the evidence is insufficient, say so plainly",
        "## Sources",
    ):
        assert needle in system, needle
