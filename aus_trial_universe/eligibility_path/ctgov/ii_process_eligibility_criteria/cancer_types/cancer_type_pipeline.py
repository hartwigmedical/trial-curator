from __future__ import annotations

import argparse
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pandas as pd

from aus_trial_universe.eligibility_path.shared.cancer_types.conditions.conditions_mapping import (
    process as map_conditions_to_oncotree,
)
from aus_trial_universe.eligibility_path.shared.cancer_types.final_determination.primary_vs_conditions import (
    run_combined_workflow as build_primary_vs_conditions,
)
from aus_trial_universe.eligibility_path.shared.cancer_types.final_determination.criterion_level_determination import (
    load_oncotree_hierarchy as load_criterion_level_oncotree_hierarchy,
    run_combined_workflow as determine_criterion_level_cancer_type,
)
from aus_trial_universe.eligibility_path.shared.cancer_types.final_determination.trial_level_determination import (
    collapse_to_unique_trials,
    load_oncotree_hierarchy as load_trial_level_oncotree_hierarchy,
)
from aus_trial_universe.eligibility_path.shared.utils.curated_files import (
    has_curated_py_files,
)
from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    SUPPORTED_CSV_SUFFIXES,
    SUPPORTED_TABULAR_SUFFIXES,
    find_best_file as _find_best_file,
    output_path as _output_path,
    read_tabular_file as _read_tabular_file,
    resolve_path as _resolve_path,
    write_tabular_file as _write_tabular_file,
)

logger = logging.getLogger(__name__)

DEFAULT_ELIGIBILITY_DATA_DIR = Path("data/eligibility_path/exports/intermediates/ctgov")
DEFAULT_SHARED_RESOURCES_DIR = Path("data/eligibility_path/resources")
DEFAULT_TRIALS_DIR = Path("data/trial_inputs/ctgov/extracted_trials")
DEFAULT_PROCESSED_SUBDIR = Path("cancer_type")

DEFAULT_CURATED_DIR = Path("data/trial_inputs/ctgov/eligibility_curations")
DEFAULT_CANCER_TYPE_RESOURCE_SUBDIR = Path("cancer_type")
DEFAULT_ONCOTREE_CSV = Path("oncotree/oncotree.csv")

CONDITIONS_MAPPING_STEM = "01_conditions_mapping"
PRIMARY_VS_CONDITIONS_STEM = "02_primary_vs_conditions"
ROW_LEVEL_STEM = "03_row_level_cancer_type"
TRIAL_LEVEL_STEM = "04_trial_level_cancer_type"

DEFAULT_OUTPUT_FORMAT = "tsv"


@dataclass(frozen=True)
class CancerTypePipelineInputs:
    ctgov_extractions_csv: Path
    curated_dir: Path
    cancer_type_resource_dir: Path
    oncotree_csv: Path
    manual_overwrite_file: Path
    conditions_output_dir: Path
    output_dir: Path
    output_format: str = DEFAULT_OUTPUT_FORMAT
    fail_on_error: bool = False


@dataclass(frozen=True)
class CancerTypePipelineOutputs:
    conditions_mapping_file: Path
    primary_vs_conditions_file: Path
    row_level_cancer_type_file: Path
    trial_level_cancer_type_file: Path


