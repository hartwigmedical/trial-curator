from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

from aus_trial_universe.ctgov.iii_b_load_curated_rules import load_curated_rules
from aus_trial_universe.ctgov.utils.i_tree_traversal import normalise_forest_into_list, walk_trial
from aus_trial_universe.ctgov.utils.iv_criterion_pruning import (
    prune_nontarget_criteria_in_rules,
    remove_descriptions_from_rules,
    remove_exclude_and_flipped_from_rules,
)
from aus_trial_universe.ctgov.utils.vi_write_curated_rules import write_rules_py
from aus_trial_universe.ctgov.utils.v_traverse_oncotree import build_term_to_level_index

from aus_trial_universe.ctgov.iv_a_overwrite_primary_tumour import (
    build_primary_tumor_map,
    overwrite_primary_tumour_in_rules,
)
from aus_trial_universe.ctgov.v_a_overwrite_gene_alteration import (
    build_gene_alteration_map,
    overwrite_gene_alteration_in_rules,
    GeneAltCurationRow,
)
from aus_trial_universe.ctgov.v_b_overwrite_molecular_signature import (
    build_molecular_signature_map,
    overwrite_molecular_signature_in_rules,
    MolecularMappingRow,
)

logger = logging.getLogger(__name__)

SUPPORTED_CRITERIA = {"primary_tumour", "molecular_signature", "gene_alteration"}

TARGET_CRITERION_CLASSNAMES = {
    "PrimaryTumorCriterion",
    "MolecularSignatureCriterion",
    "GeneAlterationCriterion",
}

_CONTAINER_CLASSNAMES = {"AndCriterion", "OrCriterion", "NotCriterion"}


def _iter_input_py_files(curated_dir: Path) -> Iterable[Path]:
    if curated_dir.is_file():
        yield curated_dir
        return
    yield from sorted(p for p in curated_dir.glob("NCT*.py") if p.is_file())


# Mapping CSV deduction
def _pick_most_recent(paths: List[Path]) -> Path:
    if not paths:
        raise ValueError("Internal error: _pick_most_recent called with empty list")
    return max(paths, key=lambda p: p.stat().st_mtime)


def _find_mapping_csv(resources_dir: Path, token: str) -> Path:
    matches = [p for p in resources_dir.glob("*.csv") if token.lower() in p.name.lower()]
    if not matches:
        raise FileNotFoundError(f"Could not find mapping CSV containing '{token}' in {resources_dir}")
    return _pick_most_recent(matches)


def deduce_mapping_csvs(resources_dir: Path, criteria: Set[str]) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    if "primary_tumour" in criteria:
        out["primary_tumour"] = _find_mapping_csv(resources_dir, "PrimaryTumourCurationResource")
    if "molecular_signature" in criteria:
        out["molecular_signature"] = _find_mapping_csv(resources_dir, "MolecularSignatureCurationResource")
    if "gene_alteration" in criteria:
        out["gene_alteration"] = _find_mapping_csv(resources_dir, "GeneAlterationCurationResource")
    return out


# Cleanup utilities
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
    for r in rules:
        cur = getattr(r, "curation", None)
        if isinstance(cur, list) and len(cur) == 0:
            continue
        if cur is None:
            continue
        kept.append(r)
    rules[:] = kept


def minify_rules_and_containers(rules: List[Any]) -> None:

    def _visit(rule: Any, node: Any, _parent: Optional[Any], _depth: int) -> None:
        cls = type(node).__name__

        if cls in TARGET_CRITERION_CLASSNAMES:
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

    for r in rules:
        rd = getattr(r, "__dict__", None)
        if not isinstance(rd, dict):
            continue
        rt = rd.get("rule_text")
        cur = rd.get("curation")
        rd.clear()
        rd["rule_text"] = rt
        rd["curation"] = cur


# ---------------------------
# Core wrapper
# ---------------------------

