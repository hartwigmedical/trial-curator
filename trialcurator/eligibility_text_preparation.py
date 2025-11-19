import logging
from typing import Any

from trialcurator.llm_client import LlmClient
from trialcurator.utils import llm_json_check_and_repair

logger = logging.getLogger(__name__)

#
# def llm_parse_





def llm_sanitise_text(eligibility_criteria: str, client: LlmClient) -> str:
    logger.info("\nSTART TEXT SANITISATION\n")

    system_prompt = "You are a clinical trial eligibility criteria sanitization assistant."

    user_prompt = """Clean the eligibility criteria text below using the following instructions

## DISTINGUISH INCLUSION & EXCLUSION CRITERIA
- Ensure that 'Inclusion Criteria:' and 'Exclusion Criteria:' each appear on their own line.
- If these headers appear inside a bullet point, move them to their own line before the related section.
- Do not duplicate, paraphrase, or remove headers.
- Maintain distinct eligibility groups (cohorts, parts, phase etc) if provided in the original text.

## TYPO CORRECTION & NORMALIZATION
- Fix typos and misspellings in units, medical terms, and lab test names.
- Use ^ for power instead of superscript (e.g., 10^9 not 10⁹).
- Use 'x' for multiplication instead of '*' or 'times' (e.g., 5 x ULN).
- Use uppercase 'L' for liters (e.g., mg/dL).
- Use SI unit for lab measurements.
- Replace well-known terms with standard abbreviations, especially but not limited to ECOG, HIV, HBV, HCV, ULN, CNS, \
ANC, AST, ALT, aPTT. Remove the un-abbreviated term. e.g. both "Eastern Cooperative Oncology Group" and "Eastern \
Cooperative Oncology Group (ECOG)" should be replaced with "ECOG".

## FORMATTING & BULLETING
- Normalize all bullet points to use '-' consistently. This does **not** apply to 'Inclusion Criteria:' and 'Exclusion Criteria:'
- Ensure each bullet starts on a new line.

## INDENTATION

- Use a single hyphen (`-`) for all bullet points.
- Top-level bullets must not be indented.
- Nested bullet points (child bullets) must be indented **with exactly two spaces**.

### Examples

**Correct:**

- Parent point A  
  - Child point 1  
  - Child point 2  

**Incorrect:**

  - Parent point A  
    - Child point 1

### Correction of incorrect input:
Convert the following:

* Parent point A  
  * Parent point B  
    * child point 1  

To:

- Parent point A  
- Parent point B  
  - child point 1  

### Special Rules for indentation:
- A child bullet must only appear **immediately after a parent bullet that ends with a colon** (e.g., "... unless the following criteria are met:").
- If a bullet does **not** end with a colon or similar clause (e.g., “must meet one of the following”), then all subsequent bullets are considered top-level.
- Do **not** promote or demote bullet level based on visual indentation alone; rely on **semantic cues** (colon or conditional phrases).

## CRITERION SPLITTING
- If a criterion lists multiple conditions joined together that are logically independent exclusion/inclusion rules, \
split each into its own bullet

## REMOVE PERMISSIVE OR NON-RESTRICTIVE LINES
- Only include criteria that explicitly define inclusion or exclusion rules.
- Remove permissive statements that do not restrict eligibility (e.g., "X is allowed", "Y may be permitted", "X are eligible")
- Remove any statement about informed consent (e.g., "Patient must be able to provide informed consent").
- Remove any rule that consists solely of descriptive or contextual information and does not impose any inclusion or exclusion requirement.
- However, note the following exceptions:
    - Retain the rule if it contains even a single restrictive criterion, regardless of how much descriptive text it has.
    - If a bullet contains conditions under which exclusion **does not apply**, preserve both the parent and its qualifying subpoints.
    - Do **not** remove only a portion of the statement. The statement should be either removed entirely or kept as a whole.

### LAB VALUES
- When multiple lab values or thresholds are listed (e.g., hemoglobin < 5 mmol/L, platelets < 100, etc.), ensure the \
correct logical operator is implied:
  - If these appear under EXCLUSION criteria: join using OR (any of these makes the patient ineligible).
  - If these appear under INCLUSION criteria: join using AND (all must be satisfied for inclusion).
- If the original text connects such lab conditions with "and" or lacks explicit connectors, infer the correct logic as \
above and split into separate bullet points accordingly.
- If multiple lab values are listed under a general category in a line, convert into parent and sub-bullets, i.e \
"Sufficient function (X > 10, Y < 5)" should be converted to bullet "Sufficient function" and sub-bullets "X > 10" and "Y < 5".
- If there are multiple general category each with multiple lab values, convert into multiple parent and sub-bullets, \
i.e. A (X > 10, Y < 5), B (M < 1, N > 4) should be converted to bullet "A" and sub-bullets "X > 10" and "Y < 5" and \
bullet "B" and sub-bullets "M < 1" and "N > 4".
- If a criterion uses a disjunction or conjunction for two separate lab values like "Aspartate amino transferase (AST) \
or alanine amino transferase (ALT) > or equal to three times the upper limit of normal (if related to liver metastases \
> five times the upper limit of normal)", rewrite it as two separate bullet points:
    - AST ≥ 3 × ULN (if related to liver metastases > 5 × ULN)
    - ALT) ≥ 3 × ULN (if related to liver metastases > 5 × ULN)
    
### GENDER CRITERIA
- If gender is expressed as both male and female, and the criterion does not impose a different condition for each, \
remove the gender part and retain only the meaningful eligibility condition. Example:
  - “Male or female, aged 18 years or older” → “Aged 18 years or older”
  - “Male and female participants must use contraception” → “Must use contraception”
- If gender is mentioned solely to describe a condition that is already sex-specific (e.g., ovarian cancer), remove \
the gender descriptor. Example:
  - “Female participants with ovarian cancer” → “Participants with ovarian cancer”
- Retain gender references only when they impose a restriction, such as:
  - Differentiated rules based on gender (e.g., “Males must...”, “Females must not...”)
  - Criteria that apply only to a subset, such as “females of childbearing potential”
- Remove gender in pregnancy and breastfeeding criteria. Example:
  - “Females who are pregnant or breastfeeding” → “Pregnant or breastfeeding”

## OUTPUT STRUCTURE
- Answer in one text block with no additional explanation.

"""
    user_prompt += f"\n### INPUT TEXT\n{eligibility_criteria.strip()}\n"

    response = client.llm_ask(user_prompt, system_prompt).replace("```", "")
    return response


