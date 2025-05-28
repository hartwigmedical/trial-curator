import json
from json import JSONDecodeError
import pandas as pd
import logging
import argparse
from typing import TypedDict, cast

from trialcurator.actin_curator_utils import llm_output_to_rule_obj, find_new_actin_rules
from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient
from trialcurator.utils import load_trial_data, load_eligibility_criteria, batch_tagged_criteria_by_words
from trialcurator.eligibility_sanitiser import llm_extract_cohort_tagged_text
from trialcurator import actin_mapping_prompts

logger = logging.getLogger(__name__)

TEMPERATURE = 0.0
BATCH_MAX_WORDS = 60


class ActinMapping(TypedDict):
    description: str
    actin_rule: dict[str, list | dict]
    new_rule: list[str]


def load_actin_resource(filepath: str) -> tuple[pd.DataFrame, list[str]]:
    actin_df = pd.read_csv(filepath, header=0)
    actin_categories = actin_df.columns.str.strip().tolist()
    return actin_df, actin_categories


def identify_actin_categories(eligibility_criteria: str, client: LlmClient, actin_categories: list[str]) -> dict:
    category_str = "\n".join(f"- {cat}" for cat in actin_categories)
    logger.info(f"Classifying {len(eligibility_criteria.splitlines())} criteria into ACTIN categories.")

    system_prompt = f"""
## ROLE
You are a clinical trial curation assistant for a system called ACTIN, which determines available 
treatment options for cancer patients.

## TASK
Classify each eligibility criterion into one or more ACTIN categories.

## ACTIN CATEGORIES
The following categories are available:
{category_str}

## OUTPUT FORMAT
Return a valid JSON object. Do not include any extra text.

Example:
{{
    "INCLUDE Histologically or cytologically confirmed metastatic CRPC": ["Cancer_Type_and_Tumor_Site_Localization"],
    "EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment": ["Infectious_Disease_History_and_Status"]
}}
"""

    user_prompt = f"""
Classify the following eligibility criteria:
{eligibility_criteria}
"""

    response = client.llm_ask(user_prompt, system_prompt)

    try:
        return llm_output_to_rule_obj(response)
    except JSONDecodeError:
        repair_prompt = f"""Fix the following JSON so it parses correctly. Return only the corrected JSON object:
{response}
"""
        repaired_response = client.llm_ask(repair_prompt)
        return llm_output_to_rule_obj(repaired_response)


def sort_criteria_by_category(cat_criteria: dict) -> dict:

    sorted_cat_criteria = {}
    temp_cat = None
    temp_criteria = []

    for criterion, cat in cat_criteria.items():
        cat = tuple(cat)

        if cat == temp_cat:
            temp_criteria.append(criterion)
        else:
            if temp_cat is not None:
                sorted_cat_criteria[temp_cat] = temp_criteria
            temp_cat = cat
            temp_criteria = [criterion]

    if temp_criteria:
        sorted_cat_criteria[temp_cat] = temp_criteria

    return sorted_cat_criteria


