from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aus_trial_universe.ctgov.iii_b_load_curated_rules import load_curated_rules
from aus_trial_universe.ctgov.utils.i_tree_traversal import walk_trial
from aus_trial_universe.ctgov.utils.ii_normalisation import (
    fix_mojibake_str,
    is_effectively_empty,
    norm,
)
from aus_trial_universe.ctgov.utils.iii_csv_mapping_file import load_resource_csv
from aus_trial_universe.ctgov.utils.v_traverse_oncotree import (
    build_term_to_level_index,
    levels_for_terms,
    split_or_terms,
)
from aus_trial_universe.ctgov.utils.vi_write_curated_rules import write_rules_py
from aus_trial_universe.ctgov.utils.vii_tree_pruning import remove_node_from_parent

logger = logging.getLogger(__name__)

PRIMARY_TUMOR_CLASS_NAME = "PrimaryTumorCriterion"


# Mapping construction
def _norm_cell(x: Any) -> str:
    if is_effectively_empty(x):
        return ""
    return norm(fix_mojibake_str(str(x)))


def build_primary_tumor_map(mapping_csv: Path) -> Dict[Tuple[str, str], str]:
    df = load_resource_csv(mapping_csv)
    required = ["PrimaryTumorType_lookup", "PrimaryTumorLocation_lookup", "Oncotree_curation"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Mapping CSV missing required columns: {missing}")

    out: Dict[Tuple[str, str], str] = {}
    for _, row in df.iterrows():
        t = _norm_cell(row.get("PrimaryTumorType_lookup"))
        loc = _norm_cell(row.get("PrimaryTumorLocation_lookup"))

        if t == "" and loc == "":
            continue

        key = (t, loc)

        raw = row.get("Oncotree_curation")
        cur_str = "" if is_effectively_empty(raw) else fix_mojibake_str(str(raw)).strip()

        if key in out and out[key] != cur_str:
            logger.warning("Duplicate mapping key %s with differing curation; last-one-wins", key)
        out[key] = cur_str

    return out


# Node rewriting
def rewrite_primary_tumor_node(node: Any, terms: List[str], levels: List[int]) -> None:
    d = getattr(node, "__dict__", None)
    if not isinstance(d, dict):
        raise TypeError(f"PrimaryTumorCriterion node has no __dict__: {node}")

    d.clear()
    d["Oncotree_term"] = terms
    d["Oncotree_level"] = levels


def get_primary_tumor_key_from_node(node: Any) -> Optional[Tuple[str, str]]:
    raw_type = getattr(node, "primary_tumor_type", None)
    raw_loc = getattr(node, "primary_tumor_location", None)

    t = _norm_cell(raw_type)
    loc = _norm_cell(raw_loc)

    if not t and not loc:
        return None
    if t and loc:
        return (t, loc)
    if t:
        return (t, "")
    return ("", loc)


# Core overwrite
def overwrite_primary_tumour_in_rules(
    rules: List[Any],
    *,
    mapping: Dict[Tuple[str, str], str],
    term_to_level: Dict[str, int],
) -> None:

    def _visit(rule: Any, node: Any, parent: Optional[Any], _depth: int) -> None:
        if type(node).__name__ != PRIMARY_TUMOR_CLASS_NAME:
            return

        key = get_primary_tumor_key_from_node(node)
        if key is None:
            remove_node_from_parent(rule, parent, node)
            return

        mapped = mapping.get(key)
        if mapped is None or is_effectively_empty(mapped):
            remove_node_from_parent(rule, parent, node)
            return

        terms = split_or_terms(mapped)  # always list
        if not terms:
            remove_node_from_parent(rule, parent, node)
            return

        levels = levels_for_terms(terms, term_to_level)
        if levels is None:
            remove_node_from_parent(rule, parent, node)
            return

        rewrite_primary_tumor_node(node, terms=terms, levels=levels)

    walk_trial(rules, _visit)


# CLI (optional)
def iter_input_py_files(input_path: Path) -> Iterable[Path]:
    if input_path.is_file():
        yield input_path
        return
    yield from sorted(p for p in input_path.glob("NCT*.py") if p.is_file())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Overwrite PrimaryTumorCriterion using OncoTree mapping.")
    parser.add_argument("--curated_dir", required=True, type=Path, help="Curated NCT*.py file or directory")
    parser.add_argument("--output_dir", required=True, type=Path, help="Output directory for *_overwritten.py")
    parser.add_argument("--oncotree_csv", required=True, type=Path, help="Path to oncotree.csv")
    parser.add_argument("--mapping_csv", required=True, type=Path, help="Path to PrimaryTumourCurationResource_*.csv")
    parser.add_argument("--log_level", default="INFO", help="Logging level (INFO/DEBUG/...)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    mapping = build_primary_tumor_map(args.mapping_csv)
    term_to_level = build_term_to_level_index(args.oncotree_csv)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    n_files = 0
    for py_path in iter_input_py_files(args.curated_dir):
        n_files += 1

        rules = load_curated_rules(py_path)
        if not rules:
            continue

        overwrite_primary_tumour_in_rules(rules, mapping=mapping, term_to_level=term_to_level)

        out_path = args.output_dir / f"{py_path.stem}_overwritten.py"
        write_rules_py(rules, out_path)
        logger.info("Wrote %s", out_path)

    logger.info("Done. Processed %d file(s).", n_files)
    return 0


if __name__ == "__main__":
    main()
