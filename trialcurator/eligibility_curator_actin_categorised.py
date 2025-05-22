import json
from json import JSONDecodeError
import pandas as pd
import logging
import argparse
from typing import TypedDict

from trialcurator.actin_curator_utils import llm_output_to_rule_obj
from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient
from trialcurator.utils import load_trial_data, load_eligibility_criteria
from trialcurator.eligibility_sanitiser import llm_extract_cohort_tagged_text
from trialcurator.eligibility_curator_actin import actin_mark_new_rules

logger = logging.getLogger(__name__)

TEMPERATURE = 0.0


class ActinMapping(TypedDict):
    description: str
    actin_rule: dict[str, list | dict]
    new_rule: list[str]


def load_actin_file(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(file_path, header=0)
    df.columns = df.columns.str.strip()
    return df


def identify_actin_categories(input_eligibility_criteria: str, client: LlmClient, actin_file: pd.DataFrame) -> dict:
    categories_list = actin_file.columns.tolist()

    system_prompt = f"""
You are an assistant that classifies eligibility criteria into relevant ACTIN categories.
Each criterion belongs to one or more categories.

ACTIN categories:
{categories_list}

INSTRUCTIONS:
- Return a single JSON object
- Each eligibility criterion should be a key
- The value should be a list of matched ACTIN categories
- Do NOT include any text outside the JSON

Example:
{{
    "INCLUDE Histologically or cytologically confirmed metastatic CRPC": ["cancer_type_and_tumor_and_lesion_localization"],
    "EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment": ["infections"]
}}
"""

    user_prompt = f"""
Classify the following eligibility criterion:
\"\"\"
{input_eligibility_criteria}
\"\"\"
"""
    eligibility_criteria_w_category = client.llm_ask(user_prompt, system_prompt)
    # print(eligibility_criteria_w_category)

    try:
        eligibility_criteria_w_category_obj = llm_output_to_rule_obj(eligibility_criteria_w_category)
    except JSONDecodeError:
        user_prompt = f"""Fix up the following JSON:
{eligibility_criteria_w_category}
Return answer in a ```json code block```.
"""
        eligibility_criteria_w_category = client.llm_ask(user_prompt)
        eligibility_criteria_w_category_obj = llm_output_to_rule_obj(eligibility_criteria_w_category)

    return eligibility_criteria_w_category_obj


def sort_criteria_by_category(eligibility_criteria_w_category_obj: dict) -> dict:
    sel_categories_single = {}
    sel_categories_grouped = {}

    for key, val in eligibility_criteria_w_category_obj.items():
        val = tuple(sorted(val))

        if len(val) > 1:
            if val not in sel_categories_single.keys():
                sel_categories_single[val] = [key]
            else:
                sel_categories_single[val].append(key)
        else:
            if val not in sel_categories_grouped.keys():
                sel_categories_grouped[val] = [key]
            else:
                sel_categories_grouped[val].append(key)

    eligibility_criteria_w_category_obj_sorted = sel_categories_grouped.copy()
    eligibility_criteria_w_category_obj_sorted.update(sel_categories_single)
    return eligibility_criteria_w_category_obj_sorted


def map_to_actin_categorised(categorised_eligibility_criteria: dict, client: LlmClient, actin_file: pd.DataFrame) -> list[dict]:

    results = []

    for key, val in categorised_eligibility_criteria.items():
        sel_actin_rules = "\n".join(pd.Series(actin_file[list(key)].to_numpy().flatten()).dropna().str.strip().tolist())
        eligibility_criteria = "\n".join(val)

        system_prompt = """
You are a clinical trial curation assistant for a system called ACTIN.
Your task is to convert each free-text eligibility criterion into structured ACTIN rules.

## Input format

- Each line starting with `INCLUDE` or `EXCLUDE` begins a new **eligibility block**.
- All **indented or bullet-point lines** underneath belong to that block.
- Treat the **entire block (header + sub-points)** as a **single criterion**.

### Example:
INCLUDE Adequate bone marrow function:
  - ANC ≥ 1.5 x 10^9/L
  - Platelet count ≥ 100 x 10^9/L

## ACTIN rule structure

- ACTIN rules are defined with a rule name, 
    E.g. HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y_WITHIN_Z_WEEKS, where X, Y, Z are placeholders for parameters. 
- Parameters are based on placeholders:
  - RULE → no param: `[]`
  - RULE_X → one param: `["value"]`
  - RULE_X_Y_Z → three params: `["v1", "v2", 5]`

## MAIN RULE MATCHING INSTRUCTIONS

- Match each eligibility block into one or more ACTIN rules from the ACTIN RULES LIST.
    - Match by **rule name pattern**, not exact text.
- Accept biologically equivalent terms (e.g. “fusion” = “rearrangement”).
- Prefer general rules unless specificity is required.
- Only create a new rule if there is truly no match

## Fallback rules

Use if no exact rule match applies:

| Scenario              | Rule                                     |
|-----------------------|------------------------------------------|
| Treatment line        | `IS_ELIGIBLE_FOR_TREATMENT_LINE_X[...]`  |
| Gene rearrangement    | `FUSION_IN_GENE_X[...]`                  |
| Comorbidity           | `NOT(HAS_SEVERE_CONCOMITANT_CONDITION)`  |
| Disease history       | `HAS_HISTORY_OF_CARDIOVASCULAR_DISEASE`  |

## Logical operators

| Operator | Format                         | Meaning                                                   |
|----------|--------------------------------|-----------------------------------------------------------|
| `AND`    | `{ "AND": [rule1, rule2] }`    | All conditions required                                   |
| `OR`     | `{ "OR": [rule1, rule2] }`     | When the text offers **alternative acceptable options**   |
| `NOT`    | `{ "NOT": rule }`              | Negate a single rule                                      |

## Numerical comparison logic

| Text  | Rule Format                               |
|-------|-------------------------------------------|
| ≥ X   | `SOMETHING_IS_AT_LEAST_X[...]`            |
| > X   | `SOMETHING_IS_AT_LEAST_X[...]` (adjusted) |
| ≤ X   | `SOMETHING_IS_AT_MOST_X[...]`             |
| < X   | `SOMETHING_IS_AT_MOST_X[...]` (adjusted)  |

## Exclusion logic
- For every EXCLUDE line, the **entire logical condition** must be wrapped in a single `NOT`, unless the matched ACTIN \
rule is already negative in meaning (e.g. `HAS_NOT`, `IS_NOT`)

## Output format
Output an JSON array of objects representing the criteria. Example:
```json
[
    {
        "description": "EXCLUDE Body weight over 150 kg",
        "actin_rule": { "NOT": { "HAS_BODY_WEIGHT_OF_AT_LEAST_X": [150] } },
    },
    {
        "description": "INCLUDE Eligible for systemic treatment with capecitabine + anti-VEGF antibody",
        "actin_rule": { "IS_ELIGIBLE_FOR_TREATMENT_LINE_X": ["capecitabine", "anti-VEGF antibody"] },
    }
    {
        "description": "INCLUDE Is female",
        "actin_rule": { "IS_FEMALE": [] },
    }
]
```

## General guidance/Reminders

- Capture full clinical and logical meaning.
- Do not paraphrase or omit relevant details.
"""

        user_prompt = f"""
# ACTIN RULES for category {list(key)}:
```
{sel_actin_rules}
```

# ELIGIBILITY CRITERIA:
```
{eligibility_criteria}
```
"""

        mapped_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)

        try:
            rule_obj = llm_output_to_rule_obj(mapped_eligibility_criteria)
        except JSONDecodeError:
            user_prompt = f"""Fix up the following JSON:
{mapped_eligibility_criteria}
Return answer in a ```json code block```.
"""
            mapped_eligibility_criteria = client.llm_ask(user_prompt)
            rule_obj = llm_output_to_rule_obj(mapped_eligibility_criteria)

        results.extend(rule_obj)
    return results


