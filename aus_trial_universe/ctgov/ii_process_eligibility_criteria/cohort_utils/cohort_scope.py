from __future__ import annotations

import logging
from collections.abc import Iterable as IterableABC
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import pandas as pd

from aus_trial_universe.ctgov.i_download_trials_and_extract_eligibility.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.ctgov.utils.general.text_normalisation import (
    clean_cell_str,
    is_effectively_empty,
)

logger = logging.getLogger(__name__)

GENERAL_COHORT = "(general)"
COHORT_DELIMITER = " | "
COHORT_BASE_COLUMNS: Sequence[str] = ("nct_id", "cohort")


def _is_blank(value: object) -> bool:
    if value is None:
        return True

    try:
        is_na = pd.isna(value)
        if bool(is_na):
            return True
    except (TypeError, ValueError):
        pass

    try:
        if is_effectively_empty(value):
            return True
    except (TypeError, ValueError):
        pass

    return str(value).strip() == ""


def _display_cell(value: object) -> str:
    if _is_blank(value):
        return ""
    return clean_cell_str(value)


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []

    for value in values:
        cleaned = _display_cell(value)
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)

    return out


def normalize_nct_id(value: object) -> str:
    """Return a stable uppercase NCT identifier, or blank if unavailable."""
    return _display_cell(value).upper()


def normalize_cohort_label(value: object) -> str:
    """Return the display label used in cohort-level outputs."""
    return _display_cell(value)


def _coerce_raw_cohort_values(value: object) -> List[object]:
    if _is_blank(value):
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, set):
        return sorted(value, key=repr)

    if isinstance(value, dict):
        # Rule.cohorts is expected to be list-like, not dict-like.  Treat an
        # unexpected dict as a single malformed value rather than silently
        # inventing cohort labels from keys or values.
        return [value]

    if isinstance(value, IterableABC):
        return list(value)

    return [value]


def coerce_cohorts(value: object) -> List[str]:
    """
    Normalize a raw cohort container into a deduplicated list of cohort labels.

    This is intended for raw values such as Rule.cohorts.  A string is treated
    as one cohort label, not split on COHORT_DELIMITER.  Use parse_cohorts() for
    values that were previously serialized into a tabular file.
    """
    return _dedupe_preserve_order(
        normalize_cohort_label(raw_value)
        for raw_value in _coerce_raw_cohort_values(value)
    )


def rule_cohorts(rule: Any) -> List[str]:
    """Return the curated cohort labels attached to a Rule, if any."""
    return coerce_cohorts(getattr(rule, "cohorts", None))


def serialize_cohorts(cohorts: object) -> str:
    """Serialize raw cohort labels for row-level / mapped-criteria tables."""
    return COHORT_DELIMITER.join(coerce_cohorts(cohorts))


def serialize_rule_cohorts(rule: Any) -> str:
    """
    Serialize Rule.cohorts for mapped-criteria outputs.

    Blank string means the source Rule has no cohort designation and therefore
    represents a general trial-wide rule.
    """
    return COHORT_DELIMITER.join(rule_cohorts(rule))


def parse_cohorts(value: object) -> List[str]:
    """
    Parse the serialized cohorts column from row-level / mapped-criteria tables.

    Blank string means general trial-wide scope.  Nonblank strings are split on
    COHORT_DELIMITER, preserving label order and removing duplicates.
    """
    if _is_blank(value):
        return []

    if isinstance(value, str):
        return _dedupe_preserve_order(
            part.strip()
            for part in value.split(COHORT_DELIMITER)
        )

    return coerce_cohorts(value)


def _iter_input_py_files(curated_dir: Path) -> Iterable[Path]:
    if curated_dir.is_file():
        yield curated_dir
        return

    yield from sorted(path for path in curated_dir.glob("NCT*.py") if path.is_file())


def _validate_rule_sequence(rules: object, py_path: Path) -> Sequence[Any] | None:
    if rules is None:
        logger.warning("Skipping %s because load_curated_rules returned None", py_path)
        return None

    if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
        logger.warning("Skipping %s because loaded rules are not a sequence", py_path)
        return None

    return rules


