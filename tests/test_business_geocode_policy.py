"""R14-E — geocode attempt-tracking + retry-window policy for
``vn_geo.business.seed_from_config``.

Hermetic: tmp_path databases; connectors stubbed via ``business._connector_map``;
the Goong/Nominatim chain steps and ``time.sleep`` are monkeypatched. No network.

Policy under test: a coordinate-less record is re-geocoded only when its attempt
marker (``kv`` key ``geocode_attempted:<entity_id>``) is absent or older than
``business_geocode_retry_days`` (default 7) AND the stored entity (same
``entity_id``) has no coordinates yet. A skipped attempt sleeps not and adds no
document version.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from vn_geo import business

N = 3


# ---------- fixtures / helpers ----------


def _records(n=N):
    return [
        {"name": f"Doanh nghiệp {i}", "address": f"{i} Đường X, Yên Dũng", "tax_code": f"{i:010d}"} for i in range(n)
    ]


def _stub_map(records, name="connectors_masothue"):
    module = SimpleNamespace(__name__=f"vn_geo.{name}", fetch=lambda area, **kw: [dict(r) for r in records])
    return {name: module}


def _write_config(tmp_path, *, areas=None, **extra):
    cfg = {"business_enabled": True, "areas": areas or [{"name": "Yên Dũng", "province": "Tỉnh Bắc Ninh"}]}
    cfg.update(extra)
    path = tmp_path / "areas.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "biz.db")


@pytest.fixture()
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(business.time, "sleep", lambda s: calls.append(s))
    return calls


def _install_geocoders(monkeypatch, outcomes=None):
    """Patch the geocode chain steps with call counters.

    ``outcomes`` is a per-Nominatim-call list of success bools (missing entries
    fail). Goong always misses (key pending, as in production). Returns the
    ``{"goong": n, "nominatim": n}`` counter dict.
    """
    calls = {"goong": 0, "nominatim": 0}
    seq = list(outcomes) if outcomes is not None else []

    def fake_goong(query):
        calls["goong"] += 1
        return None

    def fake_nominatim(query):
        idx = calls["nominatim"]
        calls["nominatim"] += 1
        if idx < len(seq) and seq[idx]:
            return (21.2, 106.25, "nominatim")
        return None

    monkeypatch.setattr(business, "_goong_geocode", fake_goong)
    monkeypatch.setattr(business, "_nominatim_geocode", fake_nominatim)
    return calls


def _count_documents(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def _kv(db_path):
    with sqlite3.connect(db_path) as conn:
        return dict(conn.execute("SELECT key, value FROM kv").fetchall())


# ---------- retry-window policy ----------


def test_failed_attempts_mark_then_skip_on_rerun(tmp_path, db_path, monkeypatch, sleeps):
    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(_records()))
    calls = _install_geocoders(monkeypatch)  # every attempt fails
    cfg = _write_config(tmp_path)

    run1 = business.seed_from_config(cfg, db_path)
    assert run1["seeded"] == N
    assert run1["geocoded"] == N
    assert run1["geocode_skipped_recent"] == 0
    assert run1["geocode_skipped_enriched"] == 0
    assert run1["areas"][0]["geocoded"] == N
    assert calls["nominatim"] == N
    # a marker is written for every attempt, failures included
    assert len([k for k in _kv(db_path) if k.startswith("geocode_attempted:")]) == N
    assert sleeps == [1.5] * N
    versions_after_run1 = _count_documents(db_path)

    run2 = business.seed_from_config(cfg, db_path)
    assert run2["seeded"] == N
    assert run2["geocoded"] == 0
    assert run2["geocode_skipped_recent"] == N
    assert run2["geocode_skipped_enriched"] == 0
    assert calls["nominatim"] == N  # no new network calls
    assert sleeps == [1.5] * N  # the skip path never sleeps
    assert _count_documents(db_path) == versions_after_run1  # zero new document versions


def test_successful_attempt_is_enriched_skipped_on_rerun(tmp_path, db_path, monkeypatch, sleeps):
    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(_records(1)))
    calls = _install_geocoders(monkeypatch, outcomes=[True])  # the only attempt succeeds
    cfg = _write_config(tmp_path)

    run1 = business.seed_from_config(cfg, db_path)
    assert run1["geocoded"] == 1
    assert business.query_entities(db_path, "", limit=5)[0]["lat"] is not None
    versions_after_run1 = _count_documents(db_path)

    run2 = business.seed_from_config(cfg, db_path)
    assert run2["geocoded"] == 0
    assert run2["geocode_skipped_enriched"] == 1
    assert run2["geocode_skipped_recent"] == 0
    assert calls["nominatim"] == 1  # the enriched record is not re-geocoded
    assert _count_documents(db_path) == versions_after_run1  # stored coords carried, no churn


def test_retry_window_elapsed_retries(tmp_path, db_path, monkeypatch, sleeps):
    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(_records()))
    calls = _install_geocoders(monkeypatch)  # fail every time
    cfg = _write_config(tmp_path, business_geocode_retry_days=0)  # window closed immediately

    run1 = business.seed_from_config(cfg, db_path)
    assert run1["geocoded"] == N
    run2 = business.seed_from_config(cfg, db_path)
    assert run2["geocoded"] == N  # marker age (>= 0) is not < 0 -> retried
    assert run2["geocode_skipped_recent"] == 0
    assert calls["nominatim"] == 2 * N


def test_stale_marker_retries_within_default_window(tmp_path, db_path, monkeypatch, sleeps):
    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(_records()))
    calls = _install_geocoders(monkeypatch)
    cfg = _write_config(tmp_path)  # default retry_days = 7

    business.seed_from_config(cfg, db_path)
    # backdate every marker far past the 7-day window
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE kv SET value = ? WHERE key LIKE 'geocode_attempted:%'", ("2020-01-01T00:00:00+00:00",))
    run2 = business.seed_from_config(cfg, db_path)
    assert run2["geocoded"] == N
    assert calls["nominatim"] == 2 * N


def test_retry_days_default_is_seven_when_key_absent(tmp_path, db_path, monkeypatch, sleeps):
    assert business._geocode_retry_days({}) == 7.0
    assert business._geocode_retry_days({"business_geocode_retry_days": 3}) == 3.0
    assert business._geocode_retry_days({"business_geocode_retry_days": "nope"}) == 7.0

    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(_records()))
    _install_geocoders(monkeypatch)
    cfg = _write_config(tmp_path)  # no business_geocode_retry_days key
    business.seed_from_config(cfg, db_path)
    run2 = business.seed_from_config(cfg, db_path)
    assert run2["geocoded"] == 0
    assert run2["geocode_skipped_recent"] == N


def test_geocode_missing_false_makes_no_attempts_or_markers(tmp_path, db_path, monkeypatch, sleeps):
    monkeypatch.setattr(business, "_connector_map", lambda: _stub_map(_records()))
    calls = _install_geocoders(monkeypatch)
    cfg = _write_config(tmp_path, business_geocode_missing=False)

    out = business.seed_from_config(cfg, db_path)
    assert out["seeded"] == N
    assert out["geocoded"] == 0
    assert out["geocode_skipped_recent"] == 0
    assert out["geocode_skipped_enriched"] == 0
    assert calls["nominatim"] == 0 and calls["goong"] == 0
    assert sleeps == []
    assert [k for k in _kv(db_path) if k.startswith("geocode_attempted:")] == []
    assert business.query_entities(db_path, "", limit=5)[0]["lat"] is None
