import unittest

from ...eligibility_sanitiser import llm_simplify_and_tag_text
from ...gemini_client import GeminiClient
from ...openai_client import OpenaiClient

# test exclusion rules in input are correctly converted to inclusion rules in output
# NCT00875433
# TO DO: Serum creatinine > 1.5 x ULN or calculated/measured creatinine clearance ≥ 45 mL/min. --> checking this rule is medically incorrect as clearance should be <=

class TestExclusionFlipping(unittest.TestCase):

    def setUp(self):
        #self.client = OpenaiClient()
        self.client = GeminiClient()

    def test_flip_labvalue_exclusion(self):
        input_text = '''
Exclusion Criteria:

- QTcF interval > 470 ms at screening.
- PR interval > 230 ms at screening.
- QRS interval > 120 ms at screening.
- ANC < 1,500/mm^3.
- Platelet count < 100,000/mm^3.
- Bilirubin > 1.5 mg/dL (> 26 μmol/L, SI unit equivalent).
- AST ≥ 3 × ULN (if related to liver metastases > 5 × ULN).
- ALT ≥ 3 × ULN (if related to liver metastases > 5 × ULN).
'''

        expected_output_text = '''
INCLUDE QTcF interval ≤ 470 ms at screening
INCLUDE PR interval ≤ 230 ms at screening
INCLUDE QRS interval ≤ 120 ms at screening
INCLUDE ANC ≥ 1,500/mm^3
INCLUDE Platelet count ≥ 100,000/mm^3
INCLUDE Bilirubin ≤ 1.5 mg/dL (≤ 26 μmol/L, SI unit equivalent)
INCLUDE AST < 3 × ULN (if related to liver metastases ≤ 5 × ULN)
INCLUDE ALT < 3 × ULN (if related to liver metastases ≤ 5 × ULN)
'''

        output_text = llm_simplify_and_tag_text(input_text, self.client)

        # check that the number of trial groups are the same
        self.assertEqual(output_text, expected_output_text)

    def test_non_labvalue_exclusion(self):
        input_text = '''
Exclusion Criteria:
- Does not demonstrate adequate organ function.
- Prior radiotherapy within 2 weeks of start of study intervention.
- Transfusion of blood products or administration of colony stimulating factors within 4 weeks prior to baseline.
'''

        expected_output_text = '''INCLUDE Demonstrate adequate organ function as defined by laboratory limits.
EXCLUDE Prior radiotherapy within 2 weeks of start of study intervention.
EXCLUDE Transfusion of blood products or administration of colony stimulating factors within 4 weeks prior to baseline.
'''

        output_text = llm_simplify_and_tag_text(input_text, self.client)

        # check that the number of trial groups are the same
        self.assertEqual(output_text, expected_output_text)
