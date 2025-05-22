import json
import re
from typing import Any


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


# def extract_code_blocks(text: str, lang: str) -> str:
#     """
#     Extracts and returns a list of <lang> code snippets found within
#     i.e. triple backtick Python code blocks (```python ... ```).
#     """
#     pattern = re.compile(r"```" + lang + "(.*?)```", re.DOTALL)
#     return "".join(pattern.findall(text))

def extract_code_blocks(text: str, lang: str = "json") -> str:
    """
    Extracts <lang> code block from triple backticks. If not found, return full text.
    """
    pattern = re.compile(rf"```{lang}\s*(.*?)```", re.DOTALL)
    match = pattern.search(text)
    return match.group(1).strip() if match else text.strip()

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

def load_eligibility_criteria(trial_data):
    protocol_section = trial_data['protocolSection']
    eligibility_module = protocol_section['eligibilityModule']
    return unescape_json_str(eligibility_module['eligibilityCriteria'])

# deeply remove any field with the given field name in a json type structure
def deep_remove_field(data: Any, field_name) -> Any:
    if isinstance(data, dict):
        return {
            key: deep_remove_field(value, field_name) for key, value in data.items() if key != field_name
        }
    elif isinstance(data, list):
        return [deep_remove_field(item, field_name) for item in data]
    else:
        return data
