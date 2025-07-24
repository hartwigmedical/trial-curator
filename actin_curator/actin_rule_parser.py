from typing import Any

from utils.parser import Parser

def parse_actin_rule(text: str) -> dict[str, list]:
    return ActinRuleParser(text).consume_rule()

class ActinRuleParser(Parser):
    def __init__(self, text: str) -> None:
        super().__init__(text)

    def consume_identifier(self) -> str:
        self.consume_whitespace()
        return self.consume_while(lambda c: c.isalnum() or c == '_')

    def consume_value(self) -> float | int | None | bool | list | str:
        if self.startswith("'"):
            return self.consume_quoted_string("'")
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
                self.raise_error(f"Invalid expression: {v}'")

    def consume_list(self) -> list:
        items = []
        self.consume_list_like('[', ']', lambda: items.append(self.consume_value()))
        return items

    def consume_subrules(self) -> list[dict[str, Any]]:
        subrules: list[dict[str, Any]] = []
        self.consume_list_like('(', ')', lambda: subrules.append(self.consume_rule()))
        return subrules

    def consume_rule(self) -> dict[str, list | dict]:
        rule_name = self.consume_identifier()
        self.consume_whitespace()

        # for composite types we need to parse the subrule that are in ()
        if rule_name in ('AND', 'OR', 'NOT'):
            subrules = self.consume_subrules()
            if rule_name == 'NOT':
                if len(subrules) != 1:
                    self.raise_error('Expected 1 subrule in NOT')
                return {rule_name: subrules[0]}
            else:
                return {rule_name: subrules}

        else:
            # is there an arg list?
            args = []
            if self.peek() == '[':
                args = self.consume_list()
            return {rule_name: args}
