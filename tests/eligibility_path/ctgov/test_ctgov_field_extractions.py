from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.ii_extract_fields import (
    default_ctgov_input_files,
    extract_fields_to_outputs,
    load_ctgov_trials,
)


def trial_record(nct_id: str, intervention_type: str) -> dict:
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": f"Trial {nct_id}",
                "officialTitle": f"Official trial {nct_id}",
            },
            "statusModule": {"overallStatus": "RECRUITING"},
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Example Sponsor"},
            },
            "designModule": {"phases": ["PHASE2"]},
            "conditionsModule": {"conditions": ["Cancer"]},
            "eligibilityModule": {
                "minimumAge": "18 Years",
                "maximumAge": "80 Years",
            },
            "contactsLocationsModule": {
                "locations": [
                    {
                        "facility": "Example Hospital",
                        "city": "Sydney",
                        "state": "NSW",
                        "zip": "2000",
                        "country": "Australia",
                    },
                ],
            },
            "armsInterventionsModule": {
                "interventions": [
                    {
                        "type": intervention_type,
                        "name": "Example Intervention",
                        "otherNames": ["Alias"],
                    },
                ],
                "armGroups": [
                    {
                        "type": "EXPERIMENTAL",
                        "label": "Arm A",
                        "interventionNames": [f"{intervention_type}: Example Intervention"],
                    },
                ],
            },
        },
    }


def test_extract_fields_to_outputs_writes_curator_csv(tmp_path: Path):
    input_json = tmp_path / "ctgov_trials_delta.json"
    input_json.write_text(
        json.dumps(
            [
                trial_record("NCT00000001", "DRUG"),
                trial_record("NCT00000002", "PROCEDURE"),
                trial_record("NCT06461286", "DRUG"),
            ]
        ),
        encoding="utf-8",
    )

    csv_path = extract_fields_to_outputs(input_json, tmp_path / "trials")

    assert csv_path == tmp_path / "trials" / "ctgov_field_extractions.csv"
    assert csv_path.exists()
    assert not (tmp_path / "trials" / "ctgov_field_extractions.xlsx").exists()

    csv_df = pd.read_csv(csv_path)
    assert csv_df["nctId"].tolist() == ["NCT00000001"]
    assert csv_df["officialTitle"].tolist() == ["Official trial NCT00000001"]


def test_extract_fields_to_outputs_keeps_non_drug_pottr_trials(tmp_path: Path):
    input_json = tmp_path / "ctgov_trials_delta.json"
    input_json.write_text(
        json.dumps(
            [
                trial_record("NCT00000001", "DRUG"),
                trial_record("NCT00000002", "PROCEDURE"),  # non-drug, but POTTR-listed
                trial_record("NCT00000003", "PROCEDURE"),  # non-drug, not POTTR-listed
                trial_record("NCT06461286", "PROCEDURE"),  # POTTR-listed AND removal-listed
            ]
        ),
        encoding="utf-8",
    )

    csv_path = extract_fields_to_outputs(
        input_json,
        tmp_path / "trials",
        pottr_trial_ids={"NCT00000002", "NCT06461286"},
    )

    csv_df = pd.read_csv(csv_path)
    # The non-drug POTTR trial is retained; the non-drug non-POTTR trial is dropped.
    # POTTR overrides both the drug filter and the manual-removal list (NCT06461286).
    assert csv_df["nctId"].tolist() == [
        "NCT00000001",
        "NCT00000002",
        "NCT06461286",
    ]


def test_load_ctgov_trials_merges_multiple_input_files_by_nct_id(tmp_path: Path):
    initial_json = tmp_path / "01_initial_search_ctgov_input.json"
    append_json = tmp_path / "02_pottr_append_ctgov_input.json"
    initial_json.write_text(
        json.dumps([trial_record("NCT00000001", "DRUG")]),
        encoding="utf-8",
    )
    append_json.write_text(
        json.dumps(
            [
                trial_record("NCT00000001", "BIOLOGICAL"),
                trial_record("NCT00000002", "DRUG"),
            ]
        ),
        encoding="utf-8",
    )

    trials = load_ctgov_trials([initial_json, append_json])

    assert [
        trial["protocolSection"]["identificationModule"]["nctId"]
        for trial in trials
    ] == ["NCT00000001", "NCT00000002"]
    assert (
        trials[0]["protocolSection"]["armsInterventionsModule"]["interventions"][0][
            "type"
        ]
        == "BIOLOGICAL"
    )


def test_default_ctgov_input_files_use_newest_version_staged_files(tmp_path: Path):
    root = tmp_path / "data/trial_inputs/ctgov/input_trials"
    older = root / "version_01012026"
    newer = root / "version_02012026"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "ctgov_input.json").write_text("[]\n", encoding="utf-8")
    initial = newer / "01_initial_search_ctgov_input.json"
    append = newer / "02_pottr_append_ctgov_input.json"
    initial.write_text("[]\n", encoding="utf-8")
    append.write_text("[]\n", encoding="utf-8")

    assert default_ctgov_input_files(root) == [initial, append]
