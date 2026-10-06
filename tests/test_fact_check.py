"""Tests for ``fact_check.py`` — F3-P2 verification pass (frozen contract v1).

Hermetic: no network, no real Hermes runtime. The aux judge path is exercised
through a stubbed ``fact_check._load_aux`` seam (the lazy-import boundary), so
no aux call can ever leave the process.
"""

import json
import types
from pathlib import Path

import pytest

import fact_check as fc

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "factcheck"


def run_json(argv, capsys):
    """Run the CLI in-process with --json appended; return (exit, report, captured)."""
    code = fc.main([*map(str, argv), "--json"])
    out = capsys.readouterr()
    return code, json.loads(out.out), out


def ok_args():
    return ["--draft", FIXTURES / "draft_ok.md", "--ledger", FIXTURES / "ledger_ok.json"]


def bad_args():
    return ["--draft", FIXTURES / "draft_bad.md", "--ledger", FIXTURES / "ledger_bad.json"]


# ---------------------------------------------------------------------------
# Schema shape + clean pass
# ---------------------------------------------------------------------------


def test_clean_draft_passes_with_full_schema(capsys):
    code, rep, _ = run_json(ok_args(), capsys)
    assert code == 0
    assert rep["schema"] == "fact_check.v1"
    assert set(rep) == {
        "schema", "draft", "ledger", "stats", "flags", "claims", "trust", "judge", "summary",
    }
    assert rep["stats"] == {
        "sentences": 2, "cited_sentences": 2, "citations": 2, "unique_ids": 2, "coverage": 1.0,
    }
    assert rep["flags"] == []
    assert rep["summary"] == {"pass": True, "flags_n": 0, "unsupported_n": 0, "conflicting_n": 0}
    assert rep["judge"] == {"mode": "off", "status": "skipped", "verdicts_n": 0}
    assert [c["verdict"] for c in rep["claims"]] == ["not_judged", "not_judged"]
    assert all(set(c) == {"sentence", "ids", "verdict", "quote", "note"} for c in rep["claims"])
    assert rep["claims"][0]["sentence"] == 0 and rep["claims"][0]["ids"] == [1]
    assert rep["trust"]["1"]["tier"] == "primary"
    assert rep["trust"]["2"]["tier"] == "news"


def test_flag_shape_and_sentence_index(capsys):
    _, rep, _ = run_json(bad_args(), capsys)
    assert all(set(f) == {"type", "id", "sentence", "detail"} for f in rep["flags"])
    by_key = {(f["type"], f["id"]): f for f in rep["flags"]}
    assert by_key[("missing_id", 9)]["sentence"] == 2  # 0-based prose-sentence index
    assert by_key[("missing_id", 9)]["detail"]


# ---------------------------------------------------------------------------
# Citation parsing (grounded-citations conventions)
# ---------------------------------------------------------------------------


def test_citation_parsing_skips_links_footnotes_and_sources_block(tmp_path, capsys):
    draft = tmp_path / "draft.md"
    draft.write_text(
        "The first claim is supported by the ledger source.[1]\n"
        "A markdown [link label](https://x.example) needs no ledger entry now.\n"
        "A footnote marker [2]: also stays outside the citation set.\n"
        "The last claim uses the second registered source here.[2]\n\n"
        "## Sources\n\n"
        "[1] https://www.nih.gov/vitamin-d-fact-sheet\n"
        "[2] https://www.reuters.com/health/vitamin-d-deficiency\n",
        encoding="utf-8",
    )
    code, rep, _ = run_json(["--draft", draft, "--ledger", FIXTURES / "ledger_ok.json"], capsys)
    assert code == 0
    assert rep["stats"]["sentences"] == 4
    assert rep["stats"]["cited_sentences"] == 2
    assert rep["stats"]["citations"] == 2
    assert rep["stats"]["unique_ids"] == 2
    assert rep["flags"] == []


def test_citations_inside_fenced_code_are_ignored(tmp_path, capsys):
    draft = tmp_path / "draft.md"
    draft.write_text(
        "Only this real sentence carries a citation marker.[1]\n\n"
        "```\n"
        "A code comment mentions [7] and another reference [8].\n"
        "```\n\n"
        "## Sources\n\n[1] https://www.nih.gov/vitamin-d-fact-sheet\n",
        encoding="utf-8",
    )
    code, rep, _ = run_json(["--draft", draft, "--ledger", FIXTURES / "ledger_ok.json"], capsys)
    assert code == 0
    assert rep["stats"]["unique_ids"] == 1
    assert ("missing_id", 7) not in {(f["type"], f["id"]) for f in rep["flags"]}


