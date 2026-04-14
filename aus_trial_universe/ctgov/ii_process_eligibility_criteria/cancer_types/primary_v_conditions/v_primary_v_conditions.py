
from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

INPUT_INCLUSIVE_COL = "final_cancer_2_inclusive"
INPUT_EXCLUSIVE_COL = "final_cancer_2_exclusive"
OUTPUT_INCLUSIVE_COL = "final_cancer_3_inclusive"
OUTPUT_EXCLUSIVE_COL = "final_cancer_3_exclusive"

OR_DELIMITER = " | "
AND_DELIMITER = " & "
PAN_CANCER_TERMS = {"pan-cancer", "pan cancer", "pan-cancer ", "pan cancer "}

LEVEL_COLUMN_RE = re.compile(r"^level_(\d+)$")
CODE_RE = re.compile(r"\(([^()]+)\)\s*$")
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


def _extract_code(term: str) -> Optional[str]:
    text = _normalize_string(term)
    if not text:
        return None
    match = CODE_RE.search(text)
    if match:
        code = match.group(1).strip()
        return code or None
    return text


def _is_pan_cancer_term(term: str) -> bool:
    normalized = _normalize_string(term).casefold()
    return normalized in {"pan-cancer", "pan cancer"}


def _split_or_terms(raw_value: object) -> List[str]:
    return _split_top_level(_normalize_string(raw_value), OR_DELIMITER)


def _split_and_terms(raw_value: object) -> List[str]:
    return _split_top_level(_normalize_string(raw_value), AND_DELIMITER)


def _join_or_terms(values: Sequence[str]) -> str:
    cleaned = [v for v in (_normalize_string(x) for x in values) if v]
    return OR_DELIMITER.join(_dedupe_preserve_order(cleaned))


def _join_and_terms(values: Sequence[str]) -> str:
    cleaned = [v for v in (_normalize_string(x) for x in values) if v]
    return AND_DELIMITER.join(_dedupe_preserve_order(cleaned))


class OncoTreeHierarchy:
    def __init__(
        self,
        parents_by_code: Dict[str, Set[str]],
        children_by_code: Dict[str, Set[str]],
    ) -> None:
        self.parents_by_code = parents_by_code
        self.children_by_code = children_by_code

    def has_code(self, code: str) -> bool:
        return code in self.parents_by_code or code in self.children_by_code

    def descendants_of(self, code: str) -> Set[str]:
        visited: Set[str] = set()
        stack = list(self.children_by_code.get(code, set()))

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            stack.extend(self.children_by_code.get(current, set()))

        return visited

    def is_strict_descendant(self, candidate_code: str, reference_code: str) -> bool:
        if candidate_code == reference_code:
            return False
        return candidate_code in self.descendants_of(reference_code)

    def is_strict_ancestor(self, candidate_code: str, reference_code: str) -> bool:
        return self.is_strict_descendant(reference_code, candidate_code)


def load_oncotree_hierarchy(oncotree_csv: Path) -> OncoTreeHierarchy:
    df = pd.read_csv(oncotree_csv, dtype=str, keep_default_na=False, na_values=[])
    df.columns = [str(c).strip() for c in df.columns]

    level_columns: List[Tuple[int, str]] = []
    for column in df.columns:
        match = LEVEL_COLUMN_RE.fullmatch(column)
        if match:
            level_columns.append((int(match.group(1)), column))

    if not level_columns:
        raise ValueError(
            f"No level_* columns found in OncoTree CSV '{oncotree_csv}'. "
            f"Found columns: {list(df.columns)}"
        )

    level_columns.sort()
    ordered_level_cols = [column for _, column in level_columns]

    parents_by_code: Dict[str, Set[str]] = {}
    children_by_code: Dict[str, Set[str]] = {}

    for _, row in df.iterrows():
        branch_codes: List[str] = []

        for column_name in ordered_level_cols:
            cell = _normalize_string(row.get(column_name, ""))
            if not cell:
                continue
            code = _extract_code(cell)
            if not code:
                continue
            branch_codes.append(code)
            parents_by_code.setdefault(code, set())
            children_by_code.setdefault(code, set())

        for i in range(len(branch_codes) - 1):
            parent_code = branch_codes[i]
            child_code = branch_codes[i + 1]
            parents_by_code.setdefault(child_code, set()).add(parent_code)
            children_by_code.setdefault(parent_code, set()).add(child_code)

    return OncoTreeHierarchy(
        parents_by_code=parents_by_code,
        children_by_code=children_by_code,
    )


