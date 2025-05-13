import unittest
from pathlib import Path

from trialcurator.eligibility_curator import parse_actin_output_to_json
import trialcurator.eligibility_curator_actin as actin
from trialcurator.openai_client import OpenaiClient

actin_rules = actin.load_actin_rules(str(Path(__file__).resolve().parent/"data/ACTIN_test_cases/ACTIN_CompleteList_03042025.csv"))

class TestActinMappingAccuracy(unittest.TestCase):

    def setUp(self):
        self.client = OpenaiClient()

    def test_currently_correct(self):
    # Initial mappings are expected to be correct

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
        expected_output = '''
HAS_LIFE_EXPECTANCY_OF_AT_LEAST_X_MONTHS[3]
NOT(HAS_KNOWN_ACTIVE_CNS_METASTASES)
CAN_PROVIDE_FRESH_TISSUE_SAMPLE_FOR_FURTHER_ANALYSIS
NOT(IS_PREGNANT)
IS_AT_LEAST_X_YEARS_OLD[18]
HAS_WHO_STATUS_OF_AT_MOST_X[1]
HAS_ADEQUATE_ORGAN_FUNCTION
NOT(HAS_ACTIVE_SECOND_MALIGNANCY)
'''
        actual_output = parse_actin_output_to_json("-", actin.map_to_actin(input_text, self.client, actin_rules))["mappings"]
        actin_output = "\n".join([x["ACTIN_rules"] for x in actual_output])
        self.assertEqual(expected_output.strip(), actin_output)

    def test_drug_category_initial(self):
    # Initial mappings are expected to be incorrect (to be corrected with subsequent prompt)

        input_text = '''
- INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1
- EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol
'''
        expected_output = '''
HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[IMMUNOTHERAPY, ANTI_PD_1; ANTI_PD_L1; PD_1_PD_L1_ANTIBODY]
NOT(HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[TARGETED_THERAPY, EGFR_ANTIBODY])
'''
        actual_output = parse_actin_output_to_json("-", actin.map_to_actin(input_text, self.client, actin_rules))["mappings"]
        actin_output = "\n".join([x["ACTIN_rules"] for x in actual_output])
        self.assertEqual(expected_output.strip(), actin_output)

    def test_drug_category_correction(self):
    # Corrections on initial mappings
    # Notes: Reproducibility remains an issue. This test can still fail at times.

        input_text = '''
- INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1
- EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol
'''
        expected_output = '''
NOT(HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[IMMUNOTHERAPY, PD-1 antibody])
NOT(HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[TARGETED THERAPY, EGFR antibody])
'''
        actual_output_initial = parse_actin_output_to_json("-", actin.map_to_actin(input_text, self.client, actin_rules))["mappings"]
        actin_output_initial = "\n".join([x["ACTIN_rules"] for x in actual_output_initial])
        actin_output_corrected = actin.correct_common_actin_mistakes(actin_output_initial, self.client)
        self.assertEqual(expected_output.strip(), actin_output_corrected.lstrip())

    def test_other_incorrect_initial(self):
    # Initial mappings are expected to be incorrect

        input_text = '''
- INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.
- EXCLUDE Is currently participating in another study of a therapeutic agent
'''
        expected_output = '''
HAS_MEASURABLE_DISEASE
IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL
'''
        actual_output = parse_actin_output_to_json("-", actin.map_to_actin(input_text, self.client, actin_rules))["mappings"]
        actin_output = "\n".join([x["ACTIN_rules"] for x in actual_output])
        self.assertEqual(expected_output.strip(), actin_output)

    def test_other_incorrect_correction(self):
    # Corrections on initial mappings

        input_text = '''
- INCLUDE Participants must have at least one measurable lesion per response evaluation criteria in solid tumors.
- EXCLUDE Is currently participating in another study of a therapeutic agent
'''
        expected_output = '''
HAS_MEASURABLE_DISEASE
IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL
'''
        actual_output_initial = parse_actin_output_to_json("-", actin.map_to_actin(input_text, self.client, actin_rules))["mappings"]
        actin_output_initial = "\n".join([x["ACTIN_rules"] for x in actual_output_initial])
        actin_output_corrected = actin.correct_common_actin_mistakes(actin_output_initial, self.client)
        self.assertEqual(expected_output.strip(), actin_output_corrected.lstrip())
