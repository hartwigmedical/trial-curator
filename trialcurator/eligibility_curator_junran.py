import csv
import inspect
import json
import logging
import re
import sys
import argparse

from trialcurator import clinical_trial_schema
from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

logger.setLevel(logging.DEBUG)

TEMPERATURE = 0.0
TOP_P = 1.0


def unescape_json_str(json_str: str) -> str:
    return (json_str.replace("\\'", "'")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\>", ">")
            .replace("\\<", "<")
            .replace("\\[", "[")
            .replace("\\]", "]"))

def load_eligibility_criteria(trial_data):

    protocol_section = trial_data['protocolSection']
    eligibility_module = protocol_section['eligibilityModule']
    eligibility_criteria = eligibility_module['eligibilityCriteria']

    return unescape_json_str(eligibility_criteria)


def load_trial_data(json_file: str) -> dict:

    with open(json_file, 'r', encoding='utf-8') as f:

        json_data = json.load(f)

        return json_data



# Stage 1a: Preliminary cleaning
def clean_raw_text(input_eligibility_criteria: str, client: LlmClient) -> str:

    # logger.info(f"input eligibility criteria: {input_eligibility_criteria}")

    system_prompt = """
                    You are a preprocessing assistant that cleans up messy clinical trial eligibility criteria text.
                    Your job is to sanitize and normalize formatting so the text is easier to parse.
                    Do not rewrite the content or interpret medical meaning.
                    There must be no loss of information.
                    """

    user_prompt = """
                    Please clean up the eligibility criteria text below:
                    
                    - Normalize all bullet point markers to a consistent format using '~*'
                    - Remove extra spaces between '~' and '*' unless it is a correct sub-point.
                    - Keep lines like '~* For Cohorts A, B, C' clearly intact.
                    - Ensure each bullet starts on a new line.
                    - Fix misspellings.
                    - Do not change the meaning of the text.
                    - Preserve section headers exactly, such as 'Inclusion Criteria:' and 'Exclusion Criteria:' — these must remain on their own line and unchanged.
                    - If 'Inclusion Criteria:' or 'Exclusion Criteria:' appear inside a bullet, move them to their own line as standalone headers before the relevant section starts.
                    
                    What follows is the input eligibility criteria:
                    """
    user_prompt += f"\n{input_eligibility_criteria}\n"

    sanitised_text_1 = client.llm_ask(user_prompt, system_prompt)

    logger.info(f"Primary text cleaning: {sanitised_text_1}")
    return sanitised_text_1


# Stage 1b: Secondary check - particularly to distinguish between inclusion & exclusion criteria
def validate_and_fix_formatting(sanitised_text_1: str, client: LlmClient) -> str:

    system_prompt = """
                    You are a formatting validator and fixer for structured clinical trial output.
                    Your job is to make sure both 'Inclusion Criteria:' and 'Exclusion Criteria:' headers are on their own line,
                    and that they are consistently formatted across all cohorts.
                    """
    user_prompt = """
                    Please fix the formatting below so that both
                    'Inclusion Criteria:' and 'Exclusion Criteria:' appear as headers on their own lines, and not inline or attached to bullet points."
                    
                    What follows is the eligibility criteria:
                    """
    user_prompt += f"\n{sanitised_text_1}\n"

    sanitised_text_2 = client.llm_ask(user_prompt, system_prompt)

    logger.info(f"Secondary text cleaning: {sanitised_text_2}")
    return sanitised_text_2

# Stage 3a: Map to ACTIN rules
def load_actin_rules(file_path: str) -> str:
    """Reads and flattens ACTIN rules from CSV file into a newline-separated bullet list."""
    with open(file_path, newline="") as csvfile:
        reader = csv.reader(csvfile)
        next(reader)  # Skip header
        rules: Set[str] = {cell.strip() for row in reader for cell in row if cell.strip()}
    return "\n".join(f"- {rule}" for rule in sorted(rules))


def map_to_ACTIN(sanitised_text_2: str, client: LlmClient) -> str:
    # Path to your ACTIN rules
    actin_file = "/Users/junrancao/PycharmProjects/analysis/trialcurator/tests/external/data/ACTIN_test_cases/ACTIN_CompleteList_03042025.csv"
    actin_rule_text = load_actin_rules(actin_file)

    # Example block to guide the LLM with the exact pattern you want
    example_block = """
Examples:

Input: Patients with a known history of stroke within the last 6 months
Mapped ACTIN Rule(s): NOT(HAS_HISTORY_OF_STROKE_WITHIN_X_MONTHS[6])

Input: Male patients aged 18 years and older
Mapped ACTIN Rule(s): IS_MALE AND IS_AT_LEAST_X_YEARS_OLD[18]

Input: Documented results of the presence of an Epidermal Growth Factor Receptor (EGFR) exon 20 insertion mutation in tumor tissue or blood from local or central testing.
Mapped ACTIN Rule(s): MUTATION_IN_GENE_X_IN_EXON_Y_OF_TYPE_Z[EGFR, 20, INSERT]
"""

    system_prompt = """
You are an expert curator of clinical trials.
Your task is to map each condition in free text into one or more of the machine-interpretable rules called ACTIN.
Use logical operators like AND, OR, and NOT.
If no appropriate rule exists, return: "No ACTIN rules available".
Do NOT create new rules outside of the list.
Format your output clearly and consistently as shown in the examples.
"""

    user_prompt = f"""
Below is the input eligibility criteria, divided into 'Inclusion' and 'Exclusion' sections:

{sanitised_text_2}

Your task:
- For each input line, map it to zero or more ACTIN rules.
- Use "NOT(...)" for exclusion criteria.
- If no rule fits, return: "No ACTIN rules available".
- Match formatting from the examples.

ACTIN RULES:
{actin_rule_text}

{example_block}

Now begin mapping the lines from the input criteria:
"""

    actin_rules = client.llm_ask(user_prompt, system_prompt)

    logger.info(f"Mapping to ACTIN:\n{actin_rules}")
    return actin_rules



