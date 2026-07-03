"""Tests for workflow engine primitives (fan_out, refine)."""
from __future__ import annotations

import pytest

from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out, refine


def test_fan_out_preserves_order():
    thunks = [(lambda i=i: i * 10) for i in range(5)]
    assert fan_out(thunks) == [0, 10, 20, 30, 40]


def test_fan_out_empty_and_single():
    assert fan_out([]) == []
    assert fan_out([lambda: 42]) == [42]


def test_fan_out_propagates_exceptions():
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        fan_out([lambda: 1, boom])


def test_refine_ok_first_try_skips_repair():
    calls = {"produce": 0, "check": 0, "repair": 0}

    def produce():
        calls["produce"] += 1
        return "v0"

    def check(v):
        calls["check"] += 1
        return CheckResult(ok=True)

    def repair(v, problems):
        calls["repair"] += 1
        return v

    result = refine(produce, check, repair)
    assert result.ok and result.attempts == 1 and result.value == "v0"
    assert calls == {"produce": 1, "check": 1, "repair": 0}


def test_refine_repairs_until_ok():
    def produce():
        return 0

    def check(v):
        return CheckResult(ok=(v >= 2), problems=[] if v >= 2 else [f"too small: {v}"])

    def repair(v, problems):
        return v + 1

    result = refine(produce, check, repair, max_attempts=5)
    assert result.ok and result.value == 2 and result.attempts == 3


def test_refine_stops_at_max_attempts():
    def produce():
        return 0

    def check(v):
        return CheckResult(ok=False, problems=["always bad"])

    def repair(v, problems):
        return v + 1

    result = refine(produce, check, repair, max_attempts=3)
    # produce -> 0, then 2 repairs (attempts 1->2->3); stops without a 3rd repair.
    assert not result.ok and result.attempts == 3 and result.value == 2


def test_refine_rejects_bad_max_attempts():
    with pytest.raises(ValueError):
        refine(lambda: 0, lambda v: CheckResult(ok=True), lambda v, p: v, max_attempts=0)
