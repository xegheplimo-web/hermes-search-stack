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
