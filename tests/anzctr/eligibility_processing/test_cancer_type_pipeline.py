from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aus_trial_universe.anzctr.eligibility.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline import (
    MANUAL_OVERWRITE_COL,
    PRIMARY_VS_HEALTH_CONDITION_ROW_COL,
    apply_anzctr_manual_overwrites,
    build_health_condition_mapping_table,
    build_unresolved_check_review_table,
    discover_pipeline_inputs,
    normalize_anzctr_trial_id,
)


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_normalize_anzctr_trial_id_adds_actrn_prefix_when_source_is_numeric():
    assert normalize_anzctr_trial_id(12605000003673) == "ACTRN12605000003673"
    assert normalize_anzctr_trial_id(" actrn12605000003673 ") == "ACTRN12605000003673"
    assert normalize_anzctr_trial_id("") == ""


def test_health_condition_mapping_uses_generic_shared_condition_columns(tmp_path: Path):
    input_csv = tmp_path / "anzctr_field_extractions_w_drugs.csv"
    pd.DataFrame(
        {
            "ACTRN": [12605000003673],
            "HEALTH CONDITION": ["Breast cancer | Lung cancer"],
        }
    ).to_csv(input_csv, index=False)

    resource_dir = tmp_path / "resources/cancer_type"
    resource_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "Conditions_lookup": ["Breast cancer", "Lung cancer"],
            "Oncotree_curation": ["Breast", "Non-Small Cell Lung Cancer"],
        }
    ).to_csv(resource_dir / "ConditionsCurationResource_01012026.csv", index=False)

    out = build_health_condition_mapping_table(
        input_csv,
        cancer_type_resource_dir=resource_dir,
    )

    assert out.to_dict("records") == [
        {
            "trial_id": "ACTRN12605000003673",
            "conditions_original": "Breast cancer | Lung cancer",
            "conditions_oncotree_curation": "Breast | Non-Small Cell Lung Cancer",
        }
    ]


def test_unresolved_check_review_table_contains_only_blank_manual_check_rows():
    df = pd.DataFrame(
        {
            PRIMARY_VS_HEALTH_CONDITION_ROW_COL: [1, 2, 3],
            "trial_id": ["ACTRN1", "ACTRN2", "ACTRN3"],
            "primary_tumor_type": ["A", "B", "C"],
            "primary_tumor_location": ["", "", ""],
            "conditions_original": ["Condition A", "Condition B", "Condition C"],
            "primary_vs_conditions_relation": [
                "identical",
                "CHECK",
                "CHECK | identical",
            ],
            MANUAL_OVERWRITE_COL: ["", "", "include both"],
        }
    )

    out = build_unresolved_check_review_table(df)

    assert out[PRIMARY_VS_HEALTH_CONDITION_ROW_COL].tolist() == [2]
    assert out["trial_id"].tolist() == ["ACTRN2"]
    assert out[MANUAL_OVERWRITE_COL].tolist() == [""]


def test_apply_anzctr_manual_overwrites_uses_row_number_for_duplicate_keys(tmp_path: Path):
    generated_df = pd.DataFrame(
        {
            PRIMARY_VS_HEALTH_CONDITION_ROW_COL: ["1", "2"],
            "trial_id": ["ACTRN1", "ACTRN1"],
            "primary_tumor_type": ["MM", "MM"],
            "primary_tumor_location": ["", ""],
            "conditions_original": ["Mesothelioma | Lung cancer", "Mesothelioma | Lung cancer"],
            MANUAL_OVERWRITE_COL: ["", ""],
        }
    )
    manual_file = tmp_path / "manual_review.csv"
    pd.DataFrame(
        {
            PRIMARY_VS_HEALTH_CONDITION_ROW_COL: ["2"],
            "trial_id": ["ACTRN1"],
            "primary_tumor_type": ["MM"],
            "primary_tumor_location": [""],
            "conditions_original": ["Mesothelioma | Lung cancer"],
            MANUAL_OVERWRITE_COL: ["ignore primary"],
        }
    ).to_csv(manual_file, index=False)

    out = apply_anzctr_manual_overwrites(generated_df, manual_file)

    assert out[MANUAL_OVERWRITE_COL].tolist() == ["", "ignore primary"]


