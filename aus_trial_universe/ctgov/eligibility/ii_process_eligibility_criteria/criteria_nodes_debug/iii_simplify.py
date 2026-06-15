from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Iterable, List, Optional, Set

from aus_trial_universe.eligibility_utils.general.load_curated_rules import load_curated_rules
from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.criteria_registry import (
    SUPPORTED_CRITERIA,
    CRITERION_TO_CLASSNAME,
)
from aus_trial_universe.eligibility_utils.general import (
    normalise_forest_into_list,
    walk_trial,
)
from aus_trial_universe.eligibility_utils.general import (
    prune_nontarget_criteria_in_rules,
    remove_descriptions_from_rules,
    remove_exclude_and_flipped_from_rules,
)
from aus_trial_universe.eligibility_utils.general import write_rules_py
from aus_trial_universe.eligibility_utils.general import remove_node_from_parent

logger = logging.getLogger(__name__)

TREATMENT_RELATED_CLASSNAMES = {"PriorTreatmentCriterion", "CurrentTreatmentCriterion", "TreatmentOptionCriterion"}


# ---------------------------
# Generic helpers
# ---------------------------

def _iter_input_py_files(input_dir: Path) -> Iterable[Path]:
    if input_dir.is_file():
        yield input_dir
        return
    yield from sorted(p for p in input_dir.glob("NCT*_filtered.py") if p.is_file())


def _children_of(node: Any) -> List[Any]:
    out: List[Any] = []

    crit_list = getattr(node, "criteria", None)
    if isinstance(crit_list, list):
        out.extend(child for child in crit_list if child is not None)

    child = getattr(node, "criterion", None)
    if child is not None:
        out.append(child)

    return out


def _subtree_contains_class(node: Any, classnames: Set[str]) -> bool:
    if node is None:
        return False

    if type(node).__name__ in classnames:
        return True

    return any(_subtree_contains_class(child, classnames) for child in _children_of(node))


# ---------------------------
# NOT-treatment exception
# ---------------------------

def _enclosing_notcriteria(ancestors: List[Any]) -> List[Any]:
    return [anc for anc in ancestors if type(anc).__name__ == "NotCriterion"]


def _selected_node_should_be_dropped_in_not_context(
    node: Any,
    ancestors: List[Any],
    selected_criterion_classnames: Set[str],
) -> bool:
    if type(node).__name__ not in selected_criterion_classnames:
        return False

    enclosing_nots = _enclosing_notcriteria(ancestors)
    if not enclosing_nots:
        return False

    for not_node in reversed(enclosing_nots):
        not_child = getattr(not_node, "criterion", None)
        if not_child is None:
            continue
        if _subtree_contains_class(not_child, TREATMENT_RELATED_CLASSNAMES):
            return True

    return False


def drop_selected_criteria_in_treatment_not_contexts(
    rules: List[Any],
    *,
    selected_criterion_classnames: Set[str],
) -> None:
    def _walk(rule: Any, node: Any, parent: Optional[Any], ancestors: List[Any]) -> None:
        if _selected_node_should_be_dropped_in_not_context(
            node,
            ancestors,
            selected_criterion_classnames,
        ):
            logger.debug(
                "Dropping %s under NotCriterion because same NotCriterion subtree contains treatment",
                type(node).__name__,
            )
            remove_node_from_parent(rule, parent, node)
            return

        for child in list(_children_of(node)):
            _walk(rule, child, node, ancestors + [node])

    for rule in rules:
        forest = normalise_forest_into_list(rule)
        for root in list(forest):
            _walk(rule, root, None, [])


# ---------------------------
# Cleanup / minify logic
# ---------------------------

def _cleanup_empty_containers_in_rule(rule: Any) -> None:
    forest = normalise_forest_into_list(rule)

    def _clean(node: Any) -> bool:
        cls = type(node).__name__

        crit_list = getattr(node, "criteria", None)
        if isinstance(crit_list, list):
            new_children: List[Any] = []
            for child in crit_list:
                if _clean(child):
                    new_children.append(child)
            setattr(node, "criteria", new_children)

        if hasattr(node, "criterion"):
            child = getattr(node, "criterion", None)
            if child is not None and not _clean(child):
                setattr(node, "criterion", None)

        if cls in ("AndCriterion", "OrCriterion"):
            c = getattr(node, "criteria", None)
            return isinstance(c, list) and len(c) > 0

        if cls == "NotCriterion":
            return getattr(node, "criterion", None) is not None

        return True

    new_forest: List[Any] = [root for root in forest if _clean(root)]
    setattr(rule, "curation", new_forest)


def cleanup_empty_containers_in_rules(rules: List[Any]) -> None:
    for rule in rules:
        _cleanup_empty_containers_in_rule(rule)


