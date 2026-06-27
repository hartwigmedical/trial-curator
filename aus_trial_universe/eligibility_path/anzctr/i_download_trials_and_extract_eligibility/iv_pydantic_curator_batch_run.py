from __future__ import annotations

import argparse
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

import pandas as pd

import pydantic_curator.pydantic_curator as curator

logger = logging.getLogger(__name__)

# Third-party / curator-internal loggers that flood the shared run log (especially
# at DEBUG): the OpenAI client's HTTP traffic and the curator library internals.
# Capped to WARNING so only this module's per-trial "<id> curated. Saved as <path>."
# status lines remain in the log.
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


DEFAULT_INPUT_CSV = Path("data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv")
DEFAULT_OUTPUT_DIR = Path("data/trial_inputs/anzctr/eligibility_curations")

TRIAL_ID_COLUMN = "ACTRN"
INCLUSION_COLUMN = "INCLUSIVE CRITERIA"
EXCLUSION_COLUMN = "EXCLUSIVE CRITERIA"
REQUIRED_COLUMNS = [TRIAL_ID_COLUMN, INCLUSION_COLUMN, EXCLUSION_COLUMN]

WHITESPACE_RE = re.compile(r"\s+")
TRAILING_FLOAT_ZERO_RE = re.compile(r"^(\d+)\.0$")
UNSAFE_FILENAME_CHAR_RE = re.compile(r"[^A-Za-z0-9_.-]+")

EMPTY_CRITERIA_VALUES = {
    "",
    "n/a",
    "na",
    "nil",
    "none",
    "not applicable",
    "no inclusion criteria",
    "no inclusive criteria",
    "no exclusion criteria",
    "no exclusive criteria",
}


class AnzctrTrial(NamedTuple):
    trial_id: str
    eligibility_criteria: str


def clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return WHITESPACE_RE.sub(" ", str(value).strip())


def normalise_empty_criteria(value: object) -> str:
    text = clean_text(value)
    if text.casefold().rstrip(".") in EMPTY_CRITERIA_VALUES:
        return ""
    return text


def normalise_actrn(value: object) -> str:
    text = clean_text(value)
    if not text:
        return ""

    text = WHITESPACE_RE.sub("", text)
    match = TRAILING_FLOAT_ZERO_RE.match(text)
    if match:
        text = match.group(1)

    if text.casefold().startswith("actrn"):
        text = "ACTRN" + text[5:]
    else:
        text = f"ACTRN{text}"

    return UNSAFE_FILENAME_CHAR_RE.sub("_", text)


