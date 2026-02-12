import argparse
import logging
import json
from pathlib import Path
from typing import Any

import pandas as pd

import pydantic_curator.pydantic_curator as curator

logger = logging.getLogger(__name__)


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


def main():
    parser = argparse.ArgumentParser(description="Process CT.gov trials via the Pydantic curator.")
    parser.add_argument("--input_json", help="JSON file with multiple CT.gov trials", required=True)
    parser.add_argument("--selected_trial_csv", help="CSV with trials containing drug interventions", required=True)
    parser.add_argument("--trial_id", help="NCT ID (e.g. NCT01234567) to curate a single trial", required=False)
    parser.add_argument("--output_dir", help="Directory to write per-trial curated .py files", required=True)
    parser.add_argument("--limit", help="Optional: no. trials to process", default=None, type=int, required=False)
    parser.add_argument("--overwrite_existing", help="Re-curate even if output file exists", action="store_true", required=False)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    client = curator.OpenaiClient()  # client is from the pydantic curator namespace
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(args.input_json, "r", encoding="utf-8") as f:
        trials = json.load(f)
    if not isinstance(trials, list):
        raise ValueError("Expected input JSON to be a list of trials.")

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

    for ind, trial in enumerate(trials, start=1):  # to start counting trials from 1,2,3... instead of being 0-indexed
        trial_id = get_nct_id(trial)
        if not trial_id:
            logger.warning("Skipping trial with missing nctId (%d/%d examined).", ind, trials_count)
            failed += 1
            continue

        trial_id = trial_id.strip()
        output_filepath = out_dir / f"{trial_id}.py"

        if output_filepath.exists() and not args.overwrite_existing:
            logger.info(f"{trial_id}.py exists. Skipping.")
            logger.info(f"{ind}/{trials_count} examined.")
            skipped += 1
            continue

        try:
            eligibility_criteria = curator.load_eligibility_criteria(trial)
            processed_rules = curator.llm_rules_prep_workflow(eligibility_criteria, client)
            if not processed_rules:
                raise ValueError("No rules produced by text preparation workflow.")

            curated_rules: list[curator.RuleOutput] = []
            for criterion in processed_rules:
                curated_result = curator.pydantic_curator_workflow(criterion, client)
                curated_rules.append(curated_result)

            curator._write_output_py(output_filepath, curated_rules)
            logger.info(f"{trial_id} curated. Saved as {output_filepath}.")
            logger.info(f"{ind}/{trials_count} examined.")
            completed += 1

        except Exception:
            logger.exception(f"{trial_id} failed.")
            logger.info(f"{ind}/{trials_count} examined.")
            failed += 1

    logger.info(f"Finished. total={trials_count}, of which completed={completed}, skipped={skipped}, failed={failed}")


if __name__ == "__main__":
    main()
