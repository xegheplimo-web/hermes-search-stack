"""Hermetic tests for ``depth_policy.py`` (R6-C depth-escalation policy).

Fully offline and deterministic: inline signal dicts only, no network, no
filesystem, no sleeps. CLI coverage goes through ``main(argv)`` so nothing
shells out.
"""

import json

import pytest

import depth_policy as dp

CASES = [
    {
        "name": "empty signals default to fast (unmeasured chars skip)",
        "signals": {},
        "mode": "fast",
        "score": 0.15,
        "reasons_contain": ["few search results"],
        "reasons_absent": ["tiny extract", "errors with thin evidence", "rich evidence"],
    },
    {
        "name": "all thin evidence escalates to deep",
        "signals": {
            "query": "gold price today",
            "search_result_counts": [2],
            "extract_char_totals": [500],
            "errors": ["backend timeout"],
            "query_markers": {},
        },
        "mode": "deep",
        "score": 0.45,
        "reasons_contain": ["errors with thin evidence", "few search results", "tiny extract"],
    },
    {
        "name": "rich evidence stays fast with discount",
        "signals": {
            "query": "plain overview",
            "search_result_counts": [6, 6],
            "extract_char_totals": [9000, 8000],
            "errors": [],
            "query_markers": {},
        },
        "mode": "fast",
        "score": -0.20,
        "reasons_contain": ["rich evidence"],
    },
    {
        "name": "comparative marker alone stays fast",
        "signals": {
            "query": "plain",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": [],
            "query_markers": {"comparative": True},
        },
        "mode": "fast",
        "score": 0.30,
        "reasons_contain": ["comparative"],
    },
    {
        "name": "comparative plus multi-part escalates",
        "signals": {
            "query": "plain",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": [],
            "query_markers": {"comparative": True, "multi_part": True},
        },
        "mode": "deep",
        "score": 0.55,
        "reasons_contain": ["comparative", "multi-part"],
    },
    {
        "name": "multi-part marker alone stays fast",
        "signals": {
            "query": "plain",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": [],
            "query_markers": {"multi_part": True},
        },
        "mode": "fast",
        "score": 0.25,
        "reasons_contain": ["multi-part"],
    },
    {
        "name": "two deep terms add weight but stay fast alone",
        "signals": {
            "query": "compare research methods",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": [],
            "query_markers": {},
        },
        "mode": "fast",
        "score": 0.20,
        "reasons_contain": ["deep-research terms"],
    },
    {
        "name": "deep terms plus thin evidence escalate",
        "signals": {
            "query": "compare research methods",
            "search_result_counts": [2],
            "extract_char_totals": [500],
            "errors": [],
            "query_markers": {},
        },
        "mode": "deep",
        "score": 0.45,
        "reasons_contain": ["deep-research terms", "few search results", "tiny extract"],
    },
    {
        "name": "single deep term gets no bonus",
        "signals": {
            "query": "research methods overview",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": [],
            "query_markers": {},
        },
        "mode": "fast",
        "score": 0.0,
        "reasons_contain": [],
    },
    {
        "name": "vietnamese deep terms match",
        "signals": {
            "query": "so sánh chi tiết giá vàng",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": [],
            "query_markers": {},
        },
        "mode": "fast",
        "score": 0.20,
        "reasons_contain": ["deep-research terms"],
    },
    {
        "name": "errors with ample chars get no bonus",
        "signals": {
            "query": "plain",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": ["one backend failed"],
            "query_markers": {},
        },
        "mode": "fast",
        "score": 0.0,
        "reasons_contain": [],
    },
    {
        "name": "boundary score exactly 0.45 escalates",
        "signals": {
            "query": "plain",
            "search_result_counts": [2],
            "extract_char_totals": [5000],
            "errors": [],
            "query_markers": {"comparative": True},
        },
        "mode": "deep",
        "score": 0.45,
        "reasons_contain": ["comparative", "few search results"],
    },
    {
        "name": "just below threshold stays fast",
        "signals": {
            "query": "plain",
            "search_result_counts": [2],
            "extract_char_totals": [5000],
            "errors": [],
            "query_markers": {"multi_part": True},
        },
        "mode": "fast",
        "score": 0.40,
        "reasons_contain": ["multi-part", "few search results"],
    },
    {
        "name": "rich discount offsets comparative marker",
        "signals": {
            "query": "plain",
            "search_result_counts": [6, 6],
            "extract_char_totals": [9000, 9000],
            "errors": [],
            "query_markers": {"comparative": True},
        },
        "mode": "fast",
        "score": 0.10,
        "reasons_contain": ["comparative", "rich evidence"],
    },
    {
        "name": "vs needs word boundaries",
        "signals": {
            "query": "observations data review",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": [],
            "query_markers": {},
        },
        "mode": "fast",
        "score": 0.0,
        "reasons_contain": [],
    },
    {
        "name": "vn marker alone changes nothing",
        "signals": {
            "query": "plain",
            "search_result_counts": [5, 5],
            "extract_char_totals": [3000, 3000],
            "errors": [],
            "query_markers": {"vn": True},
        },
        "mode": "fast",
        "score": 0.0,
        "reasons_contain": [],
    },
]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_decision_table(case):
    """Table-driven check of mode, exact score, and triggered reasons."""
    decision = dp.needs_depth(case["signals"])
    assert decision["mode"] == case["mode"]
    assert decision["score"] == pytest.approx(case["score"])
    for fragment in case["reasons_contain"]:
        assert any(fragment in reason for reason in decision["reasons"]), decision["reasons"]
    for fragment in case.get("reasons_absent", []):
        assert not any(fragment in reason for reason in decision["reasons"]), decision["reasons"]
    if not case["reasons_contain"]:
        assert decision["reasons"] == []
    assert set(decision) == {"mode", "score", "reasons"}


