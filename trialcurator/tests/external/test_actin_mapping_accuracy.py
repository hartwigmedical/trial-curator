import unittest
from pathlib import Path

import trialcurator.eligibility_curator_actin as actin
from trialcurator.openai_client import OpenaiClient

actin_rules = actin.load_actin_rules(str(Path(__file__).resolve().parent/"data/ACTIN_test_cases/ACTIN_CompleteList_03042025.csv"))

class TestActinMappingAccuracy(unittest.TestCase):

    def setUp(self):
        self.client = OpenaiClient()

    def test_currently_correct(self):
        # Initial mappings are expected to be correct
        # Passing rate should be 100%

        input_text = '''
- INCLUDE Participants must have a life expectancy of at least 3 months at the time of the first dose.
- EXCLUDE Participants who have any untreated symptomatic CNS metastases.
- INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site
- EXCLUDE Are pregnant.
- INCLUDE Are at least 18 years old.
- INCLUDE Has an ECOG performance status of 0 or 1
- INCLUDE Has adequate organ and bone marrow function as defined in the protocol
- EXCLUDE Has second malignancy that is progressing or requires active treatment as defined in the protocol
'''
        expected_output = \
[
        {
            "description": "INCLUDE Participants must have a life expectancy of at least 3 months at the time of the first dose.",
            "actin_rule": "HAS_LIFE_EXPECTANCY_OF_AT_LEAST_X_MONTHS",
            "actin_params": [3],
            "new_rule": False
        },
        {
            "description": "EXCLUDE Participants who have any untreated symptomatic CNS metastases.",
            "actin_rule": "NOT(HAS_KNOWN_ACTIVE_CNS_METASTASES)",
            "actin_params": [],
            "new_rule": False
        },
        {
            "description": "INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site",
            "actin_rule": "CAN_PROVIDE_FRESH_TISSUE_SAMPLE_FOR_FURTHER_ANALYSIS",
            "actin_params": [],
            "new_rule": False
        },
        {
            "description": "EXCLUDE Are pregnant.",
            "actin_rule": "NOT(IS_PREGNANT)",
            "actin_params": [],
            "new_rule": False
        },
        {
            "description": "INCLUDE Are at least 18 years old.",
            "actin_rule": "IS_AT_LEAST_X_YEARS_OLD",
            "actin_params": [18],
            "new_rule": False
        },
        {
            "description": "INCLUDE Has an ECOG performance status of 0 or 1",
            "actin_rule": "HAS_WHO_STATUS_OF_AT_MOST_X",
            "actin_params": [1],
            "new_rule": False
        },
        {
            "description": "INCLUDE Has adequate organ and bone marrow function as defined in the protocol",
            "actin_rule": "HAS_ADEQUATE_ORGAN_FUNCTION",
            "actin_params": [],
            "new_rule": False
        },
        {
            "description": "EXCLUDE Has second malignancy that is progressing or requires active treatment as defined in the protocol",
            "actin_rule": "NOT(HAS_ACTIVE_SECOND_MALIGNANCY)",
            "actin_params": [],
            "new_rule": False
        }
]
        actual_output = actin.map_to_actin(input_text, self.client, actin_rules, 2)
        self.assertEqual(expected_output, actual_output)

    def test_drug_category_initial(self):
        # Initial mappings are not the correct answers - hence the need for a second ACTIN prompt
        # They are shown in expected_output to be used as inputs in the next test
        # Passing rate ~40%

        input_text = '''
- INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1
- EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol
'''
        expected_output = \
[
    {
        "description": "INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1",
        "actin_rule": "HAS_NOT_HAD_CATEGORY_X_TREATMENT",
        "actin_params": ["PD-1/PD-L1 inhibitors"],
        "new_rule": False
    },
    {
        "description": "EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol",
        "actin_rule": "NOT(HAS_HAD_TREATMENT_WITH_ANY_DRUG_X_WITHIN_Y_WEEKS)",
        "actin_params": ["anti-EGFR antibody","recent"],
        "new_rule": False
    }
]
        actual_output = actin.map_to_actin(input_text, self.client, actin_rules, 3)
        self.assertEqual(expected_output, actual_output)

    def test_drug_category_correction(self):
        input_mappings = \
[
    {
        "description": "INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1",
        "actin_rule": "HAS_NOT_HAD_CATEGORY_X_TREATMENT",
        "actin_params": ["PD-1/PD-L1 inhibitors"],
        "new_rule": False
    },
    {
        "description": "EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol",
        "actin_rule": "NOT(HAS_HAD_TREATMENT_WITH_ANY_DRUG_X)",
        "actin_params": ["anti-EGFR antibody"],
        "new_rule": False
    }
]
        expected_output = \
[
    {
        "description": "INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1",
        "actin_rule": "NOT(HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y)",
        "actin_params": ["IMMUNOTHERAPY", "PD-1/PD-L1 inhibitors"],
        "new_rule": False
    },
    {
        "description": "EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol",
        "actin_rule": "NOT(HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y)",
        "actin_params": ["TARGETED THERAPY", "EGFR antibody"],
        "new_rule": False
    }
]
        actual_output = actin.correct_actin_mistakes(input_mappings, self.client, 3)
        self.assertEqual(expected_output, actual_output)

    def test_other_incorrect_initial(self):
        input_text = '''
- INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.
- EXCLUDE Is currently participating in another study of a therapeutic agent
'''
        expected_output = \
[
    {
        "description": "INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.",
        "actin_rule": "HAS_MEASURABLE_DISEASE_RECIST",
        "actin_params": [],
        "new_rule": False
    },
    {
        "description": "EXCLUDE Is currently participating in another study of a therapeutic agent",
        "actin_rule": "NOT(IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL)",
        "actin_params": [],
        "new_rule": False
    }
]
        actual_output = actin.map_to_actin(input_text, self.client, actin_rules, 3)
        self.assertEqual(expected_output, actual_output)

    def test_other_incorrect_correction(self):
        input_mappings = \
[
    {
        "description": "INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.",
        "actin_rule": "HAS_MEASURABLE_DISEASE_RECIST",
        "actin_params": [],
        "new_rule": False
    },
    {
        "description": "EXCLUDE Is currently participating in another study of a therapeutic agent",
        "actin_rule": "NOT(IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL)",
        "actin_params": [],
        "new_rule": False
    }
]
        expected_output = [
    {
        "description": "INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.",
        "actin_rule": "HAS_MEASURABLE_DISEASE",
        "actin_params": [],
        "new_rule": False
    },
    {
        "description": "EXCLUDE Is currently participating in another study of a therapeutic agent",
        "actin_rule": "IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL",
        "actin_params": [],
        "new_rule": False
    }
]
        actual_output = actin.correct_actin_mistakes(input_mappings, self.client, 3)
        self.assertEqual(expected_output, actual_output)