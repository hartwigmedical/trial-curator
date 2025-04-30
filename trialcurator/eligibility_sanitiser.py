import json
import logging
import sys

from trialcurator.llm_client import LlmClient
from trialcurator.utils import extract_code_blocks, unescape_json_str

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.INFO)

def llm_sanitise_text(eligibility_criteria: str, client: LlmClient) -> str:

    logger.info(f"eligibility criteria: {eligibility_criteria}")

    system_prompt = """
You are a clinical trial eligibility criteria sanitization assistant.
Your job is to prepare free-text eligibility criteria for programmatic processing.

GUIDELINES
- Cleaning and formatting the text
- Removing permissive or descriptive lines
- Preserving only valid inclusion/exclusion rules

DO NOT:
- Summarize, paraphrase, or alter the medical content
- Change the meaning of any restrictive criteria
- Remove headers like 'Inclusion Criteria:' or 'Exclusion Criteria:'
"""
    user_prompt = """
Clean the eligibility criteria text below using the following instructions

DISTINGUISH INCLUSION & EXCLUSION CRITERIA
- Ensure that 'Inclusion Criteria:' and 'Exclusion Criteria:' each appear on their own line.
- If these headers appear inside a bullet point, move them to their own line before the related section.
- Do not duplicate, paraphrase, or remove headers.
- Maintain distinct eligibility groups (cohorts, parts, phase etc) if provided in the original text.

TYPO CORRECTION & NORMALIZATION
- Fix typos and misspellings in units, medical terms, and lab test names.
- Use ^ for power instead of superscript (e.g., 10^9 not 10⁹).
- Use 'x' for multiplication instead of '*' or 'times' (e.g., 5 x ULN).
- Use uppercase 'L' for liters (e.g., mg/dL).
- Use SI unit for lab measurements.
- Replace well-known terms with standard abbreviations (e.g., ECOG, HIV, HBV, HCV, ULN, CNS).

FORMATTING & BULLETING
- Normalize all bullet points to use '-' consistently.
- Ensure each bullet starts on a new line.
- If a criterion includes multiple conditions that can logically stand alone, split them into \
distinct bullet points.

REMOVE PERMISSIVE OR NON-RESTRICTIVE LINES
- Only include criteria that explicitly define inclusion or exclusion rules.
- Remove permissive statements that do not restrict eligibility (e.g., "X is allowed", "Y may be permitted", "X are eligible")
- Remove any descriptive/contextual statement that don’t impose inclusion or exclusion requirements.
- Remove any statement about informed consent (e.g., "Patient must be able to provide informed consent").

LOGICAL CONNECTORS FOR LAB THRESHOLDS
- When multiple lab values or thresholds are listed (e.g., hemoglobin < 5 mmol/L, platelets < 100, etc.), ensure the correct logical operator is implied:
  - If these appear under EXCLUSION criteria: join using OR (any of these makes the patient ineligible).
  - If these appear under INCLUSION criteria: join using AND (all must be satisfied for inclusion).
- If the original text connects such lab conditions with "and" or lacks explicit connectors, infer the correct logic as above and split into separate bullet points accordingly.

OUTPUT STRUCTURE
- Answer in one text block with no additional explanation.

INPUT TEXT
"""
    user_prompt += f"\n{eligibility_criteria}\n"

    sanitised_text = client.llm_ask(user_prompt, system_prompt).replace("```", "")
    return sanitised_text


