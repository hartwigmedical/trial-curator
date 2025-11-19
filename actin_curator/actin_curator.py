import json
import sys

import pandas as pd
import logging
import argparse
from typing import TypedDict, Any
from rapidfuzz import fuzz

from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient

from trialcurator.utils import load_trial_data, load_eligibility_criteria, llm_json_check_and_repair
from trialcurator.eligibility_text_preparation import llm_rules_prep_workflow

from actin_curator import actin_mapping_prompts
from actin_curator.actin_curator_utils import load_actin_resource, flatten_actin_rules, find_new_actin_rules, actin_rule_reformat


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
        raise ValueError(f"Input criterion has been incorrected changed.\nOriginal: {input_rule}\nReturned: {cat_key}")

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
        raise ValueError(f"Expect a list of two dicts. Instead got: {response}")

    return response[0]


def actin_workflow(input_rules: list[dict[str, Any]], client: LlmClient, actin_filepath: str) -> list[ActinMapping]:

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

    # 5. Generate confidence score and explanation
    rules_w_confidence = []
    for criterion in rules_w_new:
        criterion_updated = criterion.copy()

        confidence_fields = actin_mark_confidence_score(criterion, client)
        criterion_updated["confidence_level"] = confidence_fields.get("confidence_level")
        criterion_updated["confidence_explanation"] = confidence_fields.get("confidence_explanation")
        rules_w_confidence.append(criterion_updated)

    actin_output = rules_w_confidence.copy()
    return actin_output


def printable_summary(actin_output: list[ActinMapping], file):
    print(f"====== ACTIN MAPPING SUMMARY ======\n", file=file)

    for index, rule in enumerate(actin_output, start=1):
        input_rule = rule.get("input_rule")
        if input_rule is None:
            raise ValueError("Input rule is missing")

        actin_rule_formatted = rule.get("actin_rule_reformat")
        if actin_rule_formatted is None:
            raise ValueError("Formatted ACTIN rule is missing")

        print(f"Input Rule:\n{input_rule}", file=file)
        print(f"Mapped ACTIN Rule:\n{actin_rule_formatted}\n", file=file)
        print("\n")


def main():
    parser = argparse.ArgumentParser(description="ACTIN trial curator")
    parser.add_argument('--input_file', help='json file containing trial data', required=False)
    parser.add_argument('--input_text_file', help='text file containing eligibility criteria', required=False)
    parser.add_argument('--output_file_complete', help='complete output file from ACTIN curator', required=True)
    parser.add_argument('--output_file_concise', help='human readable output summary file from ACTIN curator (.tsv or .txt recommended)', required=False)
    parser.add_argument('--actin_filepath', help='Full path to ACTIN rules CSV', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    logger.info("\n=== Starting ACTIN curator ===\n")

    client = OpenaiClient()

    if args.input_file:
        if args.input_text_file:
            raise ValueError("--input_file and --input_text_file cannot both be specified")
        trial_data = load_trial_data(args.input_file)
        eligibility_criteria = load_eligibility_criteria(trial_data)
    elif args.input_text_file:
        with open(args.input_text_file, 'r') as f:
            eligibility_criteria = f.read()
    else:
        raise ValueError("Either --input_file or --input_text_file must be specified")
    logger.info(f"Loaded {len(eligibility_criteria)} eligibility criteria")

    # Text preparation workflow
    processed_rules = llm_rules_prep_workflow(eligibility_criteria, client)

    # ACTIN curator workflow
    actin_outputs = actin_workflow(processed_rules, client, args.actin_filepath)

    with open(args.output_file_complete, "w", encoding="utf-8") as f:
        json.dump(actin_outputs, f, indent=2)
    logger.info(f"Complete ACTIN results written to {args.output_file_complete}")

    if args.output_file_concise:
        with open(args.output_file_concise, "w", encoding="utf-8") as f:
            printable_summary(actin_outputs, f)  # write to file
        printable_summary(actin_outputs, sys.stdout)  # display on screen
        logger.info(f"Human readable ACTIN summary results written to {args.output_file_concise}")


if __name__ == "__main__":
    main()