def discover_pipeline_inputs(
    *,
    repo_root: Path,
    eligibility_data_dir: Path,
    trials_dir: Optional[Path],
    resources_dir: Optional[Path],
    processed_dir: Optional[Path],
    curated_dir: Optional[Path],
    ctgov_extractions_csv: Optional[Path],
    cancer_type_resource_dir: Optional[Path],
    oncotree_csv: Optional[Path],
    manual_overwrite_file: Optional[Path],
    conditions_output_dir: Optional[Path],
    output_dir: Optional[Path],
    output_format: str,
    fail_on_error: bool,
) -> CancerTypePipelineInputs:
    repo_root = repo_root.resolve()

    resolved_eligibility_data_dir = _resolve_path(eligibility_data_dir, repo_root)

    resolved_trials_dir = (
        _resolve_path(trials_dir, repo_root)
        if trials_dir is not None
        else _resolve_path(DEFAULT_TRIALS_DIR, repo_root)
    )

    resolved_resources_dir = (
        _resolve_path(resources_dir, repo_root)
        if resources_dir is not None
        else _resolve_path(DEFAULT_SHARED_RESOURCES_DIR, repo_root)
    )

    resolved_processed_dir = (
        _resolve_path(processed_dir, repo_root)
        if processed_dir is not None
        else resolved_eligibility_data_dir / DEFAULT_PROCESSED_SUBDIR
    )

    resolved_curated_dir = (
        _resolve_path(curated_dir, repo_root)
        if curated_dir is not None
        else _resolve_path(DEFAULT_CURATED_DIR, repo_root)
    )

    resolved_cancer_type_resource_dir = (
        _resolve_path(cancer_type_resource_dir, repo_root)
        if cancer_type_resource_dir is not None
        else resolved_resources_dir / DEFAULT_CANCER_TYPE_RESOURCE_SUBDIR
    )

    resolved_conditions_output_dir = (
        _resolve_path(conditions_output_dir, repo_root)
        if conditions_output_dir is not None
        else resolved_processed_dir
    )

    resolved_output_dir = (
        _resolve_path(output_dir, repo_root)
        if output_dir is not None
        else resolved_processed_dir
    )

    resolved_ctgov_extractions_csv = (
        _resolve_path(ctgov_extractions_csv, repo_root)
        if ctgov_extractions_csv is not None
        else _find_best_file(
            resolved_trials_dir,
            token_groups=[["ctgov"], ["field", "extraction", "extractions"]],
            allowed_suffixes=SUPPORTED_CSV_SUFFIXES,
            recursive=False,
            label="CTGov field extractions CSV",
        )
    )

    resolved_oncotree_csv = (
        _resolve_path(oncotree_csv, repo_root)
        if oncotree_csv is not None
        else (
            resolved_resources_dir / DEFAULT_ONCOTREE_CSV
            if (resolved_resources_dir / DEFAULT_ONCOTREE_CSV).exists()
            else _find_best_file(
                resolved_resources_dir,
                token_groups=[["oncotree", "onco_tree"]],
                allowed_suffixes=SUPPORTED_CSV_SUFFIXES,
                recursive=False,
                label="OncoTree CSV",
            )
        )
    )

    resolved_manual_overwrite_file = (
        _resolve_path(manual_overwrite_file, repo_root)
        if manual_overwrite_file is not None
        else _find_best_file(
            resolved_cancer_type_resource_dir,
            token_groups=[["manual"], ["overwrite"]],
            allowed_suffixes=SUPPORTED_TABULAR_SUFFIXES,
            recursive=False,
            label="manual overwrite resource",
        )
    )

    _find_best_file(
        resolved_cancer_type_resource_dir,
        token_groups=[["condition", "conditions"]],
        allowed_suffixes=SUPPORTED_TABULAR_SUFFIXES,
        recursive=False,
        label="conditions curation resource",
    )

    _find_best_file(
        resolved_cancer_type_resource_dir,
        token_groups=[
            ["primarytumour", "primary_tumour", "primarytumor", "primary_tumor"],
        ],
        allowed_suffixes=SUPPORTED_TABULAR_SUFFIXES,
        recursive=False,
        label="primary tumour curation resource",
    )

    inputs = CancerTypePipelineInputs(
        ctgov_extractions_csv=resolved_ctgov_extractions_csv,
        curated_dir=resolved_curated_dir,
        cancer_type_resource_dir=resolved_cancer_type_resource_dir,
        oncotree_csv=resolved_oncotree_csv,
        manual_overwrite_file=resolved_manual_overwrite_file,
        conditions_output_dir=resolved_conditions_output_dir,
        output_dir=resolved_output_dir,
        output_format=output_format,
        fail_on_error=fail_on_error,
    )

    validate_pipeline_inputs(inputs)
    return inputs


def validate_pipeline_inputs(inputs: CancerTypePipelineInputs) -> None:
    required_files = [
        ("CTGov field extractions CSV", inputs.ctgov_extractions_csv),
        ("OncoTree CSV", inputs.oncotree_csv),
        ("manual overwrite resource", inputs.manual_overwrite_file),
    ]

    for label, path in required_files:
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"{label} is not a file: {path}")

    if not inputs.curated_dir.exists():
        raise FileNotFoundError(
            f"Curated rules directory/file does not exist: {inputs.curated_dir}"
        )

    if not has_curated_py_files(inputs.curated_dir, trial_id_prefix="NCT"):
        raise FileNotFoundError(
            f"Curated rules path does not contain NCT*.py files directly: {inputs.curated_dir}"
        )

    if not inputs.cancer_type_resource_dir.exists():
        raise FileNotFoundError(
            f"Cancer-type resource directory does not exist: {inputs.cancer_type_resource_dir}"
        )

    if not inputs.cancer_type_resource_dir.is_dir():
        raise ValueError(
            f"Cancer-type resource path is not a directory: {inputs.cancer_type_resource_dir}"
        )