def overwrite_selected_criteria_for_trial(
    rules: List[Any],
    *,
    criteria: List[str],
    resources_dir: Path,
    term_to_level: Dict[str, int],
    pt_map: Optional[Dict[Tuple[str, str], str]] = None,
    ms_map: Optional[Dict[str, MolecularMappingRow]] = None,
    ga_map: Optional[Dict[Tuple[str, str, str], GeneAltCurationRow]] = None,
    shared_resource_cache: Optional[Dict[str, pd.DataFrame]] = None,
) -> bool:
    crit_set = set(criteria)
    unknown = crit_set - SUPPORTED_CRITERIA
    if unknown:
        raise ValueError(f"Unknown criteria requested: {sorted(unknown)}")

    if shared_resource_cache is None:
        shared_resource_cache = {}

    # Overwrites
    if "primary_tumour" in crit_set:
        if pt_map is None:
            raise ValueError("pt_map must be provided when primary_tumour is selected")
        overwrite_primary_tumour_in_rules(rules, mapping=pt_map, term_to_level=term_to_level)

    if "molecular_signature" in crit_set:
        if ms_map is None:
            raise ValueError("ms_map must be provided when molecular_signature is selected")
        overwrite_molecular_signature_in_rules(
            rules,
            mapping=ms_map,
            resources_dir=resources_dir,
            term_to_level=term_to_level,
            resources_cache=shared_resource_cache,
        )

    if "gene_alteration" in crit_set:
        if ga_map is None:
            raise ValueError("ga_map must be provided when gene_alteration is selected")
        overwrite_gene_alteration_in_rules(
            rules,
            mapping=ga_map,
            resources_dir=resources_dir,
            term_to_level=term_to_level,
            resources_cache=shared_resource_cache,
        )

    # Prune to the target criteria only
    prune_nontarget_criteria_in_rules(rules, TARGET_CRITERION_CLASSNAMES)

    cleanup_empty_containers_in_rules(rules)

    drop_empty_rules(rules)

    remove_descriptions_from_rules(rules)
    remove_exclude_and_flipped_from_rules(rules)
    minify_rules_and_containers(rules)

    return len(rules) > 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wrapper: overwrite selected criteria, prune all others, cleanup empty containers."
    )
    parser.add_argument("--curated_dir", required=True, type=Path)
    parser.add_argument("--output_dir", required=True, type=Path)
    parser.add_argument("--resources_dir", required=True, type=Path)
    parser.add_argument("--oncotree_csv", required=True, type=Path)
    parser.add_argument(
        "--criteria",
        nargs="+",
        default=["primary_tumour", "molecular_signature", "gene_alteration"],
    )
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    crit_set = set(args.criteria)
    mapping_csvs = deduce_mapping_csvs(args.resources_dir, crit_set)
    for k, v in mapping_csvs.items():
        logger.info("Using mapping CSV for %s: %s", k, v)

    # Build these ONCE (fix #3A)
    term_to_level = build_term_to_level_index(args.oncotree_csv)

    pt_map = None
    ms_map = None
    ga_map = None

    if "primary_tumour" in crit_set:
        pt_map = build_primary_tumor_map(mapping_csvs["primary_tumour"])
        # Optional wrapper-side guard in case the CSV has blank/blank rows (fix #3B)
        pt_map.pop(("", ""), None)

    if "molecular_signature" in crit_set:
        ms_map = build_molecular_signature_map(mapping_csvs["molecular_signature"])

    if "gene_alteration" in crit_set:
        ga_map = build_gene_alteration_map(mapping_csvs["gene_alteration"])

    shared_resource_cache: Dict[str, pd.DataFrame] = {}

    n_files = 0
    n_overwritten = 0
    n_discarded = 0

    for py_path in _iter_input_py_files(args.curated_dir):
        n_files += 1
        rules = load_curated_rules(py_path)
        if not rules:
            logger.warning("Skipping (no rules loaded): %s", py_path)
            continue

        keep = overwrite_selected_criteria_for_trial(
            rules,
            criteria=list(args.criteria),
            resources_dir=args.resources_dir,
            term_to_level=term_to_level,
            pt_map=pt_map,
            ms_map=ms_map,
            ga_map=ga_map,
            shared_resource_cache=shared_resource_cache,
        )

        suffix = "_overwritten.py" if keep else "_discarded.py"
        out_path = args.output_dir / f"{py_path.stem}{suffix}"
        write_rules_py(rules, out_path)

        if keep:
            n_overwritten += 1
        else:
            n_discarded += 1

        logger.info("Wrote %s", out_path)

    logger.info(
        "Done. Processed %d file(s): %d overwritten, %d discarded.",
        n_files,
        n_overwritten,
        n_discarded,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
