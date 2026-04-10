from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd

from aus_trial_universe.ctgov.utils.oncotree.traverse_oncotree import OncoTree

logger = logging.getLogger(__name__)


CONDITIONS_CLEANED_COL = "conditions_oncotree_curation_cleaned"
PRIMARY_SIMPLIFIED_COL = "primary_tumor_oncotree_curation_simplified"
CONDITIONS_CLEANED_SIMPLIFIED_COL = "conditions_oncotree_curation_cleaned_simplified"
RELATION_COL = "primary_vs_conditions_relation"

CODE_RE = re.compile(r"\(([^()]+)\)\s*$")
NULL_SENTINELS = {"", "[None]", "NOT([None])"}
DELIMITER = " | "


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _split_terms_preserve_order(value: object) -> List[str]:
    text = _normalize_string(value)
    if not text:
        return []

    parts = [part.strip() for part in text.split("|")]
    return [part for part in parts if part]


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _extract_code(curation: str) -> Optional[str]:
    text = _normalize_string(curation)
    if not text or text in NULL_SENTINELS:
        return None

    match = CODE_RE.search(text)
    if match:
        code = match.group(1).strip()
        return code or None

    return text


def _normalize_term_for_special_cases(term: str) -> str:
    return _normalize_string(term).casefold()


def _is_pan_cancer_term(term: str) -> bool:
    normalized = _normalize_term_for_special_cases(term)
    return normalized == "pan-cancer" or normalized == "pan cancer"


def _is_effectively_missing_conditions_term(term: str) -> bool:
    return _normalize_string(term) == "[None]"


def _clean_conditions_curation(value: object) -> str:
    terms = _split_terms_preserve_order(value)
    deduped_terms = _dedupe_preserve_order(terms)

    if len(deduped_terms) > 1:
        deduped_terms = [term for term in deduped_terms if term != "[None]"]

    return DELIMITER.join(deduped_terms)


def _is_strict_ancestor(tree: OncoTree, ancestor_code: str, descendant_code: str) -> bool:
    if ancestor_code == descendant_code:
        return False
    return ancestor_code in {node.code for node in tree.ancestors(descendant_code)}


def _is_strict_descendant(tree: OncoTree, descendant_code: str, ancestor_code: str) -> bool:
    return _is_strict_ancestor(tree, ancestor_code, descendant_code)


def _simplify_curation_terms(tree: OncoTree, value: object) -> str:
    terms = _dedupe_preserve_order(_split_terms_preserve_order(value))
    if len(terms) <= 1:
        return DELIMITER.join(terms)

    codes = [_extract_code(term) for term in terms]
    keep_mask = [True] * len(terms)

    for i, code_i in enumerate(codes):
        if not code_i:
            continue

        for j, code_j in enumerate(codes):
            if i == j or not code_j:
                continue

            if _is_strict_ancestor(tree, code_j, code_i):
                keep_mask[i] = False
                break

    simplified_terms = [term for term, keep in zip(terms, keep_mask) if keep]
    return DELIMITER.join(simplified_terms)


def _classify_primary_term_against_conditions(
    tree: OncoTree,
    primary_term: str,
    condition_terms: Sequence[str],
) -> str:
    primary_code = _extract_code(primary_term)

    for condition_term in condition_terms:
        condition_code = _extract_code(condition_term)

        if primary_term == condition_term:
            return "identical"

        if primary_code and condition_code:
            if _is_strict_ancestor(tree, primary_code, condition_code):
                return "is_ancestor_of"

            if _is_strict_descendant(tree, primary_code, condition_code):
                return "is_descendant_of"

        if _is_pan_cancer_term(condition_term) and primary_term != condition_term:
            return "is_descendant_of"

    return "CHECK"


def classify_relation(
    tree: OncoTree,
    primary_curation: object,
    cleaned_conditions_curation: object,
) -> str:
    primary_terms = _split_terms_preserve_order(primary_curation)
    condition_terms = _split_terms_preserve_order(cleaned_conditions_curation)

    if not condition_terms or all(_is_effectively_missing_conditions_term(term) for term in condition_terms):
        return "conditions_missing"

    if not primary_terms:
        return "primary_missing"

    condition_terms_non_missing = [
        term for term in condition_terms if not _is_effectively_missing_conditions_term(term)
    ]

    term_relations = [
        _classify_primary_term_against_conditions(tree, primary_term, condition_terms_non_missing)
        for primary_term in primary_terms
    ]
    return DELIMITER.join(term_relations)


def add_comparison_columns(
    df: pd.DataFrame,
    tree: OncoTree,
) -> pd.DataFrame:
    required_cols = [
        "primary_tumor_oncotree_curation",
        "conditions_oncotree_curation",
    ]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Input CSV missing required column(s): {missing}")

    out = df.copy()

    cleaned_conditions = [
        _clean_conditions_curation(value)
        for value in out["conditions_oncotree_curation"].tolist()
    ]
    primary_simplified = [
        _simplify_curation_terms(tree, value)
        for value in out["primary_tumor_oncotree_curation"].tolist()
    ]
    cleaned_conditions_simplified = [
        _simplify_curation_terms(tree, value)
        for value in cleaned_conditions
    ]
    relations = [
        classify_relation(
            tree=tree,
            primary_curation=primary_value,
            cleaned_conditions_curation=cleaned_value,
        )
        for primary_value, cleaned_value in zip(
            primary_simplified,
            cleaned_conditions_simplified,
        )
    ]

    conditions_insert_at = out.columns.get_loc("conditions_oncotree_curation") + 1
    out.insert(conditions_insert_at, CONDITIONS_CLEANED_COL, cleaned_conditions)

    primary_insert_at = out.columns.get_loc("primary_tumor_oncotree_curation") + 1
    out.insert(primary_insert_at, PRIMARY_SIMPLIFIED_COL, primary_simplified)

    cleaned_insert_at = out.columns.get_loc(CONDITIONS_CLEANED_COL) + 1
    out.insert(cleaned_insert_at, CONDITIONS_CLEANED_SIMPLIFIED_COL, cleaned_conditions_simplified)

    out[RELATION_COL] = relations
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clean and simplify primary/conditions OncoTree curation fields, then "
            "compare simplified primary terms against simplified cleaned conditions "
            "terms using OncoTree ancestry. Writes an augmented CSV."
        )
    )
    parser.add_argument(
        "--input_csv",
        required=True,
        type=Path,
        help="Input CSV containing primary_tumor_oncotree_curation and conditions_oncotree_curation",
    )
    parser.add_argument(
        "--oncotree_csv",
        required=True,
        type=Path,
        help="OncoTree CSV used by aus_trial_universe.ctgov.utils.oncotree.traverse_oncotree.OncoTree",
    )
    parser.add_argument(
        "--output_csv",
        required=True,
        type=Path,
        help="Path to write the augmented output CSV",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        help="Logging level (INFO/DEBUG/...)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    logger.info("Loading input CSV: %s", args.input_csv)
    df = pd.read_csv(
        args.input_csv,
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )
    df.columns = [str(col).strip() for col in df.columns]

    logger.info("Loading OncoTree CSV: %s", args.oncotree_csv)
    tree = OncoTree.from_oncotree_csv(args.oncotree_csv)

    logger.info("Adding cleaned/simplified curation columns and comparison column")
    out = add_comparison_columns(df, tree)

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output_csv, index=False)

    logger.info("Done. Wrote %d rows to %s", len(out), args.output_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
