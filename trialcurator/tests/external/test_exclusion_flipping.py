import unittest
import difflib

from trialcurator.eligibility_sanitiser import llm_simplify_and_tag_text
from trialcurator.openai_client import OpenaiClient

# test exclusion rules in input are correctly converted to inclusion rules in output
# NCT00875433
# TO DO: Serum creatinine > 1.5 x ULN or calculated/measured creatinine clearance ≥ 45 mL/min. --> checking this rule is medically incorrect as clearance should be <=

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


class TestExclusionFlipping(unittest.TestCase):

    def test_simplify_and_tag_text(self):

        client = OpenaiClient(0)
        output_text = llm_simplify_and_tag_text(input_text, client)

        actual_output_lines = output_text.strip().splitlines()
        expected_output_lines = expected_output_text.strip().splitlines()

        different_rules = []

        if actual_output_lines != expected_output_lines:

            for line in difflib.unified_diff(expected_output_lines, actual_output_lines):

                if line.startswith("-") or line.startswith("+"):

                    different_rules.append(line)

            diff = "\n".join(different_rules)
            self.fail(f"Output differs from expectation:\n{diff}")