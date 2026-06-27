from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    read_tabular_file as _read_tabular_file,
    write_tabular_file as _write_tabular_file,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_MAPPED_CRITERIA_FILE = Path(
    "data/eligibility_path/exports/intermediates/ctgov/gene_alteration/"
    "03_gene_alteration_mapped_criteria.tsv"
)
DEFAULT_OUTPUT_FILE = Path(
    "data/eligibility_path/exports/intermediates/ctgov/gene_alteration/"
    "diagnostics/01a_gene_alteration_conflicts_trial_level.tsv"
)

TRIAL_ID_COLUMN_CANDIDATES: Sequence[str] = (
    "trial_id",
    "nct_id",
    "trialId",
    "nctId",
    "ACTRN",
)

REQUIRED_VALUE_COLUMNS: Sequence[str] = (
    "polarity",
    "gene_alteration_curation",
)

OUTPUT_COLUMNS: Sequence[str] = (
    "conflict_group_id",
    "nct_id",
    "positive_term",
    "positive_associated_criteria",
    "negative_term",
    "negative_associated_criteria",
)


@dataclass(frozen=True)
class SignedCriterionTerm:
    trial_id: str
    conflict_term: str
    sign: str  # "positive" or "negative"
    source_term: str
    signed_term: str
    associated_criterion: str


@dataclass(frozen=True)
class ConflictGroup:
    conflict_group_id: int
    trial_id: str
    conflict_term: str
    positive_items: List[SignedCriterionTerm]
    negative_items: List[SignedCriterionTerm]


# =============================================================================
# Generic table helpers
# =============================================================================


def _normalize_string(value: object) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


def _ensure_required_columns(df: pd.DataFrame, required: Sequence[str]) -> None:
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"Input table missing required column(s): {missing}")


def _find_trial_id_column(df: pd.DataFrame) -> str:
    normalized_to_original = {
        re.sub(r"[^a-z0-9]+", "", str(column).casefold()): str(column)
        for column in df.columns
    }

    for candidate in TRIAL_ID_COLUMN_CANDIDATES:
        normalized = re.sub(r"[^a-z0-9]+", "", candidate.casefold())
        if normalized in normalized_to_original:
            return normalized_to_original[normalized]

    raise ValueError(
        "Could not infer trial ID column. "
        f"Tried candidates: {list(TRIAL_ID_COLUMN_CANDIDATES)}. "
        f"Available columns: {list(df.columns)}"
    )


