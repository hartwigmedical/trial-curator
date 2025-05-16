import json
import pandas as pd
import logging
import argparse
from typing import TypedDict
from itertools import chain

import yaml

from trialcurator.actin_curator_utils import fix_malformed_yaml, fix_rule_format
from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient
from trialcurator.gemini_client import GeminiClient
from trialcurator.utils import load_trial_data, load_eligibility_criteria, batch_tagged_criteria, extract_code_blocks
from trialcurator.eligibility_sanitiser import llm_extract_cohort_tagged_text

logger = logging.getLogger(__name__)

TEMPERATURE = 0.0
BATCH_SIZE = 5


def load_actin_rules(file_path: str) -> list[str]:
    df = pd.read_csv(file_path, header=None)
    actin_rules = df[0].str.strip().tolist()
    return actin_rules


class ActinMapping(TypedDict):
    description: str
    actin_rule: dict[str, list | dict]
    new_rule: list[str]


def map_to_actin(input_eligibility_criteria: str, client: LlmClient, actin_rules: list[str]) -> list[ActinMapping]:

    actin_rules = "\n".join(actin_rules)

    system_prompt = """
You are a clinical trial curation assistant for a system called ACTIN.  
Your task is to convert each free-text eligibility criterion into structured ACTIN rules.

## Input format

- Each line starting with `INCLUDE` or `EXCLUDE` begins a new **eligibility block**.
- All **indented or bullet-point lines** underneath belong to that block.
- Treat the **entire block (header + sub-points)** as a **single criterion**.

### Example:
```text
INCLUDE Adequate bone marrow function:
  - ANC ≥ 1.5 x 10^9/L
  - Platelet count ≥ 100 x 10^9/L
```

## ACTIN rule structure

- ACTIN rules are defined with a rule name, 
    E.g. HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y_WITHIN_Z_WEEKS, where X, Y, Z are placeholders for parameters. 
- Parameters are based on placeholders:
  - `RULE` → no param: `RULE`
  - `RULE_X` → one param: `RULE_X: ["value"]`
  - `RULE_X_Y_Z` → three params: `RULE_X: ["v1", "v2", 5]`

## MAIN RULE MATCHING INSTRUCTIONS

- Match each eligibility block into one or more ACTIN rules from the ACTIN RULES LIST.
  - Match by **rule name pattern**, not exact text.
- Accept biologically equivalent terms (e.g. “fusion” = “rearrangement”).
- Prefer general rules unless specificity is required.
- Only create a new rule if there is truly no match:
  - If the rule name doesn’t exist in the ACTIN rule list:
    ```yaml
    new_rule:
      - NEW_RULE_NAME
    ```
  - Otherwise:
    ```yaml
    new_rule: []
    ```

## Fallback rules

Use if no exact rule match applies:

| Scenario           | Rule                                     |
|--------------------|------------------------------------------|
| Treatment line     | `IS_ELIGIBLE_FOR_TREATMENT_LINE_X[...]`  |
| Gene rearrangement | `FUSION_IN_GENE_X[...]`                  |
| Comorbidity        | `NOT: HAS_SEVERE_CONCOMITANT_CONDITION`  |
| Disease history    | `HAS_HISTORY_OF_CARDIOVASCULAR_DISEASE`  |
| Unspecified cancer | `HAS_CANCER_TYPE[X]`                     |

## Logical Operators

- **AND**  
  ```yaml
  AND:
    - rule1
    - rule2
  ```
- **OR**  
  ```yaml
  OR:
    - rule1
    - rule2
  ```
- **NOT**  
  ```yaml
  NOT:
    rule
  ```

## Numerical comparison logic

| Text  | Rule Format                               |
|-------|-------------------------------------------|
| ≥ X   | `SOMETHING_IS_AT_LEAST_X[...]`            |
| > X   | `SOMETHING_IS_AT_LEAST_X[...]` (adjusted) |
| ≤ X   | `SOMETHING_IS_AT_MOST_X[...]`             |
| < X   | `SOMETHING_IS_AT_MOST_X[...]` (adjusted)  |

## Exclusion logic

- For every EXCLUDE line, the **entire logical condition** must be wrapped in a single `NOT`,  
  unless the matched ACTIN rule is already negative in meaning (e.g. `HAS_NOT`, `IS_NOT`)

## Output format

Output a **YAML array** with criteria objects.
`description`: Always wrap this field in double quotes.

### Example:

```yaml
- description: "EXCLUDE Body weight over 150 kg"
  actin_rule:
    NOT:
      HAS_BODY_WEIGHT_OF_AT_LEAST_X:
        - 150
  new_rule: []

- description: "INCLUDE Eligible for systemic treatment with capecitabine + anti-VEGF antibody"
  actin_rule:
    IS_ELIGIBLE_FOR_TREATMENT_LINE_X:
      - capecitabine
      - anti-VEGF antibody
  new_rule:
    - IS_ELIGIBLE_FOR_TREATMENT_LINE_X

- description: "INCLUDE Is female"
  actin_rule:
    IS_FEMALE
  new_rule: []
```

## General guidance / Reminders

- Capture full clinical and logical meaning.
- Do not paraphrase or omit relevant details.
- Mark `new_rule` only if the rule name is truly new to ACTIN.

"""

    user_prompt = f"""Following are the ACTIN rules:
```
{actin_rules}
```

Map the following eligibility criteria:
```
{input_eligibility_criteria}
```
"""

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)
    # print(f"RAW OUTPUT:\n{output_eligibility_criteria}")

    output_eligibility_criteria = extract_code_blocks(output_eligibility_criteria, "yaml")
    output_eligibility_criteria = fix_malformed_yaml(output_eligibility_criteria)
    # print(f"CLEANED OUTPUT:\n{output_eligibility_criteria}")

    try:
        output_eligibility_criteria_final = yaml.safe_load(output_eligibility_criteria)
        output_eligibility_criteria_final = fix_rule_format(output_eligibility_criteria_final)
        logger.info(f"Mapping to ACTIN:\n{output_eligibility_criteria_final}")
        # print(f"FINAL OUTPUT):\n{output_eligibility_criteria_final}")
        return output_eligibility_criteria_final
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON\n{e}")
        logger.warning(output_eligibility_criteria)
        raise


