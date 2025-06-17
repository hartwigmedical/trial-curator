from pydantic_curator.criterion_formatter import format_criterion
from pydantic_curator.criterion_schema import IfCriterion, LabValueCriterion


def test_format():
    formatted = '''
    not {
       or {
          histology(histology_type="sarcomatoid"),
          histology(histology_type="spindle cell"),
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

def test_timing_format():
    formatted = '''
    timing(reference="now", ) {
       or {
          histology(histology_type="sarcomatoid"),
          histology(histology_type="spindle cell"),
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

def test_if_format():
    criterion = IfCriterion(
        description="Corrected QT interval (QTcF) of >450 msec (males) or >470 msec (females) using Fridericia's correction formula",
        condition=LabValueCriterion(
            description="Corrected QT interval (QTcF) of >450 msec (males)",
            measurement="Corrected QT interval (QTcF)",
            unit="msec",
            value=450,
            operator=">",
        ),
        then=LabValueCriterion(
            description="Corrected QT interval (QTcF) of >450 msec (males)",
            measurement="Corrected QT interval (QTcF)",
            unit="msec",
            value=450,
            operator=">",
        ),
        else_=LabValueCriterion(
            description="Corrected QT interval (QTcF) of >470 msec (females)",
            measurement="Corrected QT interval (QTcF)",
            unit="msec",
            value=470,
            operator=">",
        )
    )

    expected = """If(description="Corrected QT interval (QTcF) of >450 msec (males) or >470 msec (females) using Fridericia's correction formula")
{
    LabValue(description="Corrected QT interval (QTcF) of >450 msec (males)",
        measurement="Corrected QT interval (QTcF)",
        unit="msec",
        value=450.0,
        operator=">")
}
then {
    LabValue(description="Corrected QT interval (QTcF) of >450 msec (males)",
        measurement="Corrected QT interval (QTcF)",
        unit="msec",
        value=450.0,
        operator=">")
}
else {
    LabValue(description="Corrected QT interval (QTcF) of >470 msec (females)",
        measurement="Corrected QT interval (QTcF)",
        unit="msec",
        value=470.0,
        operator=">")
}"""

    assert format_criterion(criterion) == expected
