"""vn_geo.connectors_masothue — polite masothue.com listing-index crawler (R13-B).

Contract: ``analysis/r13-interfaces.md`` §3 —

    def fetch(area: str, politeness_s: float = 2.0) -> list[dict]

Each returned dict matches the R13 entity schema v1 (interfaces §2): the
``name`` / ``address_text`` fields are ``fold_d``-folded (đ/Đ→d) per the R9-W2B
hard rule, and the untouched source row is preserved under ``raw``.

robots.txt (verified live 2026-10, R1 in ``analysis/vn-business-data-sources.md``):
``User-Agent: * / Allow: / / Disallow: /Ajax/*``. This module only touches the
server-rendered listing indexes under ``/tra-cuu-ma-so-thue-theo-tinh/...`` and
the public ``/Search/`` results page; ``/Ajax/*`` is never requested
(:func:`_assert_allowed` guards every URL). ≥2 s politeness between HTTP calls is
enforced by :class:`_Pacer`.

Record shape on a listing page (server-rendered HTML, single-quoted attrs)::

    <div data-prefetch='/2401011154-cong-ty-...'>
      <h3><a href='/2401011154-...' title='...'>NAME</a></h3>
      <div>
        <i class='fa fa-hashtag'></i> Mã số thuế: <a href='/...'>2401011154</a><br />
        <i class='fa fa-user'></i> Người đại diện: <em><a href='...'>REP</a></em>
      </div>
      <address><i class='fa fa-map-marker'></i>  ADDRESS</address>
    </div>
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from searchstore.store import fold_d

from . import VnGeoError

BASE = "https://masothue.com"
USER_AGENT = "hermes-vn-geo/0.1"
PROVINCE_INDEX_PATH = "/tra-cuu-ma-so-thue-theo-tinh/"
SEARCH_PATH = "/Search/"
SOURCE = "masothue"
DISALLOWED_PREFIX = "/Ajax/"
TTL_CLASS = "poi"
DEFAULT_TIMEOUT = 30.0

# Province listing slugs (discovered live from the province index, 2026-10).
PROVINCES: dict[str, str] = {
    "bac giang": "bac-giang-72",
    "bac ninh": "bac-ninh-170",
    "hai phong": "hai-phong-99",
    "tay ninh": "tay-ninh-90",
}

# Human display names for the province slugs above.
PROVINCE_NAMES: dict[str, str] = {
    "bac-giang-72": "Bắc Giang",
    "bac-ninh-170": "Bắc Ninh",
    "hai-phong-99": "Hải Phòng",
    "tay-ninh-90": "Tây Ninh",
}

# District listing slugs keyed by province slug (discovered live from the
# province page — e.g. Bắc Giang links ``/…/huyen-yen-dung-2119``).
DISTRICTS: dict[str, dict[str, str]] = {
    "bac-giang-72": {
        "yen dung": "huyen-yen-dung-2119",
    },
}

# --------------------------------------------------------------------------- regex

_RECORD_RE = re.compile(r"<div data-prefetch='/(?P<path>[^']+)'>(?P<body>.*?</address>)", re.S)
_NAME_RE = re.compile(r"<h3[^>]*>\s*<a[^>]*>(?P<name>.*?)</a>", re.S)
_TAX_RE = re.compile(r"Mã số thuế:\s*<a[^>]*>(?P<tax>[^<]+)</a>")
_REP_RE = re.compile(r"Người đại diện:\s*<em>\s*<a[^>]*>(?P<rep>.*?)</a>", re.S)
_ADDR_RE = re.compile(r"<address>\s*(?:<i[^>]*>\s*</i>)?\s*(?P<addr>.*?)</address>", re.S)
_LINK_RE = re.compile(r"href='/tra-cuu-ma-so-thue-theo-tinh/([a-z0-9-]+)'>([^<]+)</a>")
_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- helpers


def _strip_tags(value: str) -> str:
    """Collapse tags + whitespace and unescape entities in a fragment."""
    text = _TAG_RE.sub(" ", value or "")
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fold_text(value) -> str:
    """Diacritic/đ-folded, lowercased, single-spaced key (for matching)."""
    s = fold_d(str(value or ""))
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _entity_id(source: str, source_id: str) -> str:
    digest = hashlib.sha1(f"{source}|{source_id}".encode(), usedforsecurity=False).hexdigest()
    return f"e_{digest[:12]}"


def _assert_allowed(url: str) -> str:
    """Raise if *url* targets the robots-disallowed ``/Ajax/*`` tree."""
    path = urllib.parse.urlsplit(url).path
    if path.startswith(DISALLOWED_PREFIX):
        raise VnGeoError(f"robots.txt forbids {DISALLOWED_PREFIX}* — refusing to fetch {url}")
    return url


def _http_get(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> tuple[str, int, bytes]:
    """GET *url* politely; return ``(final_url, status, body_bytes)``."""
    _assert_allowed(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - https module constant
            return resp.geturl(), resp.status, resp.read()
    except urllib.error.HTTPError as e:  # 4xx/5xx still carries a status + (empty) body
        return e.geturl() or url, e.code, e.read() or b""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise VnGeoError(f"masothue GET {url} failed: {e}") from e


class _Pacer:
    """Wraps a getter so every call after the first sleeps ``politeness_s``.

    Injected in tests to assert politeness without real sleeping.
    """

    def __init__(self, getter, sleeper, politeness_s: float):
        self._getter = getter
        self._sleeper = sleeper
        self._politeness = politeness_s
        self.calls = 0

    def get(self, url: str) -> tuple[str, int, bytes]:
        if self.calls:
            self._sleeper(self._politeness)
        self.calls += 1
        return self._getter(url)


# --------------------------------------------------------------------------- parsing


def _group(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    if m is None:
        return ""
    return _strip_tags(m.group(m.lastindex or 1))


def parse_listing(html: str) -> list[dict]:
    """Parse a server-rendered listing page into raw record dicts.

    Output keys: ``name`` / ``tax_code`` / ``representative`` / ``address`` /
    ``source_url`` / ``source_id``. Records without a name *and* tax code are
    skipped. Parsing is regex-based (no third-party HTML parser).
    """
    out: list[dict] = []
    for m in _RECORD_RE.finditer(html or ""):
        body = m.group("body")
        path = m.group("path")
        name = _group(_NAME_RE, body)
        tax = _group(_TAX_RE, body)
        if not name and not tax:
            continue
        out.append(
            {
                "name": name,
                "tax_code": tax,
                "representative": _group(_REP_RE, body),
                "address": _group(_ADDR_RE, body),
                "source_url": f"{BASE}/{path}",
                "source_id": tax or path,
            }
        )
    return out


def _iter_ld_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_ld_nodes(item)
    elif isinstance(data, dict):
        if isinstance(data.get("@graph"), list):
            for item in data["@graph"]:
                yield from _iter_ld_nodes(item)
        if isinstance(data.get("itemListElement"), list):
            for item in data["itemListElement"]:
                node = item.get("item") if isinstance(item, dict) and "item" in item else item
                yield from _iter_ld_nodes(node)
        yield data


def parse_jsonld(html: str) -> list[dict]:
    """Extract record-like nodes from ``application/ld+json`` blocks.

    Listing pages carry site-level JSON-LD (WebSite/Organization) only, so this
    yields ``[]`` there; it exists for the R13 contract ("parse … + JSON-LD")
    and handles ``@graph`` / ``itemListElement`` / Organization nodes that do
    carry ``name`` + ``address``.
    """
    out: list[dict] = []
    for m in _LDJSON_RE.finditer(html or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for node in _iter_ld_nodes(data):
            if not isinstance(node, dict):
                continue
            name = node.get("name")
            addr = node.get("address")
            if isinstance(addr, dict):
                addr = ", ".join(str(v) for v in addr.values() if v)
            if not isinstance(name, str) or not name.strip() or not isinstance(addr, str) or not addr.strip():
                continue
            tax = node.get("taxID") or node.get("tax_code") or ""
            out.append(
                {
                    "name": _strip_tags(name),
                    "tax_code": _strip_tags(str(tax)),
                    "representative": "",
                    "address": _strip_tags(addr),
                    "source_url": str(node.get("url") or ""),
                    "source_id": _strip_tags(str(tax)) or _strip_tags(name),
                }
            )
    return out


def parse_page(html: str) -> list[dict]:
    """Merge listing-table records with any JSON-LD records (dedupe by source_id)."""
    merged: dict[str, dict] = {}
    for rec in parse_listing(html) + parse_jsonld(html):
        merged.setdefault(rec["source_id"], rec)
    return list(merged.values())


def parse_area_links(html: str) -> dict[str, str]:
    """Map folded area names → listing slugs from ``/tra-cuu-…-theo-tinh/<slug>`` links."""
    out: dict[str, str] = {}
    for slug, label in _LINK_RE.findall(html or ""):
        name = _strip_tags(label)
        if name:
            out.setdefault(_fold_text(name), slug)
    return out


# --------------------------------------------------------------------------- shaping


def to_entity(record: dict, *, area: str = "", province: str = "") -> dict:
    """Shape a raw parsed record into an entity-schema-v1 dict (interfaces §2)."""
    name = record.get("name") or ""
    address = record.get("address") or ""
    tax = str(record.get("tax_code") or "").strip()
    source_id = tax or str(record.get("source_id") or "").strip() or _fold_text(name)
    now = _iso_now()
    return {
        "entity_id": _entity_id(SOURCE, source_id),
        "name": fold_d(name),
        "kind": "other",
        "category_raw": "",
        "category": "other",
        "cat_confidence": 0.0,
        "tax_code": tax,
        "address_text": fold_d(address),
        "area_old": area,
        "province": province,
        "lat": None,
        "lng": None,
        "phone": "",
        "website": "",
        "source": SOURCE,
        "source_url": record.get("source_url", ""),
        "source_id": source_id,
        "status": "unknown",
        "rating": None,
        "review_count": None,
        "first_seen": now,
        "last_seen": now,
        "checked_at": now,
        "ttl_class": TTL_CLASS,
        "confidence": 0.7,
        "raw": dict(record),
    }


def _province_label(slug: str, links: dict[str, str]) -> str:
    if slug in PROVINCE_NAMES:
        return PROVINCE_NAMES[slug]
    for name, s in links.items():
        if s == slug:
            return name
    return slug


# --------------------------------------------------------------------------- resolution


def resolve_area(area: str, pacer: _Pacer) -> tuple[str, str, str]:
    """Resolve *area* → ``(listing_slug, province_label, scope)``.

    ``scope`` is ``"district"`` or ``"province"``. Direct hits on :data:`DISTRICTS`
    / :data:`PROVINCES` avoid any network call; otherwise the province index is
    fetched once to match a province name.
    """
    folded = _fold_text(area)
    if not folded:
        raise VnGeoError("area must be a non-empty string")
    for province_slug, dists in DISTRICTS.items():
        if folded in dists:
            return dists[folded], _province_label(province_slug, {}), "district"
    if folded in PROVINCES:
        return PROVINCES[folded], area, "province"
    _, status, body = pacer.get(f"{BASE}{PROVINCE_INDEX_PATH}")
    if status != 200:
        raise VnGeoError(f"masothue province index returned HTTP {status}")
    links = parse_area_links(body.decode("utf-8", "replace"))
    if folded in links:
        return links[folded], area, "province"
    raise VnGeoError(f"could not resolve area {area!r} to a masothue listing (known districts: {_known_districts()})")


def _known_districts() -> list[str]:
    return sorted(name for dists in DISTRICTS.values() for name in dists)


def _listing_url(slug: str, page: int) -> str:
    url = f"{BASE}{PROVINCE_INDEX_PATH}{slug}"
    return url if page <= 1 else f"{url}?page={page}"


# --------------------------------------------------------------------------- fetch


def fetch(
    area: str,
    politeness_s: float = 2.0,
    *,
    max_pages: int = 1,
    getter=None,
    sleeper=None,
) -> list[dict]:
    """Crawl masothue listing index pages for *area*; return §1-shaped dicts.

    ``max_pages`` bounds listing pages (``?page=2`` …). ``getter`` / ``sleeper``
    are injectable for hermetic tests. Every HTTP call after the first sleeps
    ``politeness_s`` seconds (default 2.0). Records are deduped by tax code.
    """
    if politeness_s < 0:
        raise VnGeoError(f"politeness_s must be >= 0, got {politeness_s!r}")
    if max_pages < 1:
        raise VnGeoError(f"max_pages must be >= 1, got {max_pages!r}")
    pacer = _Pacer(getter or _http_get, sleeper or time.sleep, politeness_s)
    slug, province, _scope = resolve_area(area, pacer)
    seen: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        _, status, body = pacer.get(_listing_url(slug, page))
        if status != 200:
            break
        for rec in parse_page(body.decode("utf-8", "replace")):
            seen.setdefault(rec["source_id"], rec)
    return [to_entity(rec, area=area, province=province) for rec in seen.values()]


def search(
    keyword: str,
    area: str = "",
    politeness_s: float = 2.0,
    *,
    getter=None,
    sleeper=None,
) -> list[dict]:
    """Query the public ``/Search/`` results page; return §1-shaped dicts.

    ``area`` (optional) scopes results via the ``city=<province-id>`` filter.
    """
    if not _fold_text(keyword):
        raise VnGeoError("keyword must be a non-empty string")
    pacer = _Pacer(getter or _http_get, sleeper or time.sleep, politeness_s)
    province = ""
    params = {"q": keyword, "type": "auto"}
    if area:
        slug, province, _ = resolve_area(area, pacer)
        params["city"] = slug.rsplit("-", 1)[-1]
    url = f"{BASE}{SEARCH_PATH}?{urllib.parse.urlencode(params)}"
    _, status, body = pacer.get(url)
    if status != 200:
        raise VnGeoError(f"masothue search returned HTTP {status} for {keyword!r}")
    seen: dict[str, dict] = {}
    for rec in parse_page(body.decode("utf-8", "replace")):
        seen.setdefault(rec["source_id"], rec)
    return [to_entity(rec, area=area, province=province) for rec in seen.values()]


if __name__ == "__main__":  # pragma: no cover - manual helper
    import sys

    _area = sys.argv[1] if len(sys.argv) > 1 else "Yên Dũng"
    for _e in fetch(_area, max_pages=1):
        print(f"{_e['tax_code'] or '-':<14} {_e['name']} — {_e['address_text']}")
