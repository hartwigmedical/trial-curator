from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from aus_trial_universe.ctgov.iii_b_load_curated_rules import load_curated_rules
from aus_trial_universe.ctgov.utils.i_tree_traversal import normalise_forest_into_list, walk_trial
from aus_trial_universe.ctgov.utils.ii_normalisation import (
    fix_mojibake_str,
    is_effectively_empty,
    norm,
)
import pandas as pd

from aus_trial_universe.ctgov.utils.iii_csv_mapping_file import load_resource_csv
from aus_trial_universe.ctgov.utils.iv_criterion_pruning import (
    prune_nontarget_criteria_in_rules,
    remove_descriptions_from_rules,
    remove_exclude_and_flipped_from_rules,
)
from aus_trial_universe.ctgov.utils.vi_write_curated_rules import write_rules_py
from aus_trial_universe.ctgov.utils.vii_tree_pruning import remove_node_from_parent

logger = logging.getLogger(__name__)

SUPPORTED_CRITERIA = {"primary_tumour", "gene_alteration"}

CRITERION_TO_CLASSNAME = {
    "primary_tumour": "PrimaryTumorCriterion",
    "gene_alteration": "GeneAlterationCriterion",
}


# ---------------------------
# Generic helpers
# ---------------------------

def _iter_input_py_files(curated_dir: Path) -> Iterable[Path]:
    if curated_dir.is_file():
        yield curated_dir
        return
    yield from sorted(p for p in curated_dir.glob("NCT*.py") if p.is_file())


def _norm_cell(x: Any) -> str:
    if is_effectively_empty(x):
        return ""
    return norm(fix_mojibake_str(str(x)))


def _clean_cell_str(x: Any) -> str:
    if is_effectively_empty(x):
        return ""
    return fix_mojibake_str(str(x)).strip()


def _pick_most_recent(paths: List[Path]) -> Path:
    if not paths:
        raise ValueError("Internal error: _pick_most_recent called with empty list")
    return max(paths, key=lambda p: p.stat().st_mtime)


def _find_mapping_resource(resources_dir: Path, token: str) -> Path:
    allowed_suffixes = {".csv", ".xlsx", ".xls"}
    matches = [
        p
        for p in resources_dir.iterdir()
        if p.is_file()
        and not p.name.startswith("~$")
        and p.suffix.lower() in allowed_suffixes
        and token.lower() in p.stem.lower()
    ]
    if not matches:
        raise FileNotFoundError(
            f"Could not find mapping resource containing '{token}' in {resources_dir}"
        )
    return _pick_most_recent(matches)


def deduce_mapping_csvs(resources_dir: Path, criteria: Set[str]) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    if "primary_tumour" in criteria:
        out["primary_tumour"] = _find_mapping_resource(resources_dir, "PrimaryTumourCurationResource")
    if "gene_alteration" in criteria:
        out["gene_alteration"] = _find_mapping_resource(resources_dir, "GeneAlterationCurationResource")
    return out


