import pytest

from pydantic_curator.criterion_parser import parse_criterion, CriterionParser
from pydantic_curator.criterion_schema import DiagnosticFindingCriterion, NotCriterion, IfCriterion, \
    MetastasesCriterion, TimingCriterion


def test_parse_simple():
    formatted = 'DiagnosticFinding(finding="Metastatic disease", method="imaging", modality="CT, MRI, \\n or bone scan")'
    criterion = parse_criterion(formatted)
    assert isinstance(criterion, DiagnosticFindingCriterion)
    assert criterion.finding == "Metastatic disease"
    assert criterion.method == "imaging"
    assert criterion.modality == "CT, MRI, \n or bone scan"

def test_parse_composite():

    formatted = '''
    Not {
       Or {
          Histology(histology_type="sarcomatoid"),
          Histology (histology_type = "spindle cell" ),
          Histology(histology_type="neuroendocrine small cell"),
          And {
             Not {
                TreatmentOption(treatment=StandardOfCare())
             },
             PriorTreatment(treatment=SystemicTherapy(description="taxane regimens"), number_of_prior_lines=IntRange(min_inclusive=1))
          }
       }
    }
    '''

    criterion = parse_criterion(formatted)
    assert isinstance(criterion, NotCriterion)

    formatted = '''
        Timing(description="History of another malignancy in the previous 2 years",
            reference="now",
            window_days=IntRange(min_inclusive = -730))
        {
            Comorbidity(description="History of another malignancy",
                comorbidity="another malignancy")
        }
    '''

    criterion = parse_criterion(formatted)
    assert isinstance(criterion, TimingCriterion)

def test_parse_if_else():

    formatted = '''
    If {TissueAvailability(confidence=1.0)}
    then {RequiredAction(confidence=1.0, action="Allow for correlative biomarker studies")}
    else {RequiredAction(confidence=1.0, action="Consent and undergo fresh tumor biopsy")}
    '''

    criterion = parse_criterion(formatted)
    assert isinstance(criterion, IfCriterion)

    formatted = '''
    If(description="A performance status ≥2 on the ECOG Performance Scale (solid tumours cohort) or Karnofsky performance scale of ≥60 (haematologic malignancies cohort)") {
        PrimaryTumor(description="solid tumours cohort",
            primary_tumor_type="solid tumours")
    }
    then {
        PerformanceStatus(description="A performance status ≥2 on the ECOG Performance Scale",
            scale="ECOG",
            value_range=IntRange(min_inclusive=2))
    }
    else {
        PerformanceStatus(description="Karnofsky performance scale of ≥60 (haematologic malignancies cohort)",
            scale="Karnofsky",
            value_range=IntRange(min_inclusive=60))
    }
    '''

    criterion = parse_criterion(formatted)
    assert isinstance(criterion, IfCriterion)

def test_parse_list():

    formatted = 'Metastases(location="CNS", additional_details=["symptomatic", "typical of lung cancer"])'

    criterion = parse_criterion(formatted)
    assert isinstance(criterion, MetastasesCriterion)
    assert criterion.location == "CNS"
    assert criterion.additional_details == ["symptomatic", "typical of lung cancer"]

'''
@pytest.mark.parametrize('input_str,expected_type',
[
    ('Age(age=30)', 'age'),
    ('Sex(sex="female")', 'sex'),
    ('PriorTreatment(treatment="SystemicTherapy()", timing_info(reference="now", window_days(min_inclusive=30)))', 'PriorTreatment'),
    ('and{age(age=30), sex(sex="female")}', 'and'),
    ('not{or{age(age=20), sex(sex="male")}}', 'not'),
    ('if{tissueavailability(confidence=1.0)} then{requiredaction(action="Consent")} else {requiredaction(action="Biopsy")}', 'if'),
    ('metastases(location="liver", additional_details=["one", "two", "three"])', 'metastases'),
    ('criterion(key=["plain", 5])', 'criterion')
])
def test_valid_criteria(input_str, expected_type):
    parser = CriterionParser(input_str)
    result = parser.consume_criterion()
    assert result["type"] == expected_type
'''

def test_invalid_syntax():

    with pytest.raises(ValueError) as excinfo:
        parser = CriterionParser("And{Age(age=30), sex(sex=\"female\")")  # missing closing brace
        parser.consume_criterion()

