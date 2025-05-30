import re
from typing import Any

from pydantic_curator.criterion_schema import BaseCriterion, AndCriterion, OrCriterion, NotCriterion, IfCriterion
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
  priortherapy(therapy="radiotherapy", timing_info(reference="now", window_days(min_inclusive=30)))
}
'''

INDENT_UNIT = '   '

def format_criterion(obj: Any, indent: int = 0) -> str:
    indent_str = INDENT_UNIT * indent

    if isinstance(obj, dict):
        items = []
        for k, v in obj.items():
            formatted_value = format_criterion(v, 0)
            if isinstance(v, dict):
                # if this is a dictionary, we make it person(name="Tom", age=25)
                items.append(f'{indent_str}{k}({formatted_value})')
            else:
                items.append(f'{indent_str}{k}={formatted_value}')
        inner = ', '.join(items)
        return f'{inner}'
    elif isinstance(obj, list):
        items = [format_criterion(item, indent + 1) for item in obj]
        inner = ', '.join(f'{item}' for item in items)
        return f'[{inner}]'
    elif isinstance(obj, str):
        return f'"{obj}"'  # string: add quotes
    elif obj is None:
        return 'null'  # match JSON null
    else:
        return str(obj)  # number, boolean: as is

class CriterionFormatter:
    @staticmethod
    def format(criterion: BaseCriterion) -> str:
        return CriterionFormatter._format(criterion, indent=0)

    @staticmethod
    def _format(criterion: BaseCriterion, indent: int) -> str:
        indent_str = INDENT_UNIT * indent
        if isinstance(criterion, (AndCriterion, OrCriterion)):
            typename = criterion.type
            lines = []
            for subcriterion in criterion.criteria:
                lines.append(CriterionFormatter._format(subcriterion, indent + 1))
            inner = ',\n'.join(lines)
            return (
                f'{indent_str}{typename} {{\n'
                f'{inner}\n'
                f'{indent_str}}}'
            )
        elif isinstance(criterion, NotCriterion):
            inner = CriterionFormatter._format(criterion.criterion, indent + 1)
            return (
                f'{indent_str}not {{\n'
                f'{inner}\n'
                f'{indent_str}}}'
            )
        elif isinstance(criterion, IfCriterion):
            condition = CriterionFormatter._format(criterion.condition, 0)
            then = CriterionFormatter._format(criterion.then, 0)
            output = f'{indent_str}if {{{condition}}}\n{indent_str} then {{{then}}}'
            if criterion.else_:
                else_ = CriterionFormatter._format(criterion.else_, 0)
                output = output + f'\n{indent_str} else {{{else_}}}'
            return output
        else:
            # For normal (leaf) criteria, single-line compact JSON
            value_dict = deep_remove_field(criterion.model_dump(serialize_as_any=True, exclude_none=True), 'description')
            compact_json = format_criterion(value_dict)

            # now we want to change those type = criterion to criterion ()
            compact_json = re.sub(f'type\\s*=\\s*\\"{criterion.type}\\",?\\s*', f'{criterion.type}(', compact_json) + ')'
            return indent_str + compact_json