def actin_workflow(eligibility_criteria: str, client: LlmClient, actin_file: pd.DataFrame) -> list[ActinMapping]:
    eligibility_criteria_w_category = identify_actin_categories(eligibility_criteria, client, actin_file)
    eligibility_criteria_w_category = sort_criteria_by_category(eligibility_criteria_w_category)
    actin_mapping = map_to_actin_categorised(eligibility_criteria_w_category, client, actin_file)

    actin_rules = (
        pd.Series(actin_file.to_numpy().flatten()).dropna().str.strip().tolist()
    )

    cancer_rules = [r for r in actin_rules if "CANCER_TYPE_X" in r]
    print("Cancer rules found:", cancer_rules)
    print("Number of cancer rules:", len(cancer_rules))

    return actin_mark_new_rules(actin_mapping, actin_rules)


def actin_map_by_cohort(eligibility_criteria: str, client: LlmClient, actin_rules) -> dict[str, list[ActinMapping]]:
    cohort_texts: dict[str, str] = llm_extract_cohort_tagged_text(eligibility_criteria, client)
    logger.info(f"Processing cohorts: {list(cohort_texts.keys())}")

    cohort_actin_outputs: dict[str, list[ActinMapping]] = {}
    for cohort_name, tagged_text in cohort_texts.items():
        cohort_actin_outputs[cohort_name] = actin_workflow(tagged_text, client, actin_rules)

    return cohort_actin_outputs


def main():
    parser = argparse.ArgumentParser(description="Clinical trial curator")
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_file', help='output file containing trial data', required=True)
    parser.add_argument('--ACTIN_path', help='Full path to ACTIN rules CSV', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    client = OpenaiClient(TEMPERATURE)
    actin_rules = load_actin_file(args.ACTIN_path)

    trial_data = load_trial_data(args.trial_json)
    eligibility_criteria = load_eligibility_criteria(trial_data)

    actin_outputs = actin_map_by_cohort(eligibility_criteria, client, actin_rules)

    with open(args.out_trial_file, "w", encoding="utf-8") as f:
        json.dump(actin_outputs, f, indent=2)

    logger.info(f"ACTIN results written to {args.out_trial_file}")


if __name__ == "__main__":
    main()
