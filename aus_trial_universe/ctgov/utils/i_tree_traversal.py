import logging
from typing import Any, Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)


def iter_children(node: Any) -> List[Any]:
    """
    Return all children nodes from a node.

    Children are any nodes found in attributes:
        - `criteria`  (a list of nodes)
        - `criterion` (single child node)
        - `condition` (single child node)

    If it is a leaf node (no children), returns [].
    """
    children: List[Any] = []

    multi = getattr(node, "criteria", None)
    if multi is not None:
        if isinstance(multi, (list, tuple)):
            children.extend(list(multi))
        else:
            children.append(multi)

    for attr_name in ("criterion", "condition"):
        child = getattr(node, attr_name, None)
        if child is not None:
            children.append(child)

    return children


def walk_node(
        node: Any,
        visit_logic: Callable[[Any, Optional[Any], int], None],
        parent: Optional[Any] = None,
        depth: int = 0,
        _seen: Optional[set[int]] = None,
) -> None:
    """DFS starting from a single root node (a tree)."""
    if _seen is None:
        _seen = set()
    nid = id(node)
    if nid in _seen:
        return
    _seen.add(nid)

    visit_logic(node, parent, depth)

    for child in iter_children(node):
        walk_node(child, visit_logic, parent=node, depth=depth + 1, _seen=_seen)


def walk_forest(
        forest: Iterable[Any],
        visit_logic: Callable[[Any, Optional[Any], int], None],
) -> None:
    """Traverse a Forest (list of tree roots inside a Rule() object)."""
    for root in forest:
        walk_node(root, visit_logic, parent=None, depth=0)


def normalise_forest_into_list(rule: Any) -> List[Any]:
    """
    Normalise rule.curation into a list of root nodes.

    Handles three cases:
        - curation missing or None -> []
        - curation list/tuple      -> list(curation)
        - curation single node     -> [curation]
    """
    cur = getattr(rule, "curation", None)
    if cur is None:
        return []
    if isinstance(cur, (list, tuple)):
        return list(cur)
    return [cur]


def walk_trial(
        rules: Iterable[Any],
        visit_logic: Callable[[Any, Any, Optional[Any], int], None],
) -> None:
    """
    Traverse the trial file (a super-forest) containing all Rule objects.
    Callback signature: visit_logic(rule, node, parent, depth)
    """
    for rule in rules:
        rule_text = getattr(rule, "rule_text", None)
        if rule_text is None:
            logger.error("Missing rule text on a Rule object")
            raise ValueError("Missing rule text on a Rule object")

        forest = normalise_forest_into_list(rule)

        def _node_visitor(node: Any, parent: Optional[Any], depth: int, _rule=rule) -> None:
            visit_logic(_rule, node, parent, depth)

        walk_forest(forest, _node_visitor)
