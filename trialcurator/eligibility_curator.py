import inspect
import json
import logging
import re
import sys

from trialcurator import clinical_trial_schema
from trialcurator.llm_client import LlmClient
from trialcurator.openai_client import OpenaiClient

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.INFO)

logger.setLevel(logging.DEBUG)

#openai.api_key = os.environ["OPENAI_API_KEY"]

TEMPERATURE = 0.0
TOP_P = 0.1

'''
documentation of common errors that I cannot easily fix by changing prompt:
 - "Male or female" becomes just male
 - "No concomitant anti-tumor therapy" not enclosed in NotCriterion
 - 
 - Sometimes uses measurement unit 10*9/L instead of 10^9/L
'''

# we perform 3 stage curation
# 1: separation into cohorts
# 2: curate each cohort criteria
# 3: refine criteria cause gemini struggle to use the full extent of the schema

def load_trial_data(json_file: str) -> dict:
    with open(json_file, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
        #logger.info(json.dumps(json_data, indent=2))
        return json_data

# for eligibility that have different groups, extract them
def llm_sanitise_text(eligibility_criteria: str, client: LlmClient) -> str:

    logger.info(f"eligibility criteria: {eligibility_criteria}")

    prompt = 'Following are the eligibility criteria for a clinical trial:\n'
    prompt += f"```\n{eligibility_criteria}\n```\n"
    prompt += '''### Instructions for Sanitization of Eligibility Criteria Text
- Remove any criteria related to informed consent.
- Only include criteria that explicitly define inclusion or exclusion rules.
- Remove permissive statements such as "X is allowed" or "Y may be permitted.".
- Fix typos and misspellings, especially in units, medical terms, and lab tests.
- Use ^ for power instead of superscript, i.e. 10^9 instead of 10⁹.
- For lab values, use x for times instead of * or times, i.e. "5 x" instead of "5 *" or "5 times".
- Use uppercase L for liter units (e.g., "mg/dL").
- Replace well-known phrases with accepted abbreviations where appropriate, i.e. "ECOG", "HIV", "HBV", "HCV", "x ULN".
- If a criterion includes multiple conditions that can logically stand alone, separate them into distinct bullet points.
- Maintain consistent bulleting and indentation, use `-` instead of `*` for bullet points.
- Maintain distinct eligibility groups (cohorts, parts, phase etc) if provided in the original text.
- Answer in one text block with no additional explanation.
'''

    sanitised_text = client.llm_ask(prompt).replace("```", "")
    return sanitised_text

# for eligibility that have different groups, extract them
def llm_extract_eligibility_groups(eligibility_criteria: str, client: LlmClient) -> list[str]:

    logger.info(eligibility_criteria)

    #prompt = 'Following are the eligibility criteria for a clinical trial:\n'
    prompt = f"```\n{eligibility_criteria}\n```\n"

    '''Instructions:
- Some clinical trials have different eligibility criteria for distinct groups (e.g., part, phase, cohort). Extract the names of these eligibility groups into a JSON list of strings, but only if they are explicitly defined and have mutually exclusive eligibility criteria.
- If no distinct eligibility groups are described, return a single-item list containing "default".
- Only list groups that have distinct eligibility criteria.
- Do not create general or umbrella groups that overlap or encompass more specific groups.
- Each group must be mutually exclusive and based only on criteria that differ from other groups.
- Answer should be a single code block with no explanation.'''

    prompt += '''
You are given the inclusion and exclusion criteria of a clinical trial. Some trials define different eligibility groups (e.g., "part 1", "cohort A", "phase 2") with distinct sets of eligibility criteria.
**Your task** is to extract the names of these eligibility groups into a JSON list of strings, but only under the following conditions:
1. Explicit Labeling: Only extract a group if it is explicitly named (e.g., "part 1", "cohort A", "arm B"). Do not infer or create names.
2. Distinct Eligibility Criteria: Groups must have meaningfully different eligibility criteria.
3. Preserve Full Group Names: If a group name contains a parent group and one or more subgroups, capture the full hierarchical name as it appears in the text. For example, if the text uses a structure like:
   - `GROUP A:`  
     under which appears `Subtype X:`  
     and then `Condition Y`,  
   then the group name should be extracted as: "GROUP A: Subtype X: Condition Y"
   Do NOT shorten this to `"GROUP A"` or `"Subtype X"`.
4. Do NOT Merge or Generalize:
   - Do NOT create general or umbrella categories (e.g., combining "part 1" and "part 2" into one group).
   - Do NOT list a group just because it has a different name — the criteria must actually differ.
5. Default Case: If no distinct groups are defined, return a single-item list `["default"]`
**Output format**: Return a JSON array of group names (as strings), exactly as they appear in the text (e.g., `"part 1"`, `"cohort A"`).
    '''
    response = client.llm_ask(prompt)

    try:
        eligibility_groups = json.loads(extract_code_blocks(response, 'json'))
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to decode JSON from response text: {e}")
        eligibility_groups = []

    logger.info(f"found the following eligibility groups: {eligibility_groups}")
    return eligibility_groups

# for eligibility that have different parts, extract them
def llm_extract_text_for_groups(eligibility_criteria: str, groups: [str], client: LlmClient) -> dict:

    prompt = 'Following are the eligibility criteria for a clinical trial:\n'
    prompt += f"```\n{eligibility_criteria}\n```\n"
    prompt += '''Given the above clinical trial eligibility criteria and list of cohort-specific eligibility groups:
Eligibility Groups: '''
    prompt += json.dumps(groups, indent=2)
    prompt += '''
Instructions:
- Extract the eligibility criteria (both general and group-specific inclusion/exclusion criteria) for each group.
- Each group's criteria should be self-contained: include all relevant general and group-specific criteria.
- Return the result in a JSON object with the format:
{
  "GROUP NAME": "Eligibility text...",
  ...
}
- The eligibility text should maintain consistent bulleting and indentation.
- Remove references to the eligibility group names themselves. e.g. "Inclusion Criteria (Cohort 1)". should be changed to just "Inclusion Criteria".
- Output only the final JSON (no explanation or extra text).
'''
    response = client.llm_ask(prompt)

    try:
        group_text_dict = json.loads(extract_code_blocks(response, 'json'))
        # replace \\n with \n, not sure why this is needed
        for g in group_text_dict.keys():
            group_text_dict[g] = unescape_json_str(group_text_dict[g])
        logger.info(f"group text dict: {group_text_dict}")

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to decode JSON from response text: {e}")
        group_text_dict = {}

    return group_text_dict

def llm_curate_from_text(eligibility_criteria: str, client: LlmClient) -> str:

    # print the clinical trial schema
    prompt = f'{inspect.getsource(clinical_trial_schema)}\n'
    prompt += 'Create an object called inclusion_criteria to represent the following inclusion criteria:\n'
    prompt += f"```\n{eligibility_criteria}\n```\n"
    prompt += '''
Remember:
- Focus strictly on the inclusion and exclusion criteria and ignore any descriptive or background details and non requirements.
- Exclusion criteria should be converted into inclusion criteria with negation using the `NotCriterion` class.
- DO NOT create a separate object for exclusion criteria.
- `description` field should be complete and self-contained.
- Ensure that criterion to do with lab measurements use the `LabValueCriterion` class.
- Do not use PrimaryTumorCriterion for criteria involving other cancers or prior malignancies; instead, use ComorbidityCriterion with a condition like "other active malignancy" and specify a timeframe if provided.
- Use PrimaryTumorCriterion for any mention of tumor type (e.g., "melanoma", "solid tumor", "lymphoma").
- Use MolecularCriterion for biomarker-based eligibility (e.g., "PD-L1-positive") and set gene and alteration accordingly.
- Use PrimaryTumorCriterion AND MolecularCriterion for tumor type with biomarker (e.g., "PD-L1-positive melanoma"). 
- Answer should be given in a single code block with no explanation.'''

    response = client.llm_ask(prompt)

    python_code = extract_code_blocks(response, 'python')

    return python_code

def llm_refine_answer(clinical_trial_code: str, client: LlmClient) -> str:

    # make sure the schema is included
    clinical_trial_code = prepend_schema_if_missing(clinical_trial_code)
    prompt = 'Given the following code:\n\n'
    prompt += f"```python\n{clinical_trial_code}\n```\n"
    prompt += '''
Refactor the above code with the following rules:
- Replace instances of `OtherCriterion` with more specific criterion types where applicable.
- Wrap any criterion expressing negation (e.g., using "not", "no") in a `NotCriterion`, and populate the `description` field accordingly.
- For criteria that contain disjunctive logic (e.g., "X or Y"), split them into individual criteria and wrap them in an `OrCriterion`.
- For medication criteria that contain disjunctive medications (e.g., "X or Y"), split them into individual medication criteria and wrap them in an `OrCriterion`.
- Normalize lab value expressions to use `"x ULN"` for any upper limit of normal comparisons.
- Return a single code block with no explanation.
'''

    response = client.llm_ask(prompt)
    python_code = extract_code_blocks(response, 'python')

    return python_code

'''
Simplify lab values
'''
def llm_simplify(clinical_trial_code: str, client: LlmClient) -> str:

    clinical_trial_code = prepend_schema_if_missing(clinical_trial_code)
    prompt = 'Given the following code:\n\n'
    prompt += f"```python\n{clinical_trial_code}\n```\n"
    prompt += '''
Refactor the above code with the following improvements:
- Simplify `LabValueCriterion` expressions inside `NotCriterion`, e.g., `NOT(x > 20)` → `x <= 20`, make sure the meaning is unchanged.
- Simplify disjunctive `LabValueCriterion` inside `NotCriterion`, e.g., `NOT(x > 20 OR y > 10)` → `x <= 20 AND y <= 10`, make sure the meaning is unchanged.
- For criteria nested inside `NotCriterion`, fill in `description` text to the inner criteria, removing negation from the wording. Do not modify the `NotCriterion` description.
- Replace generic `OtherCriterion` inside `NotCriterion` with more specific types (e.g., `ComorbidityCriterion`, `MetastasesCriterion`) when applicable.
- Make sure the code conforms to the pydantic schema.
- Return only a single code block as the output, with no explanation.
'''

    response = client.llm_ask(prompt)
    python_code = extract_code_blocks(response, 'python')
    print(f"refined python code = {python_code}")

    return python_code

def llm_curate_groups(group_text: dict, llm_client: LlmClient) -> str:

    group_eligibility_criteria = {}

    for g in group_text.keys():
        logger.info(f'eligibility group: {g}')
        eligibility_criteria = group_text[g]
        clinical_trial_code = llm_curate_from_text(eligibility_criteria, llm_client)
        clinical_trial_code = llm_refine_answer(clinical_trial_code, llm_client)
        clinical_trial_code = llm_simplify(clinical_trial_code, llm_client)
        group_eligibility_criteria[g] = clinical_trial_code

    # now put them all together into nicely printed python code
    eligibility_criteria_code = '{\n'
    for g in group_eligibility_criteria.keys():
        clinical_trial_code = group_eligibility_criteria[g]
        # strip out anything before inclusion_criteria =
        clinical_trial_code = remove_up_to(clinical_trial_code, "inclusion_criteria = ").strip()
        # indent the code
        clinical_trial_code = clinical_trial_code.replace("\n", "\n    ")
        eligibility_criteria_code += f'    "{g}": {clinical_trial_code},\n'
    eligibility_criteria_code += '}'

    return eligibility_criteria_code

def load_eligibility_criteria(trial_data):
    protocol_section = trial_data['protocolSection']
    eligibility_module = protocol_section['eligibilityModule']
    return unescape_json_str(eligibility_module['eligibilityCriteria'])

def remove_up_to(text: str, search: str) -> str:
    _, _, after = text.partition(search)
    return after

# Prepend the schema if it is not added by the LLM. The reason is that
# it is necessary for the next stage to know the class definitions
def prepend_schema_if_missing(trial_code: str) -> str:
    search_str = 'class BaseCriterion'
    if search_str not in trial_code:
        trial_code = f'{inspect.getsource(clinical_trial_schema)}\n{trial_code}'
        if search_str not in trial_code:
            raise Exception(f'{search_str} not in schema')
    return trial_code

def extract_code_blocks(text: str, lang: str) -> str:
    """
    Extracts and returns a list of <lang> code snippets found within
    i.e. triple backtick Python code blocks (```python ... ```).
    """
    pattern = re.compile(r"```" + lang + "(.*?)```", re.DOTALL)
    return "".join(pattern.findall(text))

# not sure why it is not unescaped correctly
def unescape_json_str(json_str: str) -> str:
    return (json_str.replace("\\'", "'")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\>", ">")
            .replace("\\<", "<")
            .replace("\\[", "[")
            .replace("\\]", "]"))

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Clinical trial curator")
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_py', help='output python file containing trial data', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    trial_data = load_trial_data(args.trial_json)

    client = OpenaiClient(TEMPERATURE, TOP_P)

    eligibility_criteria = load_eligibility_criteria(trial_data)

    eligibility_criteria = llm_sanitise_text(eligibility_criteria, client)
    groups = llm_extract_eligibility_groups(eligibility_criteria, client)
    group_text = llm_extract_text_for_groups(eligibility_criteria, groups, client)

    # curate each part separately
    clinical_trial_code = llm_curate_groups(group_text, client)

    # write it out to the python file
    with open(args.out_trial_py, 'w', encoding='utf-8') as f:
        f.write(clinical_trial_code)


if __name__ == "__main__":
    main()