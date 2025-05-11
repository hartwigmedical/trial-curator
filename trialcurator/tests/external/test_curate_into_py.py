import unittest

from trialcurator.criterion_schema import *
from trialcurator.eligibility_curator import llm_curate_from_text, llm_curate_by_batch
from trialcurator.eligibility_py_loader import exec_py_into_variable
from trialcurator.openai_client import OpenaiClient

class TestCurateIntoPy(unittest.TestCase):

    def setUp(self):
        self.client = OpenaiClient()

    # test curate into pyton
    def test_curate_into_py(self):

        input_text = '''
EXCLUDE Receiving additional, concurrent, active therapy for GBM outside of the trial
EXCLUDE Extensive leptomeningeal disease
INCLUDE QTc ≤ 450 msec if male and QTc ≤ 470 msec if female
EXCLUDE History of another malignancy in the previous 2 years
'''

        expected_criteria = [
            NotCriterion(
                criterion=OtherCriterion(
                    description="Receiving additional, concurrent, active therapy for GBM outside of the trial",
                    reason="Receiving additional, concurrent, active therapy for GBM outside of the trial"
                ),
                description="EXCLUDE Receiving additional, concurrent, active therapy for GBM outside of the trial"
            ),
            NotCriterion(
                criterion=DiagnosticFindingCriterion(
                    finding="extensive leptomeningeal disease",
                    description="Extensive leptomeningeal disease"
                ),
                description="EXCLUDE Extensive leptomeningeal disease"
            ),
            IfCriterion(
                condition=SexCriterion(
                    sex="male",
                    description="QTc ≤ 450 msec if male"
                ),
                then=LabValueCriterion(
                    measurement="QTc",
                    unit="msec",
                    value=450,
                    operator="<=",
                    description="QTc ≤ 450 msec if male"
                ),
                else_=LabValueCriterion(
                    measurement="QTc",
                    unit="msec",
                    value=470,
                    operator="<=",
                    description="QTc ≤ 470 msec if female"
                ),
                description="INCLUDE QTc ≤ 450 msec if male and QTc ≤ 470 msec if female"
            ),
            NotCriterion(
                criterion=ComorbidityCriterion(
                    comorbidity="another malignancy",
                    timing_info=TimingInfo(
                        reference="now",
                        window_days=IntRange(min_inclusive=-730)
                    ),
                    description="History of another malignancy in the previous 2 years"
                ),
                description="EXCLUDE History of another malignancy in the previous 2 years"
            )
        ]

        python_code = llm_curate_by_batch(input_text, self.client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)

        # check that the number of trial groups are the same
        self.assertEqual(expected_criteria, criteria)

