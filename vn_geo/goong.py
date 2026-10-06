"""Goong (VN Google Maps alternative) REST client + 1000/day cap (R5-B).

Frozen contract: ``analysis/r5-interfaces.md`` §4. Goong (goong.io) is the
Vietnamese alternative to Google Maps APIs (Google Maps Platform is blocked
for VN). The client enforces a hard local daily cap of 1000 requests (the free
tier) so usage never eats into the $100 balance. No API key exists yet —
hermetic tests only; no live calls.

CLI: ``python -m vn_geo.goong <autocomplete|geocode|reverse|detail|usage>``
— exit 0 ok · 1 runtime error · 2 usage/IO.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import VnGeoError

BASE_URL = "https://rsapi.goong.io"
DEFAULT_TIMEOUT = 30.0
DEFAULT_DAILY_LIMIT = 1000
USER_AGENT = "hermes-vn-geo/0.1"


# --------------------------------------------------------------------------- paths


def _default_dir() -> Path:
    """Default config dir: ``<LOCALAPPDATA>/hermes/vn-geo``."""
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "hermes" / "vn-geo"


# --------------------------------------------------------------------------- key resolution


def _read_key_file(path: Path) -> str | None:
    """Parse a ``KEY=value`` env file and return the Goong key, or None.

    Ignores ``#`` comments and blank lines; strips surrounding quotes from
    values. Returns None when the file is missing or has no GOONG_API_KEY.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "GOONG_API_KEY":
            value = value.strip().strip('"').strip("'").strip()
            if value:
                return value
    return None


def resolve_api_key(api_key: str | None = None, *, key_file: str | Path | None = None) -> str:
    """Resolve the Goong API key: explicit arg → env → key file → error.

    The key is never stored in or appended to error messages.
    """
    if api_key:
        return api_key
    env_key = os.environ.get("GOONG_API_KEY")
    if env_key:
        return env_key
    path = Path(key_file) if key_file is not None else _default_dir() / "keys.env"
    key = _read_key_file(path)
    if key:
        return key
    raise VnGeoError(f"no Goong API key found: set GOONG_API_KEY or create {path} with GOONG_API_KEY=...")


# --------------------------------------------------------------------------- daily limiter


class DailyLimiter:
    """Hard local daily request cap with persistent JSON state.

    State file: ``{"date": "YYYY-MM-DD", "count": n}``. Date rollover
    resets the count. ``today`` is injectable (``YYYY-MM-DD`` str) for tests;
    default = local date.
    """

    def __init__(self, state_path: str | Path | None = None, daily_limit: int = DEFAULT_DAILY_LIMIT):
        self._state_path = Path(state_path) if state_path is not None else _default_dir() / "goong_usage.json"
        self._daily_limit = daily_limit

    def _today(self, today: str | None = None) -> str:
        return today if today is not None else datetime.date.today().isoformat()

    def _load_count(self, today: str) -> int:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        if not isinstance(data, dict) or data.get("date") != today:
            return 0
        count = data.get("count", 0)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return 0
        return count

    def _save(self, today: str, count: int) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps({"date": today, "count": count}), encoding="utf-8")

    def usage(self, today: str | None = None) -> dict:
        """Return ``{"date", "used", "limit", "remaining"}`` for the day."""
        today = self._today(today)
        used = self._load_count(today)
        return {
            "date": today,
            "used": used,
            "limit": self._daily_limit,
            "remaining": self._daily_limit - used,
        }

    def check(self, today: str | None = None) -> None:
        """Raise VnGeoError when the daily limit is reached."""
        today = self._today(today)
        if self._load_count(today) >= self._daily_limit:
            raise VnGeoError(
                f"Goong daily limit {self._daily_limit} reached for {today}; retry tomorrow or raise daily_limit"
            )

    def record(self, today: str | None = None) -> None:
        """Increment the day's counter and persist it."""
        today = self._today(today)
        self._save(today, self._load_count(today) + 1)


# --------------------------------------------------------------------------- client


def _format_latlng(value: object) -> str:
    """Accept a ``"lat,lng"`` string or a ``(lat, lng)`` tuple."""
    if isinstance(value, str):
        return value
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return f"{value[0]},{value[1]}"
    raise VnGeoError(f"invalid latlng {value!r}: expected 'lat,lng' string or (lat, lng) tuple")


