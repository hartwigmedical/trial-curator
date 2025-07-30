import pytest
from pathlib import Path

import actin_curator.actin_curator_utils
from trialcurator.openai_client import OpenaiClient
import actin_curator.actin_curator as actin

input_general_1 = '''
INCLUDE Participants must have a life expectancy of at least 3 months at the time of the first dose.
INCLUDE Are at least 18 years old.
INCLUDE Has an ECOG performance status of 0 or 1
'''

input_bodily_function_1 = '''
EXCLUDE Resting heart rate > 100 bpm
'''

input_infection_1 = '''
EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients treated for Hepatitis C and have undetectable viral loads are eligible.
'''

input_reproduction_1 = '''
EXCLUDE Are pregnant.
'''

input_reproduction_2 = '''
INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration
'''

input_labvalue_1 = '''
INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L
'''

input_labvalue_2 = '''
INCLUDE Adequate bone marrow, hepatic, and renal function, as assessed by the following laboratory requirements within 30 days before the start of study intervention:
    - Hemoglobin ≥9.0 g/dL.
    - Absolute neutrophil count (ANC) ≥1500/mm^3.
    - Platelet count ≥100,000/mm^3.
    - Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if the participant has a confirmed history of Gilbert's syndrome.
    - ALT and AST <2.5 x ULN (≤5 x ULN for participants with liver involvement).
    - eGFR >60 mL/min/1.73 m^2, according to the MDRD abbreviated formula and creatinine clearance (CrCl) >60 mL/min based on Cockcroft-Gault formula.
'''

input_labvalue_3 = '''
INCLUDE Has adequate organ and bone marrow function as defined in the protocol
'''

input_cancer_type_1 = '''
INCLUDE Histologically or cytologically confirmed metastatic CRPC
INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma
'''

input_cancer_type_2 = '''
INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer
INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma
'''

input_cancer_type_3 = '''
EXCLUDE Participants who have any untreated symptomatic CNS metastases.
INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site
'''

input_cancer_type_4 = '''
INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.
'''

input_second_malignancy_1 = '''
EXCLUDE Has second malignancy that is progressing or requires active treatment as defined in the protocol
'''

input_treatment_1 = '''
INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1
'''

input_treatment_2 = '''
EXCLUDE Is currently participating in another study of a therapeutic agent
'''

input_toxicity_1 = '''
INCLUDE Any abnormalities in magnesium are not > Grade 2
'''

input_washout_period_1 = '''
EXCLUDE Has received recent anti-EGFR antibody therapy within the past 4 weeks
'''


def ignore_order_diff(rule: str) -> str:
    if rule.startswith("AND(") and rule.endswith(")"):
        inner = rule[4:-1]
        parts = [p.strip() for p in inner.split(",")]
        return f"AND({', '.join(sorted(parts))})"
    return rule


@pytest.fixture(scope="module")
def client_and_actin_data():
    client = OpenaiClient(0.0)

    actin_repo_root = Path(__file__).resolve().parents[2]
    actin_rules_path = actin_repo_root / "data/ACTIN_rules/ACTIN_rules_w_categories_13062025.csv"
    actin_rules, actin_categories = actin_curator.actin_curator_utils.load_actin_resource(str(actin_rules_path))

    return client, actin_rules, actin_categories


def test_category_assignment_1(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = input_labvalue_2 + input_infection_1

    expected_categories = {
        'EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients '
        'treated for Hepatitis C and have undetectable viral loads are eligible.': [
            'Infectious_Disease_History_and_Status'],
        "INCLUDE Adequate bone marrow, hepatic, and renal function, as assessed by the following laboratory "
        "requirements within 30 days before the start of study intervention: - Hemoglobin ≥9.0 g/dL. - Absolute "
        "neutrophil count (ANC) ≥1500/mm^3. - Platelet count ≥100,000/mm^3. - Total bilirubin ≤1.5 x ULN, "
        "or ≤3 x ULN if the participant has a confirmed history of Gilbert's syndrome. - ALT and AST <2.5 x ULN ("
        "≤5 x ULN for participants with liver involvement). - eGFR >60 mL/min/1.73 m^2, according to the MDRD "
        "abbreviated formula and creatinine clearance (CrCl) >60 mL/min based on Cockcroft-Gault formula.": [
            'Hematologic_Parameters', 'Liver_Function', 'Renal_Function'],
    }
    actual_categories = actin.identify_actin_categories(input_text, client, actin_categories)
    assert actual_categories == expected_categories


def test_category_assignment_2(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data
    input_text = input_labvalue_1 + input_bodily_function_1 + input_cancer_type_1 + input_cancer_type_2

    expected_categories = {
        'EXCLUDE Resting heart rate > 100 bpm': ['Vital_Signs_and_Body_Function_Metrics'],
        'INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this '
        'study, the ULN for ALT is 45 U/L': [
            'Liver_Function'],
        'INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or '
        'metastatic cholangiocarcinoma': [
            'Cancer_Type_and_Tumor_Site_Localization'],
        'INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer': [
            'Cancer_Type_and_Tumor_Site_Localization'],
        'INCLUDE Histologically or cytologically confirmed metastatic CRPC': [
            'Cancer_Type_and_Tumor_Site_Localization'],
        'INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma': [
            'Cancer_Type_and_Tumor_Site_Localization']
    }
    actual_categories = actin.identify_actin_categories(input_text, client, actin_categories)
    assert actual_categories == expected_categories