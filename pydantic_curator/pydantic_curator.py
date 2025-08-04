import json
import logging
import argparse
import re
from json import JSONDecodeError
from typing import NamedTuple

from .pydantic_curator_utils import extract_criterion_schema_classes, clean_curated_output
from .criterion_schema import BaseCriterion

from trialcurator.llm_client import LlmClient
from trialcurator.eligibility_text_preparation import llm_rules_prep_workflow
from trialcurator.utils import load_trial_data, unescape_json_str, extract_code_blocks
from trialcurator.openai_client import OpenaiClient

logger = logging.getLogger(__name__)

# NOTE this is not the same as the one used in loading the dataframe
# the reason is that the curation is a BaseCriterion instead of str
class RuleOutput(NamedTuple):
    rule_text: str
    exclude: bool
    flipped: bool
    cohorts: list[str] | None
    curation: str

CRITERION_TYPES = [re.search(r'.*\.(\w+)Criterion', str(c)).group(1) for c in BaseCriterion.__subclasses__()]

# We give instructions only when a given criterion type is present
INSTRUCTION_CRITERION_TYPES = {
    '- Use `PrimaryTumorCriterion` for tumor types and / or locations under current study (e.g., melanoma, prostate).'
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
ComorbidityCriterion with a condition like "other active malignancy".'
    : ['PrimaryTumorCriterion', 'Comorbidity'],

    '- Use PriorTreatmentCriterion for past treatments. Multiple prior treatments must be separated into separate \
PriorTreatmentCriterion objects enclosed in appropriate OrCriterion / AndCriterion.'
    : ['PriorTreatment'],

    '- Use CurrentTreatmentCriterion for current treatments. Multiple current treatments must be separated into separate \
CurrentTreatmentCriterion objects enclosed in appropriate OrCriterion / AndCriterion.'
    : ['CurrentTreatment'],

    '- Use TreatmentOptionCriterion for requirements related to available, appropriate, or eligible treatments. In case of \
not amenable to or not eligible for a specific treatment, model it as a NotCriterion wrapping a TreatmentOptionCriterion.'
    : ['TreatmentOption'],

    '''- Use `ClinicalJudgementCriterion` only for subjective clinical assessment that are not defined or followed by \
objective measurements like lab values.
- If a criterion includes subjective or qualitative language (e.g., "adequate", "sufficient", "acceptable") but \
provides a concrete lab-based threshold (e.g., a named lab test with a value and unit), the *entire criterion* must be \
modeled as a `LabValueCriterion`. Do NOT use `ClinicalJudgementCriterion` in these cases.**
- comorbidity must be modelled using `ComorbidityCriterion` and not `ClinicalJudgementCriterion`'''
    : ['ClinicalJudgement', 'LabValue'],

    '- Use `OtherCriterion` for criteria that do not fit any other types defined in the schema.'
    : ['Other']
}


