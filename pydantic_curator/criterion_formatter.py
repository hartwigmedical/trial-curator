import re
from typing import Any

from pydantic_curator.criterion_schema import BaseCriterion, AndCriterion, OrCriterion, NotCriterion, IfCriterion
from trialcurator.utils import deep_remove_field

'''
write the criterion in a customised format:
and {
   diagnosticfinding(finding="histological or cytological documentation of cancer"),
   primarytumor(primary_tumor_location="colorectal", primary_tumor_type="metastatic colorectal cancer"),
   or {
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
    def format(criterion: BaseCriterion, keep_description=False) -> str:
        return CriterionFormatter._format(criterion, indent=0, keep_description=keep_description)

    @staticmethod
    def _format(criterion: BaseCriterion, indent: int, keep_description) -> str:
        indent_str = INDENT_UNIT * indent
        if isinstance(criterion, (AndCriterion, OrCriterion)):
            typename = criterion.type
            lines = []
            for subcriterion in criterion.criteria:
                lines.append(CriterionFormatter._format(subcriterion, indent + 1, keep_description))
            inner = ',\n'.join(lines)
            return (
                f'{indent_str}{typename} {{\n'
                f'{inner}\n'
                f'{indent_str}}}'
            )
        elif isinstance(criterion, NotCriterion):
            inner = CriterionFormatter._format(criterion.criterion, indent + 1, keep_description)
            return (
                f'{indent_str}not {{\n'
                f'{inner}\n'
                f'{indent_str}}}'
            )
        elif isinstance(criterion, IfCriterion):
            condition = CriterionFormatter._format(criterion.condition, indent + 1, keep_description)
            then = CriterionFormatter._format(criterion.then, indent + 1, keep_description)
            output = (f'{indent_str}if {{\n'
                      f'{condition}\n'
                      f'{indent_str}}} then {{\n'
                      f'{then}\n'
                      f'{indent_str}}}')
            if criterion.else_:
                else_ = CriterionFormatter._format(criterion.else_, indent + 1, keep_description)
                output = output + (f'\n{indent_str}else {{\n'
                                   f'{else_}}}\n'
                                   f'{indent_str}}}')
            return output
        else:
            # For normal (leaf) criteria, single-line compact JSON
            value_dict = criterion.model_dump(serialize_as_any=True, exclude_none=True)

            if not keep_description:
                value_dict = deep_remove_field(value_dict, 'description')

            compact_json = format_criterion(value_dict)

            # now we want to change those type = criterion to criterion ()
            compact_json = re.sub(f'type\\s*=\\s*\\"{criterion.type}\\",?\\s*',
                                  f'{criterion.type}(', compact_json) + ')'
            return indent_str + compact_json
