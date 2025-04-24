import unittest

from trialcurator.eligibility_sanitiser import llm_simplify_and_tag_text
from trialcurator.openai_client import OpenaiClient

# test that this input gets the following output

input_text = '''
Inclusion Criteria:

- Age ≥ 18 years
- Biopsy proven primary adenocarcinoma (or undifferentiated carcinoma) of the stomach, including tumors at the oesophagogastric junction provided that the bulk of the tumor is located in the stomach, and the intended surgical treatment is a gastric resection and not an oesophagectomy
- cT3-cT4 tumor (TNM classification, 7th edition), considered to be resectable (including lymph nodes)
- Limited peritoneal carcinomatosis (PCI < 7) and/or tumor positive peritoneal cytology confirmed by laparoscopy or laparotomy and proven by pathological examination
- Treatment with systemic chemotherapy, with the latest course ending within 8 weeks prior to inclusion
- Absence of disease progression during systemic chemotherapy (prior to inclusion)
- WHO performance status 0-2
- Adequate bone marrow, hepatic and renal function

  - ANC ≥ 1.5 x 10^9/L
  - Platelet count ≥ 100 x 10^9/L
  - Serum bilirubin ≤ 1.5 x ULN
  - ALAT and ASAT ≤ 2.5 x ULN
  - Creatinine clearance ≥ 50 mL/min (measured or calculated by Cockcroft-Gault formula)

- For female patients who are not sterilized or in menopause:

  - Negative pregnancy test (urine/serum)
  - No breastfeeding or active pregnancy ambition
  - Reliable contraceptive methods

Exclusion Criteria:

- Distant metastases (e.g., liver, lung, para-aortic lymph nodes; i.e., stations 14 and 16) or small bowel dissemination
- Recurrent gastric cancer
- Prior resection of the primary gastric tumor
- Non-synchronous peritoneal carcinomatosis
- Current other malignancy (other than cervix carcinoma and basalioma)
- Uncontrolled infectious disease or known infection with HIV-1 or -2
- A known history of HBV or HCV with active viral replication
- Recent myocardial infarction (< 6 months) or unstable angina
'''

expected_output_text = '''
INCLUDE Age ≥ 18 years
INCLUDE Biopsy proven primary adenocarcinoma (or undifferentiated carcinoma) of the stomach, including tumors at the oesophagogastric junction provided that the bulk of the tumor is located in the stomach, and the intended surgical treatment is a gastric resection and not an oesophagectomy
INCLUDE cT3-cT4 tumor (TNM classification, 7th edition), considered to be resectable (including lymph nodes)
INCLUDE Limited peritoneal carcinomatosis (PCI < 7) and/or tumor positive peritoneal cytology confirmed by laparoscopy or laparotomy and proven by pathological examination
INCLUDE Treatment with systemic chemotherapy, with the latest course ending within 8 weeks prior to inclusion
INCLUDE Absence of disease progression during systemic chemotherapy (prior to inclusion)
INCLUDE WHO performance status 0-2
INCLUDE Adequate bone marrow, hepatic and renal function
  - ANC ≥ 1.5 x 10^9/L
  - Platelet count ≥ 100 x 10^9/L
  - Serum bilirubin ≤ 1.5 x ULN
  - ALAT and ASAT ≤ 2.5 x ULN
  - Creatinine clearance ≥ 50 mL/min (measured or calculated by Cockcroft-Gault formula)
INCLUDE For female patients who are not sterilized or in menopause:
  - Negative pregnancy test (urine/serum)
  - No breastfeeding or active pregnancy ambition
  - Reliable contraceptive methods
EXCLUDE Distant metastases (e.g., liver, lung, para-aortic lymph nodes; i.e., stations 14 and 16) or small bowel dissemination
EXCLUDE Recurrent gastric cancer
EXCLUDE Prior resection of the primary gastric tumor
EXCLUDE Non-synchronous peritoneal carcinomatosis
EXCLUDE Current other malignancy (other than cervix carcinoma and basalioma)
EXCLUDE Uncontrolled infectious disease or known infection with HIV-1 or -2
EXCLUDE A known history of HBV or HCV with active viral replication
EXCLUDE Recent myocardial infarction (< 6 months) or unstable angina
'''


class TestEligibilitySanitiser(unittest.TestCase):

    # test that simplify and tagging gets the correct output
    def test_simplify_and_tag_text(self):

        client = OpenaiClient(0)
        output_text = llm_simplify_and_tag_text(input_text, client)

        # check that the number of trial groups are the same
        self.assertEqual(output_text.casefold(), expected_output_text.casefold())