def llm_categorise_criteria(tagged_criteria: str, client: LlmClient) -> dict[str, list[str]]:
    categories = [c for c in CRITERION_TYPES if c not in ['Not', 'And', 'Or', 'If']]

    system_prompt = f"""
You are an assistant that classifies eligibility criteria into relevant categories.
Each criterion belongs to one or more categories.

# Categories:
{categories}

# INSTRUCTIONS:
- Each eligibility criterion should be a key
- The value should be a list of matched categories
- Be inclusive in classification—if there is any uncertainty, err on the side of including a potentially relevant category.
- Return a single JSON code block. Do NOT include any text outside the JSON

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
- Use `PriorTreatment` for past treatment.
- Use `TreatmentOption` for requirements related to available, appropriate, amenability or eligible treatments.
- Use `Sex` if gender (e.g. male, female) is mentioned.
- Use `Other` when a criterion doesn’t clearly fit any other category, including study participation restrictions \
, population qualifiers, or general clinical appropriateness.

Example:
```json
{{
    "INCLUDE Histologically or cytologically confirmed metastatic CRC": ["PrimaryTumor"],
    "EXCLUDE Known HIV, active Hepatitis B without receiving antiviral treatment": ["Infection", "CurrentTreatment"]
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


def add_essential_types(criteria_types: set[str]):
    criteria_types.update(['And', 'Or', 'Not', 'If'])


# Important lessons:
# 1. Categorisation step is much better at choosing criterion type, giving it the type improves performance.
# 2. We must tell LLM to choose criterion types from schema, otherwise it only use those with detailed instructions
def llm_curate_from_text(criteria_to_types: dict[str, list[str]], client: LlmClient,
                         additional_instructions: str = None) -> str:
    # logger.info(f"criteria_types: {criteria_types}")

    # collect all the criteria types
    criteria_types = set([t for type_list in criteria_to_types.values() for t in type_list])

    add_essential_types(criteria_types)
    criterion_mapping_rules = '\n'.join(
        [k for k, v in INSTRUCTION_CRITERION_TYPES.items() if criteria_types.intersection(set(v))])

    system_prompt = '''
You are an expert clinical trial curator. Your role is to convert unstructured inclusion and exclusion criteria into a \
structured format using a predefined Python schema.'''

    # print the clinical trial schema
    prompt = f'{extract_criterion_schema_classes(criteria_types)}\n'
    prompt += '''
INSTRUCTIONS:

# General
- Exclusion criteria must be expressed as inclusion criteria wrapped in a `NotCriterion`
- Top-level grouping requirement: For each top-level INCLUDE or EXCLUDE rule in the original text, generate exactly one \
top-level criterion, wrapping all relevant subconditions using AndCriterion, OrCriterion, or NotCriterion as needed.
- Assume gender is either male or female only. Therefore:
  - Use `if male ... else: ...` instead of checking both values.
  - Phrases such as "male or female", "males and females" is the same as "all participants".
- DO NOT invent new criterion type, only use those provided.
- Answer should be given in a single python code block with no explanation. Use python classes instead of dict.

# Description field
- Top-level criteria: `input_description` field **must** capture the **full original text exactly as written**, including:
  - the `INCLUDE` or `EXCLUDE` tag at the beginning
  - sub-bullet points with original formatting.
- Non–top-level criteria: `sub_description' field should be complete and self-contained.

# Composite Criterion
- Use IfCriterion for any conditional logic, explicit or implied (e.g., “if X then Y”, “Y in males, Z in females”, \
“≥10 if X-negative”). Never use AND for mutually exclusive criteria, use IF instead (e.g., “Y in males and X in females”).
- Always decompose any criterion containing multiple distinct conditions joined by logical conjunctions (“and”, “or”, \
“as well as”, “with”, “without”) into individual components, using:
  - AndCriterion for “and”-like phrases
  - OrCriterion for “or”-like phrases
- Wrap criterion in NotCriterion if it should be negated.
- Wrap criterion in TimingCriterion if it timing or time frame is provided.
'''
    prompt += f'''
# Criterion Mapping Rules
In general, choose appropriate criterion type from the schema. Following are more specific rules to help with ambiguity:  
{criterion_mapping_rules}

Create a python variable of the type `List[BaseCriterion]` to represent the following criterion along with their criteria types.
Return only the python variable. Do not include any extra text.
'''
    prompt += f'''
```json
{json.dumps(criteria_to_types, indent=2)}
```
'''
    # use for gui re-curation
    if additional_instructions:
        prompt += f'# Additional instructions:\n{additional_instructions}'

    response = client.llm_ask(prompt, system_prompt=system_prompt)

    python_code = extract_code_blocks(response, 'python')

    return python_code

def pydantic_curator_workflow(criterion_dict: dict, client: LlmClient, additional_instructions: str = None) -> RuleOutput:
    logger.info("\n=== START PYDANTIC CURATION ====\n")

    rule: str = criterion_dict.get("input_rule")
    exclude: bool = criterion_dict.get("exclude")
    if exclude:
        input_rule = "EXCLUDE " + rule
    else:
        input_rule = "INCLUDE " + rule
    logger.info(f"Input rule:\n{input_rule}\n")

    flipped: bool = criterion_dict.get("flipped")
    cohort: list[str] | None = criterion_dict.get("cohort")

    # Run Pydantic curator
    curated_category: dict[str, list[str]] = llm_categorise_criteria(input_rule, client)
    logger.info(f"Assigned to categories:\n{curated_category.values()}\n")

    curated_rule = llm_curate_from_text(curated_category, client, additional_instructions)
    logger.info(f"Raw curated output:\n{curated_rule}\n")

    # Clean curation output
    curated_rule_cleaned = clean_curated_output(curated_rule)
    if not curated_rule_cleaned.strip():
        raise ValueError("Cleaned LLM output is empty")
    logger.info(f"Cleaned curated output:\n{curated_rule_cleaned}\n")

    pydantic_output = RuleOutput(
        rule_text=input_rule,
        exclude=exclude,
        flipped=flipped is True,
        cohorts=cohort,
        curation=curated_rule_cleaned
    )

    return pydantic_output


def load_eligibility_criteria(trial_data):
    protocol_section = trial_data['protocolSection']
    eligibility_module = protocol_section['eligibilityModule']
    return unescape_json_str(eligibility_module['eligibilityCriteria'])


def main():
    parser = argparse.ArgumentParser(description="Pydantic Clinical trial curator")
    parser.add_argument('--trial_json', help='JSON file containing trial data', required=True)
    parser.add_argument('--curated_output', help='Output file for curated trial data (expected filetype: .py)', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    client = OpenaiClient()

    trial_data = load_trial_data(args.trial_json)
    eligibility_criteria = load_eligibility_criteria(trial_data)
    logger.info(f"Loaded {len(eligibility_criteria)} eligibility criteria")

    # Text preparation workflow
    processed_rules = llm_rules_prep_workflow(eligibility_criteria, client)

    # Pydantic curator workflow
    curated_rules = []
    for criterion in processed_rules:
        curated_result = pydantic_curator_workflow(criterion, client)
        curated_rules.append(curated_result)

    # Output formatting
    tab_spaces = "    "
    with open(args.curated_output, 'w', encoding='utf-8') as f:
        f.write("rules = [\n")
        for rule in curated_rules:
            f.write(f"{tab_spaces}Rule(\n")
            f.write(f"{tab_spaces * 2}rule_text={repr(rule.rule_text)},\n")
            f.write(f"{tab_spaces * 2}exclude={rule.exclude},\n")

            f.write(f"{tab_spaces * 2}flipped={rule.flipped},\n")

            if rule.cohorts is not None:
                f.write(f"{tab_spaces * 2}cohorts={repr(rule.cohorts)},\n")

            f.write(f"{tab_spaces * 2}curation=")
            curation_lines = rule.curation.strip().splitlines()
            temp_curation = []
            counter = 0
            for line in curation_lines:
                if counter == 0:
                    temp_curation.append(f"{tab_spaces * 0}{line}")
                else:
                    temp_curation.append(f"{tab_spaces * 3}{line}")
                counter += 1
            formatted_curation_lines = "\n".join(temp_curation)
            f.write(formatted_curation_lines)
            f.write("\n")

            f.write(f"{tab_spaces * 2}),\n\n")
        f.write("]\n")


if __name__ == "__main__":
    main()
