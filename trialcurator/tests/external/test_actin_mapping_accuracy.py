import unittest
from pathlib import Path

from trialcurator.tests.external import test_actin_inputs as ti
import trialcurator.eligibility_curator_actin as actin
from trialcurator.openai_client import OpenaiClient


class BaseActinClass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = OpenaiClient()
        cls.actin_rules = actin.load_actin_file(
            str(Path(__file__).resolve().parent / "data/ACTIN_test_cases/ACTIN_list_w_categories_23052025.csv"))


class TestActinCategoryAssignment(BaseActinClass):

    def test_category_assignment_1(self):
        input_text = ti.input_labvalue_2 + ti.input_infection_1

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
                'Laboratory_and_Blood_Count_Requirements'],
        }
        actual_categories = actin.identify_actin_categories(input_text, self.client, self.actin_rules)
        self.assertEqual(expected_categories, actual_categories)

    def test_category_assignment_2(self):
        input_text = ti.input_labvalue_1 + ti.input_bodily_function_1 + ti.input_cancer_type_1 + ti.input_cancer_type_2

        expected_categories = {
            'EXCLUDE Resting heart rate > 100 bpm': ['Vital_Signs_and_Body_Function_Metrics'],
            'INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this '
            'study, the ULN for ALT is 45 U/L': [
                'Laboratory_and_Blood_Count_Requirements'],
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
        actual_categories = actin.identify_actin_categories(input_text, self.client, self.actin_rules)
        self.assertEqual(expected_categories, actual_categories)


class TestActinCategorySorting(BaseActinClass):

    def test_category_sorting(self):
        input_text = ti.input_cancer_type_1 + ti.input_infection_1 + ti.input_cancer_type_2 + ti.input_labvalue_2 + ti.input_bodily_function_1 + ti.input_labvalue_1

        expected_output = {
            ('Cancer_Type_and_Tumor_Site_Localization',):
                [
                    'INCLUDE Histologically or cytologically confirmed metastatic CRPC',
                    'INCLUDE Have a histopathologically confirmed diagnosis consistent with locally advanced unresectable or metastatic cholangiocarcinoma',
                    'INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer',
                    'INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma'
                ],
            ('Infectious_Disease_History_and_Status',):
                [
                    'EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment, or Hepatitis C; patients treated for Hepatitis C and have undetectable viral loads are eligible.'
                ],
            ('Laboratory_and_Blood_Count_Requirements',):
                [
                    "INCLUDE Adequate bone marrow, hepatic, and renal function, as assessed by the following laboratory requirements within 30 days before the start of study intervention: - Hemoglobin ≥9.0 g/dL. - Absolute neutrophil count (ANC) ≥1500/mm^3. - Platelet count ≥100,000/mm^3. - Total bilirubin ≤1.5 x ULN, or ≤3 x ULN if the participant has a confirmed history of Gilbert's syndrome. - ALT and AST <2.5 x ULN (≤5 x ULN for participants with liver involvement). - eGFR >60 mL/min/1.73 m^2, according to the MDRD abbreviated formula and creatinine clearance (CrCl) >60 mL/min based on Cockcroft-Gault formula.",
                    'INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L'
                ],
            ('Vital_Signs_and_Body_Function_Metrics',):
                [
                    'EXCLUDE Resting heart rate > 100 bpm'
                ]
        }

        actual_output = actin.sort_criteria_by_category(
            actin.identify_actin_categories(input_text, self.client, self.actin_rules)
        )
        self.assertEqual(expected_output, actual_output)


class TestActinMapping(BaseActinClass):

    def test_mapping_1(self):
        input_text = ti.input_general_1 + ti.input_reproduction_1 + ti.input_cancer_type_3 + ti.input_labvalue_3 + ti.input_second_malignancy_1

        expected_mapping = [
            {
                "description": "INCLUDE Participants must have a life expectancy of at least 3 months at the time of the first dose.",
                "actin_rule": {"HAS_LIFE_EXPECTANCY_OF_AT_LEAST_X_MONTHS": [3]},
                'new_rule': []
            },
            {
                "description": "INCLUDE Are at least 18 years old.",
                "actin_rule": {"IS_AT_LEAST_X_YEARS_OLD": [18]},
                'new_rule': []
            },
            {
                "description": "INCLUDE Has an ECOG performance status of 0 or 1",
                "actin_rule": {"HAS_WHO_STATUS_OF_AT_MOST_X": [1]},
                'new_rule': []
            },
            {
                "description": "EXCLUDE Are pregnant.",
                "actin_rule": {"NOT": {"IS_PREGNANT": []}},
                'new_rule': []
            },
            {
                "description": "EXCLUDE Participants who have any untreated symptomatic CNS metastases.",
                "actin_rule": {"NOT": {"HAS_KNOWN_ACTIVE_CNS_METASTASES": []}},
                'new_rule': []
            },
            {
                "description": "INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site",
                "actin_rule": {"CAN_PROVIDE_FRESH_TISSUE_SAMPLE_FOR_FURTHER_ANALYSIS": []},
                'new_rule': []
            },
            {
                "description": "INCLUDE Has adequate organ and bone marrow function as defined in the protocol",
                "actin_rule": {"HAS_ADEQUATE_ORGAN_FUNCTION": []},
                'new_rule': []
            },
            {
                "description": "EXCLUDE Has second malignancy that is progressing or requires active treatment as defined in the protocol",
                "actin_rule": {"NOT": {"HAS_ACTIVE_SECOND_MALIGNANCY": []}},
                'new_rule': []
            }
        ]
        actual_mapping = actin.actin_workflow(input_text, self.client, self.actin_rules)
        self.assertEqual(expected_mapping, actual_mapping)

    def test_mapping_2(self):
        input_text = ti.input_labvalue_2 + ti.input_infection_1

        expected_mapping = [
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
                'new_rule': []
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
                'new_rule': []
            }
        ]
        actual_mapping = actin.actin_workflow(input_text, self.client, self.actin_rules)
        self.assertEqual(expected_mapping, actual_mapping)

    def test_mapping_3(self):
        input_text = ti.input_treatment_1 + ti.input_washout_period_1

        expected_mapping = [
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
        actual_mapping = actin.actin_workflow(input_text, self.client, self.actin_rules)
        self.assertEqual(expected_mapping, actual_mapping)

    def test_mapping_4(self):
        input_text = ti.input_cancer_type_4 + ti.input_treatment_2

        expected_mapping = [
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
        actual_mapping = actin.actin_workflow(input_text, self.client, self.actin_rules)
        self.assertEqual(expected_mapping, actual_mapping)

    def test_mapping_5(self):
        input_text = ti.input_reproduction_2 + ti.input_labvalue_1 + ti.input_bodily_function_1 + ti.input_toxicity_1

        expected_mapping = [
            {
                'description': 'INCLUDE ALT =< 135 U/L (must be performed within 7 days prior to enrollment). For the purpose of this study, the ULN for ALT is 45 U/L',
                'actin_rule': {'HAS_ALAT_ULN_OF_AT_MOST_X': [3]},
                "new_rule": []
            },
            {
                'description': 'INCLUDE Any abnormalities in magnesium are not > Grade 2',
                'actin_rule': {
                    'NOT': {
                        'HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_WITH_ANY_ICD_TITLE_Y': [3, 'magnesium']}
                },
                "new_rule": []
            },
            {
                'description': 'EXCLUDE Resting heart rate > 100 bpm',
                'actin_rule': {'HAS_RESTING_HEART_RATE_BETWEEN_X_AND_Y': [0, 100]},
                "new_rule": []
            },
            {
                'description': 'INCLUDE Women of childbearing potential must have a negative serum pregnancy test within 72 hours prior to CNA3103 administration',
                'actin_rule': {'NOT': {'IS_PREGNANT': []}},
                "new_rule": []
            }
        ]
        actual_mapping = actin.actin_workflow(input_text, self.client, self.actin_rules)
        self.assertEqual(expected_mapping, actual_mapping)

    def test_mapping_6(self):
        input_text = ti.input_cancer_type_1 + ti.input_cancer_type_2

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
                },
                "new_rule": []
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
                    {'HAS_CANCER_TYPE_X': ['cholangiocarcinoma']}]},
                "new_rule": []
            },
            {
                'description': 'INCLUDE Histologically confirmed diagnosis of metastatic prostate cancer',
                'actin_rule': {'AND': [
                    {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                    {'HAS_METASTATIC_CANCER': []},
                    {'HAS_CANCER_TYPE_X': ['prostate cancer']}]
                },
                "new_rule": []
            },
            {
                'description': 'INCLUDE Histologically or cytologically confirmed metastatic uveal melanoma',
                'actin_rule': {'AND': [
                    {'OR': [
                        {'HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []},
                        {'HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE': []}]
                    },
                    {'HAS_METASTATIC_CANCER': []},
                    {'HAS_CANCER_TYPE_X': ['uveal melanoma']}]},
                "new_rule": []
            }
        ]
        actual_mapping = actin.actin_workflow(input_text, self.client, self.actin_rules)
        self.assertEqual(expected_mapping, actual_mapping)
