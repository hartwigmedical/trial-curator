import inspect
import json
import logging
import re
from json import JSONDecodeError

from pydantic_curator.criterion_schema import BaseCriterion
from pydantic_curator import criterion_schema
from trialcurator.eligibility_curator_actin import load_actin_resource
from trialcurator.eligibility_sanitiser import llm_extract_cohort_tagged_text
from trialcurator.llm_client import LlmClient
from trialcurator.utils import load_trial_data, unescape_json_str, extract_code_blocks, batch_tagged_criteria_by_words
from trialcurator.openai_client import OpenaiClient

logger = logging.getLogger(__name__)

BATCH_MAX_WORDS = 60

CRITERION_TYPES = [re.search(r'.*\.(\w+)Criterion', str(c)).group(1) for c in BaseCriterion.__subclasses__()]

# We give instructions only when a given criterion type is present
INSTRUCTION_CRITERION_TYPES = {
    '- Use `PrimaryTumorCriterion` for tumor types and / or locations (e.g., melanoma, prostate).'
    : ['PrimaryTumor'],

    '- Use `MolecularBiomarkerCriterion` for expression-based biomarkers (e.g., PD-L1, HER2, IHC 3+).'
    : ['MolecularBiomarker'],

    '- Use `MolecularSignatureCriterion` for composite biomarkers or genomic signatures (e.g., MSI-H, TMB-H, HRD).'
    : ['MolecularSignature'],

    '- Use `GeneAlterationCriterion` for genomic alterations (e.g., EGFR mutation, ALK fusion). \
When specifying protein variants, always use the HGVS protein notation format.'
    : ['GeneAlteration'],

    '- Use `LabValueCriterion` for lab-based requirements that have lab measurement, unit, value, and operator.'
    : ['LabValue'],

    '- Use PrimaryTumorCriterion AND MolecularSignatureCriterion for tumor type with biomarker (e.g., "PD-L1-positive \
melanoma").'
    : ['PrimaryTumor', 'MolecularSignature'],

    '- Use HistologyCriterion only for named histologic subtypes (e.g., "adenocarcinoma", "squamous cell carcinoma", \
"mucinous histology"). Use PrimaryTumorCriterion together with HistologyCriterion for tumor types + histologic type. \
Multiple histology types must be seperated into multiple HistologyCriterion wrapped inside OrCriterion.'
    : ['Histology', 'PrimaryTumor'],

    '- DiagnosticFindingCriterion for statements like "histological confirmation of cancer", but use only PrimaryTumorCriterion \
if specific tumor type or location is mentioned (e.g., "histological confirmation of melanoma").'
    : ['DiagnosticFinding', ' PrimaryTumor', 'Histology'],

    '- Use SymptomCriterion only for symptom related to the tumor. Use ComorbidityCriterion for conditions not related \
to the tumor.'
    : ['Symptom'],

    '- Do not use PrimaryTumorCriterion for criteria involving other cancers or prior malignancies; instead, use \
ComorbidityCriterion with a condition like "other active malignancy" and specify a timeframe if provided.'
    : ['PrimaryTumorCriterion', 'Comorbidity'],

    '- Use PriorTherapyCriterion with timing_info for past treatment + timing. Only use IfCriterion if the text includes an \
explicit "if...then" or equivalent.'
    : ['PriorTherapy'],

    '- Use TreatmentOptionCriterion for requirements related to available, appropriate, or eligible treatments. In case of \
not amenable to or not eligible for a specific treatment, model it as a NotCriterion wrapping a TreatmentOptionCriterion.'
    : ['TreatmentOption'],

    '''- Use `ClinicalJudgementCriterion` only for subjective clinical assessment that are not defined or followed by \
objective measurements like lab values.
- If a criterion includes subjective or qualitative language (e.g., "adequate", "sufficient", "acceptable") but \
provides a concrete lab-based threshold (e.g., a named lab test with a value and unit), the *entire criterion* must be \
modeled as a `LabValueCriterion`. Do NOT use `ClinicalJudgementCriterion` in these cases.**'''
    : ['ClinicalJudgement', 'LabValue'],

    '- Use `OtherCriterion` when a criterion doesn’t clearly fit any other class, including study participation restrictions \
, population qualifiers, or general clinical appropriateness.'
    : ['Other']
}


