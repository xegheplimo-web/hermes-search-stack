"""Pytest wrapper for the answer-quality golden evals (R2-C contract v1 §3).

Runs ``evals/answer_quality/run_answer_quality.py`` against the real ``fact_check.py``
(default path) in a subprocess and asserts the runner exits 0. There is deliberately
NO ``pytest.skip``: when ``fact_check.py`` is missing or a case regresses, this test
must FAIL loudly so the integration gap stays visible.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "evals" / "answer_quality" / "run_answer_quality.py"
REPO_ROOT = RUNNER.parents[2]


def test_answer_quality_golden_cases_pass() -> None:
    assert RUNNER.is_file(), f"runner missing: {RUNNER}"
    proc = subprocess.run(
        [sys.executable, str(RUNNER)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    report = f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.returncode == 0, f"answer-quality runner failed (exit {proc.returncode})\n{report}"