# ---------------------------------------------------------------------------
# Mechanical flags
# ---------------------------------------------------------------------------


def test_missing_id_flag_fails_the_run(capsys):
    code, rep, _ = run_json(bad_args(), capsys)
    assert code == 1
    assert rep["summary"]["pass"] is False
    assert ("missing_id", 9) in {(f["type"], f["id"]) for f in rep["flags"]}


def test_no_quote_snippet_only_and_low_trust_flags(capsys):
    _, rep, _ = run_json(bad_args(), capsys)
    flags = {(f["type"], f["id"]) for f in rep["flags"]}
    assert ("no_quote", 1) in flags      # entry carries no quotes
    assert ("snippet_only", 1) in flags  # and no full-extract evidence at all
    assert ("low_trust", 1) in flags     # unknown host + demotions push score below 0.5
    assert ("low_trust", 2) in flags     # unknown host
    assert ("snippet_only", 2) not in flags  # quotes prove a fetched page
    assert ("no_quote", 2) not in flags


def test_extract_marker_suppresses_snippet_only(tmp_path, capsys):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"version": 1, "sources": [
        {"id": 1, "url": "https://example.org/page", "title": "t", "accessed": "2026-10-06",
         "extracted": True},
    ]}), encoding="utf-8")
    draft = tmp_path / "draft.md"
    draft.write_text("A claim resting on the extracted page appears here.[1]\n", encoding="utf-8")
    _, rep, _ = run_json(["--draft", draft, "--ledger", ledger], capsys)
    flags = {(f["type"], f["id"]) for f in rep["flags"]}
    assert ("no_quote", 1) in flags
    assert ("snippet_only", 1) not in flags  # explicit extract evidence beats the default


# ---------------------------------------------------------------------------
# Trust scoring
# ---------------------------------------------------------------------------


def test_trust_tiers_scores_and_demotions(tmp_path, capsys):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"version": 1, "sources": [
        {"id": 1, "url": "https://www.nih.gov/a", "title": "t1", "accessed": "d",
         "quotes": [{"text": "q1", "added": "d"}]},
        {"id": 2, "url": "https://www.reuters.com/b", "title": "t2", "accessed": "d",
         "quotes": [{"text": "q2", "added": "d"}]},
        {"id": 3, "url": "https://en.wikipedia.org/wiki/x", "title": "t3", "accessed": "d",
         "quotes": [{"text": "q3", "added": "d"}]},
        {"id": 4, "url": "https://eathealthy365.com/cure", "title": "t4", "accessed": "d",
         "served_by": "keyless_rescue", "backend_error": "timeout"},
        {"id": 5, "url": "https://someone.example.net/blog", "title": "t5", "accessed": "d",
         "quotes": [{"text": "q5", "added": "d"}]},
    ]}), encoding="utf-8")
    draft = tmp_path / "draft.md"
    draft.write_text(
        "One sweeping claim cites every registered source at once.[1][2][3][4][5]\n",
        encoding="utf-8",
    )
    _, rep, _ = run_json(["--draft", draft, "--ledger", ledger], capsys)
    trust = rep["trust"]
    assert trust["1"]["tier"] == "primary" and trust["1"]["score"] == 0.95
    assert trust["2"]["tier"] == "news" and trust["2"]["score"] == 0.80
    assert trust["3"]["tier"] == "aggregator" and trust["3"]["score"] == 0.55
    t4 = trust["4"]
    assert t4["tier"] == "unknown"
    assert t4["score"] < 0.5
    assert set(t4["demotions"]) >= {"served_by", "backend_error", "snippet_only", "unknown_host"}
    # quoted but unknown host: only the unknown-host demotion applies
    assert trust["5"]["demotions"] == ["unknown_host"]
    flags = {(f["type"], f["id"]) for f in rep["flags"]}
    assert ("low_trust", 3) not in flags  # aggregator base 0.55 stays at/above threshold
    assert ("low_trust", 4) in flags and ("low_trust", 5) in flags


