import pytest

from pydantic_curator.criterion_schema import *
from pydantic_curator.pydantic_curator import llm_curate_by_batch, llm_categorise_criteria
from pydantic_curator.eligibility_py_loader import exec_py_into_variable
from pydantic_curator.utils import criterion_equal_ignore_description, criteria_equal_ignore_description
from trialcurator.openai_client import OpenaiClient

class TestCategoriseRule:

    @pytest.fixture
    def client(self):
        return OpenaiClient()

    def test_categorisation(self, client):
        input_text = '''EXCLUDE Males or females of reproductive potential may not participate unless they have agreed \
to use two effective methods of birth control, including a medically accepted barrier or contraceptive method (e.g., \
male or female condom) for the duration of the study. Abstinence is an acceptable method of birth control'''

        rule_categories: dict[str, list[str]] = llm_categorise_criteria(input_text, client)
        assert input_text in rule_categories
        categories = rule_categories[input_text]
        assert categories == ["Sex", "ReproductiveStatus", "RequiredAction"]

    def test_categorisation_male(self, client):
        input_text = '''INCLUDE Nonsterile males must be willing to use a highly effective method of birth control for \
the duration of the study treatment period and for ≥ 4 months after the last dose of study drugs(s)'''

        rule_categories: dict[str, list[str]] = llm_categorise_criteria(input_text, client)
        assert input_text in rule_categories
        categories = rule_categories[input_text]
        assert categories == ["Sex", "ReproductiveStatus", "RequiredAction"]

class TestCurateIntoPy:

    @pytest.fixture
    def client(self):
        return OpenaiClient()

    # test curate into pyton
    def test_curate_into_py(self, client):

        input_text = '''
EXCLUDE Receiving additional, concurrent, active therapy for GBM outside of the trial
EXCLUDE Extensive leptomeningeal disease
INCLUDE QTc ≤ 450 msec if male and QTc ≤ 470 msec if female
EXCLUDE History of another malignancy in the previous 2 years
'''

        expected_criteria = [
            NotCriterion(
                description="EXCLUDE Receiving additional, concurrent, active therapy for GBM outside of the trial",
                criterion=AndCriterion(
                    criteria=[
                        CurrentTreatmentCriterion(
                            description="Receiving additional, concurrent, active therapy",
                            treatment=SystemicTherapy(description="additional, concurrent, active therapy")
                        )
                    ]
                )
            ),
            NotCriterion(
                description="EXCLUDE Extensive leptomeningeal disease",
                criterion=MetastasesCriterion(
                    description="Extensive leptomeningeal disease",
                    location="leptomeningeal"
                )
            ),
            IfCriterion(
                description="INCLUDE QTc ≤ 450 msec if male and QTc ≤ 470 msec if female",
                condition=SexCriterion(
                    description="if male",
                    sex="male"
                ),
                then=LabValueCriterion(
                    description="QTc ≤ 450 msec",
                    measurement="QTc",
                    unit="msec",
                    value=450,
                    operator="<="
                ),
                else_=LabValueCriterion(
                    description="QTc ≤ 470 msec if female",
                    measurement="QTc",
                    unit="msec",
                    value=470,
                    operator="<="
                )
            ),
            NotCriterion(
                description="EXCLUDE History of another malignancy in the previous 2 years",
                criterion=TimingCriterion(
                    description="History of another malignancy in the previous 2 years",
                    reference="previous 2 years",
                    criterion=ComorbidityCriterion(
                        description="History of another malignancy",
                        comorbidity="another malignancy"
                    )
                )
            )
        ]

        python_code = llm_curate_by_batch(input_text, client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)

        # check that the number of trial groups are the same
        assert criteria_equal_ignore_description(expected_criteria, criteria)

    # test top level rules are same as the input
    def test_one_per_top_level_rule(self, client):

        input_text_list = [
            'EXCLUDE History of idiopathic pulmonary fibrosis, organizing pneumonia (e.g., \
bronchiolitis obliterans), drug-induced pneumonitis, or idiopathic pneumonitis, or evidence of active pneumonitis on \
screening chest CT scan',
            'EXCLUDE Active infection requiring systemic antibiotic/antifungal medication, known clinically \
active HBV or HCV infection, or on antiretroviral therapy for HIV disease']

        python_code = llm_curate_by_batch('\n'.join(input_text_list), client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)

        # check that there are two top level criteria
        assert 2 == len(criteria)
        assert input_text_list[0] == criteria[0].description
        assert input_text_list[1] == criteria[1].description

    def test_hidden_if_rule(self, client):

        input_text = 'INCLUDE QTc interval ≤ 450 ms in males, and ≤ 470 ms in females'

        python_code = llm_curate_by_batch(input_text, client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)

        assert isinstance(criteria[0], IfCriterion)

    def test_primary_tumor_plus_histology(self, client):

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
                    description="Primary tumor location is prostate",
                    primary_tumor_location="prostate"
                ),
                NotCriterion(
                    criterion=HistologyCriterion(
                        histology_type="neuroendocrine",
                        description="Histology type is neuroendocrine"
                    ),
                    description="Not neuroendocrine differentiation"
                ),
                NotCriterion(
                    criterion=HistologyCriterion(
                        histology_type="small cell",
                        description="Histology type is small cell"
                    ),
                    description="Not small cell differentiation"
                )
            ])

        python_code = llm_curate_by_batch(input_text, client)

        #print(python_code)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)
        assert isinstance(criteria[0], AndCriterion)

        sub_criteria = criteria[0].criteria

        # make sure it contains a PrimaryTumor and two NOT(Histology)
        assert isinstance(sub_criteria[0], PrimaryTumorCriterion)
        assert isinstance(sub_criteria[1], HistologyCriterion)
        assert isinstance(sub_criteria[2], NotCriterion)
        if isinstance(sub_criteria[2].criterion, OrCriterion):
            or_criterion = sub_criteria[2].criterion
            assert isinstance(or_criterion.criteria[0], HistologyCriterion)
            assert isinstance(or_criterion.criteria[1], HistologyCriterion)
        else:
            assert isinstance(sub_criteria[2].criterion, HistologyCriterion)
            assert isinstance(sub_criteria[3], NotCriterion)
            assert isinstance(sub_criteria[3].criterion, HistologyCriterion)

    def test_retain_include_exclude_tags(self, client):
        input_text = '''INCLUDE Patient must be ≥ 18 years of age
INCLUDE Histologically confirmed adenocarcinoma of the prostate without neuroendocrine or small cell differentiation
INCLUDE ECOG performance status of ≤ 1'''

        python_code = llm_curate_by_batch(input_text, client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)
        assert 3 == len(criteria)
        assert criteria[0].description.startswith('INCLUDE')
        assert criteria[1].description.startswith('INCLUDE')
        assert criteria[2].description.startswith('INCLUDE')

    def test_do_not_use_clinical_judgement(self, client):
        input_text = '''INCLUDE Adequate bone marrow function
- Platelets > 100 x 10^9/L
- ANC > 1.5 x 10^9/L
- Hb > 100
INCLUDE Adequate liver function
- ALT < 1.5 x ULN
- AST < 1.5 x ULN
- Bilirubin < 2 x ULN
INCLUDE Adequate renal function (creatinine clearance > 50 mL/min)'''

        python_code = llm_curate_by_batch(input_text, client)

        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)
        assert 3 == len(criteria)
        for criterion in criteria:
            assert isinstance(criterion, AndCriterion | LabValueCriterion)
            if isinstance(criterion, AndCriterion):
                for sub_criterion in criterion.criteria:
                    assert isinstance(sub_criterion, LabValueCriterion)
