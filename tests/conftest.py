"""Isolate Goong daily-limiter state (R14-G): never touch the machine-global file."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_goong_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_VN_GEO_GOONG_USAGE", str(tmp_path / "goong_usage.json"))
