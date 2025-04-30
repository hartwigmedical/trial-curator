import json
import re
import pandas as pd
import logging
import sys
import argparse

from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient
from trialcurator.gemini_client import GeminiClient
from trialcurator.utils import load_trial_data, load_eligibility_criteria
from trialcurator.eligibility_sanitiser import llm_sanitise_text, llm_extract_eligibility_groups, llm_extract_text_for_groups, llm_simplify_and_tag_text

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

logger.setLevel(logging.DEBUG)

TEMPERATURE = 0.0

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
    
EXCLUSION LOGIC (important):
- For every EXCLUDE line, the **entire logical condition** must be wrapped in a single `NOT(...)`, even if rule names already sound negative (e.g., IS_PREGNANT).
- For EXCLUDE lines, always use:
    NOT(condition1 AND condition2 AND ...)  ← preferred
    or
    NOT(condition)  ← if it's a single clause

Never write:
    - NOT(A OR B) ← this is logically incorrect
    - NOT(A) OR NOT(B) ← also incorrect
Correct alternatives:
    - NOT(A AND B)
    - NOT(A) AND NOT(B)

Examples:
    - EXCLUDE pregnant or lactating
      → NOT(IS_PREGNANT AND IS_BREASTFEEDING)

- Do NOT omit NOT(...) even if the rule is inherently adverse or negative in name (e.g., HAS_ACTIVE_INFECTION, IS_PREGNANT).

NUMERIC COMPARISON LOGIC
- “≥ X” → `IS_AT_LEAST_X[...]`
- “> X” → `IS_AT_LEAST_X[...]` with increased value if needed
- “≤ X” → `IS_AT_MOST_X[...]`
- “< X” → `IS_AT_MOST_X[...]` with decreased value if needed

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

FORMATTING
- Use square brackets `[...]` for rule parameters.
- Use `AND`, `OR`, and `NOT` on their own lines.
- Never paraphrase or omit important medical or logical detail.
- Do not mark rules as new unless they introduce a completely new rule name.

MULTI-LINE DEFINITIONS
- If a criterion contains a header phrase (e.g. "Inadequate organ function, defined as:") and is followed by lab values or other restrictive bullets,
  treat the **entire definition as one unit**.
- Do NOT map the header line alone. Instead, include the lab values or bullet clauses beneath it when generating ACTIN rules.
- If the bullet points are multiple lab values under EXCLUDE, treat them as part of a compound exclusion clause (see numeric logic).

EXCLUSION LOGIC FOR NUMERIC CONDITIONS
- For EXCLUDE criteria that list abnormal lab values (e.g., “Hemoglobin < 5”, “Creatinine > 1.5”):
    1. First, rewrite each clause in its POSITIVE form using `IS_AT_LEAST_X[...]` or `IS_AT_MOST_X[...]` as per the comparison.
    2. Then determine the correct NOT logic:

    - If the exclusion condition lists multiple abnormal values connected with **OR**:
        Wrap the ENTIRE positive condition in a single `NOT(...)` with **AND** logic inside.
        Example:
        EXCLUDE Hemoglobin <5 OR Neutrophils <1.5
        → NOT(
              HAS_HEMOGLOBIN_MMOL_PER_L_OF_AT_LEAST_X[5]
              AND
              HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X[1.5]
          )

    - If the exclusion clause uses **AND** (i.e., ALL of the abnormal values must be true to exclude), apply `NOT(...)` to each positive clause and connect with OR.
        Example:
        EXCLUDE Bilirubin >2 AND ALT >3
        → NOT(HAS_TOTAL_BILIRUBIN_ULN_OF_AT_MOST_X[2])
          OR
          NOT(HAS_ALT_ULN_OF_AT_MOST_X[3])
          
SPECIAL CASES: Combined Measurements
- If the condition mentions a combined category (e.g., "Liver transaminases >5 ULN"), assume it refers to **both AST and ALT**, unless otherwise specified.
- Do NOT write:
    NOT(HAS_ASAT_ULN...) OR NOT(HAS_ALAT_ULN...)
- Instead, write:
    NOT(HAS_ASAT_ULN_OF_AT_MOST_X[5] AND HAS_ALAT_ULN_OF_AT_MOST_X[5])
    OR
    Use the combined ACTIN rule if one exists:
    NOT(HAS_ASAT_AND_ALAT_ULN_OF_AT_MOST_X_OR_AT_MOST_Y_WHEN_LIVER_METASTASES_PRESENT[5, 5])

COMMON MISTAKES TO AVOID
- Do not write: NOT(A OR B) → this implies patient must be BOTH A and B to be excluded, which is incorrect
- Do not use OR between NOT(...) clauses → logically wrong
- Always express EXCLUSION as NOT(A AND B), or NOT(single condition)
- Do NOT fabricate details that are not explicitly or implicitly stated in the original line (e.g., do not infer specific drug classes like corticosteroids unless they are mentioned).

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
    NOT(
        HAS_HEMOGLOBIN_MMOL_PER_L_OF_AT_LEAST_X[5]
        AND
        HAS_NEUTROPHILS_ABS_OF_AT_LEAST_X[1.5]
    )
New rule:
    False

Input:
    EXCLUDE Liver transaminases >5 x ULN
ACTIN Output:
    NOT(HAS_ASAT_ULN_OF_AT_MOST_X[5]
    AND
    HAS_ALAT_ULN_OF_AT_MOST_X[5])
New rule:
    False
    
Input:
    EXCLUDE Pregnant or lactating women.
ACTIN Output:
    NOT(IS_PREGNANT AND IS_BREASTFEEDING)
New rule:
    False
    
OR, if either condition is sufficient to exclude:
ACTIN Output:
    NOT(IS_PREGNANT)
    AND
    NOT(IS_BREASTFEEDING)
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

    cleaned_text = llm_sanitise_text(eligibility_criteria, client)
    cohort_names = llm_extract_eligibility_groups(cleaned_text, client)
    cohort_texts = llm_extract_text_for_groups(cleaned_text, cohort_names, client)

    logger.info(f"Processing cohorts: {list(cohort_texts.keys())}")

    full_text_output = []
    all_parsed_json = []

    for cohort_name, cohort_criteria in cohort_texts.items():

        tagged_text = llm_simplify_and_tag_text(cohort_criteria, client)
        logger.debug(f"Tagged criteria for {cohort_name}:\n{tagged_text}")

        mapped_output = map_to_actin(tagged_text, client, actin_rules)
        full_text_output.append(f"===== {cohort_name} =====\n{mapped_output}\n")

        parsed_json = parse_actin_output_to_json(trial_id + f"::{cohort_name}", mapped_output)
        all_parsed_json.append(parsed_json)

    txt_path = (args.out_trial_file.replace(".json", ".txt"))
    with open(txt_path, "w") as f:
        f.write("\n\n".join(full_text_output))

    with open(args.out_trial_file, "w") as f:
        json.dump(all_parsed_json, f, indent=2, ensure_ascii=False)

    logger.info(f"ACTIN results written to {args.out_trial_file} and {txt_path}")

if __name__ == "__main__":
    main()