def llm_tag_cohort_and_direction(eligibility_criteria: str, client: LlmClient) -> list[dict[str, Any]]:
    # Direction: whether a rule is an INCLUSION or an EXCLUSION criterion
    logger.info("\nSTART COHORT AND DIRECTION TAGGING\n")

    system_prompt = """You are a medical text processing assistant. Given the clinical trial eligibility criteria,
1. Tag the inclusion or exclusion status of each rule.
2. Tag the cohort(s) applicable to each rule."""

    user_prompt = """
## TASK ONE INSTRUCTIONS FOR INCLUSION/EXCLUSION TAGGING
Your task is to:
- Tag each top-level bullet with true or false depending on whether the criterion is an **exclusion** rule or otherwise.

## INCLUSION/EXCLUSION TAGGING GUIDELINES
- Tag each top-level bullet with any sub-bullets listed beneath it using hyphenation.

## TASK TWO INSTRUCTIONS FOR COHORT TAGGING
Some trials have various eligibility groups (e.g., "part 1", "cohort A", "phase 2"), each with a distinct set of rules. 
These are called the different **cohorts** in a trial.

For each rule, your task is to
1. Tag its applicable cohort(s) in a list of string. E.g., ["Cohort A", "Cohort B"]
If there is only a single cohort in the trial or if a rule is not cohort-specific, ignore this tagging.

## COHORT IDENTIFICATION GUIDELINES
- Explicitly named cohort: Only extract an eligibility group if it is explicitly named. 
    - For example: "dose-escalation cohorts (Phase 1a)", "cohort A", "Part 2: Arm B". 
    - Do not infer or invent names.
- Cohort names may appear as:
    - Phrases in inclusion and exclusion criteria headings: A cohort name can be identified if there are matching phrases in the Inclusion Criteria and Exclusion Criteria headings that refer to a specific eligibility group. 
    - For example, phrases like:
      - "X Inclusion Criteria:" followed by "X Exclusion Criteria:", "Inclusion Criteria(X):" followed by "Exclusion YourCriteria(X):"
      - "X: Inclusion Criteria" followed by "X: Exclusion Criteria".
      In these cases, "X" should be treated as the name of a specific eligibility group.
  - Headings (e.g., 'Cohort A Only:')
  - Inline phrases (e.g., Phase 2:, Part B:).
  - Consider any phrase ending with 'Only', 'Cohort', 'Part', 'Phase', etc., or disease status, cancer types, dosage etc as a potential cohort if it has distinct criteria beneath or tied to it. Remove the word 'only' from cohort names.
- Distinct Eligibility Criteria: 
    - The named group must have at least one eligibility criterion that differs from other groups. Do NOT extract multiple names that share identical criteria.
- Preserve Full Group Names: 
    - If a group name contains a parent group and one or more subgroups, capture the full hierarchical name as it appears in the text. 
    - For example: If `GROUP A:` contains `Subtype X:` which contains `Condition Y`, then the group name should be extracted as: "GROUP A: Subtype X: Condition Y"
  - Do NOT shorten this to "GROUP A" or "Subtype X".
- Do NOT Merge or Generalize:
  - Do NOT create general or umbrella categories (e.g., combining "part 1" and "part 2" into one group).
  - Do NOT list a group just because it has a different name — the criteria must actually differ.
  
## OUTPUT CONTENTS
- The "rule" text must NOT include any cohort label or prefix. Remove any instance of the cohort name if it appears at the beginning of the rule.
- The cohort name should appear **only** in the "cohorts" list and not inside the "rule" field.

## RULE FORMATTING
- The "rule" text must retain all original formatting, including indentation.
- If a line begins with a dash and it is a **top-level** bullet, remove the dash.
- If it is a **subpoint**, retain the dash and original indentation.
- Preserve any line breaks using `\\n` within the JSON string.

## OUTPUT STRUCTURE
Return a valid JSON array. Each item should be an object with:
- "input_rule" (string, preserving formatting)
- "exclude" (boolean)
- "cohorts" (optional list of strings, omit if not present)

Do not include any extra text before or after the JSON output.

Example:
```json
[
    {
       "input_rule": "Age ≥ 18 years",
       "exclude": false,
       "cohorts": ["Cohort A", "Cohort B"]
    },
    {
       "input_rule": "For female patients:\\n  - Negative pregnancy test\\n  - Reliable contraceptive methods",
       "exclude": false,
       "cohorts": ["Cohort C"]
    },
    {
       "input_rule": "HIV infection",
       "exclude": true,
    },
]
```

"""
    user_prompt += f"\n### INPUT TEXT\n{eligibility_criteria.strip()}\n"

    response = client.llm_ask(user_prompt, system_prompt=system_prompt)
    return llm_json_check_and_repair(response, client)


