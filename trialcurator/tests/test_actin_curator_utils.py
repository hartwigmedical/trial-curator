import unittest

from trialcurator.actin_curator_utils import fix_malformed_json


class TestActinCuratorUtils(unittest.TestCase):
    def test_fix_malformed_json(self):
        broken = '''[
    {
        "description": "EXCLUDE Adverse events from prior anti-cancer therapy that have not resolved",
        "actin_rule": { "NOT": { "HAS_EXPERIENCED_IMMUNOTHERAPY_RELATED_ADVERSE_EVENTS": [] } },
        "new_rule": []
    },
    {
        "description": "EXCLUDE Known AIDS-related illness, HBV, or HCV",
        "actin_rule": { "NOT": { "OR": [
                { "HAS_KNOWN_HIV_INFECTION": [ ] },
                { "HAS_KNOWN_HEPATITIS_B_INFECTION": [ 1+1 ] },
                { "HAS_KNOWN_HEPATITIS_C_INFECTION": [] }
            ] },
        "new_rule": []
    },
    {
        "description": "EXCLUDE ",
        "actin_rule": { "NOT": { "CALCULATE": [ 2*7, "TWO"] } },
        "new_rule": []
    },
    {
        "description": "EXCLUDE Recent surgery or radiotherapy",
        "actin_rule": { "NOT": "PREGNANT" },
        "new_rule": []
    },
    {
        "description": "EXCLUDE Prior gastrointestinal disease",
        "actin_rule": "HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE",
        "new_rule": []
    }
    {
        "description": "EXCLUDE Prior gastrointestinal disease",
        "actin_rule": "HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE": [],
        "new_rule": []
    }
]'''

        expected = '''[
    {
        "description": "EXCLUDE Adverse events from prior anti-cancer therapy that have not resolved",
        "actin_rule": { "NOT": { "HAS_EXPERIENCED_IMMUNOTHERAPY_RELATED_ADVERSE_EVENTS": [] } },
        "new_rule": []
    },
    {
        "description": "EXCLUDE Known AIDS-related illness, HBV, or HCV",
        "actin_rule": { "NOT": { "OR": [
                { "HAS_KNOWN_HIV_INFECTION": [] },
                { "HAS_KNOWN_HEPATITIS_B_INFECTION": [2] },
                { "HAS_KNOWN_HEPATITIS_C_INFECTION": [] }
            ] },
        "new_rule": []
    },
    {
        "description": "EXCLUDE ",
        "actin_rule": { "NOT": { "CALCULATE": [14, "TWO"] } },
        "new_rule": []
    },
    {
        "description": "EXCLUDE Recent surgery or radiotherapy",
        "actin_rule": { "NOT": { "PREGNANT": [] } },
        "new_rule": []
    },
    {
        "description": "EXCLUDE Prior gastrointestinal disease",
        "actin_rule": { "HAS_HISTORY_OF_GASTROINTESTINAL_DISEASE": [] },
        "new_rule": []
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

        fixed_json = fix_malformed_json(broken)
        self.assertEqual(expected, fixed_json)