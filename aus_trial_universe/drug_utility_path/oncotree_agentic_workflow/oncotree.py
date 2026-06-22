from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import OncoTreeNode

DEFAULT_ONCOTREE_YAML = Path("data/eligibility_resources/oncotree/oncotree.yaml")


@dataclass(frozen=True)
class OncoTree:
    nodes_by_code: dict[str, OncoTreeNode]
    root_codes: tuple[str, ...]

    @classmethod
    def from_yaml(cls, path: str | Path = DEFAULT_ONCOTREE_YAML) -> "OncoTree":
        return load_oncotree_yaml(path)

    def has_code(self, code: str) -> bool:
        return code in self.nodes_by_code

    def get(self, code: str) -> OncoTreeNode | None:
        return self.nodes_by_code.get(code)

    def ancestors(self, code: str) -> tuple[OncoTreeNode, ...]:
        node = self.get(code)
        if node is None:
            return ()

        path: list[OncoTreeNode] = []
        current: OncoTreeNode | None = node
        while current is not None:
            path.append(current)
            current = (
                self.nodes_by_code.get(current.parent_code)
                if current.parent_code is not None
                else None
            )
        return tuple(reversed(path))

    def path_names(self, code: str) -> tuple[str, ...]:
        return tuple(node.name for node in self.ancestors(code))

    def is_ancestor(self, ancestor_code: str, descendant_code: str) -> bool:
        return any(
            node.code == ancestor_code
            for node in self.ancestors(descendant_code)[:-1]
        )

    def root_nodes(self) -> tuple[OncoTreeNode, ...]:
        return tuple(self.nodes_by_code[code] for code in self.root_codes)

    def children(self, code: str) -> tuple[OncoTreeNode, ...]:
        node = self.get(code)
        if node is None:
            return ()
        return tuple(
            self.nodes_by_code[child_code]
            for child_code in node.children_codes
            if child_code in self.nodes_by_code
        )


def remove_ancestor_codes(codes: Iterable[str], tree: "OncoTree") -> tuple[str, ...]:
    """Drop any code that is an ancestor of another code in the same set.

    When the navigator emits both a parent and one of its descendants, only the
    more specific descendant is kept.
    """

    unique_codes = tuple(dict.fromkeys(codes))
    kept: list[str] = []
    for code in unique_codes:
        if any(
            other != code and tree.is_ancestor(code, other)
            for other in unique_codes
            if tree.has_code(other)
        ):
            continue
        kept.append(code)
    return tuple(kept)


def load_oncotree_yaml(path: str | Path = DEFAULT_ONCOTREE_YAML) -> OncoTree:
    """Load the project OncoTree YAML without requiring PyYAML.

    This parser intentionally supports only the simple resource shape used by
    data/eligibility_resources/oncotree/oncotree.yaml:
    nested lists of {code, name, level, children}.
    """

    path = Path(path)
    nodes: dict[str, _MutableNode] = {}
    roots: list[str] = []
    stack: list[tuple[int, _MutableNode]] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped == "children:":
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if stripped.startswith("- code:"):
            code = _value_after_colon(stripped)
            while stack and indent <= stack[-1][0]:
                stack.pop()

            parent = stack[-1][1] if stack else None
            node = _MutableNode(
                code=code,
                parent_code=parent.code if parent is not None else None,
            )
            if code in nodes:
                raise ValueError(f"Duplicate OncoTree code in {path}: {code}")
            nodes[code] = node
            if parent is None:
                roots.append(code)
            else:
                parent.children_codes.append(code)
            stack.append((indent, node))
            continue

        if not stack:
            continue

        current = stack[-1][1]
        if stripped.startswith("name:"):
            current.name = _value_after_colon(stripped)
        elif stripped.startswith("level:"):
            current.level = int(_value_after_colon(stripped))

    missing = [
        node.code
        for node in nodes.values()
        if not node.name or node.level < 1
    ]
    if missing:
        raise ValueError(f"Incomplete OncoTree node(s) in {path}: {missing[:10]}")

    immutable_nodes = {
        code: OncoTreeNode(
            code=node.code,
            name=node.name,
            level=node.level,
            parent_code=node.parent_code,
            children_codes=tuple(node.children_codes),
        )
        for code, node in nodes.items()
    }
    return OncoTree(nodes_by_code=immutable_nodes, root_codes=tuple(roots))


@dataclass
class _MutableNode:
    code: str
    parent_code: str | None = None
    name: str = ""
    level: int = 0
    children_codes: list[str] = field(default_factory=list)


def _value_after_colon(line: str) -> str:
    return line.split(":", 1)[1].strip().strip("\"'")

