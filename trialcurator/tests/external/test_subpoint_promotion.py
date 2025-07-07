import pytest

from trialcurator.eligibility_sanitiser import llm_subpoint_promotion
from trialcurator.openai_client import OpenaiClient


@pytest.fixture
def client():
    return OpenaiClient()


def test_two_levels_promotion(client):
    input_text = '''
- Serum TBIL:
  - Patients with documented Gilbert's syndrome - baseline TBIL < 3 × ULN.
  - Patient with either HCC or liver metastases - baseline TBIL < 3 × ULN.
  - All other patients baseline TBIL < 2 × ULN.
'''

    expected_output = '''
- Patients with documented Gilbert's syndrome - baseline TBIL < 3 × ULN.
- Patient with either HCC or liver metastases - baseline TBIL < 3 × ULN.
- All other patients baseline TBIL < 2 × ULN.
'''

    actual_output = llm_subpoint_promotion(input_text, client)
    assert actual_output == expected_output.strip()


def test_three_levels_promotion(client):
    input_text = '''
Adequate Hepatic function:
  - Serum TBIL:
    - Patients with documented Gilbert's syndrome - baseline TBIL < 3 × ULN.
    - Patient with either HCC or liver metastases - baseline TBIL < 3 × ULN.
    - All other patients baseline TBIL < 2 × ULN.
'''

    expected_output = '''
- Patients with documented Gilbert's syndrome - baseline TBIL < 3 × ULN.
- Patient with either HCC or liver metastases - baseline TBIL < 3 × ULN.
- All other patients baseline TBIL < 2 × ULN.
'''

    actual_output = llm_subpoint_promotion(input_text, client)
    assert actual_output == expected_output.strip()


def test_nested_lab_value_promotions(client):
    input_text = '''
'Adequate hematological, liver, and kidney function as follows:\n'
      '  - Bone marrow reserve:\n'
      '    - ANC ≥ 1.5 × 10^9/L without growth factor support in the 2 '
      'weeks prior to study entry.\n'
      '    - Hemoglobin ≥ 90 g/L without transfusion and/or without growth '
      'factor support in 2 weeks prior to study entry.\n'
      '    - Platelet count ≥ 100 × 10^9/L without transfusion in 2 weeks '
      'prior to study entry.\n'
      '  - Hepatic function:\n'
      '    - AST < 3 × ULN (≤5 × ULN if liver metastases or HCC).\n'
      '    - ALT < 3 × ULN (≤5 × ULN if liver metastases or HCC).\n'
      '  - Renal function:\n'
      '    - Serum creatinine < 1.5 × ULN or CrCL > 50 mL/min, as per the '
      'Cockcroft-Gault Equation.'
'''

    expected_output = '''
- ANC ≥ 1.5 × 10^9/L without growth factor support in the 2 weeks prior to study entry.
- Hemoglobin ≥ 90 g/L without transfusion and/or without growth factor support in 2 weeks prior to study entry.
- Platelet count ≥ 100 × 10^9/L without transfusion in 2 weeks prior to study entry.
- AST < 3 × ULN (≤5 × ULN if liver metastases or HCC).
- ALT < 3 × ULN (≤5 × ULN if liver metastases or HCC).
- Serum creatinine < 1.5 × ULN or CrCL > 50 mL/min, as per the Cockcroft-Gault Equation.  
'''

    actual_output = llm_subpoint_promotion(input_text, client)
    assert actual_output == expected_output.strip()


def test_nested_required_action_promotions(client):
    input_text = '''
'Practice adequate contraceptive measures:\n'
          '  - Female patients must:\n'
          '    - Be of nonchildbearing potential or;\n'
          '    - If of childbearing potential, must have a negative serum '
          'pregnancy test at Screening and a negative urine pregnancy test '
          'before the first study drug administration and on Day 1 of each '
          'Cycle. They must agree not to attempt to become pregnant, must not '
          'donate ova, and must agree to use 2 forms of highly effective '
          'contraceptive method between signing consent, during the study, and '
          'at least 90 days after the last dose of study drug, OR use 1 form '
          'of highly effective contraceptive method, plus an additional '
          'barrier method of contraception between signing consent, during the '
          'study, and at least 90 days after the last dose of study drug.\n'
          '    - Women of childbearing potential with same sex partners '
          '(abstinence from penile vaginal intercourse) are eligible when this '
          'is their preferred and usual lifestyle.\n'
          '  - Male patients must:\n'
          '    - Be willing not to donate sperm and if engaging in sexual '
          'intercourse with a female partner who could become pregnant, a '
          'willingness to use a condom in addition to having the female '
          'partner use a highly effective contraceptive method between signing '
          'consent, during the study, and at least 90 days after the last dose '
          'of the study drug.'
'''

    expected_output = '''
- Female patients must be of nonchildbearing potential.
- Female patients of childbearing potential must have a negative serum pregnancy test at Screening and a negative urine pregnancy test before the first study drug administration and on Day 1 of each Cycle. They must agree not to attempt to become pregnant, must not donate ova, and must agree to use 2 forms of highly effective contraceptive method between signing consent, during the study, and at least 90 days after the last dose of study drug, OR use 1 form of highly effective contraceptive method, plus an additional barrier method of contraception between signing consent, during the study, and at least 90 days after the last dose of study drug.
- Women of childbearing potential with same sex partners (abstinence from penile vaginal intercourse) are eligible when this is their preferred and usual lifestyle.
- Male patients must be willing not to donate sperm and if engaging in sexual intercourse with a female partner who could become pregnant, a willingness to use a condom in addition to having the female partner use a highly effective contraceptive method between signing consent, during the study, and at least 90 days after the last dose of the study drug.
'''

    actual_output = llm_subpoint_promotion(input_text, client)
    assert actual_output == expected_output.strip()


def test_should_not_promote(client):
    input_text = '''
'Patients with brain metastases are excluded, unless all of the '
          'following criteria are met:\n'
          '  - CNS lesions are asymptomatic and previously treated\n'
          '  - Patient does not require ongoing daily steroid treatment for '
          'replacement for adrenal insufficiency (except ≤ 10 mg prednisone '
          '[or equivalent]) for at least 14 days before the first dose of '
          'study drug\n'
          '  - Imaging demonstrates stable disease 28 days after last '
          'treatment'
'''

    expected_output = '''
'Patients with brain metastases are excluded, unless all of the following criteria are met:
  - CNS lesions are asymptomatic and previously treated
  - Patient does not require ongoing daily steroid treatment for replacement for adrenal insufficiency (except ≤ 10 mg prednisone [or equivalent]) for at least 14 days before the first dose of study drug
  - Imaging demonstrates stable disease 28 days after last treatment
'''

    actual_output = llm_subpoint_promotion(input_text, client)
    assert actual_output == expected_output.strip()
