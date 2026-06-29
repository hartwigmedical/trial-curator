"""The single shared curation-tree walker must enumerate If-branch children.

Previously two copies of the child enumerator disagreed: the shared one skipped
``then``/``else_`` branches while ``primary_vs_conditions`` walked them.  They are
now unified on this general enumerator, so nodes nested inside conditional logic
are never silently skipped.
"""

from aus_trial_universe.eligibility_path.shared.utils.traverse_curation_tree import (
    iter_children,
    walk_node,
)


class _Node:
    def __init__(self, name, **attrs):
        self.name = name
        for key, value in attrs.items():
            setattr(self, key, value)


def test_iter_children_includes_if_branches():
    if_node = _Node(
        "if",
        condition=_Node("cond"),
        then=_Node("then_leaf"),
        else_=_Node("else_leaf"),
    )
    assert {c.name for c in iter_children(if_node)} == {"cond", "then_leaf", "else_leaf"}


def test_iter_children_handles_lists_and_singletons():
    logic = _Node("logic", criteria=[_Node("a"), _Node("b")], criterion=_Node("c"))
    assert [c.name for c in iter_children(logic)] == ["a", "b", "c"]


def test_iter_children_leaf_returns_empty():
    assert iter_children(_Node("leaf")) == []


def test_walk_node_visits_nodes_nested_in_if_branches():
    root = _Node(
        "root",
        criterion=_Node("if", then=_Node("then", criteria=[_Node("deep")])),
    )
    visited = []
    walk_node(root, lambda node, parent, depth: visited.append(node.name))
    assert {"root", "if", "then", "deep"} <= set(visited)
