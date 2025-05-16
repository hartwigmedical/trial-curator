import unittest

from trialcurator.actin_curator_utils import fix_malformed_json, fix_rule_format, evaluate_and_fix_json_lists


class TestActinCuratorUtils(unittest.TestCase):

    def test_fix_fix_rule_format(self):
        broken = [
            {
                "actin_rule": "IS_MALE",
            },
            {
                "actin_rule": {"NOT": "IS_MALE"}
            },
            {
                "actin_rule": {
                    "AND": [
                        "IS_FEMALE",
                        {"HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X": [1500]},
                        {"HAS_THROMBOCYTES_ABS_OF_AT_LEAST_X": [100000]},
                        {"OR": [
                            "IS_MALE",
                            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE": [3.0]}
                        ]},
                        {"HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT": [2.5, 5.0]},
                        {"AND": [
                            {"HAS_EGFR_MDRD_OF_AT_LEAST_X": [60]},
                            {"HAS_CREATININE_CLEARANCE_CG_OF_AT_LEAST_X": [60]}
                        ]}
                    ]
                },
            }
        ]

        expected = [
            {
                "actin_rule": {"IS_MALE": []},
            },
            {
                "actin_rule": {"NOT": {"IS_MALE": []}}
            },
            {
                "actin_rule": {
                    "AND": [
                        {"IS_FEMALE": []},
                        {"HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X": [1500]},
                        {"HAS_THROMBOCYTES_ABS_OF_AT_LEAST_X": [100000]},
                        {"OR": [
                            {"IS_MALE": []},
                            {"HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE": [3.0]}
                        ]},
                        {"HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT": [2.5, 5.0]},
                        {"AND": [
                            {"HAS_EGFR_MDRD_OF_AT_LEAST_X": [60]},
                            {"HAS_CREATININE_CLEARANCE_CG_OF_AT_LEAST_X": [60]}
                        ]}
                    ]
                },
            }
        ]

        fixed_obj = fix_rule_format(broken)
        self.assertEqual(expected, fixed_obj)

    def test_fix_malformed_json(self):
        broken = '''[
    {
        "actin_rule": { "IS_MALE" },
        "actin_rule": "IS_MALE": [],
        "actin_rule": "POCKET_TWO": [2, "two"],
    }
    {
        "description": "EXCLUDE Prior gastrointestinal disease",
        "actin_rule": "HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE": [],
        "new_rule": []
    }
]'''

        expected = '''[
    {
        "actin_rule": "IS_MALE",
        "actin_rule": { "IS_MALE": [] },
        "actin_rule": { "POCKET_TWO": [2, "two"] },
    }
    {
        "description": "EXCLUDE Prior gastrointestinal disease",
        "actin_rule": { "HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE": [] },
        "new_rule": []
    }
]'''

        fixed_json = fix_malformed_json(broken)
        self.assertEqual(expected, fixed_json)

    def test_eval_json_list(self):
        broken = '''[
    {
        "formula": { "NOT": { "OR": [
                { "FIVE_PLUS_FIVE_EQUALS": [ 5+5, "=", 20 // 2] },
                { "ONE_PLUS_ONE": [ 1+1 ] },
            ] },
    },
    {
        "calculate": { "CALCULATE": [ 2*7, "TWO"] },
        "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 2*7, "TWO", {}] } },
        "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 2*7, "TWO", []] } },
        ", in strings": { "CALCULATE": [ 2*7, "comma, here", []] },
    },
]'''

        expected = '''[
    {
        "formula": { "NOT": { "OR": [
                { "FIVE_PLUS_FIVE_EQUALS": [10, "=", 10] },
                { "ONE_PLUS_ONE": [2] },
            ] },
    },
    {
        "calculate": { "CALCULATE": [14, "TWO"] },
        "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 2*7, "TWO", {}] } },
        "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 2*7, "TWO", []] } },
        ", in strings": { "CALCULATE": [ 2*7, "comma, here", []] },
    },
]'''

        fixed_json = evaluate_and_fix_json_lists(broken)
        self.assertEqual(expected, fixed_json)