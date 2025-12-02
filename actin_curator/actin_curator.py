import json
import sys
from pathlib import Path

import pandas as pd
import logging
import argparse
from typing import TypedDict, Any
from rapidfuzz import fuzz

from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient

from trialcurator.utils import load_trial_data, load_eligibility_criteria, llm_json_check_and_repair
from trialcurator.eligibility_text_preparation import llm_rules_prep_workflow, llm_rules_prep_workflow_grouped_w_original_statements

from . import actin_mapping_prompts
from .actin_curator_utils import load_actin_resource, flatten_actin_rules, find_new_actin_rules, actin_rule_reformat


logger = logging.getLogger(__name__)

RULE_SIMILARITY_THRESHOLD = 95  # To only allow for punctuation differences - most commonly the presence or absence of a full stop.


class ActinMapping(TypedDict, total=False):
    input_rule: str
    exclude: bool
    flipped: bool
    cohort: list[str]
    actin_category: list[str]
    actin_rule: dict[str, list | dict] | str
    actin_rule_reformat: str
    new_rule: list[str]
    confidence_level: float
    confidence_explanation: str
    # Parent-level metadata from grouped workflow
    original_input_rule: str
    original_input_rule_id: str
    section: str


def identify_actin_categories(input_rule: str, client: LlmClient, actin_categories: list[str]) -> list[dict[str, Any]]:
    logger.info("\nSTART ACTIN CATEGORISATION\n")

    category_str = "\n".join(f"- {cat}" for cat in actin_categories)

    logger.info(f"Classifying ```{input_rule}``` into ACTIN categories.")

    intro_prompt = f"""
## ROLE
You are a clinical trial curation assistant for a system called ACTIN, which determines available treatment options for cancer patients.

## TASK
Classify each eligibility criterion into one or more ACTIN categories.

- Most criteria should be assigned to a single, most relevant category.
- Assign multiple categories **only** when the criterion clearly describes **clinically distinct concepts** that each map to different categories.
- If a criterion includes bullet points, newlines, or multiple clauses, treat it as a **single atomic unit**. Do not split or paraphrase it.
- Do **not** assign a category based on a term appearing in the text unless the clinical meaning directly aligns with that category.
  Example:
  - A criterion mentioning “untreated CNS metastases” should typically fall under **Medical_History_and_Comorbidities**, not **Cancer_Type_and_Tumor_Site_Localization**, unless tumor classification is explicitly discussed.

## ACTIN CATEGORIES
The following categories are available:
{category_str}

"""
    json_example = """
## OUTPUT FORMAT
- Return a valid JSON object. Do not include any extra text.
- The input criterion must remain **unchanged**.

### Example 1:
```json
[
    { 
        "Histologically or cytologically confirmed metastatic CRPC": ["Cancer_Type_and_Tumor_Site_Localization"]
    }
]
```
### Example 2:
```json
[
    {
        "Known HIV, active Hepatitis B without receiving antiviral treatment": ["Infectious_Disease_History_and_Status"]
    }
]
```

"""
    system_prompt = intro_prompt + json_example

    user_prompt = f"""
Classify the following eligibility criterion:
- Do not split or paraphrase it, even if it contains line breaks or bullet points.
- Return exactly one JSON object for each input string, using the **original string as-is** as the JSON key.

Input:

{input_rule}
"""

    response_init = client.llm_ask(user_prompt, system_prompt)
    response = llm_json_check_and_repair(response_init, client)

    if not isinstance(response, list) or len(response) > 1:
        raise TypeError(f"Should return a single JSON object for criterion. Instead returned {len(response)} rules:\n{response}")

    # Check the LLM has not erroneously altered the eligibility rule text
    cat_key = next(iter(response[0]))  # The category key is the rule itself
    if fuzz.ratio(cat_key, input_rule) < RULE_SIMILARITY_THRESHOLD:
        raise ValueError(
            f"Input criterion has been incorrectly changed.\n"
            f"Original: {input_rule}\nReturned: {cat_key}"
        )

    return response


