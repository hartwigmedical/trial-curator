import argparse
import logging
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import pydantic_curator.pydantic_curator as curator
from aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.ii_extract_fields import (
    default_ctgov_input_files,
    load_ctgov_trials,
)

logger = logging.getLogger(__name__)

# Third-party / curator-internal loggers that flood the shared run log: the OpenAI
# SDK's HTTP traffic and the curator library internals. Capped to WARNING so only
# this module's concise per-trial "Trial <id> processed ... saved in <path>" status
# lines remain. (The OpenaiClient wrapper logs prompts/responses at DEBUG.)
_NOISY_CURATOR_LOGGERS = (
    "pydantic_curator",
    "openai",
    "httpx",
    "httpcore",
    "anthropic",
    "urllib3",
)


def _quiet_curator_internals() -> None:
    for name in _NOISY_CURATOR_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


DEFAULT_INPUT_JSON = Path(
    "data/trial_inputs/ctgov/input_trials/version_13022026/ctgov_input.json"
)
DEFAULT_SELECTED_TRIAL_CSV = Path(
    "data/trial_inputs/ctgov/extracted_trials/ctgov_field_extractions.csv"
)
DEFAULT_OUTPUT_DIR = Path("data/trial_inputs/ctgov/eligibility_curations")


def load_selected_trial_ids(csv_path: str) -> set[str]:
    df = pd.read_csv(csv_path)
    df.columns = [c.lower().strip() for c in df.columns]

    col = "nctid"
    if col not in df.columns:
        raise ValueError("Selected trials CSV must contain a column named 'nctid'")

    return set(df[col].dropna().astype(str).str.strip().str.upper())


def get_nct_id(trial: dict[str, Any]) -> str | None:
    return (
        trial.get("protocolSection", {})
        .get("identificationModule", {})
        .get("nctId")
    )


def process_single_trial(
    trial: dict[str, Any],
    out_dir: Path,
    overwrite_existing: bool,
) -> tuple[str, str]:
    """
    Process a single trial and return (status, trial_id).

    status is one of: completed, skipped
    Exceptions are allowed to propagate so caller can log/count failures.
    """
    trial_id = get_nct_id(trial)
    if not trial_id:
        raise ValueError("Skipping trial with missing nctId.")

    trial_id = trial_id.strip()
    output_filepath = out_dir / f"{trial_id}.py"

    if output_filepath.exists() and not overwrite_existing:
        return ("skipped", trial_id)

    client = curator.OpenaiClient()  # one client per worker thread

    eligibility_criteria = curator.load_eligibility_criteria(trial)
    processed_rules = curator.llm_rules_prep_workflow(eligibility_criteria, client)
    if not processed_rules:
        raise ValueError("No rules produced by text preparation workflow.")

    curated_rules: list[curator.RuleOutput] = []
    for criterion in processed_rules:
        curated_result = curator.pydantic_curator_workflow(criterion, client)
        curated_rules.append(curated_result)

    curator._write_output_py(output_filepath, curated_rules)
    return ("completed", trial_id)


def main():
    parser = argparse.ArgumentParser(description="Process CT.gov trials via the Pydantic curator.")
    parser.add_argument(
        "--input_json",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "JSON file(s) with CT.gov trials. Defaults to the newest "
            "data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy> files."
        ),
    )
    parser.add_argument(
        "--selected_trial_csv",
        type=Path,
        default=DEFAULT_SELECTED_TRIAL_CSV,
        help=f"CSV with trials containing drug interventions. Default: {DEFAULT_SELECTED_TRIAL_CSV}",
    )
    parser.add_argument("--trial_id", help="NCT ID (e.g. NCT01234567) to curate a single trial", required=False)
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to write per-trial curated .py files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument("--limit", help="Optional: no. trials to process", default=None, type=int, required=False)
    parser.add_argument("--overwrite_existing", help="Re-curate even if output file exists", action="store_true", required=False)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    parser.add_argument("--max_workers", help="Number of trials to process in parallel", default=1, type=int, required=False)
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    _quiet_curator_internals()

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    trials = load_ctgov_trials(args.input_json or default_ctgov_input_files())

    # Filter to selected trials
    selected_ids = load_selected_trial_ids(args.selected_trial_csv)
    before = len(trials)
    trials = [t for t in trials if (get_nct_id(t) or "").strip().upper() in selected_ids]
    logger.info("Filtered by selected_trial_csv: %d -> %d trials", before, len(trials))

    if not trials:
        logger.warning("No trials left after filtering. Check selected_trial_csv and input_json.")
        return

    # If trial_id specified, further restrict to that single trial
    if args.trial_id:
        target_id = args.trial_id.strip().upper()
        before = len(trials)
        trials = [t for t in trials if (get_nct_id(t) or "").strip().upper() == target_id]

        if not trials:
            logger.error(
                "Trial %s not found after filtering. (Either not in %s, or not present in %s.)",
                target_id,
                args.selected_trial_csv,
                args.input_json,
            )
            return

        logger.info("Restricting to single trial %s (%d -> %d)", target_id, before, len(trials))

    if args.limit is not None:
        trials = trials[: args.limit]

    completed = skipped = failed = 0
    trials_count = len(trials)
    logger.info(f"Processing {trials_count} trials")

    # Preserve original missing-nctId behavior before submitting to thread pool
    valid_trials: list[dict[str, Any]] = []
    examined = 0

    for trial in trials:
        trial_id = get_nct_id(trial)
        if not trial_id:
            examined += 1
            logger.warning("Skipping trial with missing nctId (%d/%d examined).", examined, trials_count)
            failed += 1
            continue
        valid_trials.append(trial)

    if not valid_trials:
        logger.info(f"Finished. total={trials_count}, of which completed={completed}, skipped={skipped}, failed={failed}")
        return

    max_workers = max(1, args.max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_trial = {
            executor.submit(
                process_single_trial,
                trial,
                out_dir,
                args.overwrite_existing,
            ): trial
            for trial in valid_trials
        }

        for future in as_completed(future_to_trial):
            examined += 1
            trial = future_to_trial[future]
            trial_id = (get_nct_id(trial) or "").strip() or "<missing nctId>"

            try:
                status, returned_trial_id = future.result()

                if status == "skipped":
                    logger.info(f"[{examined}/{trials_count}] Trial {returned_trial_id} already curated; skipped.")
                    skipped += 1
                elif status == "completed":
                    output_filepath = out_dir / f"{returned_trial_id}.py"
                    logger.info(
                        f"[{examined}/{trials_count}] Trial {returned_trial_id} processed by "
                        f"Pydantic curator and saved in {output_filepath}"
                    )
                    completed += 1
                else:
                    logger.error(f"[{examined}/{trials_count}] {returned_trial_id} returned unknown status: {status}")
                    failed += 1

            except Exception:
                logger.exception(f"[{examined}/{trials_count}] Trial {trial_id} failed.")
                failed += 1

    logger.info(f"Finished. total={trials_count}, of which completed={completed}, skipped={skipped}, failed={failed}")


if __name__ == "__main__":
    main()
