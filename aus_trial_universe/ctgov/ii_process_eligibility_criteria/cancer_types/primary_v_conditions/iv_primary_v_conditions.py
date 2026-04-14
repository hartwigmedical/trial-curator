
from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

INPUT_COL = "final_cancer_1"
INCLUSIVE_COL = "final_cancer_2_inclusive"
EXCLUSIVE_COL = "final_cancer_2_exclusive"

OR_DELIMITER = " | "
PAN_CANCER = "Pan-cancer"
NOT_WRAPPER_RE = re.compile(r"^NOT\((.*)\)$")


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()


def _split_top_level(expr: str, delimiter: str) -> List[str]:
    if not expr:
        return []

    parts: List[str] = []
    buf: List[str] = []
    paren = 0
    bracket = 0
    brace = 0
    i = 0
    n = len(expr)
    delim_len = len(delimiter)

    while i < n:
        ch = expr[i]

        if ch == "(":
            paren += 1
            buf.append(ch)
            i += 1
            continue
        if ch == ")":
            paren = max(paren - 1, 0)
            buf.append(ch)
            i += 1
            continue
        if ch == "[":
            bracket += 1
            buf.append(ch)
            i += 1
            continue
        if ch == "]":
            bracket = max(bracket - 1, 0)
            buf.append(ch)
            i += 1
            continue
        if ch == "{":
            brace += 1
            buf.append(ch)
            i += 1
            continue
        if ch == "}":
            brace = max(brace - 1, 0)
            buf.append(ch)
            i += 1
            continue

        if paren == 0 and bracket == 0 and brace == 0 and expr[i:i + delim_len] == delimiter:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            i += delim_len
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)

    return parts


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _is_balanced_wrapped_not(term: str) -> bool:
    term = term.strip()
    if not term.startswith("NOT(") or not term.endswith(")"):
        return False
    inner = term[4:-1]
    if not inner:
        return False

    paren = 0
    bracket = 0
    brace = 0

    for i, ch in enumerate(term):
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
            if paren < 0:
                return False
            if paren == 0 and i != len(term) - 1:
                return False
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            bracket -= 1
            if bracket < 0:
                return False
        elif ch == "{":
            brace += 1
        elif ch == "}":
            brace -= 1
            if brace < 0:
                return False

    return paren == 0 and bracket == 0 and brace == 0


def _unwrap_not(term: str) -> Optional[str]:
    term = term.strip()
    if not _is_balanced_wrapped_not(term):
        return None
    return term[4:-1].strip()


def _strip_balanced_outer_parens(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        inner = expr[1:-1].strip()
        if not inner:
            return expr

        paren = 0
        bracket = 0
        brace = 0
        valid = True

        for i, ch in enumerate(expr):
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren -= 1
                if paren < 0:
                    valid = False
                    break
                if paren == 0 and i != len(expr) - 1:
                    valid = False
                    break
            elif ch == "[":
                bracket += 1
            elif ch == "]":
                bracket -= 1
                if bracket < 0:
                    valid = False
                    break
            elif ch == "{":
                brace += 1
            elif ch == "}":
                brace -= 1
                if brace < 0:
                    valid = False
                    break

        if not valid or paren != 0 or bracket != 0 or brace != 0:
            return expr
        expr = inner

    return expr


def _collect_from_component(component: str, inclusive_terms: List[str], exclusive_terms: List[str]) -> None:
    component = _normalize_string(component)
    if not component:
        return

    inner_not = _unwrap_not(component)
    if inner_not is not None:
        exclusive_terms.append(f"NOT({inner_not})")
        return

    stripped = _strip_balanced_outer_parens(component)
    nested_or_terms = _split_top_level(stripped, OR_DELIMITER)
    if len(nested_or_terms) > 1:
        for nested_term in nested_or_terms:
            _collect_from_component(nested_term, inclusive_terms, exclusive_terms)
        return

    inclusive_terms.append(stripped)


def split_final_cancer_1(expr: object) -> Tuple[str, str]:
    raw_expr = _normalize_string(expr)
    if not raw_expr:
        return PAN_CANCER, ""

    inclusive_terms: List[str] = []
    exclusive_terms: List[str] = []

    or_clauses = _split_top_level(raw_expr, OR_DELIMITER)
    if not or_clauses:
        return PAN_CANCER, ""

    for clause in or_clauses:
        and_components = _split_top_level(clause, " & ")
        if not and_components:
            and_components = [clause]

        for component in and_components:
            _collect_from_component(component, inclusive_terms, exclusive_terms)

    inclusive_terms = _dedupe_preserve_order([term for term in inclusive_terms if term])
    exclusive_terms = _dedupe_preserve_order([term for term in exclusive_terms if term])

    inclusive_norms = {term for term in inclusive_terms}
    exclusive_terms = [
        term
        for term in exclusive_terms
        if (_unwrap_not(term) or "") not in inclusive_norms
    ]

    return OR_DELIMITER.join(inclusive_terms), OR_DELIMITER.join(exclusive_terms)


def add_final_cancer_2_columns(df: pd.DataFrame) -> pd.DataFrame:
    if INPUT_COL not in df.columns:
        raise ValueError(f"Input file missing required column: {INPUT_COL}")

    out = df.copy()
    split_values = [split_final_cancer_1(value) for value in out[INPUT_COL].tolist()]
    out[INCLUSIVE_COL] = [inclusive for inclusive, _ in split_values]
    out[EXCLUSIVE_COL] = [exclusive for _, exclusive in split_values]
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
            "Split final_cancer_1 into final_cancer_2_inclusive and "
            "final_cancer_2_exclusive, then remove exact contradictions."
        )
    )
    parser.add_argument(
        "--input_file",
        required=True,
        type=Path,
        help="Input CSV/XLSX containing final_cancer_1.",
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

    logger.info("Computing %s and %s", INCLUSIVE_COL, EXCLUSIVE_COL)
    out = add_final_cancer_2_columns(df)

    logger.info("Writing output file: %s", args.output_file)
    _write_tabular_file(out, args.output_file)

    logger.info("Done. Wrote %d rows to %s", len(out), args.output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
