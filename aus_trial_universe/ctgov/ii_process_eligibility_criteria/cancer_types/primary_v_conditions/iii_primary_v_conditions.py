
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

PRIMARY_COL = "primary_tumor_oncotree"
CONDITIONS_COL = "conditions_oncotree"
INCLUSIVE_COL = "inclusive_rule"
RELATION_COL = "primary_vs_conditions_relation"
MANUAL_COL = "manual_overwrite"
OUTPUT_COL = "final_cancer_1"

OR_DELIMITER = " | "
AND_DELIMITER = " & "

MISSING_SENTINELS = {"", "[None]"}
MANUAL_INCLUDE_BOTH = "include both"
MANUAL_IGNORE_CONDITIONS = "ignore conditions"
MANUAL_IGNORE_PRIMARY = "ignore primary"


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()


def _split_or_terms(value: object) -> List[str]:
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


def _join_or_terms(terms: Sequence[str]) -> str:
    cleaned = [term for term in (_normalize_string(t) for t in terms) if term]
    deduped = _dedupe_preserve_order(cleaned)
    return OR_DELIMITER.join(deduped)


def _normalize_manual_overwrite(value: object) -> str:
    return _normalize_string(value).casefold()


def _parse_bool(value: object) -> bool:
    text = _normalize_string(value).casefold()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise ValueError(f"Could not parse boolean value: {value!r}")


def _is_missing_curation(value: object) -> bool:
    text = _normalize_string(value)
    return text in MISSING_SENTINELS


def _validate_aligned_lengths(primary_terms: Sequence[str], relation_terms: Sequence[str], row_index: int) -> None:
    if len(primary_terms) != len(relation_terms):
        raise ValueError(
            "Primary terms and relation terms must align 1:1 for non-missing rows. "
            f"Row {row_index}: {len(primary_terms)} primary term(s) vs {len(relation_terms)} relation term(s)."
        )


def _or_union(primary_curation: str, conditions_curation: str) -> str:
    primary_terms = _split_or_terms(primary_curation)
    condition_terms = _split_or_terms(conditions_curation)
    return _join_or_terms([*primary_terms, *condition_terms])


def _wrap_not(expr: str) -> str:
    expr = _normalize_string(expr)
    if not expr:
        return ""
    return f"NOT({expr})"


def _group_if_needed(expr: str) -> str:
    expr = _normalize_string(expr)
    if not expr:
        return ""
    if "|" in expr:
        return f"({expr})"
    return expr


def _compose_and(left_expr: str, right_expr: str) -> str:
    left = _normalize_string(left_expr)
    right = _normalize_string(right_expr)
    if left and right:
        return f"{_group_if_needed(left)}{AND_DELIMITER}{right}"
    return left or right


def _all_top_level_or_terms_are_negative(expr: str) -> bool:
    terms = _split_or_terms(expr)
    if not terms:
        return False

    non_missing_terms = [term for term in terms if not _is_missing_curation(term)]
    if not non_missing_terms:
        return False

    return all(term.startswith("NOT(") and term.endswith(")") for term in non_missing_terms)


def _apply_pan_cancer_fallback_if_only_negative(expr: str) -> str:
    expr = _normalize_string(expr)
    if not expr:
        return expr
    if _all_top_level_or_terms_are_negative(expr):
        return "Pan-cancer"
    return expr


def _compute_with_manual_overwrite(
    *,
    inclusive_rule: bool,
    manual_overwrite: str,
    primary_curation: str,
    conditions_curation: str,
) -> str:
    if manual_overwrite == MANUAL_INCLUDE_BOTH:
        if inclusive_rule:
            return _or_union(primary_curation, conditions_curation)
        return _compose_and(conditions_curation, _wrap_not(primary_curation))

    if manual_overwrite == MANUAL_IGNORE_CONDITIONS:
        if inclusive_rule:
            return _normalize_string(primary_curation)
        return _apply_pan_cancer_fallback_if_only_negative(_wrap_not(primary_curation))

    if manual_overwrite == MANUAL_IGNORE_PRIMARY:
        return _normalize_string(conditions_curation)

    raise ValueError(f"Unsupported manual_overwrite value: {manual_overwrite!r}")


