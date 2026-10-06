"""vn_geo.categories — VN canonical business category rules (R13-A).

Contract: ``analysis/r13-interfaces.md`` §3::

    classify(category_raw: str, name: str) -> tuple[str, float]
    load_rules(path: str) -> None

Rules live in ``data/categories.yaml`` (``cat_id -> {keywords, synonyms}``).
The entity ``kind`` is the cat_id prefix before the first ``_``
(``lodging_budget`` -> ``lodging``); unmatched input falls back to ``other``
at low confidence.

Matching folds đ/Đ -> d, strips diacritics and casefolds both keywords and
input (superset of ``searchstore.store.fold_d``, R9-W2B), so unaccented
keywords match accented names and vice versa. A keyword only matches on
whole words — ``com`` fires on "Cơm tấm 286" but not inside "computer".

``data/`` is gitignored, so the shipped file may be absent on a fresh
checkout: ``_DEFAULT_RULES`` embeds the same ruleset and is used as the
fallback. When PyYAML is importable the file is parsed with it; otherwise a
restricted line parser handles the file's own shape (``key:`` headers plus
``key: [a, b]`` / ``- item`` lists) — the hermes runtime has no PyYAML.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import VnGeoError

try:
    import yaml
except ImportError:  # pragma: no cover - exercised indirectly via fallback parser tests
    yaml = None

KINDS = frozenset({"store", "lodging", "food", "service", "other"})
FALLBACK_CATEGORY = "other"
_CONF_RAW_MATCH = 0.9
_CONF_NAME_MATCH = 0.75
_CONF_IDENTITY = 0.95
_CONF_FALLBACK = 0.2

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "categories.yaml"

# Embedded mirror of data/categories.yaml (the file is the editable copy;
# tests/test_categories.py asserts they stay identical). Order matters: the
# first matching category wins, so keep specific cats above generic ones.
_DEFAULT_RULES: dict[str, dict] = {
    "lodging_budget": {
        "keywords": [
            "nhà nghỉ",
            "nha nghi",
            "nhà trọ",
            "nha tro",
            "phòng trọ",
            "phong tro",
            "phòng cho thuê",
            "phong cho thue",
            "khu trọ",
            "motel",
            "homestay",
            "hostel",
            "guest house",
            "guesthouse",
        ],
        "synonyms": ["nha nghi binh dan", "nhà nghỉ bình dân"],
    },
    "lodging_hotel": {
        "keywords": ["khách sạn", "khach san", "hotel", "resort", "condotel", "khu nghỉ dưỡng", "villa"],
        "synonyms": ["khach san mini"],
    },
    "food": {
        "keywords": [
            "quán ăn",
            "quan an",
            "quán cơm",
            "quan com",
            "cơm tấm",
            "com tam",
            "cơm phần",
            "com phan",
            "cơm gà",
            "com ga",
            "cơm",
            "com",
            "phở",
            "pho",
            "bún chả",
            "bun cha",
            "bún",
            "bun",
            "hủ tiếu",
            "hu tieu",
            "mì quảng",
            "mi quang",
            "quán bún",
            "quan bun",
            "nhà hàng",
            "nha hang",
            "restaurant",
            "quán nhậu",
            "quan nhau",
            "lẩu",
            "lau",
            "cháo",
            "chao",
            "bánh mì",
            "banh mi",
            "bánh xèo",
            "banh xeo",
            "gà rán",
            "ga ran",
            "đồ ăn",
            "do an",
            "tiệm bánh",
            "tiem banh",
            "bakery",
        ],
        "synonyms": ["quan an", "nha hang"],
    },
    "food_drink": {
        "keywords": [
            "cà phê",
            "ca phe",
            "cafe",
            "coffee",
            "coffee shop",
            "trà sữa",
            "tra sua",
            "milk tea",
            "trà chanh",
            "tra chanh",
            "sinh tố",
            "sinh to",
            "nước ép",
            "nuoc ep",
            "quán nước",
            "quan nuoc",
            "bia hơi",
            "bia hoi",
            "bar",
            "pub",
            "juice",
        ],
        "synonyms": ["quan ca phe"],
    },
    "store_retail": {
        "keywords": [
            "cửa hàng",
            "cua hang",
            "shop",
            "tạp hóa",
            "tap hoa",
            "siêu thị",
            "sieu thi",
            "minimart",
            "bách hóa",
            "bach hoa",
            "cửa hàng tiện lợi",
            "cua hang tien loi",
            "supermarket",
            "convenience store",
            "grocery",
            "mart",
        ],
        "synonyms": ["tap hoa", "cua hang"],
    },
    "store_electronics": {
        "keywords": [
            "điện thoại",
            "dien thoai",
            "điện tử",
            "dien tu",
            "điện máy",
            "dien may",
            "điện lạnh",
            "dien lanh",
            "đtdđ",
            "vi tính",
            "vi tinh",
            "máy tính",
            "may tinh",
            "electronics",
            "phone store",
        ],
        "synonyms": ["dien thoai", "dien may"],
    },
    "store_fashion": {
        "keywords": [
            "thời trang",
            "thoi trang",
            "quần áo",
            "quan ao",
            "áo quần",
            "ao quan",
            "giày dép",
            "giay dep",
            "clothing",
            "fashion",
            "boutique",
        ],
        "synonyms": ["quan ao"],
    },
    "service_beauty": {
        "keywords": [
            "salon",
            "spa",
            "cắt tóc",
            "cat toc",
            "barber",
            "nail",
            "làm đẹp",
            "lam dep",
            "thẩm mỹ",
            "tham my",
            "gội đầu",
            "goi dau",
            "massage",
            "beauty",
        ],
        "synonyms": ["cat toc"],
    },
    "service_repair": {
        "keywords": [
            "sửa chữa",
            "sua chua",
            "sửa xe",
            "sua xe",
            "sửa máy",
            "sua may",
            "sửa điện thoại",
            "sua dien thoai",
            "bảo hành",
            "bao hanh",
            "rửa xe",
            "rua xe",
            "vá xe",
            "garage",
            "car repair",
            "repair",
            "cứu hộ",
            "cuu ho",
        ],
        "synonyms": ["sua chua"],
    },
    "service_health": {
        "keywords": [
            "phòng khám",
            "phong kham",
            "nhà thuốc",
            "nha thuoc",
            "pharmacy",
            "bệnh viện",
            "benh vien",
            "nha khoa",
            "phòng mạch",
            "phong mach",
            "y tế",
            "y te",
            "clinic",
            "hospital",
            "dental",
        ],
        "synonyms": ["nha thuoc", "phong kham"],
    },
    "service_finance": {
        "keywords": [
            "ngân hàng",
            "ngan hang",
            "bank",
            "atm",
            "cầm đồ",
            "cam do",
            "tiệm vàng",
            "tiem vang",
            "pawnbroker",
        ],
        "synonyms": ["ngan hang"],
    },
    "service_laundry": {
        "keywords": ["giặt là", "giat la", "giặt ủi", "giat ui", "giặt khô", "giat kho", "laundry"],
        "synonyms": ["giat la"],
    },
    "service_transport": {
        "keywords": [
            "vận tải",
            "van tai",
            "xe khách",
            "xe khach",
            "bến xe",
            "ben xe",
            "gửi xe",
            "gui xe",
            "bãi xe",
            "bai xe",
            "taxi",
            "logistics",
        ],
        "synonyms": ["van tai"],
    },
    "service_print": {
        "keywords": ["photocopy", "in ấn", "in an", "chụp ảnh", "chup anh"],
        "synonyms": ["in an"],
    },
    "other": {"keywords": [], "synonyms": []},
}

_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[0-9a-z]+")

# Active ruleset: None -> resolved lazily from _DEFAULT_PATH / _DEFAULT_RULES.
_rules: dict[str, dict] | None = None
_compiled: list[tuple[str, list[re.Pattern]]] | None = None
_known_cat_ids: frozenset[str] | None = None


# --------------------------------------------------------------------------- folding


def fold_text(text: str) -> str:
    """Full unaccent fold: đ/Đ -> d, NFD diacritic strip, casefold, whitespace
    collapse. Superset of ``searchstore.store.fold_d`` (which only folds đ)."""
    s = str(text).replace("đ", "d").replace("Đ", "d")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return _WS_RE.sub(" ", s).casefold().strip()


def _keyword_re(keyword: str) -> re.Pattern:
    """Whole-word regex over folded text (re.escape keeps multiword gaps)."""
    return re.compile(r"(?<![0-9a-z])" + re.escape(fold_text(keyword)) + r"(?![0-9a-z])")


# --------------------------------------------------------------------------- rule loading


def _parse_minimal_yaml(text: str) -> dict:
    """Restricted YAML-subset parser for the categories file's own shape.

    Supports ``# comments``, ``key:`` headers, ``key: [a, b]`` inline lists and
    ``key:`` + ``- item`` block lists (indented under a key). Anything else
    raises VnGeoError — PyYAML is used instead when it is installed.
    """
    data: dict[str, dict] = {}
    current: str | None = None
    list_key: str | None = None
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip() if not raw_line.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("-") and current is not None and list_key is not None:
            item = stripped[1:].strip().strip('"').strip("'")
            if item:
                data[current][list_key].append(item)
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", stripped)
        if m is None:
            raise VnGeoError(f"categories yaml:{lineno}: cannot parse line {raw_line!r}")
        key, value = m.group(1), m.group(2).strip()
        if not raw_line.startswith((" ", "\t")):
            current = key
            data[current] = {"keywords": [], "synonyms": []}
            list_key = None
            if value:
                raise VnGeoError(f"categories yaml:{lineno}: unexpected value after category {key!r}")
            continue
        if current is None:
            raise VnGeoError(f"categories yaml:{lineno}: list entry before any category")
        list_key = key
        if not value:
            continue
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]
            data[current][list_key] = [v for v in items if v]
        else:
            data[current][list_key] = [value.strip('"').strip("'")]
    return data


def _load_rule_data(path) -> dict[str, dict]:
    """Read a categories file into ``{cat_id: {"keywords": [...], "synonyms": [...]}}``."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise VnGeoError(f"cannot read categories file {path}: {e}") from e
    data = yaml.safe_load(text) if yaml is not None else _parse_minimal_yaml(text)
    if not isinstance(data, dict):
        raise VnGeoError(f"categories file {path}: expected a mapping of cat_id -> rules")
    rules: dict[str, dict] = {}
    for cat_id, spec in data.items():
        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            raise VnGeoError(f"categories file {path}: {cat_id!r} must map to {{keywords, synonyms}}")
        rules[str(cat_id)] = {
            "keywords": [str(k) for k in (spec.get("keywords") or [])],
            "synonyms": [str(k) for k in (spec.get("synonyms") or [])],
        }
    return rules