def llm_curate_by_batch(eligibility_criteria: str, client: LlmClient) -> str:
    # split into small batches and curate
    criteria_batches = batch_tagged_criteria_by_words(eligibility_criteria, BATCH_MAX_WORDS)

    # split into batches
    curated_py_list = []
    for criteria_text in criteria_batches:
        while True:
            criteria_to_types: dict[str, list[str]] = categorise_criteria(criteria_text, client)

            # collect all the criteria types
            criteria_types = set([t for type_list in criteria_to_types.values() for t in type_list])

            curated = llm_curate_from_text(criteria_text, criteria_types, client)

            # this matches both "inclusion_criteria = [" and "inclusion_criteria: List[BaseCriterion] = ["
            # we want to extract just the criteria in the list
            m = re.search(r'inclusion_criteria.*(?::\s*List\[BaseCriterion])?= \[(.*)]', curated, flags=re.DOTALL)
            if m:
                result = m.group(1)
                # remove blank lines
                result = re.sub(r'^\s*\n|(\n\s*)+\Z', '', result, flags=re.MULTILINE)
                curated_py_list.append(result)
                logger.info(f'result = {result}')
                break
            else:
                logger.warning('unable to parse curation output, retrying')

    # join them back together
    return '[\n' + ',\n'.join(curated_py_list) + '\n]'


def categorise_criteria(tagged_criteria: str, client: LlmClient) -> dict[str, list[str]]:
    categories = [c for c in CRITERION_TYPES if c not in ['Not', 'And', 'Or', 'If']]

    system_prompt = f"""
You are an assistant that classifies eligibility criteria into relevant categories.
Each criterion belongs to one or more categories.

# Categories:
{categories}

# INSTRUCTIONS:
- Return a single JSON code block.
- Each eligibility criterion should be a key
- The value should be a list of matched categories
- Do NOT include any text outside the JSON

# Criterion Mapping Rules
- Use `PrimaryTumor` for tumor types and / or locations (e.g., melanoma, prostate).
- Use `MolecularBiomarker` for expression-based biomarkers (e.g., PD-L1, HER2, IHC 3+).
- Use `MolecularSignature` for composite biomarkers or genomic signatures (e.g., MSI-H, TMB-H, HRD).
- Use `GeneAlteration` for genomic alterations (e.g., EGFR mutation, ALK fusion).
- When specifying protein variants, always use the HGVS protein notation format.
- Use `LabValue` only for lab-based requirements that have lab measurement, unit, value, and operator.
- Use `PrimaryTumor` AND `MolecularSignature` for tumor type with biomarker (e.g., "PD-L1-positive melanoma").
- Use `Histology` only for named histologic subtypes (e.g., "adenocarcinoma", "squamous cell carcinoma", "mucinous histology").
- Use `DiagnosticFinding` for statements like "histological confirmation of cancer", but use only PrimaryTumorCriterion \
if specific tumor type or location is mentioned (e.g., "histological confirmation of melanoma").
- Use `Symptom` only for symptom related to the tumor. Use ComorbidityCriterion for conditions not related to the tumor.
- Use `ClinicalJudgement` only for subjective clinical assessment that are not defined or followed by objective \
measurements like lab values.
- Do not use `PrimaryTumor` for criteria involving other cancers or prior malignancies; instead, use \
`Comorbidity` with a condition like "other active malignancy" and specify a timeframe if provided.
- Use `PriorTherapy` for past treatment.
- Use `TreatmentOption` for requirements related to available, appropriate, amenability or eligible treatments.
- Use `Other` when a criterion doesn’t clearly fit any other class, including study participation restrictions \
, population qualifiers, or general clinical appropriateness.

Example:
```json
{{
    "INCLUDE Histologically or cytologically confirmed metastatic CRC": ["PrimaryTumor"],
    "EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment": ["Infection", "CurrentMedication"]
}}
```
"""

    user_prompt = f"""
Classify the following eligibility criterion:
```
{tagged_criteria}
```
"""
    llm_output = client.llm_ask(user_prompt, system_prompt)
    # print(f"Here are the categories {eligibility_criteria_w_category}")

    try:
        json_code_block = extract_code_blocks(llm_output, "json")
        criteria_categories = json.loads(json_code_block)
    except JSONDecodeError:
        user_prompt = f"""Fix up the following JSON:
{llm_output}
Return answer in a ```json code block```.
"""
        llm_output = client.llm_ask(user_prompt)
        json_code_block = extract_code_blocks(llm_output, "json")
        criteria_categories = json.loads(json_code_block)

    return criteria_categories


