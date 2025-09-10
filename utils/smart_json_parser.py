import logging
import re
from typing import Any

from utils.parser import Parser

logger = logging.getLogger(__name__)


class SmartJsonParser(Parser):
    """A robust JSON parser that handles malformed JSON-like strings with custom error handling.

    This parser is designed to handle both standard JSON and slightly malformed JSON-like inputs,
    with additional handling for common JSON formatting mistakes output by LLM:
    1. key1: key2: val -> key1: {key2: val}
    2. missing closing }
    3. mathematical expressions
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)

    def consume_value(self) -> dict[str, Any] | list | str | int | float | bool | None:
        self.consume_whitespace()
        if self.startswith('"'):
            return self.consume_quoted_string()
        elif self.startswith('['):
            return self.consume_list()
        elif self.startswith('{'):
            return self.consume_dict()
        v = self.consume_while(lambda c: c.isalnum() or c in '._+-*/()% ').strip()
        if v == 'true':
            return True
        elif v == 'false':
            return False
        elif v == 'null':
            return None
        elif _is_math_expression(v):
            # evaluate math expressions
            try:
                return eval(v, {"__builtins__": None}, {})
            except:
                self.raise_error(f"Invalid math expressions: {v}")
        else:
            self.raise_error(f"Invalid expresison: {v}")

    def consume_list(self) -> list:
        items = []
        self.consume_list_like('[', ']', lambda: items.append(self.consume_value()))
        return items

    def consume_dict(self) -> dict[str, Any] | str:
        dictionary: dict[str, Any] = {}
        lone_value: str | None = None

        def consume_key_val():
            nonlocal dictionary
            nonlocal lone_value
            key = self.consume_quoted_string()
            self.consume_whitespace()
            if self.peek() == ':':
                self.consume()
                val = self.consume_value()
                self.consume_whitespace()
                if self.peek() == ':':
                    # sometimes LLM creates invalid JSON like
                    # key1: key2: val, change it to key1: {key2: val}
                    self.consume()
                    val = {val: self.consume_value()}
                dictionary[key] = val
            elif lone_value is None:
                # sometimes LLM creates invalid JSON like { "key" }, change it to just "key"
                lone_value = key
            else:
                self.raise_error(f"Expected ':' after key, got {self.peek()} instead.")

        # if we see ,{ or ] before reaching }, assume that the } is missing
        missing_close_regex = r'(,\s*{)|(\s*])'
        self.consume_list_like('{', '}', consume_key_val, missing_close_regex)

        if lone_value is not None:
            return lone_value

        return dictionary


def _is_math_expression(s: str) -> bool:
    return bool(re.fullmatch(r'[\d.\s+\-*/()%]+', s.strip()))
