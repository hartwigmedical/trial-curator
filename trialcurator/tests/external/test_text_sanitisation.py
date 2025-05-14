import re
import unittest

from trialcurator.eligibility_sanitiser import llm_sanitise_text
from trialcurator.gemini_client import GeminiClient
from trialcurator.openai_client import OpenaiClient

class TestTextSanitisation(unittest.TestCase):

    def setUp(self):
        self.client = OpenaiClient()
        # self.client = GeminiClient()

    def test_criterion_retention_and_removal(self):

        input_text = '''
Key Inclusion Criteria:
1. Has an Eastern Cooperative Oncology Group (ECOG) performance status of 0 or 1.
5. Willing to provide tumor tissue from newly obtained biopsy from a tumor site that has not been previously irradiated
6. Has adequate organ and bone marrow function as defined in the protocol
7. In the judgement of the investigator, has a life expectancy of at least 3 months
8. Radiotherapy is allowed.
9. Patient's informed consent is required.
'''

        expected_output_text = '''Inclusion Criteria:
- ECOG performance status of 0 or 1
- Willing to provide tumor tissue from a newly obtained biopsy from a tumor site that has not been previously irradiated
- Adequate organ and bone marrow function as defined in the protocol
- In the judgment of the investigator, has a life expectancy of at least 3 months'''

        output_text = llm_sanitise_text(input_text, self.client)

        # remove preceding and trailing blank lines and trailing fullstops
        output_text = re.sub(r'^\s*\n|(\n\s*)+\Z', '', output_text, flags=re.MULTILINE)
        output_text = output_text.strip('.\n\r').replace('.\n', '\n')

        # check that each condition is the one we want. For some rules it seems to change
        # between runs so we look for the substring to check
        # i.e. radiotherapy is allowed and patient consent removed, the rest retained
        lines = output_text.split('\n')
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], 'Inclusion Criteria:')
        self.assertIn('ECOG performance status of 0 or 1', lines[1])
        self.assertEqual(lines[2], '- Willing to provide tumor tissue from a newly obtained biopsy from a tumor site that has not been previously irradiated')
        self.assertIn('adequate organ and bone marrow function', lines[3].lower())
        self.assertIn('life expectancy of at least 3 months', lines[4].lower())

    def test_criterion_splitting(self):
        input_text = '''
Exclusion Criteria:
1. Haematocrit ≥ 50%, untreated severe obstructive sleep apnoea or poorly controlled heart failure (NYHA >1)
        '''

        expected_output_text = '''Exclusion Criteria:

- Hematocrit ≥ 50%
- Untreated severe obstructive sleep apnea
- Poorly controlled heart failure (NYHA > 1)'''

        output_text = llm_sanitise_text(input_text, self.client)

        # remove preceding and trailing blank lines and trailing fullstops
        output_text = output_text.strip('.\n\r').replace('.\n', '\n')
        self.assertEqual(expected_output_text, output_text)
