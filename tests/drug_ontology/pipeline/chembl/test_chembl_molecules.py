from aus_trial_universe.ctgov.drug_ontology.sources.chembl.molecules import (
    equivalence_keys,
    is_oncology_indication,
    normalize_match_key,
)


def test_normalize_match_key_strips_trademark_and_normalizes_hyphen():
    assert normalize_match_key("KEYTRUDA®") == "keytruda"
    assert normalize_match_key("trastuzumab-deruxtecan") == "trastuzumab deruxtecan"


def test_equivalence_keys_preserve_adc_payload_core():
    keys = equivalence_keys("fam-trastuzumab deruxtecan-nxki")

    assert "trastuzumab deruxtecan" in keys


def test_isotope_notation_is_canonicalized_but_not_removed():
    assert "177lu" in normalize_match_key("[177Lu]Lu-PSMA-617")
    assert "psma" in normalize_match_key("[177Lu]Lu-PSMA-617")


def test_oncology_indication_detection():
    assert is_oncology_indication("Breast Neoplasms", "")
    assert is_oncology_indication("", "lung cancer")
    assert not is_oncology_indication("Hypertension", "")
