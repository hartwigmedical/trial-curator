import logging
import pytest

from trialcurator.eligibility_sanitiser import llm_extract_cohorts
from trialcurator.openai_client import OpenaiClient

logger = logging.getLogger(__name__)

@pytest.fixture
def client():
    return OpenaiClient()

def test_extract_from_header(client):
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

    cohorts = llm_extract_cohorts(criteria, client)
    assert cohorts == ['Newly Diagnosed', 'Recurrent']

def test_extract_from_inline_phrases(client):
    criteria = '''
Inclusion Criteria:

- Ovarian Cancer Cohorts Only: Histologically or cytologically confirmed diagnosis of advanced, epithelial ovarian \
cancer (except carcinosarcoma), primary peritoneal, or fallopian tube cancer.
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
    cohorts = llm_extract_cohorts(criteria, client)
    assert cohorts == ["Ovarian Cancer Cohorts", "Endometrial Cancer Cohorts"]

def test_extract_from_header_phrase(client):
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
    cohorts = llm_extract_cohorts(criteria, client)
    assert cohorts == ['part 1', 'part 2']

def test_default_and_expansion_cohorts(client):
    criteria = '''
Inclusion Criteria:
- Has an ECOG performance status of 0 or 1
- Has histologically or cytologically confirmed cancer that meets criteria as defined in the protocol
- Expansion Cohorts only: Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1
- Has at least 1 lesion that meets study criteria as defined in the protocol
- Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site that has not been previously irradiated
- Has adequate organ and bone marrow function as defined in the protocol
- In the judgement of the investigator, has a life expectancy of at least 3 months

Exclusion Criteria:
- Is currently participating in another study of a therapeutic agent
- Has participated in any study of an investigational agent or an investigational device within 4 weeks of the first administration of study drug as defined in the protocol
- Has received treatment with an approved systemic therapy within 4 weeks of the first administration of study drug or has not yet recovered (i.e., grade 1 or baseline) from any acute toxicities
- Has received recent anti-EGFR antibody therapy as defined in the protocol
- Has received radiation therapy or major surgery within 14 days of the first administration of study drug or has not recovered (i.e., grade 1 or baseline) from adverse events
- Has received any previous systemic, non-immunomodulatory biologic therapy within 4 weeks of first administration of study drug
- Has had prior anti-cancer immunotherapy within 5 half-lives prior to study drug as defined in the protocol
- Has second malignancy that is progressing or requires active treatment as defined in the protocol
- Has any condition requiring ongoing/continuous corticosteroid therapy (>10 mg prednisone/day or anti-inflammatory equivalent) within 1-2 weeks prior to the first dose of study drug as defined in the protocol
- Has ongoing or recent (within 5 years) evidence of significant autoimmune disease or any other condition that required treatment with systemic immunosuppressive treatments as defined in the protocol
- Has untreated or active primary brain tumor, CNS metastases, leptomeningeal disease, or spinal cord compression
- Has encephalitis, meningitis, organic brain disease (e.g., Parkinson's disease) or uncontrolled seizures within 1 year prior to the first dose of study drug
- Has any ongoing inflammatory skin disease as defined in the protocol    
'''
    cohorts = llm_extract_cohorts(criteria, client)
    assert cohorts == ['default', 'Expansion Cohorts']
