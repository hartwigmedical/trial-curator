import pytest

from pydantic_curator.criterion_parser import parse_criterion, CriterionParser
from pydantic_curator.criterion_schema import DiagnosticFindingCriterion, NotCriterion, IfCriterion, \
    MetastasesCriterion


def test_parse_simple():
    formatted = 'diagnosticfinding(confidence=1.0, finding="Metastatic disease", method="imaging", modality="CT, MRI, \\n or bone scan")'
    criterion = parse_criterion(formatted)
    assert isinstance(criterion, DiagnosticFindingCriterion)
    assert criterion.confidence == 1.0
    assert criterion.finding == "Metastatic disease"
    assert criterion.method == "imaging"
    assert criterion.modality == "CT, MRI, \n or bone scan"

def test_parse_composite():

    formatted = '''
not {
   or {
  histology(histology_type="sarcomatoid"),
  histology (histology_type = "spindle cell" ),
  histology(histology_type="neuroendocrine small cell"),
  and {
    not {
        treatmentoption(treatment_option="standard of care")
    },
    priortherapy(therapy="taxane regimens", number_of_prior_lines(min_inclusive=1))
  }
   }
}
'''

    criterion = parse_criterion(formatted)
    assert isinstance(criterion, NotCriterion)

def test_parse_if_else():

    formatted = '''
if {tissueavailability(confidence=1.0)}
 then {requiredaction(confidence=1.0, action="Allow for correlative biomarker studies")}
 else {requiredaction(confidence=1.0, action="Consent and undergo fresh tumor biopsy")}
'''

    criterion = parse_criterion(formatted)
    assert isinstance(criterion, IfCriterion)

def test_parse_list():

    formatted = '''
metastases(location="CNS", additional_details=["symptomatic", "typical of lung cancer"])
'''

    criterion = parse_criterion(formatted)
    assert isinstance(criterion, MetastasesCriterion)
    assert criterion.location == "CNS"
    assert criterion.additional_details == ["symptomatic", "typical of lung cancer"]

@pytest.mark.parametrize("input_str,expected_type", [
    ("age(age=30)", "age"),
    ("sex(sex=\"female\")", "sex"),
    ("priortherapy(therapy=\"radiotherapy\", timing_info(reference=\"now\", window_days(min_inclusive=30)))", "priortherapy"),
    ("and{age(age=30), sex(sex=\"female\")}", "and"),
    ("not{or{age(age=20), sex(sex=\"male\")}}", "not"),
    ("if{tissueavailability(confidence=1.0)} then{requiredaction(action=\"Consent\")} else {requiredaction(action=\"Biopsy\")}", "if"),
    ("metastases(location=\"liver\", additional_details=[\"one\", \"two\", \"three\"])", "metastases"),
    ("criterion(key=[\"plain\", 5])", "criterion")
])
def test_valid_criteria(input_str, expected_type):
    parser = CriterionParser(input_str)
    result = parser.consume_criterion()
    assert result["type"] == expected_type

def test_invalid_syntax():

    with pytest.raises(ValueError) as excinfo:
        parser = CriterionParser("and{age(age=30), sex(sex=\"female\")")  # missing closing brace
        parser.consume_criterion()
    assert "Expected ',' or '}'" in str(excinfo.value)
