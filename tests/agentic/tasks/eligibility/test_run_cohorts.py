"""Regime (cohort) enumeration from CTGov armGroups (spec §6.1 — the drug-regime axis)."""
from __future__ import annotations

from aus_trial_universe.tasks.eligibility.extraction.loaders import (
    _clean_intervention_name,
    _ctgov_cohorts,
    _pharmacological_drugs,
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


def test_pharmacological_filter_keeps_drug_and_biological_excludes_others_and_placebo():
    # {Drug, Biological} kept; Radiation/Procedure/Device dropped; Placebo excluded.
    names = ["Biological: Ris-Rez", "Drug: Topotecan", "Radiation: Radiotherapy",
             "Procedure: Surgery", "Device: Pump", "Drug: Placebo"]
    assert _pharmacological_drugs(names) == ["Ris-Rez", "Topotecan"]


def test_ctgov_regime_keeps_biological_arm_and_carries_description_and_arm_type():
    # NCT07099898 shape: the investigational arm is a BIOLOGICAL — must NOT be dropped by a "Drug:" filter.
    ai = {"armGroups": [
        {"label": "Ris-Rez", "type": "EXPERIMENTAL", "description": "risvutatug rezetecan",
         "interventionNames": ["Biological: Ris-Rez"]},
        {"label": "Topotecan", "type": "ACTIVE_COMPARATOR", "description": "SoC comparator",
         "interventionNames": ["Drug: Topotecan"]},
    ], "interventions": []}
    cohorts = _ctgov_cohorts(ai)
    assert [(c.label, c.drug, c.arm_type, c.description) for c in cohorts] == [
        ("Ris-Rez", "Ris-Rez", "EXPERIMENTAL", "risvutatug rezetecan"),
        ("Topotecan", "Topotecan", "ACTIVE_COMPARATOR", "SoC comparator"),
    ]


def test_ctgov_regime_drops_non_drug_arms():
    # A pure-radiotherapy arm and a placebo-only arm are NOT drug regimes -> dropped; mixed arm keeps only its drug.
    ai = {"armGroups": [
        {"label": "Chemo+RT", "type": "EXPERIMENTAL",
         "interventionNames": ["Drug: Cisplatin", "Radiation: Radiotherapy"]},
        {"label": "RT only", "type": "ACTIVE_COMPARATOR", "interventionNames": ["Radiation: Radiotherapy"]},
        {"label": "Placebo", "type": "PLACEBO_COMPARATOR", "interventionNames": ["Drug: Placebo"]},
    ], "interventions": []}
    cohorts = _ctgov_cohorts(ai)
    assert [(c.label, c.drug) for c in cohorts] == [("Chemo+RT", "Cisplatin")]  # RT-only + placebo dropped


def test_ctgov_fallback_filters_typed_non_drugs():
    ai = {"armGroups": [], "interventions": [
        {"type": "DRUG", "name": "pembrolizumab"},
        {"type": "RADIATION", "name": "Radiotherapy"},
        {"type": "DRUG", "name": "Placebo"},
    ]}
    cohorts = _ctgov_cohorts(ai)
    assert len(cohorts) == 1 and cohorts[0].drug == "pembrolizumab"


def test_ctgov_drops_closed_not_enrolling_arms():
    # A drug-bearing arm whose label marks it closed / not-recruiting is not a matchable regime -> dropped
    # (NCT05009992 shape: several "NOT CURRENTLY ENROLLING - ARM ..." arms alongside open cohorts).
    ai = {"armGroups": [
        {"label": "NOT CURRENTLY ENROLLING - ARM 2: ONC201", "type": "EXPERIMENTAL",
         "interventionNames": ["Drug: ONC201"]},
        {"label": "Cohort 5 - ONC201 + Targeted therapies", "type": "EXPERIMENTAL",
         "interventionNames": ["Drug: ONC201"]},
        {"label": "Arm X (withdrawn)", "type": "EXPERIMENTAL", "interventionNames": ["Drug: Paxalisib"]},
    ], "interventions": []}
    cohorts = _ctgov_cohorts(ai)
    assert [c.label for c in cohorts] == ["Cohort 5 - ONC201 + Targeted therapies"]  # closed + withdrawn dropped
