from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from aus_trial_universe.ctgov.iii_b_load_curated_rules import load_curated_rules
from aus_trial_universe.ctgov.utils.i_tree_traversal import walk_trial
from aus_trial_universe.ctgov.utils.ii_normalisation import fix_mojibake_str, is_effectively_empty, norm
from aus_trial_universe.ctgov.utils.iii_csv_mapping_file import load_resource_csv
from aus_trial_universe.ctgov.utils.v_traverse_oncotree import (
    build_term_to_level_index,
    levels_for_terms,
    split_or_terms,
)
from aus_trial_universe.ctgov.utils.vi_write_curated_rules import write_rules_py
from aus_trial_universe.ctgov.utils.vii_tree_pruning import remove_node_from_parent
from aus_trial_universe.ctgov.utils.viii_move_to_logic import create_new_criterion_node, replace_or_remove

logger = logging.getLogger(__name__)

CRITERION_NAME = "MolecularSignatureCriterion"


# Normalization & small utilities
def _norm_cell(x: Any) -> str:
    if is_effectively_empty(x):
        return ""
    return norm(fix_mojibake_str(str(x)))


def _first_lookup_col(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        if str(c).lower().endswith("_lookup"):
            return c
    return None


@dataclass(frozen=True)
class MolecularMappingRow:
    findings: str
    move_to: str


def build_molecular_signature_map(mapping_csv: Path) -> Dict[str, MolecularMappingRow]:
    df = load_resource_csv(mapping_csv)

    required = ["Signature_lookup", "Findings_curation"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Molecular signature mapping CSV missing required columns: {missing}")

    move_col = next((c for c in df.columns if str(c).strip().lower() == "move_to"), None)

    out: Dict[str, MolecularMappingRow] = {}
    for _, row in df.iterrows():
        k = _norm_cell(row.get("Signature_lookup"))
        if not k:
            continue

        f_raw = row.get("Findings_curation")
        findings = "" if is_effectively_empty(f_raw) else fix_mojibake_str(str(f_raw)).strip()

        mv_raw = row.get(move_col) if move_col else None
        move_to = "" if is_effectively_empty(mv_raw) else fix_mojibake_str(str(mv_raw)).strip()

        if k in out and out[k] != MolecularMappingRow(findings=findings, move_to=move_to):
            logger.warning("Duplicate MolecularSignature key '%s' with differing values; last-one-wins", k)

        out[k] = MolecularMappingRow(findings=findings, move_to=move_to)

    return out


# Resource resolution for Move_to
class ResourceResolver:

    def __init__(self, resources_dir: Path, cache: Optional[Dict[str, pd.DataFrame]] = None) -> None:
        self.resources_dir = resources_dir
        self.cache: Dict[str, pd.DataFrame] = cache if cache is not None else {}

    def get(self, move_to_value: str) -> Optional[pd.DataFrame]:
        key = move_to_value
        if key in self.cache:
            return self.cache[key]

        candidates = [move_to_value]
        if not move_to_value.lower().endswith(".csv"):
            candidates.append(move_to_value + ".csv")

        for cand in candidates:
            p = self.resources_dir / cand
            if p.exists() and p.is_file():
                df = load_resource_csv(p)
                self.cache[key] = df
                return df

        return None


def _find_matching_row(df: pd.DataFrame, lookup_col: str, key_norm: str) -> Optional[pd.Series]:
    for _, row in df.iterrows():
        if _norm_cell(row.get(lookup_col)) == key_norm:
            return row
    return None


# Target handlers for Move_to
TargetBuilder = Callable[[Any, pd.DataFrame, pd.Series, Dict[str, int]], Optional[Any]]


def _build_target_primary_tumor(
    old_node: Any,
    target_df: pd.DataFrame,
    target_row: pd.Series,
    term_to_level: Dict[str, int],
) -> Optional[Any]:
    if "Oncotree_curation" not in target_df.columns:
        return None

    raw = target_row.get("Oncotree_curation")
    if is_effectively_empty(raw):
        return None

    terms = split_or_terms(raw)
    if not terms:
        return None

    levels = levels_for_terms(terms, term_to_level)
    if levels is None:
        return None

    new_node = create_new_criterion_node(old_node, "PrimaryTumorCriterion")
    if new_node is None:
        return None

    d = getattr(new_node, "__dict__", None)
    if not isinstance(d, dict):
        return None
    d.clear()
    d["Oncotree_term"] = terms
    d["Oncotree_level"] = levels
    return new_node


def _build_target_molecular_signature(
    old_node: Any,
    target_df: pd.DataFrame,
    target_row: pd.Series,
    _term_to_level: Dict[str, int],
) -> Optional[Any]:
    if "Findings_curation" not in target_df.columns:
        return None

    raw = target_row.get("Findings_curation")
    if is_effectively_empty(raw):
        return None

    findings = fix_mojibake_str(str(raw)).strip()

    new_node = create_new_criterion_node(old_node, "MolecularSignatureCriterion")
    if new_node is None:
        return None

    d = getattr(new_node, "__dict__", None)
    if not isinstance(d, dict):
        return None
    d.clear()
    d["findings_signature"] = findings
    return new_node


def _infer_target_builder(move_to_value: str) -> Optional[TargetBuilder]:
    name = move_to_value.lower()
    if "primarytumour" in name or "primarytumor" in name:
        return _build_target_primary_tumor
    if "molecularsignature" in name:
        return _build_target_molecular_signature
    return None


# Node rewriting (non-Move_to path)
def _rewrite_molecular_signature_node(node: Any, findings_signature: str) -> None:
    d = getattr(node, "__dict__", None)
    if not isinstance(d, dict):
        raise TypeError(f"{CRITERION_NAME} node has no __dict__: {node}")
    d.clear()
    d["findings_signature"] = findings_signature


def _get_signature_key(node: Any) -> Optional[str]:
    if not hasattr(node, "signature"):
        return None
    k = _norm_cell(getattr(node, "signature", None))
    return k or None


# Core overwrite
def overwrite_molecular_signature_in_rules(
    rules: List[Any],
    *,
    mapping: Dict[str, MolecularMappingRow],
    resources_dir: Path,
    term_to_level: Dict[str, int],
    resources_cache: Optional[Dict[str, pd.DataFrame]] = None,
) -> None:
    resolver = ResourceResolver(resources_dir=resources_dir, cache=resources_cache)

    def _visit(rule: Any, node: Any, parent: Optional[Any], _depth: int) -> None:
        if type(node).__name__ != CRITERION_NAME:
            return

        sig_key = _get_signature_key(node)
        if sig_key is None:
            remove_node_from_parent(rule, parent, node)
            return

        row = mapping.get(sig_key)
        if row is None:
            remove_node_from_parent(rule, parent, node)
            return

        # Move_to takes precedence (it replaces criterion type entirely)
        if not is_effectively_empty(row.move_to):
            target_df = resolver.get(row.move_to)
            if target_df is None:
                replace_or_remove(rule, parent, node, None)
                return

            lookup_col = _first_lookup_col(target_df)
            if lookup_col is None:
                replace_or_remove(rule, parent, node, None)
                return

            target_row = _find_matching_row(target_df, lookup_col, sig_key)
            if target_row is None:
                replace_or_remove(rule, parent, node, None)
                return

            builder = _infer_target_builder(row.move_to)
            if builder is None:
                replace_or_remove(rule, parent, node, None)
                return

            new_node = builder(node, target_df, target_row, term_to_level)
            replace_or_remove(rule, parent, node, new_node)
            return

        # Non-move_to path
        if is_effectively_empty(row.findings):
            remove_node_from_parent(rule, parent, node)
            return

        _rewrite_molecular_signature_node(node, findings_signature=row.findings)

    walk_trial(rules, _visit)


# CLI (optional)
def _iter_input_py_files(input_path: Path) -> Iterable[Path]:
    if input_path.is_file():
        yield input_path
        return
    yield from sorted(p for p in input_path.glob("NCT*.py") if p.is_file())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Overwrite MolecularSignatureCriterion using mapping + Move_to."
    )
    parser.add_argument("--curated_dir", required=True, type=Path, help="Curated NCT*.py file or directory")
    parser.add_argument("--output_dir", required=True, type=Path, help="Output directory for *_overwritten.py")
    parser.add_argument("--resources_dir", required=True, type=Path, help="Directory containing all resource CSVs")
    parser.add_argument("--oncotree_csv", required=True, type=Path, help="Path to oncotree.csv")
    parser.add_argument("--mapping_csv", required=True, type=Path, help="MolecularSignatureCurationResource_*.csv")
    parser.add_argument("--log_level", default="INFO", help="Logging level (INFO/DEBUG/...)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    mapping = build_molecular_signature_map(args.mapping_csv)
    term_to_level = build_term_to_level_index(args.oncotree_csv)

    cache: Dict[str, pd.DataFrame] = {}

    n_files = 0
    for py_path in _iter_input_py_files(args.curated_dir):
        n_files += 1
        rules = load_curated_rules(py_path)
        if not rules:
            continue

        overwrite_molecular_signature_in_rules(
            rules,
            mapping=mapping,
            resources_dir=args.resources_dir,
            term_to_level=term_to_level,
            resources_cache=cache,
        )

        out_path = args.output_dir / f"{py_path.stem}_overwritten.py"
        write_rules_py(rules, out_path)
        logger.info("Wrote %s", out_path)

    logger.info("Done. Processed %d file(s).", n_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
