from __future__ import annotations

import argparse
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from aus_trial_universe.eligibility_path.shared.cancer_types.conditions.conditions_mapping import (
    build_conditions_mapping_table,
    find_conditions_mapping_file,
    load_mapping,
    read_tabular_file,
)
from aus_trial_universe.eligibility_path.shared.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.eligibility_path.shared.utils.curated_files import (
    has_curated_py_files,
    iter_curated_py_files,
)
from aus_trial_universe.eligibility_path.shared.cancer_types.final_determination.primary_vs_conditions import (
    add_comparison_columns,
    build_conditions_only_rows,
    extract_rows_from_rules,
    rows_to_dataframe,
)
from aus_trial_universe.eligibility_path.shared.cancer_types.final_determination.criterion_level_determination import (
    load_oncotree_hierarchy as load_criterion_level_oncotree_hierarchy,
    run_combined_workflow as determine_criterion_level_cancer_type,
)
from aus_trial_universe.eligibility_path.shared.cancer_types.final_determination.trial_level_determination import (
    collapse_to_unique_trials,
    load_oncotree_hierarchy as load_trial_level_oncotree_hierarchy,
)
from aus_trial_universe.eligibility_path.shared.cancer_types import (
    build_primary_tumor_map,
)
from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    SUPPORTED_CSV_SUFFIXES,
    SUPPORTED_TABULAR_SUFFIXES,
    find_best_file as _find_best_file,
    output_path as _output_path,
    resolve_path as _resolve_path,
    write_tabular_file as _write_tabular_file,
)
from aus_trial_universe.eligibility_path.shared.oncotree.traverse_oncotree import OncoTree
from aus_trial_universe.eligibility_path.shared.cohorts import normalize_anzctr_trial_id

logger = logging.getLogger(__name__)

DEFAULT_ELIGIBILITY_DATA_DIR = Path("data/eligibility_path/exports/intermediates/anzctr")
DEFAULT_SHARED_RESOURCES_DIR = Path("data/eligibility_path/resources")
DEFAULT_TRIALS_DIR = Path("data/trial_inputs/anzctr/extracted_trials")
DEFAULT_PROCESSED_SUBDIR = Path("cancer_type")
DEFAULT_CURATED_DIR = Path("data/trial_inputs/anzctr/eligibility_curations")
DEFAULT_CANCER_TYPE_RESOURCE_SUBDIR = Path("cancer_type")
DEFAULT_ONCOTREE_CSV = Path("oncotree/oncotree.csv")

DEFAULT_INPUT_CSV = Path("data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv")

HEALTH_CONDITION_MAPPING_STEM = "01_health_condition_mapping"
PRIMARY_VS_HEALTH_CONDITION_STEM = "02_primary_vs_health_condition"
ROW_LEVEL_STEM = "03_row_level_cancer_type"
TRIAL_LEVEL_STEM = "04a_cancer_type_trial_level"

DEFAULT_OUTPUT_FORMAT = "tsv"
PRIMARY_VS_HEALTH_CONDITION_ROW_COL = "primary_vs_health_condition_row"
TRIAL_ID_COL = "trial_id"
RELATION_COL = "primary_vs_conditions_relation"
MANUAL_OVERWRITE_COL = "manual_overwrite"
MANUAL_KEY_COLS: Sequence[str] = (
    TRIAL_ID_COL,
    "primary_tumor_type",
    "primary_tumor_location",
    "conditions_original",
)


@dataclass(frozen=True)
class AnzctrCancerTypePipelineInputs:
    input_csv: Path
    curated_dir: Path
    cancer_type_resource_dir: Path
    oncotree_csv: Path
    output_dir: Path
    manual_overwrite_file: Optional[Path] = None
    output_format: str = DEFAULT_OUTPUT_FORMAT
    fail_on_error: bool = False


@dataclass(frozen=True)
class AnzctrCancerTypePipelineOutputs:
    health_condition_mapping_file: Path
    primary_vs_health_condition_file: Path
    row_level_cancer_type_file: Path
    trial_level_cancer_type_file: Path


