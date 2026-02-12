from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from aus_trial_universe.ctgov.utils.ii_normalisation import (
    fix_mojibake_df,
    fix_mojibake_str,
    is_effectively_empty,
    norm,
)

# -----------------------------------------------------------------------------
# Constants / parsing utilities
# -----------------------------------------------------------------------------

LEVEL_COLS = [f"level_{i}" for i in range(1, 8)]
CODE_RE = re.compile(r"\(([^()]+)\)\s*$")  # requires trailing "(CODE)"


def parse_name_code(cell: object) -> Optional[Tuple[str, str]]:
    """
    Parse 'Name (CODE)' -> (Name, CODE). CODE must be present as the trailing token.

    Returns None if:
    - blank/empty (including NA/unknown after normalization)
    - trailing (CODE) missing
    - name or code missing after parsing

    Note: this is intentionally strict (no fuzzy matching).
    """
    if is_effectively_empty(cell):
        return None
    s = fix_mojibake_str(str(cell)).strip()
    m = CODE_RE.search(s)
    if not m:
        return None
    code = m.group(1).strip()
    name = s[: m.start()].strip()
    if not code or not name:
        return None
    return name, code


# -----------------------------------------------------------------------------
# PrimaryTumor overwrite utilities (term -> level, OR splitting)
# -----------------------------------------------------------------------------

def split_or_terms(value: object) -> List[str]:
    """
    Split a mapping cell into OR terms using '|' only.
    Commas are treated as literal (no special handling).

    Always returns a list; blank/empty -> [].
    """
    if is_effectively_empty(value):
        return []
    s = fix_mojibake_str(str(value)).strip()
    parts = [p.strip() for p in s.split("|")]
    return [p for p in parts if p]  # drop empties


def build_term_to_level_index(oncotree_csv: str | Path) -> Dict[str, int]:
    """
    Build a strict lookup: norm('Name (CODE)') -> level_int (1..7),
    scanning every non-empty cell in level_1..level_7.

    Raises if:
    - required columns missing
    - the same normalized term appears at different levels
    """
    path = Path(oncotree_csv)
    df = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False, na_values=[])
    df.columns = [str(c).strip() for c in df.columns]
    df = fix_mojibake_df(df)

    missing = [c for c in LEVEL_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"OncoTree CSV missing expected columns: {missing}")

    term_to_level: Dict[str, int] = {}

    for _, row in df.iterrows():
        for level_idx, col in enumerate(LEVEL_COLS, start=1):
            cell = row.get(col)
            if is_effectively_empty(cell):
                continue
            term = fix_mojibake_str(str(cell)).strip()
            key = norm(term)
            if not key:
                continue

            prev = term_to_level.get(key)
            if prev is None:
                term_to_level[key] = level_idx
            elif prev != level_idx:
                raise ValueError(
                    f"Term appears at multiple levels: '{term}' -> {prev} vs {level_idx}"
                )

    return term_to_level


def levels_for_terms(terms: List[str], term_to_level: Dict[str, int]) -> Optional[List[int]]:
    """
    Convert a list of 'Name (CODE)' terms into parallel list of levels.
    Returns None if any term is not found (caller decides removal semantics).
    """
    levels: List[int] = []
    for t in terms:
        lvl = term_to_level.get(norm(t))
        if lvl is None:
            return None
        levels.append(lvl)
    return levels


# -----------------------------------------------------------------------------
# Tree data structures
# -----------------------------------------------------------------------------

@dataclass
class OncoTreeNode:
    code: str
    name: str
    level: int
    parent: Optional["OncoTreeNode"] = None
    children: Dict[str, "OncoTreeNode"] = field(default_factory=dict)

    @property
    def term(self) -> str:
        """Return canonical 'Name (CODE)'."""
        return f"{self.name} ({self.code})"

    def add_child(self, child: "OncoTreeNode") -> None:
        self.children[child.code] = child


