from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Sequence

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils import (
    read_tabular_file as _read_tabular_file,
    resolve_path as _resolve_path,
    write_tabular_file as _write_tabular_file,
)
from aus_trial_universe.eligibility_path.shared.cohorts import (
    normalize_anzctr_trial_id as _normalize_anzctr_trial_id,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_ELIGIBILITY_DATA_DIR = Path("data/eligibility_path")

DEFAULT_TRIALS_FILE = Path("data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv")
DEFAULT_CANCER_TYPE_FILE = Path("exports/intermediates/anzctr/cancer_type/04a_cancer_type_trial_level.tsv")
DEFAULT_GENE_ALTERATION_FILE = Path("exports/intermediates/anzctr/gene_alteration/04a_gene_alteration_trial_level.tsv")
DEFAULT_MOLECULAR_SIGNATURE_FILE = Path(
    "exports/intermediates/anzctr/molecular_signature/03a_molecular_signature_trial_level.tsv"
)

DEFAULT_EXPORT_DIR = Path("exports/final/anzctr")
DEFAULT_EXPORT_STEM = "trial_resource"
DEFAULT_EXPORT_SUFFIX_FORMAT = "%d%m%Y"

TRIAL_ID_COLUMN_CANDIDATES: Sequence[str] = (
    "ACTRN",
    "trial_id",
    "trialId",
    "TrialId",
    "trialid",
    "trial ID",
)

OUTPUT_TRIAL_ID_COLUMN = "trialId"

FINAL_OUTPUT_COLUMNS: Sequence[str] = (
    "cancer_type_inclusive",
    "cancer_type_exclusive",
    "gene_alteration_inclusive",
    "gene_alteration_exclusive",
    "molecular_signature_inclusive",
    "molecular_signature_exclusive",
)


@dataclass(frozen=True)
class TrialResourcePipelineInputs:
    trials_file: Path
    cancer_type_file: Path
    gene_alteration_file: Path
    molecular_signature_file: Path
    output_file: Path
    trial_id_column: Optional[str] = None


@dataclass(frozen=True)
class TrialResourcePipelineOutputs:
    output_file: Path


def _default_export_file(
    eligibility_dir: Path,
    *,
    export_date: Optional[str] = None,
) -> Path:
    if export_date is None:
        export_date = date.today().strftime(DEFAULT_EXPORT_SUFFIX_FORMAT)

    export_date = str(export_date).strip()
    if not re.fullmatch(r"\d{8}", export_date):
        raise ValueError(
            f"export_date must be in ddmmyyyy format, got {export_date!r}"
        )

    return eligibility_dir / DEFAULT_EXPORT_DIR / f"{DEFAULT_EXPORT_STEM}_{export_date}.tsv"


def _normalize_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _find_trial_id_column(df: pd.DataFrame, explicit_column: Optional[str] = None) -> str:
    columns = [str(column).strip() for column in df.columns]

    if explicit_column is not None:
        if explicit_column not in columns:
            raise ValueError(
                f"Explicit trial ID column {explicit_column!r} not found. "
                f"Available columns: {columns}"
            )
        return explicit_column

    normalized_to_original = {
        _normalize_header(column): column
        for column in columns
    }

    for candidate in TRIAL_ID_COLUMN_CANDIDATES:
        normalized_candidate = _normalize_header(candidate)
        if normalized_candidate in normalized_to_original:
            return normalized_to_original[normalized_candidate]

    raise ValueError(
        "Could not infer trial ID column. "
        f"Tried candidates: {list(TRIAL_ID_COLUMN_CANDIDATES)}. "
        f"Available columns: {columns}"
    )


def _canonicalize_base_trial_id_output_column(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` with a single user-visible trial ID column: ``trialId``.

    ``_trial_id_key`` remains as the internal merge key.  Any duplicate trial ID
    columns used only for detection/normalisation, such as ``trial_id``, are
    removed from the exported base columns.
    """
    if "_trial_id_key" not in df.columns:
        raise ValueError("Internal error: _trial_id_key is required before trial ID canonicalisation")

    out = df.copy()
    visible_columns = [column for column in out.columns if column != "_trial_id_key"]
    candidate_norms = {_normalize_header(column) for column in TRIAL_ID_COLUMN_CANDIDATES}
    id_positions = [
        idx
        for idx, column in enumerate(visible_columns)
        if _normalize_header(column) in candidate_norms
    ]
    insert_at = min(id_positions) if id_positions else 0

    out = out.drop(columns=[OUTPUT_TRIAL_ID_COLUMN], errors="ignore")
    out.insert(insert_at, OUTPUT_TRIAL_ID_COLUMN, out["_trial_id_key"])

    duplicate_id_columns = [
        column
        for column in out.columns
        if column not in {OUTPUT_TRIAL_ID_COLUMN, "_trial_id_key"}
        and _normalize_header(column) in candidate_norms
    ]
    if duplicate_id_columns:
        LOGGER.info(
            "Dropping duplicate trial ID column(s) from resource export: %s",
            duplicate_id_columns,
        )
        out = out.drop(columns=duplicate_id_columns)

    return out


def _require_columns(
    df: pd.DataFrame,
    required_columns: Iterable[str],
    *,
    label: str,
) -> None:
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise ValueError(
            f"{label} missing required column(s): {missing}. "
            f"Found columns: {list(df.columns)}"
        )


def _count_nonblank(df: pd.DataFrame, columns: Sequence[str]) -> int:
    if df.empty:
        return 0

    mask = pd.Series(False, index=df.index)
    for column in columns:
        mask = mask | (df[column].fillna("").astype(str).str.strip() != "")

    return int(mask.sum())


# =============================================================================
# Component loading / merging
# =============================================================================


def _load_base_trials(
    trials_file: Path,
    *,
    trial_id_column: Optional[str],
) -> pd.DataFrame:
    df = _read_tabular_file(trials_file)
    df.columns = [str(column).strip() for column in df.columns]

    key_column = _find_trial_id_column(df, trial_id_column)

    out = df.copy()
    out["_trial_id_key"] = out[key_column].map(_normalize_anzctr_trial_id)

    blank_keys = int((out["_trial_id_key"] == "").sum())
    if blank_keys:
        LOGGER.warning("Base trials file has %d row(s) with blank trial ID key", blank_keys)

    out = _canonicalize_base_trial_id_output_column(out)

    duplicate_keys = out.loc[
        out["_trial_id_key"].ne("") & out["_trial_id_key"].duplicated(keep=False),
        "_trial_id_key",
    ]
    if not duplicate_keys.empty:
        LOGGER.warning(
            "Base trials file has %d duplicate trial ID row(s), affecting %d trial(s). "
            "Merge will preserve all base rows.",
            len(duplicate_keys),
            duplicate_keys.nunique(),
        )

    LOGGER.info(
        "Loaded base trials: %s rows=%d unique_trials=%d trial_id_column=%s",
        trials_file,
        len(out),
        out["_trial_id_key"].replace("", pd.NA).dropna().nunique(),
        key_column,
    )

    return out


def _load_component_table(
    path: Path,
    *,
    required_value_columns: Sequence[str],
    label: str,
) -> pd.DataFrame:
    df = _read_tabular_file(path)
    df.columns = [str(column).strip() for column in df.columns]

    trial_id_column = _find_trial_id_column(
        df,
        "trial_id" if "trial_id" in df.columns else None,
    )

    _require_columns(df, required_value_columns, label=label)

    out = df.copy()
    out["_trial_id_key"] = out[trial_id_column].map(_normalize_anzctr_trial_id)
    out = out[out["_trial_id_key"] != ""].copy()

    duplicated = out.loc[out["_trial_id_key"].duplicated(keep=False), "_trial_id_key"]
    if not duplicated.empty:
        duplicate_examples = ", ".join(sorted(duplicated.unique())[:20])
        raise ValueError(
            f"{label} has duplicate trial_id rows for {duplicated.nunique()} trial(s): "
            f"{duplicate_examples}"
        )

    out = out.loc[:, ["_trial_id_key", *required_value_columns]]

    LOGGER.info(
        "Loaded %s: %s rows=%d unique_trials=%d rows_with_any_value=%d",
        label,
        path,
        len(out),
        out["_trial_id_key"].nunique(),
        _count_nonblank(out, required_value_columns),
    )

    return out


def _merge_component(
    base_df: pd.DataFrame,
    component_df: pd.DataFrame,
    *,
    value_columns: Sequence[str],
    label: str,
) -> pd.DataFrame:
    # Drop stale versions of output columns from the base table before merging.
    base_clean = base_df.drop(
        columns=[column for column in value_columns if column in base_df.columns],
        errors="ignore",
    )

    merged = base_clean.merge(
        component_df,
        how="left",
        on="_trial_id_key",
        validate="many_to_one",
    )

    for column in value_columns:
        merged[column] = merged[column].fillna("").astype(str)

    matched_rows = int(
        merged.loc[:, list(value_columns)]
        .apply(lambda series: series.astype(str).str.strip() != "")
        .any(axis=1)
        .sum()
    )

    LOGGER.info(
        "Merged %s: base_rows=%d rows_with_any_%s_value=%d",
        label,
        len(merged),
        label,
        matched_rows,
    )

    return merged


def build_trial_resource_table(
    *,
    trials_file: Path,
    cancer_type_file: Path,
    gene_alteration_file: Path,
    molecular_signature_file: Path,
    trial_id_column: Optional[str] = None,
) -> pd.DataFrame:
    base_df = _load_base_trials(
        trials_file,
        trial_id_column=trial_id_column,
    )

    cancer_type_df = _load_component_table(
        cancer_type_file,
        required_value_columns=(
            "cancer_type_inclusive",
            "cancer_type_exclusive",
        ),
        label="cancer_type",
    )

    gene_alteration_df = _load_component_table(
        gene_alteration_file,
        required_value_columns=(
            "gene_alteration_inclusive",
            "gene_alteration_exclusive",
        ),
        label="gene_alteration",
    )

    molecular_signature_df = _load_component_table(
        molecular_signature_file,
        required_value_columns=(
            "molecular_signature_inclusive",
            "molecular_signature_exclusive",
        ),
        label="molecular_signature",
    )

    out = base_df

    out = _merge_component(
        out,
        cancer_type_df,
        value_columns=("cancer_type_inclusive", "cancer_type_exclusive"),
        label="cancer_type",
    )

    out = _merge_component(
        out,
        gene_alteration_df,
        value_columns=("gene_alteration_inclusive", "gene_alteration_exclusive"),
        label="gene_alteration",
    )

    out = _merge_component(
        out,
        molecular_signature_df,
        value_columns=("molecular_signature_inclusive", "molecular_signature_exclusive"),
        label="molecular_signature",
    )

    out = out.drop(columns=["_trial_id_key"], errors="ignore")

    base_columns = [
        column
        for column in out.columns
        if column not in FINAL_OUTPUT_COLUMNS
    ]
    out = out.loc[:, [*base_columns, *FINAL_OUTPUT_COLUMNS]]

    LOGGER.info(
        "Built trial resource table: rows=%d columns=%d unique_trials=%d",
        len(out),
        len(out.columns),
        out[OUTPUT_TRIAL_ID_COLUMN]
        .map(_normalize_anzctr_trial_id)
        .replace("", pd.NA)
        .dropna()
        .nunique()
        if OUTPUT_TRIAL_ID_COLUMN in out.columns
        else 0,
    )

    return out


# =============================================================================
# Pipeline orchestration
# =============================================================================


def discover_pipeline_inputs(
    *,
    repo_root: Path,
    eligibility_data_dir: Path,
    trials_file: Optional[Path],
    cancer_type_file: Optional[Path],
    gene_alteration_file: Optional[Path],
    molecular_signature_file: Optional[Path],
    output_file: Optional[Path],
    export_date: Optional[str],
    trial_id_column: Optional[str],
) -> TrialResourcePipelineInputs:
    repo_root = repo_root.resolve()
    eligibility_dir = _resolve_path(eligibility_data_dir, repo_root)

    resolved_trials_file = _resolve_path(
        trials_file if trials_file is not None else DEFAULT_TRIALS_FILE,
        repo_root,
    )

    resolved_cancer_type_file = _resolve_path(
        cancer_type_file
        if cancer_type_file is not None
        else eligibility_dir / DEFAULT_CANCER_TYPE_FILE,
        repo_root,
    )

    resolved_gene_alteration_file = _resolve_path(
        gene_alteration_file
        if gene_alteration_file is not None
        else eligibility_dir / DEFAULT_GENE_ALTERATION_FILE,
        repo_root,
    )

    resolved_molecular_signature_file = _resolve_path(
        molecular_signature_file
        if molecular_signature_file is not None
        else eligibility_dir / DEFAULT_MOLECULAR_SIGNATURE_FILE,
        repo_root,
    )

    resolved_output_file = _resolve_path(
        output_file
        if output_file is not None
        else _default_export_file(
            eligibility_dir,
            export_date=export_date,
        ),
        repo_root,
    )

    inputs = TrialResourcePipelineInputs(
        trials_file=resolved_trials_file,
        cancer_type_file=resolved_cancer_type_file,
        gene_alteration_file=resolved_gene_alteration_file,
        molecular_signature_file=resolved_molecular_signature_file,
        output_file=resolved_output_file,
        trial_id_column=trial_id_column,
    )

    validate_pipeline_inputs(inputs)
    return inputs


def validate_pipeline_inputs(inputs: TrialResourcePipelineInputs) -> None:
    required_files = [
        ("base trials file", inputs.trials_file),
        ("trial-level cancer type file", inputs.cancer_type_file),
        ("trial-level gene alteration file", inputs.gene_alteration_file),
        ("trial-level molecular signature file", inputs.molecular_signature_file),
    ]

    for label, path in required_files:
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"{label} is not a file: {path}")

    if inputs.output_file.suffix.casefold() != ".tsv":
        raise ValueError(
            f"Trial resource export must be a TSV file. Got: {inputs.output_file}"
        )


def run_trial_resource_pipeline(
    inputs: TrialResourcePipelineInputs,
) -> TrialResourcePipelineOutputs:
    LOGGER.info("Trial-resource pipeline inputs:")
    LOGGER.info("  trials_file:                %s", inputs.trials_file)
    LOGGER.info("  cancer_type_file:           %s", inputs.cancer_type_file)
    LOGGER.info("  gene_alteration_file:       %s", inputs.gene_alteration_file)
    LOGGER.info("  molecular_signature_file:   %s", inputs.molecular_signature_file)
    LOGGER.info("  output_file:                %s", inputs.output_file)
    LOGGER.info("  trial_id_column:              %s", inputs.trial_id_column)

    trial_resource_df = build_trial_resource_table(
        trials_file=inputs.trials_file,
        cancer_type_file=inputs.cancer_type_file,
        gene_alteration_file=inputs.gene_alteration_file,
        molecular_signature_file=inputs.molecular_signature_file,
        trial_id_column=inputs.trial_id_column,
    )

    _write_tabular_file(trial_resource_df, inputs.output_file)
    LOGGER.info(
        "Wrote trial resource export: %s rows=%d",
        inputs.output_file,
        len(trial_resource_df),
    )

    return TrialResourcePipelineOutputs(
        output_file=inputs.output_file,
    )


# =============================================================================
# CLI
# =============================================================================


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the final ANZCTR trial resource by merging the reviewed "
            "trial-level cancer type, gene alteration, and molecular signature outputs "
            "onto the base ANZCTR field extraction table. The pipeline writes one "
            "dated TSV export."
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
        help="Eligibility path data root. Defaults to data/eligibility_path.",
    )

    parser.add_argument(
        "--trials_file",
        type=Path,
        default=None,
        help=(
            "Base ANZCTR field extraction table. Defaults to "
            "data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv."
        ),
    )

    parser.add_argument(
        "--cancer_type_file",
        type=Path,
        default=None,
        help=(
            "Trial-level cancer type file. Defaults to "
            "data/eligibility_path/exports/intermediates/anzctr/cancer_type/04a_cancer_type_trial_level.tsv."
        ),
    )

    parser.add_argument(
        "--gene_alteration_file",
        type=Path,
        default=None,
        help=(
            "Trial-level gene alteration file. Defaults to "
            "data/eligibility_path/exports/intermediates/anzctr/gene_alteration/04a_gene_alteration_trial_level.tsv."
        ),
    )

    parser.add_argument(
        "--molecular_signature_file",
        type=Path,
        default=None,
        help=(
            "Trial-level molecular signature file. Defaults to "
            "data/eligibility_path/exports/intermediates/anzctr/molecular_signature/03a_molecular_signature_trial_level.tsv."
        ),
    )

    parser.add_argument(
        "--output_file",
        type=Path,
        default=None,
        help=(
            "Output TSV export path. Defaults to "
            "data/eligibility_path/exports/final/anzctr/trial_resource_<ddmmyyyy>.tsv."
        ),
    )

    parser.add_argument(
        "--export_date",
        default=None,
        help=(
            "Date suffix for the default export filename, in ddmmyyyy format. "
            "Defaults to today's date."
        ),
    )

    parser.add_argument(
        "--trial_id_column",
        default=None,
        help="Optional explicit trial ID column in the base trials file.",
    )

    parser.add_argument(
        "--log_level",
        default="INFO",
        help="Logging level.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    inputs = discover_pipeline_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        trials_file=args.trials_file,
        cancer_type_file=args.cancer_type_file,
        gene_alteration_file=args.gene_alteration_file,
        molecular_signature_file=args.molecular_signature_file,
        output_file=args.output_file,
        export_date=args.export_date,
        trial_id_column=args.trial_id_column,
    )

    outputs = run_trial_resource_pipeline(inputs)

    LOGGER.info("Trial-resource pipeline complete.")
    LOGGER.info("output_file:   %s", outputs.output_file)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
