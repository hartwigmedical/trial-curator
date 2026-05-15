from __future__ import annotations

import pandas as pd

from aus_trial_universe.ctgov.ii_process_eligibility_criteria.cancer_types.final_determination.row_level_determination import (
    FINAL_CANCER_2_EXCLUSIVE_COL,
    FINAL_CANCER_2_INCLUSIVE_COL,
    FINAL_CANCER_3_EXCLUSIVE_COL,
    FINAL_CANCER_3_INCLUSIVE_COL,
    OncoTreeHierarchy as RowLevelOncoTreeHierarchy,
    add_final_cancer_3_columns,
    _clean_positive_terms,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.cancer_types.final_determination.trial_level_determination import (
    INPUT_EXCLUSIVE_COL,
    INPUT_INCLUSIVE_COL,
    NCT_ID_COL,
    OUTPUT_EXCLUSIVE_COL,
    OUTPUT_INCLUSIVE_COL,
    OncoTreeHierarchy as TrialLevelOncoTreeHierarchy,
    collapse_to_unique_trials,
)


def _empty_row_level_hierarchy() -> RowLevelOncoTreeHierarchy:
    return RowLevelOncoTreeHierarchy(
        parents_by_code={},
        children_by_code={},
    )


def _empty_trial_level_hierarchy() -> TrialLevelOncoTreeHierarchy:
    return TrialLevelOncoTreeHierarchy(
        parents_by_code={},
        children_by_code={},
    )


def test_clean_positive_terms_retains_pan_cancer_with_specific_terms() -> None:
    hierarchy = _empty_row_level_hierarchy()

    terms = _clean_positive_terms(
        "Pan-cancer | Cutaneous Melanoma (SKCM) | Non-Small Cell Lung Cancer (NSCLC)",
        hierarchy,
    )

    assert terms == [
        "Pan-cancer",
        "Cutaneous Melanoma (SKCM)",
        "Non-Small Cell Lung Cancer (NSCLC)",
    ]


def test_final_cancer_3_retains_pan_cancer_from_final_cancer_2() -> None:
    hierarchy = _empty_row_level_hierarchy()

    df = pd.DataFrame(
        [
            {
                FINAL_CANCER_2_INCLUSIVE_COL: (
                    "Pan-cancer | Cutaneous Melanoma (SKCM) | "
                    "Non-Small Cell Lung Cancer (NSCLC)"
                ),
                FINAL_CANCER_2_EXCLUSIVE_COL: "",
                "conditions_w_molecular": "",
                "siblings_summary": "",
            }
        ]
    )

    out = add_final_cancer_3_columns(df, hierarchy)

    assert out.loc[0, FINAL_CANCER_3_INCLUSIVE_COL] == (
        "Pan-cancer | Cutaneous Melanoma (SKCM) | "
        "Non-Small Cell Lung Cancer (NSCLC)"
    )
    assert out.loc[0, FINAL_CANCER_3_EXCLUSIVE_COL] == ""


def test_trial_level_collapse_retains_pan_cancer_with_specific_terms() -> None:
    hierarchy = _empty_trial_level_hierarchy()

    df = pd.DataFrame(
        [
            {
                NCT_ID_COL: "NCT00000001",
                INPUT_INCLUSIVE_COL: (
                    "Pan-cancer | Cutaneous Melanoma (SKCM) | "
                    "Non-Small Cell Lung Cancer (NSCLC)"
                ),
                INPUT_EXCLUSIVE_COL: "",
            }
        ]
    )

    out = collapse_to_unique_trials(df, hierarchy)

    assert len(out) == 1
    assert out.loc[0, OUTPUT_INCLUSIVE_COL] == (
        "Pan-cancer | Cutaneous Melanoma (SKCM) | "
        "Non-Small Cell Lung Cancer (NSCLC)"
    )
    assert out.loc[0, OUTPUT_EXCLUSIVE_COL] == ""


def test_trial_level_collapse_retains_pan_cancer_across_multiple_rows() -> None:
    hierarchy = _empty_trial_level_hierarchy()

    df = pd.DataFrame(
        [
            {
                NCT_ID_COL: "NCT00000002",
                INPUT_INCLUSIVE_COL: "Pan-cancer",
                INPUT_EXCLUSIVE_COL: "",
            },
            {
                NCT_ID_COL: "NCT00000002",
                INPUT_INCLUSIVE_COL: "Cutaneous Melanoma (SKCM)",
                INPUT_EXCLUSIVE_COL: "",
            },
            {
                NCT_ID_COL: "NCT00000002",
                INPUT_INCLUSIVE_COL: "Non-Small Cell Lung Cancer (NSCLC)",
                INPUT_EXCLUSIVE_COL: "",
            },
        ]
    )

    out = collapse_to_unique_trials(df, hierarchy)

    assert len(out) == 1
    assert out.loc[0, OUTPUT_INCLUSIVE_COL] == (
        "Pan-cancer | Cutaneous Melanoma (SKCM) | "
        "Non-Small Cell Lung Cancer (NSCLC)"
    )
    assert out.loc[0, OUTPUT_EXCLUSIVE_COL] == ""