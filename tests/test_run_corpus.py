"""Hermetic tests for ``evals/r9/run_corpus.py`` (R10-C corpus runner).

Fully offline: a tiny local HTTP stub server stands in for the gateway
(``POST /v1/chat/completions``). No sleeps (``--sleep 1``), no network
beyond localhost.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import scripts.scoreboard as scoreboard
from evals.r9 import run_corpus

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = REPO_ROOT / "evals" / "r9" / "corpus_v0.jsonl"
SPLITS_PATH = REPO_ROOT / "evals" / "r9" / "splits.json"

STUB_ANSWER = (
    "Mức phạt theo Nghị định 168/2024 còn hiệu lực, xem điều 5 khoản 2 [1]. "
    "Cập nhật ngày 02/10/2026, giá tham khảo 21.500 đồng/lít.\n"
    "\n"
    "## Sources\n"
    "\n"
    "[1] Thư viện pháp luật — https://thuvienphapluat.vn/van-ban/test\n"
)


def _holdout_ids() -> set[str]:
    return run_corpus.load_holdout_ids(SPLITS_PATH)


# ─── split assignment ────────────────────────────────────────────────────


def test_holdout_rule_matches_frozen_ids():
    assert _holdout_ids() == {"vn-005", "vn-015", "vn-025", "vn-035", "vn-045"}


def test_assign_split_frozen_rule():
    holdout = _holdout_ids()
    assert run_corpus.assign_split({"id": "vn-005", "difficulty": "easy"}, holdout) == "holdout"
    assert run_corpus.assign_split({"id": "vn-015", "difficulty": "hard"}, holdout) == "holdout"
    assert run_corpus.assign_split({"id": "vn-036", "difficulty": "hard"}, holdout) == "challenge"
    assert run_corpus.assign_split({"id": "vn-001", "difficulty": "easy"}, holdout) == "regression"
    assert run_corpus.assign_split({"id": "vn-016", "difficulty": "medium"}, holdout) == "regression"


def test_corpus_splits_cover_all_cases_without_overlap():
    corpus = run_corpus.load_corpus(CORPUS_PATH)
    assert len(corpus) == 50
    holdout = _holdout_ids()
    groups: dict[str, list] = {"holdout": [], "challenge": [], "regression": []}
    for case in corpus:
        groups[run_corpus.assign_split(case, holdout)].append(case["id"])
    assert len(groups["holdout"]) == 5
    assert all(
        run_corpus.assign_split(c, holdout) != "holdout"
        for c in corpus
        if c["difficulty"] == "hard" and c["id"] not in holdout_ids_frozen()
    )
    assert sum(len(v) for v in groups.values()) == 50
    # challenge = hard minus holdout
    hard_ids = {c["id"] for c in corpus if c["difficulty"] == "hard"}
    assert set(groups["challenge"]) == hard_ids - set(groups["holdout"])


def holdout_ids_frozen() -> set[str]:
    return {"vn-005", "vn-015", "vn-025", "vn-035", "vn-045"}


# ─── citations / sources parsing ─────────────────────────────────────────


def test_parse_sources_block():
    sources = run_corpus.parse_sources(STUB_ANSWER)
    assert len(sources) == 1
    assert sources[0]["domain"] == "thuvienphapluat.vn"
    assert sources[0]["url"].startswith("https://")


def test_parse_sources_absent_without_section():
    assert run_corpus.parse_sources("plain answer [1] with no section") == []


def test_citations_and_section_signals():
    assert run_corpus.CITATION_RE.search(STUB_ANSWER)
    assert run_corpus.SOURCES_SECTION_RE.search(STUB_ANSWER)


# ─── check logic ─────────────────────────────────────────────────────────


def _case(**overrides) -> dict:
    base = {
        "id": "vn-001",
        "difficulty": "easy",
        "domain": "law",
        "query": "q",
        "variants": [],
        "expected": {"must_include": [], "must_not_include": [], "required_fields": []},
        "ground_truth": {"status": "stable", "source": "x", "checked_at": None},
    }
    base.update(overrides)
    return base


def test_must_include_match_is_not_pending():
    case = _case(
        expected={
            "must_include": ["nghị định 168/2024"],
            "must_not_include": [],
            "required_fields": [],
        }
    )
    verdict = run_corpus.evaluate_case(case, STUB_ANSWER)
    assert verdict["signals"]["must_include_matched"] == 1
    assert verdict["judge_pending"] == 0
    assert verdict["pass"] is True


def test_must_include_unmatched_is_judge_pending_not_fail():
    case = _case(
        expected={
            "must_include": ["một kỳ vọng ngữ nghĩa không bao giờ khớp nguyên văn xyz"],
            "must_not_include": [],
            "required_fields": [],
        }
    )
    verdict = run_corpus.evaluate_case(case, STUB_ANSWER)
    assert verdict["judge_pending"] == 1
    assert verdict["pass"] is True  # pending never fails


def test_must_not_include_hit_fails():
    case = _case(
        expected={
            "must_include": [],
            "must_not_include": ["nghị định 168/2024"],
            "required_fields": [],
        }
    )
    verdict = run_corpus.evaluate_case(case, STUB_ANSWER)
    assert verdict["pass"] is False


def test_required_field_missing_fails_and_unknown_is_pending():
    case = _case(
        expected={
            "must_include": [],
            "must_not_include": [],
            "required_fields": ["citations", "address", "some_future_field"],
        }
    )
    verdict = run_corpus.evaluate_case(case, "plain text without markers")
    assert verdict["signals"]["required_fields"]["citations"] == "missing"
    assert verdict["signals"]["required_fields"]["some_future_field"] == "judge-pending"
    assert verdict["pass"] is False


def test_diacritics_preserved_in_normalization():
    assert "đ" in run_corpus.normalize("ĐÈN ĐỎ")
    assert run_corpus.normalize("  Mức   phạt\nvượt  ") == "mức phạt vượt"


def test_dynamic_without_ttl_is_not_stale():
    case = _case(ground_truth={"status": "dynamic", "source": "x", "checked_at": None})
    verdict = run_corpus.evaluate_case(case, STUB_ANSWER)
    assert verdict["stale"] is False
    assert "no ground truth yet" in verdict["signals"]["stale_note"]
    assert verdict["signals"]["freshness_as_of_present"] is True


def test_dynamic_with_ttl_and_null_checked_at_is_stale_not_fail():
    case = _case(
        ground_truth={"status": "dynamic", "source": "x", "checked_at": None},
        freshness={"ttl_days": 1},
        expected={"must_include": [], "must_not_include": [], "required_fields": []},
    )
    verdict = run_corpus.evaluate_case(case, STUB_ANSWER)
    assert verdict["stale"] is True
    assert verdict["pass"] is True  # stale never fails


# ─── stub gateway server ─────────────────────────────────────────────────


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": STUB_ANSWER}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence the test server
        pass


@pytest.fixture()
def stub_gateway():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_live_run_against_stub_gateway(stub_gateway, tmp_path):
    rc = run_corpus.main(
        [
            "--gateway",
            stub_gateway,
            "--out-dir",
            str(tmp_path),
            "--ids",
            "vn-001,vn-002",
            "--sleep",
            "1",
        ]
    )
    assert rc == 0
    payloads = list(tmp_path.glob("r10_corpus_*.json"))
    assert len(payloads) == 1
    payload = json.loads(payloads[0].read_text(encoding="utf-8"))
    assert payload["totals"]["total"] == 2
    assert payload["live_calls"] == 2
    assert set(payload["totals"]) == {"pass", "fail", "total", "elapsed_s"}
    case = payload["cases"][0]
    assert case["kind"] == "corpus"
    assert set(case) >= {"id", "kind", "input", "pass", "latency_s", "notes", "error"}
    assert "signals" in case and "judge_pending" in case
    assert (tmp_path / (payloads[0].stem + ".md")).is_file()


def test_dry_run_writes_battery_schema(tmp_path):
    rc = run_corpus.main(["--dry-run", "--out-dir", str(tmp_path), "--limit", "2", "--sleep", "1"])
    assert rc == 0
    payload = json.loads(next(tmp_path.glob("r10_corpus_*.json")).read_text(encoding="utf-8"))
    assert payload["live_calls"] == 0
    assert payload["totals"]["total"] == 2
    assert payload["cases"][0]["kind"] == "corpus"


def test_scoreboard_ingests_corpus_output(tmp_path):
    run_corpus.main(["--dry-run", "--out-dir", str(tmp_path), "--limit", "2", "--sleep", "1"])
    (tmp_path / "battery_probe.json").write_text(
        json.dumps(
            {
                "generated": "2026-10-07T00:00:00",
                "live_calls": 0,
                "cases": [
                    {
                        "id": "S1",
                        "kind": "search",
                        "pass": True,
                        "latency_s": 1.0,
                        "n_results": 3,
                        "backends": [],
                    }
                ],
                "totals": {"pass": 1, "fail": 0, "total": 1, "elapsed_s": 1.0},
            }
        ),
        encoding="utf-8",
    )
    batteries, _keyless = scoreboard.load_runs(tmp_path, 5)
    files = [r["file"] for r in batteries]
    assert any(f.startswith("r10_corpus_") for f in files)
    assert "battery_probe.json" in files
    corpus_run = next(r for r in batteries if r["file"].startswith("r10_corpus_"))
    assert corpus_run["total"] == 2
    assert "corpus" in corpus_run["kinds"]


# ─── R15-A corpus_v1 validity ────────────────────────────────────────────

CORPUS_V1_PATH = REPO_ROOT / "evals" / "r9" / "corpus_v1.jsonl"

ALLOWED_SEVERITY = {"S0", "S1", "S2", "S3"}
ALLOWED_GTS = {"official", "human-reviewed", "key", "derived"}


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_corpus_v1_has_68_lines_unique_ids():
    rows = _load_jsonl(CORPUS_V1_PATH)
    assert len(rows) == 68
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == 68


def test_corpus_v1_preserves_v0_queries_and_must_include():
    v0 = {r["id"]: r for r in _load_jsonl(CORPUS_PATH)}
    v1 = {r["id"]: r for r in _load_jsonl(CORPUS_V1_PATH)}
    assert len(v0) == 50
    for cid, old in v0.items():
        assert cid in v1, f"{cid} missing from corpus_v1"
        new = v1[cid]
        assert new["query"] == old["query"], f"{cid} query regressed"
        assert new["expected"]["must_include"] == old["expected"]["must_include"], f"{cid} must_include regressed"


def test_corpus_v1_new_cases_have_severity_and_ground_truth_source():
    v1 = {r["id"]: r for r in _load_jsonl(CORPUS_V1_PATH)}
    new_ids = [f"vn-{n:03d}" for n in range(51, 69)]
    for cid in new_ids:
        assert cid in v1, f"{cid} missing from corpus_v1"
        case = v1[cid]
        assert case.get("severity") in ALLOWED_SEVERITY, f"{cid} bad severity"
        assert case.get("ground_truth_source") in ALLOWED_GTS, f"{cid} bad ground_truth_source"


def test_corpus_v1_ground_truth_source_never_model():
    rows = _load_jsonl(CORPUS_V1_PATH)
    for row in rows:
        assert str(row.get("ground_truth_source", "")).lower() not in {"model", "llm"}, row["id"]


def test_corpus_v1_holdout_excluded_from_default_splits():
    corpus = run_corpus.load_corpus(CORPUS_V1_PATH)
    holdout = _holdout_ids()
    selected = run_corpus.select_cases(corpus, holdout, ["regression", "challenge"], False, None, None, False)
    selected_ids = {case["id"] for case, _, _ in selected}
    for cid in selected_ids:
        assert int(cid[3:]) % 10 != 5, f"holdout {cid} leaked into default splits"
    assert not selected_ids & holdout


# ─── R15-A severity-weighted pass rate ───────────────────────────────────


def _swpr_case(severity: str, passed: bool) -> dict:
    return {"id": f"t-{severity}-{passed}", "severity": severity, "pass": passed}


def test_severity_weighted_pass_rate_mixed_math():
    cases = [
        _swpr_case("S0", True),  # 4.0 pass
        _swpr_case("S0", False),  # 4.0 fail
        _swpr_case("S1", True),  # 2.0 pass
        _swpr_case("S2", True),  # 1.0 pass
        _swpr_case("S3", False),  # 0.5 fail
    ]
    # weighted pass = 4 + 2 + 1 = 7; total = 4+4+2+1+0.5 = 11.5
    assert run_corpus.severity_weighted_pass_rate(cases) == pytest.approx(7.0 / 11.5)


def test_severity_weighted_pass_rate_weights():
    assert run_corpus.SEVERITY_WEIGHTS == {"S0": 4.0, "S1": 2.0, "S2": 1.0, "S3": 0.5}
    assert run_corpus.severity_weighted_pass_rate([_swpr_case("S0", True)]) == pytest.approx(1.0)
    assert run_corpus.severity_weighted_pass_rate([_swpr_case("S3", False)]) == pytest.approx(0.0)


def test_severity_weighted_pass_rate_empty_is_none():
    assert run_corpus.severity_weighted_pass_rate([]) is None


# ─── R15-A p95 (nearest-rank, not p90) ───────────────────────────────────


def test_p95_latency_nearest_rank_not_p90():
    values = [float(v) for v in range(1, 21)]  # 1..20: p90 -> 18, p95 -> 19
    assert run_corpus._pct(values, 90) == pytest.approx(18.0)
    assert run_corpus._pct(values, 95) == pytest.approx(19.0)


def test_dry_run_v1_emits_v2_aggregates(tmp_path):
    rc = run_corpus.main(
        ["--dry-run", "--corpus", str(CORPUS_V1_PATH), "--out-dir", str(tmp_path), "--limit", "4", "--sleep", "1"]
    )
    assert rc == 0
    payloads = list(tmp_path.glob("r15_corpus_*.json"))
    assert len(payloads) == 1
    payload = json.loads(payloads[0].read_text(encoding="utf-8"))
    assert set(payload["aggregates"]) == {
        "severity_weighted_pass_rate",
        "p50_latency_s",
        "p95_latency_s",
        "variant_consistency_avg",
    }
    for case in payload["cases"]:
        assert "severity" in case and "variant_consistency" in case


# ─── R15-A variant consistency ───────────────────────────────────────────


def test_variant_consistency_avg_mean_over_variant_cases_only():
    cases = [
        {"id": "vn-001:main", "base_id": "vn-001", "pass": True, "variant_consistency": 0.5, "has_variants": True},
        {
            "id": "vn-001:variant-0",
            "base_id": "vn-001",
            "pass": False,
            "variant_consistency": 0.5,
            "has_variants": True,
        },
        {"id": "vn-002", "base_id": "vn-002", "pass": True, "variant_consistency": 1.0, "has_variants": False},
    ]
    assert run_corpus.variant_consistency_avg(cases) == pytest.approx(0.5)


def test_variant_consistency_avg_none_without_variants():
    cases = [{"id": "vn-002", "base_id": "vn-002", "pass": True, "variant_consistency": 1.0, "has_variants": False}]
    assert run_corpus.variant_consistency_avg(cases) is None
    assert run_corpus.variant_consistency_avg([]) is None


def test_dry_run_variant_probes_share_passed_over_probes(tmp_path):
    corpus_path = tmp_path / "mini.jsonl"
    corpus_path.write_text(
        json.dumps(
            {
                "id": "vn-101",
                "difficulty": "easy",
                "domain": "law",
                "query": "q main",
                "variants": ["q v1", "q v2"],
                "expected": {"must_include": [], "must_not_include": [], "required_fields": []},
                "ground_truth": {"status": "stable", "source": "x", "checked_at": None},
                "severity": "S1",
                "ground_truth_source": "key",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = run_corpus.main(
        [
            "--dry-run",
            "--corpus",
            str(corpus_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--variants",
            "--split",
            "regression",
            "--sleep",
            "1",
        ]
    )
    assert rc == 0
    payload = json.loads(next((tmp_path / "out").glob("r15_corpus_*.json")).read_text(encoding="utf-8"))
    assert len(payload["cases"]) == 3  # main + 2 variants
    for case in payload["cases"]:
        assert case["variant_consistency"] == pytest.approx(1.0)  # 3 passed / 3 probes
    assert payload["aggregates"]["variant_consistency_avg"] == pytest.approx(1.0)
