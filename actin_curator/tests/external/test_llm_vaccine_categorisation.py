import pytest
from pathlib import Path

from trialcurator.openai_client import OpenaiClient
from actin_curator import actin_curator, actin_curator_utils


@pytest.fixture(scope="module")
def client_and_actin_data():
    client = OpenaiClient()
    actin_repo_root = Path(__file__).resolve().parents[2]

    actin_rules_path = (
        actin_repo_root
        / "data/ACTIN_rules/ACTIN_rules_w_categories_WARNIF_19122025.csv"
    )

    actin_rules, actin_categories, _ = (
        actin_curator_utils.load_actin_resource(str(actin_rules_path))
    )

    return client, actin_rules, actin_categories


def assert_categories(input_text, expected_categories, client, actin_categories):
    actual_output = actin_curator.identify_actin_categories(
        input_text,
        client,
        actin_categories,
    )[0]

    actual_categories = next(iter(actual_output.values()))
    assert set(actual_categories) == set(expected_categories)


# ---------------------------
# Vaccine categorisation
# ---------------------------

def test_live_vaccine_category_1(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data

    assert_categories(
        "Has received a live vaccine within 30 days of the first dose of study intervention",
        ["Infectious_Disease_History_and_Status"],
        client,
        actin_categories,
    )


def test_live_vaccine_category_2(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data

    assert_categories(
        "Receipt of live attenuated vaccine within 30 days prior to the first dose of trial treatment. COVID-19 vaccination is allowed.",
        ["Infectious_Disease_History_and_Status"],
        client,
        actin_categories,
    )


def test_non_live_vaccine_category_1(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data

    assert_categories(
        "Has received non live vaccine within 30 days of first dose of study intervention",
        ["Infectious_Disease_History_and_Status"],
        client,
        actin_categories,
    )


def test_non_live_vaccine_category_2(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data

    assert_categories(
        "Has received mRNA-Based vaccine within 4 weeks of study Day 1",
        ["Infectious_Disease_History_and_Status"],
        client,
        actin_categories,
    )


def test_unspecified_vaccine_category_1(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data

    assert_categories(
        "Has received vaccine within 30 days of first dose of study intervention",
        ["Infectious_Disease_History_and_Status"],
        client,
        actin_categories,
    )


# ---------------------------
# Medication categorisation
# ---------------------------

def test_medication_category_1(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data

    assert_categories(
        "Is receiving any form of immunosuppressive therapy within 7 days prior to first dose of study drug",
        ["Current_Medication_Use"],
        client,
        actin_categories,
    )


def test_medication_category_2(client_and_actin_data):
    client, _, actin_categories = client_and_actin_data

    assert_categories(
        "Participant is receiving anti-epileptic drugs at least 14 days prior to first dose of study treatment",
        ["Current_Medication_Use"],
        client,
        actin_categories,
    )