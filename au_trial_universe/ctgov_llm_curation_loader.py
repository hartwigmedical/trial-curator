import logging
from pathlib import Path
from typing import Any

import pydantic_curator.criterion_schema as cs
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def load_curated_rules(py_filepath: Path) -> list[Any] | None:

    class Rule:  # Because the curated output is of the form: rules = [Rule(rule_text="...", exclude=False, flipped=False, curation=[...])]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def _make_shim(class_name: str):  # create shim to bypass validation errors
        def __init__(self, *_, **kwargs):
            self.__dict__.update(kwargs)
        return type(class_name, (), {"__init__": __init__})

    module_globs: dict[str, Any] = {"__builtins__": __builtins__}

    for name in dir(cs):
        obj = getattr(cs, name)

        if isinstance(obj, type):
            use_shim = False

            try:
                if issubclass(obj, BaseModel):  # Bypass validation & just extract the criteria if it is a subclass of Baseclass (i.e. all the criteria)
                    use_shim = True
            except TypeError:
                pass

            if name.endswith("Criterion"):
                use_shim = True

            module_globs[name] = _make_shim(name) if use_shim else obj
        else:
            module_globs[name] = obj

    module_globs["Rule"] = Rule

    try:
        curations = py_filepath.read_text(encoding="utf-8")
        exec(
            compile(curations, str(py_filepath), "exec"),
            module_globs
        )
    except SyntaxError as e:
        logger.error("SyntaxError in %s:%s\n%s", py_filepath, e.lineno, e.msg)
        return None
    except Exception as e:
        logger.exception("While executing %s: %s", py_filepath, e)
        return None

    rules = module_globs.get("rules")
    if rules is None:
        logger.error("%s has no `rules` variable", py_filepath)
        return None

    return rules
