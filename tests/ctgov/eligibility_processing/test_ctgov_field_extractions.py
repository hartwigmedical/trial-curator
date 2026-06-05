from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from aus_trial_universe.ctgov.eligibility.i_download_trials_and_extract_eligibility.ii_extract_fields import (
    extract_fields_to_outputs,
)


def trial_record(nct_id: str, intervention_type: str) -> dict:
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": f"Trial {nct_id}",
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


def test_extract_fields_to_outputs_writes_curator_csv_and_review_workbook(tmp_path: Path):
    input_json = tmp_path / "ctgov_trials_delta.json"
    input_json.write_text(
        json.dumps(
            [
                trial_record("NCT00000001", "DRUG"),
                trial_record("NCT00000002", "PROCEDURE"),
            ]
        ),
        encoding="utf-8",
    )

    xlsx_path, csv_path = extract_fields_to_outputs(input_json, tmp_path / "trials")

    assert xlsx_path.exists()
    assert csv_path.exists()

    csv_df = pd.read_csv(csv_path)
    assert csv_df["nctId"].tolist() == ["NCT00000001"]

    workbook_df = pd.read_excel(xlsx_path, sheet_name="general")
    assert workbook_df["nctId"].tolist() == ["NCT00000001"]