def correct_actin_mistakes(initial_actin_mapping: list[ActinMapping], client: LlmClient) -> list[ActinMapping]:
    system_prompt = """
You are a post-processing assistant for ACTIN rule mapping. 
Your job is to identify and then correct mistakes in mapped ACTIN rules.

# MISTAKES TO CORRECT:

## Incorrect rule for drug class

- If the original ACTIN rule is one of:
    - HAS_HAD_TREATMENT_WITH_ANY_DRUG_X
    - HAS_NOT_HAD_CATEGORY_X_TREATMENT
    - HAS_RECEIVED_ANY_ANTI_CANCER_THERAPY_WITHIN_X_WEEKS
    - or any semantically equivalent variant

And the value refers to a **drug class** (e.g., "PD-1 inhibitor", "anti-EGFR antibody", "HER2-targeted therapy"),

Then rewrite it using:
    HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[<CATEGORY>, <DRUG CLASS>]

Where the parameters are:
    - CATEGORY: IMMUNOTHERAPY, TARGETED THERAPY, CHEMOTHERAPY, etc.
    - DRUG CLASS: PD-1 antibody, anti-EGFR antibody, HER2 antibody, etc.

- If the original mapping contains NOT(...), or if the original description starts with "EXCLUDE", \
then you must wrap the corrected rule in a NOT(...) block — even if the rule name itself is already negative in nature.

❌ Incorrect:
```yaml
- description: "EXCLUDE ..."
  actin_rule:
    HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y
      -...

```

✅ Correct:
```yaml
- description: "EXCLUDE ..."
  actin_rule:
    NOT:
       HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y
         -...
```

## Double negatives

- If the description begins with "EXCLUDE" and the rule is like NOT(IS_NOT_...) or NOT(HAS_NO_...), 
  remove the outer NOT.

## Overly specific rule names

- If a rule adds specific context not present in the description (e.g., HAS_MEASURABLE_DISEASE_RECIST), 
  simplify it (e.g., HAS_MEASURABLE_DISEASE).

# RULES:

- Only update the "actin_rule" field
- Do not invent new rules
- Do not modify `"description"` or `"new_rule"` fields.
- Return the same YAML format with no extra text
"""
    user_prompt = f"""
Below are the initial ACTIN mappings. 
Review each mapping and make corrections to "actin_rule" fields as instructed:
```yaml
{yaml.dump(initial_actin_mapping)}
```
"""

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)
    # print(f"RAW OUTPUT:\n{output_eligibility_criteria}")

    output_eligibility_criteria = extract_code_blocks(output_eligibility_criteria, "yaml")
    output_eligibility_criteria = fix_malformed_yaml(output_eligibility_criteria)
    # print(f"CLEANED OUTPUT:\n{output_eligibility_criteria}")

    try:
        output_eligibility_criteria_final = yaml.safe_load(output_eligibility_criteria)
        output_eligibility_criteria_final = fix_rule_format(output_eligibility_criteria_final)
        logger.info(f"Mapping to ACTIN:\n{output_eligibility_criteria_final}")
        # print(f"FINAL OUTPUT):\n{output_eligibility_criteria_final}")
        return output_eligibility_criteria_final
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON\n{e}")
        logger.warning(output_eligibility_criteria)
        raise


