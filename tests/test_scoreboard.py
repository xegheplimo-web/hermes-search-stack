"""Hermetic tests for ``scripts/scoreboard.py`` (R6-D quality/speed scoreboard).

Fully offline: no network, no sleeps. Fixture battery/keyless files live in
``tests/fixtures/scoreboard/``; CLI coverage goes through ``main(argv)``
so nothing shells out. One fixture (``battery_malformed.json``) is
intentionally broken JSON to exercise the warn-and-skip path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import scripts.scoreboard as scoreboard

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "scoreboard"


# ─── nearest-rank percentile math ────────────────────────────────────────────


def test_nearest_rank_acceptance_example():
    # [1,2,3,4] -> p50 = 2 (rank ceil(0.5*4) = 2), p90 = 4 (rank ceil(0.9*4) = 4).
    assert scoreboard.nearest_rank_percentile([1, 2, 3, 4], 50) == 2
    assert scoreboard.nearest_rank_percentile([1, 2, 3, 4], 90) == 4


def test_nearest_rank_single_value():
    assert scoreboard.nearest_rank_percentile([5.5], 50) == 5.5
    assert scoreboard.nearest_rank_percentile([5.5], 90) == 5.5


def test_nearest_rank_empty_returns_none():
    assert scoreboard.nearest_rank_percentile([], 50) is None
    assert scoreboard.nearest_rank_percentile([], 90) is None


def test_nearest_rank_unsorted_input_with_duplicates():
    values = [5, 1, 9, 3, 3]  # sorted: [1, 3, 3, 5, 9]
    assert scoreboard.nearest_rank_percentile(values, 50) == 3  # rank ceil(2.5) = 3
    assert scoreboard.nearest_rank_percentile(values, 90) == 9  # rank ceil(4.5) = 5


# ─── battery parsing ─────────────────────────────────────────────────────────


def test_parse_battery_run_matches_declared_totals():
    run = scoreboard.parse_battery_run(FIXTURES / "battery_alpha.json")
    assert run["file"] == "battery_alpha.json"
    assert run["generated"] == "2026-10-01T00:00:00"
    assert run["live_calls"] == 6
    assert (run["pass"], run["fail"], run["total"]) == (7, 0, 7)
    assert run["elapsed_s"] == 12.5


def test_parse_battery_run_kind_latency_stats():
    run = scoreboard.parse_battery_run(FIXTURES / "battery_alpha.json")
    search = run["kinds"]["search"]
    assert search["n"] == 4
    assert search["p50"] == 2.0
    assert search["p90"] == 4.0
    assert search["mean_n_results"] == 4.5
    extract = run["kinds"]["extract"]
    assert extract["n"] == 3
    assert extract["p50"] == 1.5  # rank ceil(0.5*3) = 2 -> second value
    assert extract["p90"] == 2.5  # rank ceil(0.9*3) = 3 -> third value
    assert "mean_n_results" not in extract


def test_parse_battery_run_backends_union_sorted():
    run = scoreboard.parse_battery_run(FIXTURES / "battery_alpha.json")
    assert run["backends"] == [
        "firecrawl",
        "keenable",
        "keyless",
        "managed",
        "perplexity",
        "searxng",
    ]


def test_parse_battery_run_counts_failures():
    run = scoreboard.parse_battery_run(FIXTURES / "battery_beta.json")
    assert (run["pass"], run["fail"], run["total"]) == (2, 1, 3)
    assert run["kinds"]["search"]["p50"] == 2.0  # rank ceil(0.5*2) = 1
    assert run["kinds"]["search"]["p90"] == 3.0  # rank ceil(0.9*2) = 2
    assert run["kinds"]["search"]["mean_n_results"] == 2.0
    assert run["kinds"]["extract"]["p50"] == 1.0


def test_parse_battery_run_recomputes_missing_totals(tmp_path):
    payload = {
        "generated": "2026-10-03T00:00:00",
        "live_calls": 2,
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
    }
    path = tmp_path / "battery_x.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    run = scoreboard.parse_battery_run(path)
    assert (run["pass"], run["fail"], run["total"]) == (1, 0, 1)
    assert run["elapsed_s"] is None


def test_parse_battery_run_totals_mismatch_warns_and_recomputes(tmp_path, capsys):
    payload = {
        "generated": "2026-10-03T00:00:00",
        "live_calls": 2,
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
        "totals": {"pass": 5, "fail": 5, "total": 10, "elapsed_s": 99.0},
    }
    path = tmp_path / "battery_x.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    run = scoreboard.parse_battery_run(path)
    assert (run["pass"], run["fail"], run["total"]) == (1, 0, 1)
    assert "disagree" in capsys.readouterr().err


def test_parse_battery_run_rejects_wrong_schema(tmp_path):
    path = tmp_path / "battery_bad.json"
    path.write_text(json.dumps({"generated": "2026-10-03T00:00:00"}), encoding="utf-8")
    with pytest.raises(ValueError, match="cases"):
        scoreboard.parse_battery_run(path)


# ─── keyless parsing ─────────────────────────────────────────────────────────


def test_parse_keyless_run():
    run = scoreboard.parse_keyless_run(FIXTURES / "keyless_alpha.json")
    assert run["file"] == "keyless_alpha.json"
    assert run["generated"] == "2026-10-01T01:00:00"
    assert run["live_calls"] == 4
    assert (run["pass"], run["fail"], run["total"]) == (2, 0, 2)


def test_parse_keyless_run_with_failure():
    run = scoreboard.parse_keyless_run(FIXTURES / "keyless_beta.json")
    assert run["live_calls"] == 6
    assert (run["pass"], run["fail"], run["total"]) == (2, 1, 3)


# ─── discovery + malformed tolerance ─────────────────────────────────────────


def test_discover_runs_newest_last_and_limit():
    batteries, keyless = scoreboard.discover_runs(FIXTURES, 5)
    assert [p.name for p in batteries] == [
        "battery_alpha.json",
        "battery_beta.json",
        "battery_malformed.json",
    ]
    assert [p.name for p in keyless] == ["keyless_alpha.json", "keyless_beta.json"]
    batteries, _ = scoreboard.discover_runs(FIXTURES, 1)
    assert [p.name for p in batteries] == ["battery_malformed.json"]


def test_load_runs_skips_malformed_with_warning(capsys):
    batteries, keyless = scoreboard.load_runs(FIXTURES, 5)
    assert [r["file"] for r in batteries] == ["battery_alpha.json", "battery_beta.json"]
    assert [r["file"] for r in keyless] == ["keyless_alpha.json", "keyless_beta.json"]
    assert "battery_malformed.json" in capsys.readouterr().err


# ─── scoreboard build + trend ────────────────────────────────────────────────


def test_build_scoreboard_structure():
    sb = scoreboard.build_scoreboard(FIXTURES, 5)
    assert set(sb) == {"generated_at", "runs", "keyless", "latest", "trend"}
    assert sb["latest"]["file"] == "battery_beta.json"
    assert len(sb["runs"]) == 2
    assert len(sb["keyless"]) == 2
    assert sb["trend"]["previous"] is None
    assert "no previous" in sb["trend"]["note"]


def test_trend_against_previous_scoreboard(tmp_path):
    for name in ("battery_alpha.json", "battery_beta.json"):
        shutil.copy(FIXTURES / name, tmp_path / name)
    previous = {
        "generated_at": "2026-10-01T12:00:00+00:00",
        "latest": {
            "file": "battery_alpha.json",
            "generated": "2026-10-01T00:00:00",
            "live_calls": 6,
            "pass": 7,
            "fail": 0,
            "total": 7,
            "kinds": {"search": {"p50": 2.0}, "extract": {"p50": 1.5}},
        },
    }
    (tmp_path / "scoreboard.json").write_text(json.dumps(previous), encoding="utf-8")
    sb = scoreboard.build_scoreboard(tmp_path, 5)
    trend = sb["trend"]
    assert trend["previous_file"] == "battery_alpha.json"
    assert trend["pass_delta"] == -5  # beta pass 2 - alpha pass 7
    assert trend["fail_delta"] == 1
    assert trend["live_calls_delta"] == -1  # 5 - 6
    assert trend["p50_search_delta_s"] == 0.0  # 2.0 - 2.0
    assert trend["p50_extract_delta_s"] == -0.5  # 1.0 - 1.5


def test_trend_notes_unchanged_latest_run(tmp_path):
    shutil.copy(FIXTURES / "battery_alpha.json", tmp_path / "battery_alpha.json")
    previous = {
        "generated_at": "2026-10-01T12:00:00+00:00",
        "latest": {"file": "battery_alpha.json", "pass": 7, "fail": 0},
    }
    (tmp_path / "scoreboard.json").write_text(json.dumps(previous), encoding="utf-8")
    sb = scoreboard.build_scoreboard(tmp_path, 5)
    assert "unchanged" in sb["trend"]["note"]


# ─── markdown render ─────────────────────────────────────────────────────────


def test_markdown_render_smoke():
    sb = scoreboard.build_scoreboard(FIXTURES, 5)
    md = scoreboard.render_markdown(sb, FIXTURES)
    assert "Latest battery run" in md
    assert "battery_beta.json" in md
    assert "Cross-run trend" in md
    assert "Keyless runs" in md
    assert "keyless_beta.json" in md
    assert f"generated_at: {sb['generated_at']}" in md
    assert "2.00" in md  # beta search p50


def test_markdown_render_empty_scoreboard():
    sb = {
        "generated_at": "2026-10-06T00:00:00+00:00",
        "runs": [],
        "keyless": [],
        "latest": None,
        "trend": {"previous": None, "note": "no battery runs found"},
    }
    md = scoreboard.render_markdown(sb, FIXTURES)
    assert "No battery runs found" in md
    assert "No keyless runs in window." in md
    assert "generated_at: 2026-10-06T00:00:00+00:00" in md


# ─── CLI (in-process via main(argv)) ─────────────────────────────────────────


def test_main_writes_outputs_and_exits_0(tmp_path):
    out_md = tmp_path / "analysis" / "scoreboard.md"
    out_json = tmp_path / "results" / "scoreboard.json"
    rc = scoreboard.main(
        [
            "--results-dir",
            str(FIXTURES),
            "--out-md",
            str(out_md),
            "--out-json",
            str(out_json),
        ]
    )
    assert rc == 0
    assert out_md.is_file() and out_json.is_file()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["latest"]["file"] == "battery_beta.json"
    assert "generated_at: " in out_md.read_text(encoding="utf-8")


def test_main_json_stdout_mode(tmp_path, capsys):
    rc = scoreboard.main(
        [
            "--results-dir",
            str(FIXTURES),
            "--out-md",
            str(tmp_path / "a.md"),
            "--out-json",
            str(tmp_path / "s.json"),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert {"generated_at", "runs", "latest", "trend"} <= set(payload)


def test_main_missing_results_dir_warns_and_exits_0(tmp_path, capsys):
    rc = scoreboard.main(
        [
            "--results-dir",
            str(tmp_path / "nope"),
            "--out-md",
            str(tmp_path / "a.md"),
            "--out-json",
            str(tmp_path / "s.json"),
        ]
    )
    assert rc == 0
    assert "not found" in capsys.readouterr().err
    payload = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert payload["runs"] == [] and payload["latest"] is None


def test_main_malformed_only_dir_exits_0(tmp_path, capsys):
    (tmp_path / "battery_broken.json").write_text("{not json", encoding="utf-8")
    rc = scoreboard.main(
        [
            "--results-dir",
            str(tmp_path),
            "--out-md",
            str(tmp_path / "a.md"),
            "--out-json",
            str(tmp_path / "s.json"),
        ]
    )
    assert rc == 0
    assert "battery_broken.json" in capsys.readouterr().err
    payload = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert payload["runs"] == []


def test_main_last_must_be_positive(tmp_path):
    with pytest.raises(SystemExit) as exc:
        scoreboard.main(
            [
                "--results-dir",
                str(FIXTURES),
                "--last",
                "0",
                "--out-md",
                str(tmp_path / "a.md"),
                "--out-json",
                str(tmp_path / "s.json"),
            ]
        )
    assert exc.value.code == 2


# ─── R15-A gap scaffold ──────────────────────────────────────────────────


def _write_run(path: Path, passes: int, total: int) -> None:
    cases = [{"id": f"vn-{i:03d}", "pass": i < passes, "latency_s": 1.0} for i in range(total)]
    path.write_text(
        json.dumps(
            {
                "generated": "2026-10-07T00:00:00",
                "live_calls": 0,
                "cases": cases,
                "totals": {"pass": passes, "fail": total - passes, "total": total, "elapsed_s": 1.0},
            }
        ),
        encoding="utf-8",
    )


def test_build_gap_no_current_run_returns_none(tmp_path):
    assert scoreboard.build_gap(tmp_path) is None


def test_build_gap_no_reference_warns_and_returns_none(tmp_path, capsys):
    _write_run(tmp_path / "r15_corpus_20261007_000000.json", 1, 2)
    assert scoreboard.build_gap(tmp_path) is None
    assert "reference" in capsys.readouterr().err.lower()


def test_build_gap_math_positive_means_behind(tmp_path):
    _write_run(tmp_path / "r15_corpus_20261007_000000.json", 2, 4)  # current 0.5
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir()
    _write_run(ref_dir / "ref.json", 3, 4)  # reference 0.75
    gap = scoreboard.build_gap(tmp_path)
    assert gap is not None
    assert gap["reference_pass_rate"] == pytest.approx(0.75)
    assert gap["gap_pp"] == pytest.approx(25.0)  # (0.75 - 0.5) * 100
    assert gap["reference_file"] == "ref.json"
    assert gap["current_file"] == "r15_corpus_20261007_000000.json"


def test_build_gap_malformed_reference_warns_no_crash(tmp_path, capsys):
    _write_run(tmp_path / "r15_corpus_20261007_000000.json", 1, 2)
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir()
    (ref_dir / "broken.json").write_text("{not json", encoding="utf-8")
    assert scoreboard.build_gap(tmp_path) is None
    assert "broken.json" in capsys.readouterr().err
