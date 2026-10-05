"""Hermetic tests for ``deep_research.py`` and ``verify_deep_research.py``.

Fully offline: no network, no sleeps, no Hermes imports. GOOD/BAD report
fixtures exercise every mechanical check in both directions; CLI coverage goes
through ``main(argv)`` so nothing shells out.
"""

import textwrap

import pytest

import deep_research as dr
import verify_deep_research as vdr

GOOD_REPORT = textwrap.dedent(
    """\
    # Software Testing in 2026: A Deep Research Report

    This report synthesises current evidence on software testing practice.[1]
    It covers definitions, history, tooling comparisons, recent developments, and open risks.[2][3]

    ## Overview and Definition

    ### What software testing means

    Software testing is the disciplined evaluation of a system against its requirements.[1]
    The field distinguishes verification from validation, a split that shapes every practice.[2]
    Modern definitions emphasise risk reduction rather than defect counting.[3]
    This framing connects the craft to business outcomes.[4]

    ### Scope of this report

    The report focuses on industrial practice rather than formal methods alone.[5]
    Academic results are included where they changed tooling or team behaviour.[6]

    ## Background and Mechanism

    Testing grew from ad hoc debugging into an engineering discipline over five decades.[7]
    The shift to continuous integration moved testing left, embedding it in daily development.[8]
    Tooling matured from xUnit frameworks to property-based generators.[9]

    ## Applications and Comparison

    | Approach | Strength  | Weakness |
    |----------|-----------|----------|
    | Unit     | Fast      | Narrow   |
    | E2E      | Realistic | Slow     |

    Trade-offs between layers are well documented.[10]
    Teams rarely pick a single layer; the testing trophy remains the dominant heuristic.[11]

    > "Testing shows the presence, not the absence of bugs."[12]

    ## Recent News and Updates

    Recent releases pushed AI-assisted test generation into mainstream CI pipelines.[13]
    Vendors now compete on flake detection as much as raw speed.[14]

    ## Risks and Limitations

    Coverage metrics can mislead when they reward shallow assertions.[15]
    AI-generated tests risk encoding the same misunderstandings as the code they check.[16]

    ## Conclusion and Next Steps

    The evidence favours layered, risk-weighted testing with explicit ownership.[17]
    Next steps include auditing flake rates and piloting property-based suites.[18]

    ## Sources

    [1] https://example.com/1
    [2] https://example.com/2
    [3] https://example.com/3
    [4] https://example.com/4
    [5] https://example.com/5
    [6] https://example.com/6
    [7] https://example.com/7
    [8] https://example.com/8
    [9] https://example.com/9
    [10] https://example.com/10
    [11] https://example.com/11
    [12] https://example.com/12
    [13] https://example.com/13
    [14] https://example.com/14
    [15] https://example.com/15
    [16] https://example.com/16
    [17] https://example.com/17
    [18] https://example.com/18
    """
)

BAD_REPORT = textwrap.dedent(
    """\
    # Bad Report

    Intro paragraph has a space before a citation [1] and a bare https://bad.example.com URL in body text.

    ## Section One

    ### A subsection

    - bullet item one
    - bullet item two

    This sentence carries too many citations.[1][2][3][4]

    ## Section Two

    A normal cited sentence.[2]

    ## Conclusion

    Closing thoughts.[3]

    ## Sources

    [1] https://a.example
    [2] https://b.example
    [3] https://c.example
    [4] https://d.example
    """
)

VI_REPORT = textwrap.dedent(
    """\
    # Báo cáo kiểm thử

    Đoạn này tóm tắt các phát hiện chính của báo cáo nghiên cứu.[1]

    ## Tổng quan và định nghĩa

    ### Định nghĩa

    Nội dung tiếng Việt đủ dài để kiểm tra cơ chế.[1] Câu thứ hai bổ sung chi tiết.[2]

    ## Bối cảnh và cơ chế

    Văn bản mô tả bối cảnh lịch sử phát triển.[3]

    ## Ứng dụng và so sánh

    Phân tích ứng dụng thực tiễn của phương pháp.[4]

    ## Tin tức gần đây

    Tin tức mới nhất về lĩnh vực này.[5]

    ## Rủi ro và hạn chế

    Rủi ro và hạn chế chính được nêu ra.[6]

    ## Kết luận và khuyến nghị

    Tổng hợp phát hiện và khuyến nghị bước tiếp theo.[7]

    ## Sources

    [1] https://a1.example
    [2] https://a2.example
    [3] https://a3.example
    [4] https://a4.example
    [5] https://a5.example
    [6] https://a6.example
    [7] https://a7.example
    """
)

ALL_CHECK_IDS = {
    "C1.title",
    "C1.sections",
    "C1.subsections",
    "C1.conclusion",
    "C2.sources-block",
    "C3.citation-spacing",
    "C3.citations-per-sentence",
    "C3.id-per-bracket",
    "C3.bare-urls",
    "C4.no-lists",
    "C5.word-count",
    "C7.language",
}


def _failed_ids(results):
    return {r.check_id for r in results if r.status == "FAIL"}


# --- check(): fixture-level --------------------------------------------------
def test_good_report_has_no_failures_and_all_check_ids():
    results = dr.check(GOOD_REPORT)
    assert _failed_ids(results) == set()
    assert {r.check_id for r in results} == ALL_CHECK_IDS


