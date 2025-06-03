import re
from typing import Any

from utils.parser import Parser
from .criterion_schema import *

CRITERION_CLASS_MAP = {
    re.search(r'.*\.(\w+)Criterion', str(c)).group(1).lower(): c for c in BaseCriterion.__subclasses__()
}

def load_criterion(data: dict) -> BaseCriterion:
    if not isinstance(data, dict) or "type" not in data:
        raise ValueError("Invalid criterion object: missing 'type' field")

    criterion_type = data["type"].lower()
    cls = CRITERION_CLASS_MAP.get(criterion_type)
    if cls is None:
        raise ValueError(f"Unknown criterion type: {criterion_type}")

    # Recursively process nested fields
    processed_data = {}
    for key, value in data.items():
        if key == "type":
            continue
        if isinstance(value, dict) and "type" in value:
            processed_data[key] = load_criterion(value)
        elif isinstance(value, list) and all(isinstance(item, dict) and "type" in item for item in value):
            processed_data[key] = [load_criterion(item) for item in value]
        else:
            processed_data[key] = value

    return cls(type=criterion_type, **processed_data)

def parse_criterion(text: str) -> BaseCriterion:
    return load_criterion(parse_criterion_to_dict(text))

def parse_criterion_to_dict(text: str) -> dict:
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

    def consume_criterion(self) -> dict:
        identifier = self.consume_identifier()
        self.consume_whitespace()
        if identifier == 'and' or identifier == 'or':
            return {
                "type": identifier,
                "criteria": self.consume_braced_criteria()
            }
        elif identifier == 'not':
            return {
                "type": "not",
                "criterion": self.consume_braced_criterion()
            }
        elif identifier == 'if':
            cond = self.consume_braced_criterion()
            identifier = self.consume_identifier()
            if identifier != 'then':
                self.raise_error(f"Expected 'then' after 'if', got '{identifier}'")

            then = self.consume_braced_criterion()

            result = {"type": "if", "condition": cond, "then": then}

            # check if there is else case
            self.consume_whitespace()
            if self.startswith('else'):
                if (identifier := self.consume_identifier()) != 'else':
                    self.raise_error(f"Expected 'else' after 'if', got '{identifier}'")
                result["else"] = self.consume_braced_criterion()
            return result
        else:
            # Match terminal: e.g., diagnosis(finding="...")
            args = self.consume_arg_list()
            return {'type': identifier, **args}

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
            if self.peek() == '(':
                # function style call
                args[key] = self.consume_arg_list()
            elif self.peek() == '=':
                self.consume()
                self.consume_whitespace()
                args[key] = self.consume_value()
            else:
                self.raise_error(f"Expected '=' after key, got {self.peek()} instead.")

        self.consume_list_like('(', ')', consume_arg)
        return args
