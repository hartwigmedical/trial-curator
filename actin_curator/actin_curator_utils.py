import logging
import pandas as pd


logger = logging.getLogger(__name__)


def actin_rule_is_empty(actin_rule: dict | list | str | None) -> bool:
    """
    Return True if actin_rule contains ONLY logical operators (AND/OR/NOT)
    """

    if actin_rule is None:
        return True

    if isinstance(actin_rule, str):
        return actin_rule.strip() == ""

    if isinstance(actin_rule, list):
        if not actin_rule:
            return True

        return all(actin_rule_is_empty(x) for x in actin_rule)

    if isinstance(actin_rule, dict):
        if not actin_rule:
            return True

        if len(actin_rule) != 1:
            return False

        key, val = next(iter(actin_rule.items()))

        if key in {"AND", "OR"}:
            if not isinstance(val, list) or len(val) == 0:
                return True

            return all(actin_rule_is_empty(x) for x in val)

        if key == "NOT":
            return actin_rule_is_empty(val)

        return False

    return False


def blank_shell_only_actin_rule_fields(criteria: dict) -> dict:
    out = criteria.copy()

    if actin_rule_is_empty(out.get("actin_rule")):
        out["actin_rule"] = ""
        out["actin_rule_reformat"] = ""

    return out


def _split_actin_rule_and_warnif_columns(actin_df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """
    The ACTIN resource contains paired columns <CATEGORY>, <WARN_IF> for each category.
    """
    cols = [str(c).strip() for c in actin_df.columns.tolist()]

    if len(cols) % 2 != 0:
        raise ValueError(f"Expected an even number of columns (rule/warnif pairs). Got {len(cols)} columns.")

    category_cols: list[str] = []
    warnif_cols: list[str] = []

    for i in range(0, len(cols), 2):
        rule_col = cols[i]
        warn_col = cols[i + 1]

        category_cols.append(rule_col)
        warnif_cols.append(warn_col)

    return category_cols, warnif_cols


def _build_rule_to_warnif_map_from_pairs(actin_df_raw: pd.DataFrame, category_cols: list[str], warnif_cols: list[str]) -> dict[str, bool]:
    rule_to_warnif: dict[str, bool] = {}

    for cat_col, warn_col in zip(category_cols, warnif_cols):
        for raw_rule, raw_warn in zip(actin_df_raw[cat_col].tolist(), actin_df_raw[warn_col].tolist()):
            if raw_rule is None:
                continue

            rule = str(raw_rule).strip()
            if not rule or rule.lower() == "nan":
                continue

            if rule in rule_to_warnif:
                continue  # first occurrence used if there are accidental rule duplicates

            warn = False  # default WARN_IF to False
            if raw_warn is not None:
                if isinstance(raw_warn, bool):
                    warn = raw_warn
                elif isinstance(raw_warn, (int, float)):
                    if isinstance(raw_warn, float) and raw_warn != raw_warn:
                        warn = False
                    else:
                        warn = int(raw_warn) == 1
                else:
                    s = str(raw_warn).strip().lower()
                    warn = s in {"true", "t", "1", "yes", "y"}

            rule_to_warnif[rule] = warn

    return rule_to_warnif


def load_actin_resource(filepath: str) -> tuple[pd.DataFrame, list[str], dict[str, bool]]:
    actin_df_raw = pd.read_csv(filepath, header=0)

    category_cols, warnif_cols = _split_actin_rule_and_warnif_columns(actin_df_raw)
    rule_to_warnif = _build_rule_to_warnif_map_from_pairs(actin_df_raw, category_cols, warnif_cols)

    actin_rules_df = actin_df_raw[category_cols].copy()
    actin_categories = category_cols

    return actin_rules_df, actin_categories, rule_to_warnif


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
        raise TypeError(f"Unexpected data type encountered for actin_rule: {type(actin_rule).__name__} for {actin_rule}")
