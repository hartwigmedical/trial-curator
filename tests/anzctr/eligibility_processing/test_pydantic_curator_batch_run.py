from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility import (
    iii_pydantic_curator_batch_run as batch,
)


def test_build_eligibility_criteria_text_uses_curator_headers_and_omits_empty_values():
    assert batch.build_eligibility_criteria_text(
        "Adults with metastatic cancer",
        "No exclusion criteria",
    ) == "Inclusion Criteria:\nAdults with metastatic cancer"

    assert batch.build_eligibility_criteria_text(
        "N/A",
        "Prior hypersensitivity to study drug",
    ) == "Exclusion Criteria:\nPrior hypersensitivity to study drug"

    assert batch.build_eligibility_criteria_text("None", "Not applicable") == ""


def test_load_anzctr_trials_normalises_actrn_and_builds_eligibility_text(tmp_path: Path):
    input_csv = tmp_path / "anzctr_field_extractions.csv"
    pd.DataFrame(
        {
            "ACTRN": [12605000003673, " actrn12605000025639 "],
            "INCLUSIVE CRITERIA": ["Include A", "Include B"],
            "EXCLUSIVE CRITERIA": ["No exclusion criteria", "Exclude B"],
        }
    ).to_csv(input_csv, index=False)

    trials = batch.load_anzctr_trials(input_csv)

    assert trials == [
        batch.AnzctrTrial(
            trial_id="ACTRN12605000003673",
            eligibility_criteria="Inclusion Criteria:\nInclude A",
        ),
        batch.AnzctrTrial(
            trial_id="ACTRN12605000025639",
            eligibility_criteria=(
                "Inclusion Criteria:\nInclude B\n\n"
                "Exclusion Criteria:\nExclude B"
            ),
        ),
    ]


def test_load_anzctr_trials_requires_expected_columns(tmp_path: Path):
    input_csv = tmp_path / "missing_columns.csv"
    pd.DataFrame({"ACTRN": ["ACTRN1"]}).to_csv(input_csv, index=False)

    with pytest.raises(ValueError, match="INCLUSIVE CRITERIA, EXCLUSIVE CRITERIA"):
        batch.load_anzctr_trials(input_csv)


def test_process_single_trial_calls_pydantic_curator(monkeypatch, tmp_path: Path):
    calls = {}
    fake_client = object()

    monkeypatch.setattr(batch.curator, "OpenaiClient", lambda: fake_client)

    def fake_rules_prep_workflow(eligibility_criteria, client):
        calls["eligibility_criteria"] = eligibility_criteria
        calls["prep_client"] = client
        return [{"input_rule": "Adults with cancer", "exclude": False}]

    def fake_pydantic_curator_workflow(criterion, client):
        calls["criterion"] = criterion
        calls["curator_client"] = client
        return "curated-rule"

    def fake_write_output_py(output_filepath, curated_rules):
        calls["output_filepath"] = output_filepath
        calls["curated_rules"] = curated_rules
        Path(output_filepath).write_text("rules = []\n")

    monkeypatch.setattr(batch.curator, "llm_rules_prep_workflow", fake_rules_prep_workflow)
    monkeypatch.setattr(
        batch.curator,
        "pydantic_curator_workflow",
        fake_pydantic_curator_workflow,
    )
    monkeypatch.setattr(batch.curator, "_write_output_py", fake_write_output_py)

    status, trial_id = batch.process_single_trial(
        batch.AnzctrTrial(
            trial_id="ACTRN12605000003673",
            eligibility_criteria="Inclusion Criteria:\nAdults with cancer",
        ),
        tmp_path,
        overwrite_existing=False,
    )

    assert (status, trial_id) == ("completed", "ACTRN12605000003673")
    assert calls == {
        "eligibility_criteria": "Inclusion Criteria:\nAdults with cancer",
        "prep_client": fake_client,
        "criterion": {"input_rule": "Adults with cancer", "exclude": False},
        "curator_client": fake_client,
        "output_filepath": tmp_path / "ACTRN12605000003673.py",
        "curated_rules": ["curated-rule"],
    }


def test_process_single_trial_skips_existing_output_before_creating_client(
    monkeypatch,
    tmp_path: Path,
):
    output_filepath = tmp_path / "ACTRN12605000003673.py"
    output_filepath.write_text("rules = []\n")

    def fail_if_called():
        raise AssertionError("OpenAI client should not be created for skipped files")

    monkeypatch.setattr(batch.curator, "OpenaiClient", fail_if_called)

    assert batch.process_single_trial(
        batch.AnzctrTrial(
            trial_id="ACTRN12605000003673",
            eligibility_criteria="Inclusion Criteria:\nAdults with cancer",
        ),
        tmp_path,
        overwrite_existing=False,
    ) == ("skipped", "ACTRN12605000003673")


def test_run_batch_filters_trial_id_and_counts_statuses(monkeypatch, tmp_path: Path):
    input_csv = tmp_path / "anzctr_field_extractions.csv"
    output_dir = tmp_path / "curated"
    pd.DataFrame(
        {
            "ACTRN": ["ACTRN1", "ACTRN2"],
            "INCLUSIVE CRITERIA": ["Include 1", "Include 2"],
            "EXCLUSIVE CRITERIA": ["Exclude 1", "Exclude 2"],
        }
    ).to_csv(input_csv, index=False)

    processed_trial_ids = []

    def fake_process_single_trial(trial, out_dir, overwrite_existing):
        processed_trial_ids.append(trial.trial_id)
        assert out_dir == output_dir
        assert overwrite_existing is True
        return ("completed", trial.trial_id)

    monkeypatch.setattr(batch, "process_single_trial", fake_process_single_trial)

    summary = batch.run_batch(
        input_csv=input_csv,
        output_dir=output_dir,
        trial_id="2",
        overwrite_existing=True,
        max_workers=1,
    )

    assert processed_trial_ids == ["ACTRN2"]
    assert summary == {"total": 1, "completed": 1, "skipped": 0, "failed": 0}