def load_mapping_resource(mapping_path: Path) -> pd.DataFrame:
    suffix = mapping_path.suffix.lower()
    if suffix == ".csv":
        return load_resource_csv(mapping_path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(mapping_path)
    raise ValueError(f"Unsupported mapping resource format: {mapping_path}")


# ---------------------------
# Primary tumour logic
# ---------------------------

def build_primary_tumor_map(mapping_csv: Path) -> Dict[Tuple[str, str], str]:
    df = load_mapping_resource(mapping_csv)

    required = ["PrimaryTumorType_lookup", "PrimaryTumorLocation_lookup", "Oncotree_curation"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Primary tumour mapping CSV missing required columns: {missing}")

    out: Dict[Tuple[str, str], str] = {}

    for _, row in df.iterrows():
        tumor_type = _norm_cell(row.get("PrimaryTumorType_lookup"))
        tumor_location = _norm_cell(row.get("PrimaryTumorLocation_lookup"))

        if tumor_type == "" and tumor_location == "":
            continue

        key = (tumor_type, tumor_location)
        val = _clean_cell_str(row.get("Oncotree_curation"))

        if key in out and out[key] != val:
            logger.warning("Duplicate primary tumour mapping key %r with differing values; last-one-wins", key)

        out[key] = val

    return out


def _get_primary_tumor_key_from_node(node: Any) -> Optional[Tuple[str, str]]:
    raw_type = getattr(node, "primary_tumor_type", None)
    raw_location = getattr(node, "primary_tumor_location", None)

    tumor_type = _norm_cell(raw_type)
    tumor_location = _norm_cell(raw_location)

    if not tumor_type and not tumor_location:
        return None
    if tumor_type and tumor_location:
        return (tumor_type, tumor_location)
    if tumor_type:
        return (tumor_type, "")
    return ("", tumor_location)


def _rewrite_primary_tumor_node(node: Any, oncotree_curation: str) -> None:
    d = getattr(node, "__dict__", None)
    if not isinstance(d, dict):
        raise TypeError(f"PrimaryTumorCriterion node has no __dict__: {node}")

    d.clear()
    d["Oncotree_curation"] = oncotree_curation


def overwrite_primary_tumour_in_rules(
    rules: List[Any],
    *,
    mapping: Dict[Tuple[str, str], str],
) -> None:
    def _visit(rule: Any, node: Any, parent: Optional[Any], _depth: int) -> None:
        if type(node).__name__ != "PrimaryTumorCriterion":
            return

        key = _get_primary_tumor_key_from_node(node)
        if key is None:
            remove_node_from_parent(rule, parent, node)
            return

        mapped = mapping.get(key)
        if mapped is None or is_effectively_empty(mapped):
            remove_node_from_parent(rule, parent, node)
            return

        _rewrite_primary_tumor_node(node, oncotree_curation=mapped)

    walk_trial(rules, _visit)


# ---------------------------
# Gene alteration logic
# ---------------------------

def _gene_alt_key(gene: str, alteration: str, variant: str) -> Tuple[str, str, str]:
    return (gene, alteration, variant)


def build_gene_alteration_map(mapping_csv: Path) -> Dict[Tuple[str, str, str], str]:
    df = load_mapping_resource(mapping_csv)

    required = [
        "Gene_lookup",
        "Alteration_lookup",
        "Variant_lookup",
        "Mapping_args",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Gene alteration mapping CSV missing required columns: {missing}")

    out: Dict[Tuple[str, str, str], str] = {}

    for _, row in df.iterrows():
        key = _gene_alt_key(
            _norm_cell(row.get("Gene_lookup")),
            _norm_cell(row.get("Alteration_lookup")),
            _norm_cell(row.get("Variant_lookup")),
        )

        val = _clean_cell_str(row.get("Mapping_args"))

        if key in out and out[key] != val:
            logger.warning("Duplicate GeneAlteration key %r with differing values; last-one-wins", key)

        out[key] = val

    return out


def _get_gene_alteration_key_from_node(node: Any) -> Optional[Tuple[str, str, str]]:
    gene = _norm_cell(getattr(node, "gene", None))
    alteration = _norm_cell(getattr(node, "alteration", None))
    variant = _norm_cell(getattr(node, "variant", None))

    if not gene and not alteration and not variant:
        return None

    return (gene, alteration, variant)


def _rewrite_gene_alteration_node(node: Any, mapping_args: str) -> None:
    d = getattr(node, "__dict__", None)
    if not isinstance(d, dict):
        raise TypeError(f"GeneAlterationCriterion node has no __dict__: {node}")

    d.clear()
    d["gene_alteration_curation"] = mapping_args


def overwrite_gene_alteration_in_rules(
    rules: List[Any],
    *,
    mapping: Dict[Tuple[str, str, str], str],
) -> None:
    def _visit(rule: Any, node: Any, parent: Optional[Any], _depth: int) -> None:
        if type(node).__name__ != "GeneAlterationCriterion":
            return

        key = _get_gene_alteration_key_from_node(node)
        if key is None:
            remove_node_from_parent(rule, parent, node)
            return

        mapped = mapping.get(key)
        if mapped is None or is_effectively_empty(mapped):
            remove_node_from_parent(rule, parent, node)
            return

        _rewrite_gene_alteration_node(node, mapping_args=mapped)

    walk_trial(rules, _visit)


# ---------------------------
# Cleanup / minify
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


def minify_rules_and_containers(rules: List[Any], target_criterion_classnames: Set[str]) -> None:
    def _visit(rule: Any, node: Any, _parent: Optional[Any], _depth: int) -> None:
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
        rd["rule_text"] = rule_text
        rd["curation"] = curation


# ---------------------------
# Core wrapper
# ---------------------------

def overwrite_selected_criteria_for_trial(
    rules: List[Any],
    *,
    criteria: List[str],
    pt_map: Optional[Dict[Tuple[str, str], str]] = None,
    ga_map: Optional[Dict[Tuple[str, str, str], str]] = None,
) -> bool:
    crit_set = set(criteria)
    unknown = crit_set - SUPPORTED_CRITERIA
    if unknown:
        raise ValueError(f"Unknown criteria requested: {sorted(unknown)}")

    if "primary_tumour" in crit_set:
        if pt_map is None:
            raise ValueError("pt_map must be provided when primary_tumour is selected")
        overwrite_primary_tumour_in_rules(rules, mapping=pt_map)

    if "gene_alteration" in crit_set:
        if ga_map is None:
            raise ValueError("ga_map must be provided when gene_alteration is selected")
        overwrite_gene_alteration_in_rules(rules, mapping=ga_map)

    target_criterion_classnames = {CRITERION_TO_CLASSNAME[c] for c in crit_set}

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
        description="Overwrite selected criteria, prune all others, cleanup empty containers."
    )
    parser.add_argument("--curated_dir", required=True, type=Path, help="Directory of curated NCT*.py files or a single file")
    parser.add_argument("--output_dir", required=True, type=Path, help="Output directory for *_overwritten.py")
    parser.add_argument("--resources_dir", required=True, type=Path, help="Directory containing mapping resources (.csv/.xlsx/.xls)")
    parser.add_argument(
        "--criteria",
        nargs="+",
        required=True,
        choices=sorted(SUPPORTED_CRITERIA),
        help="Criteria to keep and overwrite. Supported: primary_tumour gene_alteration",
    )
    parser.add_argument("--log_level", default="INFO", help="Logging level (INFO/DEBUG/...)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    crit_set = set(args.criteria)
    mapping_csvs = deduce_mapping_csvs(args.resources_dir, crit_set)
    for key, path in mapping_csvs.items():
        logger.info("Using mapping resource for %s: %s", key, path)

    pt_map: Optional[Dict[Tuple[str, str], str]] = None
    ga_map: Optional[Dict[Tuple[str, str, str], str]] = None

    if "primary_tumour" in crit_set:
        pt_map = build_primary_tumor_map(mapping_csvs["primary_tumour"])
        pt_map.pop(("", ""), None)

    if "gene_alteration" in crit_set:
        ga_map = build_gene_alteration_map(mapping_csvs["gene_alteration"])

    args.output_dir.mkdir(parents=True, exist_ok=True)

    n_files = 0
    n_overwritten = 0
    n_discarded = 0

    for py_path in _iter_input_py_files(args.curated_dir):
        n_files += 1

        rules = load_curated_rules(py_path)
        if not rules:
            logger.warning("No rules loaded from %s; skipping", py_path)
            n_discarded += 1
            continue

        kept_any = overwrite_selected_criteria_for_trial(
            rules,
            criteria=args.criteria,
            pt_map=pt_map,
            ga_map=ga_map,
        )

        if not kept_any:
            logger.info("No retained target criteria after overwrite/prune for %s; skipping write", py_path)
            n_discarded += 1
            continue

        out_path = args.output_dir / f"{py_path.stem}_overwritten.py"
        write_rules_py(rules, out_path)
        logger.info("Wrote %s", out_path)
        n_overwritten += 1

    logger.info(
        "Done. Seen %d file(s); wrote %d; discarded %d.",
        n_files,
        n_overwritten,
        n_discarded,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
