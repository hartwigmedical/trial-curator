import json
import pandas as pd
import logging
import argparse
from typing import TypedDict, Union
from itertools import chain

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
    actin_rule: dict[str, list]
    new_rule: list[str]


def map_to_actin(input_eligibility_criteria: str, client: LlmClient, actin_rules: list[str]) -> list[ActinMapping]:
    system_prompt = """
You are a clinical trial curation assistant.
Your task is to convert each free-text eligibility criterion into structured ACTIN rules.

1. Input format

- Each line starting with `INCLUDE` or `EXCLUDE` begins a new **eligibility block**.
- All **indented or bullet-point lines** underneath belong to that block.
- Treat the **entire block (header + sub-points)** as a **single criterion**.
    E.g. Below is one eligibility block (which treats header and bullet points as one joined description).
    INCLUDE Adequate bone marrow function
      - ANC ≥ 1.5 x 10^9/L
      - Platelet count ≥ 100 x 10^9/L

2. ACTIN rule structure

- ACTIN rules are defined with a rule name, 
    E.g. HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y_WITHIN_Z_WEEKS, where X, Y, Z are placeholders for parameters. 
- In general,
  - RULE_X → one param: ["value"]
  - RULE_X_Y_Z → three params: ["v1", "v2", 5]
- Not all rules have parameters.

3. MAIN RULE MATCHING INSTRUCTIONS

- Match each eligibility block into one or more ACTIN rules from the ACTIN RULES LIST.
    - Match by **rule name pattern**, not exact text.
- Accept biologically equivalent terms (e.g. “fusion” = “rearrangement”).
- Prefer general rules unless specificity is required.
- Only create a new rule if there is truly no match
    - If the rule name doesn’t exist in the ACTIN rule list:
        "new_rule": ["NEW_RULE_NAME"]
    - Otherwise: 
        "new_rule": []

4. Fallback rules

Use if no exact rule match applies:

| Scenario              | Rule                                     |
|-----------------------|------------------------------------------|
| Treatment line        | `IS_ELIGIBLE_FOR_TREATMENT_LINE_X[...]`  |
| Gene rearrangement    | `FUSION_IN_GENE_X[...]`                  |
| Comorbidity           | `NOT(HAS_SEVERE_CONCOMITANT_CONDITION)`  |
| Disease history       | `HAS_HISTORY_OF_CARDIOVASCULAR_DISEASE`  |
| Unspecified cancer    | `HAS_CANCER_TYPE[X]`                     |

4. Logical operators

| Operator | Format                         | Meaning                   |
|----------|--------------------------------|---------------------------|
| `AND`    | `{ "AND": [rule1, rule2] }`    | All conditions required   |
| `OR`     | `{ "OR": [rule1, rule2] }`     | At least one condition    |
| `NOT`    | `{ "NOT": rule }`              | Negate a single rule      |

5. Numerical comparison logic

| Text  | Rule Format                     |
|-------|----------------------------------|
| ≥ X   | `IS_AT_LEAST_X[...]`            |
| > X   | `IS_AT_LEAST_X[...]` (adjusted) |
| ≤ X   | `IS_AT_MOST_X[...]`             |
| < X   | `IS_AT_MOST_X[...]` (adjusted)  |

6. Exclusion logic
- For every EXCLUDE line, the **entire logical condition** must be wrapped in a single `NOT`, even if the rule is \
inherently negative in name (e.g., HAS_ACTIVE_INFECTION, IS_PREGNANT).

7. Output format (in JSON)
Each processed criterion becomes:
```json
{
  "description": "INCLUDE ...",
  "actin_rule": { ... },
  "new_rule": []
},
{
  "description": "EXCLUDE ...",
  "actin_rule": { ... },
  "new_rule": [NEW_RULE_X]
}
```

Use arrays of one object for each criterion, example:
```json
[
    {
        "description": "EXCLUDE Body weight over 150 kg",
        "actin_rule": { "NOT": { "HAS_BODY_WEIGHT_OF_AT_LEAST_X": [150] } },
        "new_rule": []
    },
    {
        "description": "INCLUDE Eligible for systemic treatment with capecitabine + anti-VEGF antibody",
        "actin_rule": { "IS_ELIGIBLE_FOR_TREATMENT_LINE_X": ["capecitabine", "anti-VEGF antibody"] },
        "new_rule": ["IS_ELIGIBLE_FOR_TREATMENT_LINE_X"]
    }
]
```
8. General guidance/Reminders

- Capture full clinical and logical meaning.
- Do not paraphrase or omit relevant details.
- Mark `new_rule` only if the rule name is truly new to ACTIN.


"""
    user_prompt = """
Reminder the instructions are:

- Match each eligibility block into one or more ACTIN rules from the ACTIN RULES LIST.
- Accept biologically equivalent terms
- Prefer general rules unless specificity is required.
- Only create a new rule if there is truly no match    

EXAMPLES:
[
    {
        "description": "EXCLUDE Body weight over 150 kg",
        "actin_rule": { "NOT": { "HAS_BODY_WEIGHT_OF_AT_LEAST_X": [150] }},
        "new_rule": []
    },
    {
        "description": "INCLUDE Eligible for systemic treatment with capecitabine + anti-VEGF antibody",
        "actin_rule": { "IS_ELIGIBLE_FOR_TREATMENT_LINE_X": ["capecitabine", "anti-VEGF antibody"] },
        "new_rule": ["IS_ELIGIBLE_FOR_TREATMENT_LINE_X"]
    }
]

"""
    user_prompt += """..."""
    user_prompt += "\nACTIN RULES:\n" + "\n".join(actin_rules)
    user_prompt += """..."""

    user_prompt += f"""
Now map the following eligibility criteria:
{input_eligibility_criteria}
"""

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)
    print(f"RAW OUTPUT:\n{output_eligibility_criteria}")

    output_eligibility_criteria = extract_code_blocks(output_eligibility_criteria, "json")
    print(f"OUTPUT after extract_code_blocks():\n{output_eligibility_criteria}")

    try:
        output_eligibility_criteria_final = json.loads(output_eligibility_criteria)
        logger.info(f"Mapping to ACTIN:\n{output_eligibility_criteria_final}")
        # print(f"FINAL OUTPUT):\n{output_eligibility_criteria_final}")
        return output_eligibility_criteria_final
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON\n{e}")
        raise