class GoongClient:
    """Goong REST client with a hard daily request cap.

    The API key is resolved in ``__init__`` (fail fast). Every network method
    runs ``limiter.check()`` → attempt → ``limiter.record()`` (in ``finally``),
    so one increment is counted per attempted request even when the HTTP call
    fails afterwards.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        key_file: str | Path | None = None,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        state_path: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        limiter: DailyLimiter | None = None,
    ):
        self._api_key = resolve_api_key(api_key, key_file=key_file)
        self._timeout = timeout
        self._limiter = limiter if limiter is not None else DailyLimiter(state_path=state_path, daily_limit=daily_limit)

    def usage(self) -> dict:
        """The limiter's usage for today."""
        return self._limiter.usage()

    def _get(self, path: str, params: dict) -> dict:
        """GET ``path`` with ``params`` (+ ``api_key`` last) and return the payload."""
        query = dict(params)
        query["api_key"] = self._api_key
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        timeout = self._timeout
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - module-level https constant
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            snippet = ""
            try:
                snippet = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                snippet = ""
            raise VnGeoError(f"Goong API HTTP {exc.code}: {snippet}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VnGeoError(f"Goong request failed: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VnGeoError(f"invalid JSON from Goong: {exc}") from exc
        if not isinstance(payload, dict):
            raise VnGeoError("unexpected Goong response (not a JSON object)")
        status = payload.get("status")
        if status != "OK":
            detail = payload.get("error_message") or payload.get("description") or payload.get("message") or ""
            if detail:
                raise VnGeoError(f"Goong API error: {status} — {detail}")
            raise VnGeoError(f"Goong API error: {status}")
        return payload

    def autocomplete(
        self,
        text: str,
        *,
        location: str | tuple[float, float] | None = None,
        limit: int = 5,
        radius: int | None = None,
        more_compound: object | None = None,
        include_deprecated: bool = False,
    ) -> list[dict]:
        """Place autocomplete suggestions (``/v2/place/autocomplete``)."""
        self._limiter.check()
        try:
            params: dict = {"input": text, "limit": limit}
            if location is not None:
                params["location"] = _format_latlng(location)
            if radius is not None:
                params["radius"] = radius
            if more_compound is not None:
                params["more_compound"] = more_compound
            if include_deprecated:
                params["has_deprecated_administrative_unit"] = "true"
            payload = self._get("/v2/place/autocomplete", params)
            return payload.get("predictions", [])
        finally:
            self._limiter.record()

    def geocode(self, address: str, *, include_deprecated: bool = False) -> list[dict]:
        """Geocode an address (``/v2/geocode?address=...``)."""
        self._limiter.check()
        try:
            params: dict = {"address": address}
            if include_deprecated:
                params["has_deprecated_administrative_unit"] = "true"
            payload = self._get("/v2/geocode", params)
            return payload.get("results", [])
        finally:
            self._limiter.record()

    def reverse(
        self,
        latlng: str | tuple[float, float],
        *,
        limit: int | None = None,
        include_deprecated: bool = False,
    ) -> list[dict]:
        """Reverse geocode ``"lat,lng"`` or ``(lat, lng)`` (``/v2/geocode?latlng=...``)."""
        self._limiter.check()
        try:
            params: dict = {"latlng": _format_latlng(latlng)}
            if limit is not None:
                params["limit"] = limit
            if include_deprecated:
                params["has_deprecated_administrative_unit"] = "true"
            payload = self._get("/v2/geocode", params)
            return payload.get("results", [])
        finally:
            self._limiter.record()

    def place_detail(self, place_id: str, *, include_deprecated: bool = False) -> dict:
        """Place detail by place_id (``/v2/place/detail``)."""
        self._limiter.check()
        try:
            params: dict = {"place_id": place_id}
            if include_deprecated:
                params["has_deprecated_administrative_unit"] = "true"
            payload = self._get("/v2/place/detail", params)
            return payload.get("result", {})
        finally:
            self._limiter.record()


# --------------------------------------------------------------------------- CLI


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--key-file", metavar="PATH", help="keys.env file with GOONG_API_KEY=...")
    parser.add_argument(
        "--state-path",
        metavar="PATH",
        help="daily usage state file (default: <LOCALAPPDATA>/hermes/vn-geo/goong_usage.json)",
    )
    parser.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT, metavar="N")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _make_client(args: argparse.Namespace) -> GoongClient:
    return GoongClient(key_file=args.key_file, daily_limit=args.daily_limit, state_path=args.state_path)