def map_to_actin_rules(criteria_dict: dict, client: LlmClient, actin_df: pd.DataFrame) -> dict[str, Any]:
    logger.info("\nSTART ACTIN RULES MAPPING\n")

    system_prompt = actin_mapping_prompts.COMMON_SYSTEM_PROMPTS

    exclusion = criteria_dict.get("exclude")
    if not isinstance(exclusion, bool):
        raise TypeError("Exclusion flag is not a boolean.")

    rule = criteria_dict.get("input_rule")
    if not isinstance(rule, str):
        raise TypeError("Eligibility rule is not a string.")

    if exclusion:
        criterion = "EXCLUDE " + rule
    else:
        criterion = "INCLUDE " + rule

    category = criteria_dict.get("actin_category")
    if not isinstance(category, list):
        raise ValueError("ACTIN category is not a list of strings.")

    category_prompts = ""
    sel_actin_rules = ""
    user_prompt = ""

    for cat in category:

        if cat not in actin_df.columns:
            raise ValueError(f"Category '{cat}' is not found in actin_df columns")

        temp_prompt = actin_mapping_prompts.SPECIFIC_CATEGORY_PROMPTS.get(cat)
        if temp_prompt is None:
            raise ValueError(f"No category-specific prompts found for {cat}")

        category_prompts += temp_prompt + "\n"

        temp_rules = actin_df[cat].dropna().astype(str).str.strip().tolist()
        sel_actin_rules += "\n".join(temp_rules) + "\n"

        user_prompt = f"""
## ELIGIBILITY CRITERIA
```
{criterion}
```

## CATEGORY ASSIGNMENT
These criteria belong to the ACTIN category:
- {cat}

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
    return llm_json_check_and_repair(response, client)


def actin_mark_new_rules(actin_rule: dict | list | str, actin_df: pd.DataFrame) -> list[str]:
    logger.info("\nSTART IDENTIFYING NEWLY INVENTED ACTIN RULES\n")

    actin_rules = flatten_actin_rules(actin_df)
    return find_new_actin_rules(actin_rule, actin_rules)


def actin_mark_confidence_score(criteria_dict: ActinMapping, client: LlmClient) -> dict[str, Any]:
    logger.info("\nSTART GENERATING ACTIN MAPPING CONFIDENCE SCORE\n")

    system_prompt = """
## ROLE
You are a clinical trial curation evaluator for a system called ACTIN, which determines available treatment options for cancer patients.

## TASK
Given the eligibility criterion block, evaluate your confidence in the mapped 'actin_rule' dictionary.

Provide:
- `confidence_level`: A floating-point number between 0.0 and 1.0 indicating your confidence that the mapping is correct.
- `confidence_explanation`: A brief, precise explanation (1–3 sentences) of the rationale behind your score.

## SCORING GUIDANCE
- 1.0 → High certainty the mapping is correct.
- 0.0 → Confident the mapping is incorrect.
- Values between 0.0 and 1.0 → Reflect uncertainty, ambiguity, or partial correctness.

## EVALUATION GUIDANCE
- Compare the semantic similarity between `input_rule` against `actin_rule`.
    - The greater the degree of similarity, the higher the score.
    - Conversely, if the semantic match between the criterion and the rule is weak, vague, or indirect, the resultant confidence score should be lower.
- Factor in the boolean values in `exclude`:
    - An exclusion clause (`exclude`: true) can be harder to interpret.
    - All else being equal, `exclude`: true should have a lower confidence score than `exclude`: false
- Factor in the boolean values in `flipped`:
    - Flipping the logic from an exclusion clause to an inclusion clause may introduce errors.
    - All else being equal, `flipped`: true should have a lower confidence score than `flipped`: false
- Factor in the values in `new_rule`:
    - All else being equal, create new rule(s) should have a lower confidence score than using existing rule(s)
- Factor in the presence of multiple logical operators (AND, OR, NOT) or nested logic.
    - Greater complexity may introduce errors and could result in a lower confidence score.

## OUTPUT
Return a valid JSON object containing the two new fields:

```json
[
    {
        "confidence_level": <float>,
        "confidence_explanation": "<str>"
    }
]
```
"""

    user_prompt = f"""
Evaluate the confidence of the following eligibility criterion block with ACTIN rule mapping:
```
{criteria_dict}
```