def llm_subpoint_promotion(eligibility_criteria: str, client: LlmClient) -> list[str]:
    logger.info("\nSTART CHILD BULLET PROMOTION\n")

    system_prompt = "You are a clinical trial curation assistant. You are given clinical trial eligibility criteria that may include nested bullets."

    user_prompt = """
## INSTRUCTION
Your task is to:
- Remove parent level bullet and promote child bullets to their parent level if ALL of the following are true:
  1. The parent bullet is a general heading or umbrella term that only serves to provide context to the sub-bullet points.
  2. The parent bullet is not only applicable to a subpopulation of patients (e.g. "for patients with X:").
  3. The parent bullet does not imply conditionality (e.g. “if”, “unless”, “when”, “only if”, “must meet one of”, “any of the following”).
  4. The sub-bullets are logically independent and can stand alone without altering the clinical interpretation.
- Otherwise, keep the parent bullet and sub-bullet as they are with original formatting.

## IMPORTANT
- Repeat recursively for deeper nesting.
  - If sub-bullets are promoted, re-evaluate their former parent to determine if it too should now be promoted. Repeat recursively until no further promotions apply.
  - If a parent bullet has all its sub-bullets promoted, and it now contains no clinical information of its own, it must also be removed.
- Do NOT merge sibling bullet points.
- Do NOT promote if doing so changes the original clinical meaning or introduces assumptions.
- Do NOT partially promote sub-bullets from the same parent.

## OUTPUT STRUCTURE
- Return a JSON **list of strings** only.
- Bullets on the same level must be left aligned.
- Do not wrap in additional fields or provide commentary.

## EXAMPLES

### Example 1

**Input:**
```
- Hepatic function:
  - AST < 3 × ULN
  - ALT < 3 × ULN
```

**Output:**
```json
[
  "AST < 3 × ULN",
  "ALT < 3 × ULN"
]
```

---

### Example 2 (Preserve input — do NOT promote)

**Input:**
```
- Patients with condition XYZ are excluded unless they meet the following criteria:
  - Must be stable
  - No prior treatments
  - No active disease
```

**Output:**
```json
[
  "Patients with condition XYZ are excluded unless all of the following apply:\\n- Must be stable\\n- No prior treatments\\n- No active disease"
]
```

"""
    user_prompt += f"\n### INPUT TEXT\n{eligibility_criteria.strip()}\n"

    response = client.llm_ask(user_prompt, system_prompt=system_prompt)
    return llm_json_check_and_repair(response, client)


