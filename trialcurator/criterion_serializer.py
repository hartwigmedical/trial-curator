import re
from typing import Any

from trialcurator.criterion_schema import BaseCriterion, AndCriterion, OrCriterion, NotCriterion, IfCriterion
from trialcurator.utils import deep_remove_field

'''
write the criterion in a customised format:
and{
  diagnosticfinding(finding="histological or cytological documentation of cancer"),
  primarytumor(primary_tumor_location="colorectal", primary_tumor_type="metastatic colorectal cancer"),
  or{
    metastases(location="≥ 2 different organs", additional_details=[">1 extra-hepatic metastases"]),
  },
  surgery(surgical_procedure="Feasible radical tumor debulking"),
'''

def custom_format(obj: Any, indent: int = 0) -> str:
    indent_str = '  ' * indent

    if isinstance(obj, dict):
        items = []
        for k, v in obj.items():
            formatted_value = custom_format(v, 0)
            if isinstance(v, dict):
                # if this is a dictionary, we make it person(name="Tom", age=25)
                items.append(f'{indent_str}{k}({formatted_value})')
            else:
                items.append(f'{indent_str}{k}={formatted_value}')
        inner = ', '.join(items)
        return f'{inner}'
    elif isinstance(obj, list):
        items = [custom_format(item, indent + 1) for item in obj]
        inner = ', '.join(f'{item}' for item in items)
        return f'[{inner}]'
    elif isinstance(obj, str):
        return f'"{obj}"'  # string: add quotes
    elif obj is None:
        return 'null'  # match JSON null
    else:
        return str(obj)  # number, boolean: as is

class CriterionSerializer:
    @staticmethod
    def serialize(criterion: BaseCriterion) -> str:
        return CriterionSerializer._serialize(criterion, indent=0)

    @staticmethod
    def _serialize(criterion: BaseCriterion, indent: int) -> str:
        indent_str = '  ' * indent

        if isinstance(criterion, (AndCriterion, OrCriterion)):
            typename = criterion.type
            lines = []
            for subcriterion in criterion.criteria:
                lines.append(CriterionSerializer._serialize(subcriterion, indent + 1))
            inner = ',\n'.join(lines)
            return (
                f'{indent_str}{typename}{{\n'
                f'{inner}\n'
                f'{indent_str}}}'
            )
        elif isinstance(criterion, NotCriterion):
            inner = CriterionSerializer._serialize(criterion.criterion, 0)
            return f'{indent_str}not{{{inner}}}'
        elif isinstance(criterion, IfCriterion):
            condition = CriterionSerializer._serialize(criterion.condition, 0)
            then = CriterionSerializer._serialize(criterion.then, 0)
            output = f'{indent_str}if{{{condition}}} then {{{then}}}'
            if criterion.else_:
                else_ = CriterionSerializer._serialize(criterion.else_, 0)
                output = output + f' else {{{else_}}}'
            return output
        else:
            # For normal (leaf) criteria, single-line compact JSON
            value_dict = deep_remove_field(criterion.model_dump(serialize_as_any=True, exclude_none=True), 'description')
            compact_json = custom_format(value_dict)

            # now we want to change those type = criterion to criterion ()
            compact_json = re.sub(f'type\\s*=\\s*\\"{criterion.type}\\",?\\s*', f'{criterion.type}(', compact_json) + ')'
            return indent_str + compact_json

