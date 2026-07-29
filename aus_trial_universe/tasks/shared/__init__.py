"""Shared, path-neutral building blocks used by BOTH the eligibility and the drug-utility paths.

Currently: trial-cohort (arm) identification — so a trial's `(trialId, arm)` split, and the `trial_arm_id`
derived from it, is IDENTICAL across the two paths (the join key must match). Neither path imports arm
identification from the other; both import it from here.
"""
from aus_trial_universe.tasks.shared.cohorts import (
    Cohort,
    anzctr_regimes,
    extract_anzctr_drugs,
    resolve_cohorts,
    trial_arm_id,
    trial_id_of,
)
from aus_trial_universe.tasks.shared.schema import TRIAL_ARMS_COLUMNS, TrialArm
from aus_trial_universe.tasks.shared.store import TrialArmStore

__all__ = [
    "Cohort", "anzctr_regimes", "extract_anzctr_drugs", "resolve_cohorts", "trial_arm_id", "trial_id_of",
    "TrialArm", "TRIAL_ARMS_COLUMNS", "TrialArmStore",
]
