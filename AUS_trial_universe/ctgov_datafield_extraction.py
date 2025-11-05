import argparse
import logging
import json
from typing import List, Any, Dict
from pathlib import Path
import pandas as pd

logger = logging.getLogger(__name__)


def _dedup_records(ele_list: List[str]) -> List[str]:
    already_there = set()
    dedup_output = []

    for i in ele_list:
        if i not in already_there:
            already_there.add(i)
            dedup_output.append(i)

    return dedup_output


def extract_basic_fields(trial: Dict[str, Any]) -> Dict[str, Any]:
    nctId = trial.get("protocolSection").get("identificationModule").get("nctId")
    briefTitle = trial.get("protocolSection").get("identificationModule").get("briefTitle")
    leadSponsorName = trial.get("protocolSection").get("sponsorCollaboratorsModule").get("leadSponsor").get("name")

    basic_tbl = {
        "nctId": nctId,
        "briefTitle": briefTitle,
        "leadSponsor": leadSponsorName,
    }

    return basic_tbl


def extract_intervention_fields(trial: Dict[str, Any]) -> Dict[str, Any]:
    nctId = trial.get("protocolSection").get("identificationModule").get("nctId")
    interventions: list[dict[str, Any]] = trial.get("protocolSection").get("armsInterventionsModule").get("interventions")

    iType: List[str] = []
    iName: List[str] = []
    iOtherNames: List[str] = []

    for inter in interventions:
        iTypeSingle = inter.get("type")
        iNameSingle = inter.get("name")
        iOtherNamesSingle = inter.get("otherNames") or []

        if isinstance(iTypeSingle, str):
            iType.append(iTypeSingle.strip())
        if isinstance(iNameSingle, str):
            iName.append(iNameSingle.strip())
        if isinstance(iOtherNamesSingle, list):
            iOtherNames.extend(x.strip() for x in iOtherNamesSingle if isinstance(x, str) and x.strip())

    iType = _dedup_records(iType)
    iName = _dedup_records(iName)
    iOtherNames = _dedup_records(iOtherNames)

    inter_tbl = {
        "nctId": nctId,
        "interventionType": iType,
        "interventionName": iName,
        "interventionOtherNames": iOtherNames
    }

    return inter_tbl


def extract_location_fields(trial: Dict[str, Any]) -> Dict[str, Any]:
    nctId = trial.get("protocolSection").get("identificationModule").get("nctId")
    locations = trial.get("protocolSection").get("contactsLocationsModule", {}).get("locations", [])

    locFacility: List[str] = []
    locAddress: List[str] = []

    for loc in locations:
        if not isinstance(loc, dict):
            continue

        country = loc.get("country", "").strip()
        if country not in {"Australia", "New Zealand"}:
            continue

        facility = loc.get("facility", "").strip()
        locFacility.append(facility)

        city = loc.get("city", "").strip()
        state = loc.get("state", "").strip()
        postcode = loc.get("zip", "").strip()
        address_components = [x for x in [city, state, country, postcode] if x]
        address = ", ".join(address_components) if address_components else country or ""
        locAddress.append(address)

    locFacility = _dedup_records(locFacility)
    locAddress = _dedup_records(locAddress)

    loc_tbl = {
        "nctId": nctId,
        "facility": locFacility,
        "address": locAddress,
    }

    return loc_tbl


def main():
    parser = argparse.ArgumentParser(description="Extract relevant data fields from JSON file with all CT.gov trials")
    parser.add_argument("--ctgov_filepath", help="Filepath to the aggregate CT.gov json file", required=True)
    parser.add_argument("--output_file", help="Output summary table", required=True)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    trial_text = Path(args.ctgov_filepath).read_text(encoding="utf-8")
    trials = json.loads(trial_text)

    rows = []
    count = 0
    for trial in trials:
        basic_tbl = extract_basic_fields(trial)
        inter_tbl = extract_intervention_fields(trial)
        loc_tbl = extract_location_fields(trial)

        combined_tbl: Dict[str, Any] = basic_tbl | inter_tbl | loc_tbl
        rows.append(combined_tbl)
        count += 1

    combined_df = pd.DataFrame(rows)

    # Remove trials with zero DRUG intervention
    final_df = combined_df.loc[
        combined_df["interventionType"].apply(lambda x: "DRUG" in x)
    ].reset_index(drop=True)

    logger.info(f"Read in {count} trials from ct.gov")
    logger.info(f"Processed {len(combined_df)} trials for data fields extraction")
    logger.info(f"Filtered to {len(final_df)} trials with at least one drug intervention")

    final_df.to_csv(args.output_file, index=False)


if __name__ == "__main__":
    main()
