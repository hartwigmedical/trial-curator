import argparse
import logging
import json
from collections.abc import Sequence
from typing import List, Any, Dict
from pathlib import Path

import pandas as pd

from aus_trial_universe.trials_to_remove.trials_to_remove import should_remove_trial
from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    existing_files,
    latest_version_dir,
)
from aus_trial_universe.eligibility_path.shared.trial_resource.combined_trial_resource_export import (
    load_pottr_trial_ids_best_effort,
    normalize_trial_id,
)

logger = logging.getLogger(__name__)

DEFAULT_CT_GOV_INPUT_JSON = Path(
    "data/trial_inputs/ctgov/input_trials/version_13022026/ctgov_input.json"
)
DEFAULT_CT_GOV_INPUT_ROOT = Path("data/trial_inputs/ctgov/input_trials")
DEFAULT_CTGOV_INPUT_FILENAMES = (
    "01_initial_search_ctgov_input.json",
    "02_pottr_append_ctgov_input.json",
    "ctgov_input.json",
    "ctgov_trials_merged.json",
)
DEFAULT_OUTPUT_DIR = Path("data/trial_inputs/ctgov/extracted_trials")


def _dedup_records(ele_list: List[str] | None) -> List[str]:
    if not ele_list:
        return []

    already_there = set()
    dedup_output = []

    for i in ele_list:
        if i not in already_there:
            already_there.add(i)
            dedup_output.append(i)

    return dedup_output


def extract_basic_fields(trial: Dict[str, Any]) -> Dict[str, Any]:
    nctId: str = trial.get("protocolSection").get("identificationModule").get("nctId")
    briefTitle: str = trial.get("protocolSection").get("identificationModule").get("briefTitle")
    officialTitle: str = trial.get("protocolSection").get("identificationModule").get("officialTitle")
    status: str = trial.get("protocolSection").get("statusModule").get("overallStatus")
    leadSponsorName: str = trial.get("protocolSection").get("sponsorCollaboratorsModule").get("leadSponsor").get("name")
    phases: list[str] = trial.get("protocolSection").get("designModule").get("phases")

    basic_tbl = {
        "nctId": nctId,
        "briefTitle": briefTitle,
        "officialTitle": officialTitle,
        "status": status,
        "phases": phases,
        "leadSponsor": leadSponsorName,
    }
    return basic_tbl


def extract_conditions(trial: Dict[str, Any]) -> Dict[str, Any]:
    nctId = trial.get("protocolSection").get("identificationModule").get("nctId")
    conditions: list[str] = trial.get("protocolSection").get("conditionsModule").get("conditions")

    conditions_tbl = {
        "nctId": nctId,
        "conditions": conditions
    }

    return conditions_tbl


def extract_age_fields(trial: Dict[str, Any]) -> Dict[str, Any]:
    nctId = trial.get("protocolSection").get("identificationModule").get("nctId")
    minage = trial.get("protocolSection").get("eligibilityModule").get("minimumAge", "")
    maxage = trial.get("protocolSection").get("eligibilityModule").get("maximumAge", "")

    age_tbl = {
        "nctId": nctId,
        "minAge": minage,
        "maxAge": maxage,
    }
    return age_tbl


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

        facility = (loc.get("facility") or "").strip()  # accommodate when facility is `None`
        if facility:
            locFacility.append(facility)

        city = loc.get("city", "").strip()
        state = loc.get("state", "").strip()
        postcode = loc.get("zip", "").strip()
        add_components = [x for x in [city, state, postcode, country] if x]
        address = ", ".join(add_components) if add_components else country or ""
        locAddress.append(address)

    locFacility = _dedup_records(locFacility)
    locAddress = _dedup_records(locAddress)

    loc_tbl = {
        "nctId": nctId,
        "facility": locFacility,
        "address": locAddress,
    }
    return loc_tbl


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


def extract_arms_fields(trial: Dict[str, Any]) -> List[Dict[str, Any]]:
    nctId = trial.get("protocolSection").get("identificationModule").get("nctId")
    arm_groups: List[Dict[str, Any]] = trial.get("protocolSection").get("armsInterventionsModule", {}).get("armGroups", [])

    arm_rows: List[Dict[str, Any]] = []
    for arm in arm_groups:
        arm_type = arm.get("type")
        arm_label = arm.get("label")
        intervention_names = arm.get("interventionNames")

        intervention_regime: List[str] = []
        if isinstance(intervention_names, list):
            for ele in intervention_names:
                if isinstance(ele, str) and ":" in ele:
                    prefix, val = ele.split(":", 1)

                    if prefix and val:
                        cleaned_val = val.strip()
                        if cleaned_val:
                            intervention_regime.append(cleaned_val)

        arm_rows.append({
            "nctId": nctId,
            "armType": arm_type.strip() if isinstance(arm_type, str) else arm_type,
            "armLabel": arm_label.strip() if isinstance(arm_label, str) else arm_label,
            "intervention_regime": str(intervention_regime),
        })
    return arm_rows


