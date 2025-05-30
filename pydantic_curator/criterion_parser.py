import re
from typing import Any

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

class CriterionParser:
    def __init__(self, text: str) -> None:
        self.text: str = text
        self.i: int = 0
        self.n: int = len(text)
        self.line_idx: int = 0

    def error(self, message: str) -> None:
        lines = self.text.splitlines()
        start = max(0, self.line_idx - 2)
        end = min(len(lines), self.line_idx + 1)
        snippet = lines[start:end]
        pointer = ' ' * (self.i - (self.text.rfind('\n', 0, self.i) + 1)) + '^'
        raise ValueError(f"{message} (line {self.line_idx + 1}):\n" +
                         '\n'.join(snippet) + f"\n{pointer}")

    def peek(self) -> str:
        return self.text[self.i] if self.i < self.n else ''

    def consume(self) -> str:
        c = self.text[self.i]
        self.i += 1
        if c == '\n':
            self.line_idx += 1
        return c

    def consume_while(self, condition: callable) -> str:
        result: str = ''
        while self.i < self.n and condition(self.text[self.i]):
            c = self.text[self.i]
            result += c
            self.i += 1
            if c == '\n':
                self.line_idx += 1
        return result

    def consume_whitespace(self) -> None:
        self.consume_while(str.isspace)

    def consume_identifier(self) -> str:
        return self.consume_while(lambda c: c.isalnum() or c == '_')

    def consume_quoted_string(self) -> str:
        if self.peek() != '"':
            self.error("Expected opening quote for string")
        self.i += 1  # skip opening quote
        s: str = ''
        escape: bool = False
        while self.i < self.n:
            c = self.text[self.i]
            if escape:
                s += c
                escape = False
            elif c == '\\':
                s += c
                escape = True
            elif c == '"':
                self.i += 1  # skip closing quote
                break
            else:
                s += c
            self.i += 1
        return s.encode('raw_unicode_escape').decode('unicode_escape')

    def is_eof(self) -> bool:
        return self.i >= self.n

    def startswith(self, s: str) -> bool:
        return self.text.startswith(s, self.i)

    def consume_list_like(self, open_char: str, close_char: str, consumer: callable):
        self.consume_whitespace()
        if (next_char := self.peek()) != open_char:
            self.error(f"Expected '{open_char}', got '{next_char}'")
        self.consume()
        self.consume_whitespace()
        if self.peek() == close_char:
            self.consume()
            return

        while True:
            self.consume_whitespace()
            consumer()
            self.consume_whitespace()
            if (next_char := self.peek()) not in (',', close_char):
                self.error(f"Expected ',' or '{close_char}', got '{next_char}'")
            self.consume()
            if next_char == close_char:
                break

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
                self.error(f"Invalid expresison: {v}'")

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
            self.error('Expected 1 criterion')
        return criteria[0]

    def consume_criterion(self) -> dict:
        self.consume_whitespace()
        if self.startswith('and') or self.startswith('or') or self.startswith('not') or self.startswith('if'):
            op = self.consume_identifier()
            if op in ('and', 'or'):
                return {
                    "type": op,
                    "criteria": self.consume_braced_criteria()
                }
            elif op == 'not':
                return {
                    "type": "not",
                    "criterion": self.consume_braced_criterion()
                }
            elif op == 'if':
                cond = self.consume_braced_criterion()
                self.consume_whitespace()

                identifier = self.consume_identifier()
                if identifier != 'then':
                    self.error(f"Expected 'then' after 'if', got '{identifier}'")

                then = self.consume_braced_criterion()

                result = {"type": "if", "condition": cond, "then": then}

                # check if there is else case
                self.consume_whitespace()
                if self.startswith('else_'):
                    self.consume_identifier()
                    result["else"] = self.consume_braced_criterion()
                return result
            self.error(f"Invalid expression: {op}")
        else:
            # Match terminal: e.g., diagnosis(finding="...")
            criterion_type = self.consume_identifier()
            args = self.consume_arg_list()
            return {'type': criterion_type, **args}

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
                self.error(f"Expected '=' after key, got {self.peek()} instead.")

        self.consume_list_like('(', ')', consume_arg)
        return args
