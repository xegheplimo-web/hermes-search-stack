"""vn_geo.connectors_gosom — gosom/google-maps-scraper JSON(L) → entity-schema-v1 inputs.

Frozen contracts: ``analysis/r13-interfaces.md`` §1 (entity schema v1) + §3
(``connectors_*`` module contract: ``fetch(area, politeness_s=2.0) -> list[dict]``)
and ``analysis/r13-gmaps-scraper-analysis.md`` §3A (mapping table).

gosom scrapes out-of-band (Docker, see the R13-E runbook) and writes **one place
per JSONL line** (``-json -results <out>.jsonl``). This adapter never scrapes: it
reads that output and maps each record to the "normalized-to-§1 inputs" consumed
by ``vn_geo.business.normalize``:

======================  ==================================================
gosom field             §1 output key
======================  ==================================================
``title``               ``name``
``category``            ``category_raw`` (fallback ``categories[0]``)
``address``             ``address_text``
``phone``               ``phone``
``web_site``            ``website`` (fallback ``website``)
``latitude``            ``lat``
``longitude``           ``lng`` (fallback ``longtitude`` — gosom typo alias)
``place_id`` / ``cid``  ``source_id`` (place_id preferred, cid secondary)
``status``              ``status`` (``open|closed|unknown``)
``link``                ``source_url``
``review_rating``       ``rating``
``review_count``        ``review_count``
======================  ==================================================

``source`` is always ``"gmaps"``. ``name``/``address_text`` are also folded with
``searchstore.store.fold_d`` (đ/Đ→d, R9-W2B hard rule) into ``name_folded`` /
``address_folded`` so downstream FTS/dedupe is diacritic-safe. The untouched
gosom object is kept under ``raw``.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from searchstore.store import fold_d

from . import VnGeoError

SOURCE = "gmaps"

# Canonical gosom output for the R13-E seed run (repo-relative).
DEFAULT_RESULTS = Path(__file__).resolve().parents[1] / "agent_logs" / "r13e_gmaps.jsonl"

# status hints — closed is checked first ("closed" is a substring of nothing we
# care about here, but ordering keeps "permanently closed" unambiguous).
_CLOSED_HINTS = ("permanently closed", "temporarily closed", "đóng cửa", "tạm đóng", "đã đóng", "closed")
_OPEN_HINTS = ("đang mở", "mở cửa", "open")

__all__ = [
    "DEFAULT_RESULTS",
    "SOURCE",
    "fetch",
    "load_records",
    "main",
    "normalize_status",
    "parse_record",
    "save_jsonl",
]


# --------------------------------------------------------------------------- coercion helpers


def _s(value: object) -> str:
    """Coerce a gosom scalar to a stripped string (``""`` for None/containers)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _f(value: object) -> float | None:
    """Coerce a gosom scalar to ``float`` or ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _i(value: object) -> int | None:
    """Coerce a gosom scalar to ``int`` or ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _match_key(text: str) -> str:
    """Diacritic- + đ-insensitive key for area filtering (fold_d + NFD strip)."""
    folded = fold_d(text).casefold()
    return "".join(c for c in unicodedata.normalize("NFD", folded) if not unicodedata.combining(c))


# --------------------------------------------------------------------------- mapping


def normalize_status(raw: object) -> str:
    """Map a gosom ``status`` value to ``"open" | "closed" | "unknown"``.

    gosom emits ``""`` for most places and occasionally a free-text label; we only
    trust explicit open/closed wording and fall back to ``unknown`` otherwise.
    """
    if isinstance(raw, list):
        text = " ".join(_s(item) for item in raw)
    else:
        text = _s(raw)
    lowered = text.casefold()
    if not lowered:
        return "unknown"
    if any(hint in lowered for hint in _CLOSED_HINTS):
        return "closed"
    if any(hint in lowered for hint in _OPEN_HINTS):
        return "open"
    return "unknown"


def parse_record(obj: dict) -> dict:
    """Map one gosom place object to an entity-schema-§1-shaped input dict."""
    if not isinstance(obj, dict):
        raise VnGeoError(f"gosom record must be an object, got {type(obj).__name__}")
    name = _s(obj.get("title"))
    address = _s(obj.get("address"))
    category = _s(obj.get("category"))
    if not category:
        cats = obj.get("categories")
        if isinstance(cats, list) and cats:
            category = _s(cats[0])
    place_id = _s(obj.get("place_id"))
    cid = _s(obj.get("cid"))
    lat = _f(obj.get("latitude"))
    lng = _f(obj.get("longitude"))
    if lng is None:
        lng = _f(obj.get("longtitude"))
    return {
        "name": name,
        "category_raw": category,
        "address_text": address,
        "phone": _s(obj.get("phone")),
        "website": _s(obj.get("web_site")) or _s(obj.get("website")),
        "lat": lat,
        "lng": lng,
        "source_id": place_id or cid,
        "status": normalize_status(obj.get("status")),
        "source": SOURCE,
        "source_url": _s(obj.get("link")),
        "rating": _f(obj.get("review_rating")),
        "review_count": _i(obj.get("review_count")),
        "name_folded": fold_d(name),
        "address_folded": fold_d(address),
        "raw": obj,
    }


# --------------------------------------------------------------------------- io / entry point


def load_records(path: str | Path) -> list[dict]:
    """Load gosom output as a list of raw place dicts.

    Accepts JSONL (one object per line — gosom's native ``-results`` format) *or*
    a whole-file JSON array / single object.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise VnGeoError(f"file not found: {path}") from None
    except OSError as exc:
        raise VnGeoError(f"cannot read {path}: {exc}") from None

    stripped = text.lstrip()
    if not stripped:
        return []

    # Whole-file JSON (array or single object) wins; otherwise fall back to JSONL.
    if stripped[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]

    records: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VnGeoError(f"invalid JSONL in {path} (line {lineno}): {exc}") from None
        if isinstance(obj, dict):
            records.append(obj)
    return records


def save_jsonl(records: list[dict], path: str | Path) -> None:
    """Save records as JSONL (one JSON object per line, UTF-8)."""
    dest = Path(path)
    if str(dest.parent) not in ("", "."):
        dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fetch(
    area: str = "",
    politeness_s: float = 2.0,
    *,
    path: str | Path | None = None,
) -> list[dict]:
    """§3 connector entry point: gosom output → entity-schema-§1 inputs.

    gosom paces itself (runbook: ``-c 2``), so ``politeness_s`` is accepted for
    signature parity but unused here. ``area`` optionally filters records whose
    folded name/address contains the (diacritic-insensitive) area text.
    """
    del politeness_s  # gosom handles its own pacing; kept for §3 signature parity.
    records = load_records(path if path is not None else DEFAULT_RESULTS)
    out = [parse_record(rec) for rec in records]
    if area:
        needle = _match_key(area)
        out = [d for d in out if needle in _match_key(f"{d['name']} {d['address_text']}")]
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vn_geo.connectors_gosom", description=__doc__)
    parser.add_argument("--path", default=str(DEFAULT_RESULTS), help="gosom JSON/JSONL output file")
    parser.add_argument("--area", default="", help="optional area filter (e.g. 'Yên Dũng')")
    parser.add_argument("--json", action="store_true", help="print machine-readable summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        records = fetch(args.area, path=args.path)
    except VnGeoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"records": len(records), "path": args.path, "area": args.area}, ensure_ascii=False))
    else:
        print(f"parsed {len(records)} gosom records from {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
