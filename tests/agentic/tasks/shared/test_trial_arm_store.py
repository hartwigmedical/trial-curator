"""Shared cohort-identification module: the trial_arm_id slug (deterministic, reproducible) and the TrialArmStore
persistence for the central `trial_arms` registry (spec §6.1)."""
from __future__ import annotations

import csv
from datetime import date

from aus_trial_universe.agentic.tasks.shared.cohorts import trial_arm_id, trial_id_of
from aus_trial_universe.agentic.tasks.shared.schema import TrialArm
from aus_trial_universe.agentic.tasks.shared.store import TrialArmStore


def test_trial_arm_id_is_deterministic_and_recoverable():
    assert trial_arm_id("NCT04221035", "all") == "NCT04221035::all"
    assert trial_arm_id("ACTRN12626000505303", "intervention") == "ACTRN12626000505303::intervention"
    # reproducible: same inputs -> same id (no counter / sequence authority)
    assert trial_arm_id("NCT1", "Arm A") == trial_arm_id("NCT1", "Arm A")
    # trialId recovered from the slug (the part before the first ::)
    assert trial_id_of(trial_arm_id("NCT1", "Arm A: Drug X")) == "NCT1"


def test_trial_arm_id_normalizes_whitespace_and_separator():
    # internal whitespace collapsed; a stray "::" in a free-text arm label is neutralised so the separator is clean
    assert trial_arm_id("NCT1", "Arm  A\tX") == "NCT1::Arm A X"
    assert trial_arm_id("NCT1", "weird::label") == "NCT1::weird:label"
    assert trial_arm_id("", "anything") == ""   # no trialId -> no id


def test_store_round_trip_and_replace(tmp_path):
    s = TrialArmStore()
    s.set_trial_arms("NCT1", [
        TrialArm(trial_arm_id=trial_arm_id("NCT1", "Arm A"), trialId="NCT1", registry="ctgov",
                 arm="Arm A", arm_type="EXPERIMENTAL"),
        TrialArm(trial_arm_id=trial_arm_id("NCT1", "Arm B"), trialId="NCT1", registry="ctgov",
                 arm="Arm B", arm_type="ACTIVE_COMPARATOR")])
    s.set_trial_arms("ACTRN9", [
        TrialArm(trial_arm_id=trial_arm_id("ACTRN9", "intervention"), trialId="ACTRN9", registry="anzctr",
                 arm="intervention", arm_type="EXPERIMENTAL")])
    vdir = s.save(tmp_path, on=date(2026, 7, 25))
    assert vdir.name == "current_version" and (vdir / ".version").read_text().strip() == "2026-07-25"
    rows = list(csv.DictReader(open(vdir / "trial_arms.tsv"), delimiter="\t"))
    assert rows[0]["trial_arm_id"] == trial_arm_id("NCT1", "Arm A") and rows[0]["registry"] == "ctgov"

    loaded = TrialArmStore.load(tmp_path)
    assert loaded.has_trial("NCT1") and loaded.has_trial("ACTRN9")
    assert loaded.ids() == {trial_arm_id("NCT1", "Arm A"), trial_arm_id("NCT1", "Arm B"),
                            trial_arm_id("ACTRN9", "intervention")}

    # re-running a trial REPLACES its arms; other trials untouched
    loaded.set_trial_arms("NCT1", [TrialArm(trial_arm_id=trial_arm_id("NCT1", "all"), trialId="NCT1",
                                            registry="ctgov", arm="all", arm_type="EXPERIMENTAL")])
    loaded.save(tmp_path)
    reloaded = TrialArmStore.load(tmp_path)
    assert reloaded.ids() == {trial_arm_id("NCT1", "all"), trial_arm_id("ACTRN9", "intervention")}


def test_empty_load(tmp_path):
    assert TrialArmStore.load(tmp_path).ids() == set()
