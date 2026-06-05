from aus_trial_universe.ctgov.drug_ontology.sources.pottr.load import (
    alias_equivalence_keys,
)
from aus_trial_universe.ctgov.drug_ontology.sources.pottr.drug_classes import (
    candidate_anchor_variants,
    normalize_alias_key,
    split_aliases,
    split_direct_classes,
)


def test_split_aliases_preserves_pipe_separated_pottr_aliases():
    assert split_aliases("FOLFOX|mFOLFOX6|FOLFOX regimen") == [
        "FOLFOX",
        "mFOLFOX6",
        "FOLFOX regimen",
    ]


def test_split_direct_classes_uses_commas_and_semicolons_not_slash():
    assert split_direct_classes("class A, class B; class C/D") == [
        "class A",
        "class B",
        "class C/D",
    ]


def test_candidate_anchor_variants_preserves_precise_unsuffixed_core():
    variants = candidate_anchor_variants("fam-trastuzumab deruxtecan-nxki")

    assert variants[0] == "trastuzumab deruxtecan"
    assert "fam-trastuzumab deruxtecan-nxki" in variants


def test_normalize_alias_key_preserves_hyphenated_alias_surface():
    assert normalize_alias_key("Nivolumab-Relatlimab") == "nivolumab-relatlimab"


def test_alias_equivalence_keys_add_tokenized_punctuation_variant():
    keys = alias_equivalence_keys("Nivolumab-Relatlimab")

    assert "nivolumab-relatlimab" in keys
    assert "nivolumab relatlimab" in keys
