"""Tests for moving curated `.py` files no longer in the latest download."""

from __future__ import annotations

import json

import pytest

from aus_trial_universe.eligibility_path.shared.cohorts import normalize_nct_id
from aus_trial_universe.eligibility_path.shared.curated_expiry import (
    move_expired_curations,
)
from aus_trial_universe.eligibility_path.shared.workflow import (
    recursive_end_to_end_workflow as workflow,
)


def _write_curation(curated_dir, trial_id):
    curated_dir.mkdir(parents=True, exist_ok=True)
    path = curated_dir / f"{trial_id}.py"
    path.write_text("rules = []\n", encoding="utf-8")
    return path


# --- core ---------------------------------------------------------------------

def test_move_expired_curations_moves_only_absent_non_pottr(tmp_path):
    curated = tmp_path / "eligibility_curations"
    _write_curation(curated, "NCT00000001")  # in latest download -> keep
    _write_curation(curated, "NCT00000002")  # POTTR-exempt -> keep
    _write_curation(curated, "NCT00000003")  # stale -> move

    result = move_expired_curations(
        curated_dir=curated,
        current_trial_ids={"NCT00000001"},
        pottr_exempt_ids={"NCT00000002"},
        normalize_trial_id=normalize_nct_id,
        trial_id_prefix="NCT",
    )

    assert result.moved == ["NCT00000003"]
    assert result.retained_pottr == ["NCT00000002"]
    assert (curated / "NCT00000001.py").exists()
    assert (curated / "NCT00000002.py").exists()
    assert not (curated / "NCT00000003.py").exists()
    # Moved, not deleted; lands in the expired_trials subfolder.
    assert (curated / "expired_trials" / "NCT00000003.py").exists()


def test_move_expired_curations_overwrites_existing_expired_copy(tmp_path):
    curated = tmp_path / "eligibility_curations"
    _write_curation(curated, "NCT00000009")
    (curated / "expired_trials").mkdir(parents=True)
    (curated / "expired_trials" / "NCT00000009.py").write_text("old", encoding="utf-8")

    result = move_expired_curations(
        curated_dir=curated,
        current_trial_ids=set(),
        pottr_exempt_ids=set(),
        normalize_trial_id=normalize_nct_id,
        trial_id_prefix="NCT",
    )

    assert result.moved == ["NCT00000009"]
    assert (curated / "expired_trials" / "NCT00000009.py").read_text() == "rules = []\n"


# --- workflow hook ------------------------------------------------------------

def _ctgov_merged(version_dir, nct_ids):
    version_dir.mkdir(parents=True, exist_ok=True)
    studies = [
        {"protocolSection": {"identificationModule": {"nctId": nct_id}}}
        for nct_id in nct_ids
    ]
    path = version_dir / workflow.CTGOV_MERGED_FILENAME
    path.write_text(json.dumps(studies), encoding="utf-8")
    return path


def test_expire_stale_curations_uses_merged_and_exempts_pottr(tmp_path, monkeypatch):
    config = workflow.RecursiveWorkflowConfig(
        export_date="26062026",
        repo_root=tmp_path,
        ctgov_input_root=tmp_path / "ctgov/input_trials",
        anzctr_input_root=tmp_path / "anzctr/input_trials",  # no merged -> anzctr skipped
        ctgov_curated_dir=tmp_path / "ctgov/eligibility_curations",
        anzctr_curated_dir=tmp_path / "anzctr/eligibility_curations",
    )
    _ctgov_merged(config.ctgov_input_root / "version_26062026", ["NCT00000001"])
    _write_curation(config.ctgov_curated_dir, "NCT00000001")  # downloaded -> keep
    _write_curation(config.ctgov_curated_dir, "NCT00000002")  # POTTR -> keep
    _write_curation(config.ctgov_curated_dir, "NCT00000003")  # stale -> move

    monkeypatch.setattr(
        workflow,
        "load_pottr_trial_ids_best_effort",
        lambda *, registry: {"NCT00000002"} if registry == "ctgov" else set(),
    )

    workflow.expire_stale_curations(config)

    curated = config.ctgov_curated_dir
    assert (curated / "NCT00000001.py").exists()
    assert (curated / "NCT00000002.py").exists()
    assert not (curated / "NCT00000003.py").exists()
    assert (curated / "expired_trials" / "NCT00000003.py").exists()


def test_expire_stale_curations_skips_when_no_merged_input(tmp_path, monkeypatch):
    config = workflow.RecursiveWorkflowConfig(
        export_date="26062026",
        repo_root=tmp_path,
        ctgov_input_root=tmp_path / "ctgov/input_trials",  # no merged file written
        anzctr_input_root=tmp_path / "anzctr/input_trials",
        ctgov_curated_dir=tmp_path / "ctgov/eligibility_curations",
        anzctr_curated_dir=tmp_path / "anzctr/eligibility_curations",
    )
    _write_curation(config.ctgov_curated_dir, "NCT00000003")

    def _boom(*, registry):  # must not be reached when merged input is absent
        raise AssertionError("POTTR loader should not run without a merged input")

    monkeypatch.setattr(workflow, "load_pottr_trial_ids_best_effort", _boom)

    workflow.expire_stale_curations(config)

    # Nothing moved; the curation is left in place.
    assert (config.ctgov_curated_dir / "NCT00000003.py").exists()
    assert not (config.ctgov_curated_dir / "expired_trials").exists()
