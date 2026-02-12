from __future__ import annotations

from typing import Any, Iterable, List, Optional

from aus_trial_universe.ctgov.utils.i_tree_traversal import (
    normalise_forest_into_list,
    walk_forest,
    walk_trial,
)


def rule_has_search_criterion(rule: Any, searching_criteria: Iterable[str]) -> bool:
    """Return True if the Rule() contains at least one node whose class name is in searching_criteria."""
    searching = set(searching_criteria)
    found = False
    forest = normalise_forest_into_list(rule)

    def _node_search(node: Any, _parent: Optional[Any], _depth: int) -> None:
        nonlocal found
        if found:
            return
        if type(node).__name__ in searching:
            found = True

    walk_forest(forest, _node_search)
    return found


def filter_rules_by_search_criteria(rules: List[Any], searching_criteria: Iterable[str]) -> List[Any]:
    """Keep only Rule() objects that contain at least one searching criterion."""
    return [r for r in rules if rule_has_search_criterion(r, searching_criteria)]


def _prune_node(node: Any, searching: set[str]) -> bool:
    """
    Recursively prune a node's children so that only subtrees containing at least one
    searching criterion remain. Returns True if this node's subtree contains any target.
    """
    node_dict = getattr(node, "__dict__", None)
    if isinstance(node_dict, dict) and not node_dict:
        return False

    cls_name = type(node).__name__
    has_target = cls_name in searching

    crit_list = getattr(node, "criteria", None)
    if isinstance(crit_list, (list, tuple)):
        new_children: List[Any] = []
        for child in list(crit_list):
            if _prune_node(child, searching):
                new_children.append(child)
        setattr(node, "criteria", new_children)
        if new_children:
            has_target = True

    for attr_name in ("criterion", "condition"):
        child = getattr(node, attr_name, None)
        if child is not None:
            keep_child = _prune_node(child, searching)
            if keep_child:
                has_target = True
            else:
                setattr(node, attr_name, None)

    return has_target


def prune_nontarget_criteria_in_rule(rule: Any, searching_criteria: Iterable[str]) -> None:
    """For a single Rule(), prune its forest so only subtrees containing at least one searching criterion remain."""
    searching = set(searching_criteria)
    forest = normalise_forest_into_list(rule)
    new_forest: List[Any] = []
    for root in forest:
        if _prune_node(root, searching):
            new_forest.append(root)
    setattr(rule, "curation", new_forest)


def prune_nontarget_criteria_in_rules(rules: List[Any], searching_criteria: Iterable[str]) -> None:
    """Prune each Rule() so only subtrees containing at least one searching criterion remain."""
    for rule in rules:
        prune_nontarget_criteria_in_rule(rule, searching_criteria)


def remove_descriptions_from_rules(rules: List[Any]) -> None:
    """Remove `description` attribute from all nodes in all rules."""

    def _visit(_rule: Any, node: Any, _parent: Optional[Any], _depth: int) -> None:
        node_dict = getattr(node, "__dict__", None)
        if isinstance(node_dict, dict):
            node_dict.pop("description", None)

    walk_trial(rules, _visit)


def remove_exclude_and_flipped_from_rules(rules: List[Any]) -> None:
    """Remove `exclude` and `flipped` fields from Rule objects (top-level)."""
    for rule in rules:
        rule_dict = getattr(rule, "__dict__", None)
        if isinstance(rule_dict, dict):
            rule_dict.pop("exclude", None)
            rule_dict.pop("flipped", None)
