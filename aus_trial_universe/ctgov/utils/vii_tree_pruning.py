from __future__ import annotations

from typing import Any, Optional


def remove_node_from_parent(rule: Any, parent: Optional[Any], node: Any) -> None:
    """
    Remove `node` from its parent without collapsing containers.

    Supported parent shapes:
    - parent is None: remove from rule.curation (list) or set singleton curation to []
    - parent.criteria: list -> remove identity matches
    - parent.criterion / parent.condition: set to None if identity match

    Raises if it cannot remove the node deterministically.
    """
    if parent is None:
        cur = getattr(rule, "curation", None)
        if isinstance(cur, list):
            setattr(rule, "curation", [x for x in cur if x is not node])
        elif cur is node:
            setattr(rule, "curation", [])
        return

    crit_list = getattr(parent, "criteria", None)
    if isinstance(crit_list, list):
        parent.criteria = [x for x in crit_list if x is not node]  # type: ignore[attr-defined]
        return

    for attr in ("criterion", "condition"):
        if getattr(parent, attr, None) is node:
            setattr(parent, attr, None)
            return

    raise ValueError(
        f"Could not remove node {type(node).__name__} from parent {type(parent).__name__}"
    )