def actin_workflow(eligibility_criteria: str, client: LlmClient, actin_rules: list[str]) -> list[ActinMapping]:
    initial_mapping = map_to_actin(eligibility_criteria, client, actin_rules)
    corrected_mapping = correct_actin_mistakes(initial_mapping, client)
    return corrected_mapping


def actin_map_by_batch(eligibility_criteria: str, client: LlmClient, actin_rules, batch_size: int) -> \
        list[ActinMapping]:
    sanitised_text_batches = batch_tagged_criteria(eligibility_criteria, batch_size)
    curated_batches: list[list[ActinMapping]] = []

    for single_batch in sanitised_text_batches:
        mapped_rules = actin_workflow(single_batch, client, actin_rules)
        curated_batches.append(mapped_rules)

    return list(chain.from_iterable(curated_batches))


def actin_map_by_cohort(eligibility_criteria: str, client: LlmClient, actin_rules, batch_size: int) -> \
        dict[str, list[ActinMapping]]:
    cohort_texts: dict[str, str] = llm_extract_cohort_tagged_text(eligibility_criteria, client)
    logger.info(f"Processing cohorts: {list(cohort_texts.keys())}")

    cohort_actin_outputs: dict[str, list[ActinMapping]] = {}
    for cohort_name, tagged_text in cohort_texts.items():
        cohort_actin_outputs[cohort_name] = actin_map_by_batch(tagged_text, client, actin_rules, batch_size)

    return cohort_actin_outputs


def main():
    parser = argparse.ArgumentParser(description="Clinical trial curator")
    parser.add_argument('--model', help='Select between GPT and Gemini', required=True)
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_file', help='output file containing trial data', required=True)
    parser.add_argument('--ACTIN_path', help='Full path to ACTIN rules CSV', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    actin_rules = load_actin_rules(args.ACTIN_path)

    trial_data = load_trial_data(args.trial_json)
    eligibility_criteria = load_eligibility_criteria(trial_data)

    if args.model == "Gemini":
        client = GeminiClient(TEMPERATURE)
    else:
        client = OpenaiClient(TEMPERATURE)

    actin_outputs = actin_map_by_cohort(eligibility_criteria, client, actin_rules, BATCH_SIZE)

    with open(args.out_trial_file, "w", encoding="utf-8") as f:
        json.dump(actin_outputs, f, indent=2)

    logger.info(f"ACTIN results written to {args.out_trial_file}")


if __name__ == "__main__":
    main()