def llm_extract_eligibility_groups(eligibility_criteria: str, client: LlmClient) -> [str]:

    logger.info(eligibility_criteria)

    system_prompt = '''You are a medical text processing assistant.'''

    prompt = f"```\n{eligibility_criteria}\n```\n"
    prompt += '''
You are given the inclusion and exclusion criteria of a clinical trial. Some trials define different eligibility groups (e.g., "part 1", "cohort A", "phase 2") with distinct sets of eligibility criteria.
YOUR TASK is to extract the names of these eligibility groups into a JSON list of strings, but only under the following conditions:
1. Explicit Labeling: Only extract a group if it is explicitly named (e.g., "part 1", "cohort A", "arm B"). Do not infer\
 or create names.
2. Distinct Eligibility Criteria: Groups must have meaningfully different eligibility criteria.
3. Preserve Full Group Names: If a group name contains a parent group and one or more subgroups, capture the full \
hierarchical name as it appears in the text. For example:
   - If `GROUP A:` contains `Subtype X:` which contains `Condition Y`, then the group name should be extracted as: \
"GROUP A: Subtype X: Condition Y"
   - Do NOT shorten this to "GROUP A" or "Subtype X".
4. Do NOT Merge or Generalize:
   - Do NOT create general or umbrella categories (e.g., combining "part 1" and "part 2" into one group).
   - Do NOT list a group just because it has a different name — the criteria must actually differ.
5. Default Case: If no distinct groups are defined, return a single-item list `["default"]`

Output format: Return a JSON array of group names (as strings), exactly as they appear in the text (e.g., "part 1", \
"cohort A"). Do not include any explanation or formatting outside the JSON array.
'''

    response = client.llm_ask(prompt, system_prompt=system_prompt)

    try:
        eligibility_groups = json.loads(extract_code_blocks(response, 'json'))
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to decode JSON from response text: {e}")
        eligibility_groups = []

    logger.info(f"found the following eligibility groups: {eligibility_groups}")
    return eligibility_groups


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

def llm_simplify_and_tag_text(eligibility_text: str, client: LlmClient) -> str:

    system_prompt = '''
You are a clinical trial text simplification and tagging assistant.

GOALS:
1. Tag each top-level bullet with "INCLUDE" or "EXCLUDE" depending on whether the criterion is an inclusion or exclusion rule.
2. Convert EXCLUDE rules to logically equivalent INCLUDE rules only if the meaning is precisely preserved.
3. Promote sub-bullets to top-level bullets only when doing so does not change the intended logical structure.

LOGIC CONVERSION RULES:
- Flip EXCLUDE rules to INCLUDE only when the semantic meaning is unchanged, including:
  - Negated Inclusions (EXCLUDE + NOT)
    - EXCLUDE patients who do not have / demonstrate / show / meet X → INCLUDE patients who have / demonstrate / show / meet X
  - Measurement comparisons:
    - EXCLUDE X < N → INCLUDE X ≥ N
    - EXCLUDE X > N → INCLUDE X ≤ N
  - Multiple disjunctive measurement comparisons:
    - EXCLUDE (X < N OR Y > M) → INCLUDE (X ≥ N AND Y ≤ M) 
  - Scalar clinical estimates (e.g., life expectancy, QTc, age):
    - ✅ EXCLUDE QTcF > 470 ms → INCLUDE QTcF ≤ 470 ms
    - ✅ EXCLUDE Life expectancy < 6 months → INCLUDE Life expectancy ≥ 6 months
- Do not flip if it could:
  - Change the clinical, temporal, or semantic intent
  - Broaden or narrow the scope unintentionally
  - Introduce assumptions not present in the original
  - Example where flipping is not allowed:
    - ❌ EXCLUDE "Surgery (< 6 months)" → INCLUDE "Surgery ≥ 6 months ago" (flipping changes the meaning — do not flip)
  - When in doubt, leave as EXCLUDE.

SUB-BULLET PROMOTION RULES:
- Unless overridden by parent statement, assume sub-bullets in inclusion criteria are conjunctive, and in exclusion \
criteria are disjunctive (OR).
- Only promote sub-bullets to parent-level bullets if:
  - The parent bullet does not imply conditionality (e.g., “if”, “unless”, “when”).
  - Promoting the sub-bullets does not change the logical meaning (e.g., “INCLUDE any of the following” vs “INCLUDE all of the following”).
  - The grouping of sub-bullets doesn't represent a specific logical constraint.
- Do not promote if doing so changes the original clinical meaning or introduces assumptions.
  
DO NOT:
- Change the medical meaning
- Drop any important detail

OUTPUT FORMAT:
- Each top-level bullet must be tagged as "INCLUDE" or "EXCLUDE", with any sub-bullets listed beneath it using hyphenation e.g.
```
INCLUDE Age ≥ 18 years
INCLUDE for female patients:
  - Negative pregnancy test
  - Reliable contraceptive methods
EXCLUDE HIV infection
```
- Do not add blank lines. Do not add commentary or explanation.
'''
    user_prompt = f'''
Below is the eligibility criteria for a clinical trial. Perform tagging and logic flipping as described above.
{eligibility_text}
Return the cleaned, tagged lines below:
'''

    response = client.llm_ask(user_prompt, system_prompt)
    return response.replace("```", "")