def llm_exclusion_logic_flipping(eligibility_criteria: str, client: LlmClient) -> list[dict[str, bool | str]]:
    logger.info("\nSTART EXCLUSION LOGIC FLIPPING\n")

    system_prompt = 'You are a clinical trial curation assistant. You are given an eligibility exclusion rule.'

    user_prompt = """
## INSTRUCTIONS
For this exclusion rule, determine if it can be rewritten as a logically equivalent inclusion rule without changing the original meaning.
- If so, rewrite the rule and set `flipped: true` and `exclude: false`
- If not, return the rule unchanged and set `"flipped: false` and `exclude: true`

## LOGIC CONVERSION RULES:
- Flip exclusion rules to inclusion rules only when the semantic meaning is unchanged, including:
  - Negated Inclusions (EXCLUDE + NOT)
    - EXCLUDE patients who do NOT have / demonstrate / show / meet X → INCLUDE patients who have / demonstrate / show / meet X
    - flip even if X is vague concept like adequate organ function
  - Measurement comparisons:
    - EXCLUDE X < N → INCLUDE X ≥ N
    - EXCLUDE X > N → INCLUDE X ≤ N
  - Multiple disjunctive measurement comparisons:
    - EXCLUDE (X < N OR Y > M) → INCLUDE (X ≥ N AND Y ≤ M) 
  - Scalar clinical estimates (e.g., life expectancy, QTc, age):
    - Correct: EXCLUDE QTcF > 470 ms → INCLUDE QTcF ≤ 470 ms
    - Correct: EXCLUDE Life expectancy < 6 months → INCLUDE Life expectancy ≥ 6 months
    - Correct: EXCLUDE X ≥ 3 × ULN → INCLUDE X < 3 × ULN
  - Redundant exclusion:  
    - When a criterion is phrased as ‘EXCLUDE participants must not have X’, rephrase to remove the redundant negation while preserving exclusion intent. Convert to: ‘EXCLUDE patient who have X’
- If the input rule contains **multiple conditions joined by commas or 'or'**, treat each condition separately.
  - For example: "reading A ≥ 50%, condition B or condition C"
    → Split into:
      1. "reading A ≥ 50%"
      2. "condition B"
      3. "condition C"
  - Then apply the flipping logic to each component **individually**.
- Do not flip if it could:
  - Change the clinical, temporal, or semantic intent
  - Broaden or narrow the scope unintentionally
  - Introduce assumptions not present in the original
  - Example where flipping is not allowed:
    - Incorrect: EXCLUDE "Surgery (< 6 months)" → INCLUDE "Surgery ≥ 6 months ago" (flipping changes the meaning — do not flip)
- **When in doubt, leave as EXCLUDE**.
- Do NOT change the medical meaning.
- Do NOT drop any important detail.

## OUTPUT STRUCTURE
Return a JSON **list** of dictionaries, where each dictionary has:
- `"input_rule"`: the rule text (flipped or original)
- `"flipped"`: `true` if flipped to inclusion; `false` otherwise
- `"exclude"`: must be `false` if `flipped` is `true`, otherwise `true`

- Do **not** prepend with the words EXCLUDE or INCLUDE.

## EXAMPLES

### Example 1

```json
[
    {
        "input_rule": "Age ≥ 18 years",
        "exclude": true,
        "flipped": false
    }
]
```

### Example 2

```json
[
    {
        "input_rule": "Life expectancy is 3 months or more",
        "exclude": false,
        "flipped": true
    }
]
```

### Example 3
An exclusion rule of the form:
`Reading ≥ 50, or condition B, or condition C`  
becomes

```json
[
    {
        "input_rule": "Reading < 50",
        "exclude": false,
        "flipped": true
    },
    {
        "input_rule": "condition B",
        "exclude": true,
        "flipped": false
    },
    {
        "input_rule": "condition C",
        "exclude": true,
        "flipped": false
    }
]
```

"""
    user_prompt += f"\n### INPUT TEXT\n{eligibility_criteria.strip()}\n"

    response = client.llm_ask(user_prompt, system_prompt=system_prompt)
    response = llm_json_check_and_repair(response, client)

    # In case LLM does not adjust `exclude` after exclusion logic flipping. It would lead to errors download when we append `INCLUDE` or `EXCLUDE` for the Pydantic or Actin curators
    for item in response:
        if item.get("flipped") is True:
            item["exclude"] = False
    return response


