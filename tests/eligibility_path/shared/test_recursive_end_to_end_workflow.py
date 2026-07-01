from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aus_trial_universe.eligibility_path.shared.workflow import (
    recursive_end_to_end_workflow as workflow,
)


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]\n", encoding="utf-8")
    return path


def test_split_missing_trial_ids_groups_supported_registries():
    missing = pd.DataFrame(
        {
            "trial_id": ["NCT00000001", "ACTRN12624000000000", "OTHER1"],
            "registry": ["ctgov", "anzctr", ""],
        }
    )

    assert workflow.split_missing_trial_ids(missing) == {
        "ctgov": ["NCT00000001"],
        "anzctr": ["ACTRN12624000000000"],
    }


def test_recursive_workflow_downloads_pottr_append_until_missing_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = workflow.RecursiveWorkflowConfig(
        export_date="26062026",
        repo_root=tmp_path,
        python_bin="python",
        ctgov_input_root=tmp_path / "data/trial_inputs/ctgov/input_trials",
        ctgov_state_dir=tmp_path / "data/trial_inputs/ctgov/download_state",
        anzctr_input_root=tmp_path / "data/trial_inputs/anzctr/input_trials",
        ctgov_extracted_dir=tmp_path / "data/trial_inputs/ctgov/extracted_trials",
        anzctr_extracted_csv=tmp_path
        / "data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv",
        ctgov_curated_dir=tmp_path / "data/trial_inputs/ctgov/eligibility_curations",
        anzctr_curated_dir=tmp_path / "data/trial_inputs/anzctr/eligibility_curations",
        ctgov_intermediate_dir=tmp_path / "data/eligibility_path/exports/intermediates/ctgov",
        anzctr_intermediate_dir=tmp_path / "data/eligibility_path/exports/intermediates/anzctr",
        eligibility_data_dir=tmp_path / "data/eligibility_path",
    )
    touch(
        config.ctgov_input_root
        / "version_26062026"
        / workflow.CTGOV_INITIAL_FILENAME
    )
    touch(
        config.anzctr_input_root
        / "version_26062026"
        / workflow.ANZCTR_INITIAL_FILENAME
    )
    missing_frames = iter(
        [
            pd.DataFrame(
                {
                    "trial_id": ["NCT00000001", "ACTRN12624000000000"],
                    "registry": ["ctgov", "anzctr"],
                }
            ),
            pd.DataFrame(columns=["trial_id", "registry"]),
        ]
    )
    monkeypatch.setattr(
        workflow,
        "run_combined_export_and_get_missing",
        lambda _config: next(missing_frames),
    )
    commands: list[list[str]] = []

    workflow.run_recursive_workflow(
        config,
        run_command=lambda command: commands.append(list(command)),
        initial_downloads=False,
    )

    command_text = "\n".join(" ".join(command) for command in commands)
    assert "i_api_download --trial_ids" in command_text
    assert "i_download_trials --trial_ids" in command_text
    assert "--output_dir" in command_text
    assert "iii_pydantic_curator_batch_run" in command_text
    assert "iv_pydantic_curator_batch_run" in command_text
    assert "--overwrite_existing" not in command_text
    assert "02_pottr_append" not in command_text
    assert command_text.count("ii_extract_fields") == 2
    assert command_text.count("iii_extract_drugs") == 2


def test_fresh_recursive_workflow_downloads_initial_inputs_then_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = workflow.RecursiveWorkflowConfig(
        export_date="26062026",
        repo_root=tmp_path,
        python_bin="python",
        ctgov_input_root=tmp_path / "ctgov/input_trials",
        ctgov_state_dir=tmp_path / "ctgov/state",
        anzctr_input_root=tmp_path / "anzctr/input_trials",
        ctgov_curated_dir=tmp_path / "ctgov/eligibility_curations",
        anzctr_curated_dir=tmp_path / "anzctr/eligibility_curations",
    )
    missing_frames = iter(
        [
            pd.DataFrame(
                {
                    "trial_id": ["ACTRN12624000000000"],
                    "registry": ["anzctr"],
                }
            ),
            pd.DataFrame(columns=["trial_id", "registry"]),
        ]
    )
    monkeypatch.setattr(
        workflow,
        "run_combined_export_and_get_missing",
        lambda _config: next(missing_frames),
    )
    # Fresh-download runs also refresh the POTTR eligibility snapshot; stub it so the test
    # stays hermetic (no network) and assert it fired.
    snapshot_calls: list = []
    monkeypatch.setattr(
        workflow,
        "snapshot_pottr_eligibility",
        lambda _config: snapshot_calls.append(_config),
    )

    def capture_command(command):
        commands.append(list(command))
        command_text = " ".join(command)
        if "i_api_download" in command_text:
            touch(
                config.ctgov_input_root
                / "version_26062026"
                / workflow.CTGOV_INITIAL_FILENAME
            )
        if "i_download_trials" in command_text:
            touch(
                config.anzctr_input_root
                / "version_26062026"
                / workflow.ANZCTR_INITIAL_FILENAME
            )

    commands: list[list[str]] = []

    workflow.run_recursive_workflow(
        config,
        run_command=capture_command,
        initial_downloads=True,
    )

    assert len(snapshot_calls) == 1

    command_text = "\n".join(" ".join(command) for command in commands)
    assert "i_api_download --all" in command_text
    assert "i_download_trials --initial_search" in command_text
    assert "iii_pydantic_curator_batch_run" in command_text
    assert "iv_pydantic_curator_batch_run" in command_text
    assert "version_26062026" in command_text
    assert "i_download_trials --initial_search" in " ".join(commands[0])
    assert "i_api_download --all" in " ".join(commands[1])
    anzctr_initial_command = next(
        command
        for command in commands
        if command[2].endswith(".i_download_trials") and "--initial_search" in command
    )
    assert "--timeout_ms" in anzctr_initial_command
    assert "--search_retries" in anzctr_initial_command