def test_trust_json_overrides(capsys):
    base = ["--draft", FIXTURES / "draft_trust.md", "--ledger", FIXTURES / "ledger_trust.json"]
    code, rep, _ = run_json(base, capsys)
    assert code == 0  # low_trust flags are advisory in non-strict mode
    assert {f["id"] for f in rep["flags"] if f["type"] == "low_trust"} == {1, 2}

    code, rep, _ = run_json([*base, "--trust", FIXTURES / "trust.json"], capsys)
    assert rep["trust"]["1"]["tier"] == "primary"
    assert rep["trust"]["1"]["score"] == 0.95
    assert rep["trust"]["2"]["score"] == 0.9  # explicit score override, tier stays table-resolved
    assert rep["trust"]["2"]["tier"] == "unknown"
    assert not any(f["type"] == "low_trust" for f in rep["flags"])
    assert code == 0


def test_malformed_trust_json_is_io_error(tmp_path, capsys):
    trust = tmp_path / "trust.json"
    trust.write_text("{ not json", encoding="utf-8")
    code = fc.main([*map(str, ok_args()), "--trust", str(trust)])
    assert code == 2


# ---------------------------------------------------------------------------
# Coverage math + exit codes
# ---------------------------------------------------------------------------


def test_coverage_counts_unverified_and_gates_on_min(capsys):
    code, rep, _ = run_json(
        ["--draft", FIXTURES / "draft_cov.md", "--ledger", FIXTURES / "ledger_ok.json"], capsys
    )
    assert code == 0
    assert rep["stats"]["sentences"] == 4
    assert rep["stats"]["cited_sentences"] == 1
    assert rep["stats"]["coverage"] == 0.5  # cited ∪ [unverified] per sources.py

    code, rep, _ = run_json(
        ["--draft", FIXTURES / "draft_cov.md", "--ledger", FIXTURES / "ledger_ok.json",
         "--min-coverage", "0.75"], capsys
    )
    assert code == 1
    assert rep["summary"]["pass"] is False


def test_strict_turns_any_flag_into_failure(capsys):
    base = ["--draft", FIXTURES / "draft_strict.md", "--ledger", FIXTURES / "ledger_bad.json"]
    code, rep, _ = run_json(base, capsys)
    assert code == 0                       # flags don't fail a non-strict run
    assert rep["summary"]["flags_n"] > 0
    code, rep, _ = run_json([*base, "--strict"], capsys)
    assert code == 1
    assert rep["summary"]["pass"] is False


def test_empty_draft_is_neutral(tmp_path, capsys):
    draft = tmp_path / "empty.md"
    draft.write_text("\n", encoding="utf-8")
    code, rep, _ = run_json(["--draft", draft, "--ledger", FIXTURES / "ledger_ok.json"], capsys)
    assert code == 0
    assert rep["stats"]["sentences"] == 0
    assert rep["stats"]["coverage"] == 0.0
    assert rep["summary"]["pass"] is True


# ---------------------------------------------------------------------------
# Judge modes
# ---------------------------------------------------------------------------


def test_judge_fixture_verdicts_and_summary(capsys):
    code, rep, _ = run_json(
        [*bad_args(), "--judge", "fixture", "--judge-fixture", FIXTURES / "judge_bad.json"], capsys
    )
    assert rep["judge"] == {"mode": "fixture", "status": "ok", "verdicts_n": 3}
    verdicts = {c["sentence"]: c["verdict"] for c in rep["claims"]}
    assert verdicts == {0: "unsupported", 1: "supported", 2: "conflicting"}
    assert rep["claims"][0]["note"] == "cure claim absent from the source"
    assert rep["summary"]["unsupported_n"] == 1
    assert rep["summary"]["conflicting_n"] == 1
    assert code == 1  # missing_id still drives exit; verdicts never do


def test_judge_fixture_multi_id_claim_conflicting(capsys):
    code, rep, _ = run_json(
        ["--draft", FIXTURES / "draft_multi.md", "--ledger", FIXTURES / "ledger_ok.json",
         "--judge", "fixture", "--judge-fixture", FIXTURES / "judge_multi.json"], capsys
    )
    assert code == 0  # conflicting verdict is advisory — mechanical checks all pass
    assert rep["claims"] == [{
        "sentence": 0, "ids": [1, 2], "verdict": "conflicting",
        "quote": "1984 / 1985", "note": "the two sources disagree on the year",
    }]
    assert rep["summary"]["conflicting_n"] == 1


