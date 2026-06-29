"""Pin the canonical ANZCTR trial-id normaliser shared across the pipeline.

These cases used to be handled inconsistently by several copies of the
normaliser (download dedup, cancer-type keying, resource keying), which could
produce divergent join keys.  They now all route through
``shared.cohorts.normalize_anzctr_trial_id``.
"""

import pytest

from aus_trial_universe.eligibility_path.shared.cohorts import (
    normalize_anzctr_trial_id,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        # already prefixed (any case / surrounding whitespace)
        ("ACTRN12605000123456", "ACTRN12605000123456"),
        ("actrn12605000123456", "ACTRN12605000123456"),
        ("  ACTRN12605000123456  ", "ACTRN12605000123456"),
        # bare numeric id -> ACTRN-prefixed
        ("12605000123456", "ACTRN12605000123456"),
        (12605000123456, "ACTRN12605000123456"),
        # pandas float coercion of a numeric id (the cross-stage hazard)
        (12605000123456.0, "ACTRN12605000123456"),
        ("12605000123456.0", "ACTRN12605000123456"),
        # id embedded in surrounding text
        ("ACTRN12605000123456 (cohort A)", "ACTRN12605000123456"),
        # blanks / missing
        ("", ""),
        ("   ", ""),
        (None, ""),
        ("nan", ""),
        (float("nan"), ""),
    ],
)
def test_normalize_anzctr_trial_id(value, expected):
    assert normalize_anzctr_trial_id(value) == expected


def test_normalize_anzctr_trial_id_is_idempotent():
    once = normalize_anzctr_trial_id("12605000123456")
    assert normalize_anzctr_trial_id(once) == once


def test_multiple_embedded_ids_returns_first_and_warns(caplog):
    text = "ACTRN12605000123456 / ACTRN12605000999999"
    with caplog.at_level("WARNING"):
        result = normalize_anzctr_trial_id(text)
    assert result == "ACTRN12605000123456"
    assert any("distinct ANZCTR ids" in record.message for record in caplog.records)


def test_repeated_same_id_returns_it_without_warning(caplog):
    text = "ACTRN12605000123456 (ACTRN12605000123456)"
    with caplog.at_level("WARNING"):
        result = normalize_anzctr_trial_id(text)
    assert result == "ACTRN12605000123456"
    assert not any("distinct ANZCTR ids" in record.message for record in caplog.records)