def get_nct_id(trial: Dict[str, Any]) -> str:
    return (
        trial.get("protocolSection", {})
        .get("identificationModule", {})
        .get("nctId", "")
        .strip()
        .upper()
    )


def load_ctgov_trials(ctgov_filepaths: Path | Sequence[Path]) -> list[dict[str, Any]]:
    if isinstance(ctgov_filepaths, (str, Path)):
        paths = [Path(ctgov_filepaths)]
    else:
        paths = [Path(path) for path in ctgov_filepaths]

    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        trial_text = path.read_text(encoding="utf-8")
        trials = json.loads(trial_text)
        if not isinstance(trials, list):
            raise ValueError(f"Expected a list of CTGov trials in {path}")
        for trial in trials:
            nct_id = get_nct_id(trial)
            if nct_id:
                merged[nct_id] = trial

    return list(merged.values())


def default_ctgov_input_files(
    input_root: Path = DEFAULT_CT_GOV_INPUT_ROOT,
) -> list[Path]:
    try:
        version_dir = latest_version_dir(input_root)
    except FileNotFoundError:
        return [DEFAULT_CT_GOV_INPUT_JSON]

    staged = existing_files(
        [
            version_dir / "01_initial_search_ctgov_input.json",
            version_dir / "02_pottr_append_ctgov_input.json",
        ]
    )
    if staged:
        return staged

    fallback = existing_files(
        [version_dir / filename for filename in DEFAULT_CTGOV_INPUT_FILENAMES]
    )
    return fallback or [DEFAULT_CT_GOV_INPUT_JSON]


def extract_fields_to_outputs(
    ctgov_filepath: Path | Sequence[Path],
    output_dir: Path,
    pottr_trial_ids: set[str] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    trials = load_ctgov_trials(ctgov_filepath)
    pottr_ids = pottr_trial_ids or set()

    general_rows = []
    arm_rows = []
    count = 0
    pottr_exempt = 0

    for trial in trials:
        count += 1
        basic_tbl = extract_basic_fields(trial)
        nct_id = basic_tbl.get("nctId")
        # POTTR-listed trials must always reach the final output, so they
        # override the manual-removal list as well as the drug filter below.
        if (
            should_remove_trial(nct_id, registry="ctgov")
            and normalize_trial_id(nct_id) not in pottr_ids
        ):
            logger.info("Skipping manually removed CTGov trial: %s", nct_id)
            continue

        conditions_tbl = extract_conditions(trial)
        ages_tbl = extract_age_fields(trial)
        inter_tbl = extract_intervention_fields(trial)
        loc_tbl = extract_location_fields(trial)

        combined_tbl: Dict[str, Any] = basic_tbl | conditions_tbl | ages_tbl | inter_tbl | loc_tbl

        # Retain trials with DRUG intervention ("BIOLOGICAL" label contains drug interventions, at the cost of some false positives)
        intervention_types = combined_tbl.get("interventionType") or []
        is_drug_trial = any(t in intervention_types for t in ["DRUG", "BIOLOGICAL"])

        # POTTR-listed trials must always reach the final output, so they are
        # exempt from the drug-intervention cohort filter (see load_pottr_trial_ids).
        if not is_drug_trial:
            if normalize_trial_id(nct_id) in pottr_ids:
                pottr_exempt += 1
            else:
                continue

        general_rows.append(combined_tbl)
        arm_rows.extend(extract_arms_fields(trial))

    general_df = pd.DataFrame(general_rows)
    arms_df = pd.DataFrame(arm_rows)

    logger.info(f"Read in {count} trials from ct.gov")
    logger.info(f"Filtered to {len(general_df)} trials with at least one drug intervention with {len(arms_df)} arms/cohorts.")
    if pottr_exempt:
        logger.info("Retained %d non-drug trial(s) exempted as POTTR-listed.", pottr_exempt)

    csv_path = output_dir / "ctgov_field_extractions.csv"

    general_df.to_csv(csv_path, index=False)
    logger.info(f"Wrote CTGov field extraction CSV: {csv_path}")
    return csv_path


def main():
    parser = argparse.ArgumentParser(description="Extract relevant data fields from JSON file with all CT.gov trials")
    parser.add_argument(
        "--ctgov_filepath",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "Filepath(s) to CT.gov JSON input. Defaults to the newest "
            "data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy> files."
        ),
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to store the selected-trials CSV. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    extract_fields_to_outputs(
        args.ctgov_filepath or default_ctgov_input_files(),
        args.output_dir,
        pottr_trial_ids=load_pottr_trial_ids_best_effort(registry="ctgov"),
    )


if __name__ == "__main__":
    main()
