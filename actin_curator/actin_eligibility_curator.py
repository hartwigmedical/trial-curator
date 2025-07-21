import json
import pandas as pd
import logging
import argparse
from typing import TypedDict, Any
from rapidfuzz import fuzz

from actin_curator.actin_curator_utils import parse_llm_mapping_output, find_new_actin_rules, output_formatting
from trialcurator.gemini_client import GeminiClient
from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient
from trialcurator.utils import load_trial_data, load_eligibility_criteria, batch_tagged_criteria_by_words, \
    llm_json_check_and_repair
from trialcurator.eligibility_sanitiser import llm_rules_prep_workflow
from actin_curator import actin_mapping_prompts

logger = logging.getLogger(__name__)

TEMPERATURE = 0.0
BATCH_MAX_WORDS = 30
RULE_SIMILARITY_THRESHOLD = 95


class ActinMapping(TypedDict, total=False):
    description: str
    cohort: list[str]
    exclude: bool
    flipped: bool
    actin_rule: dict[str, list | dict] | str
    actin_category: list[str]
    new_rule: list[str]
    confidence_level: float
    confidence_explanation: str


def load_actin_resource(filepath: str) -> tuple[pd.DataFrame, list[str]]:
    actin_df = pd.read_csv(filepath, header=0)
    actin_categories = actin_df.columns.str.strip().tolist()
    return actin_df, actin_categories


def identify_actin_categories(eligibility_criteria: str, client: LlmClient, actin_categories: list[str]) -> list[dict[str, list[str]]]:
    logger.info("\nSTART ACTIN CATEGORISATION\n")

    category_str = "\n".join(f"- {cat}" for cat in actin_categories)

    logger.info(f"Classifying ```{eligibility_criteria}``` into ACTIN categories.")

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

{eligibility_criteria}
"""

    response_init = client.llm_ask(user_prompt, system_prompt)
    response = llm_json_check_and_repair(response_init, client)

    if not isinstance(response, list) or len(response) > 1:
        raise ValueError(
            f"Should return a single JSON object for criterion. Instead returned {len(response)} rules:\n{response}")

    cat_key = next(iter(response[0]))
    if fuzz.ratio(cat_key, eligibility_criteria) < RULE_SIMILARITY_THRESHOLD:
        raise ValueError(f"Input criterion has been incorrected changed.\nOriginal: {eligibility_criteria}\nReturned: {cat_key}")

    return response


def sort_criteria_by_category(cat_criteria: dict[str, list[str]]) -> dict[tuple[str, ...], list[str]]:
    sorted_cat_criteria = {}

    for criterion, cat in cat_criteria.items():
        cat = tuple(cat)

        if cat not in sorted_cat_criteria:
            sorted_cat_criteria[cat] = []

        sorted_cat_criteria[cat].append(criterion)

    return sorted_cat_criteria


def map_to_actin_by_category(sorted_cat_criteria: dict, client: LlmClient, actin_df: pd.DataFrame) -> list[
    ActinMapping]:
    mapped_results = []
    logger.info(
        f"Mapping {sum(len(c) for c in sorted_cat_criteria.values())} criteria across {len(sorted_cat_criteria)} category groups.")

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
                mapped_results.extend(llm_json_repair(response, client, parse_llm_mapping_output))

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
                mapped_results.extend(llm_json_repair(response, client, parse_llm_mapping_output))
    return mapped_results


def actin_mark_new_rules(actin_mappings: list[ActinMapping], actin_rules: list[str]) -> list[ActinMapping]:
    actin_rules = set(actin_rules)
    for mapping in actin_mappings:
        mapping['new_rule'] = find_new_actin_rules(mapping['actin_rule'], actin_rules)
    return actin_mappings


def actin_mark_confidence_score(actin_mappings: list[ActinMapping], client: LlmClient) -> list[ActinMapping]:
    final_results = []
    logger.info(f"Evaluate confidence level per mapping")

    for mapping in actin_mappings:
        system_prompt = f"""
## ROLE
You are a clinical trial curation evaluator for a system called ACTIN, which determines available 
treatment options for cancer patients.

## TASK
Given one eligibility criterion and its mapped ACTIN rule(s), evaluate your confidence in the mapping.

Provide:
- `confidence_level`: A floating-point number between 0.0 and 1.0 indicating your confidence that the mapping is correct.
- `confidence_explanation`: A brief, precise explanation (1–3 sentences) of the rationale behind your score.

## SCORING GUIDANCE
- 1.0 → High certainty the mapping is correct.
- 0.0 → Confident the mapping is incorrect.
- Values between 0.0 and 1.0 → Reflect uncertainty, ambiguity, or partial correctness.

Factors that reduce confidence include:
- Mapping to a newly invented ACTIN rule.
- The presence of multiple logical operators (AND, OR, NOT) or nested logic.
- The criterion is an exclusion clause (EXCLUDE), which is often harder to interpret.
- The semantic match between the criterion and the rule is weak, vague, or indirect.

## OUTPUT
Return a valid JSON object containing the full original mapping plus the two new fields:

{{
  "confidence_level": <float>,
  "confidence_explanation": "<str>"
}}
"""

        user_prompt = f"""
Evaluate the confidence of the following ACTIN rule mapping:

{json.dumps(mapping, indent=2)}

Return only a valid JSON object with the added `confidence_level` and `confidence_explanation` fields.
"""

        response = client.llm_ask(user_prompt, system_prompt)
        final_results.extend(llm_json_repair(response, client, parse_llm_mapping_output))
    return final_results


def actin_reformat_output(actin_mappings: list[ActinMapping]) -> list[ActinMapping]:
    for mapping in actin_mappings:
        mapping['actin_rule'] = output_formatting(mapping['actin_rule'])
    return actin_mappings


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


def human_readable_output(output, txt_path):
    with open(txt_path, 'w', encoding='utf-8') as f:
        for cohort_name, cohort_mappings in output.items():
            f.write(f"COHORT: {cohort_name}\n\n")
            for field in cohort_mappings:
                f.write("Description:\n" + field["description"] + "\n")
                f.write("ACTIN rule:\n" + field["actin_rule"] + "\n")
                f.write("Confidence level:\n" + str(field["confidence_level"]) + "\n")
                f.write("Explanation:\n" + field["confidence_explanation"] + "\n")
                f.write("--" * 100 + "\n")


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

    txt_path = args.out_trial_file.replace(".json", "_humanReadable.txt")
    human_readable_output(actin_outputs, txt_path)

    logger.info(f"ACTIN results written to {args.out_trial_file}")


if __name__ == "__main__":
    main()
