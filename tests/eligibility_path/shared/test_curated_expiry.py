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


# --- restore ------------------------------------------------------------------

def test_restores_reappeared_expired_curation(tmp_path):
    curated = tmp_path / "eligibility_curations"
    expired = curated / "expired_trials"
    expired.mkdir(parents=True)
    (expired / "NCT00000003.py").write_text("rules = []\n", encoding="utf-8")

    result = move_expired_curations(
        curated_dir=curated,
        current_trial_ids={"NCT00000003"},  # back in the latest download
        pottr_exempt_ids=set(),
        normalize_trial_id=normalize_nct_id,
        trial_id_prefix="NCT",
    )

    assert result.restored == ["NCT00000003"]
    assert result.moved == []
    assert (curated / "NCT00000003.py").exists()
    assert not (expired / "NCT00000003.py").exists()


def test_restore_skips_when_active_copy_exists(tmp_path):
    curated = tmp_path / "eligibility_curations"
    _write_curation(curated, "NCT00000003")  # active copy
    expired = curated / "expired_trials"
    expired.mkdir(parents=True)
    (expired / "NCT00000003.py").write_text("stale", encoding="utf-8")

    result = move_expired_curations(
        curated_dir=curated,
        current_trial_ids={"NCT00000003"},
        pottr_exempt_ids=set(),
        normalize_trial_id=normalize_nct_id,
        trial_id_prefix="NCT",
    )

    assert result.restored == []
    # Active copy untouched; expired copy left where it was.
    assert (curated / "NCT00000003.py").read_text() == "rules = []\n"
    assert (expired / "NCT00000003.py").read_text() == "stale"


# --- safety guard -------------------------------------------------------------

def test_safety_guard_skips_expiry_when_fraction_exceeded(tmp_path):
    curated = tmp_path / "eligibility_curations"
    _write_curation(curated, "NCT00000001")
    _write_curation(curated, "NCT00000002")
    _write_curation(curated, "NCT00000003")

    # Empty current set -> all 3 would expire (100% > 50%) -> guarded.
    result = move_expired_curations(
        curated_dir=curated,
        current_trial_ids=set(),
        pottr_exempt_ids=set(),
        normalize_trial_id=normalize_nct_id,
        trial_id_prefix="NCT",
        max_expiry_fraction=0.5,
    )

    assert result.guarded is True
    assert result.moved == []
    for trial_id in ("NCT00000001", "NCT00000002", "NCT00000003"):
        assert (curated / f"{trial_id}.py").exists()
    assert not (curated / "expired_trials").exists()


def test_safety_guard_allows_expiry_below_threshold(tmp_path):
    curated = tmp_path / "eligibility_curations"
    _write_curation(curated, "NCT00000001")  # kept
    _write_curation(curated, "NCT00000002")  # kept
    _write_curation(curated, "NCT00000003")  # stale (1/3 ~ 33% < 50%)

    result = move_expired_curations(
        curated_dir=curated,
        current_trial_ids={"NCT00000001", "NCT00000002"},
        pottr_exempt_ids=set(),
        normalize_trial_id=normalize_nct_id,
        trial_id_prefix="NCT",
        max_expiry_fraction=0.5,
    )

    assert result.guarded is False
    assert result.moved == ["NCT00000003"]
    assert (curated / "expired_trials" / "NCT00000003.py").exists()


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


def test_expire_stale_curations_exempts_pottr_alias_canonical(tmp_path, monkeypatch):
    # A POTTR id (NCT03816254) is served by CT.gov as canonical NCT03783403, which
    # lands in the curated set. It is neither in the initial download nor in the
    # POTTR id list, so without alias-awareness it would be expired then
    # re-downloaded every run. The alias map must keep it exempt.
    config = workflow.RecursiveWorkflowConfig(
        export_date="29062026",
        repo_root=tmp_path,
        ctgov_input_root=tmp_path / "ctgov/input_trials",
        anzctr_input_root=tmp_path / "anzctr/input_trials",  # no merged -> anzctr skipped
        ctgov_curated_dir=tmp_path / "ctgov/eligibility_curations",
        anzctr_curated_dir=tmp_path / "anzctr/eligibility_curations",
    )
    version = config.ctgov_input_root / "version_29062026"
    _ctgov_merged(version, ["NCT00000001"])
    (version / workflow.CTGOV_POTTR_ALIAS_FILENAME).write_text(
        "requested_id\tcanonical_id\nNCT03816254\tNCT03783403\n", encoding="utf-8"
    )
    _write_curation(config.ctgov_curated_dir, "NCT00000001")  # downloaded -> keep
    _write_curation(config.ctgov_curated_dir, "NCT03783403")  # alias canonical -> keep
    _write_curation(config.ctgov_curated_dir, "NCT00000003")  # genuinely stale -> move

    monkeypatch.setattr(
        workflow, "load_pottr_trial_ids_best_effort", lambda *, registry: set()
    )

    workflow.expire_stale_curations(config)

    curated = config.ctgov_curated_dir
    assert (curated / "NCT00000001.py").exists()
    assert (curated / "NCT03783403.py").exists()  # exempt via alias, not churned
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
