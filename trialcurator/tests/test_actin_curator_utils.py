import unittest

from trialcurator.actin_curator_utils import fix_malformed_json, find_and_fix_actin_rule, fix_json_math_expressions, \
    find_new_actin_rules


class TestActinCuratorUtils(unittest.TestCase):

    def test_fix_rule_format(self):
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
                            {"HAS_CREATININE_CLEARANCE_CG_OF_AT_LEAST_X": 60}
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

        fixed_obj = find_and_fix_actin_rule(broken)
        self.assertEqual(expected, fixed_obj)

    def test_fix_rule_format2(self):
        input_obj = [
            {
                "description": "EXCLUDE Patients who will not get surgical treatment for their endometrial cancer",
                "actin_rule": {
                    "NOT": {
                        "IS_ELIGIBLE_FOR_SURGERY_TYPE_X": [
                            "endometrial cancer"
                        ]
                    }
                }
            }
        ]
        expected = [
            {
                "description": "EXCLUDE Patients who will not get surgical treatment for their endometrial cancer",
                "actin_rule": {
                    "NOT": {
                        "IS_ELIGIBLE_FOR_SURGERY_TYPE_X": [
                            "endometrial cancer"
                        ]
                    }
                }
            }
        ]

        fixed_obj = find_and_fix_actin_rule(input_obj)
        self.assertEqual(expected, fixed_obj)

    def test_fix_malformed_json(self):
        broken = '''[
    {
        "actin_rule": { "IS_MALE" },
        "actin_rule": "IS_MALE": [],
        "actin_rule": "POCKET_TWO": [1+1, "two"],
        "actin_rule": "NOT": "IS_MALE"
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
        "actin_rule": { "NOT": "IS_MALE" }
    }
    {
        "description": "EXCLUDE Prior gastrointestinal disease",
        "actin_rule": { "HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE": [] },
        "new_rule": []
    }
]'''

        fixed_json = fix_malformed_json(broken)
        self.assertEqual(expected, fixed_json)

    def test_fix_json_math_expressions(self):
        broken = '''[
            {
                "formula": { "NOT": { "OR": [
                        { "FIVE_PLUS_FIVE_EQUALS": [ 5+5, "=", 20 // 2] },
                        { "ONE_PLUS_ONE": [ 1+1 ] },
                    ] },
            },
            {
                "calculate": { "CALCULATE": [ 2*7, "1+1"] },
                "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 2*7, " 3/5 ", {}] } },
                "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 2*7, "6 + 7", []] } },
                ", in strings": { "CALCULATE": [ 2*7, "comma, here", []] },
            },
            {
                "calculate": { "CALCULATE": [
                        2*7,
                        5+5
                    ]
                },
                "calculate": 100 * 5
            }
        ]'''

        expected = '''[
            {
                "formula": { "NOT": { "OR": [
                        { "FIVE_PLUS_FIVE_EQUALS": [ 10, "=", 10] },
                        { "ONE_PLUS_ONE": [ 2 ] },
                    ] },
            },
            {
                "calculate": { "CALCULATE": [ 14, "1+1"] },
                "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 14, " 3/5 ", {}] } },
                "DO_NOT_CALC": { "NOT": { "CALCULATE": [ 14, "6 + 7", []] } },
                ", in strings": { "CALCULATE": [ 14, "comma, here", []] },
            },
            {
                "calculate": { "CALCULATE": [
                        14,
                        10
                    ]
                },
                "calculate": 500
            }
        ]'''

        fixed_json = fix_json_math_expressions(broken)
        self.assertEqual(expected, fixed_json)

    def test_find_new_actin_rule(self):
        rule = {
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
        }

        new_rules = find_new_actin_rules(rule, {"IS_FEMALE",
                                               "HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X",
                                               "HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT",
                                               "HAS_EGFR_MDRD_OF_AT_LEAST_X"})

        expected = ['HAS_CREATININE_CLEARANCE_CG_OF_AT_LEAST_X',
                    'HAS_THROMBOCYTES_ABS_OF_AT_LEAST_X',
                    'HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X_OR_Y_IF_GILBERT_DISEASE',
                    'IS_MALE']

        self.assertEqual(expected, new_rules)
