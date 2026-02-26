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

    actin_rules, actin_categories, rule_to_warnif = (
        actin_curator_utils.load_actin_resource(str(actin_rules_path))
    )

    return client, actin_rules, actin_categories, rule_to_warnif


def assert_warnif_mapping(
    input_text: str,
    expected_expr,
    client,
    actin_rules,
    rule_to_warnif: dict,
    *,
    category: list[str],
):
    input_dict = {
        "input_rule": input_text,
        "exclude": True,  # Since all input examples contain WARN_IF(...)
        "actin_category": category,
    }

    mapped = actin_curator.map_to_actin_rules(input_dict, client, actin_rules)
    assert isinstance(mapped, list) and len(mapped) == 1, (
        f"Expected single-item list from map_to_actin_rules, got {type(mapped)}: {mapped}"
    )

    raw_rule = mapped[0].get("actin_rule")
    assert raw_rule not in (None, "", {}), f"Empty actin_rule returned: {mapped[0]}"

    expr = actin_curator_utils.actin_rule_reformat(raw_rule)
    expr = actin_curator.rewrite_not_to_warnif(expr, rule_to_warnif)

    if isinstance(expected_expr, str):
        assert expr == expected_expr
    else:
        assert expr in set(expected_expr), f"Got: {expr}\nExpected one of: {set(expected_expr)}"


# ---------------------------
# Vaccine mappings
# ---------------------------

def test_live_vaccine_mapping_1(client_and_actin_data):
    client, actin_rules, _, rule_to_warnif = client_and_actin_data

    assert_warnif_mapping(
        "Has received a live vaccine within 30 days of the first dose of study intervention",
        "WARN_IF(HAS_RECEIVED_LIVE_VACCINE_WITHIN_X_MONTHS[1])",
        client,
        actin_rules,
        rule_to_warnif,
        category=["Infectious_Disease_History_and_Status"],
    )


def test_live_vaccine_mapping_2(client_and_actin_data):
    client, actin_rules, _, rule_to_warnif = client_and_actin_data

    assert_warnif_mapping(
        "Receipt of live attenuated vaccine within 30 days prior to the first dose of trial treatment. COVID-19 vaccination is allowed.",
        "WARN_IF(HAS_RECEIVED_LIVE_VACCINE_WITHIN_X_MONTHS[1])",
        client,
        actin_rules,
        rule_to_warnif,
        category=["Infectious_Disease_History_and_Status"],
    )


def test_non_live_vaccine_mapping_1(client_and_actin_data):
    client, actin_rules, _, rule_to_warnif = client_and_actin_data

    assert_warnif_mapping(
        "Has received non live vaccine within 30 days of first dose of study intervention",
        "WARN_IF(HAS_RECEIVED_NON_LIVE_VACCINE_WITHIN_X_WEEKS[4])",
        client,
        actin_rules,
        rule_to_warnif,
        category=["Infectious_Disease_History_and_Status"],
    )


def test_non_live_vaccine_mapping_2(client_and_actin_data):
    client, actin_rules, _, rule_to_warnif = client_and_actin_data

    assert_warnif_mapping(
        "Has received mRNA-Based vaccine within 4 weeks of study Day 1",
        "WARN_IF(HAS_RECEIVED_NON_LIVE_VACCINE_WITHIN_X_WEEKS[4])",
        client,
        actin_rules,
        rule_to_warnif,
        category=["Infectious_Disease_History_and_Status"],
    )


def test_unspecified_vaccine_mapping_1(client_and_actin_data):
    client, actin_rules, _, rule_to_warnif = client_and_actin_data

    assert_warnif_mapping(
        "Has received vaccine within 30 days of first dose of study intervention",
        "WARN_IF(OR(HAS_RECEIVED_LIVE_VACCINE_WITHIN_X_MONTHS[1], HAS_RECEIVED_NON_LIVE_VACCINE_WITHIN_X_WEEKS[4]))",
        client,
        actin_rules,
        rule_to_warnif,
        category=["Infectious_Disease_History_and_Status"],
    )


# ---------------------------
# Medication mappings (controls)
# ---------------------------

def test_medication_mapping_1(client_and_actin_data):
    client, actin_rules, _, rule_to_warnif = client_and_actin_data

    assert_warnif_mapping(
        "Is receiving any form of immunosuppressive therapy within 7 days prior to first dose of study drug",
        [
            "WARN_IF(HAS_RECEIVED_CATEGORY_X_MEDICATION_WITHIN_Y_WEEKS['Immunosuppressants', 1])",
            "WARN_IF(HAS_RECEIVED_CATEGORY_X_MEDICATION_WITHIN_Y_WEEKS['immunosuppressive', 1])",
            "WARN_IF(HAS_RECEIVED_CATEGORY_X_MEDICATION_WITHIN_Y_WEEKS['immunosuppressive therapy', 1])",
        ],
        client,
        actin_rules,
        rule_to_warnif,
        category=["Current_Medication_Use"],
    )


def test_medication_mapping_2(client_and_actin_data):
    client, actin_rules, _, rule_to_warnif = client_and_actin_data

    assert_warnif_mapping(
        "Participant is receiving anti-epileptic drugs at least 14 days prior to first dose of study treatment",
        [
            "WARN_IF(HAS_RECEIVED_CATEGORY_X_MEDICATION_WITHIN_Y_WEEKS['Antiepileptics', 2])",
            "WARN_IF(HAS_RECEIVED_CATEGORY_X_MEDICATION_WITHIN_Y_WEEKS['anti-epileptic', 2])",
        ],
        client,
        actin_rules,
        rule_to_warnif,
        category=["Current_Medication_Use"],
    )
