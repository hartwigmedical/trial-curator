import logging
from typing import Any, Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)


CHILD_ATTRS: tuple[str, ...] = (
    "criteria",
    "criterion",
    "condition",
    "then",
    "else_",
)


def iter_children(node: Any) -> List[Any]:
    """
    Return all child nodes of a curation-tree node.

    Children are collected from every attribute that can hold sub-nodes:
        - `criteria`  (a list of nodes, e.g. on logic nodes)
        - `criterion` (single child node)
        - `condition` (the test of an If node)
        - `then`      (the "then" branch of an If node)
        - `else_`     (the "else" branch of an If node)

    Any of these may be a single node or a list/tuple of nodes; missing or
    None attributes contribute nothing.  Leaf nodes return [].

    This is the single, general curation-tree child enumerator: it includes the
    If-branches (`then`/`else_`) so callers never silently skip nodes nested
    inside conditional logic.
    """
    children: List[Any] = []

    for attr_name in CHILD_ATTRS:
        value = getattr(node, attr_name, None)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            children.extend(value)
        else:
            children.append(value)

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
