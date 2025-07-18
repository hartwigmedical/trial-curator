import pytest

from trialcurator.eligibility_sanitiser import llm_exclusion_logic_flipping
from trialcurator.openai_client import OpenaiClient


@pytest.fixture
def client():
    return OpenaiClient()
    # return GeminiClient()


def test_flip_lab_value_exclusion(client):
    # The following criteria are all EXCLUSION rules
    input_text = """
QTcF interval > 470 ms at screening
PR interval > 230 ms at screening
QRS interval > 120 ms at screening
ANC < 1,500/mm^3
Platelet count < 100,000/mm^3
Bilirubin > 1.5 mg/dL (> 26 μmol/L, SI unit equivalent)
AST ≥ 3 × ULN (if related to liver metastases > 5 × ULN)
ALT ≥ 3 × ULN (if related to liver metastases > 5 × ULN)
"""

    expected_output = [
        {"exclude": False,
         "flipped": True,
         "rule": "QTcF interval ≤ 470 ms at screening"},
        {"exclude": False,
         "flipped": True,
         "rule": "PR interval ≤ 230 ms at screening"},
        {"exclude": False,
         "flipped": True,
         "rule": "QRS interval ≤ 120 ms at screening"},
        {"exclude": False,
         "flipped": True,
         "rule": "ANC ≥ 1,500/mm^3"},
        {"exclude": False,
         "flipped": True,
         "rule": "Platelet count ≥ 100,000/mm^3"},
        {"exclude": False,
         "flipped": True,
         "rule": "Bilirubin ≤ 1.5 mg/dL (≤ 26 μmol/L, SI unit equivalent)"},
        {"exclude": False,
         "flipped": True,
         "rule": "AST < 3 × ULN (if related to liver metastases ≤ 5 × ULN)"},
        {"exclude": False,
         "flipped": True,
         "rule": "ALT < 3 × ULN (if related to liver metastases ≤ 5 × ULN)"}
    ]

    output = llm_exclusion_logic_flipping(input_text, client)
    assert output == expected_output


def test_non_lab_value_exclusion(client):
    # The following criteria are all EXCLUSION rules
    input_text = """
Does not demonstrate adequate organ function as defined by laboratory limits
Prior radiotherapy within 2 weeks of start of study intervention
Transfusion of blood products or administration of colony stimulating factors within 4 weeks prior to baseline
"""

    expected_output = [
        {"exclude": False,
         "flipped": True,
         "rule": "Demonstrates adequate organ function as defined by laboratory limits"},
        {"exclude": True,
         "flipped": False,
         "rule": "Prior radiotherapy within 2 weeks of start of study intervention"},
        {"exclude": True,
         "flipped": False,
         "rule": "Transfusion of blood products or administration of colony stimulating factors within 4 weeks prior to baseline"}
    ]

    output = llm_exclusion_logic_flipping(input_text, client)
    assert output == expected_output


def test_redundant_phrasing(client):
    # The following criteria are all EXCLUSION rules
    input_text = '''
Participants must not have diabetes
Participants must not have EGFR mutation
'''

    expected_output = [
        {"exclude": True,
         "flipped": False,
         "rule": "Patients who have diabetes"},
        {"exclude": True,
         "flipped": False,
         "rule": "Patients who have EGFR mutation"}
    ]

    output = llm_exclusion_logic_flipping(input_text, client)

    # sometimes the output uses Participants instead of Patients, harmonise it
    for criterion in output:
        rule = criterion.get("rule")
        if isinstance(rule, str):
            harmonized = rule.replace("Participants", "patients").replace("patients", "Patients")
            criterion["rule"] = harmonized

    assert output == expected_output


def test_difficult_flipping(client):
    # The following criterion is an EXCLUSION rule
    input_text = """
Haematocrit ≥ 50%, untreated severe obstructive sleep apnea or poorly controlled heart failure (NYHA > 1)
"""
    expected_output = [
        {"exclude": False,
         "flipped": True,
         "rule": "Haematocrit < 50%"},
        {"exclude": True,
         "flipped": False,
         "rule": "untreated severe obstructive sleep apnea"},
        {"exclude": True,
         "flipped": False,
         "rule": "poorly controlled heart failure (NYHA > 1)"}
    ]

    output = llm_exclusion_logic_flipping(input_text, client)
    assert output == expected_output