def get_criterion_fields(criterion: dict) -> tuple[str, bool, str | None]:
    return (
        criterion.get("input_rule"),
        criterion.get("exclude"),
        criterion.get("cohorts")
    )


def update_criterion_fields(input_rule: str, exclude: bool, flipped: bool | None = False, cohort: str | None = None) -> dict[str, Any]:
    updated_criterion = {
        "input_rule": input_rule,
        "exclude": exclude
    }
    if flipped is not None and exclude:
        updated_criterion["flipped"] = flipped
    if cohort is not None:
        updated_criterion["cohort"] = cohort

    return updated_criterion


def not_a_oneline_rule(rule: str) -> bool:
    lines = []
    for line in rule.splitlines():
        line = line.strip()
        if line:
            lines.append(line)
    return len(lines) > 1


def llm_rules_prep_workflow(eligibility_criteria_input: str, client) -> list[dict[str, Any]]:
    rules_sanitised = llm_sanitise_text(eligibility_criteria_input, client)  # returns a block of string
    rules_w_cohort = llm_tag_cohort_and_direction(rules_sanitised, client)  # returns a list of dict

    promoted_rules = []
    for criterion in rules_w_cohort:
        original_rule, original_exclude, original_cohort = get_criterion_fields(criterion)
        if not_a_oneline_rule(original_rule):
            rules_for_promotion = llm_subpoint_promotion(original_rule, client)  # returns a list of str
            if isinstance(rules_for_promotion, list):
                for promoted_rule in rules_for_promotion:
                    promoted_rules.append(
                        update_criterion_fields(
                            promoted_rule,
                            original_exclude,
                            original_cohort
                        )
                    )
                continue
            else:
                raise TypeError("The rules being promoted are not a list of strings")
        promoted_rules.append(criterion)

    flipped_rules = []
    for criterion in promoted_rules:
        original_rule, original_exclude, original_cohort = get_criterion_fields(criterion)
        if original_exclude:
            exclusion_flipping = llm_exclusion_logic_flipping(original_rule, client)  # returns a list of dict
            if isinstance(exclusion_flipping, list):
                for flipped_dict in exclusion_flipping:
                    flipped_rule = flipped_dict.get("input_rule")
                    if not isinstance(flipped_rule, str):
                        raise TypeError("The flipped rule did not return a string")
                    flipped_rules.append(
                        update_criterion_fields(
                            flipped_rule,
                            flipped_dict.get("exclude"),
                            flipped_dict.get("flipped"),
                            original_cohort
                        )
                    )
                continue
            else:
                raise TypeError("The flipped exclusion rule is not a list of dictionaries")
        flipped_rules.append(criterion)

    return flipped_rules
