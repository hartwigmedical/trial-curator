from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.eligibility_path.shared.cohorts import (
    build_cohort_base_from_curated_rules,
    expand_to_effective_cohort_rows,
    normalize_cohort_label,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline import (
    normalize_anzctr_trial_id,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.molecular_signature.molecular_signature_pipeline import (
    DEFAULT_CURATED_DIR,
    DEFAULT_ELIGIBILITY_DATA_DIR,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_PROCESSED_SUBDIR,
    MAPPED_CRITERIA_STEM,
    SUPPORTED_OUTPUT_FORMATS,
    _contains_actrn_py_files,
    _output_path,
    _read_tabular_file,
    _resolve_path,
    _write_tabular_file,
    collapse_to_trial_level,
)

logger = logging.getLogger(__name__)

COHORT_LEVEL_STEM = "03b_molecular_signature_cohort_level"

COHORT_LEVEL_MOLECULAR_SIGNATURE_COLUMNS: Sequence[str] = (
    "trial_id",
    "cohort",
    "molecular_signature_inclusive",
    "molecular_signature_exclusive",
)

REQUIRED_MAPPED_CRITERIA_COLUMNS: Sequence[str] = (
    "trial_id",
    "cohorts",
    "polarity",
    "molecular_signature_curation",
)

REQUIRED_COHORT_BASE_COLUMNS: Sequence[str] = (
    "trial_id",
    "cohort",
)


@dataclass(frozen=True)
class CohortLevelMolecularSignatureInputs:
    curated_dir: Path
    mapped_criteria_file: Path
    output_dir: Path
    output_format: str = DEFAULT_OUTPUT_FORMAT
    cohort_base_file: Optional[Path] = None
    fail_on_error: bool = False


@dataclass(frozen=True)
class CohortLevelMolecularSignatureOutputs:
    cohort_level_molecular_signature_file: Path


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
    return pd.DataFrame(
        columns=list(COHORT_LEVEL_MOLECULAR_SIGNATURE_COLUMNS),
    )


def _group_effective_rows(
    effective_df: pd.DataFrame,
) -> Dict[Tuple[str, str], pd.DataFrame]:
    if effective_df.empty:
        return {}

    grouped: Dict[Tuple[str, str], pd.DataFrame] = {}
    for key, group in effective_df.groupby(
        ["trial_id", "cohort"],
        sort=False,
        dropna=False,
    ):
        trial_id, cohort = key
        grouped[(normalize_anzctr_trial_id(trial_id), normalize_cohort_label(cohort))] = group

    return grouped


def _cohort_keys_in_base_order(
    cohort_base_df: pd.DataFrame,
) -> List[Tuple[str, str]]:
    keys: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    for _, row in cohort_base_df.iterrows():
        trial_id = normalize_anzctr_trial_id(row.get("trial_id", ""))
        cohort = normalize_cohort_label(row.get("cohort", ""))

        if not trial_id or not cohort:
            continue

        key = (trial_id, cohort)
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


# =============================================================================
# Molecular-signature cohort-level collapse
# =============================================================================


def collapse_to_cohort_level_molecular_signature(
    *,
    mapped_criteria_df: pd.DataFrame,
    cohort_base_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Collapse mapped MolecularSignatureCriterion rows to trial_id + cohort.

    Cohort inheritance is delegated to cohort_utils.expand_to_effective_cohort_rows():
      - blank mapped_criteria_df.cohorts rows are general trial-wide rules
      - general rules apply to '(general)' and every explicit cohort for the trial
      - rows with explicit cohorts apply only to those explicit cohorts

    Molecular-signature term rendering is delegated to the existing
    molecular_signature_pipeline.collapse_to_trial_level() function, preserving the
    current inclusive/exclusive term rendering and NOT(...) behavior.
    """
    _require_columns(
        mapped_criteria_df,
        REQUIRED_MAPPED_CRITERIA_COLUMNS,
        label="Mapped molecular-signature criteria table",
    )
    _require_columns(
        cohort_base_df,
        REQUIRED_COHORT_BASE_COLUMNS,
        label="Cohort base table",
    )

    if mapped_criteria_df.empty or cohort_base_df.empty:
        return _empty_cohort_level_table()

    effective_df = expand_to_effective_cohort_rows(
        mapped_criteria_df,
        cohort_base_df,
        trial_id_col="trial_id",
        cohorts_col="cohorts",
        cohort_col="cohort",
    )

    if effective_df.empty:
        return _empty_cohort_level_table()

    grouped = _group_effective_rows(effective_df)
    ordered_keys = _cohort_keys_in_base_order(cohort_base_df)
    ordered_keys = _append_remaining_group_keys(ordered_keys, grouped)

    output_rows: List[Dict[str, str]] = []

    for trial_id, cohort in ordered_keys:
        group = grouped.get((trial_id, cohort))
        if group is None or group.empty:
            continue

        collapsed = collapse_to_trial_level(group)
        if collapsed.empty:
            continue

        first = collapsed.iloc[0]
        inclusive = _normalize_string(
            first.get("molecular_signature_inclusive", "")
        )
        exclusive = _normalize_string(
            first.get("molecular_signature_exclusive", "")
        )

        if not inclusive and not exclusive:
            continue

        output_rows.append(
            {
                "trial_id": trial_id,
                "cohort": cohort,
                "molecular_signature_inclusive": inclusive,
                "molecular_signature_exclusive": exclusive,
            }
        )

    return pd.DataFrame(
        output_rows,
        columns=list(COHORT_LEVEL_MOLECULAR_SIGNATURE_COLUMNS),
    )


# =============================================================================
# Discovery / validation / pipeline orchestration
# =============================================================================


def discover_cohort_level_molecular_signature_inputs(
    *,
    repo_root: Path,
    eligibility_data_dir: Path,
    curated_dir: Optional[Path],
    mapped_criteria_file: Optional[Path],
    output_dir: Optional[Path],
    output_format: str,
    cohort_base_file: Optional[Path] = None,
    fail_on_error: bool = False,
) -> CohortLevelMolecularSignatureInputs:
    repo_root = repo_root.resolve()
    resolved_eligibility_data_dir = _resolve_path(eligibility_data_dir, repo_root)

    resolved_curated_dir = (
        _resolve_path(curated_dir, repo_root)
        if curated_dir is not None
        else _resolve_path(DEFAULT_CURATED_DIR, repo_root)
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

    inputs = CohortLevelMolecularSignatureInputs(
        curated_dir=resolved_curated_dir,
        mapped_criteria_file=resolved_mapped_criteria_file,
        output_dir=resolved_output_dir,
        output_format=output_format,
        cohort_base_file=resolved_cohort_base_file,
        fail_on_error=fail_on_error,
    )

    validate_cohort_level_molecular_signature_inputs(inputs)
    return inputs


def validate_cohort_level_molecular_signature_inputs(
    inputs: CohortLevelMolecularSignatureInputs,
) -> None:
    if not inputs.mapped_criteria_file.exists():
        raise FileNotFoundError(
            f"Mapped molecular-signature criteria file does not exist: "
            f"{inputs.mapped_criteria_file}"
        )

    if not inputs.mapped_criteria_file.is_file():
        raise ValueError(
            f"Mapped molecular-signature criteria path is not a file: "
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
        if not _contains_actrn_py_files(inputs.curated_dir):
            raise FileNotFoundError(
                f"Curated rules path does not contain ACTRN*.py files directly: "
                f"{inputs.curated_dir}"
            )

    normalized_format = inputs.output_format.casefold().lstrip(".")
    if normalized_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"Unsupported output_format: {inputs.output_format!r}. "
            "Expected tsv, csv, xlsx, or xls."
        )


def run_cohort_level_molecular_signature_pipeline(
    inputs: CohortLevelMolecularSignatureInputs,
) -> CohortLevelMolecularSignatureOutputs:
    inputs.output_dir.mkdir(parents=True, exist_ok=True)

    output_file = _output_path(
        inputs.output_dir,
        COHORT_LEVEL_STEM,
        inputs.output_format,
    )

    logger.info("Cohort-level molecular-signature inputs:")
    logger.info("  mapped_criteria_file:   %s", inputs.mapped_criteria_file)
    logger.info("  curated_dir:            %s", inputs.curated_dir)
    logger.info("  cohort_base_file:       %s", inputs.cohort_base_file)
    logger.info("  output_dir:             %s", inputs.output_dir)
    logger.info("  output_format:          %s", inputs.output_format)

    mapped_criteria_df = _read_tabular_file(inputs.mapped_criteria_file)
    logger.info(
        "Loaded mapped molecular-signature criteria table: rows=%d",
        len(mapped_criteria_df),
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
            trial_id_prefix="ACTRN",
            trial_id_column="trial_id",
            normalize_trial_id=normalize_anzctr_trial_id,
        )
        logger.info(
            "Built cohort base table from curated rules: rows=%d",
            len(cohort_base_df),
        )

    cohort_level_df = collapse_to_cohort_level_molecular_signature(
        mapped_criteria_df=mapped_criteria_df,
        cohort_base_df=cohort_base_df,
    )

    _write_tabular_file(cohort_level_df, output_file)
    logger.info(
        "Wrote cohort-level molecular signature table: %s rows=%d",
        output_file,
        len(cohort_level_df),
    )

    return CohortLevelMolecularSignatureOutputs(
        cohort_level_molecular_signature_file=output_file,
    )


# =============================================================================
# CLI
# =============================================================================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse mapped MolecularSignatureCriterion rows to cohort-level "
            "molecular-signature eligibility output."
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
        help=(
            "ANZCTR eligibility intermediate export root. Defaults to "
            "data/eligibility_path/exports/intermediates/anzctr."
        ),
    )
    parser.add_argument(
        "--curated_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing original curated ACTRN*.py files, or a single "
            "ACTRN*.py file. Required only when --cohort_base_file is omitted. "
            "Defaults to data/trial_inputs/anzctr/eligibility_curations."
        ),
    )
    parser.add_argument(
        "--mapped_criteria_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit 02_molecular_signature_mapped_criteria file. "
            "If omitted, uses output_dir/02_molecular_signature_mapped_criteria.<format>."
        ),
    )
    parser.add_argument(
        "--cohort_base_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit cohort base file with columns trial_id and cohort. "
            "If omitted, the cohort base is built from curated Rule.cohorts values."
        ),
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help=(
            "Output directory for processed molecular-signature files. "
            "Defaults to eligibility_data_dir/molecular_signature."
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

    inputs = discover_cohort_level_molecular_signature_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        curated_dir=args.curated_dir,
        mapped_criteria_file=args.mapped_criteria_file,
        output_dir=args.output_dir,
        output_format=args.output_format,
        cohort_base_file=args.cohort_base_file,
        fail_on_error=args.fail_on_error,
    )

    outputs = run_cohort_level_molecular_signature_pipeline(inputs)

    logger.info("Cohort-level molecular-signature pipeline complete.")
    logger.info(
        "cohort_level_molecular_signature_file: %s",
        outputs.cohort_level_molecular_signature_file,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
