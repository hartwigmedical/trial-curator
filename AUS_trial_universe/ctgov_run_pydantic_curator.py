import argparse
import logging
import json
from pathlib import Path

import pydantic_curator.pydantic_curator as curator

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Process multiple CT.gov trials via the Pydantic curator.")
    parser.add_argument("--input_json", help="JSON file with multiple CT.gov trials", required=True)
    parser.add_argument("--trial_id", help="Optional: NCT ID (e.g. NCT01234567) to curate only a specified trial", required=False)
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

    if args.trial_id:
        target_id = args.trial_id
        filtered: list[dict] = []

        for t in trials:
            nct_id = (t.get("protocolSection", {}).get("identificationModule", {}).get("nctId"))
            if nct_id == target_id:
                filtered.append(t)

        if not filtered:
            logger.error("Trial %s not found in %s", target_id, args.input_json)
            return

        trials = filtered
        logger.info("Restricting to single trial %s", target_id)

    elif args.limit is not None:
        trials = trials[: args.limit]

    completed = skipped = failed = 0

    trials_count = len(trials)
    logger.info(f"Processing {trials_count} trials")

    for ind, trial in enumerate(trials, start=1):  # to start counting trials from 1,2,3... instead of being 0-indexed
        trial_id = trial["protocolSection"]["identificationModule"]["nctId"]
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