def test_bad_report_fails_exactly_the_right_checks():
    results = dr.check(BAD_REPORT)
    assert _failed_ids(results) == {
        "C1.sections",
        "C3.citation-spacing",
        "C3.citations-per-sentence",
        "C3.bare-urls",
        "C4.no-lists",
    }
    passed = {r.check_id for r in results if r.status == "PASS"}
    assert {
        "C1.title",
        "C1.subsections",
        "C1.conclusion",
        "C2.sources-block",
        "C3.id-per-bracket",
    } <= passed


def test_vietnamese_report_passes_and_c7_stays_info():
    results = dr.check(VI_REPORT)
    assert _failed_ids(results) == set()
    c7 = next(r for r in results if r.check_id == "C7.language")
    assert c7.status == "INFO"
    assert "vietnamese" in c7.detail.lower()


def test_word_count_check_never_fails():
    tiny = "# T\n\nIntro.[1]\n\n## Conclusion\n\nShort.[2]\n\n## Sources\n\n[1] https://a.e\n[2] https://b.e\n"
    results = dr.check(tiny)
    c5 = next(r for r in results if r.check_id == "C5.word-count")
    assert c5.status == "INFO"


def test_missing_sources_block_fails_only_c2():
    body_only = GOOD_REPORT.split("## Sources")[0]
    assert _failed_ids(dr.check(body_only)) == {"C2.sources-block"}


def test_bullets_and_urls_inside_code_fences_are_ignored():
    fenced = GOOD_REPORT.replace(
        "## Sources", "```bash\n- not a body bullet\n1. nor this\nhttps://inside-fence.example\n```\n\n## Sources"
    )
    fails = _failed_ids(dr.check(fenced))
    assert "C4.no-lists" not in fails
    assert "C3.bare-urls" not in fails


# --- helpers ------------------------------------------------------------------
def test_citation_counts_respects_per_sentence_boundary():
    lines = ["Exactly three is fine.[1][2][3]", "Four is too many.[1][2][3][4]"]
    assert dr.citation_counts(lines) == [3, 4]


def test_split_body_sources_uses_last_sources_header():
    body, sources = dr.split_body_sources(GOOD_REPORT)
    assert all("example.com" not in line for line in body)
    assert any("example.com" in line for line in sources)


# --- plan / fanout emitters ----------------------------------------------------
def test_plan_output_shape_vietnamese_query():
    out = dr.build_plan("kiểm thử phần mềm")
    assert "kiểm thử phần mềm" in out
    assert "Kết luận" in out
    assert out.count('web_search "') == len(dr._THEMES) * 2
    assert "là gì" in out  # VI fan-out query
    assert "overview" in out  # EN fan-out query
    assert f"{dr.FANOUT_MIN}-{dr.FANOUT_MAX}" in out
    assert str(dr.POLITENESS_SECONDS) in out
    assert f"{dr.MIN_SECTIONS}" in out


def test_plan_output_shape_english_query():
    out = dr.build_plan("software testing")
    assert "software testing" in out
    assert "Conclusion" in out
    assert out.count('web_search "') == len(dr._THEMES) * 2


def test_fanout_output_shape_and_budget_notes():
    thin = dr.build_fanout(["alpha", "beta", "alpha"])
    assert thin.count('web_search "') == 2  # deduped, order preserved
    assert "below" in thin
    assert str(dr.POLITENESS_SECONDS) in thin
    assert "sources.py" in thin
    assert "search_backend" in thin

    in_budget = dr.build_fanout([f"q{i}" for i in range(7)])
    assert "within" in in_budget

    over = dr.build_fanout([f"q{i}" for i in range(12)])
    assert "above" in over


# --- deep_research CLI ----------------------------------------------------------
def test_cli_check_good_report(tmp_path, capsys):
    report = tmp_path / "good.md"
    report.write_text(GOOD_REPORT, encoding="utf-8")
    assert dr.main(["check", str(report)]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert "PASS" in out


def test_cli_check_bad_report_exit_1(tmp_path):
    report = tmp_path / "bad.md"
    report.write_text(BAD_REPORT, encoding="utf-8")
    assert dr.main(["check", str(report)]) == 1


def test_cli_check_missing_file_exits_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        dr.main(["check", str(tmp_path / "nope.md")])
    assert exc.value.code == 2


def test_cli_plan_and_fanout(capsys):
    assert dr.main(["plan", "kiểm thử"]) == 0
    assert "kiểm thử" in capsys.readouterr().out
    assert dr.main(["fanout", "--queries", "a", "b"]) == 0
    assert 'web_search "a"' in capsys.readouterr().out


def test_cli_no_command_exits_2():
    with pytest.raises(SystemExit):
        dr.main([])


# --- verify_deep_research CLI ---------------------------------------------------
def test_verify_good_report_exit_0(tmp_path, capsys):
    report = tmp_path / "good.md"
    report.write_text(GOOD_REPORT, encoding="utf-8")
    assert vdr.main([str(report)]) == 0
    out = capsys.readouterr().out
    for cid in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"):
        assert cid in out
    assert "sources.py verify" in out
    assert "RESULT: PASS" in out


def test_verify_bad_report_exit_1(tmp_path, capsys):
    report = tmp_path / "bad.md"
    report.write_text(BAD_REPORT, encoding="utf-8")
    assert vdr.main([str(report)]) == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_verify_missing_file_exits_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        vdr.main([str(tmp_path / "nope.md")])
    assert exc.value.code == 2
