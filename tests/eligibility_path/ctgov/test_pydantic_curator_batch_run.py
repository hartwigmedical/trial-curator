from __future__ import annotations

from pathlib import Path

from aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility import (
    iii_pydantic_curator_batch_run as batch,
)


def ctgov_trial(nct_id: str) -> dict:
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
            },
            "eligibilityModule": {
                "eligibilityCriteria": "Inclusion Criteria:\nAdults with cancer",
            },
        },
    }


def test_process_single_trial_skips_existing_output_before_creating_client(
    monkeypatch,
    tmp_path: Path,
):
    output_filepath = tmp_path / "NCT00000001.py"
    output_filepath.write_text("rules = []\n", encoding="utf-8")

    def fail_if_called():
        raise AssertionError("OpenAI client should not be created for skipped files")

    monkeypatch.setattr(batch.curator, "OpenaiClient", fail_if_called)

    assert batch.process_single_trial(
        ctgov_trial("NCT00000001"),
        tmp_path,
        overwrite_existing=False,
    ) == ("skipped", "NCT00000001")
