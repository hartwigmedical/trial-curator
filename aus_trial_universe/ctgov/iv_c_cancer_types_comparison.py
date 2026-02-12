from __future__ import annotations

import argparse
import ast
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from aus_trial_universe.ctgov.iii_b_load_curated_rules import load_curated_rules
from aus_trial_universe.ctgov.utils.i_tree_traversal import walk_trial
from aus_trial_universe.ctgov.utils.ii_normalisation import (
    fix_mojibake_str,
    is_effectively_empty,
    norm,
)
from aus_trial_universe.ctgov.utils.v_traverse_oncotree import (
    OncoTree,
    parse_name_code,
)

logger = logging.getLogger(__name__)

PRIMARY_TUMOR_CLASS_NAME = "PrimaryTumorCriterion"

# conditions_overwritten.csv expected columns
CSV_TRIAL_ID_COL = "trialId"
CSV_CONDITIONS_COL = "conditions"
CSV_COND_TERMS_COL = "Oncotree_term"
CSV_COND_LEVELS_COL = "Oncotree_level"


# =============================================================================
# Small helpers
# =============================================================================

def _norm_cell(x: Any) -> str:
    if is_effectively_empty(x):
        return ""
    return norm(fix_mojibake_str(str(x)))


def iter_input_py_files(primary_tumour_overwritten_dir: Path) -> Iterable[Path]:
    if primary_tumour_overwritten_dir.is_file():
        yield primary_tumour_overwritten_dir
        return
    yield from sorted(p for p in primary_tumour_overwritten_dir.glob("NCT*.py") if p.is_file())


def trial_id_from_py_stem(stem: str) -> str:
    """
    Normalize overwritten file stems to true trialId.

    Examples:
      - "NCT05057494_overwritten" -> "NCT05057494"
      - "NCT00026312" -> "NCT00026312"
    """
    s = str(stem).strip()
    suffix = "_overwritten"
    if s.endswith(suffix):
        return s[: -len(suffix)]
    return s


def parse_py_list_cell(cell: Any) -> List[Any]:
    """
    Parse a CSV cell that contains a Python-literal list string, e.g. "['A', 'B']" or "[1, None]".
    Returns [] on blank or parse failure.
    """
    if is_effectively_empty(cell):
        return []
    if isinstance(cell, list):
        return cell

    s = fix_mojibake_str(str(cell)).strip()
    try:
        parsed = ast.literal_eval(s)
    except Exception:
        logger.warning("Failed to parse list cell: %r", cell)
        return []
    return parsed if isinstance(parsed, list) else []


