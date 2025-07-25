import json
import pandas as pd
import logging
import argparse
from typing import TypedDict, Any
from rapidfuzz import fuzz

from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient
from trialcurator.gemini_client import GeminiClient

from trialcurator.utils import load_trial_data, load_eligibility_criteria, llm_json_check_and_repair
from trialcurator.eligibility_text_preparation import llm_rules_prep_workflow

from actin_curator import actin_mapping_prompts
from actin_curator.actin_curator_utils import load_actin_resource, flatten_actin_rules, find_new_actin_rules

logger = logging.getLogger(__name__)

TEMPERATURE = 0.0
RULE_SIMILARITY_THRESHOLD = 95  # To only allow for punctuation differences - most commonly the presence of a full stop or otherwise.


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


def identify_actin_categories(input_rule: str, client: LlmClient, actin_categories: list[str]) -> dict[str, list[str]]:
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


def actin_rule_reformat(actin_rule: dict | list | str, level: int = 0) -> str:
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
                    reformatted_container.append(actin_rule_reformat(item, level + 1))
                joined_items = f",\n{next_indent}".join(reformatted_container)
                return f"{key}\n{indent}(\n{next_indent}" + joined_items + f"\n{indent})"

            elif key == "NOT":
                # recurse here
                reformatted_rule = actin_rule_reformat(val, level+1)
                return f"{key}\n{indent}(\n{next_indent}{reformatted_rule}\n{indent})"

            else:
                if isinstance(val, dict):
                    # recurse further into dict
                    reformatted_rule = actin_rule_reformat(val, level+1)
                    return f"{key}\n{indent}(\n{next_indent}{reformatted_rule}\n{indent})"

                elif isinstance(val, list):
                    has_nesting = False
                    for item in val:
                        if isinstance(item, (dict, list)):
                            has_nesting = True
                            break
                    if has_nesting:  # recurse deeper due to nesting
                        # recurse here
                        reformatted_rule = actin_rule_reformat(val, level + 1)
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


def actin_workflow(eligibility_criteria_block: list[dict[str, Any]], client: LlmClient, actin_df: pd.DataFrame,
                   actin_categories: list[str]) -> list[ActinMapping]:
    actin_rules = (pd.Series(actin_df.to_numpy().flatten()).dropna().str.strip().tolist())

    rules_list = []
    for criterion in eligibility_criteria_block:
        for key, val in criterion.items():
            if key == "rule":
                rules_list.append(val)

    for rule in rules_list:
        cat_criteria = identify_actin_categories(rule, client, actin_categories)

    sorted_cat_criteria = sort_criteria_by_category(cat_criteria)

    actin_mapping = map_to_actin_by_category(sorted_cat_criteria, client, actin_df)
    actin_mapping = actin_mark_new_rules(actin_mapping, actin_rules)
    actin_mapping = actin_mark_confidence_score(actin_mapping, client)
    actin_mapping = actin_reformat_output(actin_mapping)

    return actin_mapping


def main():
    parser = argparse.ArgumentParser(description="ACTIN trial curator")
    parser.add_argument('--LLM_provider', help="Select OpenAI or Google", default="OpenAI")
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_file', help='output file containing trial data', required=True)
    parser.add_argument('--ACTIN_path', help='Full path to ACTIN rules CSV', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    if args.LLM_provider == "OpenAI":
        client = OpenaiClient(TEMPERATURE)
    else:
        client = GeminiClient(TEMPERATURE)
    actin_df, actin_categories = load_actin_resource(args.ACTIN_path)

    trial_data = load_trial_data(args.trial_json)
    eligibility_criteria = load_eligibility_criteria(trial_data)

    processed_eligibility_criteria = llm_rules_prep_workflow(eligibility_criteria,
                                                             client)  # returns list[dict[str, Any]]

    actin_outputs = actin_workflow(sanitised_rules, client, actin_df, actin_categories)

    with open(args.out_trial_file, "w", encoding="utf-8") as f:
        json.dump(actin_outputs, f, indent=2)

    logger.info(f"ACTIN results written to {args.out_trial_file}")


if __name__ == "__main__":
    main()
