import json
import re
import pandas as pd
import logging
import argparse

from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient
from trialcurator.gemini_client import GeminiClient
from trialcurator.utils import load_trial_data, load_eligibility_criteria, batch_tagged_criteria
from trialcurator.eligibility_sanitiser import llm_extract_cohort_tagged_text

logger = logging.getLogger(__name__)

TEMPERATURE = 0.0
RULES_BATCH_SIZE = 5

def load_actin_rules(file_path: str) -> list[str]:
    df = pd.read_csv(file_path, header=None)
    actin_rules = df[0].str.strip().tolist()
    return actin_rules

def map_to_actin(input_eligibility_criteria: str, client: LlmClient, actin_rules: list[str]) -> str:

    system_prompt = """
You are a clinical trial curation assistant.
Your task is to convert each free-text eligibility criterion into structured ACTIN rules.

TASKS
- Translate each line into one or more ACTIN rules.
- Match to the existing ACTIN RULE LIST.
- Only create a new rule if there is truly no match — search for semantic equivalents.
- Always indicate new rules with a full rule name after `New rule:`.

RULE MATCHING
- Match based on **rule name pattern**, not literal string tokens.
- A rule is NOT new if its name exists in ACTIN, even with different parameter values.
- When matching to ACTIN rules, interpret biologically equivalent phrases (e.g., "gene rearrangement", "fusion", "translocation") as semantically interchangeable **unless the context specifies otherwise**.
- For example:
    - "ROS1 rearrangement", "ROS1 fusion", or "ROS1 translocation" → use: FUSION_IN_GENE_X[ROS1]
- Prefer using **general rules** (e.g. HAS_HAD_TREATMENT_WITH_ANY_DRUG_X) over disease-specific variants unless those are explicitly required.

FALLBACK RULES (use when no ACTIN rule matches)
- Treatment eligibility:  
    `IS_ELIGIBLE_FOR_TREATMENT_LINE_X[capecitabine + anti-VEGF antibody]`
- Gene fusion or rearrangement (any chromosomal fusion event):  
    FUSION_IN_GENE_X[ROS1]
- Broad compliance-impacting condition:  
    `NOT(HAS_SEVERE_CONCOMITANT_CONDITION)`
- General fallback rules (e.g., `HAS_HISTORY_OF_CARDIOVASCULAR_DISEASE`) should be used **when they exactly match the concept described**, and are preferred over unnecessarily listing multiple redundant rules.
- For example, if the condition lists various types of recent cardiovascular disease (e.g., MI, stroke, arrhythmia), and an existing ACTIN rule such as `HAS_HISTORY_OF_CARDIOVASCULAR_DISEASE` captures them all — **use the general rule**.
- Prior drug exposure (general):  
    `HAS_HAD_TREATMENT_WITH_ANY_DRUG_X[ros1 tyrosine kinase inhibitor]`
- If unsure about exact mapping to cancer type, create a new rule HAS_CANCER_TYPE[X] (e.g. HAS_CANCER_TYPE[SCLC], HAS_CANCER_TYPE[NSCLC])

WHAT COUNTS AS A NEW RULE
- A rule is NEW if the part before square brackets (the rule name) is not found in the ACTIN RULE LIST.
    - To check: compare the rule name exactly against the list of ACTIN rules provided.
    - If the rule name is not present in the list, then set:
        New rule:
            [RULE_NAME]
    - If it is present, write:
        New rule:
            False
- Do not mark the rule as "False" unless the exact rule name already exists in the ACTIN RULE LIST.

LOGICAL STRUCTURE
- Use `OR` if multiple alternatives are valid (e.g., “histological OR cytological” confirmation).
    - Example:
      `(HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE OR HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE)`
- Use `AND` when all conditions must be met.
- Combine logical groups using parentheses when mixing `AND` and `OR`:
    - Correct: `(A OR B) AND C`
    - Incorrect: `A OR B AND C`
    
EXCLUSION LOGIC
- For every EXCLUDE line, the **entire logical condition** must be wrapped in a single `NOT(...)`, even if the rule is inherently adverse or negative in name (e.g., HAS_ACTIVE_INFECTION, IS_PREGNANT).
- For EXCLUDE lines, always use:
    NOT(condition1 OR condition2 OR ...)  ← preferred
    or
    NOT(condition)  ← if it's a single clause

NUMERIC COMPARISON LOGIC
- “≥ X” → `IS_AT_LEAST_X[...]`
- “> X” → `IS_AT_LEAST_X[...]` with increased value if needed
- “≤ X” → `IS_AT_MOST_X[...]`
- “< X” → `IS_AT_MOST_X[...]` with decreased value if needed

MULTI-LINE DEFINITIONS
- If a criterion contains a header phrase (e.g. "Inadequate organ function, defined as:") and is followed by lab values or other restrictive bullets,
  treat the **entire definition as one unit**.
- Do NOT map the header line alone. Instead, include the lab values or bullet clauses beneath it when generating ACTIN rules.

FORMATTING
- Use square brackets `[...]` for rule parameters.
- Use `AND`, `OR`, and `NOT` on their own lines.
- Never paraphrase or omit important medical or logical detail.
- Do not mark rules as new unless they introduce a completely new rule name.

OUTPUT FORMAT (strictly follow this):
Input:
    [original line]
ACTIN Output:
    [rule 1]
    AND/OR
    [rule 2]
New rule:
    [False OR full rule name]
"""

    user_prompt = """
You are given a list of clinical trial eligibility criteria, each tagged with INCLUDE or EXCLUDE.

Instructions:
- Map each line to ACTIN rules.
- For every EXCLUDE line, always wrap the entire ACTIN rule(s) in `NOT(...)`, even if the rule name already sounds negative.
- Combine logic with `AND` or `OR` according to the text.
- Use fallback rules or create new rule names only if no ACTIN match is possible.

FORMAT EXAMPLES:

Input: 
    EXCLUDE Body weight over 150 kg
ACTIN Output:
    NOT(HAS_BODY_WEIGHT_OF_AT_LEAST_X[150])
New rule:
    False

Input: 
    INCLUDE Eligible for systemic treatment with capecitabine + anti-VEGF antibody
ACTIN Output:
    IS_ELIGIBLE_FOR_TREATMENT_LINE_X[capecitabine + anti-VEGF antibody]
New rule:
    IS_ELIGIBLE_FOR_TREATMENT_LINE_X

Input: 
    EXCLUDE Mental disorders that may compromise patient compliance
ACTIN Output:
    NOT(HAS_SEVERE_CONCOMITANT_CONDITION)
New rule:
    False

Input: 
    INCLUDE Documented ROS1 gene rearrangement
ACTIN Output:
    FUSION_IN_GENE_X[ROS1]
New rule:
    False

Input:
    EXCLUDE Hemoglobin <5 mmol/L OR absolute neutrophil count <1.5 x 10^9/L
ACTIN Output:
    HAS_HEMOGLOBIN_MMOL_PER_L_OF_AT_LEAST_X[5]
    AND
    HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X[1.5]
New rule:
    False

Input:
    EXCLUDE Pregnant or lactating women.
ACTIN Output:
    NOT(IS_PREGNANT OR IS_BREASTFEEDING)
New rule:
    False

"""
    user_prompt += """..."""
    user_prompt += "\nACTIN RULES:\n" + "\n".join(actin_rules)
    user_prompt += """..."""

    user_prompt += f"""
Now map the following eligibility criteria:
{input_eligibility_criteria}
"""
    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)
    output_eligibility_criteria = output_eligibility_criteria.replace("```","")
    logger.info(f"Mapping to ACTIN:\n{output_eligibility_criteria}")
    return output_eligibility_criteria

