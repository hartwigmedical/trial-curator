import logging
from pathlib import Path
from typing import Any

import pydantic_curator.criterion_schema as cs

logger = logging.getLogger(__name__)


def load_curated_rules(py_filepath: Path) -> list[Any] | None:

    class Rule:  # Because the curated output is of the form: rules = [Rule(rule_text="...", exclude=False, flipped=False, curation=[...])]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    module_globs: dict[str, Any] = {  # to create a global namespace to give to exec() later.
        "__builtins__": __builtins__,  # to enable access to Python's built-in functions
        **{name: getattr(cs, name) for name in dir(cs)},  # dumps all the Pydantic models and base classes into this namespace
        "Rule": Rule,
    }

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
