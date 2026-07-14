"""Deterministic POTTR drug-class lookup (spec §6.1) — offline reuse of the POTTR ontology, no judgement.

``drug_database.txt``: ``drug|alias|alias<TAB>class,class`` (a drug's leaf POTTR class(es)).
``drug_class_hierarchy.txt``: ``parent<TAB>child`` edges.
For a drug we find its leaf class and walk up to the root → the full ``root -> ... -> leaf`` hierarchy (the
CORRECT ordering, not an LLM guess). Read in place from the (legacy) drug_ontology raw inputs; cached.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from aus_trial_universe.agentic.core.pipeline_io import latest_version_dir

REPO_ROOT = Path(__file__).resolve().parents[4]
POTTR_ROOT = REPO_ROOT / "data/drug_utility_path/drug_ontology/raw_inputs/POTTR"
_WS_RE = re.compile(r"\s+")
_CLASS_SPLIT_RE = re.compile(r"\s*[,;]\s*")


def _norm(name: str) -> str:
    return _WS_RE.sub(" ", (name or "").strip().lower())


def _parse_dir(vdir: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Pure parse of a POTTR version dir -> (alias -> leaf classes, child -> parent)."""
    alias_to_classes: dict[str, list[str]] = {}
    for line in (vdir / "drug_database.txt").read_text(encoding="utf-8").splitlines():
        if "\t" not in line:
            continue
        names, classes = line.split("\t", 1)
        cls = [c.strip() for c in _CLASS_SPLIT_RE.split(classes) if c.strip()]
        for alias in names.split("|"):
            key = _norm(alias)
            if key and cls:
                alias_to_classes.setdefault(key, cls)
    child_to_parent: dict[str, str] = {}
    for line in (vdir / "drug_class_hierarchy.txt").read_text(encoding="utf-8").splitlines():
        if "\t" not in line:
            continue
        parent, child = (p.strip() for p in line.split("\t", 1))
        if parent and child:
            child_to_parent.setdefault(child, parent)  # first parent wins (POTTR is near-tree)
    return alias_to_classes, child_to_parent


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, list[str]], dict[str, str]]:
    return _parse_dir(latest_version_dir(POTTR_ROOT))


def _root_path(leaf: str, child_to_parent: dict[str, str]) -> list[str]:
    """Walk leaf -> root; return [root, ..., leaf]."""
    chain, seen, node = [leaf], {leaf}, leaf
    while node in child_to_parent:
        parent = child_to_parent[node]
        if parent in seen:      # cycle guard
            break
        chain.append(parent)
        seen.add(parent)
        node = parent
    return list(reversed(chain))


def _hierarchy(name: str, alias_to_classes: dict[str, list[str]], child_to_parent: dict[str, str]) -> str:
    leaves = alias_to_classes.get(_norm(name))
    if not leaves:
        return ""
    paths = [" -> ".join(_root_path(leaf, child_to_parent)) for leaf in leaves]
    return " | ".join(dict.fromkeys(paths))


def pottr_class_for(name: str) -> str:
    """POTTR hierarchy ``root -> ... -> leaf`` for a drug/alias; "" if not in POTTR. Multiple leaves joined by ' | '."""
    return _hierarchy(name, *_load())
