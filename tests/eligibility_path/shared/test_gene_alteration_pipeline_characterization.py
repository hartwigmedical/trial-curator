"""Characterization tests locking the registry-distinguishing behaviour of the
gene-alteration pipeline before the ctgov/anzctr bodies are unified behind a
RegistrySpec.

These pin exactly what differs between registries — the trial-id output column
and the default directories — plus the shared trial-level aggregation, so the
refactor can be proven behaviour-preserving.
"""

from __future__ import annotations

import pandas as pd

from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.gene_alterations import (
    gene_alteration_pipeline as ctgov_ga,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.gene_alterations import (
    gene_alteration_pipeline as anzctr_ga,
)


def test_ctgov_collapse_keeps_nct_id_column():
    out = ctgov_ga.collapse_to_trial_level(
        pd.DataFrame(
            [
                {"nct_id": "NCT00000001", "polarity": "inclusive",
                 "gene_alteration_curation": "SmallVariant[gene=KRAS]"},
                {"nct_id": "NCT00000001", "polarity": "exclusive",
                 "gene_alteration_curation": "SmallVariant[gene=TP53]"},
            ]
        )
    )
    assert "nct_id" in out.columns
    assert "trial_id" not in out.columns
    assert len(out) == 1
    row = out.iloc[0]
    assert row["nct_id"] == "NCT00000001"
    assert row["gene_alteration_inclusive"] == "SmallVariant[gene=KRAS]"
    assert row["gene_alteration_exclusive"] == "NOT(SmallVariant[gene=TP53])"


def test_anzctr_collapse_keeps_trial_id_column():
    out = anzctr_ga.collapse_to_trial_level(
        pd.DataFrame(
            [
                {"trial_id": "ACTRN12605000000001", "polarity": "inclusive",
                 "gene_alteration_curation": "SmallVariant[gene=KRAS]"},
                {"trial_id": "ACTRN12605000000001", "polarity": "exclusive",
                 "gene_alteration_curation": "SmallVariant[gene=TP53]"},
            ]
        )
    )
    assert "trial_id" in out.columns
    assert len(out) == 1
    row = out.iloc[0]
    assert row["trial_id"] == "ACTRN12605000000001"
    assert row["gene_alteration_inclusive"] == "SmallVariant[gene=KRAS]"
    assert row["gene_alteration_exclusive"] == "NOT(SmallVariant[gene=TP53])"


def test_default_directories_are_registry_specific():
    assert "ctgov" in str(ctgov_ga.DEFAULT_CURATED_DIR)
    assert "ctgov" in str(ctgov_ga.DEFAULT_ELIGIBILITY_DATA_DIR)
    assert "anzctr" in str(anzctr_ga.DEFAULT_CURATED_DIR)
    assert "anzctr" in str(anzctr_ga.DEFAULT_ELIGIBILITY_DATA_DIR)


def test_shared_public_surface_is_present_on_both_registries():
    # Symbols imported by cohort_level_gene_alteration + tests must survive the refactor.
    required = [
        "DEFAULT_CURATED_DIR", "DEFAULT_ELIGIBILITY_DATA_DIR", "DEFAULT_OUTPUT_FORMAT",
        "DEFAULT_PROCESSED_SUBDIR", "MAPPED_CRITERIA_STEM", "SUPPORTED_OUTPUT_FORMATS",
        "_output_path", "_read_tabular_file", "_resolve_path", "_write_tabular_file",
        "collapse_to_trial_level", "discover_pipeline_inputs", "_polarity_from_rule_and_not",
        "main",
    ]
    for name in required:
        assert hasattr(ctgov_ga, name), f"ctgov missing {name}"
        assert hasattr(anzctr_ga, name), f"anzctr missing {name}"
    assert hasattr(ctgov_ga, "_contains_nct_py_files")
    assert hasattr(anzctr_ga, "_contains_actrn_py_files")


def test_polarity_mapping_is_shared_and_identical():
    for mod in (ctgov_ga, anzctr_ga):
        assert mod._polarity_from_rule_and_not(rule_exclude=False, under_not_criterion=True) == "exclusive"
        assert mod._polarity_from_rule_and_not(rule_exclude=False, under_not_criterion=False) == "inclusive"
        assert mod._polarity_from_rule_and_not(rule_exclude=True, under_not_criterion=True) == "exclusive"
