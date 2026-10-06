"""Hermetic tests for vn_geo.goong (R5-B). No live network."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest

from vn_geo import VnGeoError, goong
from vn_geo.goong import (
    BASE_URL,
    DEFAULT_DAILY_LIMIT,
    DailyLimiter,
    GoongClient,
    resolve_api_key,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vn_geo" / "goong"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _make_client(**kwargs) -> GoongClient:
    kwargs.setdefault("api_key", "test-key")
    return GoongClient(**kwargs)


def _patch_ok(monkeypatch, payload: dict) -> list[str]:
    """Monkeypatch urlopen to return ``payload``; return captured URLs."""
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(goong.urllib.request, "urlopen", fake_urlopen)
    return calls


def _query(url: str) -> dict:
    return dict(parse_qsl(urlparse(url).query))


# ---------- key resolution ----------


def test_resolve_api_key_explicit():
    assert resolve_api_key("my-key") == "my-key"


def test_resolve_api_key_env(monkeypatch):
    monkeypatch.setenv("GOONG_API_KEY", "env-key")
    assert resolve_api_key() == "env-key"


def test_resolve_api_key_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("GOONG_API_KEY", "env-key")
    assert resolve_api_key("explicit-key") == "explicit-key"


def test_resolve_api_key_key_file(tmp_path):
    key_file = tmp_path / "keys.env"
    key_file.write_text("GOONG_API_KEY=file-key\n", encoding="utf-8")
    assert resolve_api_key(key_file=str(key_file)) == "file-key"


def test_resolve_api_key_key_file_parsing(tmp_path):
    key_file = tmp_path / "keys.env"
    key_file.write_text(
        "# comment line\n\nOTHER_KEY=other\nGOONG_API_KEY=\"quoted-key\"\nANOTHER='single'\n",
        encoding="utf-8",
    )
    assert resolve_api_key(key_file=str(key_file)) == "quoted-key"


def test_resolve_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("GOONG_API_KEY", raising=False)
    missing = tmp_path / "nonexistent.env"
    with pytest.raises(VnGeoError, match="no Goong API key found"):
        resolve_api_key(key_file=str(missing))


def test_resolve_api_key_missing_message_has_path(tmp_path, monkeypatch):
    monkeypatch.delenv("GOONG_API_KEY", raising=False)
    missing = tmp_path / "nonexistent.env"
    with pytest.raises(VnGeoError) as exc_info:
        resolve_api_key(key_file=str(missing))
    msg = str(exc_info.value)
    assert str(missing) in msg
    assert "GOONG_API_KEY=" in msg


def test_resolve_api_key_env_beats_key_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GOONG_API_KEY", "env-key")
    key_file = tmp_path / "keys.env"
    key_file.write_text("GOONG_API_KEY=file-key\n", encoding="utf-8")
    assert resolve_api_key(key_file=str(key_file)) == "env-key"


# ---------- URL + params ----------


def test_autocomplete_url_and_params(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    client = _make_client()
    client.autocomplete("quán ăn")
    url = calls[0]
    assert url.startswith(f"{BASE_URL}/v2/place/autocomplete?")
    params = _query(url)
    assert params["input"] == "quán ăn"
    assert params["limit"] == "5"
    assert params["api_key"] == "test-key"


def test_autocomplete_with_location(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    client = _make_client()
    client.autocomplete("quán ăn", location="21.2,106.25")
    params = _query(calls[0])
    assert params["location"] == "21.2,106.25"


def test_autocomplete_with_location_tuple(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    client = _make_client()
    client.autocomplete("quán ăn", location=(21.2, 106.25))
    params = _query(calls[0])
    assert params["location"] == "21.2,106.25"


def test_autocomplete_with_radius_more_compound(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    client = _make_client()
    client.autocomplete("quán ăn", radius=500, more_compound=True)
    params = _query(calls[0])
    assert params["radius"] == "500"
    assert "more_compound" in params


def test_autocomplete_include_deprecated(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    client = _make_client()
    client.autocomplete("quán ăn", include_deprecated=True)
    params = _query(calls[0])
    assert params["has_deprecated_administrative_unit"] == "true"


def test_autocomplete_default_limit_is_5(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    client = _make_client()
    client.autocomplete("quán ăn")
    assert _query(calls[0])["limit"] == "5"


def test_geocode_url_and_params(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("geocode.json"))
    client = _make_client()
    client.geocode("Phường Yên Dũng, Bắc Ninh")
    url = calls[0]
    assert url.startswith(f"{BASE_URL}/v2/geocode?")
    params = _query(url)
    assert params["address"] == "Phường Yên Dũng, Bắc Ninh"
    assert params["api_key"] == "test-key"


def test_geocode_include_deprecated(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("geocode.json"))
    client = _make_client()
    client.geocode("Bắc Ninh", include_deprecated=True)
    assert _query(calls[0])["has_deprecated_administrative_unit"] == "true"


def test_reverse_string(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("geocode.json"))
    client = _make_client()
    client.reverse("21.2012,106.2493")
    url = calls[0]
    assert url.startswith(f"{BASE_URL}/v2/geocode?")
    assert _query(url)["latlng"] == "21.2012,106.2493"


def test_reverse_tuple(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("geocode.json"))
    client = _make_client()
    client.reverse((21.2012, 106.2493))
    assert _query(calls[0])["latlng"] == "21.2012,106.2493"


def test_reverse_with_limit(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("geocode.json"))
    client = _make_client()
    client.reverse("21.2,106.25", limit=3)
    assert _query(calls[0])["limit"] == "3"


def test_place_detail_url_and_params(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("detail.json"))
    client = _make_client()
    client.place_detail("ChIJxyz789")
    url = calls[0]
    assert url.startswith(f"{BASE_URL}/v2/place/detail?")
    params = _query(url)
    assert params["place_id"] == "ChIJxyz789"
    assert params["api_key"] == "test-key"


def test_api_key_last_in_query(monkeypatch):
    calls = _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    client = _make_client()
    client.autocomplete("quán ăn", radius=100)
    pairs = parse_qsl(urlparse(calls[0]).query)
    assert pairs[-1][0] == "api_key"


def test_user_agent_header(monkeypatch):
    seen: dict = {}

    def fake_urlopen(request, timeout=None):
        seen["agent"] = request.get_header("User-agent")
        return _FakeResponse(json.dumps(_fixture("autocomplete.json")).encode("utf-8"))

    monkeypatch.setattr(goong.urllib.request, "urlopen", fake_urlopen)
    _make_client().autocomplete("quán ăn")
    assert seen["agent"] == "hermes-vn-geo/0.1"


def test_timeout_passed_to_urlopen(monkeypatch):
    seen: dict = {}

    def fake_urlopen(request, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse(json.dumps(_fixture("autocomplete.json")).encode("utf-8"))

    monkeypatch.setattr(goong.urllib.request, "urlopen", fake_urlopen)
    _make_client(timeout=7.5).autocomplete("quán ăn")
    assert seen["timeout"] == 7.5


# ---------- response parsing ----------


def test_autocomplete_parses_predictions(monkeypatch):
    _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    client = _make_client()
    out = client.autocomplete("quán ăn")
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["place_id"] == "ChIJabc123YenDung"
    assert out[0]["description"] == "Quán Ăn Yên Dũng, Bắc Ninh, Việt Nam"


def test_geocode_parses_results(monkeypatch):
    _patch_ok(monkeypatch, _fixture("geocode.json"))
    client = _make_client()
    out = client.geocode("Phường Yên Dũng, Bắc Ninh")
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["formatted_address"] == "Phường Yên Dũng, Bắc Ninh, Việt Nam"
    assert out[0]["geometry"]["location"]["lat"] == 21.2012


def test_reverse_parses_results(monkeypatch):
    _patch_ok(monkeypatch, _fixture("geocode.json"))
    client = _make_client()
    out = client.reverse("21.2012,106.2493")
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["place_id"] == "ChIJxyz789YenDung"


def test_place_detail_parses_result(monkeypatch):
    _patch_ok(monkeypatch, _fixture("detail.json"))
    client = _make_client()
    out = client.place_detail("ChIJxyz789")
    assert isinstance(out, dict)
    assert out["name"] == "Yên Dũng"
    assert out["place_id"] == "ChIJxyz789YenDung"
    assert out["geometry"]["location"]["lng"] == 106.2493


# ---------- error mapping ----------


def test_non_ok_status_raises(monkeypatch):
    _patch_ok(monkeypatch, _fixture("error.json"))
    client = _make_client()
    with pytest.raises(VnGeoError, match="INVALID_REQUEST"):
        client.geocode("bad")


def test_non_ok_status_includes_message(monkeypatch):
    _patch_ok(monkeypatch, _fixture("error.json"))
    client = _make_client()
    with pytest.raises(VnGeoError) as exc_info:
        client.geocode("bad")
    assert "The provided API key is invalid." in str(exc_info.value)


def test_http_error_raises(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(goong.urllib.request, "urlopen", fake_urlopen)
    client = _make_client()
    with pytest.raises(VnGeoError, match="403"):
        client.geocode("test")


def test_url_error_raises(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(goong.urllib.request, "urlopen", fake_urlopen)
    client = _make_client()
    with pytest.raises(VnGeoError, match="request failed"):
        client.geocode("test")


def test_invalid_json_raises(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(b"not json at all{")

    monkeypatch.setattr(goong.urllib.request, "urlopen", fake_urlopen)
    client = _make_client()
    with pytest.raises(VnGeoError, match="invalid JSON"):
        client.geocode("test")


def test_error_message_has_no_key(monkeypatch):
    """The API key must never appear in error messages."""

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(goong.urllib.request, "urlopen", fake_urlopen)
    client = _make_client()
    with pytest.raises(VnGeoError) as exc_info:
        client.geocode("test")
    assert "test-key" not in str(exc_info.value)


# ---------- limiter ----------


def test_limiter_usage_initial(tmp_path):
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1000)
    usage = limiter.usage(today="2026-10-06")
    assert usage == {"date": "2026-10-06", "used": 0, "limit": 1000, "remaining": 1000}


def test_limiter_usage_dict_shape(tmp_path):
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=50)
    usage = limiter.usage(today="2026-10-06")
    assert set(usage) == {"date", "used", "limit", "remaining"}
    assert usage["limit"] == 50
    assert usage["remaining"] == 50 - usage["used"]


def test_limiter_record_increments(tmp_path):
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1000)
    limiter.record(today="2026-10-06")
    limiter.record(today="2026-10-06")
    assert limiter.usage(today="2026-10-06")["used"] == 2


def test_limiter_check_below_limit(tmp_path):
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=2)
    limiter.record(today="2026-10-06")
    limiter.check(today="2026-10-06")  # used=1 < limit=2 → no raise


def test_limiter_check_at_limit(tmp_path):
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1)
    limiter.record(today="2026-10-06")
    with pytest.raises(VnGeoError, match="daily limit 1 reached"):
        limiter.check(today="2026-10-06")


def test_limiter_check_message_actionable(tmp_path):
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1)
    limiter.record(today="2026-10-06")
    with pytest.raises(VnGeoError) as exc_info:
        limiter.check(today="2026-10-06")
    msg = str(exc_info.value)
    assert "2026-10-06" in msg
    assert "retry tomorrow" in msg


def test_limiter_date_rollover(tmp_path):
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1)
    limiter.record(today="2026-10-06")
    with pytest.raises(VnGeoError):
        limiter.check(today="2026-10-06")
    limiter.check(today="2026-10-07")  # rolled over → passes
    assert limiter.usage(today="2026-10-07")["used"] == 0


def test_limiter_state_file_roundtrip(tmp_path):
    path = tmp_path / "usage.json"
    limiter = DailyLimiter(state_path=path, daily_limit=1000)
    limiter.record(today="2026-10-06")
    limiter.record(today="2026-10-06")
    limiter2 = DailyLimiter(state_path=path, daily_limit=1000)
    assert limiter2.usage(today="2026-10-06")["used"] == 2


def test_limiter_default_limit_is_1000(tmp_path):
    limiter = DailyLimiter(state_path=tmp_path / "usage.json")
    assert limiter.usage(today="2026-10-06")["limit"] == DEFAULT_DAILY_LIMIT == 1000


# ---------- client + limiter integration ----------


def test_client_records_usage(monkeypatch, tmp_path):
    _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1000)
    client = _make_client(limiter=limiter)
    client.autocomplete("quán ăn")
    assert limiter.usage()["used"] == 1


def test_client_stops_at_limit(monkeypatch, tmp_path):
    def fail_urlopen(request, timeout=None):
        raise AssertionError("urlopen must not be called when at limit")

    monkeypatch.setattr(goong.urllib.request, "urlopen", fail_urlopen)
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1)
    limiter.record()  # used=1 → at limit
    client = _make_client(limiter=limiter)
    with pytest.raises(VnGeoError, match="daily limit"):
        client.autocomplete("quán ăn")


def test_client_records_on_failure(monkeypatch, tmp_path):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(goong.urllib.request, "urlopen", fake_urlopen)
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1000)
    client = _make_client(limiter=limiter)
    with pytest.raises(VnGeoError):
        client.geocode("test")
    assert limiter.usage()["used"] == 1  # recorded even though HTTP failed


def test_client_usage_delegates_to_limiter(monkeypatch, tmp_path):
    _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    limiter = DailyLimiter(state_path=tmp_path / "usage.json", daily_limit=1000)
    client = _make_client(limiter=limiter)
    assert client.usage() == limiter.usage()


def test_client_resolves_key_fail_fast(tmp_path, monkeypatch):
    monkeypatch.delenv("GOONG_API_KEY", raising=False)
    with pytest.raises(VnGeoError, match="no Goong API key found"):
        GoongClient(key_file=str(tmp_path / "nonexistent.env"))


# ---------- CLI ----------


def test_cli_usage_json(tmp_path, capsys):
    state = tmp_path / "usage.json"
    rc = goong.main(["usage", "--state-path", str(state), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["limit"] == 1000
    assert out["used"] == 0
    assert out["remaining"] == 1000
    assert "date" in out


def test_cli_usage_human(tmp_path, capsys):
    state = tmp_path / "usage.json"
    rc = goong.main(["usage", "--state-path", str(state)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "used=0" in out
    assert "limit=1000" in out
    assert "remaining=1000" in out


def test_cli_usage_no_key_needed(tmp_path, capsys, monkeypatch):
    """usage works without any API key configured."""
    monkeypatch.delenv("GOONG_API_KEY", raising=False)
    rc = goong.main(["usage", "--state-path", str(tmp_path / "usage.json")])
    assert rc == 0


def test_cli_missing_key_exit_1(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GOONG_API_KEY", raising=False)
    missing = tmp_path / "nonexistent.env"
    rc = goong.main(["geocode", "Bắc Ninh", "--key-file", str(missing)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no Goong API key found" in err
    assert str(missing) in err


def test_cli_missing_key_json_mode(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GOONG_API_KEY", raising=False)
    missing = tmp_path / "nonexistent.env"
    rc = goong.main(["geocode", "Bắc Ninh", "--key-file", str(missing), "--json"])
    assert rc == 1
    err = json.loads(capsys.readouterr().err)
    assert err["ok"] is False
    assert "no Goong API key found" in err["error"]


def test_cli_geocode_with_key(monkeypatch, tmp_path, capsys):
    _patch_ok(monkeypatch, _fixture("geocode.json"))
    key_file = tmp_path / "keys.env"
    key_file.write_text("GOONG_API_KEY=cli-key\n", encoding="utf-8")
    rc = goong.main(["geocode", "Bắc Ninh", "--key-file", str(key_file), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["count"] == 1


def test_cli_autocomplete_with_key(monkeypatch, tmp_path, capsys):
    _patch_ok(monkeypatch, _fixture("autocomplete.json"))
    key_file = tmp_path / "keys.env"
    key_file.write_text("GOONG_API_KEY=cli-key\n", encoding="utf-8")
    rc = goong.main(["autocomplete", "quán ăn", "--key-file", str(key_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ChIJabc123YenDung" in out


def test_cli_detail_with_key(monkeypatch, tmp_path, capsys):
    _patch_ok(monkeypatch, _fixture("detail.json"))
    key_file = tmp_path / "keys.env"
    key_file.write_text("GOONG_API_KEY=cli-key\n", encoding="utf-8")
    rc = goong.main(["detail", "ChIJxyz789", "--key-file", str(key_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Yên Dũng" in out


def test_cli_reverse_with_key(monkeypatch, tmp_path, capsys):
    _patch_ok(monkeypatch, _fixture("geocode.json"))
    key_file = tmp_path / "keys.env"
    key_file.write_text("GOONG_API_KEY=cli-key\n", encoding="utf-8")
    rc = goong.main(["reverse", "21.2012", "106.2493", "--key-file", str(key_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "21.2012,106.2493" in out


def test_cli_usage_records_via_state_path(tmp_path, capsys):
    """usage --state-path reads the persisted counter."""
    state = tmp_path / "usage.json"
    limiter = DailyLimiter(state_path=state, daily_limit=1000)
    limiter.record()  # real "today" — the CLI usage command reads the real date too
    rc = goong.main(["usage", "--state-path", str(state), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["used"] == 1


def test_cli_bad_args_exit_2():
    with pytest.raises(SystemExit) as exc:
        goong.main(["geocode"])  # missing required address
    assert exc.value.code == 2
