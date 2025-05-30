import unittest

from pydantic_curator.criterion_parser import parse_criterion
from pydantic_curator.criterion_schema import DiagnosticFindingCriterion, NotCriterion, IfCriterion, \
    MetastasesCriterion


class TestCriterionParser(unittest.TestCase):

    def test_parse_simple(self):
        formatted = 'diagnosticfinding(confidence=1.0, finding="Metastatic disease", method="imaging", modality="CT, MRI, \\n or bone scan")'
        criterion = parse_criterion(formatted)
        self.assertIsInstance(criterion, DiagnosticFindingCriterion)
        self.assertEqual(1.0, criterion.confidence)
        self.assertEqual("Metastatic disease", criterion.finding)
        self.assertEqual("imaging", criterion.method)
        self.assertEqual("CT, MRI, \n or bone scan", criterion.modality)

    def test_parse_composite(self):
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
        self.assertIsInstance(criterion, NotCriterion)

    def test_parse_if_else(self):
        formatted = '''
        if {tissueavailability(confidence=1.0)}
         then {requiredaction(confidence=1.0, action="Allow for correlative biomarker studies")}
         else {requiredaction(confidence=1.0, action="Consent and undergo fresh tumor biopsy")}
        '''

        criterion = parse_criterion(formatted)
        self.assertIsInstance(criterion, IfCriterion)

    def test_parse_list(self):
        formatted = '''
        metastases(location="CNS", additional_details=["symptomatic", "typical of lung cancer"])
        '''

        criterion = parse_criterion(formatted)
        self.assertIsInstance(criterion, MetastasesCriterion)
        self.assertEqual("CNS", criterion.location)
        self.assertEqual(["symptomatic", "typical of lung cancer"], criterion.additional_details)
