import json
from pathlib import Path

from aus_trial_universe.drug_utility_path.trial_drug_curation.pipeline import (
    CurationRunConfig,
    run_trial_drug_curation,
)
from aus_trial_universe.drug_utility_path.trial_drug_curation.utils.schema import TSV_COLUMNS


def test_pipeline_writes_one_output_and_skipped_report_from_selected_ctgov_records(
    tmp_path,
):
    ctgov_input = tmp_path / "ctgov_input.json"
    ctgov_input.write_text(
        json.dumps(
            [
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": "NCT00000001"},
                        "armsInterventionsModule": {
                            "interventions": [{"name": "SELECTED_DRUG"}]
                        },
                    }
                },
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": "NCT00000002"},
                        "armsInterventionsModule": {
                            "interventions": [{"name": "UNREQUESTED_DRUG"}]
                        },
                    }
                },
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "nested" / "curation.tsv"
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def curate_tsv(self, *, user_prompt):
            calls.append((self.kwargs, user_prompt))
            return _tsv(
                [
                    "NCT00000001",
                    "SELECTED_DRUG",
                    "SELECTED_DRUG",
                    "Experimental",
                    "intervention",
                    "Cancer",
                    "CANCER",
                    "selected drug",
                    "",
                    "True",
                    "",
                    "",
                    "",
                ]
            )

    messages = []
    result = run_trial_drug_curation(
        CurationRunConfig(
            trial_ids=["NCT00000001", "NCT99999999", "bad-id"],
            output_path=output_path,
            ctgov_input=ctgov_input,
            enrich_oncotree=False,
        ),
        progress=messages.append,
        client_factory=FakeClient,
    )

    assert result.output_path == output_path
    assert output_path.read_text(encoding="utf-8").startswith("trialId\tdrugName")
    assert result.skipped_path is not None
    assert result.skipped_path.parent == output_path.parent
    skipped_text = result.skipped_path.read_text(encoding="utf-8")
    assert "BAD-ID\tunknown\tunrecognised_trial_id" in skipped_text
    assert "NCT99999999\tctgov\tnot_in_local_input" in skipped_text
    assert len(calls) == 1
    assert "SELECTED_DRUG" in calls[0][1]
    assert "UNREQUESTED_DRUG" not in calls[0][1]
    assert any(
        "OpenAI request trial records: CTGOV=1 [NCT00000001]." in message
        for message in messages
    )
    assert any("field-level progress is not available" in m for m in messages)


def test_pipeline_prompt_only_skips_client_and_output_write(tmp_path):
    ctgov_input = tmp_path / "ctgov_input.json"
    ctgov_input.write_text(
        json.dumps(
            [
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": "NCT00000001"},
                        "armsInterventionsModule": {
                            "interventions": [{"name": "PROMPT_ONLY_DRUG"}]
                        },
                    }
                }
            ]
        ),
        encoding="utf-8",
    )

    def fail_client(**kwargs):
        raise AssertionError("prompt-only mode should not create an API client")

    result = run_trial_drug_curation(
        CurationRunConfig(
            trial_ids=["NCT00000001"],
            output_dir=tmp_path,
            ctgov_input=ctgov_input,
            prompt_only=True,
        ),
        client_factory=fail_client,
    )

    assert result.output_path is None
    assert result.tsv is None
    assert "PROMPT_ONLY_DRUG" in result.prompt
    assert not list(Path(tmp_path).glob("trial_drug_curation_*.tsv"))


def test_pipeline_batches_api_calls_and_merges_one_output_tsv(tmp_path):
    ctgov_input = tmp_path / "ctgov_input.json"
    ctgov_input.write_text(
        json.dumps(
            [
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": "NCT00000001"},
                        "armsInterventionsModule": {
                            "interventions": [{"name": "DRUG_ONE"}]
                        },
                    }
                },
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": "NCT00000002"},
                        "armsInterventionsModule": {
                            "interventions": [{"name": "DRUG_TWO"}]
                        },
                    }
                },
            ]
        ),
        encoding="utf-8",
    )
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def curate_tsv(self, *, user_prompt):
            calls.append(user_prompt)
            if "NCT00000001" in user_prompt:
                return _tsv(
                    [
                        "NCT00000001",
                        "DRUG_ONE",
                        "DRUG_ONE",
                        "Experimental",
                        "intervention",
                        "Cancer",
                        "CANCER",
                        "",
                        "",
                        "True",
                        "",
                        "",
                        "",
                    ]
                )
            return _tsv(
                [
                    "NCT00000002",
                    "DRUG_TWO",
                    "DRUG_TWO",
                    "Experimental",
                    "intervention",
                    "Cancer",
                    "CANCER",
                    "",
                    "",
                    "True",
                    "",
                    "",
                    "",
                ]
            )

    result = run_trial_drug_curation(
        CurationRunConfig(
            trial_ids=["NCT00000001", "NCT00000002"],
            output_dir=tmp_path,
            ctgov_input=ctgov_input,
            batch_size=1,
            enrich_oncotree=False,
        ),
        client_factory=FakeClient,
    )

    assert len(calls) == 2
    assert result.output_path is not None
    output_text = result.output_path.read_text(encoding="utf-8")
    assert output_text.count("trialId\tdrugName") == 1
    assert "NCT00000001\tDRUG_ONE" in output_text
    assert "NCT00000002\tDRUG_TWO" in output_text


def _tsv(row):
    return "\t".join(TSV_COLUMNS) + "\n" + "\t".join(row) + "\n"