def _reduce_positive_terms_with_oncotree(
    terms: Sequence[str],
    hierarchy: OncoTreeHierarchy,
) -> List[str]:
    deduped_terms = _dedupe_preserve_order([_normalize_string(t) for t in terms if _normalize_string(t)])
    kept_terms: List[str] = []

    for term in deduped_terms:
        code = _extract_code(term)
        if not code or not hierarchy.has_code(code):
            kept_terms.append(term)
            continue

        should_drop = False
        for other in deduped_terms:
            if other == term:
                continue
            other_code = _extract_code(other)
            if not other_code or not hierarchy.has_code(other_code):
                continue
            if hierarchy.is_strict_descendant(candidate_code=other_code, reference_code=code):
                should_drop = True
                break

        if not should_drop:
            kept_terms.append(term)

    return _dedupe_preserve_order(kept_terms)


def _clean_positive_terms(raw_value: object, hierarchy: OncoTreeHierarchy) -> List[str]:
    terms = _split_or_terms(raw_value)
    terms = [term for term in terms if term]
    terms = _dedupe_preserve_order(terms)

    if len(terms) > 1:
        terms = [term for term in terms if not _is_pan_cancer_term(term)]

    terms = _reduce_positive_terms_with_oncotree(terms, hierarchy)
    return _dedupe_preserve_order(terms)


def _extract_negative_terms(raw_value: object) -> List[str]:
    terms = _split_or_terms(raw_value)
    negative_terms: List[str] = []
    for term in terms:
        term = _normalize_string(term)
        if not term:
            continue

        and_parts = _split_and_terms(term)
        for part in and_parts:
            part = _normalize_string(part)
            inner = _unwrap_not(part)
            if inner is not None:
                negative_terms.append(f"NOT({inner})")
            else:
                # Be strict: final_cancer_2_exclusive should only contain NOT(...) terms.
                # Ignore unexpected positives rather than mixing semantics silently.
                continue

    return _dedupe_preserve_order(negative_terms)


def _filter_negative_terms(
    negative_terms: Sequence[str],
    positive_terms: Sequence[str],
    hierarchy: OncoTreeHierarchy,
) -> List[str]:
    positive_codes = [
        _extract_code(term)
        for term in positive_terms
    ]
    positive_codes = [code for code in positive_codes if code and hierarchy.has_code(code)]

    positive_term_set = set(positive_terms)
    kept: List[str] = []

    for negative_term in _dedupe_preserve_order(negative_terms):
        inner_term = _unwrap_not(negative_term)
        if inner_term is None:
            continue

        if inner_term in positive_term_set:
            continue

        inner_code = _extract_code(inner_term)
        if not inner_code or not hierarchy.has_code(inner_code):
            continue

        if any(
            hierarchy.is_strict_descendant(candidate_code=inner_code, reference_code=positive_code)
            for positive_code in positive_codes
        ):
            kept.append(f"NOT({inner_term})")

    return _dedupe_preserve_order(kept)


def simplify_row(
    row: pd.Series,
    hierarchy: OncoTreeHierarchy,
) -> Tuple[str, str]:
    positive_terms = _clean_positive_terms(row.get(INPUT_INCLUSIVE_COL, ""), hierarchy)
    negative_terms = _extract_negative_terms(row.get(INPUT_EXCLUSIVE_COL, ""))
    negative_terms = _filter_negative_terms(negative_terms, positive_terms, hierarchy)

    return _join_or_terms(positive_terms), _join_and_terms(negative_terms)


def add_final_cancer_3_columns(df: pd.DataFrame, hierarchy: OncoTreeHierarchy) -> pd.DataFrame:
    required_cols = [INPUT_INCLUSIVE_COL, INPUT_EXCLUSIVE_COL]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Input file missing required column(s): {missing}")

    out = df.copy()
    simplified = [simplify_row(row, hierarchy) for _, row in out.iterrows()]
    out[OUTPUT_INCLUSIVE_COL] = [inclusive for inclusive, _ in simplified]
    out[OUTPUT_EXCLUSIVE_COL] = [exclusive for _, exclusive in simplified]
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
            "Further simplify final_cancer_2_inclusive and final_cancer_2_exclusive "
            "into final_cancer_3_inclusive and final_cancer_3_exclusive."
        )
    )
    parser.add_argument(
        "--input_file",
        required=True,
        type=Path,
        help="Input CSV/XLSX containing final_cancer_2_inclusive and final_cancer_2_exclusive.",
    )
    parser.add_argument(
        "--oncotree_csv",
        required=True,
        type=Path,
        help="OncoTree CSV with level_* columns.",
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

    logger.info("Loading OncoTree CSV: %s", args.oncotree_csv)
    hierarchy = load_oncotree_hierarchy(args.oncotree_csv)

    logger.info("Computing %s and %s", OUTPUT_INCLUSIVE_COL, OUTPUT_EXCLUSIVE_COL)
    out = add_final_cancer_3_columns(df, hierarchy)

    logger.info("Writing output file: %s", args.output_file)
    _write_tabular_file(out, args.output_file)

    logger.info("Done. Wrote %d rows to %s", len(out), args.output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