def map_to_actin_by_category(sorted_cat_criteria: dict, client: LlmClient, actin_df: pd.DataFrame) -> list[dict]:
    mapped_results = []
    logger.info(f"Mapping criterion by ACTIN categories")

    for cat, criteria_list in sorted_cat_criteria.items():
        category_prompts = "\n".join(actin_mapping_prompts.SPECIFIC_CATEGORY_PROMPTS[i] for i in cat)
        sel_actin_rules = "\n".join(pd.Series(actin_df[list(cat)].to_numpy().flatten()).dropna().str.strip().tolist())

        system_prompt = actin_mapping_prompts.COMMON_SYSTEM_PROMPTS

        if len(cat) > 1:
            for criterion in criteria_list:
                user_prompt = f"""
## ELIGIBILITY CRITERION
```
{criterion}
```

## CATEGORY ASSIGNMENT
This criterion belongs to the following ACTIN categories:
- {"\n- ".join(cat)}

## RELEVANT ACTIN RULES
The ACTIN rules associated with these categories are:
```
{sel_actin_rules}
```

## TASK
Map the above eligibility criterion to one or more ACTIN rules from the list above. 
Use the guidelines and formatting conventions provided.

### CATEGORY-SPECIFIC MAPPING INSTRUCTIONS
{category_prompts}
"""
                response = client.llm_ask(user_prompt, system_prompt)
                try:
                    result = llm_output_to_rule_obj(response)
                except JSONDecodeError:
                    repair_prompt = f"""Fix the following JSON so it parses correctly. Return only the corrected JSON 
                    object: {response}
"""
                    repaired_response = client.llm_ask(repair_prompt)
                    result = llm_output_to_rule_obj(repaired_response)
                mapped_results.extend(result)

        elif len(cat) == 1:
            joined_criteria = "\n\n".join(criteria_list)
            word_limit_batches = batch_tagged_criteria_by_words(joined_criteria, BATCH_MAX_WORDS)

            for batch in word_limit_batches:
                user_prompt = f"""
## ELIGIBILITY CRITERIA
```
{batch}
```

## CATEGORY ASSIGNMENT
These criteria belong to the ACTIN category:
- {cat[0]}

## RELEVANT ACTIN RULES
The ACTIN rules associated with this category are:
```
{sel_actin_rules}
```

## TASK
Map the above eligibility criterion to one or more ACTIN rules from the list above. 
Use the guidelines and formatting conventions provided.

### CATEGORY-SPECIFIC MAPPING INSTRUCTIONS
{category_prompts}
"""
                response = client.llm_ask(user_prompt, system_prompt)
                try:
                    result = llm_output_to_rule_obj(response)
                except JSONDecodeError:
                    repair_prompt = f"""Fix the following JSON so it parses correctly. Return only the corrected JSON 
                    object: {response}
    """
                    repaired_response = client.llm_ask(repair_prompt)
                    result = llm_output_to_rule_obj(repaired_response)
                mapped_results.extend(result)

    return mapped_results


def actin_mark_new_rules(actin_mappings: list[dict], actin_rules: list[str]) -> list[ActinMapping]:
    actin_rules = set(actin_rules)
    for actin_mapping in actin_mappings:
        actin_mapping['new_rule'] = find_new_actin_rules(actin_mapping['actin_rule'], actin_rules)
    return cast(list[ActinMapping], actin_mappings)


def actin_workflow(eligibility_criteria: str, client: LlmClient, actin_df: pd.DataFrame, actin_categories: list[str]) -> list[ActinMapping]:
    cat_criteria = identify_actin_categories(eligibility_criteria, client, actin_categories)
    sorted_cat_criteria = sort_criteria_by_category(cat_criteria)
    actin_mapping = map_to_actin_by_category(sorted_cat_criteria, client, actin_df)

    actin_rules = (pd.Series(actin_df.to_numpy().flatten()).dropna().str.strip().tolist())
    return actin_mark_new_rules(actin_mapping, actin_rules)


def actin_workflow_by_cohort(eligibility_criteria: str, client: LlmClient, actin_df: pd.DataFrame, actin_categories: list[str]) -> dict[str, list[ActinMapping]]:
    cohort_texts: dict[str, str] = llm_extract_cohort_tagged_text(eligibility_criteria, client)
    logger.info(f"Processing cohorts: {list(cohort_texts.keys())}")

    cohort_actin_outputs: dict[str, list[ActinMapping]] = {}
    for cohort_name, tagged_text in cohort_texts.items():
        cohort_actin_outputs[cohort_name] = actin_workflow(tagged_text, client, actin_df, actin_categories)

    return cohort_actin_outputs


def main():
    parser = argparse.ArgumentParser(description="Clinical trial curator")
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_file', help='output file containing trial data', required=True)
    parser.add_argument('--ACTIN_path', help='Full path to ACTIN rules CSV', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    client = OpenaiClient(TEMPERATURE)
    actin_df, actin_categories = load_actin_resource(args.ACTIN_path)

    trial_data = load_trial_data(args.trial_json)
    eligibility_criteria = load_eligibility_criteria(trial_data)

    actin_outputs = actin_workflow_by_cohort(eligibility_criteria, client, actin_df, actin_categories)

    with open(args.out_trial_file, "w", encoding="utf-8") as f:
        json.dump(actin_outputs, f, indent=2)

    logger.info(f"ACTIN results written to {args.out_trial_file}")


if __name__ == "__main__":
    main()
