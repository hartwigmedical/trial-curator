import inspect
import json
import logging
import sys
import re

from . import criterion_schema
from .eligibility_curator_ACTIN import curate_actin, map_to_actin, load_actin_rules
from .eligibility_sanitiser import llm_extract_cohort_tagged_text
from .llm_client import LlmClient
from .utils import load_trial_data, unescape_json_str, extract_code_blocks
from .openai_client import OpenaiClient

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.INFO)

TEMPERATURE = 0.0
TOP_P = 1.0

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

def llm_curate_from_text(eligibility_criteria: str, client: LlmClient) -> str:
    system_prompt = '''
You are an expert clinical trial curator. Your role is to convert unstructured inclusion and exclusion criteria into a \
structured format using a predefined Python schema.'''

    # print the clinical trial schema
    prompt = f'{inspect.getsource(criterion_schema)}\n'
    prompt += 'Create a python variable called inclusion_criteria of type `List[BaseCriterion]` to represent the following \
    inclusion criteria:\n'
    prompt += f"```\n{eligibility_criteria}\n```\n"
    prompt += '''
INSTRUCTIONS:

# General
- Focus strictly on the inclusion and exclusion criteria. Ignore descriptive text, background context, and non-requirement \
statements.
- Exclusion criteria should be expressed as inclusion criteria wrapped in a `NotCriterion`, unless otherwise specified \
(see below).
- DO NOT create a separate list for exclusion criteria.
- Answer should be given in a single code block with no explanation.

# Special Cases
- When an inclusion criterion includes an embedded exception (e.g., “X excluding Y”), model it as X AND (NOT Y).
- If a criterion contains **conditional logic** (e.g. “if X, then Y”, “Y if X”), express it using an `IfCriterion`.
- If a criterion involves multiple distinct conditions (e.g., disease and medication), model each separately using the \
appropriate class (e.g., ComorbidityCriterion, PriorMedicationCriterion) and combine them with AndCriterion.
- The above also apply to criteria wrapped inside NotCriterion.

# Criterion Mapping Rules
- Use `PrimaryTumorCriterion` for tumor types and locations (e.g., melanoma).
- Use `MolecularBiomarkerCriterion` for expression-based biomarkers (e.g., PD-L1, HER2, IHC 3+).
- Use `MolecularSignatureCriterion` for composite biomarkers or genomic signatures (e.g., MSI-H, TMB-H, HRD).
- Use `GeneAlterationCriterion` for genomic alterations (e.g., EGFR mutation, ALK fusion).
- When specifying protein variants, always use the HGVS protein notation format.
- Use `LabValueCriterion` for lab-based requirements with a measurement, unit, value, and operator.
- Use PrimaryTumorCriterion AND MolecularSignatureCriterion for tumor type with biomarker (e.g., "PD-L1-positive melanoma").
- Use HistologyCriterion only for named histologic subtypes (e.g., "adenocarcinoma", "squamous cell carcinoma", "mucinous histology").
- DiagnosticFindingCriterion for statements like "histological confirmation of cancer".
- Use SymptomCriterion only for symptom related to the tumor. Use ComobidityCriterion for conditions not related to the tumor.
- Use ClinicalJudgementCriterion for subjective clinical assessment by investigators, rather than objective measurements \
like lab values.
- Do not use PrimaryTumorCriterion for criteria involving other cancers or prior malignancies; instead, use \
ComorbidityCriterion with a condition like "other active malignancy" and specify a timeframe if provided.
- Use PriorTherapyCriterion with timing_info for past treatment + timing. Only use IfCriterion if the text includes an \
explicit "if...then" or equivalent.
- Use TreatmentOptionCriterion for requirements related to available, appropriate, or eligible treatments. In case of \
not amenable to or not eligible for a specific treatment, model it as a NotCriterion wrapping a TreatmentOptionCriterion.
- Use `OtherCriterion` when a criterion doesn’t clearly fit any other class, including study participation restrictions,\
 population qualifiers, or general clinical appropriateness.

# Description field
- The `description` field must always be populated for every criterion, including composite criteria.
- For top-level criteria, the description must include the **entire criterion text**, including any INCLUDE or EXCLUDE tags.
- For non–top-level criteria, the description must provide a complete, self-contained explanation of the original criterion.
- Do not leave the description field empty under any circumstances.
'''

    response = client.llm_ask(prompt, system_prompt=system_prompt)

    python_code = extract_code_blocks(response, 'python')

    return python_code


