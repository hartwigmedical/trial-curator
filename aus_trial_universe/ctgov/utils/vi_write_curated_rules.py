from __future__ import annotations

from pathlib import Path
from typing import Any, List


def _indent(s: str, n: int = 4) -> str:
    pad = " " * n
    return "\n".join(pad + line if line else line for line in s.splitlines())


def obj_to_source(obj: Any) -> str:
    if obj is None:
        return "None"
    if isinstance(obj, (str, int, float, bool)):
        return repr(obj)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        inner = ",\n".join(obj_to_source(x) for x in obj)
        return "[\n" + _indent(inner) + "\n]"
    if isinstance(obj, tuple):
        if not obj:
            return "()"
        inner = ", ".join(obj_to_source(x) for x in obj)
        return f"({inner},)" if len(obj) == 1 else f"({inner})"
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = ",\n".join(f"{repr(k)}: {obj_to_source(v)}" for k, v in obj.items())
        return "{\n" + _indent(items) + "\n}"

    d = getattr(obj, "__dict__", None)
    if not isinstance(d, dict):
        return repr(obj)

    cls = type(obj).__name__
    if not d:
        return f"{cls}()"

    args = [f"{k}={obj_to_source(v)}" for k, v in d.items()]
    joined = ",\n".join(args)
    return f"{cls}(\n{_indent(joined)}\n)"


def write_rules_py(rules: List[Any], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = "rules = " + obj_to_source(rules) + "\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path
