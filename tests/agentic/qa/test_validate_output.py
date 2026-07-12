"""Tests for the independent output validator (qa/validate_output) — the review of the reviewers."""
from __future__ import annotations

from aus_trial_universe.agentic.qa.validate_output import positive_type_fragments, validate_rows


def _row(**kw):
    base = {c: "" for c in (
        "trialId", "cohort", "arm_type", "cancer_type", "oncotree_name", "oncotree_code",
        "gene_alteration", "gene_alteration_findingmodel", "molecular_signature",
        "molecular_signature_findingmodel", "molecular_biomarker", "prior_therapy", "drug")}
    base.update(kw)
    return base


def test_positive_type_fragments_ignores_not_and_conditional_carveouts():
    # single positive type -> 1 fragment
    assert positive_type_fragments("DMG including DIPG") == ["DMG including DIPG"]
    # a NOT() carve-out (even a conditional one that doesn't START with NOT) is NOT a positive type
    ct = ("DMG including DIPG AND for tumors without a pontine epicenter, NOT(thalamic DMG) "
          "AND NOT(disseminated disease)")
    assert positive_type_fragments(ct) == ["DMG including DIPG"]
    # two genuine positive types ANDed -> 2 fragments (the unsatisfiable case)
    assert len(positive_type_fragments("Stage M NBL AND Stage Ms NBL")) == 2


def test_clean_output_reports_no_problems():
    rows = [
        _row(trialId="NCT1", cohort="Arm A", cancer_type="NSCLC [ELIGIBILITY CRITERIA]",
             oncotree_code="NSCLC", gene_alteration="EGFR L858R [ELIGIBILITY CRITERIA]",
             gene_alteration_findingmodel="SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R]"),
    ]
    (rep,) = validate_rows(rows)
    assert rep.problems == []


def test_catches_impossible_conjunction_bad_code_and_subsumption():
    rows = [
        # unsatisfiable positive-AND-positive cancer_type
        _row(trialId="NCT_BAD", cohort="Arm A", cancer_type="Stage M NBL AND Stage Ms NBL [X]"),
        # invalid oncotree code
        _row(trialId="NCT_BAD", cohort="Arm A", cancer_type="NSCLC [X]", oncotree_code="NOTACODE"),
        # subsuming prior_therapy twin (same cohort+cols, one strict superset of the other)
        _row(trialId="NCT_BAD", cohort="Arm B", cancer_type="breast [X]", prior_therapy="prior chemo [X]"),
        _row(trialId="NCT_BAD", cohort="Arm B", cancer_type="breast [X]",
             prior_therapy="prior chemo AND refractory to standard therapy [X]"),
    ]
    (rep,) = validate_rows(rows)
    joined = " | ".join(rep.problems)
    assert "positive tumour types" in joined
    assert "invalid code" in joined
    assert "subsuming twin" in joined


def test_catches_self_contradiction_and_empty_row():
    rows = [
        _row(trialId="NCT_C", cohort="A", gene_alteration="BRAF V600E AND NOT(BRAF V600E) [X]"),
        _row(trialId="NCT_C", cohort="A"),  # all eligibility columns empty
    ]
    (rep,) = validate_rows(rows)
    joined = " | ".join(rep.problems)
    assert "self-contradiction" in joined
    assert "all" in joined and "empty" in joined
