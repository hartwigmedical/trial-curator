"""Cohort/drug enumeration from source structure (run.py) — Option A."""
from __future__ import annotations

from aus_trial_universe.agentic.tasks.extraction.loaders import (
    _clean_intervention_name,
    _ctgov_cohorts,
)


def test_clean_intervention_name_strips_type_prefix():
    assert _clean_intervention_name("Biological: Ris-Rez") == "Ris-Rez"
    assert _clean_intervention_name("Drug: pembrolizumab") == "pembrolizumab"
    assert _clean_intervention_name("Pembrolizumab") == "Pembrolizumab"


def test_ctgov_cohorts_one_per_arm_with_cleaned_drug():
    ai = {
        "armGroups": [
            {"label": "Arm A", "type": "EXPERIMENTAL",
             "interventionNames": ["Drug: pembrolizumab", "Drug: lenvatinib"]},
            {"label": "Arm B", "type": "ACTIVE_COMPARATOR", "interventionNames": ["Drug: chemotherapy"]},
        ],
        "interventions": [],
    }
    cohorts = _ctgov_cohorts(ai)
    assert [c.label for c in cohorts] == ["Arm A", "Arm B"]
    assert cohorts[0].drug == "pembrolizumab; lenvatinib"
    assert cohorts[1].drug == "chemotherapy"
    assert all(c.drug_source == "INTERVENTIONS MODULE" for c in cohorts)
    assert [c.arm_type for c in cohorts] == ["EXPERIMENTAL", "ACTIVE_COMPARATOR"]


def test_ctgov_cohorts_fallback_single_when_no_arms():
    ai = {"armGroups": [], "interventions": [{"name": "pembrolizumab"}, {"name": "lenvatinib"}]}
    cohorts = _ctgov_cohorts(ai)
    assert len(cohorts) == 1 and cohorts[0].label == "all"
    assert cohorts[0].drug == "pembrolizumab; lenvatinib"
