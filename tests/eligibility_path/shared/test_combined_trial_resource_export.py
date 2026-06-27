from __future__ import annotations

from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.shared.trial_resource.combined_trial_resource_export import (
    FINAL_COHORT_COLUMNS,
    FINAL_TRIAL_COLUMNS,
    build_missing_pottr_trials,
    combine_cohort_resource_tables,
    combine_trial_resource_tables,
    derive_ctgov_location_fields,
    discover_pipeline_inputs,
    load_pottr_trial_ids,
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
            "conditions": ["['Breast Cancer', 'HER2-positive Breast Cancer']"],
            "minAge": ["18 Years"],
            "maxAge": ["80 Years"],
            "address": ["['Sydney, New South Wales, 2000, Australia']"],
            "interventionType": ["['DRUG', 'BIOLOGICAL']"],
            "interventionName": ["['Capecitabine', 'Trastuzumab']"],
            "cancer_type_inclusive": [
                "Breast (BREAST) | Myelodysplastic/Myeloproliferative Neoplasms (MDS/MPN) | "
                "Primary Mediastinal (Thymic) Large B-Cell Lymphoma (PMBL) | Pan-cancer | "
                "NOT(Breast (BREAST)), NOT(Esophagus/Stomach (STOMACH)), Skin (SKIN)"
            ],
            "cancer_type_exclusive": ["NOT(Inflammatory Breast Cancer (IBC)) & NOT(Pan-cancer)"],
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
            "RECRUITMENT COUNTRY": [",Outside"],
            "RECRUITMENT STATE": ["NSW"],
            "anzctr_intervention_codes": ["Treatment: Drugs"],
            "DRUG_rxnorm_matched": ["pembrolizumab"],
            "llm_drug_to_remove": ["water"],
            "llm_drugs_to_add": [""],
            "llm_drugs_to_correct": [""],
            "llm_reasoning": ["reviewed"],
            "cancer_type_inclusive": ["Lung (LUNG)"],
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
    assert combined["trialId"].tolist() == [
        "NCT00000001",
        "ACTRN12605000003673",
    ]
    assert combined["registry"].tolist() == ["ctgov", "anzctr"]
    assert list(combined.columns[:4]) == [
        "trialId",
        "registry",
        "title",
        "scientific_title",
    ]
    assert list(combined.columns[4:11]) == [
        "primary_sponsor",
        "recruitment_status",
        "phase",
        "country",
        "original_health_conditions",
        "intervention_type",
        "intervention_name",
    ]
    assert "recruitment_state" not in combined.columns
    assert "min_age" not in combined.columns
    assert "max_age" not in combined.columns
    assert "llm_reasoning" not in combined.columns
    assert combined.loc[0, "title"] == "CTGov brief"
    assert combined.loc[0, "scientific_title"] == "CTGov official"
    assert combined.loc[0, "primary_sponsor"] == "CTGov sponsor"
    assert combined.loc[0, "phase"] == "PHASE2"
    assert combined.loc[0, "original_health_conditions"] == "Breast Cancer | HER2-positive Breast Cancer"
    assert combined.loc[0, "country"] == "Australia"
    assert combined.loc[0, "intervention_type"] == "DRUG | BIOLOGICAL"
    assert combined.loc[0, "intervention_name"] == "Capecitabine | Trastuzumab"
    assert (
        combined.loc[0, "cancer_type_inclusive"]
        == "BREAST | MDS/MPN | PMBL | Pan-cancer | NOT(BREAST), NOT(STOMACH), SKIN"
    )
    assert combined.loc[0, "cancer_type_exclusive"] == "NOT(IBC) & NOT(Pan-cancer)"
    assert combined.loc[1, "title"] == "ANZCTR public"
    assert combined.loc[1, "country"] == "Outside"
    assert combined.loc[1, "intervention_name"] == "pembrolizumab"
    assert combined.loc[1, "cancer_type_inclusive"] == "LUNG"


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


def test_derive_ctgov_location_fields_handles_addresses_without_postcodes():
    countries, states = derive_ctgov_location_fields(
        "['Sydney, New South Wales, Australia', 'Melbourne, Victoria, Australia']"
    )

    assert countries == "Australia"
    assert states == "NSW | VIC"


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


def test_build_missing_pottr_trials_uses_eligibility_and_registry_union():
    trial_resource = pd.DataFrame(
        {
            "trialId": ["NCT00000001", "ACTRN12605000003673"],
            "registry": ["ctgov", "anzctr"],
        }
    )
    cohort_resource = pd.DataFrame(
        {
            "trialId": ["NCT00000001", "ACTRN12605000003673"],
            "cohort": ["(general)", "(general)"],
        }
    )
    pottr_eligibility = pd.DataFrame(
        {
            "trial_id": ["NCT00000001", "ACTRN12699999999999"],
            "eligibility_criteria": ["criteria present", "missing eligibility"],
        }
    )
    pottr_registry = pd.DataFrame(
        {
            "trial_id": ["ACTRN12699999999999", "NCT99999999"],
            "studytitle": ["Missing ANZCTR", "Missing CTGov"],
        }
    )

    missing = build_missing_pottr_trials(
        trial_resource,
        cohort_resource,
        pottr_eligibility,
        pottr_registry,
    )

    assert missing["trial_id"].tolist() == ["ACTRN12699999999999", "NCT99999999"]
    assert missing["registry"].tolist() == ["anzctr", "ctgov"]
    assert missing["in_pottr_trial_eligibility"].tolist() == [True, False]
    assert missing["in_pottr_trial_registry"].tolist() == [True, True]
    assert missing.loc[0, "eligibility_criteria"] == "missing eligibility"
    assert missing.loc[0, "studytitle"] == "Missing ANZCTR"


def test_load_pottr_trial_ids_unions_sources_and_filters_by_registry(tmp_path: Path):
    eligibility = write(
        tmp_path / "eligibility.tsv",
        "trial_id\nNCT00000001\nACTRN12699999999999\n",
    )
    registry = write(
        tmp_path / "registry.tsv",
        "trial_id\nnct00000002\nACTRN12605000003673\n",  # lower-case normalizes up
    )

    all_ids = load_pottr_trial_ids(
        pottr_trial_eligibility_file=eligibility,
        pottr_trial_registry_file=registry,
    )
    assert all_ids == {
        "NCT00000001",
        "NCT00000002",
        "ACTRN12699999999999",
        "ACTRN12605000003673",
    }

    ctgov_ids = load_pottr_trial_ids(
        pottr_trial_eligibility_file=eligibility,
        pottr_trial_registry_file=registry,
        registry="ctgov",
    )
    assert ctgov_ids == {"NCT00000001", "NCT00000002"}

    anzctr_ids = load_pottr_trial_ids(
        pottr_trial_eligibility_file=eligibility,
        pottr_trial_registry_file=registry,
        registry="anzctr",
    )
    assert anzctr_ids == {"ACTRN12699999999999", "ACTRN12605000003673"}


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
    pottr_eligibility = write(
        tmp_path / "pottr/trial_eligibility.AU.tsv",
        "trial_id\teligibility_criteria\n"
        "NCT00000001\tcovered criteria\n"
        "ACTRN12699999999999\tmissing criteria\n",
    )
    pottr_registry = write(
        tmp_path / "pottr/trial_registry.AU.tsv",
        "trial_id\tstudytitle\n"
        "ACTRN12605000003673\tcovered ANZCTR\n"
        "ACTRN12699999999999\tmissing ANZCTR\n",
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
        pottr_trial_eligibility_file=pottr_eligibility,
        pottr_trial_registry_file=pottr_registry,
        export_date="05062026",
    )

    outputs = run_combined_trial_resource_export(inputs)

    trial_output = pd.read_csv(outputs.trial_output_file, sep="\t", dtype=str, keep_default_na=False)
    cohort_output = pd.read_csv(outputs.cohort_output_file, sep="\t", dtype=str, keep_default_na=False)
    assert list(trial_output.columns) == list(FINAL_TRIAL_COLUMNS)
    assert list(cohort_output.columns) == list(FINAL_COHORT_COLUMNS)
    assert trial_output["trialId"].tolist() == [
        "NCT00000001",
        "ACTRN12605000003673",
    ]
    assert trial_output["registry"].tolist() == ["ctgov", "anzctr"]
    assert cohort_output["cohort"].tolist() == ["(general)", "(general)"]
    assert not (data_dir / "exports/final/missing_pottr_trials_05062026.tsv").exists()
    assert outputs.missing_pottr_trials["trial_id"].tolist() == [
        "ACTRN12699999999999"
    ]
    assert outputs.missing_pottr_trials.loc[0, "eligibility_criteria"] == "missing criteria"
    assert outputs.missing_pottr_trials.loc[0, "studytitle"] == "missing ANZCTR"
