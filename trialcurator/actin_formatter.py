
def actin_json_to_text_format(criterion: dict) -> str:
    description = criterion.get("description", "").strip()
    rule_expr = render_rule(criterion["actin_rule"])
    new_rules = criterion.get("new_rule")

    output = (
        f"Input:\n    {description}\n"
        f"ACTIN Output:\n{indent_multiline(rule_expr)}\n"
        f"New rule:\n    {str(new_rules)}"
    )
    return output

def render_rule(rule_obj, indent=4):
    indent_str = " " * indent
    inner_indent_str = " " * (indent + 2)

    if isinstance(rule_obj, dict):
        for key, value in rule_obj.items():
            if key in ("AND", "OR"):
                rendered = "\n".join(
                    f"{inner_indent_str}{render_rule(r, indent + 2).lstrip()}" for r in value
                )
                return f"{key}: [\n{rendered}\n{indent_str}]"
            elif key == "NOT":
                return f"NOT({render_rule(value, indent)})"
            elif key == "IF":
                condition = render_rule(rule_obj[key]["condition"], indent + 2).lstrip()
                then = render_rule(rule_obj[key]["then"], indent + 2).lstrip()
                else_clause = rule_obj[key].get("else")
                if else_clause:
                    else_rendered = render_rule(else_clause, indent + 2).lstrip()
                    return f"IF {condition} THEN {then} ELSE {else_rendered}"
                return f"IF {condition} THEN {then}"
            else:
                param_str = ", ".join(str(v) for v in value)
                return f"{key}[{param_str}]"
    else:
        return str(rule_obj)

def indent_multiline(text, indent=4):
    pad = " " * indent
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())