def test_apply_anzctr_manual_overwrites_accepts_row_number_without_trial_id(
    tmp_path: Path,
):
    generated_df = pd.DataFrame(
        {
            PRIMARY_VS_HEALTH_CONDITION_ROW_COL: ["1"],
            "trial_id": ["ACTRN1"],
            "primary_tumor_type": ["MM"],
            "primary_tumor_location": [""],
            "conditions_original": ["Mesothelioma | Lung cancer"],
            MANUAL_OVERWRITE_COL: [""],
        }
    )
    manual_file = tmp_path / "manual_review.csv"
    pd.DataFrame(
        {
            PRIMARY_VS_HEALTH_CONDITION_ROW_COL: ["1"],
            MANUAL_OVERWRITE_COL: ["ignore primary"],
        }
    ).to_csv(manual_file, index=False)

    out = apply_anzctr_manual_overwrites(generated_df, manual_file)

    assert out[MANUAL_OVERWRITE_COL].tolist() == ["ignore primary"]


def test_apply_anzctr_manual_overwrites_requires_row_number_for_ambiguous_keys(
    tmp_path: Path,
):
    generated_df = pd.DataFrame(
        {
            PRIMARY_VS_HEALTH_CONDITION_ROW_COL: ["1", "2"],
            "trial_id": ["ACTRN1", "ACTRN1"],
            "primary_tumor_type": ["MM", "MM"],
            "primary_tumor_location": ["", ""],
            "conditions_original": ["Mesothelioma | Lung cancer", "Mesothelioma | Lung cancer"],
            MANUAL_OVERWRITE_COL: ["", ""],
        }
    )
    manual_file = tmp_path / "manual_review.csv"
    pd.DataFrame(
        {
            "trial_id": ["ACTRN1"],
            "primary_tumor_type": ["MM"],
            "primary_tumor_location": [""],
            "conditions_original": ["Mesothelioma | Lung cancer"],
            MANUAL_OVERWRITE_COL: ["ignore primary"],
        }
    ).to_csv(manual_file, index=False)

    with pytest.raises(ValueError, match="Add 'primary_vs_health_condition_row'"):
        apply_anzctr_manual_overwrites(generated_df, manual_file)


def test_anzctr_cancer_type_discovery_uses_shared_resource_defaults(tmp_path: Path):
    eligibility_dir = tmp_path / "data/anzctr/eligibility"
    write(
        eligibility_dir / "trials/anzctr_field_extractions_w_drugs.csv",
        "ACTRN,HEALTH CONDITION\n12605000003673,Breast cancer\n",
    )
    write(eligibility_dir / "trials/original_curations/ACTRN12605000003673.py", "rules = []\n")

    shared_resources = tmp_path / "data/eligibility/resources"
    write(shared_resources / "oncotree.csv", "code,name,parent\n")
    write(
        shared_resources / "cancer_type/ConditionsCurationResource_01012026.csv",
        "Conditions_lookup,Oncotree_curation\nBreast cancer,Breast\n",
    )
    write(
        shared_resources / "cancer_type/PrimaryTumourCurationResource_01012026.csv",
        "PrimaryTumour,Oncotree_curation\nbreast,Breast\n",
    )

    inputs = discover_pipeline_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=eligibility_dir,
        input_csv=None,
        curated_dir=None,
        resources_dir=None,
        cancer_type_resource_dir=None,
        oncotree_csv=None,
        manual_overwrite_file=None,
        output_dir=None,
        output_format="tsv",
        fail_on_error=False,
    )

    assert inputs.input_csv == eligibility_dir / "trials/anzctr_field_extractions_w_drugs.csv"
    assert inputs.curated_dir == eligibility_dir / "trials/original_curations"
    assert inputs.cancer_type_resource_dir == shared_resources / "cancer_type"
    assert inputs.oncotree_csv == shared_resources / "oncotree.csv"
    assert inputs.output_dir == eligibility_dir / "processed/cancer_type"
