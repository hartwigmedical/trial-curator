from typing import Any

from pydantic import BaseModel

from .criterion_schema import BaseCriterion

'''
write the criterion in a customised format:
And {
   DiagnosticFinding(finding="histological or cytological documentation of cancer"),
   PrimaryTumor(primary_tumor_location="colorectal", primary_tumor_type="metastatic colorectal cancer"),
   or {
      Metastases(location="≥ 2 different organs", additional_details=[">1 extra-hepatic metastases"]),
   },
   PriorTreatment(treatment=RadioTherapy())
}
'''

INDENT_UNIT = '    '

def add_indentation(input: str, indent: int) -> str:
    return input.replace('\n', '\n' + INDENT_UNIT * indent)

def format_criterion(obj: Any) -> str:
    return format_dump(obj)

def format_dump(item: Any, indent: int = 0):

    def criterion_type(cls_name: str) -> str:
        return cls_name.replace('Criterion', '')

    indent_str = INDENT_UNIT * indent

    match item:
        case BaseCriterion() as criterion:
            args = {}
            subcriteria_fields = []

            for field, value in criterion:
                if field == 'type':
                    continue
                elif isinstance(value, BaseCriterion):
                    subcriteria_fields.append((field, format_dump(value, 1)))
                elif isinstance(value, list) and value and isinstance(value[0], BaseCriterion):
                    lines = [format_dump(subcriterion, 1) for subcriterion in criterion.criteria ]
                    inner = ',\n'.join(lines)
                    subcriteria_fields.append((field, inner))
                elif value is not None:
                    #print(f'{field}, type={type(value)}')
                    args[field] = value

            argstr = ''

            if args:
                argstr = ',\n'.join([f'{k}={format_dump(v)}' for k, v in args.items()])
                argstr = add_indentation(f'({argstr})', indent=indent + 1)

            subcriteria_str = ''

            if subcriteria_fields:
                # to make the format nicer, if the field name is criterion or criteria, we omit the field name
                subcriteria_fields = [('' if k in ('criterion', 'criteria') else k, v) for k, v in subcriteria_fields]
                subcriteria_str = '\n' + '\n'.join([f'{k}{{\n{v}\n}}' for k, v in subcriteria_fields])
                subcriteria_str = add_indentation(subcriteria_str, indent=indent)

            return f'{indent_str}{criterion_type(criterion.__class__.__name__)}{argstr}{subcriteria_str}'

        case BaseModel() as model:
            #print(item)
            subitems = {}
            for field, value in model:
                if field != 'type' and value is not None:
                    subitems[field] = format_dump(value)

            argsstr = add_indentation(','.join([f'{k}={v}' for k, v in subitems.items()]), indent=indent+1)
            return f'{model.__class__.__name__}({argsstr})'

        case list() as container:  # pyright: ignore
            # Pyright finds this disgusting; this passes `mypy` though. `  # type:
            # ignore` would fail `mypy` is it'd be unused (because there's nothing to
            # ignore because `mypy` is content)
            return '[' + ', '.join([format_dump(i) for i in container]) + ']'
        case dict():
            return {
                k: format_dump(v)
                for k, v in item.items()  # pyright: ignore[reportUnknownVariableType]
            }
        case str():
            return f'"{item}"'
        case _:
            return str(item)

def format_like_py_code(item: Any, indent: int = 0):
    match item:
        case BaseModel() as model:
            subitems = {}
            for field, value in model:
                if field != 'type' and value is not None:
                    subitems[field] = format_dump(value)

            argsstr = add_indentation('\n' + ',\n'.join([f'{k} = {v}' for k, v in subitems.items()]), indent=indent+1)
            return f'{model.__class__.__name__}({argsstr}\n)'

        case list() | tuple() | set() as container:  # pyright: ignore
            return type(container)(  # pyright: ignore
                format_dump(i) for i in container  # pyright: ignore
            )
        case dict():
            return {
                k: format_dump(v)
                for k, v in item.items()  # pyright: ignore[reportUnknownVariableType]
            }
        case str():
            return f'"{item}"'
        case _:
            return str(item)
