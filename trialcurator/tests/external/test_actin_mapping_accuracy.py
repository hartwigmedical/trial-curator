import unittest
from pathlib import Path

import trialcurator.eligibility_curator_actin as actin
from trialcurator.openai_client import OpenaiClient

actin_rules = actin.load_actin_rules(
    str(Path(__file__).resolve().parent / "data/ACTIN_test_cases/ACTIN_CompleteList_03042025.csv"))


class TestActinMappingAccuracy(unittest.TestCase):

    def setUp(self):
        self.client = OpenaiClient()

    def test_initial_correct_simple(self):
        input_text = '''
INCLUDE Participants must have a life expectancy of at least 3 months at the time of the first dose.
EXCLUDE Participants who have any untreated symptomatic CNS metastases.
INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site
EXCLUDE Are pregnant.
INCLUDE Are at least 18 years old.
INCLUDE Has an ECOG performance status of 0 or 1
INCLUDE Has adequate organ and bone marrow function as defined in the protocol
EXCLUDE Has second malignancy that is progressing or requires active treatment as defined in the protocol
'''
        expected_output = [
            {
                "description": "INCLUDE Participants must have a life expectancy of at least 3 months at the time of the first dose.",
                "actin_rule": {"HAS_LIFE_EXPECTANCY_OF_AT_LEAST_X_MONTHS": [3]},
                "new_rule": []
            },
            {
                "description": "EXCLUDE Participants who have any untreated symptomatic CNS metastases.",
                "actin_rule": {"NOT": {"HAS_KNOWN_ACTIVE_CNS_METASTASES": []}},
                "new_rule": []
            },
            {
                "description": "INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site",
                "actin_rule": {"CAN_PROVIDE_FRESH_TISSUE_SAMPLE_FOR_FURTHER_ANALYSIS": []},
                "new_rule": []
            },
            {
                "description": "EXCLUDE Are pregnant.",
                "actin_rule": {"NOT": {"IS_PREGNANT": []}},
                "new_rule": []
            },
            {
                "description": "INCLUDE Are at least 18 years old.",
                "actin_rule": {"IS_AT_LEAST_X_YEARS_OLD": [18]},
                "new_rule": []
            },
            {
                "description": "INCLUDE Has an ECOG performance status of 0 or 1",
                "actin_rule": {"HAS_WHO_STATUS_OF_AT_MOST_X": [1]},
                "new_rule": []
            },
            {
                "description": "INCLUDE Has adequate organ and bone marrow function as defined in the protocol",
                "actin_rule": {"HAS_ADEQUATE_ORGAN_FUNCTION": []},
                "new_rule": []
            },
            {
                "description": "EXCLUDE Has second malignancy that is progressing or requires active treatment as defined in the protocol",
                "actin_rule": {"NOT": {"HAS_ACTIVE_SECOND_MALIGNANCY": []}},
                "new_rule": []
            }
        ]
        actual_output = actin.map_to_actin(input_text, self.client, actin_rules)
        self.assertEqual(expected_output, actual_output)

    def test_initial_correct_complex(self):
        # Pass rate ~50%, ignoring formatting differences in `description`
        input_text = '''
INCLUDE Adequate bone marrow, hepatic, and renal function, as assessed by the following laboratory requirements within 30 days before the start of study intervention:
    - Hemoglobin ≥9.0 g/dL.
    - Absolute neutrophil count (ANC) ≥1500/mm^3.
    - Platelet count ≥100,000/mm^3.
    - Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if the participant has a confirmed history of Gilbert's syndrome.
    - ALT and AST <2.5 x ULN (≤5 x ULN for participants with liver involvement).
    - eGFR >60 mL/min/1.73 m^2, according to the MDRD abbreviated formula and creatinine clearance (CrCl) >60 mL/min based on Cockcroft-Gault formula.
EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients treated for Hepatitis C and have undetectable viral loads are eligible.
'''
        expected_output = [
            {
                "description": "INCLUDE Adequate bone marrow, hepatic, and renal function, as assessed by the following laboratory requirements within 30 days before the start of study intervention:\n    - Hemoglobin ≥9.0 g/dL.\n    - Absolute neutrophil count (ANC) ≥1500/mm^3.\n    - Platelet count ≥100,000/mm^3.\n    - Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if the participant has a confirmed history of Gilbert's syndrome.\n    - ALT and AST <2.5 x ULN (≤5 x ULN for participants with liver involvement).\n    - eGFR >60 mL/min/1.73 m^2, according to the MDRD abbreviated formula and creatinine clearance (CrCl) >60 mL/min based on Cockcroft-Gault formula.",
                "actin_rule": {
                    "AND": [
                        {"HAS_HEMOGLOBIN_G_PER_DL_OF_AT_LEAST_X": [9.0]},
                        {"HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X": [1500]},
                        {"HAS_THROMBOCYTES_ABS_OF_AT_LEAST_X": [100000]},
                        {"OR": [
                            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X": [1.5]},
                            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE": [3.0]}
                        ]},
                        {"HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT": [2.5, 5.0]},
                        {"AND": [
                            {"HAS_EGFR_MDRD_OF_AT_LEAST_X": [60]},
                            {"HAS_CREATININE_CLEARANCE_CG_OF_AT_LEAST_X": [60]}
                        ]}
                    ]
                },
                "new_rule": []
            },
            {
                "description": "EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients treated for Hepatitis C and have undetectable viral loads are eligible.",
                "actin_rule": {
                    "NOT": {
                        "OR": [
                            {"HAS_KNOWN_HIV_INFECTION": []},
                            {"AND": [
                                {"HAS_KNOWN_HEPATITIS_B_INFECTION": []},
                                {"NOT": {"CURRENTLY_GETS_CATEGORY_X_MEDICATION": ["antiviral treatment"]}}
                            ]},
                            {"HAS_KNOWN_HEPATITIS_C_INFECTION": []}
                        ]
                    }
                },
                "new_rule": []
            }
        ]
        actual_output = actin.map_to_actin(input_text, self.client, actin_rules)
        self.assertEqual(expected_output, actual_output)

    def test_drug_category_correction(self):
        input_mappings = [
            {
                "description": "INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1",
                "actin_rule": {"NOT": {"HAS_HAD_TREATMENT_WITH_ANY_DRUG_X": ["PD-1/PD-L1 inhibitors"]}},
                "new_rule": []
            },
            {
                "description": "EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol",
                "actin_rule": {
                    "NOT": {"HAS_RECEIVED_ANY_ANTI_CANCER_THERAPY_WITHIN_X_WEEKS": ["anti-EGFR antibody therapy"]}},
                "new_rule": []
            }
        ]
        expected_output = [
            {
                "description": "INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1",
                "actin_rule": {"NOT":
                                   {"HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y": ["IMMUNOTHERAPY",
                                                                                "PD-1/PD-L1 inhibitors"]}
                               },
                "new_rule": []
            },
            {
                "description": "EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol",
                "actin_rule": {"NOT":
                                   {"HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y": ["TARGETED THERAPY",
                                                                                "anti-EGFR antibody"]}
                               },
                "new_rule": []
            }
        ]
        actual_output = actin.correct_actin_mistakes(input_mappings, self.client)
        self.assertEqual(expected_output, actual_output)

    def test_other_incorrect_correction(self):
        input_mappings = [
                {
                    "description": "INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.",
                    "actin_rule": {"HAS_MEASURABLE_DISEASE_RECIST": []},
                    "new_rule": []
                },
                {
                    "description": "EXCLUDE Is currently participating in another study of a therapeutic agent",
                    "actin_rule": {"NOT": {"IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL": []}},
                    "new_rule": []
                }
            ]
        expected_output = [
            {
                "description": "INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.",
                "actin_rule": {"HAS_MEASURABLE_DISEASE": []},
                "new_rule": []
            },
            {
                "description": "EXCLUDE Is currently participating in another study of a therapeutic agent",
                "actin_rule": {"IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL": []},
                "new_rule": []
            }
        ]
        actual_output = actin.correct_actin_mistakes(input_mappings, self.client)
        self.assertEqual(expected_output, actual_output)
