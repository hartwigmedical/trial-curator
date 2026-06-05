from pathlib import Path

import pytest

from aus_trial_universe.ctgov.drug_ontology.shared.paths import (
    ANALYSIS_PROCESSED_INPUTS_DIR,
    COMPARISON_RUNS_DIR,
    PIPELINE_PROCESSED_INPUTS_DIR,
    latest_version_dir,
    parse_version_sort_key,
)


def test_latest_version_dir_uses_calendar_order_for_date_suffixes(tmp_path: Path):
    root = tmp_path / "POTTR"
    (root / "version_29052026").mkdir(parents=True)
    (root / "version_01062026").mkdir()
    (root / "version_legacy").mkdir()

    assert latest_version_dir(root) == root / "version_01062026"


def test_latest_version_dir_uses_numeric_order_for_numeric_suffixes(tmp_path: Path):
    root = tmp_path / "ChEMBL"
    (root / "version_9").mkdir(parents=True)
    (root / "version_36").mkdir()

    assert latest_version_dir(root) == root / "version_36"


def test_latest_version_dir_ignores_non_sortable_versions(tmp_path: Path):
    root = tmp_path / "ATC"
    (root / "version_legacy").mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="No sortable version"):
        latest_version_dir(root)


def test_parse_version_sort_key_rejects_invalid_dates():
    assert parse_version_sort_key("version_31022026") is None
    assert parse_version_sort_key("not_a_version") is None


def test_processed_input_roots_are_split_by_workflow():
    assert PIPELINE_PROCESSED_INPUTS_DIR == Path("data/ctgov/drug_ontology/processed_inputs/pipeline")
    assert ANALYSIS_PROCESSED_INPUTS_DIR == Path("data/ctgov/drug_ontology/processed_inputs/analysis")
    assert COMPARISON_RUNS_DIR == Path("data/ctgov/drug_ontology/comparison_runs")
