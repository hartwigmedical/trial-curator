"""The anti-broadening rule — the deterministic guard that stops the 2026-08-05 defect class at its source.

`expr.broadens` is used in TWO places (the reconciler's R6 guard and the `mapping_drift` gate), so it is tested
once, here, against the real values that shipped wrong.
"""
from __future__ import annotations

import pytest

from aus_trial_universe.tasks.eligibility.mapping.cancer_type.expr import broadens, narrows, positive_codes


@pytest.mark.parametrize("old,new", [
    ("ACYC", "Solid tumour"),                                   # the adenoid cystic regression
    ("CERVIX OR OVARY OR UTERUS OR VULVA", "Solid tumour"),      # the gynaecological regressions
    ("BONE", "Solid tumour"),                                    # 'spine cancer'
    ("EMBT OR ES OR RCSNOS", "Solid tumour"),                    # the embryonal SRBC tumours
    ("BLCA", "BLADDER"),                                         # histology swallowed by its organ bucket
    ("ASTR2 OR ASTR3 OR ODG2 OR ODG3", "ASTR OR ODG"),           # grade discarded by group adjudication
    ("GINET", "Solid tumour"),
])
def test_real_regressions_are_flagged_as_broadening(old, new):
    assert broadens(old, new), f"{old} -> {new} must be rejected"


@pytest.mark.parametrize("old,new", [
    ("DIFG", "DMG"),                                             # a genuine refinement (adopted as a ruling)
    ("NSGCT OR SEM", "SEM"),                                     # narrowing: reported, never blocked
    ("Solid tumour AND NOT(PROSTATE)", "Solid tumour AND NOT(BREAST OR PROSTATE)"),  # tightening an exclusion
    ("Solid tumour", "Solid tumour AND NOT(HCC)"),
    ("MEL", "MEL"),
    ("MEL", "MEL OR SKCM"),                                      # adding an OR branch is NOT broadening a code
])
def test_refinements_and_narrowings_are_allowed(old, new):
    assert broadens(old, new) is None, f"{old} -> {new} must be allowed"


def test_narrowing_is_detected_but_kept_separate_from_broadening():
    assert narrows("NSGCT OR SEM", "SEM")
    assert broadens("NSGCT OR SEM", "SEM") is None
    # a broadening is never ALSO reported as a narrowing, or the gate would double-count it
    assert narrows("ACYC", "Solid tumour") is None


def test_unparseable_or_empty_never_blocks():
    """A guard that fires on garbage would abandon good values mid-run; the expression gate owns syntax."""
    assert broadens("", "Solid tumour") is None
    assert broadens("MEL", "((((") is None
    assert broadens("((((", "MEL") is None
    assert positive_codes("((((") is None


def test_positive_codes_ignores_exclusions():
    assert positive_codes("Solid tumour AND NOT(HCC)") == {"Solid tumour"}
    assert positive_codes("MEL OR SKCM") == {"MEL", "SKCM"}


def test_the_two_kinds_are_distinguished_because_confidence_differs():
    """Only sentinel-broadening is certain enough to reject in-loop.

    `IDC` -> `BREAST` proves why: for the value "metastatic breast cancer" that is the CORRECT repair of an
    over-specification, and it is the shape every group unification takes when members differ in specificity.
    Rejecting it would defeat the purpose of group reconciliation (two existing tests assert exactly that
    behaviour). Sentinel-broadening has no such defence — a sentinel is never a group's most specific covering
    code unless a member already is one."""
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.expr import (
        BROADEN_ANCESTOR,
        BROADEN_SENTINEL,
        broadening,
    )
    assert broadening("ACYC", "Solid tumour")[0] == BROADEN_SENTINEL
    assert broadening("IDC", "BREAST")[0] == BROADEN_ANCESTOR
    assert broadening("ASTR2 OR ASTR3", "ASTR")[0] == BROADEN_ANCESTOR
    # a member that is ALREADY a sentinel is not "broadened" by staying one
    assert broadening("Solid tumour AND NOT(MEL)", "Solid tumour") is None