def build_cohort_base_from_curated_rules(
    *,
    curated_dir: Path,
    fail_on_error: bool = False,
) -> pd.DataFrame:
    """
    Build the simple cohort base table from curated NCT*.py Rule.cohorts values.

    Output columns:
      - nct_id
      - cohort

    Every successfully loaded trial receives a '(general)' row.  Explicit cohort
    rows are added only when at least one Rule has a non-empty cohorts=[...]
    designation.  Textual cohort mentions inside rule_text are intentionally not
    parsed here.
    """
    curated_dir = Path(curated_dir)

    if not curated_dir.exists():
        raise FileNotFoundError(f"Curated rules path does not exist: {curated_dir}")

    rows: List[Dict[str, str]] = []
    skipped_files = 0

    for py_path in _iter_input_py_files(curated_dir):
        try:
            rules = load_curated_rules(py_path)
            rules = _validate_rule_sequence(rules, py_path)
            if rules is None:
                skipped_files += 1
                continue
        except Exception as exc:
            if fail_on_error:
                raise
            skipped_files += 1
            logger.exception("Skipping %s due to load error: %s", py_path, exc)
            continue

        nct_id = normalize_nct_id(py_path.stem)
        if not nct_id:
            logger.warning("Skipping %s because no NCT id could be derived", py_path)
            continue

        explicit_cohorts: List[str] = []
        for rule in rules:
            explicit_cohorts.extend(rule_cohorts(rule))

        rows.append({"nct_id": nct_id, "cohort": GENERAL_COHORT})

        for cohort in _dedupe_preserve_order(explicit_cohorts):
            rows.append({"nct_id": nct_id, "cohort": cohort})

    if skipped_files:
        logger.warning("Skipped %d curated file(s) while building cohort base", skipped_files)

    return pd.DataFrame(rows, columns=list(COHORT_BASE_COLUMNS))


def _require_columns(df: pd.DataFrame, required: Sequence[str], *, label: str) -> None:
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {missing}")


def _cohort_lookup(
    cohort_base_df: pd.DataFrame,
    *,
    nct_col: str,
    cohort_col: str,
) -> Dict[str, List[str]]:
    _require_columns(
        cohort_base_df,
        [nct_col, cohort_col],
        label="Cohort base table",
    )

    lookup: Dict[str, List[str]] = {}

    for _, row in cohort_base_df.iterrows():
        nct_id = normalize_nct_id(row.get(nct_col, ""))
        cohort = normalize_cohort_label(row.get(cohort_col, ""))

        if not nct_id or not cohort:
            continue

        lookup.setdefault(nct_id, [])
        if cohort not in lookup[nct_id]:
            lookup[nct_id].append(cohort)

    return lookup


def effective_cohorts_for_rule(
    *,
    row_cohorts: object,
    trial_cohorts: Sequence[str],
    include_unmatched_explicit_cohorts: bool = True,
) -> List[str]:
    """
    Return output cohorts to which one mapped criterion row applies.

    Rules with blank cohorts are general trial-wide rules and apply to every row
    in the trial's cohort base, including '(general)'.

    Rules with explicit cohorts apply only to those explicit cohorts, not to the
    '(general)' row.
    """
    explicit = parse_cohorts(row_cohorts)
    base = _dedupe_preserve_order(trial_cohorts)

    if not explicit:
        return base or [GENERAL_COHORT]

    base_set = set(base)
    matched = [cohort for cohort in base if cohort in explicit]

    if include_unmatched_explicit_cohorts:
        matched.extend(cohort for cohort in explicit if cohort not in base_set)

    return _dedupe_preserve_order(matched)


def expand_to_effective_cohort_rows(
    mapped_df: pd.DataFrame,
    cohort_base_df: pd.DataFrame,
    *,
    nct_col: str = "nct_id",
    cohorts_col: str = "cohorts",
    cohort_col: str = "cohort",
    include_unmatched_explicit_cohorts: bool = True,
) -> pd.DataFrame:
    """
    Expand row-level / mapped-criteria rows to effective cohort-level rows.

    Input convention:
      - blank cohorts_col: the source Rule has no cohorts=[...] designation
      - nonblank cohorts_col: serialized explicit cohort labels for the Rule

    Expansion convention:
      - '(general)' output cohort receives only general rows
      - explicit output cohorts receive general rows plus their own explicit rows

    The returned DataFrame preserves all original columns and adds/overwrites
    cohort_col with the effective output cohort label.
    """
    _require_columns(mapped_df, [nct_col, cohorts_col], label="Mapped criteria table")
    lookup = _cohort_lookup(cohort_base_df, nct_col=nct_col, cohort_col=cohort_col)

    output_columns = list(mapped_df.columns)
    if cohort_col not in output_columns:
        try:
            insert_at = output_columns.index(cohorts_col) + 1
        except ValueError:
            insert_at = len(output_columns)
        output_columns.insert(insert_at, cohort_col)

    if mapped_df.empty:
        return pd.DataFrame(columns=output_columns)

    rows: List[Dict[str, object]] = []

    for _, row in mapped_df.iterrows():
        nct_id = normalize_nct_id(row.get(nct_col, ""))
        if not nct_id:
            continue

        trial_cohorts = lookup.get(nct_id, [GENERAL_COHORT])
        target_cohorts = effective_cohorts_for_rule(
            row_cohorts=row.get(cohorts_col, ""),
            trial_cohorts=trial_cohorts,
            include_unmatched_explicit_cohorts=include_unmatched_explicit_cohorts,
        )

        for cohort in target_cohorts:
            out = row.to_dict()
            out[nct_col] = nct_id
            out[cohort_col] = cohort
            rows.append(out)

    return pd.DataFrame(rows, columns=output_columns)
