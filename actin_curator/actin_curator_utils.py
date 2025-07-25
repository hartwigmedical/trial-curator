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
