from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aus_trial_universe.ctgov.drug_ontology.shared.paths import COMPARISON_RUNS_DIR
from aus_trial_universe.ctgov.drug_ontology.shared.run_archive import (
    EXACT_MODE,
    LATEST_RUN_FILENAME,
    MANIFEST_FILENAME,
    UNORDERED_TSV_MODE,
    GeneratedFile,
    compare_current_to_manifest,
    create_run_snapshot,
    default_archive_dir,
    latest_manifest_path,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_default_archive_dir_uses_comparison_runs_root():
    assert default_archive_dir() == COMPARISON_RUNS_DIR


def test_create_run_snapshot_copies_files_and_updates_latest_pointer(tmp_path: Path):
    data_dir = tmp_path / "drug_ontology"
    atc_tree = write(data_dir / "processed_inputs/pipeline/ATC/version_25042026/atc_tree.tsv", "code\tlabel\nA\tAlpha\n")
    input_mapping = write(data_dir / "pipeline_outputs/input_drugs_to_rxnorm_mapping_05062026.tsv", "drug\trxcui\nA\t1\n")
    intervention = write(data_dir / "pipeline_outputs/ctgov_drug_intervention_classification_summary.tsv", "nct_id\tclass\nN1\tA\n")
    trial = write(data_dir / "pipeline_outputs/ctgov_trial_drug_classification_summary.tsv", "nct_id\tclasses\nN1\tA\n")

    manifest = create_run_snapshot(
        [
            GeneratedFile(atc_tree),
            GeneratedFile(input_mapping),
            GeneratedFile(intervention),
            GeneratedFile(trial),
        ],
        data_dir=data_dir,
        run_at=datetime(2026, 6, 5, 14, 30, 0, tzinfo=timezone.utc),
    )

    run_dir = data_dir / "comparison_runs/run_20260605_143000"
    latest = json.loads((data_dir / "comparison_runs" / LATEST_RUN_FILENAME).read_text(encoding="utf-8"))

    assert manifest["run_id"] == "run_20260605_143000"
    assert latest["manifest_path"] == f"run_20260605_143000/{MANIFEST_FILENAME}"
    assert (run_dir / "processed_inputs/pipeline/ATC/version_25042026/atc_tree.tsv").read_text(encoding="utf-8") == atc_tree.read_text(encoding="utf-8")
    assert len(manifest["files"]) == 4
    assert {entry["comparison_mode"] for entry in manifest["files"]} == {EXACT_MODE}


def test_create_run_snapshot_allocates_unique_folder_for_same_second(tmp_path: Path):
    data_dir = tmp_path / "drug_ontology"
    output = write(data_dir / "pipeline_outputs/file.tsv", "a\n1\n")
    run_at = datetime(2026, 6, 5, 14, 30, 0, tzinfo=timezone.utc)

    first = create_run_snapshot([GeneratedFile(output)], data_dir=data_dir, run_at=run_at)
    second = create_run_snapshot([GeneratedFile(output)], data_dir=data_dir, run_at=run_at)

    assert first["run_id"] == "run_20260605_143000"
    assert second["run_id"] == "run_20260605_143000_2"


def test_compare_current_to_manifest_detects_exact_match_and_change(tmp_path: Path):
    data_dir = tmp_path / "drug_ontology"
    output = write(data_dir / "pipeline_outputs/file.tsv", "a\n1\n")
    manifest = create_run_snapshot([GeneratedFile(output)], data_dir=data_dir)
    manifest_path = data_dir / "comparison_runs" / manifest["run_id"] / MANIFEST_FILENAME

    assert compare_current_to_manifest(manifest_path, data_dir=data_dir)[0].matches

    output.write_text("a\n2\n", encoding="utf-8")
    result = compare_current_to_manifest(manifest_path, data_dir=data_dir)[0]

    assert not result.matches
    assert "sha256 differs" in result.reason


def test_compare_current_to_manifest_can_ignore_tsv_row_order(tmp_path: Path):
    data_dir = tmp_path / "drug_ontology"
    output = write(data_dir / "pipeline_outputs/file.tsv", "a\tb\n1\tx\n2\ty\n")
    manifest = create_run_snapshot(
        [GeneratedFile(output, comparison_mode=UNORDERED_TSV_MODE)],
        data_dir=data_dir,
    )
    manifest_path = data_dir / "comparison_runs" / manifest["run_id"] / MANIFEST_FILENAME

    output.write_text("a\tb\n2\ty\n1\tx\n", encoding="utf-8")

    assert compare_current_to_manifest(manifest_path, data_dir=data_dir)[0].matches


def test_compare_current_to_manifest_reports_missing_current_file(tmp_path: Path):
    data_dir = tmp_path / "drug_ontology"
    output = write(data_dir / "pipeline_outputs/file.tsv", "a\n1\n")
    manifest = create_run_snapshot([GeneratedFile(output)], data_dir=data_dir)
    manifest_path = data_dir / "comparison_runs" / manifest["run_id"] / MANIFEST_FILENAME

    output.unlink()
    result = compare_current_to_manifest(manifest_path, data_dir=data_dir)[0]

    assert not result.matches
    assert "current file missing" in result.reason


def test_snapshot_rejects_files_outside_data_dir(tmp_path: Path):
    with pytest.raises(ValueError, match="must live under"):
        create_run_snapshot(
            [GeneratedFile(write(tmp_path / "outside.tsv", "a\n1\n"))],
            data_dir=tmp_path / "drug_ontology",
        )


def test_latest_manifest_path_uses_latest_pointer(tmp_path: Path):
    data_dir = tmp_path / "drug_ontology"
    output = write(data_dir / "pipeline_outputs/file.tsv", "a\n1\n")
    manifest = create_run_snapshot([GeneratedFile(output)], data_dir=data_dir)

    assert latest_manifest_path(data_dir / "comparison_runs") == (
        data_dir / "comparison_runs" / manifest["run_id"] / MANIFEST_FILENAME
    )
