import os
import json
import pandas as pd
import argparse
import logging
import re
from typing import Any
from copy import deepcopy
from trialcurator.openai_client import OpenaiClient
from trialcurator.utils import load_trial_data, load_eligibility_criteria
from actin_eligibility_curator import load_actin_resource, actin_workflow_by_cohort, ActinMapping

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

TEMPERATURE = 0.0


def simplify_actual_output(actual_output_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def flatten_actin_rule(rule: Any) -> str:
        def render(key: str, val: list) -> str:
            key = re.sub(r"\[\s*\]$", "", key)  # clean up empty brackets
            if isinstance(val, list) and any(p not in (None, "", [], {}) for p in val):
                return f"{key}[{','.join(str(p) for p in val)}]"
            return key

        if isinstance(rule, dict):
            if "NOT" in rule:
                return f"NOT({flatten_actin_rule(rule['NOT'])})"
            elif "AND" in rule:
                return f"AND({', '.join(flatten_actin_rule(r) for r in rule.get('AND', []))})"
            elif "OR" in rule:
                return f"OR({', '.join(flatten_actin_rule(r) for r in rule.get('OR', []))})"
            elif len(rule) == 1:
                key, val = next(iter(rule.items()))
                return render(key, val)
        elif isinstance(rule, str):
            return rule.strip()
        return str(rule) if rule is not None else ""

    cleaned_list = []
    for item in actual_output_list:
        new_item = deepcopy(item)

        # Strip and clean description
        if "description" in new_item and isinstance(new_item["description"], str):
            new_item["description"] = new_item["description"].strip()

        # Prefer raw dict if present, else try to decode mapping if it's a JSON string
        actin_rule_raw = new_item.get("actin_rule")
        if actin_rule_raw is None and "mapping" in new_item:
            try:
                import json
                actin_rule_raw = json.loads(new_item["mapping"])
            except (json.JSONDecodeError, TypeError):
                actin_rule_raw = None

        # If we successfully got a rule, flatten and store it back as "mapping"
        new_item["mapping"] = flatten_actin_rule(actin_rule_raw) if actin_rule_raw else ""

        cleaned_list.append(new_item)

    return cleaned_list


def tabulate_actin_output(actin_output: dict[str, list[ActinMapping]], trial_id: str) -> list[dict]:
    result_list = []
    for cohort, mappings in actin_output.items():
        for mapping in mappings:
            result_list.append({
                "trial_id": trial_id,
                "cohort": cohort,
                "criterion": mapping["description"],
                "mapping": json.dumps(mapping.get("actin_rule"), indent=2),
                "new_rule": mapping.get("new_rule"),
                "confidence_level": mapping.get("confidence_level"),
                "confidence_explanation": mapping.get("confidence_explanation"),
            })
    return result_list


def compare_runs(run1_in_list: list[dict], run2_in_list: list[dict]) -> pd.DataFrame:
    df1 = pd.DataFrame(run1_in_list).rename(columns={
        "mapping": "run1_mapping",
        "new_rule": "run1_new_rule",
        "confidence_level": "run1_confidence_level",
        "confidence_explanation": "run1_confidence_explanation",
    })
    df2 = pd.DataFrame(run2_in_list).rename(columns={
        "mapping": "run2_mapping",
        "new_rule": "run2_new_rule",
        "confidence_level": "run2_confidence_level",
        "confidence_explanation": "run2_confidence_explanation",
    })
    final = pd.merge(
        left=df1,
        right=df2,
        on=["trial_id", "cohort", "criterion"],
        how="outer",
        sort=True
    )
    return final[[
        "trial_id", "cohort", "criterion",
        "run1_mapping", "run1_new_rule", "run1_confidence_level", "run1_confidence_explanation",
        "run2_mapping", "run2_new_rule", "run2_confidence_level", "run2_confidence_explanation"
    ]]


def main():
    parser = argparse.ArgumentParser(description="ACTIN trial curator")
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--ACTIN_path', help='Full path to ACTIN rules CSV', required=True)
    parser.add_argument("--df_csv", help="Path to summary table", required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    client = OpenaiClient(TEMPERATURE)
    actin_df, actin_categories = load_actin_resource(args.ACTIN_path)
    trial_data = load_trial_data(args.trial_json)
    trial_id = trial_data.get("protocolSection", {}).get("identificationModule", {}).get("nctId", "UNKNOWN")
    eligibility_criteria = load_eligibility_criteria(trial_data)

    logger.info("1st run...")
    run1_raw = actin_workflow_by_cohort(eligibility_criteria, client, actin_df, actin_categories)
    logger.info("2nd run...")
    run2_raw = actin_workflow_by_cohort(eligibility_criteria, client, actin_df, actin_categories)

    run1_table = tabulate_actin_output(run1_raw, trial_id)
    run2_table = tabulate_actin_output(run2_raw, trial_id)

    run1_final = simplify_actual_output(run1_table)
    run2_final = simplify_actual_output(run2_table)

    logger.info("COMBINE RUNS...")
    compare_tbl = compare_runs(run1_final, run2_final)

    file_exists = os.path.exists(args.df_csv)
    compare_tbl.to_csv(
        args.df_csv,
        mode='a',  # Always append
        header=not file_exists,  # Write header only if file doesn't exist
        index=False
    )


if __name__ == "__main__":
    main()
