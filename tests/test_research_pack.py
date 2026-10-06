"""Tests for research_pack.py — R7-A pack glue (r7-interfaces §2/§3).

Hermetic: read-only inputs under tests/fixtures/research_pack, everything
written under pytest's ``tmp_path``; no network, no sockets. The CLI is
exercised in-process via ``research_pack.main()``; argparse usage errors
surface as ``SystemExit(2)``. The cross-compat test stores a built pack in a
real ``AnswerCache`` over a tmp_path db.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import research_pack
from research_pack import main as rp_main
from searchstore.answer_cache import AnswerCache

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research_pack"
LEDGER_OK = FIXTURES / "ledger_ok.json"
LEDGER_EDGE = FIXTURES / "ledger_edge.json"
TRUST_REPORT = FIXTURES / "trust_report.json"
FC_PASS = FIXTURES / "fact_check_pass.json"
FC_FAIL = FIXTURES / "fact_check_fail.json"
FC_WRONG = FIXTURES / "fact_check_wrong_schema.json"
DRAFT = FIXTURES / "draft.md"
PACK_PASS = FIXTURES / "pack_pass.json"
PACK_REJECT = FIXTURES / "pack_reject.json"

QUERY = "what is vitamin D?"


def _build(tmp_path: Path, *extra: str, ledger: Path = LEDGER_OK, query: str = QUERY):
    """Run ``build`` in-process against --out; return the parsed pack."""
    out = tmp_path / "pack.json"
    rc = rp_main(["build", "--query", query, "--ledger", str(ledger), "--out", str(out), *extra])
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Mapping (r7-interfaces §2)
# ---------------------------------------------------------------------------


def test_build_field_mapping_order_quote_fetched_at(tmp_path):
    pack = _build(tmp_path)
    assert pack["schema"] == "research_pack.v1"
    assert pack["query"] == QUERY
    assert [s["url"] for s in pack["sources"]] == [
        "https://www.nih.gov/vitamin-d-fact-sheet",
        "https://www.reuters.com/health/vitamin-d-deficiency",
        "https://en.wikipedia.org/wiki/Vitamin_D",
    ]
    s0 = pack["sources"][0]
    assert s0["title"] == "Vitamin D Fact Sheet"
    assert s0["quote"] == "vitamin D reduces fracture risk"  # quotes[0].text
    assert s0["fetched_at"] == "2026-10-06"  # ledger "accessed"
    assert s0["provider"] is None  # always null at build time
    s2 = pack["sources"][2]
    assert s2["quote"] is None  # no quotes[] -> null
    assert s2["title"] is None  # empty title -> null


def test_build_defaults(tmp_path):
    pack = _build(tmp_path)
    assert pack["scope"] == ""
    assert pack["mode"] == "deep"
    assert pack["ttl_days"] == 14
    assert pack["answer_markdown"] is None
    assert "verification" not in pack  # publish gate will reject — intended
    assert datetime.fromisoformat(pack["created_at"]).tzinfo is not None


def test_build_answer_scope_mode_ttl(tmp_path):
    pack = _build(tmp_path, "--answer", str(DRAFT), "--scope", "health", "--mode", "fast", "--ttl-days", "30")
    assert pack["answer_markdown"] == DRAFT.read_text(encoding="utf-8")
    assert pack["scope"] == "health"
    assert pack["mode"] == "fast"
    assert pack["ttl_days"] == 30


def test_build_pack_unit_fixed_now():
    pack, warnings = research_pack.build_pack(
        query="q",
        ledger_sources=[{"url": "https://a.example/x", "title": "A", "accessed": "2026-01-01"}],
        now=datetime(2026, 10, 6, tzinfo=UTC),
    )
    assert warnings == []
    assert pack["created_at"] == "2026-10-06T00:00:00+00:00"
    assert pack["sources"][0]["fetched_at"] == "2026-01-01"


# ---------------------------------------------------------------------------
# Trust join (normalized URL, both sides; no host fallback)
# ---------------------------------------------------------------------------


def test_trust_join_exact_normalized_and_miss(tmp_path):
    pack = _build(tmp_path, "--trust-report", str(TRUST_REPORT))
    by_url = {s["url"]: s for s in pack["sources"]}
    # exact URL match
    assert by_url["https://www.nih.gov/vitamin-d-fact-sheet"]["trust_score"] == 0.95
    # report URL carries a trailing slash — normalized join still matches
    assert by_url["https://www.reuters.com/health/vitamin-d-deficiency"]["trust_score"] == 0.7
    # no report entry -> null (no host fallback)
    assert by_url["https://en.wikipedia.org/wiki/Vitamin_D"]["trust_score"] is None


def test_trust_join_fragment_stripped(tmp_path):
    pack = _build(tmp_path, "--trust-report", str(TRUST_REPORT), ledger=LEDGER_EDGE)
    by_url = {s["url"]: s for s in pack["sources"]}
    # ledger URL carries "#top"; the report has it bare -> normalized match
    assert by_url["https://notes.private-journal.example.net/entry#top"]["trust_score"] == 0.25


def test_served_by_precedence(tmp_path):
    pack = _build(tmp_path, "--trust-report", str(TRUST_REPORT), ledger=LEDGER_EDGE)
    by_url = {s["url"]: s for s in pack["sources"]}
    # trust inputs.served_by wins over the ledger's "search-snippet"
    assert by_url["https://www.theguardian.com/science/vitamin-d"]["served_by"] == "rescued_from"
    # trust has served_by null -> falls through to the ledger's "extract"
    assert by_url["https://notes.private-journal.example.net/entry#top"]["served_by"] == "extract"
    # no trust entry at all -> ledger's "cache"
    assert by_url["https://example.org/only-ledger"]["served_by"] == "cache"


def test_served_by_null_when_nowhere(tmp_path):
    pack = _build(tmp_path)
    assert all(s["served_by"] is None for s in pack["sources"])
    assert all(s["trust_score"] is None for s in pack["sources"])  # no --trust-report


# ---------------------------------------------------------------------------
# Skips + verification derivation
# ---------------------------------------------------------------------------


def test_skip_sources_without_url(tmp_path, capsys):
    pack = _build(tmp_path, ledger=LEDGER_EDGE)
    assert len(pack["sources"]) == 3  # 4 ledger entries, one has no url
    assert all(s["url"] for s in pack["sources"])
    err = capsys.readouterr().err
    assert "skipped" in err and "warning" in err


def test_verification_derived_pass(tmp_path):
    pack = _build(tmp_path, "--verification", str(FC_PASS))
    ver = pack["verification"]
    assert ver["fact_check_exit"] == 0  # summary.pass -> 0
    assert ver["coverage"] == 0.75  # stats.coverage
    assert ver["min_coverage"] == 0.5  # --min-coverage default
    assert datetime.fromisoformat(ver["verified_at"]).tzinfo is not None


def test_verification_derived_fail(tmp_path):
    pack = _build(tmp_path, "--verification", str(FC_FAIL), "--min-coverage", "0.7")
    ver = pack["verification"]
    assert ver["fact_check_exit"] == 1
    assert ver["coverage"] == 0.2
    assert ver["min_coverage"] == 0.7


# ---------------------------------------------------------------------------
# Output format + --json summary
# ---------------------------------------------------------------------------


def test_build_writes_indent2_trailing_newline_utf8(tmp_path):
    out = tmp_path / "p.json"
    rc = rp_main(["build", "--query", QUERY, "--ledger", str(LEDGER_OK), "--answer", str(DRAFT), "--out", str(out)])
    assert rc == 0
    raw = out.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw.startswith("{\n  ")  # indent 2
    assert "Nghiên cứu" in raw  # ensure_ascii=False
    json.loads(raw)


def test_build_stdout_emits_pack_json(tmp_path, capsys):
    rc = rp_main(["build", "--query", "q", "--ledger", str(LEDGER_OK), "--stdout"])
    assert rc == 0
    pack = json.loads(capsys.readouterr().out)
    assert pack["schema"] == "research_pack.v1"
    assert len(pack["sources"]) == 3


def test_build_json_summary(tmp_path, capsys):
    out = tmp_path / "p.json"
    rc = rp_main(
        [
            "build",
            "--query",
            "q",
            "--ledger",
            str(LEDGER_EDGE),
            "--trust-report",
            str(TRUST_REPORT),
            "--verification",
            str(FC_PASS),
            "--out",
            str(out),
            "--json",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "out": str(out),
        "schema": "research_pack.v1",
        "query": "q",
        "mode": "deep",
        "sources_n": 3,
        "skipped_n": 1,
        "trust_scored_n": 2,
        "verified": True,
    }


def test_build_stdout_json_summary_goes_to_stderr(tmp_path, capsys):
    rc = rp_main(["build", "--query", "q", "--ledger", str(LEDGER_OK), "--stdout", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    pack = json.loads(captured.out)  # stdout stays a clean pack
    assert pack["schema"] == "research_pack.v1"
    summary = json.loads(captured.err.strip().splitlines()[-1])
    assert summary["stdout"] is True


# ---------------------------------------------------------------------------
# Exit-2 matrix
# ---------------------------------------------------------------------------


def test_exit2_missing_ledger(tmp_path):
    rc = rp_main(["build", "--query", "q", "--ledger", str(tmp_path / "nope.json"), "--out", str(tmp_path / "p.json")])
    assert rc == 2


def test_exit2_malformed_ledger(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc = rp_main(["build", "--query", "q", "--ledger", str(bad), "--out", str(tmp_path / "p.json")])
    assert rc == 2


def test_exit2_ledger_wrong_shape(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1}), encoding="utf-8")  # no sources list
    rc = rp_main(["build", "--query", "q", "--ledger", str(bad), "--out", str(tmp_path / "p.json")])
    assert rc == 2


def test_exit2_wrong_verification_schema(tmp_path):
    rc = rp_main(
        [
            "build",
            "--query",
            "q",
            "--ledger",
            str(LEDGER_OK),
            "--verification",
            str(FC_WRONG),
            "--out",
            str(tmp_path / "p.json"),
        ]
    )
    assert rc == 2


def test_exit2_missing_answer_file(tmp_path):
    rc = rp_main(
        [
            "build",
            "--query",
            "q",
            "--ledger",
            str(LEDGER_OK),
            "--answer",
            str(tmp_path / "no.md"),
            "--out",
            str(tmp_path / "p.json"),
        ]
    )
    assert rc == 2


def test_exit2_bad_mode(tmp_path):
    with pytest.raises(SystemExit) as exc:
        rp_main(
            ["build", "--query", "q", "--ledger", str(LEDGER_OK), "--mode", "slow", "--out", str(tmp_path / "p.json")]
        )
    assert exc.value.code == 2


def test_exit2_neither_out_nor_stdout(tmp_path):
    with pytest.raises(SystemExit) as exc:
        rp_main(["build", "--query", "q", "--ledger", str(LEDGER_OK)])
    assert exc.value.code == 2


def test_exit2_both_out_and_stdout(tmp_path):
    with pytest.raises(SystemExit) as exc:
        rp_main(["build", "--query", "q", "--ledger", str(LEDGER_OK), "--out", str(tmp_path / "p.json"), "--stdout"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


def test_info_pass(tmp_path, capsys):
    rc = rp_main(["info", str(PACK_PASS)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "what is vitamin D?" in out
    assert "put-gate:" in out and "PASS" in out


def test_info_reject_unverified(tmp_path, capsys):
    rc = rp_main(["info", str(PACK_REJECT)])
    assert rc == 0  # parseable pack — verdict is in the output, not the exit code
    out = capsys.readouterr().out
    assert "REJECT" in out
    assert "not verified" in out


def test_info_reject_missing_keys(tmp_path, capsys):
    bad = tmp_path / "halfpack.json"
    bad.write_text(json.dumps({"schema": "research_pack.v1", "query": "q"}), encoding="utf-8")
    rc = rp_main(["info", str(bad)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "REJECT" in out and "missing required key" in out


def test_info_json(tmp_path, capsys):
    rc = rp_main(["info", str(PACK_PASS), "--json"])
    assert rc == 0
    info = json.loads(capsys.readouterr().out)
    assert info["put_gate"] == "PASS"
    assert info["reason"] is None
    assert info["sources_n"] == 2
    assert info["trust_scored_n"] == 2
    assert info["trust_mean"] == round((0.95 + 0.7) / 2, 2)  # 0.82 — 0.825 floats low
    assert info["verified"] is True
    assert info["ttl_days"] == 14


def test_info_malformed_and_missing_exit2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("[unclosed", encoding="utf-8")
    assert rp_main(["info", str(bad)]) == 2
    assert rp_main(["info", str(tmp_path / "nope.json")]) == 2
    assert "error" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Cross-compat: a built pack is accepted by AnswerCache.put (no --force) and
# get() round-trips trust_score
# ---------------------------------------------------------------------------


def test_cross_compat_answer_cache_put_get_roundtrip(tmp_path):
    pack = _build(tmp_path, "--trust-report", str(TRUST_REPORT), "--verification", str(FC_PASS), "--answer", str(DRAFT))
    cache = AnswerCache(tmp_path / "answers.db")
    try:
        res = cache.put(pack)  # must pass the verification gate unforced
        assert res["replaced"] is False
        hit = cache.get(QUERY)
        assert hit is not None and hit["fresh"]
        got = hit["pack"]
        assert got["schema"] == "research_pack.v1"
        assert got["answer_markdown"] == DRAFT.read_text(encoding="utf-8")
        assert got["verification"]["fact_check_exit"] == 0
        scores = {s["url"]: s["trust_score"] for s in got["sources"]}
        assert scores["https://www.nih.gov/vitamin-d-fact-sheet"] == 0.95
        assert scores["https://www.reuters.com/health/vitamin-d-deficiency"] == 0.7
        assert scores["https://en.wikipedia.org/wiki/Vitamin_D"] is None
    finally:
        cache.close()


def test_cross_compat_unverified_pack_rejected_by_cache(tmp_path):
    from searchstore.answer_cache import PackError

    pack = _build(tmp_path)  # no --verification -> gate must reject
    cache = AnswerCache(tmp_path / "answers.db")
    try:
        with pytest.raises(PackError):
            cache.put(pack)
    finally:
        cache.close()