def test_judge_aux_success_makes_one_batched_call(monkeypatch, capsys):
    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return object()  # opaque response; extract_content_or_reasoning handles it

    fake_aux = types.SimpleNamespace(
        call_llm=fake_call_llm,
        extract_content_or_reasoning=lambda _resp: json.dumps({"claims": [
            {"sentence": 0, "ids": [1], "verdict": "unsupported", "quote": "q", "note": "n"},
        ]}),
    )
    monkeypatch.setattr(fc, "_load_aux", lambda: fake_aux)
    code, rep, _ = run_json([*ok_args(), "--judge", "aux"], capsys)
    assert code == 0
    assert rep["judge"] == {"mode": "aux", "status": "ok", "verdicts_n": 1}
    assert rep["claims"][0]["verdict"] == "unsupported"
    assert captured["task"] == "verify"
    assert captured["max_tokens"] <= 1500
    assert len(captured["messages"]) == 2  # one system + one user message, a single call


def test_judge_aux_import_failure_degrades(monkeypatch, capsys):
    def boom():
        raise ImportError("no agent.auxiliary_client on sys.path")

    monkeypatch.setattr(fc, "_load_aux", boom)
    code, rep, out = run_json([*ok_args(), "--judge", "aux"], capsys)
    assert rep["judge"] == {"mode": "aux", "status": "unavailable", "verdicts_n": 0}
    assert all(c["verdict"] == "not_judged" for c in rep["claims"])
    assert code == 0  # mechanical pass unchanged by judge failure
    assert "unavailable" in out.err


def test_judge_aux_call_failure_degrades(monkeypatch, capsys):
    def fail(**_kwargs):
        raise RuntimeError("aux route rate-limited")

    fake_aux = types.SimpleNamespace(call_llm=fail, extract_content_or_reasoning=str)
    monkeypatch.setattr(fc, "_load_aux", lambda: fake_aux)
    code, rep, _ = run_json([*ok_args(), "--judge", "aux"], capsys)
    assert rep["judge"]["status"] == "unavailable"
    assert code == 0


# ---------------------------------------------------------------------------
# IO / usage errors → exit 2
# ---------------------------------------------------------------------------


def test_io_errors_exit_2(tmp_path, capsys):
    # missing draft
    assert fc.main(["--draft", str(tmp_path / "nope.md"), "--ledger", str(FIXTURES / "ledger_ok.json")]) == 2
    # missing ledger
    assert fc.main(["--draft", str(FIXTURES / "draft_ok.md"), "--ledger", str(tmp_path / "nope.json")]) == 2
    # malformed ledger JSON
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert fc.main(["--draft", str(FIXTURES / "draft_ok.md"), "--ledger", str(bad)]) == 2
    capsys.readouterr()


def test_judge_fixture_io_errors_exit_2(tmp_path, capsys):
    # missing fixture file
    code = fc.main([*map(str, ok_args()), "--judge", "fixture",
                    "--judge-fixture", str(tmp_path / "nope.json")])
    assert code == 2
    # malformed fixture JSON
    bad = tmp_path / "judge.json"
    bad.write_text("not json", encoding="utf-8")
    code = fc.main([*map(str, ok_args()), "--judge", "fixture", "--judge-fixture", str(bad)])
    assert code == 2
    capsys.readouterr()


def test_fixture_mode_without_fixture_path_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        fc.main([*map(str, ok_args()), "--judge", "fixture"])
    assert exc.value.code == 2
    capsys.readouterr()


# ---------------------------------------------------------------------------
# Output routing
# ---------------------------------------------------------------------------


def test_out_writes_report_and_quiets_stdout(tmp_path, capsys):
    out_json = tmp_path / "report.json"
    code = fc.main([*map(str, ok_args()), "--json", "--out", str(out_json)])
    assert code == 0
    out = capsys.readouterr()
    assert out.out == ""
    rep = json.loads(out_json.read_text(encoding="utf-8"))
    assert rep["schema"] == "fact_check.v1"

    out_txt = tmp_path / "report.txt"
    code = fc.main([*map(str, ok_args()), "--out", str(out_txt)])
    assert code == 0
    assert "PASS" in out_txt.read_text(encoding="utf-8")


def test_text_report_on_stdout_by_default(capsys):
    code = fc.main(list(map(str, ok_args())))
    out = capsys.readouterr()
    assert code == 0
    assert "coverage" in out.out
    assert "PASS" in out.out
