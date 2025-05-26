import unittest

from pydantic_curator.criterion_schema import *
from pydantic_curator.eligibility_curator import llm_curate_by_batch
from pydantic_curator.eligibility_py_loader import exec_py_into_variable
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
                criterion=CurrentTherapyCriterion(
                    description="Receiving additional, concurrent, active therapy for GBM outside of the trial",
                    therapy="Receiving additional, concurrent, active therapy for GBM outside of the trial"
                ),
                description="EXCLUDE Receiving additional, concurrent, active therapy for GBM outside of the trial"
            ),
            NotCriterion(
                criterion=MetastasesCriterion(
                    description="Extensive leptomeningeal disease",
                    location="leptomeningeal",
                    additional_details=['extensive']
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

    # test top level rules are same as the input
    def test_one_per_top_level_rule(self):

        input_text_list = [
            'EXCLUDE History of idiopathic pulmonary fibrosis, organizing pneumonia (e.g., \
bronchiolitis obliterans), drug-induced pneumonitis, or idiopathic pneumonitis, or evidence of active pneumonitis on \
screening chest CT scan',
            'EXCLUDE Active infection requiring systemic antibiotic/antifungal medication, known clinically \
active HBV or HCV infection, or on antiretroviral therapy for HIV disease']

        python_code = llm_curate_by_batch('\n'.join(input_text_list), self.client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)

        # check that there are two top level criteria
        self.assertEqual(2, len(criteria))
        self.assertEqual(input_text_list[0], criteria[0].description)
        self.assertEqual(input_text_list[1], criteria[1].description)


    def test_hidden_if_rule(self):

        input_text = 'INCLUDE QTc interval ≤ 450 ms in males, and ≤ 470 ms in females'

        python_code = llm_curate_by_batch(input_text, self.client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)

        self.assertIsInstance(criteria[0], IfCriterion)

    def test_primary_tumor_plus_histology(self):

        input_text = 'INCLUDE Histologically confirmed adenocarcinoma of the prostate without neuroendocrine or small \
cell differentiation'

        expected_criteria = AndCriterion(
            description=input_text,
            criteria=[
                HistologyCriterion(
                    description="Histologically confirmed adenocarcinoma",
                    histology_type="adenocarcinoma"
                ),
                PrimaryTumorCriterion(
                    description="adenocarcinoma of the prostate",
                    primary_tumor_location="prostate"
                ),
                NotCriterion(
                    description="without neuroendocrine or small cell differentiation",
                    criterion=OrCriterion(
                        description="neuroendocrine or small cell differentiation",
                        criteria=[
                            HistologyCriterion(
                                description="neuroendocrine differentiation",
                                histology_type="neuroendocrine"
                            ),
                            HistologyCriterion(
                                description="small cell differentiation",
                                histology_type="small cell"
                            )
                        ]
                    )
                )
            ])

        python_code = llm_curate_by_batch(input_text, self.client)

        #print(python_code)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)
        self.assertEqual(expected_criteria, criteria[0])
        self.assertTrue(isinstance(criteria[0], AndCriterion))

        sub_criteria = criteria[0].criteria

        # make sure it contains a PrimaryTumor and two NOT(Histology)
        self.assertIsInstance(sub_criteria[0], HistologyCriterion)
        self.assertIsInstance(sub_criteria[1], PrimaryTumorCriterion)
        self.assertIsInstance(sub_criteria[2], NotCriterion)
        self.assertIsInstance(sub_criteria[2].criterion, OrCriterion)
        not_histology_criteria = sub_criteria[2].criterion.criteria
        self.assertIsInstance(not_histology_criteria[0], HistologyCriterion)
        self.assertIsInstance(not_histology_criteria[1], HistologyCriterion)

    def test_retain_include_exclude_tags(self):
        input_text = '''INCLUDE Patient must be ≥ 18 years of age
INCLUDE Histologically confirmed adenocarcinoma of the prostate without neuroendocrine or small cell differentiation
INCLUDE ECOG performance status of ≤ 1'''

        python_code = llm_curate_by_batch(input_text, self.client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)
        self.assertEqual(3, len(criteria))
        self.assertTrue(criteria[0].description.startswith('INCLUDE'))
        self.assertTrue(criteria[1].description.startswith('INCLUDE'))
        self.assertTrue(criteria[2].description.startswith('INCLUDE'))

    def test_do_not_use_clinical_judgement(self):
        input_text = '''INCLUDE Adequate bone marrow function
  - Platelets > 100 x 10^9/L
  - ANC > 1.5 x 10^9/L
  - Hb > 100
INCLUDE Adequate liver function
  - ALT < 1.5 x ULN
  - AST < 1.5 x ULN
  - Bilirubin < 2 x ULN
INCLUDE Adequate renal function (creatinine clearance > 50 mL/min)'''

        python_code = llm_curate_by_batch(input_text, self.client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)
        self.assertEqual(3, len(criteria))
        for criterion in criteria:
            self.assertTrue(isinstance(criterion, AndCriterion | LabValueCriterion))
            if isinstance(criterion, AndCriterion):
                for sub_criterion in criterion.criteria:
                    self.assertIsInstance(sub_criterion, LabValueCriterion)