Return only a valid JSON object with the added `confidence_level` and `confidence_explanation` fields.
"""
    response_init = client.llm_ask(user_prompt, system_prompt)
    response = llm_json_check_and_repair(response_init, client)

    if not isinstance(response, list) or len(response) != 1:
        raise ValueError(f"Expect a list of dicts. Instead got: {response}")

    return response[0]


def flatten_grouped_rules(grouped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat = []

    for parent in grouped:
        for c in parent.get("curations", []):
            c_copy = c.copy()

            # Propagate all parent-level attributes
            c_copy["original_input_rule"] = parent["original_input_rule"]
            c_copy["original_input_rule_id"] = parent["original_input_rule_id"]
            c_copy["section"] = parent["section"]

            flat.append(c_copy)
    return flat


def group_actin_by_parent(parents: list[dict[str, Any]], flat_actin: list[ActinMapping]) -> list[dict[str, Any]]:
    children_by_id: dict[str, list[ActinMapping]] = {}

    for child in flat_actin:
        pid = child.get("original_input_rule_id")
        if pid is None:
            continue
        children_by_id.setdefault(pid, []).append(child)

    grouped_output: list[dict[str, Any]] = []

    for parent in parents:
        pid = parent["original_input_rule_id"]
        grouped_output.append(
            {
                "original_input_rule": parent["original_input_rule"],
                "original_input_rule_id": pid,
                "section": parent["section"],
                "curations": children_by_id.get(pid, []), # May be empty list – this preserves permissive / dropped parent-level statements
            }
        )

    return grouped_output


def actin_workflow(input_rules: list[dict[str, Any]], client: LlmClient, actin_filepath: str, confidence_estimate: bool) -> list[ActinMapping]:
    actin_df, actin_cat = load_actin_resource(actin_filepath)

    # 1. Assign ACTIN category
    rules_w_cat = []
    for criterion in input_rules:
        criterion_updated = criterion.copy()

        input_rule = criterion.get("input_rule")
        if input_rule is None:
            raise TypeError(f"Eligibility rule missing in {criterion}")

        matched_actin_cat_list = identify_actin_categories(input_rule, client, actin_cat)
        matched_actin_cat_dict = matched_actin_cat_list[0]
        criterion_updated["actin_category"] = next(iter(matched_actin_cat_dict.values()))
        rules_w_cat.append(criterion_updated)

    # 2. Map to ACTIN rules
    rules_w_mapping = []
    for criterion in rules_w_cat:
        criterion_updated = criterion.copy()

        mapped_rules = map_to_actin_rules(criterion, client, actin_df)
        if isinstance(mapped_rules, list):
            criterion_updated["input_rule"] = mapped_rules[0].get("input_rule")  # Update the input_rule to have the 'prefix' of INCLUDE or EXCLUDE

        for rule in mapped_rules:
            if isinstance(rule, dict):
                criterion_updated["actin_rule"] = rule.get("actin_rule")
            elif isinstance(rule, str):
                criterion_updated["actin_rule"] = rule
            else:
                raise TypeError(f"Unexpected format in mapped_rules: {rule}")
        rules_w_mapping.append(criterion_updated)

    # 3. Reformat ACTIN rules
    rules_reformat = []
    for criterion in rules_w_mapping:
        criterion_updated = criterion.copy()

        actin_rule = criterion.get("actin_rule")
        actin_rule_reformatted = actin_rule_reformat(actin_rule)
        criterion_updated["actin_rule_reformat"] = actin_rule_reformatted
        rules_reformat.append(criterion_updated)

    # 4. Mark new rules
    rules_w_new = []
    for criterion in rules_reformat:
        criterion_updated = criterion.copy()

        actin_rule = criterion.get("actin_rule")
        new_rules = actin_mark_new_rules(actin_rule, actin_df)

        if len(new_rules) > 0:
            criterion_updated["new_rule"] = new_rules
        rules_w_new.append(criterion_updated)

    # 5. Generate confidence score and explanation - optional
    if confidence_estimate:
        rules_w_confidence = []
        for criterion in rules_w_new:
            criterion_updated = criterion.copy()

            confidence_fields = actin_mark_confidence_score(criterion, client)
            criterion_updated["confidence_level"] = confidence_fields.get("confidence_level")
            criterion_updated["confidence_explanation"] = confidence_fields.get("confidence_explanation")
            rules_w_confidence.append(criterion_updated)

        actin_output = rules_w_confidence.copy()
    else:
        actin_output = rules_w_new.copy()

    return actin_output


def printable_summary_flat(actin_output: list[ActinMapping], file):
    print(f"====== ACTIN MAPPING SUMMARY (flat) ======\n", file=file)

    for index, rule in enumerate(actin_output, start=1):
        input_rule = rule.get("input_rule")
        if input_rule is None:
            raise ValueError("Input rule is missing")

        actin_rule_formatted = rule.get("actin_rule_reformat")
        if actin_rule_formatted is None:
            raise ValueError("Formatted ACTIN rule is missing")

        parent_id = rule.get("original_input_rule_id")
        section = rule.get("section")
        original_parent = rule.get("original_input_rule")

        if parent_id or original_parent:
            print(f"--- Parent [{parent_id}] ---", file=file)
            if section is not None:
                print(f"Section: {section}", file=file)
            if original_parent:
                print("Original parent-level text:", file=file)
                print(original_parent, file=file)
            print("", file=file)

        print(f"Input Rule:", file=file)
        print(input_rule, file=file)
        print(f"Mapped ACTIN Rule:", file=file)
        print(f"{actin_rule_formatted}\n", file=file)
        print("\n", file=file)


def printable_summary_grouped(grouped_output: list[dict[str, Any]], file):
    print(f"====== ACTIN MAPPING SUMMARY (grouped by parent statement) ======\n", file=file)

    for parent in grouped_output:
        original_parent = parent.get("original_input_rule")
        parent_id = parent.get("original_input_rule_id")
        section = parent.get("section")
        curations = parent.get("curations", []) or []

        print(f"=== Parent Statement [{parent_id}] ===", file=file)
        if section is not None:
            print(f"Section: {section}", file=file)
        if original_parent:
            print("\nOriginal parent-level text:", file=file)
            print(original_parent, file=file)
        print("", file=file)

        if not curations:
            print("  (No curated ACTIN rules for this statement.)\n", file=file)
            continue

        for idx, rule in enumerate(curations, start=1):
            input_rule = rule.get("input_rule")
            actin_rule_formatted = rule.get("actin_rule_reformat")

            if input_rule is None:
                raise ValueError(f"Input rule is missing for child #{idx} under parent {parent_id}")
            if actin_rule_formatted is None:
                raise ValueError(f"Formatted ACTIN rule is missing for child #{idx} under parent {parent_id}")

            print(f"  --- Child rule #{idx} ---", file=file)
            print(f"  Input Rule:", file=file)
            print(f"  {input_rule}", file=file)
            print(f"  Mapped ACTIN Rule:", file=file)
            print(f"  {actin_rule_formatted}\n", file=file)

        print("\n", file=file)


REDUNDANT_ATTRIBUTES = ("original_input_rule", "original_input_rule_id")


def strip_redundant_curation_fields(grouped_output: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for parent in grouped_output:
        curations = parent.get("curations", [])

        for cur in curations:
            for key in REDUNDANT_ATTRIBUTES:
                cur.pop(key, None)

    return grouped_output


def main():
    parser = argparse.ArgumentParser(description="ACTIN trial curator")

    input_file = parser.add_mutually_exclusive_group(required=True)
    input_file.add_argument("--input_json", type=Path, help="Downloaded json file from ClinicalTrial.gov")
    input_file.add_argument("--input_txt", type=Path, help="Text file of trial protocol")

    parser.add_argument("--actin_filepath", help='Full path to ACTIN rules CSV', required=True)

    parser.add_argument("--output_complete", help="Complete output file from ACTIN curator", required=False)
    parser.add_argument("--output_concise", help="Human readable output summary file from ACTIN curator (.tsv or .txt recommended)", required=False)

    parser.add_argument("--group_by_original_statement", help="Group curated rules under their original parent-level statements", action="store_true", required=False)
    parser.add_argument("--confidence_estimate", help="Flag to specify whether confidence level estimation of curation is required", action="store_true", required=False)

    parser.add_argument("--log_level", help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    logger.info("\n=== Starting ACTIN curator ===\n")

    client = OpenaiClient()

    if args.input_json is not None:
        trial_data = load_trial_data(args.input_json)
        eligibility_criteria = load_eligibility_criteria(trial_data)
    elif args.input_txt is not None:
        with open(args.input_txt, 'r', encoding="utf-8") as f:
            eligibility_criteria = f.read()
    else:
        raise ValueError("Either --input_json or --input_txt must be specified")
    logger.info("Loading eligibility criteria")

    # Text preparation workflow
    grouped_parents: list[dict[str, Any]] | None = None

    if args.group_by_original_statement:
        logger.info("Using grouped text preparation workflow with original parent-level statements")
        grouped_parents = llm_rules_prep_workflow_grouped_w_original_statements(eligibility_criteria, client)
        processed_rules = flatten_grouped_rules(grouped_parents)
    else:
        logger.info("Using standard text preparation workflow")
        processed_rules = llm_rules_prep_workflow(eligibility_criteria, client)

    # ACTIN curator workflow
    actin_outputs_flat = actin_workflow(processed_rules, client, args.actin_filepath, confidence_estimate=args.confidence_estimate)

    # If grouped mode, rebuild grouped output structure
    grouped_output: list[dict[str, Any]] | None = None
    if args.group_by_original_statement and grouped_parents is not None:
        grouped_output = group_actin_by_parent(grouped_parents, actin_outputs_flat)

    # Write outputs
    if args.output_complete:
        with open(args.output_complete, "w", encoding="utf-8") as f:
            if grouped_output is not None:
                cleaned = strip_redundant_curation_fields(grouped_output)
                json.dump(cleaned, f, indent=2)
            else:
                json.dump(actin_outputs_flat, f, indent=2)
        logger.info(f"Complete ACTIN results written to {args.output_complete}")

    if args.output_concise:
        with open(args.output_concise, "w", encoding="utf-8") as f:
            if grouped_output is not None:
                printable_summary_grouped(grouped_output, f)
            else:
                printable_summary_flat(actin_outputs_flat, f)

        # Also print to stdout
        if grouped_output is not None:
            printable_summary_grouped(grouped_output, sys.stdout)
        else:
            printable_summary_flat(actin_outputs_flat, sys.stdout)

        logger.info(f"Human readable ACTIN summary results written to {args.output_concise}")


if __name__ == "__main__":
    main()
