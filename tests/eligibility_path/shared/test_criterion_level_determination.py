from __future__ import annotations

import pandas as pd

from aus_trial_universe.eligibility_path.shared.cancer_types.final_determination.criterion_level_determination import (
    compute_final_cancer_1_for_row,
)


def base_row(**overrides: object) -> pd.Series:
    values = {
        "primary_tumor_oncotree_curation_simplified": "Ocular Melanoma (OM)",
        "conditions_oncotree_curation_cleaned_simplified": "Melanoma (MEL)",
        "primary_vs_conditions_relation": "CHECK",
        "inclusive_rule": True,
        "manual_overwrite": "",
        "siblings_summary": "",
        "conditions_w_molecular": "",
    }
    values.update(overrides)
    return pd.Series(values)


def test_check_relation_inclusive_defaults_to_registry_condition():
    assert compute_final_cancer_1_for_row(base_row(), row_index=2) == "Melanoma (MEL)"


def test_check_relation_exclusive_refines_condition_with_not_primary():
    assert (
        compute_final_cancer_1_for_row(
            base_row(inclusive_rule=False),
            row_index=2,
        )
        == "Melanoma (MEL) & NOT(Ocular Melanoma (OM))"
    )


def test_mixed_check_relation_keeps_aligned_per_term_resolution():
    row = base_row(
        primary_tumor_oncotree_curation_simplified=(
            "Adenocarcinoma, NOS (ADNOS) | Breast (BREAST)"
        ),
        conditions_oncotree_curation_cleaned_simplified="Breast (BREAST)",
        primary_vs_conditions_relation="CHECK | identical",
    )

    assert compute_final_cancer_1_for_row(row, row_index=2) == "Breast (BREAST)"