def correct_common_actin_mistakes(initial_actin_mapping: str, client: LlmClient) -> str:

    system_prompt = """
You are a post-processing assistant for ACTIN rule mapping. Your job is to correct common mistakes in mapped ACTIN rules.
ONLY modify rules that clearly violate ACTIN rule design logic.
NEVER reformat valid rules or add any surrounding text.

COMMON MISTAKES TO CORRECT:

1. Drug name vs Drug class

❌ If the rule uses:
    HAS_HAD_TREATMENT_WITH_ANY_DRUG_X[...] where the bracketed content refers a Drug Class such as:
    - PD-1 inhibitor
    - anti-EGFR antibody
    - HER2-targeted therapy

✅ Then rewrite it as:
    HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y[<CATEGORY>, <DRUG CLASS] where
- CATEGORY: IMMUNOTHERAPY, TARGETED THERAPY, CHEMOTHERAPY, etc.
- DRUG CLASS: PD-1 antibody, PD-L1 antibody, EGFR antibody, HER2 antibody, etc.

⚠️ IMPORTANT:
You must ONLY use the rule name:
    - HAS_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y
or its NOT(...) form.

Do NOT to invent new rule names such as
    - HAS_NOT_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y_OR_AT_MOST_Z_LINES
    - HAS_RECEIVED_CATEGORY_X_MEDICATION_WITHIN_Y_WEEKS
    - HAS_NOT_HAD_CATEGORY_X_TREATMENT_OF_TYPES_Y

2. Double-negative mappings 

If the input text begins with "- EXCLUDE..." and the matched ACTIN rule also contains a "NOT" or "NO" inside, check the inner rule carefully:

Keep valid rules like:
    - NOT(IS_PREGNANT)
    - NOT(HAS_ACTIVE_INFECTION)
    - NOT(IS_BREASTFEEDING)
    
But fix true double-negatives like:
❌  - NOT(IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL)
❌  - NOT(HAS_NO_HISTORY_OF_...)
In those cases, remove `NOT(...)` and return only the enclosed rule.
✅  - IS_NOT_PARTICIPATING_IN_ANOTHER_TRIAL
✅  - HAS_NO_HISTORY_OF_...

3. Overly specific rule names

Some ACTIN rules are valid but too specific when a more general rule exists and fully captures the criterion.
i.e. Do not assume RECIST unless it is explicitly stated in the input.

For example:
❌ HAS_MEASURABLE_DISEASE_RECIST  
✅ HAS_MEASURABLE_DISEASE

FORMATTING INSTRUCTIONS:

- Keep each corrected ACTIN Output block in the exact same structure.
- If no changes are needed for a block, copy it exactly as-is.
- Never remove or simplify valid logic (e.g., don’t collapse multiple rules into one or vice versa).
- Do NOT add any explanatory introductions like “Below is the corrected set of ACTIN rule mappings”,"plaintext", etc.
- Return only the corrected ACTIN rules in raw format, one per line.
"""
    user_prompt = f"""
Below is the initial set of ACTIN rule mappings. 
Please review each mapping and make corrections as appropriate - remember NOT to correct mapping outside of these two types.
{initial_actin_mapping}
"""

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)
    output_eligibility_criteria = output_eligibility_criteria.replace("```","")
    logger.info(f"Correct common ACTIN mistakes:\n{output_eligibility_criteria}")
    return output_eligibility_criteria

