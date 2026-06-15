from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.eligibility_utils.cohort_utils import (
    build_cohort_base_from_curated_rules,
    expand_to_effective_cohort_rows,
    normalize_cohort_label,
    normalize_nct_id,
)
from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline import (
    DEFAULT_CURATED_SUBDIR,
    DEFAULT_ELIGIBILITY_DATA_DIR,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_PROCESSED_SUBDIR,
    MAPPED_CRITERIA_STEM,
    SUPPORTED_OUTPUT_FORMATS,
    _contains_nct_py_files,
    _output_path,
    _read_tabular_file,
    _resolve_path,
    _write_tabular_file,
    collapse_to_trial_level,
)
from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.gene_alterations.qa.gene_alteration_conflicts import (
    build_gene_alteration_conflict_report,
)

logger = logging.getLogger(__name__)

COHORT_LEVEL_STEM = "05_cohort_level_gene_alteration"
COHORT_LEVEL_CONFLICTS_STEM = "99b_gene_alteration_conflicts_cohort_level"

COHORT_LEVEL_GENE_ALTERATION_COLUMNS: Sequence[str] = (
    "nct_id",
    "cohort",
    "gene_alteration_inclusive",
    "gene_alteration_exclusive",
)

REQUIRED_MAPPED_CRITERIA_COLUMNS: Sequence[str] = (
    "nct_id",
    "cohorts",
    "polarity",
    "gene_alteration_curation",
)

REQUIRED_COHORT_BASE_COLUMNS: Sequence[str] = (
    "nct_id",
    "cohort",
)

DEFAULT_COHORT_CONFLICT_COLUMNS: Sequence[str] = (
    "conflict_group_id",
    "nct_id",
    "cohort",
    "positive_term",
    "positive_associated_criteria",
    "negative_term",
    "negative_associated_criteria",
)


@dataclass(frozen=True)
class CohortLevelGeneAlterationInputs:
    curated_dir: Path
    mapped_criteria_file: Path
    output_dir: Path
    output_format: str = DEFAULT_OUTPUT_FORMAT
    cohort_base_file: Optional[Path] = None
    fail_on_error: bool = False


@dataclass(frozen=True)
class CohortLevelGeneAlterationOutputs:
    cohort_level_gene_alteration_file: Path
    cohort_level_conflict_report_file: Path


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
    return pd.DataFrame(columns=list(COHORT_LEVEL_GENE_ALTERATION_COLUMNS))


def _empty_cohort_conflict_table() -> pd.DataFrame:
    return pd.DataFrame(columns=list(DEFAULT_COHORT_CONFLICT_COLUMNS))