def main():

    parser = argparse.ArgumentParser(description="Clinical trial curator")
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_file', help='output file containing trial data', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    trial_data = load_trial_data(args.trial_json)

    client = OpenaiClient(TEMPERATURE, TOP_P)

    eligibility_criteria = load_eligibility_criteria(trial_data)

    eligibility_criteria = clean_raw_text(eligibility_criteria, client)
    eligibility_criteria = validate_and_fix_formatting(eligibility_criteria, client)
    eligibility_criteria = map_to_ACTIN(eligibility_criteria, client)

    # with open(args.out_trial_file, "w") as f:
    #
    #     json.dump(eligibility_criteria, f, indent=2)

    with open(args.out_trial_file, "w") as f:

        if isinstance(eligibility_criteria, str):
            f.write(eligibility_criteria)

        else:
            # Fallback: convert to string with newlines (e.g., if it's a list)
            f.write("\n".join(str(line) for line in eligibility_criteria))


if __name__ == "__main__":
    main()



# 03 April - Only focus on single-cohort ACTIN trials for now
'''
# Stage 2a: Cohort identification
def cohort_identification(sanitised_text_2: str, client: LlmClient) -> str:

    system_prompt = """
                    You are an expert curator of medical clinical trials.
                    Your task is to extract cohort-level eligibility criteria
                    Always preserve inclusion/exclusion distinctions and treat each cohort independently.
                    Do not include any explanations, introductions, or commentary. Only return the final output.
                    """

    user_prompt = """
                CONTEXT:
                - You are given clinical trial eligibility criteria that may contain a single patient cohort or multiple cohorts.
                
                TASK:
                - Identify and extract each cohort (e.g. 'Part 1', 'Phase 1', 'Cohort A', 'SINGLE AGENT - HNSCC', etc) separately.
                - If a section includes multiple subgroup descriptions, treat each as its own cohort.
                
                OUTPUT FORMAT:
                    Cohort 1
                    Title: [Descriptive Title]
                    Inclusion Criteria:
                    ...
                    "Exclusion Criteria:
                    ...
                    
                RULES:
                - Always use numeric labels ('1', '2', '3', etc).
                - Do not reference other cohorts. Repeat shared criteria for each cohort.
                - Do not group multiple cohorts under a single label.
                - Criteria following 'Exclusion Criteria:' must be treated as exclusion only.
                - Return plain text only, no markdown or commentary.
                """
    user_prompt += f"\n{sanitised_text_2}\n"

    cohorts_identified = client.llm_ask(user_prompt, system_prompt)

    logger.info(f"Cohorts identification: {cohorts_identified}")
    return cohorts_identified


# Stage 2b: Assign cohort into JSON format
def extract_code_blocks(text: str, lang: str) -> str:
    """
    Extracts and returns a list of <lang> code snippets found within
    i.e. triple backtick Python code blocks (```python ... ```).
    """
    pattern = re.compile(r"```" + lang + "(.*?)```", re.DOTALL)
    return "".join(pattern.findall(text))

def cohort_json(cohorts_identified: str, client: LlmClient) -> list[str]:

    system_prompt = """
                    You are an expert curator of medical clinical trials.
                    Your task is assign eligibility criteria per cohort from free text to json format.
                    Do not include any explanations, introductions, or commentary. Only return the final output.
                    """

    user_prompt = """
                Below is the trial eligibility criteria divided into self-contained cohorts
                """
    user_prompt += f"```\n{cohorts_identified}\n```\n"

    user_prompt += """
                TASK:
                - Return the result in a JSON object with the format:
                {
                  "COHORT": "Eligibility text...",
                  ...
                }
                - The eligibility text should maintain consistent bulleting and indentation.
                - Output only the final JSON (no explanation or extra text).    
                """

    cohorts_identified_json = client.llm_ask(user_prompt, system_prompt)

    try:
        cohorts_identified_json = json.loads(extract_code_blocks(cohorts_identified_json, 'json'))
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to decode JSON from response text: {e}")
        cohorts_identified_json = []

    logger.info(f"found the following groups: {cohorts_identified_json}")
    return cohorts_identified_json
'''