class OncoTree:
    """
    Minimal OncoTree:
    - Node identity is CODE (unique key).
    - Build from oncotree.csv path-table (level_1..level_7).
    - Supports search + ancestor traversal + level lifting.
    """

    def __init__(self) -> None:
        self.nodes_by_code: Dict[str, OncoTreeNode] = {}
        self.roots: Dict[str, OncoTreeNode] = {}

    @classmethod
    def from_oncotree_csv(cls, path: str | Path) -> "OncoTree":
        """
        Build the tree from a CSV with columns level_1..level_7, each cell like 'Name (CODE)'.
        Ignores non-level columns (meta* etc).

        Validations:
        - same CODE cannot appear at multiple levels
        - parent consistency for repeated CODE occurrences
        """
        tree = cls()
        path = Path(path)

        df = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False, na_values=[])
        df.columns = [str(c).strip() for c in df.columns]
        df = fix_mojibake_df(df)

        missing = [c for c in LEVEL_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"OncoTree CSV missing expected columns: {missing}")

        for _, row in df.iterrows():
            prev: Optional[OncoTreeNode] = None

            for level_idx, col in enumerate(LEVEL_COLS, start=1):
                cell = row.get(col)
                parsed = parse_name_code(cell)
                if parsed is None:
                    continue

                name, code = parsed
                node = tree.nodes_by_code.get(code)

                if node is None:
                    node = OncoTreeNode(code=code, name=name, level=level_idx)
                    tree.nodes_by_code[code] = node
                else:
                    if node.level != level_idx:
                        raise ValueError(
                            f"CODE {code} appears at multiple levels: {node.level} vs {level_idx}"
                        )
                    # name drift would be surprising; keep first-seen but validate if different
                    if node.name != name:
                        raise ValueError(f"CODE {code} has inconsistent names: '{node.name}' vs '{name}'")

                if prev is None:
                    tree.roots[code] = node
                else:
                    if node.parent is None:
                        node.parent = prev
                        prev.add_child(node)
                    else:
                        if node.parent.code != prev.code:
                            raise ValueError(
                                f"Inconsistent parent for {code}: {node.parent.code} vs {prev.code}"
                            )

                prev = node

        return tree

    def get(self, code: str) -> Optional[OncoTreeNode]:
        return self.nodes_by_code.get(code)

    def lift_to_level(self, code: str, target_level: int) -> Optional[OncoTreeNode]:
        """Return the ancestor node at target_level (e.g. level 1 ancestor), or None if not found."""
        node = self.get(code)
        if node is None:
            return None
        cur = node
        while cur is not None and cur.level > target_level:
            cur = cur.parent
        if cur is None or cur.level != target_level:
            return None
        return cur

    def ancestors(self, code: str) -> List[OncoTreeNode]:
        """Return path root..node (inclusive)."""
        node = self.get(code)
        if node is None:
            return []
        path: List[OncoTreeNode] = []
        cur: Optional[OncoTreeNode] = node
        while cur is not None:
            path.append(cur)
            cur = cur.parent
        path.reverse()
        return path

    def ancestors_by_level(self, code: str) -> Dict[int, OncoTreeNode]:
        """Mapping {level: node} for the path root..entry. Includes the entry node."""
        return {n.level: n for n in self.ancestors(code)}

    def descendants(self, code: str) -> List[OncoTreeNode]:
        """Return all descendants (excluding the node itself)."""
        node = self.get(code)
        if node is None:
            return []
        out: List[OncoTreeNode] = []
        stack: List[OncoTreeNode] = list(node.children.values())
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(cur.children.values())
        return out

    def leaf_descendants(self, code: str) -> List[OncoTreeNode]:
        """Return all leaf descendants under the node (nodes with no children)."""
        node = self.get(code)
        if node is None:
            return []
        leaves: List[OncoTreeNode] = []
        stack: List[OncoTreeNode] = [node]
        while stack:
            cur = stack.pop()
            if not cur.children:
                if cur is not node:
                    leaves.append(cur)
                continue
            stack.extend(cur.children.values())
        return leaves