def load_conditions_overwritten_csv(path: Path) -> Dict[str, Dict[str, Any]]:
    """
    Returns mapping:
        trialId -> {
            "conditions": List[str],
            "cond_terms": List[str],
            "cond_levels": List[int|None],
        }
    """
    df = pd.read_csv(path, keep_default_na=False)

    missing = [
        c
        for c in [CSV_TRIAL_ID_COL, CSV_CONDITIONS_COL, CSV_COND_TERMS_COL, CSV_COND_LEVELS_COL]
        if c not in df.columns
    ]
    if missing:
        raise ValueError(f"conditions_overwritten CSV missing required columns: {missing}")

    out: Dict[str, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        trial_id = str(row.get(CSV_TRIAL_ID_COL)).strip()
        if not trial_id:
            continue

        conditions = [
            fix_mojibake_str(str(x))
            for x in parse_py_list_cell(row.get(CSV_CONDITIONS_COL))
            if not is_effectively_empty(x)
        ]

        cond_terms = [
            fix_mojibake_str(str(x))
            for x in parse_py_list_cell(row.get(CSV_COND_TERMS_COL))
            if not is_effectively_empty(x)
        ]

        cond_levels_raw = parse_py_list_cell(row.get(CSV_COND_LEVELS_COL))
        cond_levels: List[Optional[int]] = []
        for x in cond_levels_raw:
            if x is None or is_effectively_empty(x):
                cond_levels.append(None)
            else:
                try:
                    cond_levels.append(int(x))
                except Exception:
                    cond_levels.append(None)

        out[trial_id] = {
            "conditions": conditions,
            "cond_terms": cond_terms,
            "cond_levels": cond_levels,
        }

    return out


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def sibling_criterion_types(parent: Any, node: Any) -> List[str]:
    """
    Return sibling criterion class names for `node` under `parent`.
    Deduped (preserving first-seen order). Returned as list[str] (pandas will stringify).
    """
    if parent is None:
        return []

    sibs: List[Any] = []

    crits = getattr(parent, "criteria", None)
    if isinstance(crits, list):
        sibs.extend(c for c in crits if c is not node)

    crit_single = getattr(parent, "criterion", None)
    if crit_single is not None and crit_single is not node:
        sibs.append(crit_single)

    cond_single = getattr(parent, "condition", None)
    if cond_single is not None and cond_single is not node:
        sibs.append(cond_single)

    names = [type(s).__name__ for s in sibs]
    return _dedupe_preserve_order(names)


def safe_parse_code(term: str) -> Optional[str]:
    """
    Parse trailing CODE from an OncoTree term "Name (CODE)".
    Returns None if parse fails.
    """
    try:
        _, code = parse_name_code(term)
        return code
    except Exception:
        return None


def branch_relation(tree: OncoTree, pt_code: str, cond_code: str) -> Optional[str]:
    """
    Determine relationship of pt_code relative to cond_code:
      - "same" if identical
      - "upstream" if pt is ancestor of cond (broader)
      - "downstream" if pt is descendant of cond (more specific)
      - None otherwise (siblings / different branches)

    IMPORTANT: OncoTree.ancestors returns nodes, so compare via node.code.
    """
    if pt_code == cond_code:
        return "same"

    cond_anc_codes = {n.code for n in tree.ancestors(cond_code)}
    if pt_code in cond_anc_codes:
        return "upstream"

    pt_anc_codes = {n.code for n in tree.ancestors(pt_code)}
    if cond_code in pt_anc_codes:
        return "downstream"

    return None


# =============================================================================
# Primary tumour extraction (NO overwrite here)
# =============================================================================

def _iter_children_criteria(node: Any) -> Iterable[Any]:
    """Best-effort child iteration over Criterion-like AST nodes.

    The curated rule objects are a small, stable "criterion AST" with common fields like:
      - criteria: List[Criterion]
      - criterion: Criterion
      - condition / then / else_: Criterion (used by IfCriterion)
    """
    if node is None:
        return
    for attr in ("criteria",):
        v = getattr(node, attr, None)
        if isinstance(v, list):
            for c in v:
                if c is not None:
                    yield c

    for attr in ("criterion", "condition", "then", "else_"):
        v = getattr(node, attr, None)
        if v is not None:
            yield v


def subtree_contains_primary_tumor(node: Any) -> bool:
    """Return True iff this subtree contains a PrimaryTumorCriterion anywhere."""
    if node is None:
        return False
    if type(node).__name__ == PRIMARY_TUMOR_CLASS_NAME:
        return True
    for c in _iter_children_criteria(node):
        if subtree_contains_primary_tumor(c):
            return True
    return False


def non_pt_sibling_criterion_types(parent: Any, child: Any) -> List[str]:
    """Sibling criterion class names for `child` under `parent`, excluding PT-containing siblings.

    We only consider list-like siblings (i.e. elements of `parent.criteria`) because single-valued
    fields (criterion/condition/then/else_) don't have siblings in the structural sense.

    Siblings whose subtree contains a PrimaryTumorCriterion are excluded to avoid "self-like" clutter
    when the PT occurs inside an OR list.
    """
    if parent is None or child is None:
        return []

    sibs: List[Any] = []
    v = getattr(parent, "criteria", None)
    if isinstance(v, list) and any(x is child for x in v):
        sibs = [x for x in v if (x is not child) and (x is not None)]

    out: List[str] = []
    for s in sibs:
        if subtree_contains_primary_tumor(s):
            continue
        out.append(type(s).__name__)
    return out


class PrimaryTumourInstance:
    def __init__(
        self,
        *,
        trial_id: str,
        rule_exclude: bool,
        parent_path: Optional[str],
        sibling_classes: List[str],
        term: str,
        level: int,
    ) -> None:
        # "parent_path" is a human-readable breadcrumb, e.g. "AndCriterion -> IfCriterion -> OrCriterion"
        self.trial_id = trial_id
        self.rule_exclude = rule_exclude
        self.parent_path = parent_path
        self.sibling_classes = sibling_classes
        self.term = term
        self.level = level


def extract_primary_tumour_instances_from_rules(
    rules: List[Any],
    trial_id: str,
) -> List[PrimaryTumourInstance]:
    """Extract PrimaryTumorCriterion instances with structural context.

    Assumes PrimaryTumorCriterion nodes already contain Oncotree_term / Oncotree_level.
    Explodes OR terms into separate instances.

    Structural context:
      - parent_path: all ancestor criterion types from the root criterion to the PT's *direct* parent,
        joined with " -> ". This captures deeply nested trees.
      - sibling_classes: union of *non-PrimaryTumorCriterion-containing* siblings encountered along the
        ancestor chain at list-parent boundaries (parent.criteria lists). This surfaces the "other"
        constraints that co-occur with the PT branch (e.g., another criterion in an AndCriterion).
    """
    out: List[PrimaryTumourInstance] = []

    stack: List[Any] = []  # criterion nodes by DFS depth

    def _visit(rule: Any, node: Any, parent: Optional[Any], depth: int) -> None:
        # Maintain a best-effort ancestor stack using the provided DFS depth.
        # Depth is expected to be non-decreasing by 1 when descending, but we defend anyway.
        nonlocal stack
        if depth < 0:
            depth = 0
        while len(stack) > depth:
            stack.pop()
        if len(stack) == depth:
            stack.append(node)
        else:
            stack[depth] = node

        if type(node).__name__ != PRIMARY_TUMOR_CLASS_NAME:
            return

        terms = getattr(node, "Oncotree_term", None)
        levels = getattr(node, "Oncotree_level", None)

        if not isinstance(terms, list) or not isinstance(levels, list):
            # Not overwritten (or stage-only shape) -> ignore; caller logs via Warning if needed
            return

        if len(terms) != len(levels):
            logger.warning("Mismatched term/level lengths in %s", trial_id)
            return

        # Ancestor breadcrumb (exclude the PT node itself)
        ancestors = stack[:-1]
        parent_path = " -> ".join(type(a).__name__ for a in ancestors) if ancestors else None

        # Collect non-PT siblings along the ancestor chain at list-parent boundaries.
        sibling_classes: List[str] = []
        seen: set[str] = set()

        child_on_path: Any = node
        for p in reversed(ancestors):
            for cname in non_pt_sibling_criterion_types(p, child_on_path):
                if cname not in seen:
                    seen.add(cname)
                    sibling_classes.append(cname)
            child_on_path = p

        exclude = bool(getattr(rule, "exclude", False))

        for t, lvl in zip(terms, levels):
            if is_effectively_empty(t):
                continue
            try:
                lvl_i = int(lvl)
            except Exception:
                continue

            out.append(
                PrimaryTumourInstance(
                    trial_id=trial_id,
                    rule_exclude=exclude,
                    parent_path=parent_path,
                    sibling_classes=sibling_classes,
                    term=fix_mojibake_str(str(t)),
                    level=lvl_i,
                )
            )

    walk_trial(rules, _visit)
    return out


# =============================================================================
# Comparison logic
# =============================================================================

def compare_trial(
    *,
    trial_id: str,
    cond_payload: Optional[Dict[str, Any]],
    pt_instances: List[PrimaryTumourInstance],
    tree: OncoTree,
    warning: str = "",
) -> List[Dict[str, Any]]:

    conditions = cond_payload["conditions"] if cond_payload else []
    cond_terms = cond_payload["cond_terms"] if cond_payload else []
    cond_levels = cond_payload["cond_levels"] if cond_payload else []

    cond_codes: List[str] = []
    for t in cond_terms:
        code = safe_parse_code(t)
        if code is not None:
            cond_codes.append(code)

    rows: List[Dict[str, Any]] = []

    if not pt_instances:
        rows.append(
            {
                "trialId": trial_id,
                "conditions": conditions,
                "conditions_oncotree_terms": cond_terms,
                "conditions_oncotree_levels": cond_levels,
                "primarytumor_oncotree_term": None,
                "primarytumor_oncotree_level": None,
                "same_branch_as_conditions": None,
                "upstream_or_downstream_to_conditions": None,
                "inclusion_or_exclusion": None,
                "nested_within_criterion": None,
                "nested_with_criteria": [],
                "Warning": warning or None,
            }
        )
        return rows

    for inst in pt_instances:
        same_branch = False
        rel = None

        pt_code = safe_parse_code(inst.term)
        if pt_code and cond_codes:
            # First match wins (deterministic by conditions order)
            for cond_code in cond_codes:
                r = branch_relation(tree, pt_code, cond_code)
                if r is not None:
                    same_branch = True
                    rel = r
                    break

        rows.append(
            {
                "trialId": trial_id,
                "conditions": conditions,
                "conditions_oncotree_terms": cond_terms,
                "conditions_oncotree_levels": cond_levels,
                "primarytumor_oncotree_term": inst.term,
                "primarytumor_oncotree_level": inst.level,
                "same_branch_as_conditions": same_branch if cond_payload else None,
                "upstream_or_downstream_to_conditions": rel if same_branch else None,
                "inclusion_or_exclusion": "exclusion" if inst.rule_exclude else "inclusion",
                "nested_within_criterion": inst.parent_path,
                "nested_with_criteria": inst.sibling_classes,
                "Warning": warning or None,
            }
        )

    return rows


def build_comparison_rows(
    *,
    conditions_csv: Path,
    primary_tumour_overwritten_dir: Path,
    oncotree_csv: Path,
) -> List[Dict[str, Any]]:

    cond_by_trial = load_conditions_overwritten_csv(conditions_csv)

    py_by_trial: Dict[str, Path] = {}
    for p in iter_input_py_files(primary_tumour_overwritten_dir):
        tid = trial_id_from_py_stem(p.stem)
        if tid in py_by_trial and py_by_trial[tid] != p:
            logger.warning(
                "Duplicate overwritten .py for trialId %s: %s vs %s (keeping first)",
                tid,
                py_by_trial[tid],
                p,
            )
            continue
        py_by_trial[tid] = p

    all_trial_ids = sorted(set(cond_by_trial) | set(py_by_trial))

    tree = OncoTree.from_oncotree_csv(oncotree_csv)

    rows: List[Dict[str, Any]] = []

    for trial_id in all_trial_ids:
        cond_payload = cond_by_trial.get(trial_id)
        py_path = py_by_trial.get(trial_id)

        warnings: List[str] = []
        if cond_payload is None:
            warnings.append("Missing conditions CSV entry")
        if py_path is None:
            warnings.append("Missing curated NCT .py file")

        pt_instances: List[PrimaryTumourInstance] = []
        if py_path is not None:
            rules = load_curated_rules(py_path)
            if not rules:
                warnings.append("Failed to load curated rules .py")
            else:
                pt_instances = extract_primary_tumour_instances_from_rules(rules, trial_id)

        rows.extend(
            compare_trial(
                trial_id=trial_id,
                cond_payload=cond_payload,
                pt_instances=pt_instances,
                tree=tree,
                warning=" | ".join(warnings),
            )
        )

    return rows


# =============================================================================
# CLI
# =============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare mapped Conditions vs PrimaryTumorCriterion (branch relationship)."
    )
    parser.add_argument(
        "--conditions_overwritten_csv",
        required=True,
        type=Path,
        help="conditions_overwritten.csv",
    )
    parser.add_argument(
        "--primary_tumour_overwritten_dir",
        required=True,
        type=Path,
        help="Directory or file containing OVERWRITTEN NCT*.py (stem may end with _overwritten).",
    )
    parser.add_argument(
        "--oncotree_csv",
        required=True,
        type=Path,
        help="OncoTree hierarchy CSV",
    )
    parser.add_argument(
        "--out_csv",
        required=True,
        type=Path,
        help="Output comparison CSV",
    )
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    rows = build_comparison_rows(
        conditions_csv=args.conditions_overwritten_csv,
        primary_tumour_overwritten_dir=args.primary_tumour_overwritten_dir,
        oncotree_csv=args.oncotree_csv,
    )

    df = pd.DataFrame(rows)

    cols = [
        "trialId",
        "conditions",
        "conditions_oncotree_terms",
        "conditions_oncotree_levels",
        "primarytumor_oncotree_term",
        "primarytumor_oncotree_level",
        "same_branch_as_conditions",
        "upstream_or_downstream_to_conditions",
        "inclusion_or_exclusion",
        "nested_within_criterion",
        "nested_with_criteria",
        "Warning",
    ]
    df = df.reindex(columns=[c for c in cols if c in df.columns])

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out_csv, index=False)

    logger.info("Wrote %s", args.out_csv)
    return 0


if __name__ == "__main__":
    main()
