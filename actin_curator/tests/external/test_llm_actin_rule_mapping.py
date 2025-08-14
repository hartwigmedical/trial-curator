import pytest
from pathlib import Path

from trialcurator.openai_client import OpenaiClient
from actin_curator import actin_curator, actin_curator_utils


@pytest.fixture(scope="module")
def client_and_actin_data():
    client = OpenaiClient(0.0)
    actin_repo_root = Path(__file__).resolve().parents[2]
    actin_rules_path = actin_repo_root / "data/ACTIN_rules/ACTIN_rules_w_categories_13062025.csv"
    actin_rules, actin_categories = actin_curator_utils.load_actin_resource(str(actin_rules_path))
    return client, actin_rules, actin_categories


def test_mapping_1(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "INCLUDE Participants must have a life expectancy of at least 3 months at the time of the first dose.",
        "exclude": False,
        "actin_category": ["Performance_Status_and_Prognosis"]
    }
    expected_mapping = {"HAS_LIFE_EXPECTANCY_OF_AT_LEAST_X_MONTHS": [3]}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


def test_mapping_2(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "EXCLUDE Participants who have any untreated symptomatic CNS metastases.",
        "exclude": True,
        "actin_category": ["Medical_History_and_Comorbidities"]
    }
    expected_mapping = {'NOT': {'HAS_KNOWN_ACTIVE_CNS_METASTASES': []}}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


# 14 Aug 2025: Test is failing. Erroneously created "HAS_ADEQUATE_VENOUS_ACCESS
def test_mapping_3(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site",
        "exclude": False,
        "actin_category": ["Medical_History_and_Comorbidities"]
    }
    expected_mapping = {"CAN_PROVIDE_FRESH_TISSUE_SAMPLE_FOR_FURTHER_ANALYSIS": []}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


def test_mapping_4(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "INCLUDE Has adequate organ and bone marrow function as defined in the protocol",
        "exclude": False,
        "actin_category": ["Vital_Signs_and_Body_Function_Metrics"]
    }
    expected_mapping = "HAS_ADEQUATE_ORGAN_FUNCTION[]"
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


def test_mapping_5(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "EXCLUDE Has second malignancy that is progressing or requires active treatment as defined in the protocol",
        "exclude": True,
        "actin_category": ["Medical_History_and_Comorbidities"]
    }
    expected_mapping = {"NOT": "HAS_ACTIVE_SECOND_MALIGNANCY[]"}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


def test_mapping_6(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma",
        "exclude": False,
        "actin_category": ["Cancer_Type_and_Tumor_Site_Localization"]
    }
    expected_mapping = {'AND': [{'OR': ['HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE', 'HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE']}, 'HAS_METASTATIC_CANCER', 'HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X[uveal melanoma]']}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


