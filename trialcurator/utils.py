import json
import re
import logging
from trialcurator.llm_client import LlmClient
from utils.smart_json_parser import SmartJsonParser


logger = logging.getLogger(__name__)


def load_trial_data(json_file: str) -> dict:
    with open(json_file, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
        #logger.info(json.dumps(json_data, indent=2))
        return json_data


def unescape_json_str(json_str: str) -> str:
    return (json_str.replace("\\'", "'")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\>", ">")
            .replace("\\<", "<")
            .replace("\\[", "[")
            .replace("\\]", "]"))


def extract_code_blocks(text: str, lang: str) -> str:
    """
    Extracts and returns a list of <lang> code snippets found within
    i.e. triple backtick Python code blocks (```python ... ```).
    Otherwise, return text as is.
    """
    pattern = re.compile(r"```" + lang + "(.*?)```", re.DOTALL)
    match = pattern.findall(text)

    if match:
        return "".join(match)
    else:
        return text


def split_tagged_criteria(text: str) -> list[str]:
    """
    Split text containing tagged inclusion/exclusion criteria into individual criteria.
    Args:
        text (str): Text containing INCLUDE/EXCLUDE tagged criteria
    Returns:
        list[str]: List of individual criteria, each starting with INCLUDE or EXCLUDE
    """
    # This splits before each ^INCLUDE or ^EXCLUDE, ensuring full rules are kept intact
    criteria_list = re.split(r'(?=^(?:INCLUDE|EXCLUDE))', text.strip(), flags=re.MULTILINE)
    return [c.strip() for c in criteria_list if c.strip()]


def batch_tagged_criteria(text: str, batch_size: int) -> list[str]:
    """
    Split tagged inclusion/exclusion criteria text into batches of specified size.
    Args:
        text (str): Text containing INCLUDE/EXCLUDE tagged criteria
        batch_size (int): Number of criteria per batch
    Returns:
        list[str]: List of strings where each string contains batch_size criteria joined by newlines
    """
    # split into batches
    criteria_list = split_tagged_criteria(text)
    return ['\n'.join(criteria_list[i:i + batch_size]) for i in range(0, len(criteria_list), batch_size)]


def batch_tagged_criteria_by_words(text: str, max_words: int) -> list[str]:
    """
    Split tagged inclusion/exclusion criteria text into batches of at most max_words per batch.
    Args:
        text (str): Text containing INCLUDE/EXCLUDE tagged criteria
        max_words (int): Max number of words per batch
    Returns:
        list[str]: List of strings where each string contains batch_size criteria joined by newlines
    """
    # split into batches
    criteria_list = split_tagged_criteria(text)

    batches = [[]]
    batch_words = 0

    for criterion in criteria_list:
        num_words = len(criterion.split())
        if batch_words + num_words < max_words:
            batches[-1].append(criterion)
            batch_words += num_words
        else:
            batches.append([criterion])
            batch_words = num_words

    return ['\n'.join(batch) for batch in batches]


def load_eligibility_criteria(trial_data):
    protocol_section = trial_data['protocolSection']
    eligibility_module = protocol_section['eligibilityModule']
    return unescape_json_str(eligibility_module['eligibilityCriteria'])


def fix_json_math_expressions(raw_json: str) -> str:
    """
    Fixes math expressions in JSON values, including inside lists.
    Only evaluates unquoted expressions.
    """

    def is_math_expression(s: str) -> bool:
        return bool(re.fullmatch(r'[\d.\s+\-*/()%]+\d', s.strip()))

    def safe_eval(expr: str):
        try:
            return eval(expr, {"__builtins__": None}, {})
        except Exception:
            return expr

    def replacer(match):
        prefix = match.group(1)
        expr = match.group(2)
        suffix = match.group(3)

        if is_math_expression(expr):
            result = safe_eval(expr)
            if isinstance(result, (int, float)):
                return f"{prefix}{result}{suffix}"

        return match.group(0)

    # Pattern to match unquoted values in dicts or lists
    # Match values after :, [ or , that are math expressions,
    # but NOT quoted (no ")
    pattern = re.compile(
        r'([:\[,]\s*)'  # matches the prefix
        r'([\d.\s+\-*/()%]+\d)'  # matches the math expression
        r'(\s*)'  # spaces after the expression
        r'(?=[,\]}])',  # lookahead: ensures that what follows the expression is ,] or }
        re.MULTILINE
    )
    fixed = pattern.sub(replacer, raw_json)

    return fixed


def fix_malformed_json(json_str: str) -> str:
    """
    Sometimes LLM outputs malformed json, it is much easier to fix with regex than
    to overload the prompts with more instructions
    """

    """
    Fix dictionary without value, e.g., { "IS_MALE" } -> "IS_MALE"
    """
    pattern = r'{\s*("\w+")\s*}'
    json_str = re.sub(pattern, r'\1', json_str)

    """
    Fix malformed entries like:
      "actin_rule": "IS_MALE[]"
    to:
      "actin_rule": { "IS_MALE": [] }
    """
    pattern = r'("\w+")\s*:\s*("\w+)\[\]"'
    replacement = r'\1: { \2: [] }'
    json_str = re.sub(pattern, replacement, json_str)

    """
    Fix malformed entries like:
      "actin_rule": "RULE_NAME": [1]
    to:
      "actin_rule": { "RULE_NAME": [1] }
    """
    pattern = r'("\w+")\s*:\s*("\w+")\s*:\s*(\[[^\]]*\])'
    replacement = r'\1: { \2: \3 }'
    json_str = re.sub(pattern, replacement, json_str)

    """
    Fix malformed entries like:
      "actin_rule": "NOT": "RULE_NAME"
    to:
      "actin_rule": { "NOT": "RULE_NAME" }
    """
    pattern = r'("\w+")\s*:\s*("\w+")\s*:\s*("\w+")'
    replacement = r'\1: { \2: \3 }'
    json_str = re.sub(pattern, replacement, json_str)

    # fix up anything that has uncompleted numerical calculations
    json_str = fix_json_math_expressions(json_str)

    return json_str


# def llm_json_check_and_repair(response: str, client: LlmClient):
#
#     try:
#         extracted_response = extract_code_blocks(response, "json")
#         first_fix = fix_malformed_json(extracted_response)
#         second_fix = SmartJsonParser(first_fix).consume_value()
#         return second_fix
#
#     except Exception as e:
#         logger.warning(f"An exception {e} of type {type(e)} occurred.")
#         logger.warning("Send to LLM for repair.")
#         repair_prompt = f"""
# Fix the following JSON so it parses correctly. Return only the corrected JSON object:
# {response}
# """
#         repaired_response = client.llm_ask(repair_prompt)
#         extracted_response = extract_code_blocks(repaired_response, "json")
#         first_fix = fix_malformed_json(extracted_response)
#         second_fix = SmartJsonParser(first_fix).consume_value()
#         return second_fix


def llm_json_check_and_repair(response: str, client: LlmClient):
    # STEP 1 — Extract JSON from ```json blocks
    extracted = extract_code_blocks(response, "json").strip()

    # STEP 2 — Fast path: pure JSON?
    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        pass  # Not pure JSON

    # STEP 3 — Try existing "fix_malformed_json" helper first
    try:
        fixed = fix_malformed_json(extracted)
        return json.loads(fixed)
    except Exception:
        pass  # Still not valid JSON

    # STEP 4 — Try SmartJsonParser as a last resort
    try:
        return SmartJsonParser(extracted).consume_value()
    except Exception as e1:
        logger.warning(f"SmartJsonParser failed: {e1}. Triggering LLM repair.")

    # STEP 5 — Ask LLM to repair JSON
    repair_prompt = f"""
Fix the following JSON so it parses correctly. Return only the corrected JSON object:

```json
{extracted}
```
"""
    repaired = client.llm_ask(repair_prompt)
    extracted_repaired = extract_code_blocks(repaired, "json").strip()

    # Try json.loads first
    try:
        return json.loads(extracted_repaired)
    except json.JSONDecodeError:
        pass

    # Try fix_malformed_json then json
    try:
        repaired_fixed = fix_malformed_json(extracted_repaired)
        return json.loads(repaired_fixed)
    except Exception:
        pass

    # Absolute last resort: SmartJsonParser
    return SmartJsonParser(extracted_repaired).consume_value()
