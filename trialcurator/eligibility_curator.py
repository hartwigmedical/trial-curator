import inspect
import logging
import sys

from trialcurator import clinical_trial_schema
from trialcurator.eligibility_sanitiser import llm_sanitise_text, llm_extract_eligibility_groups, \
    llm_extract_text_for_groups
from trialcurator.llm_client import LlmClient
from trialcurator.utils import load_trial_data, unescape_json_str, extract_code_blocks
from trialcurator.openai_client import OpenaiClient

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
    prompt = f'{inspect.getsource(clinical_trial_schema)}\n'
    prompt += 'Create an object called inclusion_criteria to represent the following inclusion criteria:\n'
    prompt += f"```\n{eligibility_criteria}\n```\n"
    prompt += '''
INSTRUCTIONS:

#### General
- Focus strictly on the inclusion and exclusion criteria. Ignore descriptive text, background context, and non-requirement statements.
- Exclusion criteria should be expressed as inclusion criteria wrapped in a `NotCriterion`, unless otherwise specified (see below).
- Use the `description` field to store a complete, self-contained explanation of the original criterion.
- DO NOT create a separate object for exclusion criteria.
- Answer should be given in a single code block with no explanation.

#### Negation Handling
- Do **not** use `NotCriterion` for double negatives. Instead, resolve them logically into their positive equivalent.
  - Example: “Exclude patients who do not have X” → model as just “have X”.
- Do **not** use `NotCriterion` for negated comparisons (e.g., "exclude patients < X"). Convert to the logically equivalent positive form (e.g., "≥ X").

#### Special Cases
- When an inclusion criterion includes an embedded exception (e.g., “X excluding Y”), model it as X AND (NOT Y).
- If a criterion contains **conditional logic** (“if X, then Y”), express it using an `IfCriterion`.

#### Criterion Mapping Rules
- Use `PrimaryTumorCriterion` for tumor types and locations (e.g., melanoma, colorectal carcinoma, small cell lung).
- Use `MolecularBiomarkerCriterion` for expression-based biomarkers (e.g., PD-L1, HER2, IHC 3+).
- Use `MolecularSignatureCriterion` for composite biomarkers or genomic signatures (e.g., MSI-H, TMB-H, HRD).
- Use `GeneAlterationCriterion` for genomic alterations (e.g., EGFR mutation, ALK fusion).
- When specifying protein variants, always use the HGVS protein notation format.
- Use `LabValueCriterion` for lab-based requirements with a measurement, unit, value, and operator.
- Use PrimaryTumorCriterion AND MolecularSignatureCriterion for tumor type with biomarker (e.g., "PD-L1-positive melanoma").
- Use SymptomCriterion only for symptom related to the tumor. Use ComobidityCriterion for conditions not related to the tumor.
- Use ClinicalJudgementCriterion for clinical judgement such as adequate organ function.
- Do not use PrimaryTumorCriterion for criteria involving other cancers or prior malignancies; instead, use \
ComorbidityCriterion with a condition like "other active malignancy" and specify a timeframe if provided.
- Use `OtherCriterion` when a criterion doesn’t clearly fit any other class, including study participation restrictions,\
 population qualifiers, or general clinical appropriateness.
'''

    response = client.llm_ask(prompt, system_prompt=system_prompt)

    python_code = extract_code_blocks(response, 'python')

    return python_code

def llm_refine_answer(clinical_trial_code: str, client: LlmClient) -> str:

    system_prompt = '''
You are a clinical trial curation validator and assistant. Your task is to review and improve Python objects representing structured eligibility criteria based on a predefined schema (modeled using Pydantic).
These objects are created from free-text eligibility criteria in oncology trials.'''

    # make sure the schema is included
    clinical_trial_code = prepend_schema_if_missing(clinical_trial_code)
    prompt = 'Given the following code:\n\n'
    prompt += f"```python\n{clinical_trial_code}\n```\n"
    prompt += '''
Refactor the above code with the following rules:
- Correct misuse of fields or criterion types (e.g., replacing OtherCriterion with a specific one if appropriate)
- Improve logical structure (e.g., use IfCriterion for conditional thresholds, combine related rules with AndCriterion)
- Wrap any criterion expressing negation (e.g., using "not", "no") in a `NotCriterion`, and populate the `description` field accordingly.
- Standardize field values (e.g., stage names, confirmation methods)
- Preserve the original meaning exactly — do not infer or generalize beyond what is stated
- For criteria that contain disjunctive logic (e.g., "X or Y"), split them into individual criteria and wrap them in an `OrCriterion`.
- For medication criteria that contain disjunctive medications (e.g., "X or Y"), split them into individual medication criteria and wrap them in an `OrCriterion`.
- Normalize lab value expressions to use `"x ULN"` for any upper limit of normal comparisons.
- Adhere strictly to the pydantic schema defined in the code. Pay special care that mandatory fields are set.
- Return a single code block with no explanation.
'''

    response = client.llm_ask(prompt, system_prompt)
    python_code = extract_code_blocks(response, 'python')

    return python_code

'''
Simplify lab values
'''
def llm_simplify(clinical_trial_code: str, client: LlmClient) -> str:
    system_prompt = '''You are a clinical trial curation validator and assistant. Your task is to review and improve \
    Python objects representing structured eligibility criteria based on a predefined schema (modeled using Pydantic)'''

    clinical_trial_code = prepend_schema_if_missing(clinical_trial_code)
    prompt = 'Given the following code:\n\n'
    prompt += f"```python\n{clinical_trial_code}\n```\n"
    prompt += '''
Refactor the above code with the following improvements:
- Simplify `LabValueCriterion` or `AgeCriterion` expressions inside `NotCriterion`, e.g., `NOT(x > 20)` → `x <= 20`, make sure the meaning is unchanged.
- Simplify disjunctive `LabValueCriterion` inside `NotCriterion`, e.g., `NOT(x > 20 OR y > 10)` → `x <= 20 AND y <= 10`, make sure the meaning is unchanged.
- For criteria nested inside `NotCriterion`, fill in `description` text to the inner criteria, removing negation from the wording. Do not modify the `NotCriterion` description.
- Replace generic `OtherCriterion` inside `NotCriterion` with more specific types (e.g., `ComorbidityCriterion`, `MetastasesCriterion`) when applicable.
- Adhere strictly to the pydantic schema defined in the code.
- Return only a single code block as the output, with no explanation.
- Fill in the timing_info field of `PriorTherapyCriterion` and `PriorMedicationCriterion` if available from description.
'''

    response = client.llm_ask(prompt, system_prompt)
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