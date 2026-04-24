from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------
# Shared file IO
# ---------------------------

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


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()


REPLACEMENTS = {
    "na√Øve": "naïve",
    "Na√Øve": "Naïve",
    "Waldenstr√∂m's": "Waldenström's",
    "Waldenstr√∂m ": "Waldenström ",
    "HIF-2Œ±": "HIF-2α",
}

def _fix_mojibake(text: str) -> str:
    if not text:
        return text
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text


def _normalize_for_comparison(value: object) -> str:
    return _fix_mojibake(_normalize_string(value))


MANUAL_COL = "manual_overwrite"


# ---------------------------
# Logic from original
# ---------------------------

PRIMARY_COL = "primary_tumor_oncotree_curation_simplified"
CONDITIONS_COL = "conditions_oncotree_curation_cleaned_simplified"
INCLUSIVE_RULE_COL = "inclusive_rule"
RELATION_COL = "primary_vs_conditions_relation"
FINAL_CANCER_1_COL = "final_cancer_1"

OR_DELIMITER = " | "
AND_DELIMITER = " & "

MISSING_SENTINELS = {"", "[None]"}
MANUAL_INCLUDE_BOTH = "include both"
MANUAL_IGNORE_CONDITIONS = "ignore conditions"
MANUAL_IGNORE_PRIMARY = "ignore primary"
SIBLINGS_SUMMARY_COL = "siblings_summary"
CONDITIONS_W_MOLECULAR_COL = "conditions_w_molecular"
FORCE_IGNORE_PRIMARY_SIBLING_TOKENS: Sequence[str] = (
    "GeneAlteration",
    "Comorbidity",
    "MolecularBiomarker",
    "MolecularSignature",
    "PriorTreatment",
)
FORCE_IGNORE_PRIMARY_RELATION_TOKEN = "is_ancestor_of"


def _should_force_ignore_primary_by_siblings(value: object) -> bool:
    text = _normalize_string(value)
    if not text:
        return False
    return any(token in text for token in FORCE_IGNORE_PRIMARY_SIBLING_TOKENS)


def _should_force_ignore_primary(*, siblings_summary: object, relation_value: object) -> bool:
    return (
        _should_force_ignore_primary_by_siblings(siblings_summary)
        or FORCE_IGNORE_PRIMARY_RELATION_TOKEN in _normalize_string(relation_value)
    )



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


def _parse_optional_bool(value: object) -> bool:
    text = _normalize_string(value).casefold()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"", "false", "0", "no"}:
        return False
    raise ValueError(f"Could not parse boolean value: {value!r}")


def _row_has_molecular_conditions(row: pd.Series) -> bool:
    return _parse_optional_bool(row.get(CONDITIONS_W_MOLECULAR_COL, ""))


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
    inclusive_rule = _parse_bool(row.get(INCLUSIVE_RULE_COL, ""))
    siblings_summary = row.get(SIBLINGS_SUMMARY_COL, "")
    conditions_w_molecular = _row_has_molecular_conditions(row)

    if inclusive_rule and conditions_w_molecular:
        return "Pan-cancer"

    if _should_force_ignore_primary(
        siblings_summary=siblings_summary,
        relation_value=relation_value,
    ):
        return conditions_curation

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
    required_cols = [PRIMARY_COL, CONDITIONS_COL, INCLUSIVE_RULE_COL, RELATION_COL, MANUAL_COL, CONDITIONS_W_MOLECULAR_COL]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Input file missing required column(s): {missing}")

    out = df.copy()
    out[FINAL_CANCER_1_COL] = [
        compute_final_cancer_1_for_row(row, row_index=i)
        for i, (_, row) in enumerate(out.iterrows(), start=2)
    ]
    return out


# ---------------------------
# Logic from original
# ---------------------------

FINAL_CANCER_2_INCLUSIVE_COL = "final_cancer_2_inclusive"
FINAL_CANCER_2_EXCLUSIVE_COL = "final_cancer_2_exclusive"
PAN_CANCER = "Pan-cancer"


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


def _extract_condition_polarity_terms(conditions_curation: object) -> Tuple[Set[str], Set[str]]:
    raw_conditions = _normalize_string(conditions_curation)
    if not raw_conditions:
        return set(), set()

    positive_terms: Set[str] = set()
    negative_terms: Set[str] = set()

    for term in _split_top_level(raw_conditions, OR_DELIMITER):
        term = _normalize_string(term)
        if not term:
            continue

        inner = _unwrap_not(term)
        if inner is not None:
            negative_terms.add(inner)
        else:
            positive_terms.add(term)

    return positive_terms, negative_terms


