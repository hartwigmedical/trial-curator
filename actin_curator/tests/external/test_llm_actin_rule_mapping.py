import pytest
from pathlib import Path

from trialcurator.openai_client import OpenaiClient
from trialcurator.gemini_client import GeminiClient
from actin_curator import actin_curator, actin_curator_utils


@pytest.fixture(scope="module")
def client_and_actin_data():
    # client = OpenaiClient(0.0)
    client = GeminiClient(model="gemini-2.5-flash")

    actin_repo_root = Path(__file__).resolve().parents[2]
    actin_rules_path = actin_repo_root / "data/ACTIN_rules/ACTIN_rules_w_categories_25092025.csv"

    actin_rules, actin_categories = actin_curator_utils.load_actin_resource(str(actin_rules_path))
    return client, actin_rules, actin_categories


# =========================
# Cardiac_Function_and_ECG_Criteria
# =========================
def test_cardiac_qtcf_threshold(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "QTcF must be ≤ 470 ms at screening",
        "exclude": False,
        "actin_category": ["Cardiac_Function_and_ECG_Criteria"],
    }
    expected = {"HAS_QTCF_OF_AT_MOST_X": [470]}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Current_Medication_Use
# =========================
def test_current_meds_cyp3a_inhibitors_prohibited(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Concurrent use of strong CYP3A inhibitors is prohibited",
        "exclude": True,
        "actin_category": ["Current_Medication_Use"],
    }
    expected = {"NOT": {"CURRENTLY_GETS_MEDICATION_INHIBITING_CYP_X": ["3A"]}}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Demographics_and_General_Eligibility
# =========================
def test_demographics_age_minimum(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Participants must be aged ≥ 18 years",
        "exclude": False,
        "actin_category": ["Demographics_and_General_Eligibility"],
    }
    expected = {"IS_AT_LEAST_X_YEARS_OLD": [18]}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Drug_Intolerances_and_Toxicity_History
