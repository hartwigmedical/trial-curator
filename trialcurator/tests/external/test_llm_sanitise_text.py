import re
import pytest

from trialcurator.eligibility_text_preparation import llm_sanitise_text
from trialcurator.openai_client import OpenaiClient
from trialcurator.gemini_client import GeminiClient


def remove_blank_lines_and_trailing_footstops(text: str) -> str:
    return (re.sub(r'^\s*\n|(\n\s*)+\Z', '', text, flags=re.MULTILINE)
            .strip('.\n\r')
            .replace('.\n', '\n'))


def assert_against_variable_outputs(actual: str, expected: str | list) -> None:
    for line, idx in zip(actual.splitlines(), range(len(expected))):
        if isinstance(expected[idx], str):
            assert line == expected[idx]
        elif isinstance(expected[idx], list):
            assert line in expected[idx]
        else:
            raise TypeError(f"Type error in {expected[idx]} which is of type {type(expected[idx])}")


@pytest.fixture
def client():
    # return OpenaiClient()
    return GeminiClient(model="gemini-2.5-pro")


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
- Willing to provide tumor tissue from newly obtained biopsy from a tumor site that has not been previously irradiated
- Adequate organ and bone marrow function as defined in the protocol
- In the judgment of the investigator, has a life expectancy of at least 3 months'''

    output_text = llm_sanitise_text(input_text, client)

    # remove preceding and trailing blank lines and trailing fullstops
    output_text = remove_blank_lines_and_trailing_footstops(output_text)

    # check that each condition is the one we want. For some rules it seems to change
    # between runs so we look for the substring to check
    # i.e. radiotherapy is allowed and patient consent removed, the rest retained
    lines = output_text.split('\n')
    assert len(lines) == 5
    assert lines[0] == 'Inclusion Criteria:'
    assert 'ECOG performance status of 0 or 1' in lines[1]
    assert (lines[2] == '- Willing to provide tumor tissue from newly obtained biopsy from a tumor site that has not been previously irradiated')
    assert 'adequate organ and bone marrow function' in lines[3].lower()
    assert 'life expectancy of at least 3 months' in lines[4].lower()


def test_criterion_splitting(client):
    input_text = '''
Exclusion Criteria:
1. Haematocrit ≥ 50%, untreated severe obstructive sleep apnea or poorly controlled heart failure (NYHA > 1)
'''

    expected_output_text = '''Exclusion Criteria:
- Hematocrit ≥ 50%
- Untreated severe obstructive sleep apnea
- Poorly controlled heart failure (NYHA > 1)'''

    output_text = llm_sanitise_text(input_text, client)
    output_text = remove_blank_lines_and_trailing_footstops(output_text)
    assert output_text.replace("0.50", "50%") == expected_output_text


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
- Histologically-confirmed diagnosis of HGS ovarian cancer
- Histologically-confirmed diagnosis of HGS primary peritoneal cancer
- Histologically-confirmed diagnosis of HGS fallopian tube cancer
Exclusion Criteria:
- Pregnant
- Breastfeeding'''

    output_text = llm_sanitise_text(input_text, client)
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

    expected_output = ["Inclusion Criteria:",
                       ["- Men with prostate cancer", "- Male with prostate cancer",
                        "- Participants with prostate cancer"],
                       ["- Is female and not pregnant or breastfeeding and at least one of the following applies during the study and for ≥4 days after:",
                        "- Is female and not pregnant and not breastfeeding and at least one of the following applies during the study and for ≥4 days after:",
                        "- Is female and not pregnant/breastfeeding and at least one of the following applies during the study and for ≥4 days after:",
                        "- Is female\n- Not pregnant\n- Not breastfeeding\n- At least one of the following applies during the study and for ≥4 days after:",
                        "- Female and not pregnant and not breastfeeding, and at least one of the following applies during the study and for ≥4 days after:"],
                       "  - Is not a WOCBP",
                       "  - Is a WOCBP and uses highly effective contraception",
                       "  - Is a WOCBP who is abstinent from heterosexual intercourse"
                       ]

    actual_output = remove_blank_lines_and_trailing_footstops(llm_sanitise_text(input_text, client))
    assert_against_variable_outputs(actual_output, expected_output)


