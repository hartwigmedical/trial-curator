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

CRITERION_NAME = "GeneAlterationCriterion"


# Normalization & OR splitting
def _norm_cell(x: Any) -> str:
    if is_effectively_empty(x):
        return ""
    return norm(fix_mojibake_str(str(x)))


def _strip_outer_parens(s: str) -> str:
    t = s.strip()
    while t.startswith("(") and t.endswith(")"):
        inner = t[1:-1].strip()
        # Heuristic: only strip if parentheses appear to be outer wrappers (not unbalanced)
        if inner.count("(") != inner.count(")"):
            break
        t = inner
    return t


def split_or_terms_strip_parens(value: object) -> List[str]:
    if is_effectively_empty(value):
        return []
    s = _strip_outer_parens(fix_mojibake_str(str(value)).strip())
    parts = [p.strip() for p in s.split("|")]
    return [p for p in parts if p]


# Mapping construction
@dataclass(frozen=True)
class GeneAltCurationRow:
    gene_curation: str
    variant_curation: str
    findings_model: str
    move_to: str


def _key3(gene: str, alteration: str, variant: str) -> Tuple[str, str, str]:
    return (gene, alteration, variant)


def build_gene_alteration_map(mapping_csv: Path) -> Dict[Tuple[str, str, str], GeneAltCurationRow]:
    df = load_resource_csv(mapping_csv)

    required = [
        "Gene_lookup",
        "Alteration_lookup",
        "Variant_lookup",
        "Gene_curation",
        "Variant_curation",
        "FindingsModel_curation",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Gene alteration mapping CSV missing required columns: {missing}")

    move_col = next((c for c in df.columns if str(c).strip().lower() == "move_to"), None)

    out: Dict[Tuple[str, str, str], GeneAltCurationRow] = {}
    for _, row in df.iterrows():
        k = _key3(
            _norm_cell(row.get("Gene_lookup")),
            _norm_cell(row.get("Alteration_lookup")),
            _norm_cell(row.get("Variant_lookup")),
        )

        gene_cur = "" if is_effectively_empty(row.get("Gene_curation")) else fix_mojibake_str(str(row.get("Gene_curation"))).strip()
        var_cur = "" if is_effectively_empty(row.get("Variant_curation")) else fix_mojibake_str(str(row.get("Variant_curation"))).strip()
        fm_cur = "" if is_effectively_empty(row.get("FindingsModel_curation")) else fix_mojibake_str(str(row.get("FindingsModel_curation"))).strip()
        mv_raw = row.get(move_col) if move_col else None
        mv = "" if is_effectively_empty(mv_raw) else fix_mojibake_str(str(mv_raw)).strip()

        val = GeneAltCurationRow(
            gene_curation=gene_cur,
            variant_curation=var_cur,
            findings_model=fm_cur,
            move_to=mv,
        )

        if k in out and out[k] != val:
            logger.warning("Duplicate GeneAlteration key %r with differing values; last-one-wins", k)
        out[k] = val

    return out


# Move_to resource resolution + helpers
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


def _first_lookup_col(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        if str(c).lower().endswith("_lookup"):
            return c
    return None


def _find_matching_row_by_single_lookup(df: pd.DataFrame, lookup_col: str, key_norm: str) -> Optional[pd.Series]:
    for _, row in df.iterrows():
        if _norm_cell(row.get(lookup_col)) == key_norm:
            return row
    return None


# Move_to target registry
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


# Node rewrite (base path)
def _rewrite_gene_alteration_node(
    node: Any,
    gene_curated: List[str],
    variant_curated: List[str],
    findings_model: List[str],
) -> None:
    d = getattr(node, "__dict__", None)
    if not isinstance(d, dict):
        raise TypeError(f"{CRITERION_NAME} node has no __dict__: {node}")
    d.clear()
    d["gene_curated"] = gene_curated
    d["variant_curated"] = variant_curated
    d["findings_model"] = findings_model


def _get_node_fields(node: Any) -> Tuple[str, str, str]:
    g = _norm_cell(getattr(node, "gene", None))
    a = _norm_cell(getattr(node, "alteration", None))
    v = _norm_cell(getattr(node, "variant", None))
    return g, a, v


def _required_lookup_key_from_node(node: Any) -> Optional[Tuple[str, str, str]]:
    g, a, v = _get_node_fields(node)
    if not g and not a and not v:
        return None
    return (g, a, v)


# Core overwrite
def overwrite_gene_alteration_in_rules(
    rules: List[Any],
    *,
    mapping: Dict[Tuple[str, str, str], GeneAltCurationRow],
    resources_dir: Path,
    term_to_level: Dict[str, int],
    resources_cache: Optional[Dict[str, pd.DataFrame]] = None,
) -> None:
    resolver = ResourceResolver(resources_dir=resources_dir, cache=resources_cache)

    def _visit(rule: Any, node: Any, parent: Optional[Any], _depth: int) -> None:
        if type(node).__name__ != CRITERION_NAME:
            return

        key = _required_lookup_key_from_node(node)
        if key is None:
            remove_node_from_parent(rule, parent, node)
            return

        row = mapping.get(key)
        if row is None:
            remove_node_from_parent(rule, parent, node)
            return

        # Move_to takes precedence (replaces criterion type entirely)
        if not is_effectively_empty(row.move_to):
            gene_key_norm = key[0]  # match target on gene only
            if not gene_key_norm:
                replace_or_remove(rule, parent, node, None)
                return

            target_df = resolver.get(row.move_to)
            if target_df is None:
                replace_or_remove(rule, parent, node, None)
                return

            lookup_col = _first_lookup_col(target_df)
            if lookup_col is None:
                replace_or_remove(rule, parent, node, None)
                return

            target_row = _find_matching_row_by_single_lookup(target_df, lookup_col, gene_key_norm)
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

        # Non-move_to path: Require BOTH gene_curation and findings_model non-empty; variant optional.
        if is_effectively_empty(row.gene_curation) or is_effectively_empty(row.findings_model):
            remove_node_from_parent(rule, parent, node)
            return

        gene_curated = split_or_terms_strip_parens(row.gene_curation)
        findings_model = split_or_terms_strip_parens(row.findings_model)

        if not gene_curated or not findings_model:
            remove_node_from_parent(rule, parent, node)
            return

        variant_curated = split_or_terms_strip_parens(row.variant_curation)
        _rewrite_gene_alteration_node(
            node,
            gene_curated=gene_curated,
            variant_curated=variant_curated,
            findings_model=findings_model,
        )

    walk_trial(rules, _visit)


# CLI (optional)
def _iter_input_py_files(curated_dir: Path) -> Iterable[Path]:
    if curated_dir.is_file():
        yield curated_dir
        return
    yield from sorted(p for p in curated_dir.glob("NCT*.py") if p.is_file())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Overwrite GeneAlterationCriterion using mapping + Move_to.")
    parser.add_argument("--curated_dir", required=True, type=Path, help="Directory of curated NCT*.py files (or a single file)")
    parser.add_argument("--output_dir", required=True, type=Path, help="Output directory for *_overwritten.py")
    parser.add_argument("--resources_dir", required=True, type=Path, help="Directory containing all resource CSVs")
    parser.add_argument("--oncotree_csv", required=True, type=Path, help="Path to oncotree.csv (needed for Move_to->PrimaryTumor)")
    parser.add_argument("--mapping_csv", required=True, type=Path, help="GeneAlterationCurationResource_*.csv")
    parser.add_argument("--log_level", default="INFO", help="Logging level (INFO/DEBUG/...)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    mapping = build_gene_alteration_map(args.mapping_csv)
    term_to_level = build_term_to_level_index(args.oncotree_csv)

    cache: Dict[str, pd.DataFrame] = {}

    n_files = 0
    for py_path in _iter_input_py_files(args.curated_dir):
        n_files += 1
        rules = load_curated_rules(py_path)
        if not rules:
            continue

        overwrite_gene_alteration_in_rules(
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
