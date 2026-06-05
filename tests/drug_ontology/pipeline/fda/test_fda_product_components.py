from aus_trial_universe.ctgov.drug_ontology.sources.fda.product_components import (
    candidate_anchor_variants,
    normalize_fda_ingredient_text,
    remove_fda_prefix_and_suffix,
    split_top_level_semicolons,
)


def test_split_top_level_semicolons_does_not_split_parenthesized_group():
    value = "TRIPLE SULFA (SULFABENZAMIDE;SULFACETAMIDE;SULFATHIAZOLE); ASPIRIN"

    assert split_top_level_semicolons(value) == [
        "TRIPLE SULFA (SULFABENZAMIDE;SULFACETAMIDE;SULFATHIAZOLE)",
        "ASPIRIN",
    ]


def test_remove_fda_prefix_and_suffix_preserves_active_core():
    assert remove_fda_prefix_and_suffix("fam-trastuzumab deruxtecan-nxki") == "trastuzumab deruxtecan"
    assert remove_fda_prefix_and_suffix("ado-trastuzumab emtansine") == "trastuzumab emtansine"


def test_candidate_anchor_variants_prefers_precise_unsuffixed_core():
    variants = candidate_anchor_variants("fam-trastuzumab deruxtecan-nxki")

    assert variants[0] == "trastuzumab deruxtecan"
    assert "fam-trastuzumab deruxtecan-nxki" in variants


def test_normalize_fda_ingredient_text_is_lowercase_and_trimmed():
    assert normalize_fda_ingredient_text("  ERLOTINIB HYDROCHLORIDE  ") == "erlotinib hydrochloride"