def _group_effective_rows(
    effective_df: pd.DataFrame,
) -> Dict[Tuple[str, str], pd.DataFrame]:
    if effective_df.empty:
        return {}

    grouped: Dict[Tuple[str, str], pd.DataFrame] = {}
    for key, group in effective_df.groupby(
        ["nct_id", "cohort"],
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


def _expand_mapped_criteria_to_effective_cohorts(
    *,
    mapped_criteria_df: pd.DataFrame,
    cohort_base_df: pd.DataFrame,
) -> pd.DataFrame:
    _require_columns(
        mapped_criteria_df,
        REQUIRED_MAPPED_CRITERIA_COLUMNS,
        label="Mapped gene-alteration criteria table",
    )
    _require_columns(
        cohort_base_df,
        REQUIRED_COHORT_BASE_COLUMNS,
        label="Cohort base table",
    )

    if mapped_criteria_df.empty or cohort_base_df.empty:
        output_columns = list(mapped_criteria_df.columns)
        if "cohort" not in output_columns:
            output_columns.append("cohort")
        return pd.DataFrame(columns=output_columns)

    return expand_to_effective_cohort_rows(
        mapped_criteria_df,
        cohort_base_df,
        nct_col="nct_id",
        cohorts_col="cohorts",
        cohort_col="cohort",
    )


# =============================================================================
# Gene-alteration cohort-level collapse
# =============================================================================


def collapse_effective_gene_alteration_rows_to_cohort_level(
    effective_df: pd.DataFrame,
    *,
    cohort_base_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Collapse already-expanded effective rows to nct_id + cohort.

    Gene-alteration term rendering is delegated to the existing
    gene_alteration_pipeline.collapse_to_trial_level() function, preserving the
    current inclusive/exclusive term rendering and NOT(...) behavior.
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

        collapsed = collapse_to_trial_level(group)
        if collapsed.empty:
            continue

        first = collapsed.iloc[0]
        inclusive = _normalize_string(first.get("gene_alteration_inclusive", ""))
        exclusive = _normalize_string(first.get("gene_alteration_exclusive", ""))

        if not inclusive and not exclusive:
            continue

        output_rows.append(
            {
                "nct_id": nct_id,
                "cohort": cohort,
                "gene_alteration_inclusive": inclusive,
                "gene_alteration_exclusive": exclusive,
            }
        )

    return pd.DataFrame(
        output_rows,
        columns=list(COHORT_LEVEL_GENE_ALTERATION_COLUMNS),
    )


def collapse_to_cohort_level_gene_alteration(
    *,
    mapped_criteria_df: pd.DataFrame,
    cohort_base_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Collapse mapped GeneAlterationCriterion rows to nct_id + cohort.

    Cohort inheritance is delegated to cohort_utils.expand_to_effective_cohort_rows():
      - blank mapped_criteria_df.cohorts rows are general trial-wide rules
      - general rules apply to '(general)' and every explicit cohort for the trial
      - rows with explicit cohorts apply only to those explicit cohorts
    """
    effective_df = _expand_mapped_criteria_to_effective_cohorts(
        mapped_criteria_df=mapped_criteria_df,
        cohort_base_df=cohort_base_df,
    )
    return collapse_effective_gene_alteration_rows_to_cohort_level(
        effective_df,
        cohort_base_df=cohort_base_df,
    )


# =============================================================================
# Cohort-level conflict QA
# =============================================================================


def _insert_or_replace_cohort_column(
    df: pd.DataFrame,
    *,
    cohort: str,
) -> pd.DataFrame:
    out = df.copy()

    if "cohort" in out.columns:
        out["cohort"] = cohort
        return out

    if "nct_id" in out.columns:
        insert_at = list(out.columns).index("nct_id") + 1
    else:
        insert_at = 1

    out.insert(insert_at, "cohort", cohort)
    return out


def build_cohort_level_gene_alteration_conflict_report_from_effective_rows(
    effective_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build a cohort-aware A / NOT(A) conflict report.

    The existing conflict builder is reused within each nct_id + cohort group.
    This prevents apparent conflicts from being called across different cohorts.
    """
    if effective_df.empty:
        return _empty_cohort_conflict_table()

    _require_columns(
        effective_df,
        ["nct_id", "cohort", "polarity", "gene_alteration_curation"],
        label="Effective cohort-expanded gene-alteration criteria table",
    )

    reports: List[pd.DataFrame] = []
    grouped = _group_effective_rows(effective_df)

    for (nct_id, cohort), group in grouped.items():
        if group.empty:
            continue

        report = build_gene_alteration_conflict_report(group)
        if report.empty:
            continue

        report = _insert_or_replace_cohort_column(report, cohort=cohort)
        if "nct_id" in report.columns:
            report["nct_id"] = nct_id
        reports.append(report)

    if not reports:
        return _empty_cohort_conflict_table()

    out = pd.concat(reports, ignore_index=True)

    if "conflict_group_id" in out.columns:
        out["conflict_group_id"] = [str(i) for i in range(1, len(out) + 1)]

    # Prefer the current conflict-report column order, but ensure cohort is present
    # immediately after nct_id when possible.
    columns = list(out.columns)
    if "cohort" in columns and "nct_id" in columns:
        columns.remove("cohort")
        insert_at = columns.index("nct_id") + 1
        columns.insert(insert_at, "cohort")
        out = out.loc[:, columns]

    return out


def build_cohort_level_gene_alteration_conflict_report(
    *,
    mapped_criteria_df: pd.DataFrame,
    cohort_base_df: pd.DataFrame,
) -> pd.DataFrame:
    effective_df = _expand_mapped_criteria_to_effective_cohorts(
        mapped_criteria_df=mapped_criteria_df,
        cohort_base_df=cohort_base_df,
    )
    return build_cohort_level_gene_alteration_conflict_report_from_effective_rows(
        effective_df
    )


# =============================================================================
# Discovery / validation / pipeline orchestration
# =============================================================================


def discover_cohort_level_gene_alteration_inputs(
    *,
    repo_root: Path,
    eligibility_data_dir: Path,
    curated_dir: Optional[Path],
    mapped_criteria_file: Optional[Path],
    output_dir: Optional[Path],
    output_format: str,
    cohort_base_file: Optional[Path] = None,
    fail_on_error: bool = False,
) -> CohortLevelGeneAlterationInputs:
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

    resolved_mapped_criteria_file = (
        _resolve_path(mapped_criteria_file, repo_root)
        if mapped_criteria_file is not None
        else _output_path(
            resolved_output_dir,
            MAPPED_CRITERIA_STEM,
            output_format,
        )
    )

    resolved_cohort_base_file = (
        _resolve_path(cohort_base_file, repo_root)
        if cohort_base_file is not None
        else None
    )

    inputs = CohortLevelGeneAlterationInputs(
        curated_dir=resolved_curated_dir,
        mapped_criteria_file=resolved_mapped_criteria_file,
        output_dir=resolved_output_dir,
        output_format=output_format,
        cohort_base_file=resolved_cohort_base_file,
        fail_on_error=fail_on_error,
    )

    validate_cohort_level_gene_alteration_inputs(inputs)
    return inputs


def validate_cohort_level_gene_alteration_inputs(
    inputs: CohortLevelGeneAlterationInputs,
) -> None:
    if not inputs.mapped_criteria_file.exists():
        raise FileNotFoundError(
            f"Mapped gene-alteration criteria file does not exist: "
            f"{inputs.mapped_criteria_file}"
        )
    if not inputs.mapped_criteria_file.is_file():
        raise ValueError(
            f"Mapped gene-alteration criteria path is not a file: "
            f"{inputs.mapped_criteria_file}"
        )

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
        if not _contains_nct_py_files(inputs.curated_dir):
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


def run_cohort_level_gene_alteration_pipeline(
    inputs: CohortLevelGeneAlterationInputs,
) -> CohortLevelGeneAlterationOutputs:
    inputs.output_dir.mkdir(parents=True, exist_ok=True)

    cohort_level_file = _output_path(
        inputs.output_dir,
        COHORT_LEVEL_STEM,
        inputs.output_format,
    )
    cohort_conflict_file = _output_path(
        inputs.output_dir,
        COHORT_LEVEL_CONFLICTS_STEM,
        inputs.output_format,
    )

    logger.info("Cohort-level gene-alteration inputs:")
    logger.info("  mapped_criteria_file:   %s", inputs.mapped_criteria_file)
    logger.info("  curated_dir:            %s", inputs.curated_dir)
    logger.info("  cohort_base_file:       %s", inputs.cohort_base_file)
    logger.info("  output_dir:             %s", inputs.output_dir)
    logger.info("  output_format:          %s", inputs.output_format)

    mapped_criteria_df = _read_tabular_file(inputs.mapped_criteria_file)
    logger.info(
        "Loaded mapped gene-alteration criteria table: rows=%d",
        len(mapped_criteria_df),
    )

    if "cohorts" in mapped_criteria_df.columns:
        nonblank_cohort_rows = int(
            mapped_criteria_df["cohorts"].astype(str).str.strip().ne("").sum()
        )
        nonblank_cohort_mapped_rows = int(
            (
                mapped_criteria_df["cohorts"].astype(str).str.strip().ne("")
                & mapped_criteria_df.get(
                    "gene_alteration_curation",
                    pd.Series([""] * len(mapped_criteria_df), index=mapped_criteria_df.index),
                )
                .astype(str)
                .str.strip()
                .ne("")
            ).sum()
        )
        logger.info(
            "Mapped gene-alteration rows with nonblank cohorts: rows=%d mapped_nonblank=%d",
            nonblank_cohort_rows,
            nonblank_cohort_mapped_rows,
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

    effective_df = _expand_mapped_criteria_to_effective_cohorts(
        mapped_criteria_df=mapped_criteria_df,
        cohort_base_df=cohort_base_df,
    )
    logger.info(
        "Expanded mapped gene-alteration criteria to effective cohort rows: rows=%d",
        len(effective_df),
    )

    cohort_level_df = collapse_effective_gene_alteration_rows_to_cohort_level(
        effective_df,
        cohort_base_df=cohort_base_df,
    )
    _write_tabular_file(cohort_level_df, cohort_level_file)
    logger.info(
        "Wrote cohort-level gene alteration table: %s rows=%d",
        cohort_level_file,
        len(cohort_level_df),
    )

    cohort_conflict_df = build_cohort_level_gene_alteration_conflict_report_from_effective_rows(
        effective_df
    )
    _write_tabular_file(cohort_conflict_df, cohort_conflict_file)
    logger.info(
        "Wrote cohort-level gene alteration conflict report: %s rows=%d",
        cohort_conflict_file,
        len(cohort_conflict_df),
    )

    return CohortLevelGeneAlterationOutputs(
        cohort_level_gene_alteration_file=cohort_level_file,
        cohort_level_conflict_report_file=cohort_conflict_file,
    )


# =============================================================================
# CLI
# =============================================================================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse mapped GeneAlterationCriterion rows to cohort-level "
            "gene-alteration eligibility output and cohort-level conflict QA."
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
        "--mapped_criteria_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit 03_gene_alteration_mapped_criteria file. "
            "If omitted, uses output_dir/03_gene_alteration_mapped_criteria.<format>."
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
        "--output_dir",
        type=Path,
        default=None,
        help=(
            "Output directory for processed gene-alteration files. "
            "Defaults to eligibility_data_dir/processed/gene_alteration."
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

    inputs = discover_cohort_level_gene_alteration_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        curated_dir=args.curated_dir,
        mapped_criteria_file=args.mapped_criteria_file,
        output_dir=args.output_dir,
        output_format=args.output_format,
        cohort_base_file=args.cohort_base_file,
        fail_on_error=args.fail_on_error,
    )

    outputs = run_cohort_level_gene_alteration_pipeline(inputs)

    logger.info("Cohort-level gene-alteration pipeline complete.")
    logger.info(
        "cohort_level_gene_alteration_file: %s",
        outputs.cohort_level_gene_alteration_file,
    )
    logger.info(
        "cohort_level_conflict_report_file: %s",
        outputs.cohort_level_conflict_report_file,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
