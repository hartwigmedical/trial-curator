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


def test_lab_parameters_split_into_multiple_categories(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Adequate bone marrow, hepatic, and renal function, as assessed by the following laboratory requirements within 30 days before the start of study intervention:
    - Hemoglobin ≥9.0 g/dL.
    - Absolute neutrophil count (ANC) ≥1500/mm^3.
    - Platelet count ≥100,000/mm^3.
    - Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if the participant has a confirmed history of Gilbert's syndrome.
    - ALT and AST <2.5 x ULN (≤5 x ULN for participants with liver involvement).
    - eGFR >60 mL/min/1.73 m^2, according to the MDRD abbreviated formula and creatinine clearance (CrCl) >60 mL/min based on Cockcroft-Gault formula.
"""
    expected_output_text = {
        input_text.strip(): ["Hematologic_Parameters", "Liver_Function", "Renal_Function"]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    assert set(next(iter(actual_output.values()))) == set(next(iter(expected_output_text.values())))


def test_infectious_disease_status_hiv_hepatitis(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients treated for Hepatitis C and have undetectable viral loads are eligible.
"""
    expected_output_text = {input_text.strip(): ["Infectious_Disease_Status"]}
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    assert set(next(iter(actual_output.values()))) == set(next(iter(expected_output_text.values())))


def test_liver_function_alt_uln(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L
"""
    expected_output_text = {input_text.strip(): ["Liver_Function"]}
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    assert set(next(iter(actual_output.values()))) == set(next(iter(expected_output_text.values())))


def test_vital_signs_heart_rate(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
EXCLUDE Resting heart rate > 100 bpm
"""
    expected_output_text = {input_text.strip(): ["Vital_Signs_and_Body_Function_Metrics"]}
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    assert set(next(iter(actual_output.values()))) == set(next(iter(expected_output_text.values())))


def test_primary_tumor_and_extent_metastatic_crpc(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Histologically or cytologically confirmed metastatic CRPC
"""
    expected_output_text = {input_text.strip(): ["Primary_Tumor_Type", "Tumor_Site_and_Extent"]}
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    assert set(next(iter(actual_output.values()))) == set(next(iter(expected_output_text.values())))


def test_primary_tumor_and_extent_cholangiocarcinoma(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma
"""
    expected_output_text = {input_text.strip(): ["Primary_Tumor_Type", "Tumor_Site_and_Extent"]}
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    assert set(next(iter(actual_output.values()))) == set(next(iter(expected_output_text.values())))


def test_primary_tumor_and_extent_prostate(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer
"""
    expected_output_text = {input_text.strip(): ["Primary_Tumor_Type", "Tumor_Site_and_Extent"]}
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    assert set(next(iter(actual_output.values()))) == set(next(iter(expected_output_text.values())))


def test_primary_tumor_and_extent_uveal_melanoma(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma
"""
    expected_output_text = {input_text.strip(): ["Primary_Tumor_Type", "Tumor_Site_and_Extent"]}
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    assert set(next(iter(actual_output.values()))) == set(next(iter(expected_output_text.values())))


def test_prior_treatment_exposure(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
EXCLUDE Prior treatment with an EGFR tyrosine kinase inhibitor
"""
    expected_output_text = {
        "EXCLUDE Prior treatment with an EGFR tyrosine kinase inhibitor": [
            "Prior_Treatment_Exposure"
        ]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_treatment_lines_and_sequencing(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Received at least 2 prior lines of systemic therapy in the metastatic setting
"""
    expected_output_text = {
        "INCLUDE Received at least 2 prior lines of systemic therapy in the metastatic setting": [
            "Treatment_Lines_and_Sequencing"
        ]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_treatment_response_and_resistance(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
EXCLUDE Radiological disease progression following at least 1 line of platinum-based chemotherapy
"""
    expected_output_text = {
        "EXCLUDE Radiological disease progression following at least 1 line of platinum-based chemotherapy": [
            "Treatment_Response_and_Resistance"
        ]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_treatment_eligibility_intent_and_setting(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Eligible for palliative radiotherapy to bone metastases
"""
    expected_output_text = {
        "INCLUDE Eligible for palliative radiotherapy to bone metastases": [
            "Treatment_Eligibility_Intent_and_Setting"
        ]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_clinical_trial_participation(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
EXCLUDE Currently participating in another interventional clinical trial
"""
    expected_output_text = {
        "EXCLUDE Currently participating in another interventional clinical trial": [
            "Clinical_Trial_Participation"
        ]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_washout_periods(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
EXCLUDE Received systemic anticancer therapy within 14 days prior to first study dose; all related toxicities must have resolved to ≤ Grade 1
"""
    expected_output_text = {
        "EXCLUDE Received systemic anticancer therapy within 14 days prior to first study dose; all related toxicities must have resolved to ≤ Grade 1": [
            "Washout_Periods"
        ]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)