def _compute_without_manual_overwrite(
    *,
    inclusive_rule: bool,
    relation: str,
    primary_term: str,
    conditions_curation: str,
) -> str:
    if inclusive_rule:
        if relation in {"identical", "primary_missing", "is_descendant_of"}:
            return _normalize_string(conditions_curation)
        if relation == "is_ancestor_of":
            return _normalize_string(primary_term)
    else:
        if relation in {"identical", "primary_missing", "is_ancestor_of"}:
            return _normalize_string(conditions_curation)
        if relation == "is_descendant_of":
            return _compose_and(conditions_curation, _wrap_not(primary_term))

    raise ValueError(
        f"Unsupported relation for automatic resolution: {relation!r}. "
        "Expected one of identical, primary_missing, is_descendant_of, is_ancestor_of."
    )


def compute_final_cancer_1_for_row(row: pd.Series, row_index: int) -> str:
    primary_curation = _normalize_string(row.get(PRIMARY_COL, ""))
    conditions_curation = _normalize_string(row.get(CONDITIONS_COL, ""))
    relation_value = _normalize_string(row.get(RELATION_COL, ""))
    manual_overwrite = _normalize_manual_overwrite(row.get(MANUAL_COL, ""))
    inclusive_rule = _parse_bool(row.get(INCLUSIVE_COL, ""))

    if relation_value == "conditions_missing":
        return primary_curation

    if relation_value == "primary_missing":
        return conditions_curation

    if manual_overwrite:
        return _compute_with_manual_overwrite(
            inclusive_rule=inclusive_rule,
            manual_overwrite=manual_overwrite,
            primary_curation=primary_curation,
            conditions_curation=conditions_curation,
        )

    primary_terms = _split_or_terms(primary_curation)
    relation_terms = _split_or_terms(relation_value)
    _validate_aligned_lengths(primary_terms, relation_terms, row_index)

    per_term_outputs = [
        _compute_without_manual_overwrite(
            inclusive_rule=inclusive_rule,
            relation=relation,
            primary_term=primary_term,
            conditions_curation=conditions_curation,
        )
        for primary_term, relation in zip(primary_terms, relation_terms)
    ]
    return _join_or_terms(per_term_outputs)


def add_final_cancer_1_column(df: pd.DataFrame) -> pd.DataFrame:
    required_cols = [PRIMARY_COL, CONDITIONS_COL, INCLUSIVE_COL, RELATION_COL, MANUAL_COL]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Input file missing required column(s): {missing}")

    out = df.copy()
    out[OUTPUT_COL] = [
        compute_final_cancer_1_for_row(row, row_index=i)
        for i, (_, row) in enumerate(out.iterrows(), start=2)
    ]
    return out


def _read_tabular_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False, na_values=[])
    raise ValueError(f"Unsupported input file type: {path.suffix}")


def _write_tabular_file(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df.to_csv(path, index=False)
        return
    if suffix in {".xlsx", ".xls"}:
        df.to_excel(path, index=False)
        return
    raise ValueError(f"Unsupported output file type: {path.suffix}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add final_cancer_1 based on primary/conditions final curation, "
            "comparison relation, inclusive/exclusive rule, and manual overwrite."
        )
    )
    parser.add_argument(
        "--input_file",
        required=True,
        type=Path,
        help="Input CSV/XLSX containing primary/conditions final curation columns.",
    )
    parser.add_argument(
        "--output_file",
        required=True,
        type=Path,
        help="Path to write the augmented CSV/XLSX.",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        help="Logging level (INFO/DEBUG/...).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    logger.info("Loading input file: %s", args.input_file)
    df = _read_tabular_file(args.input_file)
    df.columns = [str(col).strip() for col in df.columns]

    logger.info("Computing %s", OUTPUT_COL)
    out = add_final_cancer_1_column(df)

    logger.info("Writing output file: %s", args.output_file)
    _write_tabular_file(out, args.output_file)

    logger.info("Done. Wrote %d rows to %s", len(out), args.output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