def llm_curate_from_text(criteria_text: str, criteria_types: set[str], client: LlmClient) -> str:
    #logger.info(f'criteria_types: {criteria_types}')

    criterion_mapping_rules = '\n'.join(
        [k for k, v in INSTRUCTION_CRITERION_TYPES.items() if criteria_types.intersection(set(v))])

    system_prompt = '''
You are an expert clinical trial curator. Your role is to convert unstructured inclusion and exclusion criteria into a \
structured format using a predefined Python schema.'''

    # print the clinical trial schema
    prompt = f'{extract_criterion_schema_classes(criteria_types)}\n'
    prompt += 'Create a python variable called inclusion_criteria of type `List[BaseCriterion]` to represent the following \
criteria:\n'
    prompt += f'''
```
{criteria_text}
```

INSTRUCTIONS:

# General
- Exclusion criteria must be expressed as inclusion criteria wrapped in a `NotCriterion`
- Top-level grouping requirement: For each top-level INCLUDE or EXCLUDE rule in the original text, generate exactly one \
top-level criterion, wrapping all relevant subconditions using AndCriterion, OrCriterion, or NotCriterion as needed.
- Assume gender is either male or female only. Use `if male ... else: ...` instead of checking both values.
- Answer should be given in a single code block with no explanation.

# Description field
- Top-level criteria: `description` field **must** capture the **full original text exactly as written**, including:
  - the `INCLUDE` or `EXCLUDE` tag at the beginning
  - sub-bullet points with original formatting.
- Non–top-level criteria: the description must provide a complete, self-contained explanation of the original criterion.
- The `description` field must always be populated for every criterion, including composite criteria.

# Confidence field
- Use `confidence` field to indicate the confidence level (0 - 1) that the curation is accurate. 1 being most confident.

# Composite Criterion
- When an inclusion criterion includes an embedded exception (e.g., “X excluding Y”), model it as X AND (NOT Y).
- Use IfCriterion for any conditional logic, explicit or implied (e.g., “if X then Y”, “Y in males, Z in females”, \
“≥10 if X-negative”). Never use AND for mutually exclusive criteria, use IF instead (e.g., “Y in males and X in females”).
- Always decompose any criterion containing multiple distinct conditions joined by logical conjunctions (“and”, “or”, \
“as well as”, “with”, “without”) into individual components, using:
  - AndCriterion for “and”-like phrases
  - OrCriterion for “or”-like phrases
Wrap elements in NotCriterion if they are part of a negation.

# Criterion Mapping Rules
{criterion_mapping_rules}

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
    clinical_trial_code = prepend_schema(clinical_trial_code, [])
    prompt = 'Given the following code:\n\n'
    prompt += f"```python\n{clinical_trial_code}\n```\n"
    prompt += '''
Refactor the above code with the following rules:
- Correct misuse of fields or criterion types (e.g., replacing OtherCriterion with a specific one if appropriate)
- Use IfCriterion for conditional thresholds, pay attention to trailing ifs (e.g. A if X, B if Y) 
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
        clinical_trial_code = llm_curate_by_batch(eligibility_criteria, llm_client)
        #clinical_trial_code = llm_refine_answer(clinical_trial_code, llm_client)
        group_eligibility_criteria[cohort] = clinical_trial_code

    # now put them all together into a nicely printed python code
    eligibility_criteria_code = '{\n'
    for g in group_eligibility_criteria.keys():
        clinical_trial_code = group_eligibility_criteria[g]
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


def extract_criterion_schema_classes(criterion_types: set[str] | list[str]) -> str:
    criterion_schema_code = inspect.getsource(criterion_schema)

    pattern = (
        r"(?:^[ \t]*#.*\n"  # Match comment lines
        r"|^[ \t]*@.*\n)*"  # Or decorators
        r"^class\s+(\w+)\(.*?\):\n"  # Class header
        r"(?:^[ \t]+.*\n)*"  # Class body (indented lines)
    )
    matches = re.finditer(pattern, criterion_schema_code, flags=re.MULTILINE)

    filtered_class_code = ""
    for match in matches:
        class_name = match.group(1)
        class_code = match.group()

        # always include BaseCriterion and non-Criterion classes
        if class_name == 'BaseCriterion' or \
                not class_name.endswith('Criterion') or \
                [t for t in criterion_types if t.lower() in class_name.lower()]:
            filtered_class_code += '\n' + class_code

    return filtered_class_code


# Prepend the schema if it is not added by the LLM. The reason is that
# it is necessary for the next stage to know the class definitions
def prepend_schema(trial_code: str, criterion_types: set[str]) -> str:
    trial_code = f'{extract_criterion_schema_classes(criterion_types)}\n{trial_code}'
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

    client = OpenaiClient()

    eligibility_criteria = load_eligibility_criteria(trial_data)
    cohort_texts = llm_extract_cohort_tagged_text(eligibility_criteria, client)
    clinical_trial_code = llm_curate_cohorts(cohort_texts, client)

    # write it out to the python file
    with open(args.out_trial_py, 'w', encoding='utf-8') as f:
        f.write(clinical_trial_code)

    # if we have ACTIN specified, curate it also
    if args.out_actin:
        actin_rules = load_actin_resource(args.actin_rules)
        cohort_actin = {}
        for cohort_name, tagged_text in cohort_texts.items():
            actin_mapping = actin_map_by_batch(tagged_text, client, actin_rules, BATCH_SIZE)
            cohort_actin[cohort_name] = actin_mapping
        # write to file
        with open(args.out_actin, "w") as f:
            json.dump(cohort_actin, f, indent=2)


if __name__ == "__main__":
    main()
