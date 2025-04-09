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

    # Examples to guide the LLM
    example_block = """
Input: 
    EXCLUDE Has ongoing androgen deprivation with serum testosterone <50 ng/dL
ACTIN Output:
    HAS_ONGOING_ANDROGEN_DEPRIVATION_WITH_TESTOSTERONE_BELOW_X_NG_DL[50]
New rule: 
    HAS_ONGOING_ANDROGEN_DEPRIVATION_WITH_TESTOSTERONE_BELOW_X_NG_DL[50]

Input: 
    INCLUDE Male patients aged 18 years and older
ACTIN Output:
    IS_MALE
    AND
    IS_AT_LEAST_X_YEARS_OLD[18]
New rule:
    False
    
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
"""

    system_prompt = """
You are a clinical trial curation assistant.
Your task is to convert each free-text eligibility criterion into structured ACTIN rules.

TASKS
- Translate each line into one or more ACTIN rules.
- Match to the existing ACTIN RULE LIST.
- If no match exists, create a new rule name with full details and parameters.
- Mark new rules by writing them fully after `New rule:`.

RULE MATCHING GUIDELINES
- Match based on **rule name structure**, not the values inside [brackets].
- A rule is NOT new if the rule name already exists in ACTIN — even if values differ.
- Do NOT mark rules as new if the only change is in bracket values, wording, or synonyms.

LOGICAL COMPARISON LOGIC
Use these conversions for numeric conditions:
- “≥ X” → `IS_AT_LEAST_X[...]`
- “> X”  → `IS_AT_LEAST_X[...] + 1` (or a custom rule if needed)
- “≤ X” → `IS_AT_MOST_X[...]`
- “< X”  → `IS_AT_MOST_X[...] - 1` (or a custom rule if needed)

EXAMPLES:
- “Excludes weight over 150 kg” → `NOT(HAS_BODY_WEIGHT_OF_AT_LEAST_X[150])`
- “Hemoglobin < 5” → `NOT(HAS_HEMOGLOBIN_OF_AT_LEAST_X[5])`
- “Creatinine > 1.5 x ULN” → `NOT(HAS_CREATININE_ULN_OF_AT_MOST_X[1.5])`

FALLBACK RULES FOR NEW CONDITIONS
Use standard formats when no rule exists:
- Eligibility for specific regimens:
    `IS_ELIGIBLE_FOR_TREATMENT_LINE_X[capecitabine + anti-VEGF antibody]`
- Broad condition impacting compliance (e.g., mental illness, severe disease):
    `NOT(HAS_SEVERE_CONCOMITANT_CONDITION)`

LOGIC & FORMATTING
- Use AND, OR, and NOT as needed.
- Use NOT(...) for exclusion lines.
- Group conditions with parentheses when mixing AND and OR.
- Use square brackets for rule parameters.
- Place each logical operator on its own line.
- Never summarize, paraphrase, or omit important qualifiers.

OUTPUT FORMAT
Each item must follow this format:

Input: 
    [original line]
ACTIN Output:
    [structured rule logic]
New rule:
    [False OR full rule name]
"""

    user_prompt = """
You are given a list of eligibility criteria, each starting with INCLUDE or EXCLUDE.

For each line:
- Generate the matching ACTIN rules.
- Use NOT(...) for EXCLUDE-tagged conditions.
- Follow comparison logic and fallback rules when needed.

FORMAT REQUIREMENTS
Input: 
    [original line]
ACTIN Output:
    [ACTIN_RULE_1]
    AND
    [ACTIN_RULE_2]
New rule:
    [False OR full rule name]
"""

    user_prompt += f"""
EXAMPLES:
{example_block}
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
