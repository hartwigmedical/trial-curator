import argparse
import os
import json
import logging
import pandas as pd

logger = logging.getLogger(__name__)


POTTR_ANZCTR_PREFIX = "ACTRN"
POTTR_CTGOV_PREFIX = "NCT"


def in_pottr_but_missing(pottr_df: pd.DataFrame, download_df: pd.DataFrame, download_id: str) -> pd.DataFrame:

    missed_trials = pottr_df.loc[pottr_df["trial_id"].isin(
        set(pottr_df["trial_id"]) - set(download_df[download_id])
    ), :].reset_index(drop=True)

    return missed_trials


def main():

    parser = argparse.ArgumentParser(description="Check downloaded ANZCTR and CT.gov trials against POTTR's trials selection for potential coverage issue")
    parser.add_argument("--pottr_filepath", help="Filepath to latest trial_registry.AU.tsv file from POTTR", required=True)
    parser.add_argument("--ctgov_filepath", help="Filepath to the aggregate CT.gov json file", required=True)
    parser.add_argument("--missed_ctgov_folder", help="Folder to save the missed CTGOV trials", required=True)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    pottr = pd.read_csv(args.pottr_filepath, sep="\t")

    pottr_anzctr = pottr.loc[pottr["trial_id"].str.startswith(POTTR_ANZCTR_PREFIX), :].reset_index(drop=True)
    logger.info("No. ANZCTR trials in POTTR: %d", len(pottr_anzctr))

    pottr_ctgov = pottr.loc[pottr["trial_id"].str.startswith(POTTR_CTGOV_PREFIX), :].reset_index(drop=True)
    logger.info("No. CTGOV trials in POTTR: %d", len(pottr_ctgov))

    with open(args.ctgov_filepath, "r") as f:
        ctgov_trials = json.load(f)

    nct_ids_list = [
        t.get("protocolSection").get("identificationModule").get("nctId")
        for t in ctgov_trials
    ]
    nct_ids = pd.DataFrame({"nctId": nct_ids_list}).dropna().reset_index(drop=True)

    missed_ctgov_trials = in_pottr_but_missing(pottr_ctgov, nct_ids, "nctId")
    if len(missed_ctgov_trials) == 0:
        logger.info("No missing POTTR CTGOV trials.")
    else:
        logger.info("Missed POTTR CTGOV trials: %d", len(missed_ctgov_trials))
        missed_ctgov_trials.to_csv(os.path.join(args.missed_ctgov_folder, "missed_trials.csv"), index=False)
        logger.info("Wrote missed POTTR CTGOV trials to %s", args.missed_ctgov_folder)


if __name__ == "__main__":
    main()
