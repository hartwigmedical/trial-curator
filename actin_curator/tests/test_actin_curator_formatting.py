import pytest
from actin_curator.actin_curator_utils import output_formatting


def test_output_formatting_1():
    input_curation = {'HAS_HAD_ANY_CANCER_TREATMENT': []}
    expected_curation = "HAS_HAD_ANY_CANCER_TREATMENT"

    actual_curation = output_formatting(input_curation)
    assert actual_curation == expected_curation


def test_output_formatting_2():
    input_curation = {"NOT": {"HAS_ACTIVE_SECOND_MALIGNANCY": []}}
    expected_curation = f"""NOT
(
    HAS_ACTIVE_SECOND_MALIGNANCY
)"""

    actual_curation = output_formatting(input_curation)
    assert actual_curation == expected_curation


def test_output_formatting_3a():
    input_curation = {"AND": [{"HAS_UNRESECTABLE_CANCER": []}, {"HAS_LOCALLY_ADVANCED_CANCER": []}]}
    expected_curation = f"""AND
(
    HAS_UNRESECTABLE_CANCER,
    HAS_LOCALLY_ADVANCED_CANCER
)"""

    actual_curation = output_formatting(input_curation)
    assert actual_curation == expected_curation


def test_output_formatting_3b():
    input_curation = {
        "AND": [
            {
                "IS_MALE": []
            },
            {
                "ADHERES_TO_SPERM_OR_EGG_DONATION_PRESCRIPTIONS": []
            },
            {
                "USES_ADEQUATE_ANTICONCEPTION": []
            }
        ]
    }
    expected_curation = f"""AND
(
    IS_MALE,
    ADHERES_TO_SPERM_OR_EGG_DONATION_PRESCRIPTIONS,
    USES_ADEQUATE_ANTICONCEPTION
)"""

    actual_curation = output_formatting(input_curation)
    assert actual_curation == expected_curation


def test_output_formatting_4a():
    input_curation = {
        "NOT": {
            "OR": [
                {"HAS_POTENTIAL_ORAL_MEDICATION_DIFFICULTIES": []},
                {"HAS_POTENTIAL_ABSORPTION_DIFFICULTIES": []},
                {"HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE": []}
            ]
        }
    }
    expected_curation = f"""NOT
(
    OR
    (
        HAS_POTENTIAL_ORAL_MEDICATION_DIFFICULTIES,
        HAS_POTENTIAL_ABSORPTION_DIFFICULTIES,
        HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE
    )
)"""

    actual_curation = output_formatting(input_curation)
    assert actual_curation == expected_curation


def test_output_formatting_4b():
    input_curation = {"NOT": {"OR": [{"HAS_KNOWN_HEPATITIS_B_INFECTION": []}, {"HAS_KNOWN_HEPATITIS_C_INFECTION": []}, {"HAS_KNOWN_HIV_INFECTION": []}]}}

    expected_curation = f"""NOT
(
    OR
    (
        HAS_KNOWN_HEPATITIS_B_INFECTION,
        HAS_KNOWN_HEPATITIS_C_INFECTION,
        HAS_KNOWN_HIV_INFECTION
    )
)"""

    actual_curation = output_formatting(input_curation)
    assert actual_curation == expected_curation


def test_output_formatting_5a():
    input_curation = {"AND":
        [
            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE": [1.5, 3.0]},
            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE": [1.5,
                                                                                                              1.0]},
            {"HAS_ASAT_ULN_OF_AT_MOST_X": [1.0]},
            {"HAS_ALAT_ULN_OF_AT_MOST_X": [1.0]},
            {"HAS_ALP_ULN_OF_AT_MOST_X": [2.5]}
        ]}
    expected_curation = f"""AND
(
    HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE[1.5, 3.0],
    HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE[1.5, 1.0],
    HAS_ASAT_ULN_OF_AT_MOST_X[1.0],
    HAS_ALAT_ULN_OF_AT_MOST_X[1.0],
    HAS_ALP_ULN_OF_AT_MOST_X[2.5]
)"""

    actual_curation = output_formatting(input_curation)
    assert actual_curation == expected_curation


def test_output_formatting_5b():
    input_curation = {"AND": [
        {"HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []},
        {"HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X": ["astrocytoma"]},
        {"HAS_ANY_STAGE_X": ["Grade 4"]},
        {"MEETS_SPECIFIC_CRITERIA_REGARDING_METASTASES": ["IDHm"]},
        {"OR": [
            {"HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE": []},
            {"MEETS_SPECIFIC_CRITERIA_REGARDING_METASTASES": ["homozygous deletion of CDKN2A/B"]}
        ]}
    ]}

    expected_curation = f"""AND
(
    HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE,
    HAS_PRIMARY_TUMOR_LOCATION_BELONGING_TO_ANY_DOID_TERM_X['astrocytoma'],
    HAS_ANY_STAGE_X['Grade 4'],
    MEETS_SPECIFIC_CRITERIA_REGARDING_METASTASES['IDHm'],
    OR
    (
        HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE,
        MEETS_SPECIFIC_CRITERIA_REGARDING_METASTASES['homozygous deletion of CDKN2A/B']
    )
)"""

    actual_curation = output_formatting(input_curation)
    assert actual_curation == expected_curation