def _result_line(result: dict) -> str:
    addr = result.get("formatted_address", "?")
    geom = result.get("geometry", {}).get("location", {})
    lat, lng = geom.get("lat"), geom.get("lng")
    if lat is not None and lng is not None:
        return f"{addr} ({lat},{lng})"
    return addr


def _cmd_autocomplete(args: argparse.Namespace) -> tuple[dict, list[str]]:
    client = _make_client(args)
    results = client.autocomplete(
        args.text,
        location=args.location,
        limit=args.limit,
        radius=args.radius,
        more_compound=args.more_compound,
        include_deprecated=args.include_deprecated,
    )
    payload = {"ok": True, "count": len(results), "results": results}
    human = [f"{r.get('description', '?')} ({r.get('place_id', '?')})" for r in results]
    return payload, human


def _cmd_geocode(args: argparse.Namespace) -> tuple[dict, list[str]]:
    client = _make_client(args)
    results = client.geocode(args.address, include_deprecated=args.include_deprecated)
    payload = {"ok": True, "count": len(results), "results": results}
    human = [_result_line(r) for r in results]
    return payload, human


def _cmd_reverse(args: argparse.Namespace) -> tuple[dict, list[str]]:
    client = _make_client(args)
    results = client.reverse((args.lat, args.lng), limit=args.limit, include_deprecated=args.include_deprecated)
    payload = {"ok": True, "count": len(results), "results": results}
    human = [_result_line(r) for r in results]
    return payload, human


def _cmd_detail(args: argparse.Namespace) -> tuple[dict, list[str]]:
    client = _make_client(args)
    result = client.place_detail(args.place_id, include_deprecated=args.include_deprecated)
    payload = {"ok": True, "result": result}
    name = result.get("name", "?")
    human = [f"{name} — {_result_line(result)}"]
    return payload, human


def _cmd_usage(args: argparse.Namespace) -> tuple[dict, list[str]]:
    limiter = DailyLimiter(state_path=args.state_path, daily_limit=args.daily_limit)
    usage = limiter.usage()
    payload = {"ok": True, **usage}
    human = [f"date={usage['date']} used={usage['used']} limit={usage['limit']} remaining={usage['remaining']}"]
    return payload, human


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_geo.goong",
        description="Goong (VN Google Maps alternative) REST client: autocomplete/geocode/reverse/detail/usage",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("autocomplete", help="place autocomplete suggestions")
    p.add_argument("text", metavar="TEXT")
    p.add_argument("--location", metavar="LAT,LNG", help="bias location (e.g. 21.2,106.25)")
    p.add_argument("--limit", type=int, default=5, metavar="N")
    p.add_argument("--radius", type=int, metavar="M")
    p.add_argument("--more-compound", action="store_true", default=None)
    p.add_argument("--include-deprecated", action="store_true")
    _add_common_args(p)
    p.set_defaults(func=_cmd_autocomplete)

    p = sub.add_parser("geocode", help="geocode an address")
    p.add_argument("address", metavar="ADDRESS")
    p.add_argument("--include-deprecated", action="store_true")
    _add_common_args(p)
    p.set_defaults(func=_cmd_geocode)

    p = sub.add_parser("reverse", help="reverse geocode lat/lng")
    p.add_argument("lat", type=float, metavar="LAT")
    p.add_argument("lng", type=float, metavar="LNG")
    p.add_argument("--limit", type=int, metavar="N")
    p.add_argument("--include-deprecated", action="store_true")
    _add_common_args(p)
    p.set_defaults(func=_cmd_reverse)

    p = sub.add_parser("detail", help="place detail by place_id")
    p.add_argument("place_id", metavar="PLACE_ID")
    p.add_argument("--include-deprecated", action="store_true")
    _add_common_args(p)
    p.set_defaults(func=_cmd_detail)

    p = sub.add_parser("usage", help="show daily usage counter (no key needed)")
    _add_common_args(p)
    p.set_defaults(func=_cmd_usage)

    return parser


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return 0 ok · 1 runtime error · 2 usage/IO."""
    args = _build_parser().parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        payload, human = args.func(args)
    except VnGeoError as exc:
        _emit_error(exc, json_mode)
        return 1
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        _emit_error(exc, json_mode)
        return 2
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in human:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