def split_final_cancer_1(
    expr: object,
    *,
    conditions_curation: object = "",
) -> Tuple[str, str]:
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

    condition_positive_terms, condition_negative_terms = _extract_condition_polarity_terms(
        conditions_curation
    )
    inclusive_norms = set(inclusive_terms)

    resolved_inclusive_terms: List[str] = []
    for term in inclusive_terms:
        if term in condition_negative_terms and f"NOT({term})" in exclusive_terms:
            continue
        resolved_inclusive_terms.append(term)

    resolved_exclusive_terms: List[str] = []
    for term in exclusive_terms:
        inner = _unwrap_not(term) or ""
        if not inner:
            resolved_exclusive_terms.append(term)
            continue

        if inner in condition_positive_terms and inner in inclusive_norms:
            continue

        if inner in condition_negative_terms and inner in inclusive_norms:
            resolved_exclusive_terms.append(term)
            continue

        if inner in inclusive_norms:
            continue

        resolved_exclusive_terms.append(term)

    return OR_DELIMITER.join(resolved_inclusive_terms), OR_DELIMITER.join(resolved_exclusive_terms)


def add_final_cancer_2_columns(df: pd.DataFrame) -> pd.DataFrame:
    required_cols = [FINAL_CANCER_1_COL, CONDITIONS_COL]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Input file missing required column(s): {missing}")

    out = df.copy()
    split_values = [
        split_final_cancer_1(
            row.get(FINAL_CANCER_1_COL, ""),
            conditions_curation=row.get(CONDITIONS_COL, ""),
        )
        for _, row in out.iterrows()
    ]
    conditions_w_molecular_values = [
        _row_has_molecular_conditions(row)
        for _, row in out.iterrows()
    ]
    out[FINAL_CANCER_2_INCLUSIVE_COL] = [
        PAN_CANCER if conditions_w_molecular else inclusive
        for (inclusive, _), conditions_w_molecular in zip(
            split_values,
            conditions_w_molecular_values,
        )
    ]
    out[FINAL_CANCER_2_EXCLUSIVE_COL] = [exclusive for _, exclusive in split_values]
    return out


# ---------------------------
# Logic from original
# ---------------------------

FINAL_CANCER_3_INCLUSIVE_COL = "final_cancer_3_inclusive"
FINAL_CANCER_3_EXCLUSIVE_COL = "final_cancer_3_exclusive"
PAN_CANCER_TERMS = {"pan-cancer", "pan cancer", "pan-cancer ", "pan cancer "}
LEVEL_COLUMN_RE = re.compile(r"^level_(\d+)$")
CODE_RE = re.compile(r"\(([^()]+)\)\s*$")


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


def _split_or_terms_top(raw_value: object) -> List[str]:
    return _split_top_level(_normalize_string(raw_value), OR_DELIMITER)


def _split_and_terms_top(raw_value: object) -> List[str]:
    return _split_top_level(_normalize_string(raw_value), AND_DELIMITER)


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
    terms = _split_or_terms_top(raw_value)
    terms = [term for term in terms if term]
    terms = _dedupe_preserve_order(terms)

    if len(terms) > 1:
        terms = [term for term in terms if not _is_pan_cancer_term(term)]

    terms = _reduce_positive_terms_with_oncotree(terms, hierarchy)
    return _dedupe_preserve_order(terms)


def _extract_negative_terms(raw_value: object) -> List[str]:
    terms = _split_or_terms_top(raw_value)
    negative_terms: List[str] = []
    for term in terms:
        term = _normalize_string(term)
        if not term:
            continue

        and_parts = _split_and_terms_top(term)
        for part in and_parts:
            part = _normalize_string(part)
            inner = _unwrap_not(part)
            if inner is not None:
                negative_terms.append(f"NOT({inner})")
            else:
                continue

    return _dedupe_preserve_order(negative_terms)


def _has_allowed_exclusion_sibling_context(siblings_summary: object) -> bool:
    text = _normalize_string(siblings_summary)
    if not text:
        return True
    return "Histology" in text or "PrimaryTumor" in text


def _filter_negative_terms(
    negative_terms: Sequence[str],
    positive_terms: Sequence[str],
    hierarchy: OncoTreeHierarchy,
    siblings_summary: object = "",
) -> List[str]:
    positive_codes = [_extract_code(term) for term in positive_terms]
    positive_codes = [code for code in positive_codes if code and hierarchy.has_code(code)]

    positive_term_set = set(positive_terms)
    has_pan_cancer_scope = any(_is_pan_cancer_term(term) for term in positive_terms)
    allow_by_sibling_context = _has_allowed_exclusion_sibling_context(siblings_summary)

    kept: List[str] = []

    for negative_term in _dedupe_preserve_order(negative_terms):
        inner_term = _unwrap_not(negative_term)
        if inner_term is None:
            continue

        if inner_term in positive_term_set:
            continue

        if not allow_by_sibling_context:
            continue

        # Pan-cancer is a universe-level scope, not a normal OncoTree node.
        # Therefore a nonblank NOT(inner) is considered inside the retained
        # inclusive scope and does not need a strict-descendant proof.
        if has_pan_cancer_scope:
            kept.append(f"NOT({inner_term})")
            continue

        inner_code = _extract_code(inner_term)
        if not inner_code or not hierarchy.has_code(inner_code):
            continue

        if any(
            hierarchy.is_strict_descendant(
                candidate_code=inner_code,
                reference_code=positive_code,
            )
            for positive_code in positive_codes
        ):
            kept.append(f"NOT({inner_term})")

    return _dedupe_preserve_order(kept)


