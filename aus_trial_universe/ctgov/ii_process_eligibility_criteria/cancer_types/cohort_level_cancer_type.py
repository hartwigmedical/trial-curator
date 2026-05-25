from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.ctgov.ii_process_eligibility_criteria.cohort_utils import (
    build_cohort_base_from_curated_rules,
    expand_to_effective_cohort_rows,
    normalize_cohort_label,
    normalize_nct_id,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline import (
    DEFAULT_CURATED_SUBDIR,
    DEFAULT_ELIGIBILITY_DATA_DIR,
    DEFAULT_ONCOTREE_CSV,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_PROCESSED_SUBDIR,
    ROW_LEVEL_STEM,
    _has_nct_py_files,
    _output_path,
    _read_tabular_file,
    _resolve_path,
    _write_tabular_file,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.cancer_types.final_determination.trial_level_determination import (
    INPUT_EXCLUSIVE_COL,
    INPUT_INCLUSIVE_COL,
    NCT_ID_COL,
    OUTPUT_EXCLUSIVE_COL,
    OUTPUT_INCLUSIVE_COL,
    collapse_trial_group,
    load_oncotree_hierarchy,
)

logger = logging.getLogger(__name__)

SUPPORTED_OUTPUT_FORMATS = {"tsv", "csv", "xlsx", "xls"}

COHORT_LEVEL_STEM = "05_cohort_level_cancer_type"

COHORT_LEVEL_CANCER_TYPE_COLUMNS: Sequence[str] = (
    "nct_id",
    "cohort",
    "cancer_type_inclusive",
    "cancer_type_exclusive",
)

REQUIRED_ROW_LEVEL_COLUMNS: Sequence[str] = (
    NCT_ID_COL,
    "cohorts",
    INPUT_INCLUSIVE_COL,
    INPUT_EXCLUSIVE_COL,
)

REQUIRED_COHORT_BASE_COLUMNS: Sequence[str] = (
    "nct_id",
    "cohort",
)


@dataclass(frozen=True)
class CohortLevelCancerTypeInputs:
    curated_dir: Path
    row_level_file: Path
    oncotree_csv: Path
    output_dir: Path
    output_format: str = DEFAULT_OUTPUT_FORMAT
    cohort_base_file: Optional[Path] = None
    fail_on_error: bool = False


@dataclass(frozen=True)
class CohortLevelCancerTypeOutputs:
    cohort_level_cancer_type_file: Path


# =============================================================================
# Validation / small table helpers
# =============================================================================


def _require_columns(
    df: pd.DataFrame,
    required: Sequence[str],
    *,
    label: str,
) -> None:
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {missing}")


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _empty_cohort_level_table() -> pd.DataFrame:
    return pd.DataFrame(columns=list(COHORT_LEVEL_CANCER_TYPE_COLUMNS))


def _group_effective_rows(
    effective_df: pd.DataFrame,
) -> Dict[Tuple[str, str], pd.DataFrame]:
    if effective_df.empty:
        return {}

    grouped: Dict[Tuple[str, str], pd.DataFrame] = {}
    for key, group in effective_df.groupby(
        [NCT_ID_COL, "cohort"],
        sort=False,
        dropna=False,
    ):
        nct_id, cohort = key
        grouped[(normalize_nct_id(nct_id), normalize_cohort_label(cohort))] = group

    return grouped


def _cohort_keys_in_base_order(
    cohort_base_df: pd.DataFrame,
) -> List[Tuple[str, str]]:
    keys: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    for _, row in cohort_base_df.iterrows():
        nct_id = normalize_nct_id(row.get("nct_id", ""))
        cohort = normalize_cohort_label(row.get("cohort", ""))

        if not nct_id or not cohort:
            continue

        key = (nct_id, cohort)
        if key in seen:
            continue

        seen.add(key)
        keys.append(key)

    return keys


def _append_remaining_group_keys(
    ordered_keys: List[Tuple[str, str]],
    grouped: Dict[Tuple[str, str], pd.DataFrame],
) -> List[Tuple[str, str]]:
    seen = set(ordered_keys)
    out = list(ordered_keys)

    for key in grouped.keys():
        if key in seen:
            continue
        seen.add(key)
        out.append(key)

    return out


def _expand_row_level_to_effective_cohorts(
    *,
    row_level_df: pd.DataFrame,
    cohort_base_df: pd.DataFrame,
) -> pd.DataFrame:
    _require_columns(
        row_level_df,
        REQUIRED_ROW_LEVEL_COLUMNS,
        label="Row-level cancer-type table",
    )
    _require_columns(
        cohort_base_df,
        REQUIRED_COHORT_BASE_COLUMNS,
        label="Cohort base table",
    )

    if row_level_df.empty or cohort_base_df.empty:
        output_columns = list(row_level_df.columns)
        if "cohort" not in output_columns:
            output_columns.append("cohort")
        return pd.DataFrame(columns=output_columns)

    return expand_to_effective_cohort_rows(
        row_level_df,
        cohort_base_df,
        nct_col=NCT_ID_COL,
        cohorts_col="cohorts",
        cohort_col="cohort",
    )


# =============================================================================
# Cancer-type cohort-level collapse
# =============================================================================


def collapse_effective_cancer_type_rows_to_cohort_level(
    effective_df: pd.DataFrame,
    *,
    cohort_base_df: pd.DataFrame,
    hierarchy,
) -> pd.DataFrame:
    """
    Collapse already-expanded effective cancer-type rows to nct_id + cohort.

    Cancer-type term rendering is delegated to the existing
    trial_level_determination.collapse_trial_group() function.  This preserves
    the current OncoTree simplification, Pan-cancer fallback, inclusive/exclusive
    rendering, and negative-term filtering behavior.
    """
    if effective_df.empty:
        return _empty_cohort_level_table()

    grouped = _group_effective_rows(effective_df)
    ordered_keys = _cohort_keys_in_base_order(cohort_base_df)
    ordered_keys = _append_remaining_group_keys(ordered_keys, grouped)

    output_rows: List[Dict[str, str]] = []

    for nct_id, cohort in ordered_keys:
        group = grouped.get((nct_id, cohort))
        if group is None or group.empty:
            continue

        inclusive, exclusive = collapse_trial_group(group, hierarchy)
        inclusive = _normalize_string(inclusive)
        exclusive = _normalize_string(exclusive)

        if not inclusive and not exclusive:
            continue

        output_rows.append(
            {
                "nct_id": nct_id,
                "cohort": cohort,
                OUTPUT_INCLUSIVE_COL: inclusive,
                OUTPUT_EXCLUSIVE_COL: exclusive,
            }
        )

    return pd.DataFrame(
        output_rows,
        columns=list(COHORT_LEVEL_CANCER_TYPE_COLUMNS),
    )


def collapse_to_cohort_level_cancer_type(
    *,
    row_level_df: pd.DataFrame,
    cohort_base_df: pd.DataFrame,
    hierarchy,
) -> pd.DataFrame:
    """
    Collapse row-level cancer-type output to nct_id + cohort.

    Cohort inheritance is delegated to cohort_utils.expand_to_effective_cohort_rows():
      - blank row_level_df.cohorts rows are general trial-wide rules
      - general rows apply to '(general)' and every explicit cohort for the trial
      - rows with explicit cohorts apply only to those explicit cohorts
    """
    effective_df = _expand_row_level_to_effective_cohorts(
        row_level_df=row_level_df,
        cohort_base_df=cohort_base_df,
    )
    return collapse_effective_cancer_type_rows_to_cohort_level(
        effective_df,
        cohort_base_df=cohort_base_df,
        hierarchy=hierarchy,
    )


# =============================================================================
# Discovery / validation / pipeline orchestration
# =============================================================================


def discover_cohort_level_cancer_type_inputs(
    *,
    repo_root: Path,
    eligibility_data_dir: Path,
    curated_dir: Optional[Path],
    row_level_file: Optional[Path],
    oncotree_csv: Optional[Path],
    output_dir: Optional[Path],
    output_format: str,
    cohort_base_file: Optional[Path] = None,
    fail_on_error: bool = False,
) -> CohortLevelCancerTypeInputs:
    repo_root = repo_root.resolve()
    resolved_eligibility_data_dir = _resolve_path(eligibility_data_dir, repo_root)

    resolved_curated_dir = (
        _resolve_path(curated_dir, repo_root)
        if curated_dir is not None
        else resolved_eligibility_data_dir / DEFAULT_CURATED_SUBDIR
    )

    resolved_output_dir = (
        _resolve_path(output_dir, repo_root)
        if output_dir is not None
        else resolved_eligibility_data_dir / DEFAULT_PROCESSED_SUBDIR
    )

    resolved_row_level_file = (
        _resolve_path(row_level_file, repo_root)
        if row_level_file is not None
        else _output_path(
            resolved_output_dir,
            ROW_LEVEL_STEM,
            output_format,
        )
    )

    resolved_oncotree_csv = (
        _resolve_path(oncotree_csv, repo_root)
        if oncotree_csv is not None
        else resolved_eligibility_data_dir / DEFAULT_ONCOTREE_CSV
    )

    resolved_cohort_base_file = (
        _resolve_path(cohort_base_file, repo_root)
        if cohort_base_file is not None
        else None
    )

    inputs = CohortLevelCancerTypeInputs(
        curated_dir=resolved_curated_dir,
        row_level_file=resolved_row_level_file,
        oncotree_csv=resolved_oncotree_csv,
        output_dir=resolved_output_dir,
        output_format=output_format,
        cohort_base_file=resolved_cohort_base_file,
        fail_on_error=fail_on_error,
    )

    validate_cohort_level_cancer_type_inputs(inputs)
    return inputs


def validate_cohort_level_cancer_type_inputs(
    inputs: CohortLevelCancerTypeInputs,
) -> None:
    if not inputs.row_level_file.exists():
        raise FileNotFoundError(
            f"Row-level cancer-type file does not exist: {inputs.row_level_file}"
        )
    if not inputs.row_level_file.is_file():
        raise ValueError(
            f"Row-level cancer-type path is not a file: {inputs.row_level_file}"
        )

    if not inputs.oncotree_csv.exists():
        raise FileNotFoundError(f"OncoTree CSV does not exist: {inputs.oncotree_csv}")
    if not inputs.oncotree_csv.is_file():
        raise ValueError(f"OncoTree CSV path is not a file: {inputs.oncotree_csv}")

    if inputs.cohort_base_file is not None:
        if not inputs.cohort_base_file.exists():
            raise FileNotFoundError(
                f"Cohort base file does not exist: {inputs.cohort_base_file}"
            )
        if not inputs.cohort_base_file.is_file():
            raise ValueError(
                f"Cohort base path is not a file: {inputs.cohort_base_file}"
            )
    else:
        if not inputs.curated_dir.exists():
            raise FileNotFoundError(
                f"Curated rules path does not exist: {inputs.curated_dir}"
            )
        if not _has_nct_py_files(inputs.curated_dir):
            raise FileNotFoundError(
                f"Curated rules path does not contain NCT*.py files directly: "
                f"{inputs.curated_dir}"
            )

    normalized_format = inputs.output_format.casefold().lstrip(".")
    if normalized_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"Unsupported output_format: {inputs.output_format!r}. "
            "Expected tsv, csv, xlsx, or xls."
        )


def run_cohort_level_cancer_type_pipeline(
    inputs: CohortLevelCancerTypeInputs,
) -> CohortLevelCancerTypeOutputs:
    inputs.output_dir.mkdir(parents=True, exist_ok=True)

    output_file = _output_path(
        inputs.output_dir,
        COHORT_LEVEL_STEM,
        inputs.output_format,
    )

    logger.info("Cohort-level cancer-type inputs:")
    logger.info("  row_level_file:     %s", inputs.row_level_file)
    logger.info("  oncotree_csv:       %s", inputs.oncotree_csv)
    logger.info("  curated_dir:        %s", inputs.curated_dir)
    logger.info("  cohort_base_file:   %s", inputs.cohort_base_file)
    logger.info("  output_dir:         %s", inputs.output_dir)
    logger.info("  output_format:      %s", inputs.output_format)

    row_level_df = _read_tabular_file(inputs.row_level_file)
    logger.info("Loaded row-level cancer-type table: rows=%d", len(row_level_df))

    if "cohorts" in row_level_df.columns:
        nonblank_cohort_rows = int(
            row_level_df["cohorts"].astype(str).str.strip().ne("").sum()
        )
        logger.info(
            "Row-level cancer-type rows with nonblank cohorts: rows=%d",
            nonblank_cohort_rows,
        )

    if inputs.cohort_base_file is not None:
        cohort_base_df = _read_tabular_file(inputs.cohort_base_file)
        logger.info(
            "Loaded cohort base table: %s rows=%d",
            inputs.cohort_base_file,
            len(cohort_base_df),
        )
    else:
        cohort_base_df = build_cohort_base_from_curated_rules(
            curated_dir=inputs.curated_dir,
            fail_on_error=inputs.fail_on_error,
        )
        logger.info(
            "Built cohort base table from curated rules: rows=%d",
            len(cohort_base_df),
        )

    hierarchy = load_oncotree_hierarchy(inputs.oncotree_csv)
    cohort_level_df = collapse_to_cohort_level_cancer_type(
        row_level_df=row_level_df,
        cohort_base_df=cohort_base_df,
        hierarchy=hierarchy,
    )

    _write_tabular_file(cohort_level_df, output_file)
    logger.info(
        "Wrote cohort-level cancer-type table: %s rows=%d",
        output_file,
        len(cohort_level_df),
    )

    return CohortLevelCancerTypeOutputs(
        cohort_level_cancer_type_file=output_file,
    )


# =============================================================================
# CLI
# =============================================================================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse row-level cancer-type eligibility output to cohort-level "
            "cancer-type eligibility output."
        )
    )

    parser.add_argument(
        "--repo_root",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to current working directory.",
    )
    parser.add_argument(
        "--eligibility_data_dir",
        type=Path,
        default=DEFAULT_ELIGIBILITY_DATA_DIR,
        help="Eligibility data directory. Defaults to data/ctgov/eligibility.",
    )
    parser.add_argument(
        "--curated_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing original curated NCT*.py files, or a single "
            "NCT*.py file. Required only when --cohort_base_file is omitted. "
            "Defaults to eligibility_data_dir/trials/original_curations."
        ),
    )
    parser.add_argument(
        "--row_level_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit 03_row_level_cancer_type file. If omitted, uses "
            "output_dir/03_row_level_cancer_type.<format>."
        ),
    )
    parser.add_argument(
        "--cohort_base_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit cohort base file with columns nct_id and cohort. "
            "If omitted, the cohort base is built from curated Rule.cohorts values."
        ),
    )
    parser.add_argument(
        "--oncotree_csv",
        type=Path,
        default=None,
        help=(
            "Optional explicit OncoTree CSV. If omitted, uses "
            "eligibility_data_dir/resources/oncotree.csv."
        ),
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help=(
            "Output directory for processed cancer-type files. Defaults to "
            "eligibility_data_dir/processed/cancer_type."
        ),
    )
    parser.add_argument(
        "--output_format",
        default=DEFAULT_OUTPUT_FORMAT,
        choices=sorted(SUPPORTED_OUTPUT_FORMATS),
        help="Output format for processed files. Defaults to tsv.",
    )
    parser.add_argument(
        "--fail_on_error",
        action="store_true",
        help="Raise immediately on the first curated rule file that fails while building cohort base.",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        help="Logging level: DEBUG, INFO, WARNING, ERROR.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    inputs = discover_cohort_level_cancer_type_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        curated_dir=args.curated_dir,
        row_level_file=args.row_level_file,
        oncotree_csv=args.oncotree_csv,
        output_dir=args.output_dir,
        output_format=args.output_format,
        cohort_base_file=args.cohort_base_file,
        fail_on_error=args.fail_on_error,
    )

    outputs = run_cohort_level_cancer_type_pipeline(inputs)

    logger.info("Cohort-level cancer-type pipeline complete.")
    logger.info(
        "cohort_level_cancer_type_file: %s",
        outputs.cohort_level_cancer_type_file,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