def test_recursive_workflow_can_use_latest_existing_input_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = workflow.RecursiveWorkflowConfig(
        export_date="26062026",
        repo_root=tmp_path,
        python_bin="python",
        ctgov_input_root=tmp_path / "ctgov/input_trials",
        anzctr_input_root=tmp_path / "anzctr/input_trials",
        use_latest_input_version=True,
    )
    touch(config.ctgov_input_root / "version_24062026" / workflow.CTGOV_INITIAL_FILENAME)
    touch(config.ctgov_input_root / "version_25062026" / workflow.CTGOV_INITIAL_FILENAME)
    touch(config.anzctr_input_root / "version_24062026" / workflow.ANZCTR_INITIAL_FILENAME)
    touch(config.anzctr_input_root / "version_25062026" / workflow.ANZCTR_INITIAL_FILENAME)
    missing_frames = iter(
        [
            pd.DataFrame(
                {
                    "trial_id": ["ACTRN12624000000000"],
                    "registry": ["anzctr"],
                }
            ),
            pd.DataFrame(columns=["trial_id", "registry"]),
        ]
    )
    monkeypatch.setattr(
        workflow,
        "run_combined_export_and_get_missing",
        lambda _config: next(missing_frames),
    )
    # run-all (reprocess, no download) must NOT refresh the POTTR snapshot — keeps it drift-free.
    snapshot_calls: list = []
    monkeypatch.setattr(
        workflow,
        "snapshot_pottr_eligibility",
        lambda _config: snapshot_calls.append(_config),
    )
    commands: list[list[str]] = []

    workflow.run_recursive_workflow(
        config,
        run_command=lambda command: commands.append(list(command)),
        initial_downloads=False,
    )

    command_text = "\n".join(" ".join(command) for command in commands)
    assert "version_25062026" in command_text
    assert "version_24062026" not in command_text
    assert snapshot_calls == []

    anzctr_append_command = next(
        command for command in commands if command[2].endswith(".i_download_trials")
    )
    assert "--output_dir" in anzctr_append_command
    assert str(config.anzctr_input_root / "version_25062026") in anzctr_append_command
    assert "--export_date" not in anzctr_append_command


def test_llm_review_flag_is_passed_to_anzctr_drug_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = workflow.RecursiveWorkflowConfig(
        export_date="26062026",
        repo_root=tmp_path,
        python_bin="python",
        ctgov_input_root=tmp_path / "ctgov/input_trials",
        anzctr_input_root=tmp_path / "anzctr/input_trials",
        llm_review=True,
        llm_limit=5,
        llm_model="test-model",
    )
    touch(config.ctgov_input_root / "version_26062026" / workflow.CTGOV_INITIAL_FILENAME)
    touch(config.anzctr_input_root / "version_26062026" / workflow.ANZCTR_INITIAL_FILENAME)
    monkeypatch.setattr(
        workflow,
        "run_combined_export_and_get_missing",
        lambda _config: pd.DataFrame(columns=["trial_id", "registry"]),
    )
    commands: list[list[str]] = []

    workflow.run_recursive_workflow(
        config,
        run_command=lambda command: commands.append(list(command)),
        initial_downloads=False,
    )

    drug_command = next(
        command for command in commands if "iii_extract_drugs" in " ".join(command)
    )
    assert "--llm_review" in drug_command
    assert "--llm_limit" in drug_command
    assert "5" in drug_command
    assert "--llm_model" in drug_command
    assert "test-model" in drug_command


def test_recursive_workflow_converges_without_raising_on_unresolvable_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = workflow.RecursiveWorkflowConfig(
        export_date="26062026",
        repo_root=tmp_path,
        python_bin="python",
        ctgov_input_root=tmp_path / "ctgov/input_trials",
        anzctr_input_root=tmp_path / "anzctr/input_trials",
        max_iterations=2,
    )
    touch(config.ctgov_input_root / "version_26062026" / workflow.CTGOV_INITIAL_FILENAME)
    touch(config.anzctr_input_root / "version_26062026" / workflow.ANZCTR_INITIAL_FILENAME)
    same_missing = pd.DataFrame(
        {"trial_id": ["NCT00000001"], "registry": ["ctgov"]}
    )
    monkeypatch.setattr(
        workflow,
        "run_combined_export_and_get_missing",
        lambda _config: same_missing,
    )

    # POTTR trials that stay missing after being downloaded (the pipeline filters
    # exclude them) must NOT crash the run: the workflow converges once no new
    # trials appear and returns the unresolved set.
    result = workflow.run_recursive_workflow(
        config,
        run_command=lambda _command: None,
        initial_downloads=False,
    )

    assert result["trial_id"].tolist() == ["NCT00000001"]