def drop_empty_rules(rules: List[Any]) -> None:
    kept: List[Any] = []
    for rule in rules:
        cur = getattr(rule, "curation", None)
        if isinstance(cur, list) and len(cur) == 0:
            continue
        if cur is None:
            continue
        kept.append(rule)
    rules[:] = kept


def minify_rules_and_containers(
    rules: List[Any],
    target_criterion_classnames: Set[str],
) -> None:
    def _visit(_rule: Any, node: Any, _parent: Optional[Any], _depth: int) -> None:
        cls = type(node).__name__

        if cls in target_criterion_classnames:
            return

        d = getattr(node, "__dict__", None)
        if not isinstance(d, dict):
            return

        if cls in ("AndCriterion", "OrCriterion"):
            crit = d.get("criteria")
            d.clear()
            if crit is not None:
                d["criteria"] = crit
            return

        if cls == "NotCriterion":
            child = d.get("criterion")
            d.clear()
            if child is not None:
                d["criterion"] = child
            return

        criteria = d.get("criteria", None)
        criterion = d.get("criterion", None)
        condition = d.get("condition", None)

        d.clear()
        if criteria is not None:
            d["criteria"] = criteria
        if criterion is not None:
            d["criterion"] = criterion
        if condition is not None:
            d["condition"] = condition

    walk_trial(rules, _visit)

    for rule in rules:
        rd = getattr(rule, "__dict__", None)
        if not isinstance(rd, dict):
            continue

        rule_text = rd.get("rule_text")
        curation = rd.get("curation")

        rd.clear()
        if rule_text is not None:
            rd["rule_text"] = rule_text
        if curation is not None:
            rd["curation"] = curation


# ---------------------------
# Core wrapper
# ---------------------------

def simplify_selected_criteria_for_trial(
    rules: List[Any],
    *,
    criteria: List[str],
) -> bool:
    crit_set = set(criteria)
    unknown = crit_set - SUPPORTED_CRITERIA
    if unknown:
        raise ValueError(f"Unknown criteria requested: {sorted(unknown)}")

    target_criterion_classnames = {
        CRITERION_TO_CLASSNAME[c]
        for c in crit_set
    }

    # drop selected criteria inside NOT contexts that also contain
    # PriorTreatmentCriterion or CurrentTreatmentCriterion anywhere in the same
    # nearest enclosing NotCriterion subtree.
    drop_selected_criteria_in_treatment_not_contexts(
        rules,
        selected_criterion_classnames=target_criterion_classnames,
    )

    # standard pruning
    prune_nontarget_criteria_in_rules(rules, target_criterion_classnames)
    cleanup_empty_containers_in_rules(rules)
    drop_empty_rules(rules)
    remove_descriptions_from_rules(rules)
    remove_exclude_and_flipped_from_rules(rules)
    minify_rules_and_containers(rules, target_criterion_classnames)

    return len(rules) > 0


# ---------------------------
# CLI
# ---------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Simplify rules from part II using the pruning logic, with the "
            "exception that selected criteria are dropped under NotCriterion when "
            "any enclosing NotCriterion subtree contains a treatment-related criterion."
        )
    )
    parser.add_argument(
        "--input_dir",
        required=True,
        type=Path,
        help="Directory containing *_filtered.py files from part II, or a single file",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        type=Path,
        help="Directory to write simplified rule files",
    )
    parser.add_argument(
        "--criteria",
        nargs="+",
        required=True,
        choices=sorted(SUPPORTED_CRITERIA),
        help="Selected criteria to retain/simplify",
    )
    parser.add_argument("--log_level", default="INFO", help="Logging level (INFO/DEBUG/...)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    n_files = 0
    n_written = 0
    n_discarded = 0

    for py_path in _iter_input_py_files(args.input_dir):
        n_files += 1

        rules = load_curated_rules(py_path)
        if not rules:
            logger.warning("No rules loaded from %s; skipping", py_path)
            n_discarded += 1
            continue

        kept_any = simplify_selected_criteria_for_trial(
            rules,
            criteria=args.criteria,
        )

        if not kept_any:
            logger.info("No retained selected criteria after simplification for %s; skipping write", py_path)
            n_discarded += 1
            continue

        if py_path.name.endswith("_filtered.py"):
            out_name = py_path.name.replace("_filtered.py", "_simplified.py")
        else:
            out_name = f"{py_path.stem}_simplified.py"

        out_path = args.output_dir / out_name
        write_rules_py(rules, out_path)
        logger.info("Wrote %s", out_path)
        n_written += 1

    logger.info(
        "Done. Seen %d file(s); wrote %d; discarded %d.",
        n_files,
        n_written,
        n_discarded,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())