def _read_anzctr_input_with_trial_id(input_csv: Path) -> pd.DataFrame:
    df = read_tabular_file(input_csv)
    df.columns = [str(column).strip() for column in df.columns]

    if "ACTRN" not in df.columns:
        raise ValueError(f"ANZCTR input CSV must contain ACTRN. Found columns: {list(df.columns)}")
    if "HEALTH CONDITION" not in df.columns:
        raise ValueError(
            f"ANZCTR input CSV must contain HEALTH CONDITION. Found columns: {list(df.columns)}"
        )

    out = df.copy()
    out["trial_id"] = out["ACTRN"].map(normalize_anzctr_trial_id)
    blank_trial_ids = int((out["trial_id"].str.strip() == "").sum())
    if blank_trial_ids:
        logger.warning("ANZCTR input has %d row(s) with blank ACTRN/trial_id", blank_trial_ids)
    return out


def build_health_condition_mapping_table(
    input_csv: Path,
    *,
    cancer_type_resource_dir: Path,
) -> pd.DataFrame:
    mapping_file = find_conditions_mapping_file(cancer_type_resource_dir)
    logger.info("Using conditions mapping file: %s", mapping_file)
    mapping, mapping_ci = load_mapping(mapping_file)

    input_df = _read_anzctr_input_with_trial_id(input_csv)
    return build_conditions_mapping_table(
        input_df,
        trial_id_column="trial_id",
        conditions_column="HEALTH CONDITION",
        mapping=mapping,
        mapping_ci=mapping_ci,
        parser="pipe",
    )


def _normalise_manual_overwrite_file(path: Path) -> pd.DataFrame:
    manual_df = read_tabular_file(path)
    manual_df.columns = [str(column).strip() for column in manual_df.columns]

    has_row_number = PRIMARY_VS_HEALTH_CONDITION_ROW_COL in manual_df.columns
    id_column = None
    for candidate in (TRIAL_ID_COL, "ACTRN", "nct_id"):
        if candidate in manual_df.columns:
            id_column = candidate
            break

    if id_column is None and not has_row_number:
        raise ValueError(
            "ANZCTR manual overwrite file must contain primary_vs_health_condition_row "
            "or one of trial_id, ACTRN, or nct_id. "
            f"Found columns: {list(manual_df.columns)}"
        )

    out = manual_df.copy()
    if id_column is not None:
        out[TRIAL_ID_COL] = out[id_column].map(normalize_anzctr_trial_id)
    if MANUAL_OVERWRITE_COL not in out.columns:
        raise ValueError(
            f"ANZCTR manual overwrite file must contain {MANUAL_OVERWRITE_COL!r}. "
            f"Found columns: {list(manual_df.columns)}"
        )
    return out