def _compile(rules: dict[str, dict]) -> list[tuple[str, list[re.Pattern]]]:
    out: list[tuple[str, list[re.Pattern]]] = []
    for cat_id, spec in rules.items():
        regexes = [
            _keyword_re(kw)
            for kw in (list(spec.get("keywords") or []) + list(spec.get("synonyms") or []))
            if fold_text(str(kw))
        ]
        out.append((cat_id, regexes))
    return out


def _active() -> list[tuple[str, list[re.Pattern]]]:
    """Compiled rules: explicit load_rules() else the default file else embedded."""
    global _compiled, _known_cat_ids
    if _compiled is None:
        rules = _rules
        if rules is None:
            rules = _load_rule_data(_DEFAULT_PATH) if _DEFAULT_PATH.exists() else _DEFAULT_RULES
        _compiled = _compile(rules)
        _known_cat_ids = frozenset(rules)
    return _compiled


def load_rules(path: str) -> None:
    """Load ``cat_id -> {keywords, synonyms}`` rules from a YAML file.

    Contract §3. Replaces the active ruleset; raises VnGeoError on unreadable
    or malformed files.
    """
    global _rules, _compiled, _known_cat_ids
    _rules = _load_rule_data(path)
    _compiled = _compile(_rules)
    _known_cat_ids = frozenset(_rules)


