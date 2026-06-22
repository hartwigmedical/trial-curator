from __future__ import annotations

from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.shared.trial_resource.combined_trial_resource_export import (
    FINAL_COHORT_COLUMNS,
    FINAL_TRIAL_COLUMNS,
    combine_cohort_resource_tables,
    combine_trial_resource_tables,
    discover_pipeline_inputs,
    run_combined_trial_resource_export,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_combine_trial_resource_tables_maps_registry_columns_to_final_schema():
    ctgov = pd.DataFrame(
        {
            "nctId": ["NCT00000001"],
            "briefTitle": ["CTGov brief"],
            "officialTitle": ["CTGov official"],
            "status": ["RECRUITING"],
            "phases": ["['PHASE2']"],
            "leadSponsor": ["CTGov sponsor"],
            "conditions": ["['Breast Cancer']"],
            "minAge": ["18 Years"],
            "maxAge": ["80 Years"],
            "address": ["['Sydney, New South Wales, 2000, Australia']"],
            "interventionType": ["['DRUG']"],
            "interventionName": ["['Capecitabine']"],
            "cancer_type_inclusive": ["Breast"],
            "cancer_type_exclusive": [""],
            "gene_alteration_inclusive": [""],
            "gene_alteration_exclusive": [""],
            "molecular_signature_inclusive": [""],
            "molecular_signature_exclusive": [""],
        }
    )
    anzctr = pd.DataFrame(
        {
            "trialId": ["ACTRN12605000003673"],
            "STUDY TITLE": ["ANZCTR public"],
            "SCIENTIFIC TITLE": ["ANZCTR scientific"],
            "RECRUITMENT STATUS": ["Recruiting"],
            "PHASE": ["Phase 2"],
            "PRIMARY SPONSOR NAME": ["ANZCTR sponsor"],
            "HEALTH CONDITION": ["Lung cancer"],
            "MIN AGE": ["18.0"],
            "MIN AGE TYPE": ["Years"],
            "MAX AGE": ["0.0"],
            "MAX AGE TYPE": ["Not stated"],
            "RECRUITMENT COUNTRY": ["Australia"],
            "RECRUITMENT STATE": ["NSW"],
            "anzctr_intervention_codes": ["Treatment: Drugs"],
            "DRUG_rxnorm_matched": ["pembrolizumab"],
            "llm_drug_to_remove": ["water"],
            "llm_drugs_to_add": [""],
            "llm_drugs_to_correct": [""],
            "llm_reasoning": ["reviewed"],
            "cancer_type_inclusive": ["Lung"],
            "cancer_type_exclusive": [""],
            "gene_alteration_inclusive": ["EGFR"],
            "gene_alteration_exclusive": [""],
            "molecular_signature_inclusive": [""],
            "molecular_signature_exclusive": [""],
        }
    )

    combined = combine_trial_resource_tables(
        [
            ("ctgov", "nctId", ctgov),
            ("anzctr", "trialId", anzctr),
        ]
    )

    assert list(combined.columns) == list(FINAL_TRIAL_COLUMNS)
    assert combined["registry"].tolist() == ["ctgov", "anzctr"]
    assert combined["trialId"].tolist() == [
        "NCT00000001",
        "ACTRN12605000003673",
    ]
    assert combined.loc[0, "title"] == "CTGov brief"
    assert combined.loc[0, "scientific_title"] == "CTGov official"
    assert combined.loc[0, "recruitment_country"] == "Australia"
    assert combined.loc[0, "recruitment_state"] == "NSW"
    assert combined.loc[0, "llm_reasoning"] == ""
    assert combined.loc[1, "title"] == "ANZCTR public"
    assert combined.loc[1, "intervention_name"] == "pembrolizumab"
    assert combined.loc[1, "min_age"] == "18 Years"
    assert combined.loc[1, "max_age"] == ""
    assert combined.loc[1, "llm_drug_to_remove"] == "water"


def test_combine_cohort_resource_tables_adds_cohort_to_schema():
    ctgov = pd.DataFrame(
        {
            "nctId": ["NCT00000001"],
            "cohort": ["(general)"],
            "briefTitle": ["CTGov brief"],
        }
    )
    anzctr = pd.DataFrame(
        {
            "trialId": ["ACTRN12605000003673"],
            "cohort": ["cohort A"],
            "STUDY TITLE": ["ANZCTR public"],
        }
    )

    combined = combine_cohort_resource_tables(
        [
            ("ctgov", "nctId", ctgov),
            ("anzctr", "trialId", anzctr),
        ]
    )

    assert list(combined.columns) == list(FINAL_COHORT_COLUMNS)
    assert combined["cohort"].tolist() == ["(general)", "cohort A"]


def test_discover_pipeline_inputs_uses_top_level_combined_export_defaults(
    tmp_path: Path,
):
    data_dir = tmp_path / "data/eligibility_path"
    ctgov_trial = write(
        data_dir / "exports/final/ctgov/trial_resource_05062026.tsv",
        "nctId\nNCT00000001\n",
    )
    anzctr_trial = write(
        data_dir / "exports/final/anzctr/trial_resource_05062026.tsv",
        "trialId\nACTRN12605000003673\n",
    )
    ctgov_cohort = write(
        data_dir / "exports/final/ctgov/cohort_resource_05062026.tsv",
        "nctId\tcohort\nNCT00000001\t(general)\n",
    )
    anzctr_cohort = write(
        data_dir / "exports/final/anzctr/cohort_resource_05062026.tsv",
        "trialId\tcohort\nACTRN12605000003673\t(general)\n",
    )

    inputs = discover_pipeline_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=data_dir,
        ctgov_trial_resource_file=None,
        anzctr_trial_resource_file=None,
        ctgov_cohort_resource_file=None,
        anzctr_cohort_resource_file=None,
        output_file=None,
        trial_output_file=None,
        cohort_output_file=None,
        export_date="05062026",
    )

    assert inputs.ctgov_trial_resource_file == ctgov_trial
    assert inputs.anzctr_trial_resource_file == anzctr_trial
    assert inputs.ctgov_cohort_resource_file == ctgov_cohort
    assert inputs.anzctr_cohort_resource_file == anzctr_cohort
    assert inputs.trial_output_file == data_dir / "exports/final/eligibility_trial_resource_05062026.tsv"
    assert inputs.cohort_output_file == data_dir / "exports/final/eligibility_cohort_resource_05062026.tsv"