def test_correct_indentation(client):
    input_text = '''
Key Exclusion Criteria
* Significant acute or chronic hepatitis B virus (HBV), hepatitis C virus (HCV) infection during the screening window, as well as historic positive for human immunodeficiency virus (HIV) or clinically significant active infections that render the patient ineligible for study treatment as determined by the treating investigator.
* Patients with known HIV infection are excluded unless they meet the following criteria:
  * Must have CD4+ T-cell (CD4+) counts ≥ 350 cells/μL at the time of screening, and
  * Must have no history of AIDS-related opportunistic infections of HIV-associated conditions such as Kaposi sarcoma or multicentric Castleman's disease, and
  * Patients on antiretroviral therapy (ART) must have achieved and maintained virologic suppression defined as confirmed HIV RNA level below 50 or the LLOQ (below the limit of detection) using the locally available assay at the time of screening and for at least 12 weeks before screening and agree to continue ART throughout the study
'''

    expected_output = '''
Exclusion Criteria:
  - Significant acute or chronic HBV infection during the screening window
  - Significant acute or chronic HCV infection during the screening window
  - Historic positive for HIV
  - Clinically significant active infections that render the patient ineligible for study treatment as determined by the treating investigator
  - Patients with known HIV infection are excluded unless they meet the following criteria:
    - CD4+ T-cell counts ≥ 350 cells/μL at the time of screening
    - No history of AIDS-related opportunistic infections or HIV-associated conditions such as Kaposi sarcoma or multicentric Castleman's disease
    - On ART with virologic suppression (defined as confirmed HIV RNA level < 50 or the LLOQ) achieved and maintained for at least 12 weeks before screening and at the time of screening
    - Patients on ART must have achieved and maintained virologic suppression, defined as a confirmed HIV RNA level below 50 or the LLOQ, for at least 12 weeks before screening
    - Patients on ART must agree to continue ART throughout the study
'''

    output_text = remove_blank_lines_and_trailing_footstops(llm_sanitise_text(input_text, client))
    assert output_text == expected_output


def test_incorrect_indentation(client):
    input_text = '''
Key Exclusion Criteria
* Significant acute or chronic hepatitis B virus (HBV), hepatitis C virus (HCV) infection during the screening window, as well as historic positive for human immunodeficiency virus (HIV) or clinically significant active infections that render the patient ineligible for study treatment as determined by the treating investigator.
  * Patients with known HIV infection are excluded unless they meet the following criteria:
    * Must have CD4+ T-cell (CD4+) counts ≥ 350 cells/μL at the time of screening, and
    * Must have no history of AIDS-related opportunistic infections of HIV-associated conditions such as Kaposi sarcoma or multicentric Castleman's disease, and
    * Patients on antiretroviral therapy (ART) must have achieved and maintained virologic suppression defined as confirmed HIV RNA level below 50 or the LLOQ (below the limit of detection) using the locally available assay at the time of screening and for at least 12 weeks before screening and agree to continue ART throughout the study
'''

    expected_output = '''
Exclusion Criteria:
  - Significant acute or chronic HBV infection during the screening window
  - Significant acute or chronic HCV infection during the screening window
  - Historic positive for HIV
  - Clinically significant active infections that render the patient ineligible for study treatment as determined by the treating investigator
  - Patients with known HIV infection are excluded unless they meet the following criteria:
    - CD4+ T-cell counts ≥ 350 cells/μL at the time of screening
    - No history of AIDS-related opportunistic infections or HIV-associated conditions such as Kaposi sarcoma or multicentric Castleman's disease
    - On ART with virologic suppression (defined as confirmed HIV RNA level < 50 or the LLOQ) achieved and maintained for at least 12 weeks before screening and at the time of screening
    - Patients on ART must have achieved and maintained virologic suppression, defined as a confirmed HIV RNA level below 50 or the LLOQ, for at least 12 weeks before screening
    - Patients on ART must agree to continue ART throughout the study
'''

    output_text = remove_blank_lines_and_trailing_footstops(llm_sanitise_text(input_text, client))
    assert output_text == expected_output