def map_actin_by_batch(eligibility_criteria: str, client: LlmClient, actin_rules, batch_size: int) -> str:
    sanitised_text_batches = batch_tagged_criteria(eligibility_criteria, batch_size)
    curated_batches = []
    for rule in sanitised_text_batches:
        mapped_rules = map_to_actin(rule, client, actin_rules)
        corrected_rules = correct_common_actin_mistakes(mapped_rules, client)
        curated_batches.append(corrected_rules)
    return '\n\n'.join(curated_batches)

def curate_actin(eligibility_criteria: str, actin_rules, client: LlmClient) -> dict[str, str]:
    cohort_texts: dict[str, str] = llm_extract_cohort_tagged_text(eligibility_criteria, client)
    logger.info(f"Processing cohorts: {list(cohort_texts.keys())}")
    cohort_actin_outputs: dict[str, str] = {}
    for cohort_name, tagged_text in cohort_texts.items():
        cohort_actin_outputs[cohort_name] = map_to_actin(tagged_text, client, actin_rules)
    return cohort_actin_outputs

def parse_actin_output_to_json(trial_id: str, mapped_text: str) -> dict:
    pattern = r"Input:\s*(INCLUDE|EXCLUDE)\s+(.*?)\nACTIN Output:\s*(.*?)\nNew rule:"
    matches = re.findall(pattern, mapped_text, re.DOTALL)
    result = {
        "trial_id": trial_id,
        "mappings": []
    }
    for tag, input_text, actin_text in matches:
        cleaned_input = input_text.strip().replace('\n', ' ')
        cleaned_actin = re.sub(r'\)\s*\n\s*(AND|OR)\s*\n\s*\(', r') \1 (', actin_text.strip(), flags=re.IGNORECASE)
        cleaned_actin = re.sub(r'\s*\n\s*', ' ', cleaned_actin).strip()
        result["mappings"].append({
            "tag": tag,
            "input_text": cleaned_input,
            "ACTIN_rules": cleaned_actin
        })
    return result

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

    trial_id = trial_data["protocolSection"]["identificationModule"]["nctId"]

    if args.model == "Gemini":
        client = GeminiClient(TEMPERATURE)
    else:
        client = OpenaiClient(TEMPERATURE)

    cohort_actin_outputs = curate_actin(eligibility_criteria, actin_rules, client)

    full_text_output = []
    all_parsed_json = []
    for cohort_name, actin_output in cohort_actin_outputs.items():
        full_text_output.append(f"===== {cohort_name} =====\n{actin_output}\n")

        parsed_json = parse_actin_output_to_json(trial_id + f"::{cohort_name}", actin_output)
        all_parsed_json.append(parsed_json)

    txt_path = (args.out_trial_file.replace(".json", ".txt"))
    with open(txt_path, "w") as f:
        f.write("\n\n".join(full_text_output))

    with open(args.out_trial_file, "w") as f:
        json.dump(all_parsed_json, f, indent=2, ensure_ascii=False)

    logger.info(f"ACTIN results written to {args.out_trial_file} and {txt_path}")

if __name__ == "__main__":
    main()