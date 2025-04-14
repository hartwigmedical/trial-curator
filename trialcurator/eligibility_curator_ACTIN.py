import json
import re
import pandas as pd
import logging
import sys
import argparse

from trialcurator.openai_client import OpenaiClient
from trialcurator.gemini_client import GeminiClient
from trialcurator.tests.external.test_extract_eligibility_groups import get_test_data_path
from trialcurator.utils import load_trial_data, load_eligibility_criteria
from trialcurator.llm_client import LlmClient
from trialcurator.eligibility_sanitiser import llm_sanitise_text, llm_extract_eligibility_groups, llm_extract_text_for_groups

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
    INCLUDE Patient has advanced NSCLC
ACTIN Output:
    HAS_ADVANCED_NSCLC
New rule:
    HAS_ADVANCED_NSCLC
    
Input: 
    INCLUDE Documented ROS1 gene rearrangement
ACTIN Output:
    FUSION_IN_GENE_X[ROS1]
New rule:
    False
    
Input: 
    INCLUDE Must be able to swallow oral tablets
ACTIN Output:
    CAN_SWALLOW_ORAL_TABLETS
New rule:
    CAN_SWALLOW_ORAL_TABLETS
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
    parser.add_argument('--model', help='Select between GPT and Gemini', required=True)
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_file', help='output file containing trial data', required=True)
    parser.add_argument('--ACTIN_path', help='Relative path to ACTIN rules', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    trial_data = load_trial_data(args.trial_json)
    eligibility_criteria = load_eligibility_criteria(trial_data)

    trial_id = trial_data["protocolSection"]["identificationModule"]["nctId"]

    if args.model == "GPT":
        client = OpenaiClient(TEMPERATURE)
    elif args.model == "Gemini":
        client = GeminiClient(TEMPERATURE)
    else: # OpenAI's model is the fallback/standard option
        client = OpenaiClient(TEMPERATURE)


    cleaned_text = llm_sanitise_text(eligibility_criteria, client)
    cohort_names = llm_extract_eligibility_groups(cleaned_text, client)
    cohort_texts = llm_extract_text_for_groups(cleaned_text, cohort_names, client)

    logger.info(f"Processing cohorts: {list(cohort_texts.keys())}")

    full_text_output = []
    all_parsed_json = []

    for cohort_name, cohort_criteria in cohort_texts.items():

        tagged_text = tag_inclusion_exclusion(cohort_criteria)
        logger.debug(f"Tagged criteria for {cohort_name}:\n{tagged_text}")

        mapped_output = map_to_actin(tagged_text, client, args.ACTIN_path)
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