# ---------- r9 §C: extract_char_totals measurement semantics ----------


def test_absent_chars_equal_empty_chars():
    """Missing key and explicit ``[]`` both mean "unmeasured"."""
    assert dp.needs_depth({"query": "q", "search_result_counts": [2]}) == dp.needs_depth(
        {"query": "q", "search_result_counts": [2], "extract_char_totals": []}
    )


def test_unmeasured_chars_skip_all_char_rules():
    """``[]`` -> no TINY / ERRORS_THIN / RICH reasons, even with errors."""
    decision = dp.needs_depth({"query": "q", "search_result_counts": [10], "errors": ["backend down"]})
    assert decision["reasons"] == []
    assert decision["score"] == 0.0
    assert decision["mode"] == "fast"


def test_measured_zeros_still_fire_tiny():
    """``[0]`` is measured evidence of empty pages -> TINY fires (+0.10)."""
    decision = dp.needs_depth({"query": "q", "search_result_counts": [10], "extract_char_totals": [0]})
    assert any("tiny extract" in r for r in decision["reasons"])
    assert decision["score"] == pytest.approx(0.10)


def test_measured_zeros_plus_errors_fire_errors_thin():
    """``[0]`` + errors -> ERRORS_THIN fires; same errors on ``[]`` do not."""
    measured = dp.needs_depth({"query": "q", "search_result_counts": [10], "extract_char_totals": [0], "errors": ["e"]})
    assert any("errors with thin evidence" in r for r in measured["reasons"])
    assert measured["score"] == pytest.approx(0.30)  # 0.20 errors + 0.10 tiny
    unmeasured = dp.needs_depth({"query": "q", "search_result_counts": [10], "errors": ["e"]})
    assert not any("errors with thin evidence" in r for r in unmeasured["reasons"])


def test_rich_discount_needs_measured_chars():
    """RICH fires on measured large chars but can never fire on ``[]``."""
    rich = dp.needs_depth({"query": "q", "search_result_counts": [10], "extract_char_totals": [16000]})
    assert any("rich evidence" in r for r in rich["reasons"])
    assert rich["score"] == pytest.approx(-0.20)
    unmeasured = dp.needs_depth({"query": "q", "search_result_counts": [10]})
    assert not any("rich evidence" in r for r in unmeasured["reasons"])


