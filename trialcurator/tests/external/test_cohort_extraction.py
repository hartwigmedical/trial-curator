import json
import unittest
import pandas as pd

from pathlib import Path

from trialcurator.eligibility_curator import *
from trialcurator.eligibility_sanitiser import llm_extract_cohorts
from trialcurator.openai_client import OpenaiClient

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

logger.setLevel(logging.DEBUG)

class TestCohortExtraction(unittest.TestCase):

    def setUp(self):
        #self.client = OpenaiClient(TEMPERATURE)
        self.client = GeminiClient(TEMPERATURE)

    def test_extract_from_header(self):
        criteria = '''
Newly Diagnosed Inclusion Criteria:
- Age ≥ 18 years.
- Histologically confirmed Grade IV GBM, inclusive of gliosarcoma (WHO criteria; IDH wild-type by IHC or sequencing for IDH) established following either a surgical resection or biopsy.
- Karnofsky performance status ≥ 60% performed within a 14-day window prior to randomization.

Recurrent Inclusion Criteria:
- Age ≥ 18 years.
- Histologically confirmed Grade IV GBM, inclusive of gliosarcoma (WHO criteria; IDH wild-type by IHC or sequencing for IDH) at first or second recurrence after initial standard, control or experimental therapy that includes at a minimum radiation therapy (RT).
- Evidence of recurrent disease demonstrated by disease progression using slightly modified RANO criteria.

Newly Diagnosed Exclusion Criteria:
- Received any prior treatment for glioma including:
  - Prior prolifeprospan 20 with carmustine wafer.
  - Prior intracerebral, intratumoral, or CSF agent.
  - Prior radiation treatment for GBM or lower-grade glioma.
- Receiving additional, concurrent, active therapy for GBM outside of the trial.
- Extensive leptomeningeal disease.

Recurrent Exclusion Criteria:
- Early disease progression prior to 3 months (12 weeks) from the completion of RT.
- More than 2 prior lines for chemotherapy administration.
- Received any prior treatment with lomustine, agents part of any of the experimental arms, and bevacizumab or other VEGF or VEGF receptor-mediated targeted agent.
- Any prior treatment with prolifeprospan 20 with carmustine wafer.
        '''

        cohorts = llm_extract_cohorts(criteria, self.client)
        self.assertEqual(['Newly Diagnosed', 'Recurrent'], cohorts)

    def test_extract_from_inline_phrases(self):
        criteria = '''
Inclusion Criteria:

- Ovarian Cancer Cohorts Only: Histologically or cytologically confirmed diagnosis of advanced, epithelial ovarian cancer (except carcinosarcoma), primary peritoneal, or fallopian tube cance
r.
- Ovarian Cancer Cohorts Only: Serum CA-125 level ≥2 x ULN (in screening).
- Received at least 1 line of platinum-containing therapy or must be platinum-intolerant.
- Documented relapse or progression on or after the most recent line of therapy.
- No standard therapy options likely to convey clinical benefit.
- Adequate organ and bone marrow function as defined in the protocol.
- Life expectancy of at least 3 months.
- Endometrial Cancer Cohorts Only:
  - Histologically confirmed endometrial cancer that has progressed or recurred after prior PD-1 therapy and platinum-based chemotherapy.
  - MUC16 positivity of tumor cells ≥25% by IHC.
  - 1-2 prior lines of systemic therapy.

Exclusion Criteria:

- Prior treatment with anti-PD-1/PD-L1 therapy, as described in the protocol.
- Ovarian Cancer cohorts only: More than 4 prior lines of cytotoxic chemotherapy.
- Endometrial cancer cohorts: Prior treatment with a MUC16-targeted therapy.
- Untreated or active primary brain tumor, CNS metastases, or spinal cord compression, as described in the protocol.
- History and/or current cardiovascular disease, as defined in the protocol.
- Severe and/or uncontrolled hypertension at screening.
'''
        cohorts = llm_extract_cohorts(criteria, self.client)
        self.assertEqual(["Ovarian Cancer Cohorts",  "Endometrial Cancer Cohorts"], cohorts)

    def test_extract_from_header_phrase(self):
        criteria = '''
Inclusion Criteria part 1:
- Patients referred to a participating research centre with suspicion of or confirmed endometrial cancer.

Exclusion Criteria part 1:
- Patients who do not have endometrial cancer.
- Patients <18 years of age.
- Patients who will not get surgical treatment for their endometrial cancer.

Inclusion Criteria part 2:
- Patients with endometrial or epithelial ovarian cancer who, following routine clinical guidelines, are offered weekly taxane (paclitaxel) treatment.
- Technical possibility to obtain a new tissue biopsy to determine stathmin level in the tumour recurrence.

Exclusion Criteria part 2:
- Patients not suffering from endometrial or epithelial ovarian cancer.
- Patients <18 years of age.
- Patients who do not agree to the proposed treatment or will receive (part of) the treatment in a non-participating centre.
'''
        cohorts = llm_extract_cohorts(criteria, self.client)
        self.assertEqual(['part 1', 'part 2'], cohorts)
