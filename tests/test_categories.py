"""Tests for vn_geo.categories (R13-A; contract analysis/r13-interfaces.md §3).

Hermetic. The shipped data/categories.yaml is exercised when present; the
embedded _DEFAULT_RULES mirror is asserted identical so gitignored data/ can
never drift from the module.
"""

from pathlib import Path

import pytest

from vn_geo import VnGeoError, categories

RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "categories.yaml"


@pytest.fixture(autouse=True)
def _default_rules_around_tests():
    yield
    categories.reset_rules()


# ---------- classify (acceptance cases) ----------


def test_classify_nha_nghi_bao_an():
    cat, conf = categories.classify("Nhà nghỉ Bảo An", "")
    assert cat == "lodging_budget"
    assert conf == pytest.approx(0.9)


def test_classify_com_tam_286():
    cat, conf = categories.classify("Cơm tấm 286", "")
    assert cat == "food"
    assert conf == pytest.approx(0.9)


def test_classify_unaccented_inputs_match():
    # fold_d rule: no-accent keywords/names hit accented rules either way.
    assert categories.classify("nha nghi bao an", "")[0] == "lodging_budget"
    assert categories.classify("com tam 286", "")[0] == "food"
    assert categories.classify("Nha Tro Sinh Vien", "")[0] == "lodging_budget"


def test_classify_accented_and_mixed_case():
    assert categories.classify("KHÁCH SẠN THÁI BÌNH", "")[0] == "lodging_hotel"
    assert categories.classify("Khách sạn An Bình", "")[0] == "lodging_hotel"
    assert categories.classify("Quán ăn gia đình", "")[0] == "food"


def test_classify_name_only_match():
    cat, conf = categories.classify("", "Cafe Mộc Xanh")
    assert cat == "food_drink"
    assert conf == pytest.approx(0.75)


def test_classify_category_beats_name():
    # Name has a lodging word, but the raw category wins.
    assert categories.classify("Quán ăn", "Nhà nghỉ Cơm Rang")[0] == "food"


def test_classify_gmaps_english_categories():
    assert categories.classify("Guest house", "")[0] == "lodging_budget"
    assert categories.classify("Restaurant", "")[0] == "food"
    assert categories.classify("Coffee shop", "")[0] == "food_drink"
    assert categories.classify("Pharmacy", "")[0] == "service_health"


def test_classify_fallback_other_low_confidence():
    cat, conf = categories.classify("zzz unknown thing", "")
    assert cat == "other"
    assert conf < 0.5


def test_classify_empty_input_is_other():
    assert categories.classify("", "") == ("other", pytest.approx(0.2))


def test_classify_word_boundary_no_substring_false_positive():
    # "com" (cơm) must not fire inside "computer".
    assert categories.classify("Computer parts", "")[0] != "food"


def test_classify_identity_cat_id_passthrough():
    # A value that already is a cat_id survives a second classify round.
    assert categories.classify("lodging_budget", "") == ("lodging_budget", pytest.approx(0.95))


# ---------- kind_of / fold_text ----------


@pytest.mark.parametrize(
    ("cat_id", "kind"),
    [
        ("lodging_budget", "lodging"),
        ("lodging_hotel", "lodging"),
        ("food", "food"),
        ("food_drink", "food"),
        ("store_retail", "store"),
        ("service_health", "service"),
        ("other", "other"),
        ("bogus_cat", "other"),
    ],
)
def test_kind_of(cat_id, kind):
    assert categories.kind_of(cat_id) == kind


def test_fold_text_strips_accents_and_d():
    assert categories.fold_text("Nhà nghỉ Đà Nẵng") == "nha nghi da nang"
    assert categories.fold_text("  Quán   PHỞ ") == "quan pho"


# ---------- load_rules ----------


def test_load_rules_custom_file(tmp_path):
    custom = tmp_path / "cats.yaml"
    custom.write_text(
        "my_cat:\n  keywords: [bánh giò]\n  synonyms: [banh gio]\nother:\n  keywords: []\n",
        encoding="utf-8",
    )
    categories.load_rules(str(custom))
    assert categories.classify("Bánh giò Tý", "") == ("my_cat", pytest.approx(0.9))
    assert categories.known_categories() == {"my_cat", "other"}


def test_load_rules_missing_file(tmp_path):
    with pytest.raises(VnGeoError, match="cannot read"):
        categories.load_rules(str(tmp_path / "nope.yaml"))


def test_load_rules_bad_shape(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(VnGeoError):
        categories.load_rules(str(bad))


def test_load_rules_block_list_shape(tmp_path):
    custom = tmp_path / "block.yaml"
    custom.write_text(
        "demo:\n  keywords:\n    - xe ôm\n    - xe om\n  synonyms: []\n",
        encoding="utf-8",
    )
    categories.load_rules(str(custom))
    assert categories.classify("", "Xe ôm Hùng") == ("demo", pytest.approx(0.75))


def test_minimal_yaml_fallback_parser():
    text = "a_cat:\n  keywords: [one, two]\n  synonyms:\n    - three\nother:\n  keywords: []\n"
    parsed = categories._parse_minimal_yaml(text)
    assert parsed == {
        "a_cat": {"keywords": ["one", "two"], "synonyms": ["three"]},
        "other": {"keywords": [], "synonyms": []},
    }


def test_minimal_parser_rejects_garbage():
    with pytest.raises(VnGeoError):
        categories._parse_minimal_yaml("not valid: [unclosed\n  !!!")


@pytest.mark.skipif(not RULES_PATH.exists(), reason="data/categories.yaml absent on this checkout")
def test_shipped_yaml_matches_embedded_defaults():
    """Drift guard: the gitignored file and _DEFAULT_RULES must be identical."""
    loaded = categories._load_rule_data(RULES_PATH)
    assert loaded == categories._DEFAULT_RULES


def test_default_ruleset_activates_without_load():
    # Fresh interpreter state: classify works off defaults with no load_rules.
    categories.reset_rules()
    assert categories.classify("nhà nghỉ x", "")[0] == "lodging_budget"
    assert "lodging_budget" in categories.known_categories()