def llm_refine_answer(clinical_trial_code: str, client: LlmClient) -> str:
    system_prompt = '''
You are a clinical trial curation validator and assistant. Your task is to review and improve Python objects representing \
structured eligibility criteria based on a predefined schema (modeled using Pydantic).
These objects are created from free-text eligibility criteria in oncology trials.'''

    # make sure the schema is included
    clinical_trial_code = prepend_schema_if_missing(clinical_trial_code)
    prompt = 'Given the following code:\n\n'
    prompt += f"```python\n{clinical_trial_code}\n```\n"
    prompt += '''
Refactor the above code with the following rules:
- Correct misuse of fields or criterion types (e.g., replacing OtherCriterion with a specific one if appropriate)
- Improve logical structure (e.g., use IfCriterion for conditional thresholds)
- Wrap any criterion expressing negation (e.g., using "not", "no") in a `NotCriterion`, and populate the `description` \
field accordingly.
- Standardize field values (e.g., stage names, confirmation methods)
- Preserve the original meaning exactly — do not infer or generalize beyond what is stated
- For criteria that contain disjunctive logic (e.g., "X or Y"), split them into individual criteria and wrap them in an \
`OrCriterion`.
- For medication criteria that contain disjunctive medications (e.g., "X or Y"), split them into individual medication \
criteria and wrap them in an `OrCriterion`.
- Normalize lab value expressions to use `"x ULN"` for any upper limit of normal comparisons.
- Adhere strictly to the pydantic schema defined in the code. Pay special care that mandatory fields are set.
- Return a single code block with no explanation.
'''

    response = client.llm_ask(prompt, system_prompt)
    python_code = extract_code_blocks(response, 'python')

    return python_code


def llm_curate_cohorts(group_text: dict, llm_client: LlmClient) -> str:
    group_eligibility_criteria = {}

    for cohort, eligibility_criteria in group_text.items():
        logger.info(f'cohort: {cohort}')
        clinical_trial_code = llm_curate_from_text(eligibility_criteria, llm_client)
        clinical_trial_code = llm_refine_answer(clinical_trial_code, llm_client)
        group_eligibility_criteria[cohort] = clinical_trial_code

    # now put them all together into a nicely printed python code
    eligibility_criteria_code = '{\n'
    for g in group_eligibility_criteria.keys():
        clinical_trial_code = group_eligibility_criteria[g]
        # strip out anything before `inclusion_criteria = ` or `inclusion_criteria: List[BaseCriterion] = `
        clinical_trial_code = remove_up_to(clinical_trial_code,
                                           r'(inclusion_criteria\s*(?::\s*List\[BaseCriterion\])?\s*=.*)').strip()
        # indent the code
        clinical_trial_code = clinical_trial_code.replace("\n", "\n    ")
        eligibility_criteria_code += f'    "{g}": {clinical_trial_code},\n'
    eligibility_criteria_code += '}'

    return eligibility_criteria_code


def load_eligibility_criteria(trial_data):
    protocol_section = trial_data['protocolSection']
    eligibility_module = protocol_section['eligibilityModule']
    return unescape_json_str(eligibility_module['eligibilityCriteria'])


#def remove_up_to(text: str, search: str) -> str:
#    _, _, after = text.partition(search)
#    return after

def remove_up_to(text, pattern):
    match = re.search(pattern, text)
    if match:
        # Return everything from the match onward
        return text[match.end() - 1:]
    else:
        return ""


# Prepend the schema if it is not added by the LLM. The reason is that
# it is necessary for the next stage to know the class definitions
def prepend_schema_if_missing(trial_code: str) -> str:
    search_str = 'class BaseCriterion'
    if search_str not in trial_code:
        trial_code = f'{inspect.getsource(criterion_schema)}\n{trial_code}'
        if search_str not in trial_code:
            raise Exception(f'{search_str} not in schema')
    return trial_code


def parse_actin_output_to_json(cohort: str, mapped_text: str) -> dict:
    pattern = r"Input:\s*(INCLUDE|EXCLUDE)\s+(.*?)\nACTIN Output:\s*(.*?)\nNew rule:"
    matches = re.findall(pattern, mapped_text, re.DOTALL)

    result = {
        "cohort": cohort,
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
    import argparse
    parser = argparse.ArgumentParser(description="Clinical trial curator")
    parser.add_argument('--trial_json', help='json file containing trial data', required=True)
    parser.add_argument('--out_trial_py', help='output python file containing trial data', required=True)
    parser.add_argument('--out_actin', help='output text file containing ACTIN trial data', required=False)
    parser.add_argument('--actin_rules', help='path to ACTIN rules CSV', required=False)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    trial_data = load_trial_data(args.trial_json)

    client = OpenaiClient(TEMPERATURE, TOP_P)

    eligibility_criteria = load_eligibility_criteria(trial_data)
    cohort_texts = llm_extract_cohort_tagged_text(eligibility_criteria, client)
    clinical_trial_code = llm_curate_cohorts(cohort_texts, client)

    # write it out to the python file
    with open(args.out_trial_py, 'w', encoding='utf-8') as f:
        f.write(clinical_trial_code)

    # if we have ACTIN specified, curate it also
    if args.out_actin:
        actin_rules = load_actin_rules(args.actin_rules)
        cohort_actin = []
        for cohort_name, tagged_text in cohort_texts.items():
            actin_output = map_to_actin(tagged_text, client, actin_rules)
            actin_json = parse_actin_output_to_json(cohort_name, actin_output)
            cohort_actin.append(actin_json)
        # write to file
        with open(args.out_actin, "w") as f:
            json.dump(cohort_actin, f, indent=2)

if __name__ == "__main__":
    main()
