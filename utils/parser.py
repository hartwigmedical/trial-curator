import logging
import re

logger = logging.getLogger(__name__)

class ParseError(ValueError):
    """Raised when the CriterionParser encounters a syntax or structure error."""
    pass

class Parser:
    """Basic parser.
    """

    def __init__(self, text: str) -> None:
        self.text: str = text
        self.i: int = 0
        self.n: int = len(text)
        self.line_idx: int = 0

    def raise_error(self, message: str) -> None:
        lines = self.text.splitlines()
        start = max(0, self.line_idx - 2)
        end = min(len(lines), self.line_idx + 1)
        snippet = lines[start:end]
        pointer = ' ' * (self.i - (self.text.rfind('\n', 0, self.i) + 1)) + '^'
        raise ParseError(f"{message} (line {self.line_idx + 1}):\n" +
                         '\n'.join(snippet) + f"\n{pointer}")

    def peek(self) -> str:
        return self.text[self.i] if self.i < self.n else ''

    def consume(self) -> str:
        if self.i >= self.n:
            return ''
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

    def consume_quoted_string(self, quote_char='"') -> str:
        self.consume_whitespace()
        if self.peek() != quote_char:
            self.raise_error("Expected opening quote for string")
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
            elif c == quote_char:
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

    def consume_list_like(self, open_char: str, close_char: str, consumer: callable, missing_close_regex: str = None):
        self.consume_whitespace()
        if (next_char := self.consume()) != open_char:
            self.raise_error(f"Expected '{open_char}', got '{next_char}'")
        self.consume_whitespace()
        if self.peek() == close_char:
            self.consume()
            return

        while True:
            self.consume_whitespace()
            consumer()
            self.consume_whitespace()
            if missing_close_regex and re.match(missing_close_regex, self.text[self.i:]):
                # something is wrong, most likely LLM missed a closing bracket
                # issue a warning and close this list, do not consume the opening bracket
                # also leave the preceding comma alone. The outer parser will need it.
                logger.warning(f"Expected '{close_char}', got ',{next_char}', assuming a {close_char} is missing.")
                break
            if (next_char := self.consume()) not in (',', close_char):
                self.raise_error(f"Expected ',' or '{close_char}', got '{next_char}'")
            if next_char == close_char:
                break
