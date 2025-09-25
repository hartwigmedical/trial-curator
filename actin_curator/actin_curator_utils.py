import logging
import pandas as pd

import json
import re
from typing import Any, Dict, List, Union

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


def actin_rule_reformat(actin_rule: dict | list | str) -> str:
    logger.info("\nSTART ACTIN RULE REFORMATTING\n")
    """
    Recursively format an ACTIN rule structure (dict/list/str) into a human-readable string.
    Outputs a single line - no new line delimiters nor indentations

    Handles:
    - Rule with no parameters: {"RULE": []}         → "RULE"
    - Rule with parameters:    {"RULE": [1.5, 3.0]} → "RULE[1.5, 3.0]"
    - Nested logic (AND/OR/NOT): adds indentation and parentheses
    - List values (leaf-level): returns '[val1, val2, ...]' using repr
    """

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
                    reformatted_container.append(actin_rule_reformat(item))
                joined_items = ", ".join(reformatted_container)
                return f"{key}({joined_items})"

            elif key == "NOT":
                # recurse here
                reformatted_rule = actin_rule_reformat(val)
                return f"{key}({reformatted_rule})"

            else:
                if isinstance(val, dict):
                    # recurse further into dict
                    reformatted_rule = actin_rule_reformat(val)
                    return f"{key}({reformatted_rule})"

                elif isinstance(val, list):
                    has_nesting = False
                    for item in val:
                        if isinstance(item, (dict, list)):
                            has_nesting = True
                            break
                    if has_nesting:  # recurse deeper due to nesting
                        # recurse here
                        reformatted_rule = actin_rule_reformat(val)
                        return f"{key}({reformatted_rule})"

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
        raise TypeError(
            f"Unexpected data type encountered for actin_rule: {type(actin_rule).__name__} for {actin_rule}")


_RULE_TOKEN_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*\[\s*(.*?)\s*\]\s*$")


def _coerce_json_params(inner: str) -> List[Any]:
    """
    inner is the text inside the square brackets in RULE[inner].
    Try JSON first; accept scalars or lists; otherwise fallback to CSV split.
    Always trim strings and coerce numeric strings to numbers.
    """
    if inner in ("", None):
        return []
    # 1) Direct JSON (may be list or scalar)
    try:
        parsed = json.loads(inner)
        if isinstance(parsed, list):
            return [_clean_param(_maybe_number(p)) for p in parsed]
        return [_clean_param(_maybe_number(parsed))]
    except Exception:
        pass
    # 2) Try wrapping as list
    try:
        parsed = json.loads(f"[{inner}]")
        if isinstance(parsed, list):
            return [_clean_param(_maybe_number(p)) for p in parsed]
    except Exception:
        pass
    # 3) Fallback CSV split
    parts = [p.strip() for p in inner.split(",") if p.strip()]
    return [_clean_param(_maybe_number(p)) for p in parts]


def _maybe_number(x: Any) -> Any:
    if not isinstance(x, str):
        return x
    try:
        if "." in x:
            return float(x)
        return int(x)
    except Exception:
        return x


def _clean_param(p: Any) -> Any:
    # Trim stray whitespace for strings like ' uveal melanoma '
    if isinstance(p, str):
        return p.strip()
    return p


def _parse_rule_token(token: str) -> Dict[str, Any]:
    """
    Convert 'RULE[]' or 'RULE["foo", 2]' to {'RULE': [...]}
    """
    m = _RULE_TOKEN_RE.match(token)
    if not m:
        # Not a RULE[...] string; return as-is inside a canonical container
        # (but the caller should avoid passing us non-rule tokens)
        return {token.strip(): []}
    name, inner = m.groups()
    params = _coerce_json_params(inner)
    return {name.strip(): params}


def _normalize_rule_obj(obj: Any) -> Any:
    """
    Recursively normalize a rule object:
    - Strings like 'RULE[... ]' -> {'RULE': [params]}
    - Dicts with AND/OR/NOT -> normalize their contents
    - Dicts with RULE -> keep, but clean params
    """
    # String case: "RULE[...]" or "RULE[]"
    if isinstance(obj, str):
        return _parse_rule_token(obj)

    if isinstance(obj, dict):
        if not obj:
            return obj
        (k, v), = obj.items()
        k = k.strip()
        if k in ("AND", "OR"):
            if isinstance(v, list):
                return {k: [_normalize_rule_obj(x) for x in v]}
            # tolerate single-item non-list
            return {k: [_normalize_rule_obj(v)]}
        if k == "NOT":
            return {"NOT": _normalize_rule_obj(v)}
        # RULE dict: ensure params are cleaned
        if isinstance(v, list):
            return {k: [_clean_param(x) for x in v]}
        # Single scalar param -> wrap list
        return {k: [_clean_param(v)]}

    # List case (rare at top level, but normalize elements)
    if isinstance(obj, list):
        return [_normalize_rule_obj(x) for x in obj]

    # Fallback: return unchanged
    return obj