def require_columns(frame: pd.DataFrame, columns: list[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"ANZCTR input CSV is missing columns: {', '.join(missing)}")


def build_eligibility_criteria_text(
    inclusion_criteria: object,
    exclusion_criteria: object,
) -> str:
    inclusion_text = normalise_empty_criteria(inclusion_criteria)
    exclusion_text = normalise_empty_criteria(exclusion_criteria)

    sections: list[str] = []
    if inclusion_text:
        sections.append(f"Inclusion Criteria:\n{inclusion_text}")
    if exclusion_text:
        sections.append(f"Exclusion Criteria:\n{exclusion_text}")

    return "\n\n".join(sections)


def load_anzctr_trials(input_csv: str | Path) -> list[AnzctrTrial]:
    frame = pd.read_csv(input_csv)
    require_columns(frame, REQUIRED_COLUMNS)

    trials: list[AnzctrTrial] = []
    for row in frame[REQUIRED_COLUMNS].itertuples(index=False, name=None):
        trial_id = normalise_actrn(row[0])
        eligibility_criteria = build_eligibility_criteria_text(row[1], row[2])
        trials.append(AnzctrTrial(trial_id=trial_id, eligibility_criteria=eligibility_criteria))

    return trials


def process_single_trial(
    trial: AnzctrTrial,
    out_dir: Path,
    overwrite_existing: bool,
) -> tuple[str, str]:
    """
    Process a single ANZCTR trial and return (status, trial_id).

    status is one of: completed, skipped. Exceptions are allowed to propagate so
    the caller can log and count failures.
    """
    if not trial.trial_id:
        raise ValueError("Skipping ANZCTR trial with missing ACTRN.")
    if not trial.eligibility_criteria.strip():
        raise ValueError(f"{trial.trial_id} has no eligibility criteria text.")

    output_filepath = out_dir / f"{trial.trial_id}.py"
    if output_filepath.exists() and not overwrite_existing:
        return ("skipped", trial.trial_id)

    client = curator.OpenaiClient()  # one client per worker thread

    processed_rules = curator.llm_rules_prep_workflow(trial.eligibility_criteria, client)
    if not processed_rules:
        raise ValueError("No rules produced by text preparation workflow.")

    curated_rules: list[curator.RuleOutput] = []
    for criterion in processed_rules:
        curated_result = curator.pydantic_curator_workflow(criterion, client)
        curated_rules.append(curated_result)

    curator._write_output_py(output_filepath, curated_rules)
    return ("completed", trial.trial_id)


def filter_trials(
    trials: list[AnzctrTrial],
    trial_id: str | None,
    limit: int | None,
) -> list[AnzctrTrial]:
    if trial_id:
        target_id = normalise_actrn(trial_id)
        before = len(trials)
        trials = [
            trial
            for trial in trials
            if trial.trial_id.casefold() == target_id.casefold()
        ]
        logger.info(
            "Restricting to single trial %s (%d -> %d)",
            target_id,
            before,
            len(trials),
        )

    if limit is not None:
        trials = trials[:limit]

    return trials


def run_batch(
    input_csv: str | Path,
    output_dir: str | Path,
    *,
    trial_id: str | None = None,
    limit: int | None = None,
    overwrite_existing: bool = False,
    max_workers: int = 1,
) -> dict[str, int]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    trials = load_anzctr_trials(input_csv)
    logger.info("Loaded %d ANZCTR trial rows from %s", len(trials), input_csv)

    trials = filter_trials(trials, trial_id=trial_id, limit=limit)
    if not trials:
        if trial_id:
            logger.error("Trial %s not found after filtering.", normalise_actrn(trial_id))
        else:
            logger.warning("No ANZCTR trials left after filtering.")
        return {"total": 0, "completed": 0, "skipped": 0, "failed": 0}

    completed = skipped = failed = 0
    trials_count = len(trials)
    logger.info("Processing %d ANZCTR trials", trials_count)

    valid_trials: list[AnzctrTrial] = []
    examined = 0
    for trial in trials:
        if not trial.trial_id:
            examined += 1
            logger.warning(
                "Skipping ANZCTR trial with missing ACTRN (%d/%d examined).",
                examined,
                trials_count,
            )
            failed += 1
            continue
        valid_trials.append(trial)

    if not valid_trials:
        logger.info(
            "Finished. total=%d, of which completed=%d, skipped=%d, failed=%d",
            trials_count,
            completed,
            skipped,
            failed,
        )
        return {
            "total": trials_count,
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
        }

    max_workers = max(1, max_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_trial = {
            executor.submit(
                process_single_trial,
                trial,
                out_dir,
                overwrite_existing,
            ): trial
            for trial in valid_trials
        }

        for future in as_completed(future_to_trial):
            examined += 1
            trial = future_to_trial[future]
            trial_id_for_log = trial.trial_id or "<missing ACTRN>"

            try:
                status, returned_trial_id = future.result()
                if status == "skipped":
                    logger.info("%s.py exists. Skipping.", returned_trial_id)
                    logger.info("%d/%d examined.", examined, trials_count)
                    skipped += 1
                elif status == "completed":
                    output_filepath = out_dir / f"{returned_trial_id}.py"
                    logger.info(
                        "%s curated. Saved as %s.",
                        returned_trial_id,
                        output_filepath,
                    )
                    logger.info("%d/%d examined.", examined, trials_count)
                    completed += 1
                else:
                    logger.error("%s returned unknown status: %s", returned_trial_id, status)
                    logger.info("%d/%d examined.", examined, trials_count)
                    failed += 1

            except Exception:
                logger.exception("%s failed.", trial_id_for_log)
                logger.info("%d/%d examined.", examined, trials_count)
                failed += 1

    logger.info(
        "Finished. total=%d, of which completed=%d, skipped=%d, failed=%d",
        trials_count,
        completed,
        skipped,
        failed,
    )
    return {
        "total": trials_count,
        "completed": completed,
        "skipped": skipped,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process ANZCTR trials via the Pydantic curator."
    )
    parser.add_argument(
        "--input_csv",
        type=Path,
        default=DEFAULT_INPUT_CSV,
        help=f"ANZCTR trial field extraction CSV. Default: {DEFAULT_INPUT_CSV}",
    )
    parser.add_argument(
        "--trial_id",
        help="ACTRN ID to curate a single trial, with or without the ACTRN prefix.",
        required=False,
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Directory to write per-trial curated .py files. "
            f"Default: {DEFAULT_OUTPUT_DIR}"
        ),
    )
    parser.add_argument(
        "--limit",
        help="Optional: no. trials to process",
        default=None,
        type=int,
    )
    parser.add_argument(
        "--overwrite_existing",
        help="Re-curate even if output file exists",
        action="store_true",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    parser.add_argument(
        "--max_workers",
        help="Number of trials to process in parallel",
        default=1,
        type=int,
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    _quiet_curator_internals()

    run_batch(
        input_csv=args.input_csv,
        output_dir=args.output_dir,
        trial_id=args.trial_id,
        limit=args.limit,
        overwrite_existing=args.overwrite_existing,
        max_workers=args.max_workers,
    )


if __name__ == "__main__":
    main()
