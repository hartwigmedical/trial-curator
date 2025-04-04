import pandas as pd
import logging
import sys
import argparse

from trialcurator.tests.external.test_extract_eligibility_groups import get_test_data_path
from trialcurator.utils import load_trial_data, load_eligibility_criteria
from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

logger.setLevel(logging.DEBUG)

TEMPERATURE = 0.0


# Stage 1a: Preliminary cleaning
def clean_raw_text(input_eligibility_criteria: str, client: LlmClient) -> str:

    logger.info(f"input eligibility criteria: {input_eligibility_criteria}")

    input_eligibility_criteria = input_eligibility_criteria.replace("**", "")

    system_prompt = """
                    You are a text normalization assistant for clinical trial data.
                    
                    Your job is to clean and reformat messy eligibility criteria to improve consistency and structure.
                    
                    You must preserve all medical content. Do not summarize, rephrase, or interpret clinical meaning.
                    
                    Only correct formatting, spelling, spacing, and bullet alignment.
                    """

    user_prompt = """
                    Please clean the eligibility criteria text below using the following rules:
                    
                    - Normalize all bullet points to use '-' consistently.
                    - Ensure each bullet starts on a new line.
                    - Remove excess whitespace around bullet markers (e.g., '~ *' → '~*').
                    - Preserve any lines like '~* For Cohorts A, B, C' exactly as-is.
                    - Correct obvious typos and standardize spelling where needed.
                    - **Do not change any clinical terminology or meaning.**
                    - Ensure 'Inclusion Criteria:' and 'Exclusion Criteria:' are on their own line and not inside any bullet point.
                    - If either header appears inside a bullet, extract it to its own line before the relevant section.
                    
                    What follows is the input eligibility criteria:
                    """
    user_prompt += f"\n{input_eligibility_criteria}\n"

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)

    logger.info(f"Primary text cleaning: {output_eligibility_criteria}")
    return output_eligibility_criteria


# Stage 1b: Secondary check - particularly to distinguish between inclusion & exclusion criteria
def validate_and_fix_formatting(input_eligibility_criteria: str, client: LlmClient) -> str:

    logger.info(f"input eligibility criteria: {input_eligibility_criteria}")

    system_prompt = """
                    You are a formatting assistant for clinical trial data.
                    
                    Your role is to ensure that structural elements—especially headers like 'Inclusion Criteria:' and 'Exclusion Criteria:'—are correctly formatted as standalone lines.
                    
                    Guidelines:
                    - Do not modify the medical meaning, language, or terminology.
                    - Do not rewrite or summarize any content.
                    - Only adjust formatting to ensure consistency and parsing readiness.
                    """

    user_prompt = """
                    Your task:
                    
                    - Ensure that 'Inclusion Criteria:' and 'Exclusion Criteria:' each appear on their own line.
                    - These headers must not be embedded within any bullet or sentence (e.g., "- Inclusion Criteria: ...").
                    - If found inline, extract and reposition the header onto its own line, directly above the related section.
                    - Do not remove, duplicate, or alter any eligibility content during this process.
                    
                    Below is the input eligibility criteria text:
                    """
    user_prompt += f"\n{input_eligibility_criteria}\n"

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)

    logger.info(f"Secondary text cleaning: {output_eligibility_criteria}")
    return output_eligibility_criteria

# Stage 1c: Remove permissive conditions
def remove_permissive_conditions(input_eligibility_criteria: str, client: LlmClient) -> str:

    logger.info(f"input eligibility criteria: {input_eligibility_criteria}")

    system_prompt = """
                    You are a clinical trial eligibility criteria filtering assistant.
                    
                    Your task is to identify and remove permissive or non-restrictive statements from eligibility text. These statements do not define actual inclusion or exclusion conditions.
                    
                    Examples of what to remove:
                    - Statements that say something is allowed, permitted, or acceptable (e.g., "X is allowed", "Y may be permitted", "Z are eligible").
                    - Descriptive or contextual lines that provide background but do not restrict who can join the trial.
                    
                    Guidelines:
                    - Never remove lines that impose restrictions or define participant requirements.
                    - Do not rewrite or paraphrase any criteria.
                    - Only delete full lines that are clearly permissive or descriptive.
                    - Do not alter or reformat valid inclusion/exclusion criteria or their headers.
                    """

    user_prompt = """
                    Please clean the following eligibility criteria by removing any non-restrictive or permissive statements.
                    
                    Instructions:
                    - Remove lines that do not impose any requirement or restriction (e.g., "Patients may be eligible if...", "X is allowed", "Y may be permitted").
                    - Keep only strict inclusion or exclusion rules.
                    - Do not change valid headers (e.g., 'Inclusion Criteria:', 'Exclusion Criteria:').
                    - Do not rephrase or modify any remaining lines — only delete clearly permissive ones.
                    
                    Input text:
                    """
    user_prompt += f"\n{input_eligibility_criteria}\n"

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)

    logger.info(f"Secondary text cleaning: {output_eligibility_criteria}")
    return output_eligibility_criteria


# Stage 2a: Map to ACTIN rules
def load_actin_rules(rel_path) -> [str]:
    actin_rules = pd.read_csv(f"{get_test_data_path()}{rel_path}", header=None)
    actin_rules = actin_rules[0].str.strip().tolist()
    return actin_rules