def test_vn_marker_is_reserved_non_scoring():
    """``vn`` validated but never scored: True == False == absent."""
    base = {"query": "q", "search_result_counts": [5, 5], "extract_char_totals": [3000, 3000]}
    with_vn = dp.needs_depth({**base, "query_markers": {"vn": True}})
    without_vn = dp.needs_depth({**base, "query_markers": {"vn": False}})
    absent = dp.needs_depth({**base, "query_markers": {}})
    assert with_vn == without_vn == absent
    assert not any("vn" in r.lower() for r in with_vn["reasons"])


def test_partial_signals_use_defaults():
    """A query-only payload behaves like empty evidence (fast at 0.15)."""
    assert dp.needs_depth({"query": "hello"}) == dp.needs_depth({})


def test_deterministic_repeat_calls():
    """Equal inputs always produce equal outputs."""
    signals = {
        "query": "compare versus research options",
        "search_result_counts": [3],
        "extract_char_totals": [1000],
        "errors": ["flaky backend"],
        "query_markers": {"comparative": True, "multi_part": True},
    }
    assert dp.needs_depth(signals) == dp.needs_depth(signals)


BAD_SIGNALS = [
    {"name": "signals not an object", "signals": [1, 2]},
    {"name": "query wrong type", "signals": {"query": 42}},
    {"name": "counts wrong type", "signals": {"search_result_counts": "many"}},
    {"name": "counts element wrong type", "signals": {"search_result_counts": ["5"]}},
    {"name": "counts element bool", "signals": {"search_result_counts": [True]}},
    {"name": "counts negative", "signals": {"search_result_counts": [-1]}},
    {"name": "chars wrong type", "signals": {"extract_char_totals": {}}},
    {"name": "chars element wrong type", "signals": {"extract_char_totals": [1.5]}},
    {"name": "errors wrong type", "signals": {"errors": "boom"}},
    {"name": "errors element wrong type", "signals": {"errors": [42]}},
    {"name": "markers wrong type", "signals": {"query_markers": [True]}},
    {"name": "marker value wrong type", "signals": {"query_markers": {"comparative": "yes"}}},
    {"name": "vn marker wrong type", "signals": {"query_markers": {"vn": "yes"}}},
]


@pytest.mark.parametrize("case", BAD_SIGNALS, ids=[c["name"] for c in BAD_SIGNALS])
def test_invalid_signal_types_raise(case):
    """Wrong types raise (the CLI maps these to exit code 2)."""
    with pytest.raises((TypeError, ValueError)):
        dp.needs_depth(case["signals"])


def test_cli_decide_thin_evidence_is_deep(capsys):
    """Thin-evidence JSON through the CLI decides deep."""
    signals = json.dumps(
        {
            "query": "gold price today",
            "search_result_counts": [2],
            "extract_char_totals": [500],
            "errors": ["timeout"],
            "query_markers": {},
        }
    )
    assert dp.main(["decide", "--signals", signals]) == 0
    out = capsys.readouterr().out
    assert "deep" in out


def test_cli_decide_rich_json_flag(capsys):
    """Rich-evidence JSON with --json emits a fast machine-readable decision."""
    signals = json.dumps(
        {
            "query": "plain overview",
            "search_result_counts": [6, 6],
            "extract_char_totals": [9000, 8000],
            "errors": [],
            "query_markers": {},
        }
    )
    assert dp.main(["decide", "--signals", signals, "--json"]) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["mode"] == "fast"
    assert set(decision) == {"mode", "score", "reasons"}


def test_cli_decide_malformed_json_exits_2(capsys):
    """Non-JSON --signals payload exits 2."""
    assert dp.main(["decide", "--signals", "{not-json"]) == 2


def test_cli_decide_wrong_types_exit_2(capsys):
    """Well-formed JSON with wrong signal types exits 2."""
    assert dp.main(["decide", "--signals", '{"query": 42}']) == 2


def test_cli_table_prints_rules(capsys):
    """The table subcommand prints every rule weight and the threshold."""
    assert dp.main(["table"]) == 0
    out = capsys.readouterr().out
    for fragment in ("0.30", "0.25", "0.20", "0.15", "0.10", "0.45"):
        assert fragment in out
