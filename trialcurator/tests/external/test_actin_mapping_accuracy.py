import unittest
from pathlib import Path

import trialcurator.eligibility_curator_actin as actin
from trialcurator.openai_client import OpenaiClient

actin_rules_complete = actin.load_actin_rules(
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
                "actin_rule": {"HAS_LIFE_EXPECTANCY_OF_AT_LEAST_X_MONTHS": [3]}
            },
            {
                "description": "EXCLUDE Participants who have any untreated symptomatic CNS metastases.",
                "actin_rule": {"NOT": {"HAS_KNOWN_ACTIVE_CNS_METASTASES": []}}
            },
            {
                "description": "INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site",
                "actin_rule": {"CAN_PROVIDE_FRESH_TISSUE_SAMPLE_FOR_FURTHER_ANALYSIS": []}
            },
            {
                "description": "EXCLUDE Are pregnant.",
                "actin_rule": {"NOT": {"IS_PREGNANT": []}}
            },
            {
                "description": "INCLUDE Are at least 18 years old.",
                "actin_rule": {"IS_AT_LEAST_X_YEARS_OLD": [18]}
            },
            {
                "description": "INCLUDE Has an ECOG performance status of 0 or 1",
                "actin_rule": {"HAS_WHO_STATUS_OF_AT_MOST_X": [1]}
            },
            {
                "description": "INCLUDE Has adequate organ and bone marrow function as defined in the protocol",
                "actin_rule": {"HAS_ADEQUATE_ORGAN_FUNCTION": []}
            },
            {
                "description": "EXCLUDE Has second malignancy that is progressing or requires active treatment as defined in the protocol",
                "actin_rule": {"NOT": {"HAS_ACTIVE_SECOND_MALIGNANCY": []}}
            }
        ]
        actual_output = actin.map_to_actin(input_text, self.client, actin_rules_complete)
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
                }
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
                }
            }
        ]
        actual_output = actin.map_to_actin(input_text, self.client, actin_rules_complete)
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

    # The tests below compares the accuracy between feeding the entire ACTIN list to the LLM versus providing a subset of rules
    def test_labvalue_complete_list(self):
        input_text = '''
INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration
INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L
EXCLUDE Resting heart rate > 100 bpm
INCLUDE Any abnormalities in magnesium are not > Grade 2
'''
        expected_mapping = [
            {
                'description': 'INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration',
                'actin_rule': {'NOT': {'IS_PREGNANT': []}}
            },
            {
                'description': 'INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L',
                'actin_rule': {'HAS_ALAT_ULN_OF_AT_MOST_X': [3]}
            },
            {
                'description': 'EXCLUDE Resting heart rate > 100 bpm',
                'actin_rule': {'HAS_RESTING_HEART_RATE_BETWEEN_X_AND_Y': [0, 100]}
            },
            {
                'description': 'INCLUDE Any abnormalities in magnesium are not > Grade 2',
                'actin_rule': {'NOT(HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y[3, magnesium])': []}
            }
        ]

        actual_output = actin.map_to_actin(input_text, self.client, actin_rules_complete)
        self.assertEqual(expected_mapping, actual_output)

    def test_labvalue_partial_list_1(self):
        actin_rules_subset_labvalue_1 = [
            "IS_MALE",
            "IS_FEMALE",
            "IS_PREGNANT",
            "HAS_ALBUMIN_G_PER_DL_OF_AT_LEAST_X",
            "HAS_ALBUMIN_LLN_OF_AT_LEAST_X",
            "HAS_ASAT_ULN_OF_AT_MOST_X",
            "HAS_ALAT_ULN_OF_AT_MOST_X",
            "HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT",
            "HAS_ALP_ULN_OF_AT_MOST_X",
            "HAS_ALP_ULN_OF_AT_LEAST_X",
            "HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X",
            "HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE",
            "HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE",
            "HAS_TOTAL_BILIRUBIN_UMOL_PER_L_OF_AT_MOST_X",
            "HAS_TOTAL_BILIRUBIN_MG_PER_DL_OF_AT_MOST_X",
            "HAS_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_X",
            "HAS_SBP_MMHG_OF_AT_LEAST_X",
            "HAS_SBP_MMHG_OF_AT_MOST_X",
            "HAS_DBP_MMHG_OF_AT_LEAST_X",
            "HAS_DBP_MMHG_OF_AT_MOST_X",
            "HAS_PULSE_OXIMETRY_OF_AT_LEAST_X",
            "HAS_RESTING_HEART_RATE_BETWEEN_X_AND_Y",
            "HAS_BODY_WEIGHT_OF_AT_LEAST_X",
            "HAS_BODY_WEIGHT_OF_AT_MOST_X",
            "HAS_BMI_OF_AT_MOST_X",
            "REQUIRES_REGULAR_HEMATOPOIETIC_SUPPORT",
            "HAS_HISTORY_OF_ANAPHYLAXIS",
            "HAS_EXPERIENCED_IMMUNOTHERAPY_RELATED_ADVERSE_EVENTS",
            "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X",
            "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y",
            "HAS_TOXICITY_ASTCT_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y",
            "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_IGNORING_ICD_TITLES_Y"
        ]

        input_text = '''
INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration
INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L
EXCLUDE Resting heart rate > 100 bpm
INCLUDE Any abnormalities in magnesium are not > Grade 2
'''
        expected_mapping = [
            {
                'description': 'INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration',
                'actin_rule': {'NOT': {'IS_PREGNANT': []}}
            },
            {
                'description': 'INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L',
                'actin_rule': {'HAS_ALAT_ULN_OF_AT_MOST_X': [3]}
            },
            {
                'description': 'EXCLUDE Resting heart rate > 100 bpm',
                'actin_rule': {'HAS_RESTING_HEART_RATE_BETWEEN_X_AND_Y': [0, 100]}
            },
            {
                'description': 'INCLUDE Any abnormalities in magnesium are not > Grade 2',
                'actin_rule': {'NOT(HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y[3, magnesium])': []}
            }
        ]

        actual_output = actin.map_to_actin(input_text, self.client, actin_rules_subset_labvalue_1)
        self.assertEqual(expected_mapping, actual_output)

    def test_labvalue_partial_list_2(self):
        actin_rules_subset_labvalue_2 = [
            "IS_PREGNANT",
            "HAS_ASAT_ULN_OF_AT_MOST_X",
            "HAS_ALAT_ULN_OF_AT_MOST_X",
            "HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT",
            "HAS_ALP_ULN_OF_AT_MOST_X",
            "HAS_ALP_ULN_OF_AT_LEAST_X",
            "HAS_PULSE_OXIMETRY_OF_AT_LEAST_X",
            "HAS_RESTING_HEART_RATE_BETWEEN_X_AND_Y",
            "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X",
            "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y",
            "HAS_TOXICITY_ASTCT_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y",
            "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_IGNORING_ICD_TITLES_Y"
        ]

        input_text = '''
INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration
EXCLUDE Resting heart rate > 100 bpm
INCLUDE Any abnormalities in magnesium are not > Grade 2
'''
        expected_mapping = [
            {
                'description': 'INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration',
                'actin_rule': {'NOT': {'IS_PREGNANT': []}}
            },
            {
                'description': 'EXCLUDE Resting heart rate > 100 bpm',
                'actin_rule': {'HAS_RESTING_HEART_RATE_BETWEEN_X_AND_Y': [0, 100]}
            },
            {
                'description': 'INCLUDE Any abnormalities in magnesium are not > Grade 2',
                'actin_rule': {'NOT(HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y[3, magnesium])': []}
            }
        ]

        actual_output = actin.map_to_actin(input_text, self.client, actin_rules_subset_labvalue_2)
        self.assertEqual(expected_mapping, actual_output)

    # Conclusion:
    # 1. Restricting the selection list increases performance PROVIDED alternative choices do not have a high percentage of matching chars
    # eg. HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y vs HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_IGNORING_ICD_TITLES_Y
    # 2. Reducing the size of the list does NOTHING to correct logical errors wrt to NOT(...)

    def test_cancertype_complete_list(self):
        input_text = '''
INCLUDE Histologically or cytologically confirmed metastatic CRPC
INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma
INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer
INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma
'''
        expected_mapping = [
            {
                'description': 'INCLUDE Histologically or cytologically confirmed metastatic CRPC',
                'actin_rule': {'AND': [
                    {'OR': [
                        {'HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                        {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []}]
                    },
                    {'HAS_METASTATIC_CANCER': []},
                    {'HAS_CANCER_TYPE_X': ['CRPC']}]
                }
            },
            {
                'description': 'INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma',
                'actin_rule': {'AND': [
                    {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                    {'OR': [
                        {'AND': [
                            {'HAS_LOCALLY_ADVANCED_CANCER': []},
                            {'HAS_UNRESECTABLE_CANCER': []}]
                        },
                        {'HAS_METASTATIC_CANCER': []}]
                    },
                    {'HAS_CANCER_TYPE_X': ['cholangiocarcinoma']}]}
            },
            {
                'description': 'INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer',
                'actin_rule': {'AND': [
                    {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                    {'HAS_METASTATIC_CANCER': []},
                    {'HAS_CANCER_TYPE_X': ['prostate cancer']}]
                }
            },
            {
                'description': 'INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma',
                'actin_rule': {'AND': [
                    {'OR': [
                        {'HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                        {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []}]
                    },
                    {'HAS_METASTATIC_CANCER': []},
                    {'HAS_CANCER_TYPE_X': ['uveal melanoma']}]}
            }
        ]

        actual_output = actin.map_to_actin(input_text, self.client, actin_rules_complete)
        self.assertEqual(expected_mapping, actual_output)

    def test_cancertype_partial_list(self):
        actin_rules_subset_cancertype = [
            "HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE",
            "HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE",
            "HAS_PATHOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE",
            "HAS_ANY_STAGE_X",
            "HAS_TNM_T_SCORE_X",
            "HAS_LOCALLY_ADVANCED_CANCER",
            "HAS_METASTATIC_CANCER",
            "HAS_UNRESECTABLE_CANCER",
            "HAS_UNRESECTABLE_STAGE_III_CANCER",
            "HAS_RECURRENT_CANCER",
            "HAS_INCURABLE_CANCER",
            "HAS_CANCER_TYPE_X"
        ]

        input_text = '''
INCLUDE Histologically or cytologically confirmed metastatic CRPC
INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma
INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer
INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma
'''
        expected_mapping = [
            {
                'description': 'INCLUDE Histologically or cytologically confirmed metastatic CRPC',
                'actin_rule': {'AND': [
                    {'OR': [
                        {'HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                        {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []}]
                    },
                    {'HAS_METASTATIC_CANCER': []},
                    {'HAS_CANCER_TYPE_X': ['CRPC']}]
                }
            },
            {
                'description': 'INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma',
                'actin_rule': {'AND': [
                    {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                    {'OR': [
                        {'AND': [
                            {'HAS_LOCALLY_ADVANCED_CANCER': []},
                            {'HAS_UNRESECTABLE_CANCER': []}]
                        },
                        {'HAS_METASTATIC_CANCER': []}]
                    },
                    {'HAS_CANCER_TYPE_X': ['cholangiocarcinoma']}]}
            },
            {
                'description': 'INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer',
                'actin_rule': {'AND': [
                    {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                    {'HAS_METASTATIC_CANCER': []},
                    {'HAS_CANCER_TYPE_X': ['prostate cancer']}]
                }
            },
            {
                'description': 'INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma',
                'actin_rule': {'AND': [
                    {'OR': [
                        {'HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                        {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []}]
                    },
                    {'HAS_METASTATIC_CANCER': []},
                    {'HAS_CANCER_TYPE_X': ['uveal melanoma']}]}
            }
        ]

        actual_output = actin.map_to_actin(input_text, self.client, actin_rules_subset_cancertype)
        self.assertEqual(expected_mapping, actual_output)

    # Conclusion:
    # 1. It is essential to add "HAS_CANCER_TYPE_X"
    # 2. The reduced list improves performance wrt to choosing between OR v AND
    # 3. Does not fix nested logical errors