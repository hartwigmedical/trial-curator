from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional

from aus_trial_universe.ctgov.utils.vii_tree_pruning import remove_node_from_parent

logger = logging.getLogger(__name__)


# Node creation

def create_new_criterion_node(
    old_node: Any,
    target_criterion_name: str,
) -> Optional[Any]:
    target_cls = None

    # 1) Same module as the old node
    module_name = type(old_node).__module__
    mod = sys.modules.get(module_name)
    if mod is not None:
        target_cls = getattr(mod, target_criterion_name, None)

    # 2) Loader fallback
    if target_cls is None:
        try:
            import aus_trial_universe.ctgov.ctgov_llm_curation_loader as loader_mod  # type: ignore
            target_cls = getattr(loader_mod, target_criterion_name, None)
        except Exception:
            target_cls = None

    # 3) Shim fallback (serialisable, but no behaviour)
    if target_cls is None:
        logger.warning(
            "Move_to: cannot resolve class '%s'; creating shim class for serialization.",
            target_criterion_name,
        )
        target_cls = type(target_criterion_name, (), {})

    try:
        return target_cls()
    except Exception as exc:
        logger.error(
            "Move_to: failed to instantiate '%s': %s",
            target_criterion_name,
            exc,
        )
        return None


# Node replacement

def replace_node_in_parent(
    rule: Any,
    parent: Optional[Any],
    old_node: Any,
    new_node: Any,
) -> None:

    if parent is None:
        cur = getattr(rule, "curation", None)
        if isinstance(cur, list):
            for i, child in enumerate(cur):
                if child is old_node:
                    cur[i] = new_node
                    return
        else:
            if cur is old_node:
                setattr(rule, "curation", new_node)
                return
        raise ValueError("Move_to: could not replace root node")

    crit_list = getattr(parent, "criteria", None)
    if isinstance(crit_list, list):
        for i, child in enumerate(crit_list):
            if child is old_node:
                crit_list[i] = new_node
                return

    for attr in ("criterion", "condition"):
        if getattr(parent, attr, None) is old_node:
            setattr(parent, attr, new_node)
            return

    raise ValueError(
        f"Move_to: could not replace node {type(old_node).__name__} "
        f"under parent {type(parent).__name__}"
    )


def replace_or_remove(
    rule: Any,
    parent: Optional[Any],
    old_node: Any,
    new_node: Optional[Any],
) -> None:

    if new_node is None:
        remove_node_from_parent(rule, parent, old_node)
    else:
        replace_node_in_parent(rule, parent, old_node, new_node)
