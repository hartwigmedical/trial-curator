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
    # json_str = evaluate_and_fix_json_lists(json_str)

    return json_str


def evaluate_and_fix_json_lists(json_str: str) -> str:
    """
    Fix and evaluate simple arithmetic expressions inside JSON list values,
    e.g. [2*7, 1/2] -> [15, 0.5]
    but only for lists that do not contain {} or [].
    """
    math_ops = ('+', '-', '*', '/', '%')

    def eval_list_expr(match):
        list_content = match.group(1)

        # Skip lists with { or [ in the content
        if any(c in list_content for c in '{}[]'):
            return f"[{list_content}]"

        items = tokenize_list_items(list_content)
        evaluated_items = []
        for item in items:
            if (not item.startswith('"')) and (any(op in item for op in math_ops)):
                # evaluating numeric expressions
                val = eval(item, {"__builtins__": None}, {})
                evaluated_items.append(str(val))
            else:
                evaluated_items.append(item)

        return '[' + ', '.join(evaluated_items) + ']'

    # Regex: match [...] blocks with at least one item but exclude ones containing {} or []
    pattern = r'\[\s*([^{}\[\]]+?)\s*\]'
    fixed_json_str = re.sub(pattern, eval_list_expr, json_str)
    return fixed_json_str


def tokenize_list_items(list_content) -> list[str]:
    """Tokenize list items safely, preserving quoted strings."""
    items = []
    current = ""
    in_string = False
    quote_char = None

    for char in list_content:
        if in_string:
            current += char
            if char == quote_char:
                in_string = False
        else:
            if char in ('"', "'"):
                in_string = True
                quote_char = char
                current += char
            elif char == ',':
                items.append(current.strip())
                current = ""
            else:
                current += char
    if current:
        items.append(current.strip())
    return items
