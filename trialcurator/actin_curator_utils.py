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
        fix_rule_format(rule_object)
        logger.info(f"Loaded into actin rule object: {rule_object}")
        return rule_object
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON\n{e}")
        logger.warning(json_code_block)
        raise e

def fix_rule_format(data):
    """
    Recursively fixes

    1. logical operators (AND/OR) so that all items in their lists
    are dictionaries of the form { rule_name: [] } instead of plain strings.
    2. NOT such that the item is a dictionary of the form { rule_name: [] }

    """
    if isinstance(data, dict):
        new_data = {}
        for key, value in data.items():
            if key in {"AND", "OR"} and isinstance(value, list):
                fixed_list = []
                for item in value:
                    if isinstance(item, str):
                        fixed_list.append({item: []})
                    else:
                        fixed_list.append(fix_rule_format(item))
                new_data[key] = fixed_list
            elif key in {"NOT", "actin_rule"} and isinstance(value, str):
                new_data[key] = {value: []}
            else:
                new_data[key] = fix_rule_format(value)
        return new_data

    elif isinstance(data, list):
        return [fix_rule_format(item) for item in data]

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
        r'(?=[,\]}])',           # lookahead: Ensures that what follows the expression is ,] or }
        re.MULTILINE
    )
    fixed = pattern.sub(replacer, raw_json)

    return fixed