def correct_actin_mistakes(initial_actin_mapping: list[ActinMapping], client: LlmClient) -> list[ActinMapping]:
    system_prompt = """
You are a post-processing assistant for ACTIN rule mapping. 
Your job is to identify and then correct mistakes in mapped ACTIN rules.

IMPORTANT:
- You must only modify the value of the "actin_rule" field.
- Do not modify the description, actin_params, or new_rule fields.
- Do not add any surrounding text.
- Retain the same output format.

MISTAKES TO CORRECT:

1. Incorrect rule for drug class
    
If the ACTIN rule is HAS_HAD_TREATMENT_WITH_ANY_DRUG_X[...] or HAS_NOT_HAD_CATEGORY_X_TREATMENT[...] \
and refers to a drug class (e.g., PD-1 inhibitor, anti-EGFR antibody, HER2-targeted therapy, etc.), \
Then rewrite the rule as:
    HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[...] or its NOT(...) form.

The parameters in HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[<CATEGORY>, <DRUG CLASS] are
    - CATEGORY: IMMUNOTHERAPY, TARGETED THERAPY, CHEMOTHERAPY, etc.
    - DRUG CLASS: PD-1 antibody, PD-L1 antibody, EGFR antibody, HER2 antibody, etc.
    
Important: Do not invent a new rule HAS_NOT_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y. Instead use NOT(HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y)

2. Double-negative mappings 

If the description begins with "EXCLUDE..." and the matched ACTIN rule also contains a "NOT" or "NO" inside, \
check if it is an incorrect double-negatives such as:
    - NOT(IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL)
    - NOT(HAS_NO_HISTORY_OF_...)

If so, remove `NOT(...)` and return only the enclosed rule:
    - IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL
    - HAS_NO_HISTORY_OF_...

3. Overly specific rule names

Some mapped ACTIN rules contain overly specific information that is not in the input description. 

For example:
❌ HAS_MEASURABLE_DISEASE_RECIST ← incorrect if the input does not mention RECIST 
✅ HAS_MEASURABLE_DISEASE ← should be the correct rule

FORMATTING INSTRUCTIONS:
- Keep the exact same format as the input.
i.e.
[
    {
        "description": "...",
        "actin_rule": { "RULE":[] },
        "new_rule": []
    },
    {
        "description": "...",
        "actin_rule": { "RULE_NAME_X":[5] },
        "new_rule": ["RULE_NAME_X"]
    }
]

Final reminder:
- Modify only the "actin_rule" field.
- Do not change overall format or "description"
- Only modify "actin_params" if the new actin_rule requires different parameters (e.g., adding a category).
"""
    user_prompt = f"""
Below are the initial ACTIN mappings. 
Please review each mapping and make corrections to "actin_rule" fields as instructed:
{json.dumps(initial_actin_mapping, indent=2)}
"""

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)
    # print(f"RAW OUTPUT:\n{output_eligibility_criteria}")

    output_eligibility_criteria = extract_code_blocks(output_eligibility_criteria, "json")
    # print(f"OUTPUT after extract_code_blocks():\n{output_eligibility_criteria}")

    try:
        output_eligibility_criteria_final = json.loads(output_eligibility_criteria)
        logger.info(f"Mapping to ACTIN:\n{output_eligibility_criteria_final}")
        # print(f"FINAL OUTPUT):\n{output_eligibility_criteria_final}")
        return output_eligibility_criteria_final
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON\n{e}")
        raise


def actin_workflow(eligibility_criteria: str, client: LlmClient, actin_rules: list[str]) -> list[
    ActinMapping]:
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

    actin_outputs = actin_map_by_cohort(eligibility_criteria, client, actin_rules, BATCH_SIZE, MAX_RETRIES)

    with open(args.out_trial_file, "w", encoding="utf-8") as f:
        json.dump(actin_outputs, f, indent=2)

    logger.info(f"ACTIN results written to {args.out_trial_file}")


if __name__ == "__main__":
    main()
