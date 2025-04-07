import json
import logging
from trialcurator.llm_client import LlmClient
from trialcurator.utils import extract_code_blocks, unescape_json_str

logger = logging.getLogger(__name__)

# 7 April 2025:
# Moved across llm_sanitise_text(), llm_extract_eligibility_groups(), llm_extract_text_for_groups() from eligibility_curator.py
# Moved across clean_raw_text(), validate_and_fix_formatting(), remove_permissive_conditions() from eligibility_curator_ACTIN.py
# clean_raw_text() & validate_and_fix_formatting() merged into llm_sanitise_text()
# Prompts such as "Remove any criteria related to informed consent" are included in the standalone function remove_permissive_conditions()

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
                    Please clean the eligibility criteria text below using the following instructions.
                    
                    DISTINGUISH INCLUSION & EXCLUSION CRITERIA
                    - Ensure that 'Inclusion Criteria:' and 'Exclusion Criteria:' each appear on their own line.
                    - If these headers appear inside a bullet point, move them to their own line before the related section.
                    - Do not duplicate, paraphrase, or remove headers.
                    
                    TYPO CORRECTION & NORMALIZATION
                    - Fix typos and misspellings in units, medical terms, and lab test names.
                    - Use ^ for power instead of superscript (e.g., 10^9 not 10⁹).
                    - Use 'x' for multiplication instead of '*' or 'times' (e.g., 5 x ULN).
                    - Use uppercase 'L' for liters (e.g., mg/dL).
                    - Replace well-known terms with standard abbreviations (e.g., ECOG, HIV, HBV, HCV, ULN).
                    
                    FORMATTING & BULLETING
                    - Normalize all bullet points to use '-' consistently.
                    - Ensure each bullet starts on a new line.
                    - Maintain original indentation and formatting as much as possible.
                    - If a bullet contains multiple logically distinct conditions, split them into separate lines.
                    
                    REMOVE PERMISSIVE OR NON-RESTRICTIVE LINES
                    - Only include criteria that explicitly define inclusion or exclusion rules.
                    - Remove any statements that describe what is allowed or may be permitted (e.g., "X is allowed", "Y are eligible").
                    - Remove any descriptive/contextual lines that don’t impose inclusion or exclusion requirements.
                    - Remove any lines about informed consent (e.g., "Patient must be able to provide informed consent").
                    
                    OUTPUT STRUCTURE
                    - Answer in one text block with no additional explanation.
                    
                    INPUT TEXT
                    """
    user_prompt += f"\n{eligibility_criteria}\n"

    sanitised_text = client.llm_ask(user_prompt, system_prompt).replace("```", "")
    return sanitised_text

