from utils.smart_json_parser import SmartJsonParser

def test_well_formed_json():
    json_str = '''[
            {
                "formula": {
                    "NOT": {
                        "OR": [
                            { "FIVE_PLUS_FIVE_EQUALS": [ 10, "=", 10] },
                            { "ONE_PLUS_ONE": [ 2 ] }
                        ]
                    }
                }
            },
            {
                "calculate": { "CALCULATE": [ 14, "1+1"] },
                "DO_NOT_CALC1": { "NOT": { "CALCULATE": [ 14, " 3/5 ", {}] } },
                "DO_NOT_CALC2": { "NOT": { "CALCULATE": [ 14, "6 + 7", []] } },
                ", in strings": { "CALCULATE": [ 14, "comma, here", []] }
            },
            {
                "calculate_1": { "CALCULATE": [
                        14,
                        10
                    ]
                },
                "calculate_2": 500
            }
        ]'''

    expected = [
        {
            "formula": {
                "NOT": {
                    "OR": [
                        {"FIVE_PLUS_FIVE_EQUALS": [10, "=", 10]},
                        {"ONE_PLUS_ONE": [2]},
                    ]
                },
            }
        },
        {
            "calculate": {"CALCULATE": [14, "1+1"]},
            "DO_NOT_CALC1": {"NOT": {"CALCULATE": [14, " 3/5 ", {}]}},
            "DO_NOT_CALC2": {"NOT": {"CALCULATE": [14, "6 + 7", []]}},
            ", in strings": {"CALCULATE": [14, "comma, here", []]},
        },
        {
            "calculate_1": {"CALCULATE": [
                14,
                10
            ]
            },
            "calculate_2": 500
        }
    ]

    parsed_obj = SmartJsonParser(json_str).consume_value()

    assert parsed_obj == expected


def test_fix_malformed_json():
    broken = '''[
        {
            "actin_rule_1": { "IS_MALE" },
            "actin_rule_2": "IS_MALE": [],
            "actin_rule_3": "POCKET_TWO": [1+1, "two"],
            "actin_rule_4": "NOT": "IS_MALE"
        },
        {
            "description": "EXCLUDE Prior gastrointestinal disease",
            "actin_rule": "HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE": [],
            "new_rule": []
        }
    ]'''

    expected = [
        {
            "actin_rule_1": "IS_MALE",
            "actin_rule_2": {"IS_MALE": []},
            "actin_rule_3": {"POCKET_TWO": [2, "two"]},
            "actin_rule_4": {"NOT": "IS_MALE"}
        },
        {
            "description": "EXCLUDE Prior gastrointestinal disease",
            "actin_rule": {"HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE": []},
            "new_rule": []
        }
    ]

    fixed_json = SmartJsonParser(broken).consume_value()

    assert fixed_json == expected


def test_fix_json_math_expressions():
    broken = '''[
            {
                "formula": { "NOT": { "OR": [
                        { "FIVE_PLUS_FIVE_EQUALS": [ 5+5, "=", 20 // 2] },
                        { "ONE_PLUS_ONE": [ 1+1 ] }
                    ] }
                }
            },
            {
                "calculate": { "CALCULATE": [ 2*7, "1+1"] },
                "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 2*7, " 3/5 ", {}] } },
                "DO_NOT_CALC1": { "NOT": { "CALCULATE": [ 6 / 2, "6 + 7", []] } },
                ", in strings": { "CALCULATE": [ 2 * 10, "comma, here", []] }
            },
            {
                "calculate": { "CALCULATE": [
                        10 - 4,
                        5+5
                    ]
                },
                "calculate1": 100 * 5
            }
        ]'''

    expected = [
        {
            "formula": {"NOT": {"OR": [
                {"FIVE_PLUS_FIVE_EQUALS": [10, "=", 10]},
                {"ONE_PLUS_ONE": [2]},
            ]}}
        },
        {
            "calculate": {"CALCULATE": [14, "1+1"]},
            "DO_NOT_CALC": {"NOT": {"CALCULATE": [14, " 3/5 ", {}]}},
            "DO_NOT_CALC1": {"NOT": {"CALCULATE": [3.0, "6 + 7", []]}},
            ", in strings": {"CALCULATE": [20, "comma, here", []]},
        },
        {
            "calculate": {"CALCULATE": [
                6,
                10
            ]
            },
            "calculate1": 500
        }
    ]

    parsed_obj = SmartJsonParser(broken).consume_value()
    assert parsed_obj == expected


def test_missing_close_braces():
    broken = '''[
        {
            "formula": {
                "NOT": {
                    "OR": [
                        { "TEST": [ 1, 2, 3 ],
                        { "TEST2": [ 2 ] }
                    ]
        }
    ]'''

    expected = [
        {
            "formula": {
                "NOT": {
                    "OR": [
                        {"TEST": [1, 2, 3]},
                        {"TEST2": [2]}
                    ]
                }
            }
        },
    ]

    parsed_obj = SmartJsonParser(broken).consume_value()

    assert parsed_obj == expected
