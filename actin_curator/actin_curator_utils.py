import logging
from collections.abc import Callable
from typing import Any

from trialcurator.llm_client import LlmClient
from trialcurator.utils import extract_code_blocks
from utils.parser import ParseError
from utils.smart_json_parser import SmartJsonParser

logger = logging.getLogger(__name__)


def parse_llm_mapping_output(llm_output: str) -> list[dict]:
    json_code_block = extract_code_blocks(llm_output, "json")

    try:
        obj = SmartJsonParser(json_code_block).consume_value()
        find_and_fix_actin_rule(obj)
        if isinstance(obj, list):
            return obj
        elif isinstance(obj, dict):
            return [obj]
        else:
            raise ValueError("Unexpected JSON structure: must be dict or list of dicts")

    except ParseError as e:
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
        for rule_name, params in rule.items():
            if (rule_name == "AND" or rule_name == "OR") and isinstance(params, list):
                new_data[rule_name] = [fix_actin_rule(p) for p in params]
            elif rule_name == "NOT":
                new_data[rule_name] = fix_actin_rule(params)
            # after this we are sure they are not composite critierion,
            # therefore it must have the format { "rule" : [...] }
            elif isinstance(params, list):
                new_data[rule_name] = params
            elif isinstance(params, bool):
                # ACTIN does not have bool param, LLM often do this: `HAS_X: true`, remove it
                new_data[rule_name] = []
            else:
                new_data[rule_name] = [params]
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

def find_new_actin_rules(rule: dict, defined_rules: set[str]) -> list[str]:
    """
    Find rules that are not already defined in the defined_rules list
    """

    new_rules: set[str] = set()

    for rule_name, params in rule.items():
        if rule_name == "AND" or rule_name == "OR":
            [new_rules.update(find_new_actin_rules(v, defined_rules)) for v in params]
        elif rule_name == "NOT":
            new_rules.update(find_new_actin_rules(params, defined_rules))
        elif rule_name not in defined_rules:
            new_rules.add(rule_name)

    return sorted(list(new_rules))


def actin_json_to_text_format(criterion: dict) -> str:
    description = criterion.get("description", "").strip()
    rule_expr = format_actin_rule(criterion["actin_rule"])
    new_rules = criterion.get("new_rule")

    output = (
        f"Input:\n    {description}\n"
        f"ACTIN Output:\n{indent_multiline(rule_expr)}\n"
        f"New rule:\n    {str(new_rules)}"
    )
    return output


def format_actin_rule(rule_obj, indent=4):
    indent_str = " " * indent
    inner_indent_str = " " * (indent + 2)

    if isinstance(rule_obj, dict):
        for key, value in rule_obj.items():
            if key in ("AND", "OR"):
                rendered = "\n".join(
                    f"{inner_indent_str}{format_actin_rule(r, indent + 2).lstrip()}" for r in value
                )
                return f"{key} (\n{rendered}\n{indent_str})"
            elif key == "NOT":
                return f"NOT ({format_actin_rule(value, indent)})"
            elif key == "IF":
                condition = format_actin_rule(rule_obj[key]["condition"], indent + 2).lstrip()
                then = format_actin_rule(rule_obj[key]["then"], indent + 2).lstrip()
                else_clause = rule_obj[key].get("else")
                if else_clause:
                    else_rendered = format_actin_rule(else_clause, indent + 2).lstrip()
                    return f"IF {condition} THEN {then} ELSE {else_rendered}"
                return f"IF {condition} THEN {then}"
            else:
                param_str = ", ".join(str(v) for v in value)
                return f"{key}[{param_str}]"
    else:
        return str(rule_obj)


def indent_multiline(text, indent=4):
    pad = " " * indent
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def output_formatting(actin_rule: dict, level: int = 0) -> str:
    """
    Removes empty []
    Change "[{...}]" to (...)
    """
    indent = "    " * level
    next_indent = "    " * (level + 1)

    if isinstance(actin_rule, dict):

        for key, val in actin_rule.items():

            if key in ('AND', 'OR'):
                val_list = []
                for item in val:
                    val_list.append(
                        output_formatting(item, level + 1)
                    )
                val = f",\n{next_indent}".join(val_list)
                return f"{key}\n{indent}(\n{next_indent}{val}\n{indent})"

            elif key == 'NOT':
                val = output_formatting(val, level + 1)
                return f"{key}\n{indent}(\n{next_indent}{val}\n{indent})"

            elif len(val) > 0:
                return f"{key}{val}"

            return f"{key}"

    else:
        raise TypeError("Unexpected type encountered")


def llm_json_repair(response: str, client: LlmClient, parser: Callable[[str], Any]) -> Any:
    try:
        return parser(response)
    except ParseError:
        logger.warning("LLM JSON output is invalid. Attempting to repair.")
        repair_prompt = f"""
Fix the following JSON so it parses correctly. Return only the corrected JSON object:
{response}
"""
        repaired_result = client.llm_ask(repair_prompt)
        return parser(repaired_result)
