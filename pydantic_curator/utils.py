import inspect
import re
from typing import Any

from . import criterion_schema
from .criterion_schema import BaseCriterion, AndCriterion, OrCriterion, IfCriterion, NotCriterion


def extract_criterion_schema_classes(criterion_types: set[str] | list[str]) -> str:

    excluded_classes = ['TypedModel']

    criterion_schema_code = inspect.getsource(criterion_schema)

    pattern = (
        r"(?:^#.*\n"  # Match comment lines
        r"|^@.*\n)*"  # Or decorators
        r"^class\s+(\w+)\((.*?)\):\n"  # Class header
        r"(?:^[ \t]+.*\n)*"  # Class body (indented lines)
    )
    matches = list(re.finditer(pattern, criterion_schema_code, flags=re.MULTILINE))

    criterion_class_code = ""
    for match in matches:
        class_name = match.group(1)
        class_code = match.group()

        # always include BaseCriterion
        if class_name == 'BaseCriterion' or \
                [t for t in criterion_types if t.lower() in class_name.lower()]:
            criterion_class_code += '\n' + class_code

    # find all the helper classes we need
    helper_class_code = ""
    for match in matches:
        class_name = match.group(1)
        base_class = match.group(2)
        class_code = match.group()
        if "Criterion" in class_name or class_name in excluded_classes:
            continue
        if (class_name in criterion_class_code or
                base_class != 'TypedModel' and base_class in criterion_class_code):
            helper_class_code += '\n' + class_code

    return helper_class_code + '\n' + criterion_class_code

def deep_remove_description(criterion: BaseCriterion):
    criterion.description = ""
    if isinstance(criterion, (AndCriterion, OrCriterion)):
        for subcriterion in criterion.criteria:
            deep_remove_description(subcriterion)
    elif isinstance(criterion, NotCriterion):
        deep_remove_description(criterion.criterion)
    elif isinstance(criterion, IfCriterion):
        deep_remove_description(criterion.condition)
        deep_remove_description(criterion.then)
        if criterion.else_:
            deep_remove_description(criterion.else_)

def criterion_equal_ignore_description(c1: BaseCriterion, c2: BaseCriterion):
    return deep_remove_field(c1.model_dump(serialize_as_any=True, exclude_none=True), 'description') == \
        deep_remove_field(c2.model_dump(serialize_as_any=True, exclude_none=True), 'description')

def criteria_equal_ignore_description(criteria1: list[BaseCriterion], criteria2: list[BaseCriterion]):
    if len(criteria1) != len(criteria2):
        return False

    for c1, c2 in zip(criteria1, criteria2):
        if not criterion_equal_ignore_description(c1, c2):
            return False
    return True

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