# =========================
def test_toxicity_grade_upper_bound_magnesium_disorders(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Any abnormalities in magnesium are not > Grade 2",
        "exclude": False,
        "actin_category": ["Drug_Intolerances_and_Toxicity_History"],
    }
    expected = {
        "NOT": {
            "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y": [3, "Magnesium abnormality"]
        }
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Electrolytes_and_Minerals
# =========================
def test_electrolytes_corrected_calcium_within_normal_limits(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Corrected calcium must be within institutional normal limits",
        "exclude": False,
        "actin_category": ["Electrolytes_and_Minerals"],
    }
    expected = {"HAS_CORRECTED_CALCIUM_WITHIN_INSTITUTIONAL_NORMAL_LIMITS": []}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Endocrine_and_Metabolic_Function
# =========================
def test_endocrine_fasting_glucose_maximum(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Fasting plasma glucose must be ≤ 7.0 mmol/L",
        "exclude": False,
        "actin_category": ["Endocrine_and_Metabolic_Function"],
    }
    expected = {"HAS_GLUCOSE_FASTING_PLASMA_MMOL_PER_L_OF_AT_MOST_X": [7.0]}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# General_Comorbidities
# =========================
def test_general_cns_mets_excluded(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Participants who have any untreated symptomatic CNS metastases.",
        "exclude": True,
        "actin_category": ["General_Comorbidities"],
    }
    expected = {"NOT": {"HAS_KNOWN_ACTIVE_CNS_METASTASES": []}}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


def test_general_second_malignancy_excluded(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Has second malignancy that is progressing or requires active treatment",
        "exclude": True,
        "actin_category": ["General_Comorbidities"],
    }
    expected = {"NOT": {"HAS_ACTIVE_SECOND_MALIGNANCY": []}}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


def test_general_provide_fresh_tissue(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site",
        "exclude": False,
        "actin_category": ["Primary_Tumor_Type"],
    }
    expected = {"CAN_PROVIDE_FRESH_TISSUE_SAMPLE_FOR_FURTHER_ANALYSIS": []}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Genomic_Alterations
# =========================
def test_genomic_gene_amplification(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Tumours with HER2 amplification are eligible",
        "exclude": False,
        "actin_category": ["Genomic_Alterations"],
    }
    expected = {"AMPLIFICATION_OF_GENE_X": ["HER2"]}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Hematologic_Parameters
# =========================
def test_hematologic_panel_multi_and_units_as_rules_require(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": (
            "Adequate bone marrow function: Hemoglobin ≥9.0 g/dL; ANC ≥1500/mm^3; Platelets ≥100,000/mm^3."
        ),
        "exclude": False,
        "actin_category": ["Hematologic_Parameters"],
    }
    expected = {
        "AND": [
            {"HAS_HEMOGLOBIN_G_PER_DL_OF_AT_LEAST_X": [9.0]},
            {"HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X": [1500]},
            {"HAS_THROMBOCYTES_ABS_OF_AT_LEAST_X": [100000]},
        ]
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Liver_Function
# =========================
def test_liver_function_full_bundle_with_gilbert_and_met_involvement(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": (
            "Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if Gilbert's; ALT and AST <2.5 x ULN (≤5 x ULN with liver involvement)."
        ),
        "exclude": False,
        "actin_category": ["Liver_Function"],
    }
    expected = {
        "AND": [
            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE": [1.5, 3]},
            {"HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT": [2.5, 5]},
        ]
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Molecular_Biomarkers --- NOT SURE
# =========================
def test_molecular_pdl1_threshold_iHC_style(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "PD-L1 expression of at least 50% by IHC",
        "exclude": False,
        "actin_category": ["Molecular_Biomarkers"],
    }
    expected = {"EXPRESSION_OF_PROTEIN_X_BY_IHC_OF_AT_LEAST_Y": ["PD-L1", 50]}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Performance_Status_and_Prognosis
# =========================
def test_performance_life_expectancy_months(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Participants must have a life expectancy of at least 3 months at the time of the first dose.",
        "exclude": False,
        "actin_category": ["Performance_Status_and_Prognosis"],
    }
    expected = {"HAS_LIFE_EXPECTANCY_OF_AT_LEAST_X_MONTHS": [3]}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Primary_Tumor_Type  (pair with Tumor_Site_and_Extent where needed)
# =========================
def test_primary_uveal_melanoma_metastatic_with_doc_evidence(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Histologically or cytologically confirmed metastatic uveal melanoma",
        "exclude": False,
        "actin_category": ["Primary_Tumor_Type", "Tumor_Site_and_Extent"],
    }
    expected = {
        "AND": [
            {"OR": [
                {"HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []},
                {"HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []}
            ]},
            {"HAS_METASTATIC_CANCER": []},
            {"HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X": ["uveal melanoma"]},
        ]
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


def test_primary_met_prostate_histology_confirmed(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Histologically confirmed diagnosis of metastatic prostate cancer",
        "exclude": False,
        "actin_category": ["Primary_Tumor_Type", "Tumor_Site_and_Extent"],
    }
    expected = {
        "AND": [
            {"HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []},
            {"HAS_METASTATIC_CANCER": []},
            {"HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X": ["prostate cancer"]},
        ]
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


def test_primary_met_crpc_hist_or_cytology(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Histologically or cytologically confirmed metastatic CRPC",
        "exclude": False,
        "actin_category": ["Primary_Tumor_Type", "Tumor_Site_and_Extent"],
    }
    expected = {
        "AND": [
            {"OR": [
                {"HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []},
                {"HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []}
            ]},
            {"HAS_METASTATIC_CANCER": []},
            {"HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X": ["Prostate cancer"]},
        ]
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Prior_Treatment_Exposure
# =========================
def test_prior_pd1_pdl1_naive(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1",
        "exclude": False,
        "actin_category": ["Prior_Treatment_Exposure"],
    }
    expected = {"NOT": {"HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y": ["Immunotherapy", "PD_1_PD_L1_ANTIBODY"]}}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Renal_Function
# =========================
def test_renal_egfr_and_crcl_both_required(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "eGFR >60 mL/min/1.73 m^2 (MDRD) and Cockcroft-Gault creatinine clearance >60 mL/min",
        "exclude": False,
        "actin_category": ["Renal_Function"],
    }
    expected = {
        "AND": [
            {"HAS_EGFR_MDRD_OF_AT_LEAST_X": [61]},
            {"HAS_CREATININE_CLEARANCE_CG_OF_AT_LEAST_X": [61]},
        ]
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Surgical_History_and_Plans
# =========================
def test_surgery_recent_major_surgery_exclusion_example(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Major surgery within 4 weeks prior to first dose",
        "exclude": True,
        "actin_category": ["Surgical_History_and_Plans"],
    }
    expected = {"NOT": {"HAS_HAD_SURGERY_WITHIN_LAST_X_WEEKS": [4]}}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Treatment_Eligibility_Intent_and_Setting
# =========================
def test_participating_in_another_trial_disallowed_rule_is_negative_already(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Is currently participating in another study of a therapeutic agent",
        "exclude": True,
        "actin_category": ["Prior_Treatment_Exposure"],
    }
    expected = {"IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL": []}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Treatment_Lines_and_Sequencing
# =========================
def test_minimum_prior_systemic_lines(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Participants must have received at least 2 prior lines of systemic therapy",
        "exclude": False,
        "actin_category": ["Treatment_Lines_and_Sequencing"],
    }
    expected = {"HAS_HAD_AT_LEAST_X_SYSTEMIC_TREATMENT_LINES": [2]}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Tumor_Site_and_Extent
# =========================
def test_tumor_extent_locally_advanced_unresectable_or_metastatic(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Histopathologically confirmed cholangiocarcinoma that is locally advanced unresectable or metastatic",
        "exclude": False,
        "actin_category": ["Primary_Tumor_Type", "Tumor_Site_and_Extent"],
    }
    expected = {
        "AND": [
            {"HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X": ["cholangiocarcinoma"]},
            {"HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []},
            {"HAS_PATHOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []},
            {"OR": [
                {"AND": [
                    {"HAS_LOCALLY_ADVANCED_CANCER": []},
                    {"HAS_UNRESECTABLE_CANCER": []}
                ]},
                {"HAS_METASTATIC_CANCER": []}
            ]},
        ]
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


def test_tumor_measurable_disease_recist(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Participants must have at least one measurable lesion per RECIST 1.1",
        "exclude": False,
        "actin_category": ["Tumor_Site_and_Extent"],
    }
    expected = {"HAS_MEASURABLE_DISEASE_RECIST": []}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Vital_Signs_and_Body_Function_Metrics
# =========================
def test_vitals_resting_hr_exclusion_as_range_cap(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Resting heart rate > 100 bpm",
        "exclude": True,
        "actin_category": ["Vital_Signs_and_Body_Function_Metrics"],
    }
    expected = {"HAS_RESTING_HEART_RATE_BETWEEN_X_AND_Y": [0, 100]}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


def test_vitals_adequate_organ_function_summary_rule(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Has adequate organ and bone marrow function as defined in the protocol",
        "exclude": False,
        "actin_category": ["Vital_Signs_and_Body_Function_Metrics"],
    }
    expected = {"HAS_ADEQUATE_ORGAN_FUNCTION": []}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Washout_Periods
# =========================
def test_washout_recent_anti_egfr_within_4_weeks_excluded(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "Has received anti-EGFR antibody therapy within the past 4 weeks",
        "exclude": True,
        "actin_category": ["Washout_Periods"],
    }
    expected = {"NOT": {"HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y_WITHIN_Z_WEEKS": ["Targeted Therapy", "anti-EGFR antibody therapy", 4]}}
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected


# =========================
# Multi-categories
# =========================
def test_full_multicategory_panel_end_to_end(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": (
            "Adequate bone marrow, hepatic, and renal function: "
            "Hemoglobin ≥9.0 g/dL; ANC ≥1500/mm^3; Platelets ≥100,000/mm^3; "
            "Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if Gilbert's; "
            "ALT and AST <2.5 x ULN (≤5 x ULN with liver involvement); "
            "eGFR >60 mL/min/1.73 m^2 (MDRD) and CrCl >60 mL/min (Cockcroft-Gault)."
        ),
        "exclude": False,
        "actin_category": ["Hematologic_Parameters", "Liver_Function", "Renal_Function"],
    }
    expected = {
        "AND": [
            {"HAS_HEMOGLOBIN_G_PER_DL_OF_AT_LEAST_X": [9.0]},
            {"HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X": [1.5]},
            {"HAS_THROMBOCYTES_ABS_OF_AT_LEAST_X": [100]},
            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE": [1.5, 3.0]},
            {"HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT": [2.5, 5.0]},
            {"HAS_EGFR_MDRD_OF_AT_LEAST_X": [60]},
            {"HAS_CREATININE_CLEARANCE_CG_OF_AT_LEAST_X": [60]},
        ]
    }
    actual = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual[0]["actin_rule"] == expected