def test_run_combined_trial_resource_export_writes_trial_and_cohort_tsvs(tmp_path: Path):
    data_dir = tmp_path / "data/eligibility_path"
    write(
        data_dir / "exports/final/ctgov/trial_resource_05062026.tsv",
        "nctId\tbriefTitle\tcancer_type_inclusive\nNCT00000001\tCTGov trial\tBreast\n",
    )
    write(
        data_dir / "exports/final/anzctr/trial_resource_05062026.tsv",
        "trialId\tSTUDY TITLE\tgene_alteration_inclusive\nACTRN12605000003673\tANZCTR trial\tEGFR\n",
    )
    write(
        data_dir / "exports/final/ctgov/cohort_resource_05062026.tsv",
        "nctId\tcohort\tbriefTitle\nNCT00000001\t(general)\tCTGov trial\n",
    )
    write(
        data_dir / "exports/final/anzctr/cohort_resource_05062026.tsv",
        "trialId\tcohort\tSTUDY TITLE\nACTRN12605000003673\t(general)\tANZCTR trial\n",
    )
    inputs = discover_pipeline_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=data_dir,
        ctgov_trial_resource_file=None,
        anzctr_trial_resource_file=None,
        ctgov_cohort_resource_file=None,
        anzctr_cohort_resource_file=None,
        output_file=None,
        trial_output_file=None,
        cohort_output_file=None,
        export_date="05062026",
    )

    outputs = run_combined_trial_resource_export(inputs)

    trial_output = pd.read_csv(outputs.trial_output_file, sep="\t", dtype=str, keep_default_na=False)
    cohort_output = pd.read_csv(outputs.cohort_output_file, sep="\t", dtype=str, keep_default_na=False)
    assert trial_output["registry"].tolist() == ["ctgov", "anzctr"]
    assert trial_output["trialId"].tolist() == [
        "NCT00000001",
        "ACTRN12605000003673",
    ]
    assert cohort_output["cohort"].tolist() == ["(general)", "(general)"]
