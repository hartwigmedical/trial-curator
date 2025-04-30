import os
import re
import pandas as pd
import argparse
import logging
import sys

from trialcurator.openai_client import OpenaiClient
from trialcurator.gemini_client import GeminiClient
from trialcurator.utils import load_trial_data, load_eligibility_criteria
from trialcurator.eligibility_sanitiser import llm_sanitise_text, llm_extract_eligibility_groups, llm_extract_text_for_groups, llm_simplify_and_tag_text
from trialcurator.eligibility_curator_ACTIN import map_to_actin, load_actin_rules

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

logger.setLevel(logging.DEBUG)

TEMPERATURE = 0.0

def parse_mapped_blocks(mapped_text: str) -> list[dict]:
    """
    Parses the full mapped_output text into a list of {description, actin_mapping}.

    -- from: --
    Input:
        INCLUDE Age ≥ 18 years
    ACTIN Output:
        IS_AT_LEAST_X_YEARS_OLD[18]
    New rule:
        False

    -- to: --
    Key	                Value
    "Age ≥ 18 years"	IS_AT_LEAST_X_YEARS_OLD[18]
    """
    pattern = r"Input:\s*(.*?)\s*\n\s*ACTIN Output:\s*(.*?)\s*\n\s*New rule:"

    matches = re.findall(pattern, mapped_text, re.DOTALL)

    rows = []

    for full_description, actin_text in matches:

        cleaned_description = full_description.strip()
        cleaned_actin = re.sub(r'\)\s*\n\s*(AND|OR)\s*\n\s*\(', r') \1 (', actin_text.strip(), flags=re.IGNORECASE)
        cleaned_actin = re.sub(r'\s*\n\s*', ' ', cleaned_actin).strip()

        rows.append({
            "description": cleaned_description,
            "current mapping": cleaned_actin
        })

    return rows


def save_summary_tbl(trial_id: str, cohort: str, parsed_rows: list[dict], csv_path: str):

    rows = []

    if not parsed_rows:
        rows.append({
            "trialID": trial_id,
            "cohort": cohort,
            "description": "LLM parsing failed",
            "current mapping": "",
            "checked?": "",
            "override rule": ""
        })

    for row in parsed_rows:

        rows.append({
            "trialID": trial_id,
            "cohort": cohort,
            "description": row["description"],
            "current mapping": row["current mapping"],
            "checked?": "",
            "override rule": ""
        })

    df = pd.DataFrame(rows)

    if not os.path.exists(csv_path):
        df.to_csv(csv_path, index=False)
    else:
        df.to_csv(csv_path, mode='a', index=False, header=False)


def main():
    parser = argparse.ArgumentParser(description="Generate mapping summary table for clinical trials.")
    parser.add_argument('--model', help='Select between GPT and Gemini', required=True)
    parser.add_argument('--trial_json_dir', help='Directory containing input trial JSON files', required=False)
    parser.add_argument('--trial_json', help='Single trial JSON file', required=False)
    parser.add_argument('--out_csv', help='Output CSV file path', required=True)
    parser.add_argument('--ACTIN_path', help='Full path to ACTIN rules CSV', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    actin_rules = load_actin_rules(args.ACTIN_path)

    if args.model == "Gemini":
        client = GeminiClient(TEMPERATURE)
    else:
        client = OpenaiClient(TEMPERATURE)

    if args.trial_json:
        trial_files = [args.trial_json]
    elif args.trial_json_dir:
        trial_files = [
            os.path.join(args.trial_json_dir, f)
            for f in os.listdir(args.trial_json_dir)
            if f.endswith('.json')
        ]
    else:
        raise ValueError("You must provide either --trial_json or --trial_json_dir")

    logger.info(f"There are {len(trial_files)} trial files.\n")

    for trial_path in trial_files:
        logger.info(f"Processing {trial_path}...")
        trial_data = load_trial_data(trial_path)
        eligibility_criteria = load_eligibility_criteria(trial_data)
        trial_id = trial_data["protocolSection"]["identificationModule"]["nctId"]

        cleaned_text = llm_sanitise_text(eligibility_criteria, client)
        cohort_names = llm_extract_eligibility_groups(cleaned_text, client)
        cohort_texts = llm_extract_text_for_groups(cleaned_text, cohort_names, client)

        for cohort_name, cohort_criteria in cohort_texts.items():

            tagged_text = llm_simplify_and_tag_text(cohort_criteria, client)
            mapped_output = map_to_actin(tagged_text, client, actin_rules)

            parsed_rows = parse_mapped_blocks(mapped_output)  # <- new simpler parse

            save_summary_tbl(
                trial_id=trial_id,
                cohort=cohort_name,
                parsed_rows=parsed_rows,
                csv_path=args.out_csv
            )

    logger.info(f"Summary CSV written to {args.out_csv}")

if __name__ == "__main__":
    main()
