import inspect
import json
import re
from typing import Any

from trialcurator import criterion_schema


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