def run_cancer_type_pipeline(inputs: CancerTypePipelineInputs) -> CancerTypePipelineOutputs:
    inputs.conditions_output_dir.mkdir(parents=True, exist_ok=True)
    inputs.output_dir.mkdir(parents=True, exist_ok=True)

    conditions_mapping_file = _output_path(
        inputs.conditions_output_dir,
        CONDITIONS_MAPPING_STEM,
        inputs.output_format,
    )
    primary_vs_conditions_file = _output_path(
        inputs.output_dir,
        PRIMARY_VS_CONDITIONS_STEM,
        inputs.output_format,
    )
    row_level_file = _output_path(
        inputs.output_dir,
        ROW_LEVEL_STEM,
        inputs.output_format,
    )
    trial_level_file = _output_path(
        inputs.output_dir,
        TRIAL_LEVEL_STEM,
        inputs.output_format,
    )

    logger.info("Cancer-type pipeline inputs:")
    logger.info("  ctgov_extractions_csv:      %s", inputs.ctgov_extractions_csv)
    logger.info("  curated_dir:                %s", inputs.curated_dir)
    logger.info("  cancer_type_resource_dir:   %s", inputs.cancer_type_resource_dir)
    logger.info("  oncotree_csv:               %s", inputs.oncotree_csv)
    logger.info("  manual_overwrite_file:      %s", inputs.manual_overwrite_file)
    logger.info("  conditions_output_dir:      %s", inputs.conditions_output_dir)
    logger.info("  output_dir:                 %s", inputs.output_dir)
    logger.info("  output_format:              %s", inputs.output_format)

    with tempfile.TemporaryDirectory(prefix="cancer_type_pipeline_") as tmp:
        tmp_dir = Path(tmp)
        tmp_conditions_csv = tmp_dir / "01_conditions_mapping.csv"

        logger.info("[1/4] Mapping CTGov conditions to OncoTree")
        map_conditions_to_oncotree(
            input_file=inputs.ctgov_extractions_csv,
            mapping_dir=inputs.cancer_type_resource_dir,
            output_csv=tmp_conditions_csv,
            trial_id_column="nctId",
            conditions_column="conditions",
            parser="python_list",
        )

        conditions_df = _read_tabular_file(tmp_conditions_csv)
        _write_tabular_file(conditions_df, conditions_mapping_file)
        logger.info(
            "Wrote conditions mapping: %s rows=%d",
            conditions_mapping_file,
            len(conditions_df),
        )

        logger.info("[2/4] Building primary-vs-conditions occurrence table")
        primary_vs_conditions_df = build_primary_vs_conditions(
            curated_dir=inputs.curated_dir,
            conditions_csv=tmp_conditions_csv,
            mapping_dir=inputs.cancer_type_resource_dir,
            oncotree_csv=inputs.oncotree_csv,
            manual_overwrite_file=inputs.manual_overwrite_file,
            fail_on_error=inputs.fail_on_error,
        )
        _write_tabular_file(primary_vs_conditions_df, primary_vs_conditions_file)
        logger.info(
            "Wrote primary-vs-conditions table: %s rows=%d",
            primary_vs_conditions_file,
            len(primary_vs_conditions_df),
        )

    logger.info("[3/4] Determining criterion-level cancer type")
    criterion_level_hierarchy = load_criterion_level_oncotree_hierarchy(inputs.oncotree_csv)
    row_level_df = determine_criterion_level_cancer_type(
        primary_vs_conditions_df,
        criterion_level_hierarchy,
    )
    _write_tabular_file(row_level_df, row_level_file)
    logger.info(
        "Wrote criterion-level cancer type table: %s rows=%d",
        row_level_file,
        len(row_level_df),
    )

    logger.info("[4/4] Collapsing to trial-level cancer type")
    trial_level_hierarchy = load_trial_level_oncotree_hierarchy(inputs.oncotree_csv)
    trial_level_df = collapse_to_unique_trials(
        row_level_df,
        trial_level_hierarchy,
    )
    _write_tabular_file(trial_level_df, trial_level_file)
    logger.info(
        "Wrote trial-level cancer type table: %s rows=%d",
        trial_level_file,
        len(trial_level_df),
    )

    return CancerTypePipelineOutputs(
        conditions_mapping_file=conditions_mapping_file,
        primary_vs_conditions_file=primary_vs_conditions_file,
        row_level_cancer_type_file=row_level_file,
        trial_level_cancer_type_file=trial_level_file,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full CTGov cancer-type determination pipeline: "
            "conditions mapping, primary-vs-conditions comparison, row-level "
            "determination, and trial-level collapse."
        )
    )

    parser.add_argument(
        "--repo_root",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--eligibility_data_dir",
        type=Path,
        default=DEFAULT_ELIGIBILITY_DATA_DIR,
        help=(
            "CTGov eligibility intermediate export root. Defaults to "
            "data/eligibility_path/exports/intermediates/ctgov."
        ),
    )
    parser.add_argument(
        "--trials_dir",
        type=Path,
        default=None,
        help=(
            "Optional trials directory. Defaults to "
            "data/trial_inputs/ctgov/extracted_trials. Used to discover "
            "ctgov_field_extractions.csv."
        ),
    )
    parser.add_argument(
        "--resources_dir",
        type=Path,
        default=None,
        help=(
            "Optional shared resources directory. Defaults to data/eligibility_path/resources. "
            "Used to discover oncotree.csv if --oncotree_csv is omitted."
        ),
    )
    parser.add_argument(
        "--processed_dir",
        type=Path,
        default=None,
        help=(
            "Optional processed cancer-type output directory. "
            "Defaults to eligibility_data_dir/cancer_type."
        ),
    )
    parser.add_argument(
        "--curated_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing curated NCT*.py files, or a single curated NCT*.py file. "
            "Defaults to data/trial_inputs/ctgov/eligibility_curations."
        ),
    )
    parser.add_argument(
        "--ctgov_extractions_csv",
        type=Path,
        default=None,
        help=(
            "Optional explicit CTGov field extractions CSV. "
            "If omitted, the pipeline discovers a matching CSV in trials_dir."
        ),
    )
    parser.add_argument(
        "--cancer_type_resource_dir",
        type=Path,
        default=None,
        help=(
            "Optional explicit cancer-type resource directory. "
            "Defaults to resources_dir/cancer_type."
        ),
    )
    parser.add_argument(
        "--oncotree_csv",
        type=Path,
        default=None,
        help=(
            "Optional explicit OncoTree CSV. "
            "Defaults to resources_dir/oncotree.csv, or discovery in resources_dir."
        ),
    )
    parser.add_argument(
        "--manual_overwrite_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit manual overwrite file. "
            "If omitted, the pipeline discovers a matching CSV/TSV/XLSX/XLS file "
            "in the cancer-type resource directory."
        ),
    )
    parser.add_argument(
        "--conditions_output_dir",
        type=Path,
        default=None,
        help=(
            "Optional output directory for the trial-level conditions mapping file. "
            "Defaults to processed_dir."
        ),
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help=(
            "Optional output directory for primary-vs-conditions, criterion-level, "
            "and trial-level files. Defaults to processed_dir."
        ),
    )
    parser.add_argument(
        "--output_format",
        default=DEFAULT_OUTPUT_FORMAT,
        choices=["tsv", "csv", "xlsx", "xls"],
        help="Output format for persisted processed cancer-type tables. Defaults to tsv.",
    )
    parser.add_argument(
        "--fail_on_error",
        action="store_true",
        help="Raise immediately on the first curated rule file that fails to load or parse.",
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

    inputs = discover_pipeline_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        trials_dir=args.trials_dir,
        resources_dir=args.resources_dir,
        processed_dir=args.processed_dir,
        curated_dir=args.curated_dir,
        ctgov_extractions_csv=args.ctgov_extractions_csv,
        cancer_type_resource_dir=args.cancer_type_resource_dir,
        oncotree_csv=args.oncotree_csv,
        manual_overwrite_file=args.manual_overwrite_file,
        conditions_output_dir=args.conditions_output_dir,
        output_dir=args.output_dir,
        output_format=args.output_format,
        fail_on_error=args.fail_on_error,
    )

    outputs = run_cancer_type_pipeline(inputs)

    logger.info("Cancer-type pipeline complete.")
    logger.info("conditions_mapping_file:       %s", outputs.conditions_mapping_file)
    logger.info("primary_vs_conditions_file:    %s", outputs.primary_vs_conditions_file)
    logger.info("row_level_cancer_type_file:    %s", outputs.row_level_cancer_type_file)
    logger.info("trial_level_cancer_type_file:  %s", outputs.trial_level_cancer_type_file)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
