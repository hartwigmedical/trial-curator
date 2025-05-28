import re
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
    text = re.sub(r'\s+', ' ', text).strip()

    def parse_expr(expr: str) -> dict:
        expr = expr.strip()
        # Match and{...}, or{...}, not{...}, if{...}
        match = re.match(r'(\w+)\s*\{(.*)\}$', expr)
        if match:
            op, body = match.groups()
            op = op.lower()
            items = _split_top_level(body)

            if op in ('and', 'or'):
                return {
                    "type": op,
                    "criteria": [parse_expr(item) for item in items]
                }
            elif op == 'not':
                return {
                    "type": "not",
                    "criterion": parse_expr(items[0])
                }
            elif op == 'if':
                cond = parse_expr(items[0])
                then = parse_expr(items[1])
                else_ = parse_expr(items[2]) if len(items) > 2 else None
                result = {"type": "if", "condition": cond, "then": then}
                if else_:
                    result["else"] = else_
                return result
            raise ValueError(f"Invalid expression: {expr}")
        else:
            # Match terminal: e.g., diagnosis(finding="...")
            match = re.match(r'(\w+)\((.*)\)$', expr)
            if not match:
                raise ValueError(f"Invalid expression: {expr}")
            criterion_type, args = match.groups()
            return _parse_args(f'type="{criterion_type}", ' + args)

    return parse_expr(text)

def _split_top_level(s: str) -> list[str]:
    parts = []
    buf = ''
    depth = 0
    in_string = False
    escape = False

    for c in s:
        if escape:
            buf += c
            escape = False
            continue

        if c == '\\':
            buf += c
            escape = True
            continue

        if c == '"' and not escape:
            in_string = not in_string
            buf += c
            continue

        if not in_string:
            if c in '([{':
                depth += 1
            elif c in ')]}':
                depth -= 1
            if c == ',' and depth == 0:
                parts.append(buf.strip())
                buf = ''
                continue

        buf += c

    if buf.strip():
        parts.append(buf.strip())

    return parts

def _parse_args(s: str) -> dict:
    def parse_block(b):
        items = _split_top_level(b)
        result = {}
        for item in items:
            item = item.strip()
            # key=value
            if '=' in item and not re.search(r'\w+\s*\(', item):
                key, val = item.split('=', 1)
                result[key.strip()] = parse_value(val.strip())
            # key(...)
            elif m := re.match(r'(\w+)\((.*)\)$', item):
                key, inner = m.groups()
                result[key] = parse_block(inner)
            else:
                raise ValueError(f"Unrecognized format: {item}")
        return result

    def parse_value(v):
        # Handle quoted strings
        if v.startswith('"') and v.endswith('"'):
            return v[1:-1]
        elif v.lower() == 'true':
            return True
        elif v.lower() == 'false':
            return False
        elif v.lower() == 'null':
            return None
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v  # fallback as raw string

    return parse_block(s)
