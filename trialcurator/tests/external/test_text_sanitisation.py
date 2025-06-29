import re
import pytest

from trialcurator.eligibility_sanitiser import llm_sanitise_text
from trialcurator.openai_client import OpenaiClient

def remove_blank_lines_and_trailing_footstops(text: str) -> str:
    return (re.sub(r'^\s*\n|(\n\s*)+\Z', '', text, flags=re.MULTILINE)
            .strip('.\n\r')
            .replace('.\n', '\n'))

@pytest.fixture
def client():
    return OpenaiClient()
    # return GeminiClient()

def test_criterion_retention_and_removal(client):
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

    output_text = llm_sanitise_text(input_text, client)

    # remove preceding and trailing blank lines and trailing fullstops
    output_text = output_text = remove_blank_lines_and_trailing_footstops(output_text)

    # check that each condition is the one we want. For some rules it seems to change
    # between runs so we look for the substring to check
    # i.e. radiotherapy is allowed and patient consent removed, the rest retained
    lines = output_text.split('\n')
    assert len(lines) == 5
    assert lines[0] == 'Inclusion Criteria:'
    assert 'ECOG performance status of 0 or 1' in lines[1]
    assert (lines[2] ==
            '- Willing to provide tumor tissue from a newly obtained biopsy from a tumor site that has not been previously irradiated')
    assert 'adequate organ and bone marrow function' in lines[3].lower()
    assert 'life expectancy of at least 3 months' in lines[4].lower()

def test_criterion_splitting(client):
    input_text = '''
Exclusion Criteria:
1. Haematocrit ≥ 50%, untreated severe obstructive sleep apnoea or poorly controlled heart failure (NYHA >1)
        '''

    expected_output_text = '''Exclusion Criteria:
- Hematocrit ≥ 50%
- Untreated severe obstructive sleep apnea
- Poorly controlled heart failure (NYHA > 1)'''

    output_text = llm_sanitise_text(input_text, client)

    # remove preceding and trailing blank lines and trailing fullstops
    output_text = remove_blank_lines_and_trailing_footstops(output_text)
    assert output_text == expected_output_text

def test_removal_redundant_sex(client):
    input_text = '''
Inclusion Criteria:
* Male or female, aged 18 years or older at the time consent is obtained.
* Men and women must use effective contraceptive methods.
* Both males and females must agree to use highly effective contraceptive precautions if conception is possible during \
the dosing period and up to 3 months after dosing
* Females of childbearing potential must have a negative urine or serum pregnancy test within 72 hours prior to \
receiving the first dose of study medication
* Female participants with histologically-confirmed diagnosis of HGS ovarian, primary peritoneal, or fallopian tube cancer
Exclusion Criteria:
* For women only: pregnant or breastfeeding
'''

    expected_output_text = '''Inclusion Criteria:
- Aged 18 years or older
- Must use effective contraceptive methods
- Must agree to use highly effective contraceptive precautions if conception is possible during the dosing period and up to \
3 months after dosing
- Females of childbearing potential must have a negative urine or serum pregnancy test within 72 hours prior \
to receiving the first dose of study medication
- Participants with histologically-confirmed diagnosis of HGS ovarian, primary peritoneal, or fallopian tube cancer
Exclusion Criteria:
- Pregnant or breastfeeding'''

    output_text = llm_sanitise_text(input_text, client)

    # remove blank lines
    output_text = remove_blank_lines_and_trailing_footstops(output_text)
    assert output_text == expected_output_text

def test_removal_redundant_sex2(client):
    input_text = '''
Inclusion Criteria:
* Male or female
* Men with prostate cancer
* Is female and not pregnant/breastfeeding and at least one of the following applies during the study and for ≥4 days after:
  - Is not a WOCBP
  - Is a WOCBP and uses highly effective contraception
  - Is a WOCBP who is abstinent from heterosexual intercourse
'''

    expected_output_text = '''Inclusion Criteria:
- Men with prostate cancer
- Not pregnant or breastfeeding
- At least one of the following applies during the study and for ≥4 days after:
  - Not a WOCBP
  - WOCBP and uses highly effective contraception
  - WOCBP who is abstinent from heterosexual intercourse'''

    output_text = llm_sanitise_text(input_text, client)

    # remove blank lines
    output_text = remove_blank_lines_and_trailing_footstops(output_text)
    assert "male or female" not in output_text.lower()
    assert "female" not in output_text.lower()
