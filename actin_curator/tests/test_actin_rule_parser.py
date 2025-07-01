from actin_curator.actin_rule_parser import parse_actin_rule


def test_actin_rule_parser_and():
    formatted = f"""AND
(
    IS_MALE,
    ADHERES_TO_SPERM_OR_EGG_DONATION_PRESCRIPTIONS,
    USES_ADEQUATE_ANTICONCEPTION
)"""

    expected_rules = {
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

    parsed_rules = parse_actin_rule(formatted)

    assert parsed_rules == expected_rules

def test_actin_rule_parser_args():
    formatted = f"""AND
(
    HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE[1.5, 3.0],
    HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE[1.5, 1.0],
    HAS_ASAT_ULN_OF_AT_MOST_X[1.0],
    HAS_ALAT_ULN_OF_AT_MOST_X[1.0],
    HAS_ALP_ULN_OF_AT_MOST_X[2.5],
    HAS_ANY_X_AND_Y['LEFT', 'RIGHT']
)"""

    expected_rules = {"AND":
        [
            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE": [1.5, 3.0]},
            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE": [1.5,
                                                                                                              1.0]},
            {"HAS_ASAT_ULN_OF_AT_MOST_X": [1.0]},
            {"HAS_ALAT_ULN_OF_AT_MOST_X": [1.0]},
            {"HAS_ALP_ULN_OF_AT_MOST_X": [2.5]},
            {"HAS_ANY_X_AND_Y": ["LEFT", "RIGHT"]},
        ]}

    parsed_rules = parse_actin_rule(formatted)

    assert parsed_rules == expected_rules

def test_actin_rule_parser_nested():
    formatted = f"""AND
(
    OR
    (
        NOT(HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE[1.5, 3.0]),
        AND(HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE[1.5, 1.0],    
            HAS_ASAT_ULN_OF_AT_MOST_X[1.0]),
        HAS_ALAT_ULN_OF_AT_MOST_X[1.0],
        HAS_ALP_ULN_OF_AT_MOST_X[2.5],
        HAS_ANY_X_AND_Y['LEFT', 'RIGHT']
    )
)"""

    expected_rules = {
        "AND":
        [
            {"OR": [
                {"NOT": {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE": [1.5, 3.0]}},
                {"AND": [
                    {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_DIRECT_BILIRUBIN_ULN_OF_AT_MOST_Y_IF_GILBERT_DISEASE": [1.5, 1.0]},
                    {"HAS_ASAT_ULN_OF_AT_MOST_X": [1.0]}
                    ]
                },
                {"HAS_ALAT_ULN_OF_AT_MOST_X": [1.0]},
                {"HAS_ALP_ULN_OF_AT_MOST_X": [2.5]},
                {"HAS_ANY_X_AND_Y": ["LEFT", "RIGHT"]}
            ]}
        ]}

    parsed_rules = parse_actin_rule(formatted)
    assert parsed_rules == expected_rules
