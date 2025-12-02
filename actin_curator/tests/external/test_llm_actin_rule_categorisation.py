import pytest
from pathlib import Path

from trialcurator.openai_client import OpenaiClient
from actin_curator import actin_curator, actin_curator_utils


@pytest.fixture(scope="module")
def client_and_actin_data():
    client = OpenaiClient()
    actin_repo_root = Path(__file__).resolve().parents[2]
    actin_rules_path = actin_repo_root / "data/ACTIN_rules/ACTIN_rules_w_categories_13062025.csv"
    actin_rules, actin_categories = actin_curator_utils.load_actin_resource(str(actin_rules_path))
    return client, actin_rules, actin_categories


def test_category_assignment_1(client_and_actin_data):
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
        "INCLUDE Adequate bone marrow, hepatic, and renal function, as assessed by the following laboratory requirements within 30 days before the start of study intervention: - Hemoglobin ≥9.0 g/dL. - Absolute neutrophil count (ANC) ≥1500/mm^3. - Platelet count ≥100,000/mm^3. - Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if the participant has a confirmed history of Gilbert's syndrome. - ALT and AST <2.5 x ULN (≤5 x ULN for participants with liver involvement). - eGFR >60 mL/min/1.73 m^2, according to the MDRD abbreviated formula and creatinine clearance (CrCl) >60 mL/min based on Cockcroft-Gault formula.": ['Hematologic_Parameters', 'Liver_Function', 'Renal_Function']
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_category_assignment_2(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients treated for Hepatitis C and have undetectable viral loads are eligible.
"""
    expected_output_text = {
        "EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients treated for Hepatitis C and have undetectable viral loads are eligible.": ["Infectious_Disease_History_and_Status"]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_category_assignment_3(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L
"""
    expected_output_text = {
        "INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L": ["Liver_Function"]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_category_assignment_4(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
EXCLUDE Resting heart rate > 100 bpm
"""
    expected_output_text = {
        "EXCLUDE Resting heart rate > 100 bpm": ["Vital_Signs_and_Body_Function_Metrics"]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_category_assignment_5(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Histologically or cytologically confirmed metastatic CRPC
"""
    expected_output_text = {
        "INCLUDE Histologically or cytologically confirmed metastatic CRPC": ["Cancer_Type_and_Tumor_Site_Localization"]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_category_assignment_6(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma
"""
    expected_output_text = {
        "INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma": ["Cancer_Type_and_Tumor_Site_Localization"]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_category_assignment_7(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer
"""
    expected_output_text = {
        "INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer": ["Cancer_Type_and_Tumor_Site_Localization"]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


def test_category_assignment_8(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = """
INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma
"""
    expected_output_text = {
        "INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma": ["Cancer_Type_and_Tumor_Site_Localization"]
    }
    actual_output = actin_curator.identify_actin_categories(input_text, client, actin_categories)[0]
    actual_categories = next(iter(actual_output.values()))
    expected_categories = next(iter(expected_output_text.values()))
    assert set(actual_categories) == set(expected_categories)