def _ensure_output_columns(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    out = df.copy()

    for column in columns:
        if column not in out.columns:
            out[column] = ""

    return out.loc[:, list(columns)]


def _dedupe_preserve_order(values: Iterable[object]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []

    for value in values:
        text = _normalize_string(value)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)

    return out


def _join_unique(values: Iterable[object], *, sep: str = " || ") -> str:
    return sep.join(_dedupe_preserve_order(values))


def _clean_text_for_cell(value: object) -> str:
    text = _normalize_string(value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# =============================================================================
# Expression helpers
# =============================================================================


def _normalize_operator_spacing(value: object) -> str:
    text = _normalize_string(value)
    text = re.sub(r"\s*\|\s*", " | ", text)
    text = re.sub(r"\s*&\s*", " & ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_wrapped_by_outer_pair(text: str, left: str, right: str) -> bool:
    text = text.strip()

    if len(text) < 2 or not (text.startswith(left) and text.endswith(right)):
        return False

    depth = 0

    for i, ch in enumerate(text):
        if ch == left:
            depth += 1
        elif ch == right:
            depth -= 1
            if depth == 0 and i != len(text) - 1:
                return False

        if depth < 0:
            return False

    return depth == 0


def _strip_redundant_outer_parens(text: str) -> str:
    text = text.strip()

    while _is_wrapped_by_outer_pair(text, "(", ")"):
        text = text[1:-1].strip()

    return text


def _is_wrapped_not(text: str) -> bool:
    text = text.strip()

    if not text.startswith("NOT(") or not text.endswith(")"):
        return False

    inner = text[4:-1].strip()
    if not inner:
        return False

    # Ensure the closing parenthesis for NOT(...) is the final character.
    depth = 0

    for i, ch in enumerate(text[3:], start=3):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != len(text) - 1:
                return False

        if depth < 0:
            return False

    return depth == 0


def _unwrap_not(text: str) -> str:
    text = text.strip()

    if _is_wrapped_not(text):
        return text[4:-1].strip()

    return text


def _canonical_term(value: object) -> str:
    text = _normalize_operator_spacing(value)
    text = _strip_redundant_outer_parens(text)
    return text


def _wrap_not_like_pipeline(term: str) -> str:
    """
    Mirror gene_alteration_pipeline behaviour:

      exclusive criterion + A      -> NOT(A)
      exclusive criterion + NOT(A) -> NOT(A)

    i.e. do not create double NOT(NOT(...)).
    """
    term = _canonical_term(term)

    if not term:
        return ""

    if _is_wrapped_not(term):
        return term

    return f"NOT({term})"


def _split_top_level_or(expr: object) -> List[str]:
    """
    Split a mapping expression on top-level | only.

    This preserves nested model expressions such as:

      Fusion[geneStart=A | geneEnd=A]
      NOT(SmallVariant[gene=EGFR & ...])
    """
    text = _normalize_string(expr)
    if not text:
        return []

    terms: List[str] = []
    buf: List[str] = []
    paren = 0
    bracket = 0

    for ch in text:
        if ch == "(":
            paren += 1
            buf.append(ch)
            continue

        if ch == ")":
            paren = max(paren - 1, 0)
            buf.append(ch)
            continue

        if ch == "[":
            bracket += 1
            buf.append(ch)
            continue

        if ch == "]":
            bracket = max(bracket - 1, 0)
            buf.append(ch)
            continue

        if ch == "|" and paren == 0 and bracket == 0:
            part = _canonical_term("".join(buf))
            if part:
                terms.append(part)
            buf = []
            continue

        buf.append(ch)

    tail = _canonical_term("".join(buf))
    if tail:
        terms.append(tail)

    return terms


def _signed_term_from_row_term(term: str, polarity: str) -> str:
    polarity_norm = _normalize_string(polarity).casefold()

    if polarity_norm == "exclusive":
        return _wrap_not_like_pipeline(term)

    return _canonical_term(term)


def _sign_and_conflict_term(signed_term: str) -> Tuple[str, str]:
    """
    Convert a signed final term into:

      A      -> positive, A
      NOT(A) -> negative, A
    """
    signed_term = _canonical_term(signed_term)

    if _is_wrapped_not(signed_term):
        return "negative", _canonical_term(_unwrap_not(signed_term))

    return "positive", signed_term


# =============================================================================
# Human-readable criterion rendering
# =============================================================================


def _associated_criterion_from_row(row: pd.Series) -> str:
    """
    Return only the criterion text that explains why a positive/negative term
    exists.

    Preference order:
      1. input_text
      2. description_input
      3. gene_input / alteration_input / variant_input
      4. gene_alteration_curation
      5. rule_text

    This deliberately avoids source_file, criterion refs, counts, and reasons.
    """
    input_text = _clean_text_for_cell(row.get("input_text", ""))
    if input_text:
        return input_text

    description = _clean_text_for_cell(row.get("description_input", ""))
    if description:
        return description

    structured_parts = [
        _clean_text_for_cell(row.get("gene_input", "")),
        _clean_text_for_cell(row.get("alteration_input", "")),
        _clean_text_for_cell(row.get("variant_input", "")),
    ]
    structured = " / ".join(part for part in structured_parts if part)
    if structured:
        return structured

    curation = _clean_text_for_cell(row.get("gene_alteration_curation", ""))
    if curation:
        return curation

    return _clean_text_for_cell(row.get("rule_text", ""))


# =============================================================================
# Conflict construction
# =============================================================================


def iter_signed_criterion_terms(mapped_df: pd.DataFrame) -> Iterable[SignedCriterionTerm]:
    trial_id_column = _find_trial_id_column(mapped_df)
    _ensure_required_columns(
        mapped_df,
        [trial_id_column, *REQUIRED_VALUE_COLUMNS],
    )

    for _, row in mapped_df.iterrows():
        trial_id = _normalize_string(row.get(trial_id_column, ""))
        if not trial_id:
            continue

        curation = _normalize_string(row.get("gene_alteration_curation", ""))
        if not curation:
            continue

        polarity = _normalize_string(row.get("polarity", "inclusive"))
        associated_criterion = _associated_criterion_from_row(row)

        for source_term in _split_top_level_or(curation):
            signed_term = _signed_term_from_row_term(source_term, polarity)
            if not signed_term:
                continue

            sign, conflict_term = _sign_and_conflict_term(signed_term)
            if not conflict_term:
                continue

            yield SignedCriterionTerm(
                trial_id=trial_id,
                conflict_term=conflict_term,
                sign=sign,
                source_term=source_term,
                signed_term=signed_term,
                associated_criterion=associated_criterion,
            )


def iter_conflict_groups(mapped_df: pd.DataFrame) -> Iterable[ConflictGroup]:
    terms_by_trial_and_base: Dict[
        Tuple[str, str],
        Dict[str, List[SignedCriterionTerm]],
    ] = {}

    for item in iter_signed_criterion_terms(mapped_df):
        key = (item.trial_id, item.conflict_term)
        terms_by_trial_and_base.setdefault(key, {"positive": [], "negative": []})
        terms_by_trial_and_base[key][item.sign].append(item)

    conflict_group_id = 0

    for (trial_id, conflict_term), by_sign in sorted(terms_by_trial_and_base.items()):
        positive_items = by_sign.get("positive", [])
        negative_items = by_sign.get("negative", [])

        if not positive_items or not negative_items:
            continue

        conflict_group_id += 1

        yield ConflictGroup(
            conflict_group_id=conflict_group_id,
            trial_id=trial_id,
            conflict_term=conflict_term,
            positive_items=positive_items,
            negative_items=negative_items,
        )


def build_gene_alteration_conflict_report(mapped_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one human-readable conflict report.

    One output row represents one trial-level apparent contradiction:

      positive_term = A
      negative_term = NOT(A)

    The associated-criteria columns contain the source criteria that produced
    each side of the conflict.
    """
    rows: List[dict[str, object]] = []
    trial_id_column = _find_trial_id_column(mapped_df)
    output_columns = tuple(
        trial_id_column if column == "nct_id" else column
        for column in OUTPUT_COLUMNS
    )

    for group in iter_conflict_groups(mapped_df):
        rows.append(
            {
                "conflict_group_id": group.conflict_group_id,
                trial_id_column: group.trial_id,
                "positive_term": group.conflict_term,
                "positive_associated_criteria": _join_unique(
                    item.associated_criterion for item in group.positive_items
                ),
                "negative_term": f"NOT({group.conflict_term})",
                "negative_associated_criteria": _join_unique(
                    item.associated_criterion for item in group.negative_items
                ),
            }
        )

    out = pd.DataFrame(rows)
    out = _ensure_output_columns(out, output_columns)

    LOGGER.info(
        "Built gene alteration conflict report: conflict_groups=%d rows=%d trials=%d",
        int(out["conflict_group_id"].nunique()) if not out.empty else 0,
        len(out),
        int(out[trial_id_column].nunique()) if not out.empty else 0,
    )

    return out


def build_conflict_report_from_file(mapped_criteria_file: Path) -> pd.DataFrame:
    mapped_df = _read_tabular_file(mapped_criteria_file)
    mapped_df.columns = [str(column).strip() for column in mapped_df.columns]
    return build_gene_alteration_conflict_report(mapped_df)


# =============================================================================
# CLI
# =============================================================================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Track apparent gene-alteration contradictions of the form A and NOT(A). "
            "Outputs one human-readable row per conflict, showing the positive term, "
            "the criteria associated with it, the negative term, and the criteria "
            "associated with it."
        )
    )
    parser.add_argument(
        "--mapped_criteria_file",
        type=Path,
        default=DEFAULT_MAPPED_CRITERIA_FILE,
        help=(
            "Mapped criteria table from the gene-alteration pipeline. "
            "Defaults to data/eligibility_path/exports/intermediates/ctgov/gene_alteration/"
            "03_gene_alteration_mapped_criteria.tsv."
        ),
    )
    parser.add_argument(
        "--output_file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=(
            "Output conflict report path. Defaults to "
            "data/eligibility_path/exports/intermediates/ctgov/gene_alteration/"
            "diagnostics/01a_gene_alteration_conflicts_trial_level.tsv."
        ),
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    report = build_conflict_report_from_file(args.mapped_criteria_file)

    LOGGER.info("Writing conflict report: %s", args.output_file)
    _write_tabular_file(report, args.output_file)
    LOGGER.info("Done. Wrote %d row(s) to %s", len(report), args.output_file)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
