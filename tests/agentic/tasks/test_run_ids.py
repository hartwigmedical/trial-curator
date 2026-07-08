"""ANZCTR id normalization / display-prefix helpers (run.py)."""
from __future__ import annotations

from aus_trial_universe.agentic.tasks.extraction.loaders import (
    _display_actrn,
    _infer_source,
    _norm_actrn,
)


def test_display_actrn_adds_prefix_to_bare_digits():
    assert _display_actrn("12605000025639") == "ACTRN12605000025639"


def test_display_actrn_idempotent_and_case_insensitive():
    assert _display_actrn("ACTRN12605000025639") == "ACTRN12605000025639"
    assert _display_actrn("actrn12605000025639") == "ACTRN12605000025639"


def test_display_actrn_empty_stays_empty():
    assert _display_actrn("") == ""
    assert _display_actrn(None) == ""


def test_norm_and_display_round_trip():
    assert _norm_actrn(_display_actrn("12605000025639")) == "12605000025639"


def test_infer_source_from_id():
    assert _infer_source("NCT07099898") == "ctgov"
    assert _infer_source("nct07099898") == "ctgov"
    assert _infer_source("ACTRN12605000025639") == "anzctr"
    assert _infer_source("12605000025639") == "anzctr"  # bare digits -> anzctr
    assert _infer_source("weird-id") is None
