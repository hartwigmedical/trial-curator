import json
import logging
import re

from .utils import extract_code_blocks

logger = logging.getLogger(__name__)


def llm_output_to_rule_obj(llm_output: str):
    json_code_block = extract_code_blocks(llm_output, "json")
    json_code_block = fix_malformed_json(json_code_block)

    try:
        rule_object = json.loads(json_code_block)
        find_and_fix_actin_rule(rule_object)
        logger.info(f"Loaded into actin rule object: {rule_object}")
        return rule_object
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON\n{e}")
        logger.warning(json_code_block)
        raise e


def fix_actin_rule(rule: str | dict | bool) -> dict:

    """
    Recursively fixes

    1. logical operators (AND/OR) so that all items in their lists
    are dictionaries of the form { rule_name: [] } instead of plain strings.
    2. NOT such that the item is a dictionary of the form { rule_name: [] }

    :param rule: an actin rule in python object form
    :return:     fixed actin rule in python object form
    """
    if isinstance(rule, str | bool):
        # a rule must look like { "rule" : [...] }
        return {rule: []}
    if isinstance(rule, dict):
        new_data = {}
        for key, value in rule.items():
            if key in {"AND", "OR"} and isinstance(value, list):
                new_data[key] = [fix_actin_rule(v) for v in value]
            elif key in {"NOT"} and isinstance(value, str):
                new_data[key] = {value: []}
            # after this we are sure they are not composite critierion, therefore
            # must have the format { "rule" : [...] }
            elif isinstance(value, list):
                new_data[key] = value
            else:
                new_data[key] = [value]
        return new_data

    return rule

def find_and_fix_actin_rule(data):

    """
    recursively search for all occurrences of the key 'actin_rule' and fix the value
    :param data: 
    :return: fixed data
    """

    if isinstance(data, dict):
        for key, value in data.items():
            if key == "actin_rule":
                data[key] = fix_actin_rule(value)
            else:
                find_and_fix_actin_rule(value)
    elif isinstance(data, list):
        for item in data:
            find_and_fix_actin_rule(item)

    return data


def fix_malformed_json(json_str: str) -> str:
    """
    Sometimes LLM outputs malformed json, it is much easier to fix with regex than
    to overload the prompts with more instructions
    """

    # fix dictionary without value, e.g., { "IS_MALE" } -> "IS_MALE"
    pattern = r'{\s*("\w+")\s*}'
    json_str = re.sub(pattern, r'\1', json_str)

    """
    Fix malformed entries like:
      "actin_rule": "RULE_NAME": [1]
    to:
      "actin_rule": { "RULE_NAME": [1] }
    """
    pattern = r'("\w+")\s*:\s*("\w+")\s*:\s*(\[[^\]]*\])'
    replacement = r'\1: { \2: \3 }'
    json_str = re.sub(pattern, replacement, json_str)

    # fix up anything that has uncompleted numerical calculations
    json_str = fix_json_math_expressions(json_str)

    return json_str


def fix_json_math_expressions(raw_json: str) -> str:
    """
    Fixes math expressions in JSON values, including inside lists.
    Only evaluates unquoted expressions.
    """

    def is_math_expression(s: str) -> bool:
        return bool(re.fullmatch(r'[\d.\s+\-*/()%]+\d', s.strip()))

    def safe_eval(expr: str):
        try:
            return eval(expr, {"__builtins__": None}, {})
        except Exception:
            return expr

    def replacer(match):
        prefix = match.group(1)
        expr = match.group(2)
        suffix = match.group(3)

        if is_math_expression(expr):
            result = safe_eval(expr)
            if isinstance(result, (int, float)):
                return f"{prefix}{result}{suffix}"

        return match.group(0)

    # Pattern to match unquoted values in dicts or lists
    # Match values after :, [ or , that are math expressions,
    # but NOT quoted (no ")
    pattern = re.compile(
        r'([:\[,]\s*)'    # matches the prefix
        r'([\d.\s+\-*/()%]+\d)'  # matches the math expression
        r'(\s*)'                 # spaces after the expression
        r'(?=[,\]}])',           # lookahead: ensures that what follows the expression is ,] or }
        re.MULTILINE
    )
    fixed = pattern.sub(replacer, raw_json)

    return fixed