def simplify_row(
    row: pd.Series,
    hierarchy: OncoTreeHierarchy,
) -> Tuple[str, str]:
    if _row_has_molecular_conditions(row):
        positive_terms = [PAN_CANCER]
    else:
        positive_terms = _clean_positive_terms(row.get(FINAL_CANCER_2_INCLUSIVE_COL, ""), hierarchy)

    negative_terms = _extract_negative_terms(row.get(FINAL_CANCER_2_EXCLUSIVE_COL, ""))
    negative_terms = _filter_negative_terms(
        negative_terms,
        positive_terms,
        hierarchy,
        siblings_summary=row.get(SIBLINGS_SUMMARY_COL, ""),
    )

    return _join_or_terms(positive_terms), _join_and_terms(negative_terms)


def add_final_cancer_3_columns(df: pd.DataFrame, hierarchy: OncoTreeHierarchy) -> pd.DataFrame:
    required_cols = [FINAL_CANCER_2_INCLUSIVE_COL, FINAL_CANCER_2_EXCLUSIVE_COL]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Input file missing required column(s): {missing}")

    out = df.copy()
    simplified = [simplify_row(row, hierarchy) for _, row in out.iterrows()]
    out[FINAL_CANCER_3_INCLUSIVE_COL] = [inclusive for inclusive, _ in simplified]
    out[FINAL_CANCER_3_EXCLUSIVE_COL] = [exclusive for _, exclusive in simplified]
    return out


# ---------------------------
# Existing logic
# ---------------------------

CONDITIONS_BASE_COL = "conditions_oncotree_curation_cleaned_simplified_pancancer"
OUTPUT_COL = "conditions_changed"


def _parse_bool_as_titlecase_string(value: bool) -> str:
    return "True" if value else "False"


def compute_conditions_changed_for_row(row: pd.Series) -> str:
    conditions_value = _normalize_string(row.get(CONDITIONS_BASE_COL, ""))
    final_inclusive_value = _normalize_string(row.get(FINAL_CANCER_3_INCLUSIVE_COL, ""))
    final_exclusive_value = _normalize_string(row.get(FINAL_CANCER_3_EXCLUSIVE_COL, ""))

    unchanged = (
        final_inclusive_value == conditions_value
        and final_exclusive_value == ""
    )
    return _parse_bool_as_titlecase_string(not unchanged)


def add_conditions_changed_column(df: pd.DataFrame) -> pd.DataFrame:
    required_cols = [CONDITIONS_BASE_COL, FINAL_CANCER_3_INCLUSIVE_COL, FINAL_CANCER_3_EXCLUSIVE_COL]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Input file missing required column(s): {missing}")

    out = df.copy()
    out[OUTPUT_COL] = [
        compute_conditions_changed_for_row(row)
        for _, row in out.iterrows()
    ]
    return out


# ---------------------------
# Combined workflow
# ---------------------------

def run_combined_workflow(df: pd.DataFrame, hierarchy: OncoTreeHierarchy) -> pd.DataFrame:
    out = add_final_cancer_1_column(df)
    out = add_final_cancer_2_columns(out)
    out = add_final_cancer_3_columns(out, hierarchy)
    out = add_conditions_changed_column(out)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Vlookup manual_overwrite onto step1 data using the concat key "
            "(nct_id, primary_tumor_type, primary_tumor_location, conditions_original), "
            "then apply the existing iii -> iv -> v logic plus conditions_changed unchanged in one module."
        )
    )
    parser.add_argument(
        "--input_file",
        required=True,
        type=Path,
        help="Input CSV/XLSX containing step1 columns.",
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

    logger.info(
        "Computing %s, %s, %s, %s, %s, and %s",
        FINAL_CANCER_1_COL,
        FINAL_CANCER_2_INCLUSIVE_COL,
        FINAL_CANCER_2_EXCLUSIVE_COL,
        FINAL_CANCER_3_INCLUSIVE_COL,
        FINAL_CANCER_3_EXCLUSIVE_COL,
        OUTPUT_COL,
    )
    out = run_combined_workflow(df, hierarchy)

    logger.info("Writing output file: %s", args.output_file)
    _write_tabular_file(out, args.output_file)

    logger.info("Done. Wrote %d rows to %s", len(out), args.output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