def _normalise_manual_value(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _add_primary_vs_row_numbers(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if PRIMARY_VS_HEALTH_CONDITION_ROW_COL in out.columns:
        out = out.drop(columns=[PRIMARY_VS_HEALTH_CONDITION_ROW_COL])
    out.insert(0, PRIMARY_VS_HEALTH_CONDITION_ROW_COL, range(1, len(out) + 1))
    return out


def _validate_manual_row_alignment(
    *,
    generated_row: pd.Series,
    manual_row: pd.Series,
    manual_file: Path,
    manual_row_number: int,
) -> None:
    mismatches = []

    for column in MANUAL_KEY_COLS:
        if column not in manual_row.index:
            continue

        generated_value = _normalise_manual_value(generated_row.get(column, ""))
        manual_value = _normalise_manual_value(manual_row.get(column, ""))
        if column == TRIAL_ID_COL:
            manual_value = normalize_anzctr_trial_id(manual_value)
        if generated_value != manual_value:
            mismatches.append(f"{column}: generated={generated_value!r} manual={manual_value!r}")

    if mismatches:
        raise ValueError(
            "ANZCTR manual overwrite row failed alignment checks. "
            f"File={manual_file}, manual row={manual_row_number}, "
            f"{PRIMARY_VS_HEALTH_CONDITION_ROW_COL}={manual_row.get(PRIMARY_VS_HEALTH_CONDITION_ROW_COL)!r}. "
            f"Mismatches: {mismatches}"
        )


def _apply_manual_overwrite_by_row_number(
    generated_df: pd.DataFrame,
    manual_df: pd.DataFrame,
    *,
    manual_file: Path,
) -> pd.DataFrame:
    out = generated_df.copy()
    row_number_to_index = {
        int(row_number): index
        for index, row_number in out[PRIMARY_VS_HEALTH_CONDITION_ROW_COL].items()
    }

    applied = 0
    for manual_row_number, (_, manual_row) in enumerate(manual_df.iterrows(), start=2):
        manual_value = _normalise_manual_value(manual_row.get(MANUAL_OVERWRITE_COL, ""))
        if not manual_value:
            continue

        raw_row_number = _normalise_manual_value(
            manual_row.get(PRIMARY_VS_HEALTH_CONDITION_ROW_COL, "")
        )
        try:
            generated_row_number = int(raw_row_number)
        except ValueError as exc:
            raise ValueError(
                f"Invalid {PRIMARY_VS_HEALTH_CONDITION_ROW_COL!r} in {manual_file} "
                f"at manual row {manual_row_number}: {raw_row_number!r}"
            ) from exc

        if generated_row_number not in row_number_to_index:
            raise ValueError(
                f"{manual_file} refers to unknown {PRIMARY_VS_HEALTH_CONDITION_ROW_COL} "
                f"{generated_row_number} at manual row {manual_row_number}"
            )

        generated_index = row_number_to_index[generated_row_number]
        _validate_manual_row_alignment(
            generated_row=out.loc[generated_index],
            manual_row=manual_row,
            manual_file=manual_file,
            manual_row_number=manual_row_number,
        )
        out.at[generated_index, MANUAL_OVERWRITE_COL] = manual_value
        applied += 1

    logger.info("Applied %d ANZCTR manual overwrite row(s) by row number", applied)
    return out


def _manual_key(row: pd.Series) -> tuple[str, str, str, str]:
    return (
        normalize_anzctr_trial_id(row.get(TRIAL_ID_COL, "")),
        _normalise_manual_value(row.get("primary_tumor_type", "")),
        _normalise_manual_value(row.get("primary_tumor_location", "")),
        _normalise_manual_value(row.get("conditions_original", "")),
    )


def _apply_manual_overwrite_by_key(
    generated_df: pd.DataFrame,
    manual_df: pd.DataFrame,
    *,
    manual_file: Path,
) -> pd.DataFrame:
    out = generated_df.copy()

    missing_key_cols = [column for column in MANUAL_KEY_COLS if column not in manual_df.columns]
    if missing_key_cols:
        raise ValueError(
            "ANZCTR manual overwrite file must include either "
            f"{PRIMARY_VS_HEALTH_CONDITION_ROW_COL!r}, or all key columns "
            f"{list(MANUAL_KEY_COLS)}. Missing: {missing_key_cols}"
        )

    generated_index_by_key: Dict[tuple[str, str, str, str], List[int]] = {}
    for generated_index, generated_row in out.iterrows():
        generated_index_by_key.setdefault(_manual_key(generated_row), []).append(generated_index)

    applied = 0
    for manual_row_number, (_, manual_row) in enumerate(manual_df.iterrows(), start=2):
        manual_value = _normalise_manual_value(manual_row.get(MANUAL_OVERWRITE_COL, ""))
        if not manual_value:
            continue

        key = _manual_key(manual_row)
        generated_indices = generated_index_by_key.get(key, [])
        if not generated_indices:
            raise ValueError(
                f"Could not align ANZCTR manual overwrite row {manual_row_number} "
                f"from {manual_file}; no generated row matches key {key!r}."
            )
        if len(generated_indices) > 1:
            raise ValueError(
                f"Could not align ANZCTR manual overwrite row {manual_row_number} "
                f"from {manual_file}; key {key!r} matches {len(generated_indices)} "
                f"generated rows. Add {PRIMARY_VS_HEALTH_CONDITION_ROW_COL!r}."
            )

        out.at[generated_indices[0], MANUAL_OVERWRITE_COL] = manual_value
        applied += 1

    logger.info("Applied %d ANZCTR manual overwrite row(s) by key", applied)
    return out


def apply_anzctr_manual_overwrites(generated_df: pd.DataFrame, manual_file: Path) -> pd.DataFrame:
    manual_df = _normalise_manual_overwrite_file(manual_file)

    if PRIMARY_VS_HEALTH_CONDITION_ROW_COL in manual_df.columns:
        return _apply_manual_overwrite_by_row_number(
            generated_df,
            manual_df,
            manual_file=manual_file,
        )

    return _apply_manual_overwrite_by_key(
        generated_df,
        manual_df,
        manual_file=manual_file,
    )


def build_primary_vs_health_condition_table(
    *,
    curated_dir: Path,
    health_condition_mapping_file: Path,
    cancer_type_resource_dir: Path,
    oncotree_csv: Path,
    manual_overwrite_file: Optional[Path],
    fail_on_error: bool,
) -> pd.DataFrame:
    primary_tumour_resource = _find_best_file(
        cancer_type_resource_dir,
        token_groups=[
            ["primarytumour", "primary_tumour", "primarytumor", "primary_tumor"],
        ],
        allowed_suffixes=SUPPORTED_TABULAR_SUFFIXES,
        label="primary tumour curation resource",
        warn_on_multiple=False,
    )
    logger.info("Using primary tumour mapping resource: %s", primary_tumour_resource)

    pt_map = build_primary_tumor_map(primary_tumour_resource)
    pt_map.pop(("", ""), None)

    conditions_lookup = {}
    conditions_df = read_tabular_file(health_condition_mapping_file)
    for _, row in conditions_df.iterrows():
        trial_id = normalize_anzctr_trial_id(row.get("trial_id", ""))
        if not trial_id:
            continue
        conditions_lookup[trial_id] = {
            "conditions_original": str(row.get("conditions_original", "")).strip(),
            "conditions_oncotree_curation": str(
                row.get("conditions_oncotree_curation", "")
            ).strip(),
        }
    logger.info(
        "Loaded trial-level health conditions for %d trial(s) from %s",
        len(conditions_lookup),
        health_condition_mapping_file,
    )

    tree = OncoTree.from_oncotree_csv(oncotree_csv)
    rows = []
    n_files = 0

    for py_path in iter_curated_py_files(curated_dir, trial_id_prefix="ACTRN"):
        n_files += 1
        trial_id = normalize_anzctr_trial_id(py_path.stem)
        if trial_id not in conditions_lookup:
            logger.info(
                "Skipping %s because %s is not present in health-condition lookup",
                py_path,
                trial_id,
            )
            continue

        try:
            rules = load_curated_rules(py_path)
            if not rules:
                logger.warning("No rules loaded from %s", py_path)
                continue
            rows.extend(
                extract_rows_from_rules(
                    nct_id=trial_id,
                    rules=rules,
                    pt_map=pt_map,
                    conditions_lookup=conditions_lookup,
                )
            )
        except Exception as exc:
            if fail_on_error:
                raise
            logger.exception("Skipping %s due to error: %s", py_path, exc)

    logger.info(
        "Extracted %d primary tumour occurrence row(s) from %d ACTRN file(s)",
        len(rows),
        n_files,
    )

    primary_out = rows_to_dataframe(rows)
    primary_out = add_comparison_columns(primary_out, tree)
    primary_out[MANUAL_OVERWRITE_COL] = ""

    conditions_only_rows = build_conditions_only_rows(
        primary_tumor_rows=rows,
        conditions_lookup=conditions_lookup,
    )
    logger.info(
        "Adding %d health-condition-only trial row(s) with no PrimaryTumorCriterion",
        len(conditions_only_rows),
    )

    if conditions_only_rows:
        conditions_only_out = rows_to_dataframe(conditions_only_rows)
        conditions_only_out = add_comparison_columns(conditions_only_out, tree)
        conditions_only_out[MANUAL_OVERWRITE_COL] = ""

        for column in primary_out.columns:
            if column not in conditions_only_out.columns:
                conditions_only_out[column] = ""
        for column in conditions_only_out.columns:
            if column not in primary_out.columns:
                primary_out[column] = ""

        primary_out = pd.concat(
            [primary_out, conditions_only_out.loc[:, primary_out.columns]],
            ignore_index=True,
        )

    primary_out = primary_out.rename(columns={"nct_id": TRIAL_ID_COL})
    primary_out = _add_primary_vs_row_numbers(primary_out)

    if manual_overwrite_file is not None:
        logger.info("Loading ANZCTR manual overwrite file: %s", manual_overwrite_file)
        primary_out = apply_anzctr_manual_overwrites(primary_out, manual_overwrite_file)

    return primary_out


def discover_pipeline_inputs(
    *,
    repo_root: Path,
    eligibility_data_dir: Path,
    input_csv: Optional[Path],
    curated_dir: Optional[Path],
    resources_dir: Optional[Path],
    cancer_type_resource_dir: Optional[Path],
    oncotree_csv: Optional[Path],
    manual_overwrite_file: Optional[Path],
    output_dir: Optional[Path],
    output_format: str,
    fail_on_error: bool,
) -> AnzctrCancerTypePipelineInputs:
    repo_root = repo_root.resolve()
    resolved_eligibility_data_dir = _resolve_path(eligibility_data_dir, repo_root)

    resolved_input_csv = (
        _resolve_path(input_csv, repo_root)
        if input_csv is not None
        else _resolve_path(DEFAULT_INPUT_CSV, repo_root)
    )
    if input_csv is None and not resolved_input_csv.exists():
        resolved_input_csv = _find_best_file(
            _resolve_path(DEFAULT_TRIALS_DIR, repo_root),
            token_groups=[["anzctr"], ["field", "extraction", "extractions"]],
            allowed_suffixes=SUPPORTED_CSV_SUFFIXES,
            recursive=False,
            label="ANZCTR field extractions CSV",
            warn_on_multiple=False,
        )

    resolved_curated_dir = (
        _resolve_path(curated_dir, repo_root)
        if curated_dir is not None
        else _resolve_path(DEFAULT_CURATED_DIR, repo_root)
    )

    resolved_resources_dir = (
        _resolve_path(resources_dir, repo_root)
        if resources_dir is not None
        else _resolve_path(DEFAULT_SHARED_RESOURCES_DIR, repo_root)
    )

    resolved_cancer_type_resource_dir = (
        _resolve_path(cancer_type_resource_dir, repo_root)
        if cancer_type_resource_dir is not None
        else resolved_resources_dir / DEFAULT_CANCER_TYPE_RESOURCE_SUBDIR
    )

    resolved_oncotree_csv = (
        _resolve_path(oncotree_csv, repo_root)
        if oncotree_csv is not None
        else resolved_resources_dir / DEFAULT_ONCOTREE_CSV
    )

    resolved_manual_overwrite_file = (
        _resolve_path(manual_overwrite_file, repo_root)
        if manual_overwrite_file is not None
        else None
    )

    resolved_output_dir = (
        _resolve_path(output_dir, repo_root)
        if output_dir is not None
        else resolved_eligibility_data_dir / DEFAULT_PROCESSED_SUBDIR
    )

    inputs = AnzctrCancerTypePipelineInputs(
        input_csv=resolved_input_csv,
        curated_dir=resolved_curated_dir,
        cancer_type_resource_dir=resolved_cancer_type_resource_dir,
        oncotree_csv=resolved_oncotree_csv,
        manual_overwrite_file=resolved_manual_overwrite_file,
        output_dir=resolved_output_dir,
        output_format=output_format,
        fail_on_error=fail_on_error,
    )
    validate_pipeline_inputs(inputs)
    return inputs


def validate_pipeline_inputs(inputs: AnzctrCancerTypePipelineInputs) -> None:
    required_files = [
        ("ANZCTR field extractions CSV", inputs.input_csv),
        ("OncoTree CSV", inputs.oncotree_csv),
    ]
    if inputs.manual_overwrite_file is not None:
        required_files.append(("ANZCTR manual overwrite file", inputs.manual_overwrite_file))

    for label, path in required_files:
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"{label} is not a file: {path}")

    if not inputs.curated_dir.exists():
        raise FileNotFoundError(
            f"Curated rules directory/file does not exist: {inputs.curated_dir}"
        )
    if not has_curated_py_files(inputs.curated_dir, trial_id_prefix="ACTRN"):
        raise FileNotFoundError(
            f"Curated rules path does not contain ACTRN*.py files directly: {inputs.curated_dir}"
        )
    if not inputs.cancer_type_resource_dir.exists():
        raise FileNotFoundError(
            f"Cancer-type resource directory does not exist: {inputs.cancer_type_resource_dir}"
        )
    if not inputs.cancer_type_resource_dir.is_dir():
        raise ValueError(
            f"Cancer-type resource path is not a directory: {inputs.cancer_type_resource_dir}"
        )

    find_conditions_mapping_file(inputs.cancer_type_resource_dir)
    _find_best_file(
        inputs.cancer_type_resource_dir,
        token_groups=[
            ["primarytumour", "primary_tumour", "primarytumor", "primary_tumor"],
        ],
        allowed_suffixes=SUPPORTED_TABULAR_SUFFIXES,
        label="primary tumour curation resource",
        warn_on_multiple=False,
    )


def run_cancer_type_pipeline(
    inputs: AnzctrCancerTypePipelineInputs,
) -> AnzctrCancerTypePipelineOutputs:
    inputs.output_dir.mkdir(parents=True, exist_ok=True)

    health_condition_mapping_file = _output_path(
        inputs.output_dir,
        HEALTH_CONDITION_MAPPING_STEM,
        inputs.output_format,
    )
    primary_vs_health_condition_file = _output_path(
        inputs.output_dir,
        PRIMARY_VS_HEALTH_CONDITION_STEM,
        inputs.output_format,
    )
    row_level_file = _output_path(inputs.output_dir, ROW_LEVEL_STEM, inputs.output_format)
    trial_level_file = _output_path(inputs.output_dir, TRIAL_LEVEL_STEM, inputs.output_format)

    logger.info("ANZCTR cancer-type pipeline inputs:")
    logger.info("  input_csv:                  %s", inputs.input_csv)
    logger.info("  curated_dir:                %s", inputs.curated_dir)
    logger.info("  cancer_type_resource_dir:   %s", inputs.cancer_type_resource_dir)
    logger.info("  oncotree_csv:               %s", inputs.oncotree_csv)
    logger.info("  manual_overwrite_file:      %s", inputs.manual_overwrite_file)
    logger.info("  output_dir:                 %s", inputs.output_dir)
    logger.info("  output_format:              %s", inputs.output_format)

    with tempfile.TemporaryDirectory(prefix="anzctr_cancer_type_pipeline_") as tmp:
        tmp_conditions_csv = Path(tmp) / "01_health_condition_mapping.csv"

        logger.info("[1/4] Mapping ANZCTR HEALTH CONDITION values to OncoTree")
        health_condition_mapping_df = build_health_condition_mapping_table(
            inputs.input_csv,
            cancer_type_resource_dir=inputs.cancer_type_resource_dir,
        )
        _write_tabular_file(health_condition_mapping_df, health_condition_mapping_file)
        _write_tabular_file(health_condition_mapping_df, tmp_conditions_csv)
        logger.info(
            "Wrote health-condition mapping: %s rows=%d",
            health_condition_mapping_file,
            len(health_condition_mapping_df),
        )

        logger.info("[2/4] Building primary-vs-health-condition occurrence table")
        primary_vs_health_condition_df = build_primary_vs_health_condition_table(
            curated_dir=inputs.curated_dir,
            health_condition_mapping_file=tmp_conditions_csv,
            cancer_type_resource_dir=inputs.cancer_type_resource_dir,
            oncotree_csv=inputs.oncotree_csv,
            manual_overwrite_file=inputs.manual_overwrite_file,
            fail_on_error=inputs.fail_on_error,
        )
        _write_tabular_file(primary_vs_health_condition_df, primary_vs_health_condition_file)
        logger.info(
            "Wrote primary-vs-health-condition table: %s rows=%d",
            primary_vs_health_condition_file,
            len(primary_vs_health_condition_df),
        )

    logger.info("[3/4] Determining criterion-level cancer type")
    criterion_level_hierarchy = load_criterion_level_oncotree_hierarchy(inputs.oncotree_csv)
    row_level_df = determine_criterion_level_cancer_type(
        primary_vs_health_condition_df,
        criterion_level_hierarchy,
    )
    _write_tabular_file(row_level_df, row_level_file)
    logger.info("Wrote criterion-level cancer type table: %s rows=%d", row_level_file, len(row_level_df))

    logger.info("[4/4] Collapsing to trial-level cancer type")
    trial_level_hierarchy = load_trial_level_oncotree_hierarchy(inputs.oncotree_csv)
    trial_level_df = collapse_to_unique_trials(
        row_level_df,
        trial_level_hierarchy,
        id_col="trial_id",
        output_id_col="trial_id",
    )
    _write_tabular_file(trial_level_df, trial_level_file)
    logger.info(
        "Wrote trial-level cancer type table: %s rows=%d",
        trial_level_file,
        len(trial_level_df),
    )

    return AnzctrCancerTypePipelineOutputs(
        health_condition_mapping_file=health_condition_mapping_file,
        primary_vs_health_condition_file=primary_vs_health_condition_file,
        row_level_cancer_type_file=row_level_file,
        trial_level_cancer_type_file=trial_level_file,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the ANZCTR cancer-type determination pipeline. Internally, "
            "ANZCTR ACTRN is normalized to trial_id and HEALTH CONDITION is "
            "mapped to conditions_original so the shared cancer-type logic can "
            "match the CTGov workflow."
        )
    )
    parser.add_argument("--repo_root", type=Path, default=Path.cwd())
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
        "--input_csv",
        type=Path,
        default=None,
        help=(
            "ANZCTR selected trial CSV. Defaults to "
            "data/trial_inputs/anzctr/extracted_trials/"
            "anzctr_field_extractions.csv."
        ),
    )
    parser.add_argument(
        "--curated_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing curated ACTRN*.py files, or a single ACTRN*.py file. "
            "Defaults to data/trial_inputs/anzctr/eligibility_curations."
        ),
    )
    parser.add_argument(
        "--resources_dir",
        type=Path,
        default=None,
        help="Shared eligibility resources directory. Defaults to data/eligibility_path/resources.",
    )
    parser.add_argument(
        "--cancer_type_resource_dir",
        type=Path,
        default=None,
        help="Cancer-type resource directory. Defaults to resources_dir/cancer_type.",
    )
    parser.add_argument(
        "--oncotree_csv",
        type=Path,
        default=None,
        help="OncoTree CSV. Defaults to resources_dir/oncotree.csv.",
    )
    parser.add_argument(
        "--manual_overwrite_file",
        type=Path,
        default=None,
        help=(
            "Optional ANZCTR manual overwrite file. If supplied, it must include "
            "manual_overwrite plus either primary_vs_health_condition_row, or "
            "trial_id/ACTRN with primary_tumor_type, primary_tumor_location, "
            "and conditions_original."
        ),
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help=(
            "Output directory for processed ANZCTR cancer-type files. "
            "Defaults to eligibility_data_dir/cancer_type."
        ),
    )
    parser.add_argument(
        "--output_format",
        default=DEFAULT_OUTPUT_FORMAT,
        choices=["tsv", "csv", "xlsx", "xls"],
    )
    parser.add_argument("--fail_on_error", action="store_true")
    parser.add_argument("--log_level", default="INFO")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s [%(name)s] - %(message)s",
    )

    inputs = discover_pipeline_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        input_csv=args.input_csv,
        curated_dir=args.curated_dir,
        resources_dir=args.resources_dir,
        cancer_type_resource_dir=args.cancer_type_resource_dir,
        oncotree_csv=args.oncotree_csv,
        manual_overwrite_file=args.manual_overwrite_file,
        output_dir=args.output_dir,
        output_format=args.output_format,
        fail_on_error=args.fail_on_error,
    )
    outputs = run_cancer_type_pipeline(inputs)

    logger.info("ANZCTR cancer-type pipeline complete.")
    logger.info("health_condition_mapping_file:     %s", outputs.health_condition_mapping_file)
    logger.info("primary_vs_health_condition_file:  %s", outputs.primary_vs_health_condition_file)
    logger.info("row_level_cancer_type_file:        %s", outputs.row_level_cancer_type_file)
    logger.info("trial_level_cancer_type_file:      %s", outputs.trial_level_cancer_type_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
