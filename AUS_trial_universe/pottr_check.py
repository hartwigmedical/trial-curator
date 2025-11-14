import argparse
import os
import json
import logging
from pathlib import Path
import pandas as pd

logger = logging.getLogger(__name__)


POTTR_ANZCTR_PREFIX = "ACTRN"
POTTR_CTGOV_PREFIX = "NCT"


def pottr_not_selected(pottr_df: pd.DataFrame, selected_df: pd.DataFrame, id_identifier: str) -> pd.DataFrame:

    not_selected = pottr_df.loc[pottr_df["trial_id"].isin(
        set(pottr_df["trial_id"]) - set(selected_df[id_identifier])
    ), :].reset_index(drop=True)

    return not_selected


def selected_not_pottr(pottr_df: pd.DataFrame, selected_df: pd.DataFrame, id_identifier: str) -> pd.DataFrame:

    not_pottr = selected_df.loc[selected_df[id_identifier].isin(
        set(selected_df[id_identifier]) - set(pottr_df["trial_id"])
    ), :].reset_index(drop=True)

    return not_pottr


def main():
    parser = argparse.ArgumentParser(description="Check downloaded ANZCTR and CT.gov trials against POTTR's trials selection for potential coverage issue")
    parser.add_argument("--pottr_filepath", help="Filepath to latest trial_registry.AU.tsv file from POTTR", required=True)

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ctgov", help="To compare ClinicalTrials.gov trials", action="store_true")
    source.add_argument("--anzctr", help="To compare ANZCTR trials", action="store_true")

    parser.add_argument("--ctgov_filepath", help="Filepath to the aggregate ctgov json file", required=False)
    parser.add_argument("--anzctr_filepath", help="Filepath to the aggregate anzctr csv file", required=False)  # Not implemented yet

    parser.add_argument("--discrepancy_dir", type=Path, help="Filepath to save the differences in trials selection", required=True)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    if args.ctgov and not args.ctgov_filepath:
        parser.error("--ctgov requires --ctgov_filepath")
    if args.anzctr and not args.anzctr_filepath:
        parser.error("--anzctr requires --anzctr_filepath")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    args.discrepancy_dir.mkdir(parents=True, exist_ok=True)

    pottr_df = pd.read_csv(args.pottr_filepath, sep="\t")

    pottr_anzctr = pottr_df.loc[pottr_df["trial_id"].str.startswith(POTTR_ANZCTR_PREFIX), :].reset_index(drop=True)
    logger.info("No. ANZCTR trials in POTTR: %d", len(pottr_anzctr))

    pottr_ctgov = pottr_df.loc[pottr_df["trial_id"].str.startswith(POTTR_CTGOV_PREFIX), :].reset_index(drop=True)
    logger.info("No. CTGOV trials in POTTR: %d", len(pottr_ctgov))

    if args.ctgov:
        with open(args.ctgov_filepath, "r", encoding="utf-8") as f:
            ctgov_trials = json.load(f)

        nct_ids_list = [t.get("protocolSection").get("identificationModule").get("nctId") for t in ctgov_trials]
        nct_ids = pd.DataFrame({"nctId": nct_ids_list}).dropna().reset_index(drop=True)

        not_selected = pottr_not_selected(pottr_ctgov, nct_ids, "nctId")
        if len(not_selected) == 0:
            logger.info("No POTTR trial has been missed.")
        else:
            logger.info("No. POTTR trials from ctgov NOT selected: %d", len(not_selected))
            not_selected.to_csv(os.path.join(args.discrepancy_dir, "ctgov_not_selected.csv"), index=False)
            logger.info("Wrote to %s", args.discrepancy_dir)

        not_pottr = selected_not_pottr(pottr_ctgov, nct_ids, "nctId")
        if len(not_pottr) == 0:
            logger.info("No selected trial is missing in POTTR.")
        else:
            logger.info("No. trials selected from ctgov NOT in POTTR: %d", len(not_pottr))
            not_pottr.to_csv(os.path.join(args.discrepancy_dir, "ctgov_not_pottr.csv"), index=False)
            logger.info("Wrote to %s", args.discrepancy_dir)

    if args.anzctr:
        # TBD
        pass


if __name__ == "__main__":
    main()
