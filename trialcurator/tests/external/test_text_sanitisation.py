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

        expected_output_text = '''
Inclusion Criteria
- ECOG performance status of 0 or 1
- Willing to provide tumor tissue from newly obtained biopsy from a tumor site that has not been previously irradiated
- Adequate organ and bone marrow function as defined in the protocol
- In the judgement of the investigator, has a life expectancy of at least 3 months
'''
        output_text = llm_sanitise_text(input_text, self.client)

        # remove any trailing fullstops
        output_text = output_text.strip('.').replace('.\n', '\n')

        self.assertEqual(expected_output_text, output_text)
