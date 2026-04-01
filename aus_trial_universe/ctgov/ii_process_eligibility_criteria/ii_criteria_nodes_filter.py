from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Iterable, List, Optional, Set

from aus_trial_universe.ctgov.i_download_trials_and_extract_eligibility.utils.load_curated_rules import load_curated_rules
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.criteria_registry import (
    SUPPORTED_CRITERIA,
    CRITERION_TO_CLASSNAME,
    CURATED_FIELD_BY_CLASSNAME,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.general_utils.traverse_curation_tree import walk_trial
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.general_utils.write_curated_rules import write_rules_py

logger = logging.getLogger(__name__)


# ---------------------------
# Helpers
# ---------------------------

def _iter_input_py_files(curated_dir: Path) -> Iterable[Path]:
    if curated_dir.is_file():
        yield curated_dir
        return
    yield from sorted(p for p in curated_dir.glob("NCT*_overwritten.py") if p.is_file())


def _node_has_curated_value(
    node: Any,
    *,
    selected_criterion_classnames: Set[str],
) -> bool:
    """
    Return True if this node is one of the selected criterion classes and
    contains a mapped curated value.
    """
    cls = type(node).__name__
    if cls not in selected_criterion_classnames:
        return False

    curated_field = CURATED_FIELD_BY_CLASSNAME.get(cls)
    if not curated_field:
        return False

    return bool(getattr(node, curated_field, None))


def _rule_has_any_curated_value(
    rule: Any,
    *,
    selected_criterion_classnames: Set[str],
) -> bool:
    """
    Traverse the rule and return True if any selected criterion node has a curated value.
    """
    found = False

    def _visit(_rule: Any, node: Any, _parent: Optional[Any], _depth: int) -> None:
        nonlocal found
        if found:
            return
        if _node_has_curated_value(
            node,
            selected_criterion_classnames=selected_criterion_classnames,
        ):
            found = True

    walk_trial([rule], _visit)
    return found


def filter_rules_with_no_curated_values(
    rules: List[Any],
    *,
    criteria: List[str],
) -> List[Any]:
    """
    Keep only rules that contain at least one mapped curated value
    for the selected criteria.
    """
    crit_set = set(criteria)
    unknown = crit_set - SUPPORTED_CRITERIA
    if unknown:
        raise ValueError(f"Unknown criteria requested: {sorted(unknown)}")

    selected_criterion_classnames = {
        CRITERION_TO_CLASSNAME[c]
        for c in crit_set
    }

    kept: List[Any] = []

    for rule in rules:
        if _rule_has_any_curated_value(
            rule,
            selected_criterion_classnames=selected_criterion_classnames,
        ):
            kept.append(rule)
        else:
            logger.debug(
                "Dropping rule with no curated values for selected criteria: %s",
                getattr(rule, "rule_text", "<no text>"),
            )

    return kept


# ---------------------------
# CLI
# ---------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Filter rules: keep only those that contain at least one mapped curated value "
            "for the selected criteria."
        )
    )
    parser.add_argument(
        "--input_dir",
        required=True,
        type=Path,
        help="Directory containing *_overwritten.py files from part I (or a single file)",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        type=Path,
        help="Directory to write filtered rule files",
    )
    parser.add_argument(
        "--criteria",
        nargs="+",
        required=True,
        choices=sorted(SUPPORTED_CRITERIA),
        help="Selected criteria whose curated values should be considered for filtering",
    )
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    n_files = 0
    n_written = 0
    n_rules_in = 0
    n_rules_out = 0

    for py_path in _iter_input_py_files(args.input_dir):
        n_files += 1

        rules = load_curated_rules(py_path)
        if not rules:
            logger.warning("No rules loaded from %s; skipping", py_path)
            continue

        n_rules_in += len(rules)

        filtered_rules = filter_rules_with_no_curated_values(
            rules,
            criteria=args.criteria,
        )
        n_rules_out += len(filtered_rules)

        if not filtered_rules:
            logger.info("All rules dropped for %s; skipping write", py_path)
            continue

        out_path = args.output_dir / py_path.name.replace("_overwritten", "_filtered")
        write_rules_py(filtered_rules, out_path)

        logger.info(
            "Wrote %s (%d → %d rules)",
            out_path,
            len(rules),
            len(filtered_rules),
        )
        n_written += 1

    logger.info(
        "Done. Files seen: %d | Files written: %d | Rules: %d → %d",
        n_files,
        n_written,
        n_rules_in,
        n_rules_out,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