def test_mapping_7(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer",
        "exclude": False,
        "actin_category": ["Cancer_Type_and_Tumor_Site_Localization"]
    }
    expected_mapping = {'AND': [{'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []}, {'HAS_METASTATIC_CANCER': []}, {'HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X': ['prostate cancer']}]}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


def test_mapping_8(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "INCLUDE Histologically or cytologically confirmed metastatic CRPC",
        "exclude": False,
        "actin_category": ["Cancer_Type_and_Tumor_Site_Localization"]
    }
    expected_mapping = {'AND': [{'OR': ['HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE', 'HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE']}, 'HAS_METASTATIC_CANCER', 'HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X[Prostate cancer]']}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


def test_mapping_9(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "EXCLUDE Resting heart rate > 100 bpm",
        "exclude": True,
        "actin_category": ["Vital_Signs_and_Body_Function_Metrics"]
    }
    expected_mapping = {'HAS_RESTING_HEART_RATE_BETWEEN_X_AND_Y': [0, 100]}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping


# 14 Aug 2025: Test can fail due to variability of response
def test_mapping_10(client_and_actin_data):
    client, actin_rules, _ = client_and_actin_data
    input_dict = {
        "input_rule": "EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients treated for Hepatitis C and have undetectable viral loads are eligible.",
        "exclude": True,
        "actin_category": ["Infectious_Disease_History_and_Status"]
    }
    expected_mapping = {'NOT': {'OR': [{'HAS_KNOWN_HIV_INFECTION': []}, {'HAS_KNOWN_HEPATITIS_B_INFECTION': []}, {'HAS_KNOWN_HEPATITIS_C_INFECTION': []}]}}
    actual_mapping = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert actual_mapping[0]["actin_rule"] == expected_mapping





# def test_mapping_6(client_and_actin_data):
#     client, actin_rules, actin_categories = client_and_actin_data
#     input_text = input_infection_1
#     expected_mapping = [
#         {
#             "description": "EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis "
#                            "C; patients treated for Hepatitis C and have undetectable viral loads are eligible.",
#             "actin_rule": 'NOT(OR(HAS_KNOWN_HIV_INFECTION, '
#                           'HAS_KNOWN_HEPATITIS_B_INFECTION, '
#                           'HAS_KNOWN_HEPATITIS_C_INFECTION))'
#         }
#     ]
#     actual_mapping = actin.actin_workflow(input_text, client, actin_rules, actin_categories)
#     assert output_formatting(actual_mapping) == expected_mapping

#
#
# def test_mapping_5(client_and_actin_data):
#     client, actin_rules, actin_categories = client_and_actin_data
#     input_text = input_labvalue_2
#     expected_mapping = [
#         {
#             "description": "INCLUDE Adequate bone marrow, hepatic, and renal function, as assessed by the following "
#                            "laboratory requirements within 30 days before the start of study intervention: - "
#                            "Hemoglobin ≥9.0 g/dL. - Absolute neutrophil count (ANC) ≥1500/mm^3. - Platelet count "
#                            "≥100,000/mm^3. - Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if the participant has a "
#                            "confirmed history of Gilbert's syndrome. - ALT and AST <2.5 x ULN (≤5 x ULN for "
#                            "participants with liver involvement). - eGFR >60 mL/min/1.73 m^2, according to the MDRD "
#                            "abbreviated formula and creatinine clearance (CrCl) >60 mL/min based on Cockcroft-Gault "
#                            "formula.",
#             "actin_rule": "AND(HAS_HEMOGLOBIN_G_PER_DL_OF_AT_LEAST_X[9.0], HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X[1.5], "
#                           "HAS_THROMBOCYTES_ABS_OF_AT_LEAST_X[100], "
#                           "HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE[1.5,3.0], HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT[2.5,5.0], HAS_EGFR_MDRD_OF_AT_LEAST_X[60], HAS_CREATININE_CLEARANCE_CG_OF_AT_LEAST_X[60])"
#         }
#     ]
#     actual_mapping = actin.actin_workflow(input_text, client, actin_rules, actin_categories)
#     assert output_formatting(actual_mapping) == expected_mapping
#

#
#
# def test_mapping_7(client_and_actin_data):
#     client, actin_rules, actin_categories = client_and_actin_data
#     input_text = input_reproduction_2 + input_labvalue_1
#
#     expected_mapping = [
#         {
#             'description': 'INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration',
#             'actin_rule': "NOT(IS_PREGNANT)",
#         },
#         {
#             'description': 'INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L',
#             'actin_rule': "HAS_ALAT_ULN_OF_AT_MOST_X[3]",
#         }
#     ]
#     actual_mapping = actin.actin_workflow(input_text, client, actin_rules, actin_categories)
#     assert output_formatting(actual_mapping) == expected_mapping
#
#
# def test_mapping_8(client_and_actin_data):
#     client, actin_rules, actin_categories = client_and_actin_data
#     input_text = input_treatment_1
#     expected_mapping = [
#         {
#             "description": "INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1",
#             "actin_rule": "NOT(HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[Immunotherapy,PD_1_PD_L1_ANTIBODY])",
#         }
#     ]
#     actual_mapping = actin.actin_workflow(input_text, client, actin_rules, actin_categories)
#     assert output_formatting(actual_mapping) == expected_mapping
#
#
# def test_mapping_9(client_and_actin_data):
#     client, actin_rules, actin_categories = client_and_actin_data
#     input_text = input_washout_period_1
#     expected_mapping = [
#         {
#             "description": "EXCLUDE Has received recent anti-EGFR antibody therapy within the past 4 weeks",
#             "actin_rule": "NOT(HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y_WITHIN_Z_WEEKS[Targeted therapy,EGFR_ANTIBODY,4])",
#         }
#     ]
#     actual_mapping = actin.actin_workflow(input_text, client, actin_rules, actin_categories)
#     assert output_formatting(actual_mapping) == expected_mapping
#
#
# def test_mapping_10(client_and_actin_data):
#     client, actin_rules, actin_categories = client_and_actin_data
#     input_text = input_cancer_type_4 + input_treatment_2
#     expected_mapping = [
#         {
#             "description": "INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors",
#             "actin_rule": "HAS_MEASURABLE_DISEASE_RECIST",
#         },
#         {
#             "description": "EXCLUDE Is currently participating in another study of a therapeutic agent",
#             "actin_rule": "IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL",
#         }
#     ]
#     actual_mapping = actin.actin_workflow(input_text, client, actin_rules, actin_categories)
#     assert output_formatting(actual_mapping) == expected_mapping
#
#
# def test_mapping_12(client_and_actin_data):
#     client, actin_rules, actin_categories = client_and_actin_data
#     input_text = input_toxicity_1
#     expected_mapping = [
#         {
#             'description': 'INCLUDE Any abnormalities in magnesium are not > Grade 2',
#             'actin_rule': "NOT(HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y[3,Disorders of magnesium metabolism])",
#         }
#     ]
#     actual_mapping = actin.actin_workflow(input_text, client, actin_rules, actin_categories)
#     assert output_formatting(actual_mapping) == expected_mapping
#
#
# def test_mapping_14(client_and_actin_data):
#     client, actin_rules, actin_categories = client_and_actin_data
#     input_text = ('INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced '
#                   'unresectable or metastatic cholangiocarcinoma')
#     expected_mapping = [
#         {
#             'description': 'INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced '
#                            'unresectable or metastatic cholangiocarcinoma',
#             'actin_rule': 'AND(HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X[cholangiocarcinoma], '
#                           'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE, '
#                           'HAS_PATHOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE, '
#                           'OR(AND(HAS_LOCALLY_ADVANCED_CANCER, HAS_UNRESECTABLE_CANCER), '
#                           'HAS_METASTATIC_CANCER))',
#         }
#     ]
#     actual_mapping = actin.actin_workflow(input_text, client, actin_rules, actin_categories)
#     assert ignore_order_diff(output_formatting(actual_mapping)[0]["actin_rule"]) == ignore_order_diff(
#         expected_mapping[0]["actin_rule"])
#
#

#
#

