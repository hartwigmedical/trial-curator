import json
import re
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

# In regression testing, whenever JSONDecodeError is encountered, if the prompt is run again, it always resolved the issue on the second try. Therefore, set max retries to (4-1) times.
MAX_RETRIES = 4

def load_actin_rules(file_path: str) -> list[str]:
    df = pd.read_csv(file_path, header=None)
    actin_rules = df[0].str.strip().tolist()
    return actin_rules

class ActinMapping(TypedDict):
    description: str
    actin_rule: dict[str, list]
    new_rule: list[str]

def map_to_actin(input_eligibility_criteria: str, client: LlmClient, actin_rules: list[str], max_retries: int) -> list[ActinMapping]:

    system_prompt = """
You are a clinical trial curation assistant.
Your task is to convert each free-text eligibility criterion into structured ACTIN rules.

TASKS
- Match each line into one or more ACTIN rules from the ACTIN RULES LIST.
- Only create a new rule if there is truly no match — after an exhaustive search for semantic equivalents.
- Always indicate new rules with a full rule name after `New rule:`.
Important:
- Each line that starts with `INCLUDE` or `EXCLUDE` is a distinct criterion to map. Process each line independently into a separate JSON object.

ACTIN RULE STRUCTURE
- ACTIN rules are defined with a rule name, e.g. HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y_WITHIN_Z_WEEKS, where X, Y, Z \
are placeholders for parameters. Not all rules have parameter. 

RULE MATCHING
- Match based on **rule name pattern**, not literal string tokens.
- A rule is NOT new if its name exists in ACTIN, even with different parameter values.
- When matching to ACTIN rules, interpret biologically equivalent phrases (e.g., "gene rearrangement", "fusion", "translocation") as semantically interchangeable **unless the context specifies otherwise**.
- For example:
    - "ROS1 rearrangement", "ROS1 fusion", or "ROS1 translocation" → use: FUSION_IN_GENE_X[ROS1]
- Prefer using **general rules** (e.g. HAS_HAD_TREATMENT_WITH_ANY_DRUG_X) over disease-specific variants unless those are explicitly required.

FALLBACK RULES (use when no exact ACTIN rule matches)
- Treatment eligibility:
    `IS_ELIGIBLE_FOR_TREATMENT_LINE_X[capecitabine + anti-VEGF antibody]`
- Gene fusion or rearrangement (any chromosomal fusion event):  
    FUSION_IN_GENE_X[ROS1]
- Broad compliance-impacting condition:  
    `NOT(HAS_SEVERE_CONCOMITANT_CONDITION)`
- General fallback rules (e.g. `HAS_HISTORY_OF_CARDIOVASCULAR_DISEASE`) should be used **when they exactly match the concept described**, and are preferred over unnecessarily listing multiple redundant rules.
- For example, if the condition lists various types of recent cardiovascular disease (e.g., MI, stroke, arrhythmia), and an existing ACTIN rule such as `HAS_HISTORY_OF_CARDIOVASCULAR_DISEASE` captures them all — **use the general rule**.
- If unsure about exact mapping to cancer type, create a new rule HAS_CANCER_TYPE[X] (e.g. HAS_CANCER_TYPE[SCLC], HAS_CANCER_TYPE[NSCLC])

WHAT COUNTS AS A NEW RULE
- A rule is NEW if the part before square brackets (the rule name) is not found in the ACTIN RULE LIST.
    - To check: compare the rule name exactly against the list of ACTIN rules provided.
    - If the rule name is not present in the list, then add it to the "new_rule" list:
        `"new_rule": ["NAME_OF_NEW_RULE"]`
    - If no new rule is added, set "new_rule" list to `[]`

LOGICAL OPERATORS

| Operator | Format                         | Meaning                   |
|----------|--------------------------------|---------------------------|
| `AND`    | `{ "AND": [rule1, rule2] }`    | All conditions required   |
| `OR`     | `{ "OR": [rule1, rule2] }`     | At least one condition    |
| `NOT`    | `{ "NOT": rule }`              | Negate a single rule      |

---

### Conditional Logic (`IF`)
```json
{
  "IF": {
    "condition": { "RULE_A": [...] },
    "then": { "RULE_B": [...] },
    "else": { "RULE_C": [...] } // optional
  }
}
```

- `then` applies if `condition` is true.
- `else` is optional (applied if false).
- Logical nesting is allowed inside any clause.

EXCLUSION LOGIC
- For every EXCLUDE line, the **entire logical condition** must be wrapped in a single `NOT`, even if the rule is \
inherently negative in name (e.g., HAS_ACTIVE_INFECTION, IS_PREGNANT).

NUMERIC COMPARISON LOGIC
- “≥ X” → `IS_AT_LEAST_X[...]`
- “> X” → `IS_AT_LEAST_X[...]` with increased value if needed
- “≤ X” → `IS_AT_MOST_X[...]`
- “< X” → `IS_AT_MOST_X[...]` with decreased value if needed

MULTI-LINE DEFINITIONS
- If a criterion contains a header phrase (e.g. "Inadequate organ function, defined as:") and is followed by lab values or other restrictive bullets,
  treat the **entire definition as one unit**.
- Do NOT map the header line alone. Instead, include the lab values or bullet clauses beneath it when generating ACTIN rules.

ACTIN RULES FORMATTING
- Format all ACTIN rules as JSON dictionaries:
  ```json
  { "RULE_NAME": [parameters] }
  ```
- Composite rule like NOT is represented the same way, in which the parameter list consists of other rules, i.e: \
`{ NOT: [{"RULE_NAME": [18]}]}`
- Never paraphrase or omit important medical or logical detail.
- Do not mark rules as new unless they introduce a completely new rule name.
- "new_rule": [] indicates no new rule is created

Output in JSON FORMAT:
```json
[
  {
    "description": "INCLUDE: Prior chemotherapy within last 12 weeks",
    "actin_rule": { "HAS_HAD_CATEGORY_CHEMOTHERAPY_TREATMENT_WITHIN_X_WEEKS": [12] },
    "new_rule": []
  }
]
```

"""
    user_prompt = """
You are given a list of clinical trial eligibility criteria, each tagged with INCLUDE or EXCLUDE.


Instructions:
- Map each criterion that begins with INCLUDE or EXCLUDE to one or more ACTIN rules.
- For every EXCLUDE line, wrap the entire ACTIN rule(s) in `NOT(...)`, even if the rule name already sounds negative.
- Combine logic with `AND` or `OR` according to the text.
- Use fallback rules or create new rule names only if no existing ACTIN match is possible.
Important:
- Each line that starts with `INCLUDE` or `EXCLUDE` is a distinct criterion to map. Process each line independently into a separate JSON object.

FORMAT EXAMPLES:
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

    output_eligibility_criteria = ""
    for attempt in range(1, max_retries):
        output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)

        output_eligibility_criteria = re.sub(r"```json\s*\n", "```json", output_eligibility_criteria, flags=re.IGNORECASE)
        extracted = extract_code_blocks(output_eligibility_criteria, "json")
        if not extracted.strip():
            logger.warning("extract_code_blocks() returned nothing; falling back to raw LLM response")
            extracted = output_eligibility_criteria
        output_eligibility_criteria = extracted

        if not output_eligibility_criteria.strip():
            raise ValueError("LLM response is empty after cleaning")

        try:
            output_eligibility_criteria = json.loads(output_eligibility_criteria)
            logger.info(f"Mapping to ACTIN:\n{output_eligibility_criteria}")
            return output_eligibility_criteria
        except json.JSONDecodeError as e:
            logger.warning(f"On attempt {attempt}: Failed to parse JSON. Retrying...\n{e}")

    logger.error("Max retries exceeded. Final unparsed output:\n%s", output_eligibility_criteria)
    raise json.JSONDecodeError("Final retry failed to parse JSON", output_eligibility_criteria, 0)

def correct_actin_mistakes(initial_actin_mapping: list[ActinMapping], client: LlmClient, max_retries: int) -> list[ActinMapping]:

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

    output_eligibility_criteria = ""
    for attempt in range(1, max_retries):
        output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)

        output_eligibility_criteria = re.sub(r"```json\s*\n", "```json", output_eligibility_criteria, flags=re.IGNORECASE)
        extracted = extract_code_blocks(output_eligibility_criteria, "json")
        if not extracted.strip():
            logger.warning("extract_code_blocks() returned nothing; falling back to raw LLM response")
            extracted = output_eligibility_criteria
        output_eligibility_criteria = extracted

        if not output_eligibility_criteria.strip():
            raise ValueError("LLM response is empty after cleaning")

        try:
            output_eligibility_criteria = json.loads(output_eligibility_criteria)
            logger.info(f"Corrections to ACTIN:\n{output_eligibility_criteria}")
            return output_eligibility_criteria
        except json.JSONDecodeError as e:
            logger.warning(f"On attempt {attempt}: Failed to parse JSON. Retrying...\n{e}")

    logger.error("Max retries exceeded. Final unparsed output:\n%s", output_eligibility_criteria)
    raise json.JSONDecodeError("Final retry failed to parse JSON", output_eligibility_criteria, 0)

def actin_workflow(eligibility_criteria: str, client: LlmClient, actin_rules: list[str], max_retries: int) -> list[ActinMapping]:
    initial_mapping = map_to_actin(eligibility_criteria, client, actin_rules, max_retries)
    corrected_mapping = correct_actin_mistakes(initial_mapping, client, max_retries)
    return corrected_mapping

def actin_map_by_batch(eligibility_criteria: str, client: LlmClient, actin_rules, batch_size: int, max_retries: int) -> list[ActinMapping]:
    sanitised_text_batches = batch_tagged_criteria(eligibility_criteria, batch_size)
    curated_batches: list[list[ActinMapping]] = []

    for single_batch in sanitised_text_batches:
        mapped_rules = actin_workflow(single_batch, client, actin_rules, max_retries)
        curated_batches.append(mapped_rules)

    return list(chain.from_iterable(curated_batches))

def actin_map_by_cohort(eligibility_criteria: str, client: LlmClient, actin_rules, batch_size: int, max_retries: int) -> dict[str, list[ActinMapping]]:
    cohort_texts: dict[str, str] = llm_extract_cohort_tagged_text(eligibility_criteria, client)
    logger.info(f"Processing cohorts: {list(cohort_texts.keys())}")

    cohort_actin_outputs: dict[str, list[ActinMapping]] = {}
    for cohort_name, tagged_text in cohort_texts.items():
        cohort_actin_outputs[cohort_name] = actin_map_by_batch(tagged_text, client, actin_rules, batch_size, max_retries)

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