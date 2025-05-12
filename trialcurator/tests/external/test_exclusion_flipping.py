import unittest

from trialcurator.eligibility_sanitiser import llm_simplify_text_logic
from trialcurator.gemini_client import GeminiClient
from trialcurator.openai_client import OpenaiClient

# test exclusion rules in input are correctly converted to inclusion rules in output
# NCT00875433
# TO DO: Serum creatinine > 1.5 x ULN or calculated/measured creatinine clearance ≥ 45 mL/min. --> checking this rule is medically incorrect as clearance should be <=

class TestExclusionFlipping(unittest.TestCase):

    def setUp(self):
        self.client = OpenaiClient()
        #self.client = GeminiClient()

    def test_flip_labvalue_exclusion(self):
        input_text = '''
EXCLUDE QTcF interval > 470 ms at screening.
EXCLUDE PR interval > 230 ms at screening.
EXCLUDE QRS interval > 120 ms at screening.
EXCLUDE ANC < 1,500/mm^3.
EXCLUDE Platelet count < 100,000/mm^3.
EXCLUDE Bilirubin > 1.5 mg/dL (> 26 μmol/L, SI unit equivalent).
EXCLUDE AST ≥ 3 × ULN (if related to liver metastases > 5 × ULN).
EXCLUDE ALT ≥ 3 × ULN (if related to liver metastases > 5 × ULN).
'''

        expected_output_text = '''INCLUDE QTcF interval ≤ 470 ms at screening
INCLUDE PR interval ≤ 230 ms at screening
INCLUDE QRS interval ≤ 120 ms at screening
INCLUDE ANC ≥ 1,500/mm^3
INCLUDE Platelet count ≥ 100,000/mm^3
INCLUDE Bilirubin ≤ 1.5 mg/dL (≤ 26 μmol/L, SI unit equivalent)
INCLUDE AST < 3 × ULN (if related to liver metastases ≤ 5 × ULN)
INCLUDE ALT < 3 × ULN (if related to liver metastases ≤ 5 × ULN)'''

        output_text = llm_simplify_text_logic(input_text, self.client)

        # remove any trailing fullstops
        output_text = output_text.replace('.\n', '\n')

        # check that the number of trial groups are the same
        self.assertEqual(expected_output_text, output_text)

    def test_non_labvalue_exclusion(self):
        input_text = '''
EXCLUDE Does not demonstrate adequate organ function as defined by laboratory limits.
EXCLUDE Prior radiotherapy within 2 weeks of start of study intervention.
EXCLUDE Transfusion of blood products or administration of colony stimulating factors within 4 weeks prior to baseline.
'''

        expected_output_text = '''INCLUDE Demonstrates adequate organ function as defined by laboratory limits
EXCLUDE Prior radiotherapy within 2 weeks of start of study intervention
EXCLUDE Transfusion of blood products or administration of colony stimulating factors within 4 weeks prior to baseline'''

        output_text = llm_simplify_text_logic(input_text, self.client)
        # remove any trailing fullstops
        output_text = output_text.replace('.\n', '\n')

        # check that the number of trial groups are the same
        self.assertEqual(expected_output_text, output_text)

    def test_redundant_phrasing(self):
        input_text = '''
EXCLUDE Participants must not have diabetes
EXCLUDE Participants must not have EGFR mutation
'''

        expected_output_text = '''EXCLUDE Patients who have diabetes
EXCLUDE Patients who have EGFR mutation'''

        output_text = llm_simplify_text_logic(input_text, self.client)
        # remove any trailing fullstops
        output_text = output_text.replace('.\n', '\n')

        # sometimes the output uses Participants instead of Patients, harmonise it
        output_text = output_text.replace('Participants', 'patients')
        output_text = output_text.replace('patients', 'Patients')

        # check that the number of trial groups are the same
        self.assertEqual(expected_output_text, output_text)