def reset_rules() -> None:
    """Restore the default ruleset (default file or embedded mirror)."""
    global _rules, _compiled, _known_cat_ids
    _rules = None
    _compiled = None
    _known_cat_ids = None


def known_categories() -> frozenset[str]:
    """Cat_ids in the active ruleset."""
    _active()
    return frozenset(_known_cat_ids or frozenset())


def kind_of(cat_id: str) -> str:
    """Entity kind for a cat_id: the prefix before the first ``_``."""
    head = str(cat_id).split("_", 1)[0]
    return head if head in KINDS else FALLBACK_CATEGORY


# --------------------------------------------------------------------------- classify


def classify(category_raw: str, name: str) -> tuple[str, float]:
    """Map a raw category/name pair to ``(cat_id, confidence)`` — contract §3.

    Pass 1 matches folded keywords against ``category_raw`` (confidence 0.9);
    pass 2 against ``name`` (0.75). A category_raw that already *is* a cat_id
    returns it at 0.95 so normalized entities survive a second classify.
    Fallback is ``other`` at 0.2.
    """
    rules = _active()
    cat_text = fold_text(category_raw or "")
    name_text = fold_text(name or "")
    if cat_text and cat_text in (_known_cat_ids or frozenset()):
        return cat_text, _CONF_IDENTITY
    for cat_id, regexes in rules:
        if cat_text and any(rx.search(cat_text) for rx in regexes):
            return cat_id, _CONF_RAW_MATCH
    for cat_id, regexes in rules:
        if name_text and any(rx.search(name_text) for rx in regexes):
            return cat_id, _CONF_NAME_MATCH
    return FALLBACK_CATEGORY, _CONF_FALLBACK
