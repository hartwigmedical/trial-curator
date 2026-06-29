from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import pandas as pd

from aus_trial_universe.eligibility_path.shared.trial_resource.combined_trial_resource_export import (
    DEFAULT_ELIGIBILITY_DATA_DIR,
    discover_pipeline_inputs,
    load_pottr_trial_ids_best_effort,
    run_combined_trial_resource_export,
)
from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import latest_version_dir
from aus_trial_universe.eligibility_path.shared.curated_expiry import move_expired_curations
from aus_trial_universe.eligibility_path.shared.cohorts import (
    normalize_anzctr_trial_id,
    normalize_nct_id,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_EXPORT_SUFFIX_FORMAT = "%d%m%Y"
DEFAULT_CTGOV_INPUT_ROOT = Path("data/trial_inputs/ctgov/input_trials")
DEFAULT_CTGOV_STATE_DIR = Path("data/trial_inputs/ctgov/download_state")
DEFAULT_ANZCTR_INPUT_ROOT = Path("data/trial_inputs/anzctr/input_trials")
DEFAULT_CTGOV_EXTRACTED_DIR = Path("data/trial_inputs/ctgov/extracted_trials")
DEFAULT_ANZCTR_EXTRACTED_CSV = Path(
    "data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv"
)
DEFAULT_CTGOV_CURATED_DIR = Path("data/trial_inputs/ctgov/eligibility_curations")
DEFAULT_ANZCTR_CURATED_DIR = Path("data/trial_inputs/anzctr/eligibility_curations")
DEFAULT_CTGOV_INTERMEDIATE_DIR = Path("data/eligibility_path/exports/intermediates/ctgov")
DEFAULT_ANZCTR_INTERMEDIATE_DIR = Path("data/eligibility_path/exports/intermediates/anzctr")
DEFAULT_RXNORM_RRF_DIR = Path("data/drug_utility_path/drug_ontology/raw_inputs/RxNorm")

CTGOV_INITIAL_FILENAME = "01_initial_search_ctgov_input.json"
CTGOV_POTTR_APPEND_FILENAME = "02_pottr_append_ctgov_input.json"
CTGOV_MERGED_FILENAME = "03_merged_ctgov_input.json"
ANZCTR_INITIAL_FILENAME = "01_initial_search_anzctr_input.xlsx"
ANZCTR_POTTR_APPEND_FILENAME = "02_pottr_append_anzctr_input.xlsx"
ANZCTR_MERGED_FILENAME = "03_merged_anzctr_input.xlsx"

# Skip curation expiry when more than this fraction of a registry's active
# curations would expire in one run — almost always a partial/failed download
# rather than genuine churn. Self-heals on the next complete download.
EXPIRY_SAFETY_FRACTION = 0.5

CommandRunner = Callable[[Sequence[str]], None]


@dataclass(frozen=True)
class RecursiveWorkflowConfig:
    export_date: str
    repo_root: Path = Path.cwd()
    python_bin: str = sys.executable
    eligibility_data_dir: Path = DEFAULT_ELIGIBILITY_DATA_DIR
    ctgov_input_root: Path = DEFAULT_CTGOV_INPUT_ROOT
    ctgov_state_dir: Path = DEFAULT_CTGOV_STATE_DIR
    anzctr_input_root: Path = DEFAULT_ANZCTR_INPUT_ROOT
    ctgov_extracted_dir: Path = DEFAULT_CTGOV_EXTRACTED_DIR
    anzctr_extracted_csv: Path = DEFAULT_ANZCTR_EXTRACTED_CSV
    ctgov_curated_dir: Path = DEFAULT_CTGOV_CURATED_DIR
    anzctr_curated_dir: Path = DEFAULT_ANZCTR_CURATED_DIR
    ctgov_intermediate_dir: Path = DEFAULT_CTGOV_INTERMEDIATE_DIR
    anzctr_intermediate_dir: Path = DEFAULT_ANZCTR_INTERMEDIATE_DIR
    rxnorm_rrf_dir: Path = DEFAULT_RXNORM_RRF_DIR
    output_format: str = "tsv"
    log_level: str = "INFO"
    max_iterations: int = 4
    use_latest_input_version: bool = False
    llm_review: bool = False
    llm_workers: int = 10
    llm_max_retries: int = 10
    llm_retry_initial_delay: float = 2
    llm_retry_max_delay: float = 60
    llm_limit: int | None = None
    llm_model: str | None = None
    anzctr_timeout_ms: int = 120_000
    anzctr_search_retries: int = 3


def run_subprocess(command: Sequence[str]) -> None:
    subprocess.run(list(command), check=True)


def version_dir(root: Path, export_date: str) -> Path:
    return root / f"version_{export_date}"


def input_version_dir(root: Path, config: RecursiveWorkflowConfig) -> Path:
    if config.use_latest_input_version:
        return latest_version_dir(root)
    return version_dir(root, config.export_date)


def existing_paths(paths: Sequence[Path]) -> list[Path]:
    return [path for path in paths if path.exists()]


def ctgov_acquisition_files(config: RecursiveWorkflowConfig) -> list[Path]:
    root = input_version_dir(config.ctgov_input_root, config)
    # Downstream reads the merged input (01 ∪ 02); fall back to the staged
    # 01/02 files for older version directories without a merged file.
    merged = existing_paths([root / CTGOV_MERGED_FILENAME])
    if merged:
        return merged
    return existing_paths(
        [
            root / CTGOV_INITIAL_FILENAME,
            root / CTGOV_POTTR_APPEND_FILENAME,
        ]
    )


def anzctr_acquisition_files(config: RecursiveWorkflowConfig) -> list[Path]:
    root = input_version_dir(config.anzctr_input_root, config)
    merged = existing_paths([root / ANZCTR_MERGED_FILENAME])
    if merged:
        return merged
    return existing_paths(
        [
            root / ANZCTR_INITIAL_FILENAME,
            root / ANZCTR_POTTR_APPEND_FILENAME,
        ]
    )


def py_module(config: RecursiveWorkflowConfig, module: str, *args: object) -> list[str]:
    return [config.python_bin, "-m", module, *[str(arg) for arg in args]]


def split_missing_trial_ids(missing: pd.DataFrame) -> dict[str, list[str]]:
    if missing.empty:
        return {"ctgov": [], "anzctr": []}
    required = {"trial_id", "registry"}
    missing_columns = required - set(missing.columns)
    if missing_columns:
        raise ValueError(f"Missing POTTR frame lacks columns: {sorted(missing_columns)}")

    out = {"ctgov": [], "anzctr": []}
    for row in missing[["trial_id", "registry"]].itertuples(index=False):
        trial_id = str(row.trial_id).strip().upper()
        registry = str(row.registry).strip().casefold()
        if registry in out and trial_id:
            out[registry].append(trial_id)
    return {registry: sorted(set(values)) for registry, values in out.items()}


def write_missing_ids_tsv(path: Path, trial_ids_by_registry: dict[str, set[str]]) -> Path:
    rows = [
        {"trial_id": trial_id, "registry": registry}
        for registry in ("ctgov", "anzctr")
        for trial_id in sorted(trial_ids_by_registry.get(registry, set()))
    ]
    pd.DataFrame(rows, columns=["trial_id", "registry"]).to_csv(
        path,
        sep="\t",
        index=False,
    )
    return path


def run_initial_downloads(
    config: RecursiveWorkflowConfig,
    run_command: CommandRunner,
) -> None:
    anzctr_download_args: list[object] = [
        "--initial_search",
        "--export_date",
        config.export_date,
        "--timeout_ms",
        config.anzctr_timeout_ms,
        "--search_retries",
        config.anzctr_search_retries,
        "--log_level",
        config.log_level,
    ]
    run_command(
        py_module(
            config,
            "aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.i_download_trials",
            *anzctr_download_args,
        )
    )
    ctgov_version_dir = version_dir(config.ctgov_input_root, config.export_date)
    run_command(
        py_module(
            config,
            "aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.i_api_download",
            "--all",
            "--output_dir",
            ctgov_version_dir,
            "--state_dir",
            config.ctgov_state_dir,
            "--log_level",
            config.log_level,
        )
    )


def _ctgov_current_trial_ids(merged_json: Path) -> set[str]:
    data = json.loads(merged_json.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for study in data:
        nct = (
            study.get("protocolSection", {})
            .get("identificationModule", {})
            .get("nctId")
        )
        if nct:
            ids.add(str(nct))
    return ids


def _anzctr_current_trial_ids(merged_xlsx: Path) -> set[str]:
    frame = pd.read_excel(merged_xlsx, sheet_name="TRIAL", dtype=str)
    column = frame["ACTRN"] if "ACTRN" in frame.columns else pd.Series(dtype=str)
    return {str(value) for value in column.tolist() if str(value).strip()}


def expire_stale_curations(config: RecursiveWorkflowConfig) -> None:
    """Move curated `.py` files whose trial is no longer in the latest download
    (the merged ``03`` input) into an ``expired_trials/`` subfolder.

    POTTR-listed trials are never expired.  Each registry is skipped when its
    merged input or curated directory is absent (e.g. before a real download).
    """
    registries = (
        (
            "ctgov",
            input_version_dir(config.ctgov_input_root, config) / CTGOV_MERGED_FILENAME,
            config.ctgov_curated_dir,
            "NCT",
            normalize_nct_id,
            _ctgov_current_trial_ids,
        ),
        (
            "anzctr",
            input_version_dir(config.anzctr_input_root, config) / ANZCTR_MERGED_FILENAME,
            config.anzctr_curated_dir,
            "ACTRN",
            normalize_anzctr_trial_id,
            _anzctr_current_trial_ids,
        ),
    )
    for registry, merged_path, curated_dir, prefix, normalize, current_ids_fn in registries:
        if not merged_path.exists():
            LOGGER.info(
                "Skipping %s curation expiry: no merged input at %s", registry, merged_path
            )
            continue
        if not curated_dir.exists():
            LOGGER.info(
                "Skipping %s curation expiry: no curated dir at %s", registry, curated_dir
            )
            continue

        current_ids = current_ids_fn(merged_path)
        pottr_ids = load_pottr_trial_ids_best_effort(registry=registry)
        if not pottr_ids:
            LOGGER.warning(
                "%s POTTR exemption list is empty (source unreachable?); expiry "
                "proceeds without POTTR exemption.",
                registry,
            )
        result = move_expired_curations(
            curated_dir=curated_dir,
            current_trial_ids=current_ids,
            pottr_exempt_ids=pottr_ids,
            normalize_trial_id=normalize,
            trial_id_prefix=prefix,
            max_expiry_fraction=EXPIRY_SAFETY_FRACTION,
        )
        if result.guarded:
            LOGGER.warning(
                "%s curation expiry guarded: latest download looked incomplete; "
                "no curations expired this run.",
                registry,
            )
        LOGGER.info(
            "%s curation expiry: moved %d stale curation(s) to %s; "
            "restored %d re-appeared; retained %d POTTR-exempt.",
            registry,
            len(result.moved),
            result.expired_dir,
            len(result.restored),
            len(result.retained_pottr),
        )


def run_registry_processing_commands(
    config: RecursiveWorkflowConfig,
    run_command: CommandRunner,
) -> None:
    ctgov_jsons = ctgov_acquisition_files(config)
    anzctr_xlsxs = anzctr_acquisition_files(config)
    if not ctgov_jsons:
        raise FileNotFoundError("No CTGov acquisition JSON files found")
    if not anzctr_xlsxs:
        raise FileNotFoundError("No ANZCTR acquisition workbooks found")

    run_command(
        py_module(
            config,
            "aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.ii_extract_fields",
            "--ctgov_filepath",
            *ctgov_jsons,
            "--output_dir",
            config.ctgov_extracted_dir,
            "--log_level",
            config.log_level,
        )
    )
    run_command(
        py_module(
            config,
            "aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.iii_pydantic_curator_batch_run",
            "--input_json",
            *ctgov_jsons,
            "--selected_trial_csv",
            config.ctgov_extracted_dir / "ctgov_field_extractions.csv",
            "--output_dir",
            config.ctgov_curated_dir,
            "--log_level",
            config.log_level,
        )
    )
    anzctr_drug_args: list[object] = [
        "--refresh_input_csv",
        "--input_xlsx",
        *anzctr_xlsxs,
        "--output_csv",
        config.anzctr_extracted_csv,
        "--rxnorm_rrf_dir",
        config.rxnorm_rrf_dir,
        "--log_level",
        config.log_level,
    ]
    if config.llm_review:
        anzctr_drug_args.extend(
            [
                "--llm_review",
                "--llm_workers",
                config.llm_workers,
                "--llm_max_retries",
                config.llm_max_retries,
                "--llm_retry_initial_delay",
                config.llm_retry_initial_delay,
                "--llm_retry_max_delay",
                config.llm_retry_max_delay,
            ]
        )
        if config.llm_limit is not None:
            anzctr_drug_args.extend(["--llm_limit", config.llm_limit])
        if config.llm_model:
            anzctr_drug_args.extend(["--llm_model", config.llm_model])

    run_command(
        py_module(
            config,
            "aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs",
            *anzctr_drug_args,
        )
    )
    run_command(
        py_module(
            config,
            "aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iv_pydantic_curator_batch_run",
            "--input_csv",
            config.anzctr_extracted_csv,
            "--output_dir",
            config.anzctr_curated_dir,
            "--log_level",
            config.log_level,
        )
    )

    for registry, intermediate_dir in (
        ("ctgov", config.ctgov_intermediate_dir),
        ("anzctr", config.anzctr_intermediate_dir),
    ):
        for module_suffix in (
            "cancer_types.cancer_type_pipeline",
            "cancer_types.cohort_level_cancer_type",
            "gene_alterations.gene_alteration_pipeline",
            "gene_alterations.cohort_level_gene_alteration",
            "molecular_signature.molecular_signature_pipeline",
            "molecular_signature.cohort_level_molecular_signature",
        ):
            run_command(
                py_module(
                    config,
                    f"aus_trial_universe.eligibility_path.{registry}.ii_process_eligibility_criteria.{module_suffix}",
                    "--eligibility_data_dir",
                    intermediate_dir,
                    "--output_format",
                    config.output_format,
                    "--log_level",
                    config.log_level,
                )
            )

        for module_suffix in (
            "trial_resource.trial_level_resource_pipeline",
            "trial_resource.cohort_level_resource_pipeline",
        ):
            run_command(
                py_module(
                    config,
                    f"aus_trial_universe.eligibility_path.{registry}.ii_process_eligibility_criteria.{module_suffix}",
                    "--export_date",
                    config.export_date,
                    "--log_level",
                    config.log_level,
                )
            )


def run_combined_export_and_get_missing(
    config: RecursiveWorkflowConfig,
) -> pd.DataFrame:
    inputs = discover_pipeline_inputs(
        repo_root=config.repo_root,
        eligibility_data_dir=config.eligibility_data_dir,
        ctgov_trial_resource_file=None,
        anzctr_trial_resource_file=None,
        ctgov_cohort_resource_file=None,
        anzctr_cohort_resource_file=None,
        output_file=None,
        trial_output_file=None,
        cohort_output_file=None,
        export_date=config.export_date,
    )
    return run_combined_trial_resource_export(inputs).missing_pottr_trials


def run_pottr_append_downloads(
    config: RecursiveWorkflowConfig,
    run_command: CommandRunner,
    pottr_append_ids: dict[str, set[str]],
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir_name:
        ids_file = write_missing_ids_tsv(
            Path(temp_dir_name) / "pottr_append_trial_ids.tsv",
            pottr_append_ids,
        )
        if pottr_append_ids.get("ctgov"):
            run_command(
                py_module(
                    config,
                    "aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.i_api_download",
                    "--trial_ids",
                    ids_file,
                    "--output_dir",
                    input_version_dir(config.ctgov_input_root, config),
                    "--state_dir",
                    config.ctgov_state_dir,
                    "--log_level",
                    config.log_level,
                )
            )
        if pottr_append_ids.get("anzctr"):
            anzctr_append_args: list[object] = [
                "--trial_ids",
                ids_file,
                "--output_dir",
                input_version_dir(config.anzctr_input_root, config),
                "--base_input_xlsx",
                input_version_dir(config.anzctr_input_root, config)
                / ANZCTR_INITIAL_FILENAME,
                "--timeout_ms",
                config.anzctr_timeout_ms,
                "--log_level",
                config.log_level,
            ]
            if not config.use_latest_input_version:
                # Reuse the whole-registry export the fresh initial download
                # cached under version_<export_date> instead of re-fetching ~90 MB.
                anzctr_append_args += ["--export_date", config.export_date]
            run_command(
                py_module(
                    config,
                    "aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.i_download_trials",
                    *anzctr_append_args,
                )
            )


def run_recursive_workflow(
    config: RecursiveWorkflowConfig,
    *,
    run_command: CommandRunner = run_subprocess,
    initial_downloads: bool = True,
) -> pd.DataFrame:
    if initial_downloads:
        run_initial_downloads(config, run_command)
        # After a fresh download, retire curations whose trial dropped out of it.
        expire_stale_curations(config)

    pottr_append_ids: dict[str, set[str]] = {"ctgov": set(), "anzctr": set()}
    final_missing = pd.DataFrame()
    for iteration in range(1, config.max_iterations + 1):
        LOGGER.info("Starting eligibility workflow iteration %d", iteration)
        run_registry_processing_commands(config, run_command)
        final_missing = run_combined_export_and_get_missing(config)
        missing_by_registry = split_missing_trial_ids(final_missing)
        new_by_registry = {
            registry: set(ids) - pottr_append_ids[registry]
            for registry, ids in missing_by_registry.items()
        }
        new_total = sum(len(ids) for ids in new_by_registry.values())
        LOGGER.info(
            "POTTR missing after iteration %d: total=%d new=%d ctgov=%d anzctr=%d",
            iteration,
            len(final_missing),
            new_total,
            len(missing_by_registry["ctgov"]),
            len(missing_by_registry["anzctr"]),
        )
        if final_missing.empty:
            return final_missing
        if new_total == 0:
            break

        for registry, ids in new_by_registry.items():
            pottr_append_ids[registry].update(ids)
        run_pottr_append_downloads(config, run_command, pottr_append_ids)

    if not final_missing.empty:
        missing_by_registry = split_missing_trial_ids(final_missing)
        LOGGER.warning(
            "Recursive eligibility workflow converged with %d POTTR trial(s) that could "
            "not be resolved by downloading (ctgov=%d, anzctr=%d). These POTTR-listed "
            "trials are downloaded and re-processed each iteration but remain excluded "
            "from the eligibility output by the pipeline's own filters (e.g. non-drug "
            "intervention codes such as 'Treatment: Other', or no determined cancer "
            "type), so further downloads cannot resolve them. The full list is logged by "
            "the combined export above; relaxing those filters for POTTR trials is a "
            "separate decision.",
            len(final_missing),
            len(missing_by_registry["ctgov"]),
            len(missing_by_registry["anzctr"]),
        )
    return final_missing


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run recursive CTGov/ANZCTR eligibility workflow through POTTR convergence."
    )
    parser.add_argument("--export_date", required=True, help="Date suffix in ddmmyyyy format.")
    parser.add_argument("--repo_root", type=Path, default=Path.cwd())
    parser.add_argument("--python_bin", default=sys.executable)
    parser.add_argument("--max_iterations", type=int, default=4)
    parser.add_argument("--skip_initial_downloads", action="store_true")
    parser.add_argument("--output_format", default="tsv")
    parser.add_argument("--rxnorm_rrf_dir", type=Path, default=DEFAULT_RXNORM_RRF_DIR)
    parser.add_argument("--log_level", default="INFO")
    parser.add_argument("--llm_review", action="store_true")
    parser.add_argument("--llm_workers", type=int, default=10)
    parser.add_argument("--llm_max_retries", type=int, default=10)
    parser.add_argument("--llm_retry_initial_delay", type=float, default=2)
    parser.add_argument("--llm_retry_max_delay", type=float, default=60)
    parser.add_argument("--llm_limit", type=int, default=None)
    parser.add_argument("--llm_model", default=None)
    parser.add_argument("--anzctr_timeout_ms", type=int, default=120_000)
    parser.add_argument("--anzctr_search_retries", type=int, default=3)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    config = RecursiveWorkflowConfig(
        export_date=args.export_date,
        repo_root=args.repo_root,
        python_bin=args.python_bin,
        max_iterations=args.max_iterations,
        output_format=args.output_format,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        log_level=args.log_level,
        use_latest_input_version=args.skip_initial_downloads,
        llm_review=args.llm_review,
        llm_workers=args.llm_workers,
        llm_max_retries=args.llm_max_retries,
        llm_retry_initial_delay=args.llm_retry_initial_delay,
        llm_retry_max_delay=args.llm_retry_max_delay,
        llm_limit=args.llm_limit,
        llm_model=args.llm_model,
        anzctr_timeout_ms=args.anzctr_timeout_ms,
        anzctr_search_retries=args.anzctr_search_retries,
    )

    run_recursive_workflow(
        config,
        initial_downloads=not args.skip_initial_downloads,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
