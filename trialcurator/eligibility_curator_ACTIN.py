import json
import re
import pandas as pd
import logging
import sys
import argparse

from trialcurator.tests.external.test_extract_eligibility_groups import get_test_data_path
from trialcurator.utils import load_trial_data, load_eligibility_criteria
from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient
from trialcurator.eligibility_sanitiser import llm_sanitise_text

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

logger.setLevel(logging.DEBUG)

TEMPERATURE = 0.0

def tag_inclusion_exclusion(eligibility_text: str) -> str:
    lines = eligibility_text.splitlines()
    tagged_lines = []
    current_tag = None

    for line in lines:
        stripped = line.strip()

        # Detect section headers
        if stripped.lower().startswith("inclusion criteria"):
            current_tag = "INCLUDE"
            continue
        elif stripped.lower().startswith("exclusion criteria"):
            current_tag = "EXCLUDE"
            continue

        if not stripped:
            continue

        # Detect bullet-style lines (hyphen or asterisk)
        if stripped.startswith("-") or stripped.startswith("*"):
            bullet_text = stripped[1:].strip()  # Remove leading '-' or '*'
            if current_tag:
                tagged_lines.append(f"{current_tag} {bullet_text}")
            else:
                tagged_lines.append(bullet_text)  # Fallback: no tag
        else:
            tagged_lines.append(stripped)

    return "\n".join(tagged_lines)


def load_actin_rules(rel_path) -> [str]:
    actin_rules = pd.read_csv(f"{get_test_data_path()}/{rel_path}", header=None)
    actin_rules = actin_rules[0].str.strip().tolist()
    return actin_rules

def map_to_actin(input_eligibility_criteria: str, client: LlmClient, rel_path: str) -> str:

    actin_rules = load_actin_rules(rel_path)

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
- Prefer using **general rules** (e.g. HAS_HAD_TREATMENT_WITH_ANY_DRUG_X) over disease-specific variants unless those are explicitly required.

LOGICAL STRUCTURE
- Use `OR` if multiple alternatives are valid (e.g., “histological OR cytological” confirmation).
    - Example:
      `(HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE OR HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE)`
- Use `AND` when all conditions must be met.
- Combine logical groups using parentheses when mixing `AND` and `OR`:
    - Correct: `(A OR B) AND C`
    - Incorrect: `A OR B AND C`

NUMERIC COMPARISON LOGIC
- “≥ X” → `IS_AT_LEAST_X[...]`
- “> X” → `IS_AT_LEAST_X[...]` with increased value if needed
- “≤ X” → `IS_AT_MOST_X[...]`
- “< X” → `IS_AT_MOST_X[...]` with decreased value if needed

FALLBACK RULES (use when no ACTIN rule matches)
- Treatment eligibility:  
    `IS_ELIGIBLE_FOR_TREATMENT_LINE_X[capecitabine + anti-VEGF antibody]`
- Gene rearrangement:  
    `HAS_GENE_REARRANGEMENT_IN_X[ROS1]`
- Broad compliance-impacting condition:  
    `NOT(HAS_SEVERE_CONCOMITANT_CONDITION)`
- Prior drug exposure (general):  
    `HAS_HAD_TREATMENT_WITH_ANY_DRUG_X[ros1 tyrosine kinase inhibitor]`

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
- Use `NOT(...)` for exclusion lines.
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
    INCLUDE Histologically or cytologically confirmed diagnosis of advanced NSCLC
ACTIN Output:
    (HAS_HISTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE
    OR
    HAS_CYTOLOGICAL_DOCUMENTATION_OF_TUMOR_TYPE)
    AND
    HAS_ADVANCED_NSCLC
New rule:
    False

"""
    user_prompt += "\nACTIN RULES:\n" + "\n".join(actin_rules)

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
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_file', help='output file containing trial data', required=True)
    parser.add_argument('--ACTIN_path', help='Relative path to ACTIN rules', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    trial_data = load_trial_data(args.trial_json)
    eligibility_criteria = load_eligibility_criteria(trial_data)

    try:
        trial_id = trial_data["protocolSection"]["identificationModule"]["nctId"]
    except KeyError:
        trial_id = "ERROR_UNKNOWN_TRIAL_ID"

    client = OpenaiClient(TEMPERATURE)

    curated_criteria = llm_sanitise_text(eligibility_criteria, client)
    curated_criteria = tag_inclusion_exclusion(curated_criteria)

    logger.debug(f"Tagged criteria:\n{curated_criteria}")

    mapped_output = map_to_actin(curated_criteria, client, args.ACTIN_path)

    with open(args.out_trial_file, "w") as f:
        f.write(mapped_output)

    parsed_json = parse_actin_output_to_json(trial_id, mapped_output)
    json_path = args.out_trial_file.replace(".txt", ".json")

    with open(json_path, "w") as f:
        json.dump(parsed_json, f, indent=2, ensure_ascii=False)

    logger.info(f"ACTIN JSON saved to {json_path}")

if __name__ == "__main__":
    main()
