from trialcurator.utils import split_tagged_criteria, batch_tagged_criteria

def test_split_tagged_criteria():
    criteria_text = '''
INCLUDE Age ≥ 18 years
EXCLUDE More than 2 prior lines for chemotherapy administration
EXCLUDE Received any prior treatment with:
  - lomustine
  - agents part of any of the experimental arms
  - bevacizumab or other VEGF or VEGF receptor-mediated targeted agent
EXCLUDE Any prior treatment with prolifeprospan 20 with carmustine wafer
EXCLUDE Any prior treatment with an intracerebral agent
INCLUDE QTc ≤ 450 msec if male and QTc ≤ 470 msec if female
EXCLUDE History of another malignancy in the previous 2 years, with a disease-free interval of < 2 years
    '''
    expect_output = [
        'INCLUDE Age ≥ 18 years',
        'EXCLUDE More than 2 prior lines for chemotherapy administration',
        'EXCLUDE Received any prior treatment with:\n  - lomustine\n  - agents part of any of the experimental ' +
        'arms\n  - bevacizumab or other VEGF or VEGF receptor-mediated targeted agent',
        'EXCLUDE Any prior treatment with prolifeprospan 20 with carmustine wafer',
        'EXCLUDE Any prior treatment with an intracerebral agent',
        'INCLUDE QTc ≤ 450 msec if male and QTc ≤ 470 msec if female',
        'EXCLUDE History of another malignancy in the previous 2 years, with a disease-free interval of < 2 years'
    ]
    splitted_tagged_criteria = split_tagged_criteria(criteria_text)
    assert expect_output == splitted_tagged_criteria


def test_batch_criteria():
    criteria_text = '''
INCLUDE Age ≥ 18 years
EXCLUDE More than 2 prior lines for chemotherapy administration
EXCLUDE Received any prior treatment with:
  - lomustine
  - agents part of any of the experimental arms
  - bevacizumab or other VEGF or VEGF receptor-mediated targeted agent
EXCLUDE Any prior treatment with prolifeprospan 20 with carmustine wafer
EXCLUDE Any prior treatment with an intracerebral agent
INCLUDE QTc ≤ 450 msec if male and QTc ≤ 470 msec if female
EXCLUDE History of another malignancy in the previous 2 years, with a disease-free interval of < 2 years
        '''
    expect_output = [
        '''INCLUDE Age ≥ 18 years
EXCLUDE More than 2 prior lines for chemotherapy administration
EXCLUDE Received any prior treatment with:
  - lomustine
  - agents part of any of the experimental arms
  - bevacizumab or other VEGF or VEGF receptor-mediated targeted agent''',
        '''EXCLUDE Any prior treatment with prolifeprospan 20 with carmustine wafer
EXCLUDE Any prior treatment with an intracerebral agent
INCLUDE QTc ≤ 450 msec if male and QTc ≤ 470 msec if female''',
        'EXCLUDE History of another malignancy in the previous 2 years, with a disease-free interval of < 2 years'
    ]
    batched_tagged_criteria = batch_tagged_criteria(criteria_text, 3)
    assert expect_output == batched_tagged_criteria
