import inspect
from typing import Any

from utils.parser import Parser
from . import criterion_schema
from .criterion_schema import *

CRITERION_SCHEMA_CLS = {name: obj for name, obj in inspect.getmembers(criterion_schema) if inspect.isclass(obj)}

def parse_criterion(text: str) -> BaseCriterion:
    return CriterionParser(text).consume_criterion()

def parse_criterion_to_dict(text: str) -> BaseCriterion:
    return CriterionParser(text).consume_criterion()

class CriterionParser(Parser):
    def __init__(self, text: str) -> None:
        super().__init__(text)

    def consume_identifier(self) -> str:
        self.consume_whitespace()
        return self.consume_while(lambda c: c.isalnum() or c == '_')

    def consume_value(self) -> float | int | None | bool | list | str:
        if self.startswith('"'):
            return self.consume_quoted_string()
        elif self.startswith('['):
            return self.consume_list()
        v = self.consume_while(lambda c: c.isalnum() or c in '._-')
        if v == 'true':
            return True
        elif v == 'false':
            return False
        elif v == 'null':
            return None
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                self.raise_error(f"Invalid expresison: {v}'")

    def consume_list(self) -> list:
        items = []
        self.consume_list_like('[', ']', lambda: items.append(self.consume_value()))
        return items

    def consume_braced_criteria(self) -> list[dict[str, Any]]:
        criteria: list[dict[str, Any]] = []
        self.consume_list_like('{', '}', lambda: criteria.append(self.consume_criterion()))
        return criteria

    def consume_braced_criterion(self) -> dict[str, Any]:
        criteria = self.consume_braced_criteria()
        if len(criteria) != 1:
            self.raise_error('Expected 1 criterion')
        return criteria[0]

    def consume_criterion(self) -> Any:
        typename = self.consume_identifier()
        criterion_cls = None
        try:
            # construct directly
            criterion_cls = CRITERION_SCHEMA_CLS[typename + 'Criterion']
        except KeyError:
            self.raise_error(f"Unknown criterion type '{typename}'")

        self.consume_whitespace()

        # is there an arg list?
        args = {}
        if self.peek() == '(':
            args = self.consume_arg_list()

        # for composite types we need to parse the subcriterions
        if typename == 'And' or typename == 'Or':
            args["criteria"] = self.consume_braced_criteria()
        elif typename == 'Not' or typename == 'Timing':
            args["criterion"] = self.consume_braced_criterion()
        elif typename == 'If':
            args["condition"] = self.consume_braced_criterion()
            key = self.consume_identifier()
            if key != 'then':
                self.raise_error(f"Expected 'then' after 'If', got '{key}'")

            args["then"] = self.consume_braced_criterion()

            # check if there is else case
            self.consume_whitespace()
            if self.startswith('else'):
                if (key := self.consume_identifier()) != 'else':
                    self.raise_error(f"Expected 'else' after 'if', got '{key}'")
                args["else_"] = self.consume_braced_criterion()

        # construct directly
        return criterion_cls(**args)

    def consume_arg_list(self) -> dict[str, Any]:
        """
        consume arg list in the form of (key1=val1, key2="val2", key3(k="v")) to a nested dict.
        :return: dict of args.
        """
        args = {}

        def consume_arg():
            nonlocal args
            key = self.consume_identifier()
            self.consume_whitespace()
            if self.peek() == '=':
                self.consume()
                self.consume_whitespace()
                i = self.i
                try:
                    val = self.consume_value()
                    args[key] = val
                except ValueError:
                    self.i = i  # back track
                    # if it is not a value, perhaps it is class type
                    typename = self.consume_identifier()
                    try:
                        # construct directly
                        type_cls = CRITERION_SCHEMA_CLS[typename]
                        args[key] = type_cls(**self.consume_arg_list())
                    except KeyError:
                        self.raise_error(f"Unknown type '{typename}'")
            else:
                self.raise_error(f"Expected '=' after key, got {self.peek()} instead.")

        self.consume_list_like('(', ')', consume_arg)
        return args
