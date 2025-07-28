import logging
from collections.abc import Callable
from typing import Any

import pandas as pd

from trialcurator.utils import extract_code_blocks
from utils.parser import ParseError
from utils.smart_json_parser import SmartJsonParser

logger = logging.getLogger(__name__)


def load_actin_resource(filepath: str) -> tuple[pd.DataFrame, list[str]]:
    actin_df = pd.read_csv(filepath, header=0)
    actin_categories = actin_df.columns.str.strip().tolist()
    return actin_df, actin_categories


def flatten_actin_rules(actin_df: pd.DataFrame) -> set[str]:
    actin_rules = (pd.Series(actin_df.to_numpy().flatten()).dropna().str.strip().tolist())
    return set(actin_rules)


def find_new_actin_rules(rule: dict | list | str, defined_rules: set[str]) -> list[str]:
    new_rules: set[str] = set()

    # Recursion base case
    if isinstance(rule, str):
        extract_rule = rule.split("[")[0].strip()  # For cases like "HAS_XYZ_RULE[1.5]" or "HAS_XYZ_RULE"
        if extract_rule not in defined_rules:
            new_rules.add(extract_rule)

    elif isinstance(rule, list):
        for subrule in rule:
            new_rules.update(find_new_actin_rules(subrule, defined_rules))

    elif isinstance(rule, dict):
        for operator, params in rule.items():
            if operator in {"AND", "OR"}:
                for subrule in params:
                    new_rules.update(find_new_actin_rules(subrule, defined_rules))
            elif operator == "NOT":
                new_rules.update(find_new_actin_rules(params, defined_rules))

            else:
                if operator not in defined_rules:  # operator becomes a potential rule name here
                    new_rules.add(operator)  # This is of the form {'SOME_RULE_NAME': ['param_val']}

                if isinstance(params, dict):
                    new_rules.update(find_new_actin_rules(params, defined_rules))
                elif isinstance(params, list):
                    has_nesting = False
                    for ele in params:
                        if isinstance(ele, (dict, list)):
                            has_nesting = True
                            break
                    if has_nesting:
                        new_rules.update(find_new_actin_rules(params, defined_rules))
                        # Otherwise disregard forms such as ['val_1', 'val_2']

    return sorted(new_rules)



def actin_rule_reformat_w_newline_indentation(actin_rule: dict | list | str, level: int = 0) -> str:
    logger.info("\nSTART ACTIN RULE REFORMATTING\n")
    """
    Recursively format an ACTIN rule structure (dict/list/str) into a human-readable string.

    Handles:
    - Rule with no parameters: {"RULE": []}         → "RULE"
    - Rule with parameters:    {"RULE": [1.5, 3.0]} → "RULE[1.5, 3.0]"
    - Nested logic (AND/OR/NOT): adds indentation and parentheses
    - List values (leaf-level): returns '[val1, val2, ...]' using repr
    """

    indent = "    " * level
    next_indent = "    " * (level + 1)

    # recursion base case 1
    if isinstance(actin_rule, str):
        return actin_rule.replace("[]", "")  # LLM is liable return results like `HAS_LEPTOMENINGEAL_DISEASE[]`

    # recursion base case 2
    elif isinstance(actin_rule, list):
        reformatted_container = []
        for item in actin_rule:
            item_str = repr(item)  # Do not recurse if it's a list. Only a minor str transformation
            reformatted_container.append(item_str)
        joined_items = ", ".join(reformatted_container)
        return "[" + joined_items + "]"

    elif isinstance(actin_rule, dict):
        if len(actin_rule) != 1:
            raise ValueError(f"Expected dict with 1 key. Instead have {len(actin_rule)}: {actin_rule}")

        for key, val in actin_rule.items():

            if key in {"AND", "OR"}:
                reformatted_container = []
                for item in val:
                    # recurse here
                    reformatted_container.append(actin_rule_reformat_w_newline_indentation(item, level + 1))
                joined_items = f",\n{next_indent}".join(reformatted_container)
                return f"{key}\n{indent}(\n{next_indent}" + joined_items + f"\n{indent})"

            elif key == "NOT":
                # recurse here
                reformatted_rule = actin_rule_reformat_w_newline_indentation(val, level+1)
                return f"{key}\n{indent}(\n{next_indent}{reformatted_rule}\n{indent})"

            else:
                if isinstance(val, dict):
                    # recurse further into dict
                    reformatted_rule = actin_rule_reformat_w_newline_indentation(val, level+1)
                    return f"{key}\n{indent}(\n{next_indent}{reformatted_rule}\n{indent})"

                elif isinstance(val, list):
                    has_nesting = False
                    for item in val:
                        if isinstance(item, (dict, list)):
                            has_nesting = True
                            break
                    if has_nesting:  # recurse deeper due to nesting
                        # recurse here
                        reformatted_rule = actin_rule_reformat_w_newline_indentation(val, level + 1)
                        return f"{key}\n{indent}(\n{next_indent}{reformatted_rule}\n{indent})"

                    elif len(val) > 0:  # in a flat list of parameters situation like [1.5, 2.3]. No more recursion.
                        reformatted_container = []
                        for sub_val in val:
                            sub_val_str = repr(sub_val)
                            reformatted_container.append(sub_val_str)
                        joined_items = ", ".join(reformatted_container)
                        return f"{key}[{joined_items}]"

                    else:
                        return key

        raise ValueError(f"Could not format ACTIN rule from dict: {actin_rule}")

    else:
        raise TypeError(f"Unexpected data type encountered for actin_rule: {type(actin_rule).__name__} for {actin_rule}")
