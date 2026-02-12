"""
vi_write_curated_rules.py

Shared serializer for curated rule objects -> Python source file.

Goal:
- Provide a single, consistent writer used by all overwrite modules.
- Serialize Rule/Criterion trees in the same style as the original curated outputs:
    ClassName(attr=value, ...)
  assuming objects are attribute-only and class names are resolvable when importing.

Notes:
- This is intentionally strict and minimal: it does not try to pretty-print
  beyond stable indentation and preserving __dict__ insertion order.
- It does not import the criterion classes; it only writes source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List


def _indent(s: str, n: int = 4) -> str:
    pad = " " * n
    return "\n".join(pad + line if line else line for line in s.splitlines())


def obj_to_source(obj: Any) -> str:
    """
    Serialize an object tree to Python source.

    Supported:
    - None, bool, int, float, str
    - list, tuple, dict
    - objects with a __dict__ (attribute-only objects)

    For objects with __dict__:
      ClassName(
          attr=value,
          ...
      )
    preserving attribute insertion order in __dict__.
    """
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
        # Fallback for uncommon types; should be rare in curated trees.
        return repr(obj)

    cls = type(obj).__name__
    if not d:
        return f"{cls}()"

    args = [f"{k}={obj_to_source(v)}" for k, v in d.items()]
    joined = ",\n".join(args)
    return f"{cls}(\n{_indent(joined)}\n)"


def write_rules_py(rules: List[Any], out_path: str | Path) -> Path:
    """
    Write a curated rules python file with a top-level `rules = [...]`.

    Returns the output path.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = "rules = " + obj_to_source(rules) + "\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path