def map_to_ACTIN(input_eligibility_criteria: str, client: LlmClient, rel_path: str) -> str:

    actin_rules = load_actin_rules(rel_path)

    # Examples to guide the LLM
    example_block = """
                    Input: Has ongoing androgen deprivation with serum testosterone <50 ng/dL
                    ACTIN Output:
                    HAS_ONGOING_ANDROGEN_DEPRIVATION_WITH_TESTOSTERONE_BELOW_X_NG_DL[50]
                    New rule: HAS_ONGOING_ANDROGEN_DEPRIVATION_WITH_TESTOSTERONE_BELOW_X_NG_DL[50]
                    
                    Input: Male patients aged 18 years and older
                    ACTIN Output:
                    IS_MALE
                    AND
                    IS_AT_LEAST_X_YEARS_OLD[18]
                    New rule: False
                    """

    system_prompt = """
                    You are a clinical trial curation assistant.
                    
                    Your job is to convert each free-text eligibility criterion into structured ACTIN rules for programmatic matching.
                    
                    Your tasks:
                    - Translate each line into one or more ACTIN rules.
                    - Match to existing rules from the ACTIN RULE LIST below.
                    - If no exact match exists, create a new rule with a full descriptive name.
                    
                    IMPORTANT:
                    - If a new rule is created, you must write it in full after `New rule:` like this:
                      New rule: HAS_TREATMENT_FREE_INTERVAL_OF_AT_LEAST_X_MONTHS[12]
                    - Do NOT just say `New rule: True` — that is considered an incomplete answer.
                    - Include ALL medically relevant details in the new rule name, using ACTIN naming conventions.
                    
                    IMPORTANT RULE VALIDATION:
                    - A rule is **not new** if its structure matches an existing ACTIN rule name — even if the values inside square brackets (e.g., [EGFR, 20, INSERTION]) differ.
                    - For example, if the rule `MUTATION_IN_GENE_X_IN_EXON_Y_OF_TYPE_Z` already exists in the ACTIN rule list, then:
                        - `MUTATION_IN_GENE_X_IN_EXON_Y_OF_TYPE_Z[EGFR, 20, INSERT]` → Not a new rule
                        - `MUTATION_IN_GENE_X_IN_EXON_Y_OF_TYPE_Z[EGFR, 20, INSERTION]` → Also **not** a new rule
                    - The *rule name* (before the `[ ]`) is what determines whether it's new — **not** the specific values or parameters inside the brackets.
                    - Never mark a rule as new based solely on different values, synonyms, formatting, or capitalization inside `[ ]`.
                    
                    Logical Formatting:
                    - Use AND, OR, and NOT as needed.
                    - Use **parentheses** to group logical expressions **whenever AND and OR are mixed**.
                        Example: (A OR B) AND C
                        Wrong: A OR B AND C
                    - Use parentheses around any multi-condition expressions to avoid ambiguity.
                    - Use square brackets for parameters: e.g., IS_AT_LEAST_X_YEARS_OLD[18]
                    
                    Example structure:
                    Input: [original text]
                    ACTIN Output:
                    [ACTIN_RULE_1]
                    AND
                    [ACTIN_RULE_2]
                    New rule: [False or full rule name]
                    
                    Do not summarize, paraphrase, or omit qualifiers. Focus only on precise logical mappings.
                """

    user_prompt = """
                You are given free-text eligibility criteria, organized into Inclusion and Exclusion sections.
                
                Your job:
                - For each line, return:
                    Input: [original text]
                    ACTIN Output: [structured ACTIN logic]
                    New rule: [False OR a new ACTIN rule name with parameters]
                
                Formatting Notes:
                - Use AND/OR/NOT with parentheses as needed.
                - Always wrap grouped expressions in parentheses when combining AND and OR.
                    Example:
                    (HAS_LOCALLY_ADVANCED_CANCER OR HAS_METASTATIC_CANCER) AND HAS_NON_SQUAMOUS_NSCLC
                    HAS_LOCALLY_ADVANCED_CANCER OR HAS_METASTATIC_CANCER AND HAS_NON_SQUAMOUS_NSCLC
                - Use square brackets for any parameters, e.g., [18], [EGFR, 20, INSERT].
                - Place each operator (AND/OR) on its own line for clarity.
                - Preserve the distinction between inclusion and exclusion — use NOT(...) for exclusions.
                - Follow the format in the examples below.
                """

    user_prompt += "\nACTIN RULES:\n" + "\n".join(actin_rules)

    user_prompt += f"""
                EXAMPLES:
                {example_block}
                
                Now map the following eligibility criteria:
                {input_eligibility_criteria}
                """

    output_eligibility_criteria = client.llm_ask(user_prompt, system_prompt)

    output_eligibility_criteria = output_eligibility_criteria.replace("```","")

    logger.info(f"Mapping to ACTIN:\n{output_eligibility_criteria}")
    return output_eligibility_criteria


def main():
    parser = argparse.ArgumentParser(description="Clinical trial curator")
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_file', help='output file containing trial data', required=True)
    parser.add_argument('--ACTIN_path', help='Relative path to ACTIN rules', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    trial_data = load_trial_data(args.trial_json)
    eligibility_criteria = load_eligibility_criteria(trial_data)

    client = OpenaiClient(TEMPERATURE)

    eligibility_criteria = clean_raw_text(eligibility_criteria, client)
    eligibility_criteria = validate_and_fix_formatting(eligibility_criteria, client)

    eligibility_criteria = remove_permissive_conditions(eligibility_criteria, client)

    eligibility_criteria = map_to_ACTIN(eligibility_criteria, client, args.ACTIN_path)

    with open(args.out_trial_file, "w") as f:
        f.write(eligibility_criteria)

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