"""Characterization tests locking the registry-distinguishing behaviour of the
molecular-signature pipeline before the ctgov/anzctr bodies are unified behind a
RegistrySpec (mirrors the gene-alteration characterization)."""

from __future__ import annotations

import pandas as pd

from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.molecular_signature import (
    molecular_signature_pipeline as ctgov_ms,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.molecular_signature import (
    molecular_signature_pipeline as anzctr_ms,
)


def test_ctgov_collapse_keeps_nct_id_column():
    out = ctgov_ms.collapse_to_trial_level(
        pd.DataFrame(
            [
                {"nct_id": "NCT00000001", "polarity": "inclusive",
                 "molecular_signature_curation": "MSI[status=HIGH]"},
                {"nct_id": "NCT00000001", "polarity": "exclusive",
                 "molecular_signature_curation": "TMB[status=LOW]"},
            ]
        )
    )
    assert "nct_id" in out.columns and "trial_id" not in out.columns
    assert len(out) == 1
    row = out.iloc[0]
    assert row["nct_id"] == "NCT00000001"
    assert row["molecular_signature_inclusive"] == "MSI[status=HIGH]"
    assert row["molecular_signature_exclusive"] == "NOT(TMB[status=LOW])"


def test_anzctr_collapse_keeps_trial_id_column():
    out = anzctr_ms.collapse_to_trial_level(
        pd.DataFrame(
            [
                {"trial_id": "ACTRN12605000000001", "polarity": "inclusive",
                 "molecular_signature_curation": "MSI[status=HIGH]"},
                {"trial_id": "ACTRN12605000000001", "polarity": "exclusive",
                 "molecular_signature_curation": "TMB[status=LOW]"},
            ]
        )
    )
    assert "trial_id" in out.columns
    assert len(out) == 1
    row = out.iloc[0]
    assert row["trial_id"] == "ACTRN12605000000001"
    assert row["molecular_signature_inclusive"] == "MSI[status=HIGH]"
    assert row["molecular_signature_exclusive"] == "NOT(TMB[status=LOW])"


def test_default_directories_are_registry_specific():
    assert "ctgov" in str(ctgov_ms.DEFAULT_CURATED_DIR)
    assert "ctgov" in str(ctgov_ms.DEFAULT_ELIGIBILITY_DATA_DIR)
    assert "anzctr" in str(anzctr_ms.DEFAULT_CURATED_DIR)
    assert "anzctr" in str(anzctr_ms.DEFAULT_ELIGIBILITY_DATA_DIR)


def test_shared_public_surface_is_present_on_both_registries():
    required = [
        "DEFAULT_CURATED_DIR", "DEFAULT_ELIGIBILITY_DATA_DIR", "DEFAULT_OUTPUT_FORMAT",
        "DEFAULT_PROCESSED_SUBDIR", "MAPPED_CRITERIA_STEM", "SUPPORTED_OUTPUT_FORMATS",
        "_output_path", "_read_tabular_file", "_resolve_path", "_write_tabular_file",
        "collapse_to_trial_level", "discover_pipeline_inputs", "_polarity_from_rule_and_not",
        "main",
    ]
    for name in required:
        assert hasattr(ctgov_ms, name), f"ctgov missing {name}"
        assert hasattr(anzctr_ms, name), f"anzctr missing {name}"
    assert hasattr(ctgov_ms, "_contains_nct_py_files")
    assert hasattr(anzctr_ms, "_contains_actrn_py_files")


def test_polarity_mapping_is_shared_and_identical():
    for mod in (ctgov_ms, anzctr_ms):
        assert mod._polarity_from_rule_and_not(rule_exclude=False, under_not_criterion=True) == "exclusive"
        assert mod._polarity_from_rule_and_not(rule_exclude=False, under_not_criterion=False) == "inclusive"
        assert mod._polarity_from_rule_and_not(rule_exclude=True, under_not_criterion=False) == "exclusive"
